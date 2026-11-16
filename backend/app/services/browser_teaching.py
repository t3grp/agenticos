"""Lease-bound, redacted browser teaching and deterministic skill proposals."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.spec import AgentSpec
from app.core.config import settings
from app.core.exceptions import (
    AuthorizationError,
    BadRequestError,
    ConcurrentChangeError,
    NotFoundError,
)
from app.core.permissions import AuthContext, Perm
from app.db.models.agent import AgentVersion
from app.db.models.agent_run import AgentRun
from app.db.models.browser_teaching import BrowserTeachingSession
from app.db.models.skill_proposal import ProposalStatus, SkillProposal
from app.db.models.skill_version import SkillVersionSource
from app.db.session import get_db_context, get_worker_db_context
from app.repositories import agent as agent_repo
from app.repositories import browser_teaching as browser_teaching_repo
from app.repositories import skill_proposal as skill_proposal_repo
from app.repositories import skill_version as skill_version_repo
from app.schemas.browser_teaching import (
    BrowserTeachingAction,
    BrowserTeachingRead,
    BrowserTeachingReview,
    BrowserTeachingStart,
)
from app.services import live_browse
from app.services.access import AGENT, resolve_access

EVENT_LIMIT = 500
BYTE_LIMIT = 128 * 1024
SESSION_SECONDS = 24 * 60 * 60
RECORD_SECONDS = 30 * 60
_SECRET_LABEL = re.compile(
    r"(?i)(password|passcode|one.?time|otp|token|secret|cookie|authorization|api.?key)"
)


def safe_origin(url: str) -> str:
    """Return only an HTTPS origin; never retain path, query or fragment."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        raise BadRequestError(
            message="Teaching supports HTTPS sites only.", details={"field": "origin"}
        ) from None
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise BadRequestError(
            message="Teaching supports HTTPS sites only.", details={"field": "origin"}
        )
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise BadRequestError(
            message="Teaching supports HTTPS sites only.", details={"field": "origin"}
        ) from None
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"


def _public_step(
    event_id: UUID, sequence: int, action: str, origin: str, target: dict[str, str] | None = None
) -> dict[str, Any]:
    """Build a closed step shape. No page text, selector, URL path or input value can enter."""
    step: dict[str, Any] = {
        "event_id": str(event_id),
        "sequence": sequence,
        "action": action,
        "origin": safe_origin(origin),
    }
    if target is not None:
        role = target.get("role", "")
        fingerprint = target.get("fingerprint", "")
        if role in {"button", "link", "textbox", "checkbox", "radio", "dropdown", "select"}:
            step["target_role"] = role
        if re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            step["target_fingerprint"] = fingerprint
    if action == "input_parameter":
        step["parameter"] = f"input_{sequence + 1}"
    return step


def _procedure_matches_origin(procedure: object, exact_origin: str) -> bool:
    return (
        isinstance(procedure, dict)
        and procedure.get("version") == 1
        and procedure.get("origin") == exact_origin
        and isinstance(procedure.get("steps"), list)
        and isinstance(procedure.get("parameters"), list)
    )


def _steps(row: BrowserTeachingSession) -> list[dict[str, Any]]:
    return list(row.events)


def _read(row: BrowserTeachingSession) -> BrowserTeachingRead:
    return BrowserTeachingRead(
        id=row.id,
        agent_id=row.agent_id,
        run_id=row.run_id,
        call_id=row.call_id,
        lease_id=row.lease_id,
        origin=row.origin,
        status=row.status,
        pause_reason=row.pause_reason,
        revision=row.revision,
        steps=_steps(row),
        parameters=row.parameters,
        proposal_id=row.proposal_id,
        expires_at=row.expires_at,
    )


def _append_event(
    row: BrowserTeachingSession,
    *,
    event_id: UUID,
    action: str,
    url: str,
    target: dict[str, str] | None,
    now: datetime,
    lease_owner: str | None,
    lease_id: str | None,
) -> bool:
    """Append a redacted input event once, or pause on a bound being reached."""
    if action not in {"click", "input_parameter", "navigate", "scroll", "checkpoint"}:
        return False
    if row.expires_at <= now or row.status != "recording":
        return False
    if row.capture_until <= now:
        row.status, row.pause_reason = "paused", "duration_limit"
    elif row.lease_id != lease_id or lease_owner != str(row.owner_user_id):
        row.status, row.pause_reason = "paused", "lease_lost"
    else:
        try:
            event_origin = safe_origin(url)
        except BadRequestError:
            event_origin = ""
        if event_origin != row.origin:
            row.status, row.pause_reason = "paused", "origin_changed"
            row.revision += 1
            return True
    if any(event.get("event_id") == str(event_id) for event in row.events):
        return False
    if len(row.events) >= EVENT_LIMIT:
        row.status, row.pause_reason = "paused", "event_limit"
    if row.status == "recording":
        event = _public_step(event_id, len(row.events), action, url, target)
        encoded = json.dumps([*row.events, event], separators=(",", ":")).encode()
        if len(encoded) > BYTE_LIMIT:
            row.status, row.pause_reason = "paused", "size_limit"
        else:
            row.events = [*row.events, event]
            row.revision += 1
            return True
    row.revision += 1
    return True


class BrowserTeachingService:
    """Own the teaching state machine and its authorization checks."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def start(
        self, ctx: AuthContext, user_id: UUID, data: BrowserTeachingStart, key: str
    ) -> BrowserTeachingRead:
        if not settings.BROWSER_TEACHING_ENABLED:
            raise NotFoundError(message="Browser teaching is not enabled.")
        if ctx.personal_user_id != user_id:
            raise AuthorizationError(message="Browser teaching needs the signed-in lease owner.")
        meta = await live_browse.metadata(str(data.run_id), data.call_id)
        if (
            meta is None
            or meta.organization_id != ctx.organization_id
            or meta.agent_id is None
            or meta.user_id != str(user_id)
            or await live_browse.lease_owner(str(data.run_id), data.call_id) != str(user_id)
            or await live_browse.lease_id(str(data.run_id), data.call_id) != data.lease_id
        ):
            raise AuthorizationError(message="Take over this managed browser before teaching.")
        # The run's agent is resolved from live metadata, not from request fields.
        agent = await agent_repo.get(self.db, meta.agent_id, organization_id=ctx.organization_id)
        if agent is None or not await resolve_access(
            self.db, ctx, agent, Perm.AGENTS_RUN, resource_type=AGENT
        ):
            raise AuthorizationError(message="You cannot use this agent's browser.")
        existing = await browser_teaching_repo.get_by_idempotency_key(
            self.db, organization_id=ctx.organization_id, idempotency_key=key
        )
        if existing is not None:
            if (
                existing.owner_user_id != user_id
                or existing.run_id != data.run_id
                or existing.call_id != data.call_id
                or existing.lease_id != data.lease_id
            ):
                raise AuthorizationError(
                    message="That teaching request belongs to another browser."
                )
            return _read(existing)
        now = datetime.now(UTC)
        active = await browser_teaching_repo.recording_for_browser(
            self.db,
            organization_id=ctx.organization_id,
            run_id=data.run_id,
            call_id=data.call_id,
        )
        reusable: BrowserTeachingSession | None = None
        changed = False
        for session in active:
            if session.expires_at <= now:
                session.status = "expired"
                session.pause_reason = "expired"
                session.events = []
                session.parameters = []
                session.revision += 1
                changed = True
            elif session.capture_until <= now:
                session.status = "paused"
                session.pause_reason = "duration_limit"
                session.revision += 1
                changed = True
            elif (
                reusable is None
                and session.owner_user_id == user_id
                and session.lease_id == data.lease_id
            ):
                reusable = session
            else:
                session.status = "paused"
                session.pause_reason = "lease_lost"
                session.revision += 1
                changed = True
        if changed:
            await self.db.flush()
        if reusable is not None:
            return _read(reusable)
        row = BrowserTeachingSession(
            organization_id=ctx.organization_id,
            owner_user_id=user_id,
            agent_id=meta.agent_id,
            run_id=data.run_id,
            call_id=data.call_id,
            lease_id=data.lease_id,
            idempotency_key=key,
            origin=safe_origin(await live_browse.origin(str(data.run_id), data.call_id) or ""),
            status="recording",
            revision=1,
            events=[],
            parameters=[],
            expires_at=now + timedelta(seconds=SESSION_SECONDS),
            capture_until=now + timedelta(seconds=RECORD_SECONDS),
        )
        try:
            async with self.db.begin_nested():
                self.db.add(row)
                await self.db.flush()
        except IntegrityError:
            duplicate = await browser_teaching_repo.get_by_idempotency_key(
                self.db, organization_id=ctx.organization_id, idempotency_key=key
            )
            if duplicate is not None:
                if (
                    duplicate.owner_user_id != user_id
                    or duplicate.run_id != data.run_id
                    or duplicate.call_id != data.call_id
                    or duplicate.lease_id != data.lease_id
                ):
                    raise AuthorizationError(
                        message="That teaching request belongs to another browser."
                    ) from None
                return _read(duplicate)
            active_after_conflict = await browser_teaching_repo.recording_for_browser(
                self.db,
                organization_id=ctx.organization_id,
                run_id=data.run_id,
                call_id=data.call_id,
            )
            winner = next(
                (
                    active_session
                    for active_session in active_after_conflict
                    if active_session.owner_user_id == user_id
                    and active_session.lease_id == data.lease_id
                ),
                None,
            )
            if winner is not None:
                return _read(winner)
            if active_after_conflict:
                raise ConcurrentChangeError(
                    message=(
                        "Another teaching session started for this browser. "
                        "Reload it before continuing."
                    )
                ) from None
            raise
        await self.db.refresh(row)
        return _read(row)

    @staticmethod
    async def record_input(
        organization_id: UUID,
        user_id: UUID,
        run_id: UUID,
        call_id: str,
        lease_id: str,
        event_id: UUID,
        action: str,
        url: str,
        target: dict[str, str] | None = None,
    ) -> None:
        """Append one server-observed action, after checking the current lease."""
        now = datetime.now(UTC)
        if action not in {"click", "input_parameter", "navigate", "scroll", "checkpoint"}:
            return
        async with get_db_context() as db:
            result = await db.execute(
                select(BrowserTeachingSession)
                .where(
                    BrowserTeachingSession.organization_id == organization_id,
                    BrowserTeachingSession.owner_user_id == user_id,
                    BrowserTeachingSession.run_id == run_id,
                    BrowserTeachingSession.call_id == call_id,
                    BrowserTeachingSession.status == "recording",
                )
                .with_for_update()
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            if not settings.BROWSER_TEACHING_ENABLED:
                row.status = "paused"
                row.pause_reason = "feature_disabled"
                row.revision += 1
                await db.flush()
                return
            changed = _append_event(
                row,
                event_id=event_id,
                action=action,
                url=url,
                target=target,
                now=now,
                lease_owner=await live_browse.lease_owner(str(run_id), call_id),
                lease_id=await live_browse.lease_id(str(run_id), call_id),
            )
            if changed:
                await db.flush()

    async def transition(
        self, ctx: AuthContext, user_id: UUID, teaching_id: UUID, data: BrowserTeachingAction
    ) -> BrowserTeachingRead:
        row = await self._owned(ctx, user_id, teaching_id, lock=True)
        if row.revision != data.revision:
            raise ConcurrentChangeError(
                message="The teaching session changed. Reload it before continuing."
            )
        if row.status not in {"recording", "paused"}:
            raise BadRequestError(message="This teaching session no longer accepts capture.")
        if data.action == "resume":
            if not settings.BROWSER_TEACHING_ENABLED:
                raise NotFoundError(message="Browser teaching is not enabled.")
            agent = await agent_repo.get(self.db, row.agent_id, organization_id=ctx.organization_id)
            if agent is None or not await resolve_access(
                self.db, ctx, agent, Perm.AGENTS_RUN, resource_type=AGENT
            ):
                raise AuthorizationError(message="You cannot use this agent's browser.")
            if (
                await live_browse.lease_owner(str(row.run_id), row.call_id) != str(user_id)
                or await live_browse.lease_id(str(row.run_id), row.call_id) != row.lease_id
            ):
                raise AuthorizationError(message="Take over this managed browser before resuming.")
            row.status = "recording"
            row.pause_reason = None
            row.capture_until = datetime.now(UTC) + timedelta(seconds=RECORD_SECONDS)
        else:
            row.status = "paused"
            row.pause_reason = "manual"
        row.revision += 1
        await self.db.flush()
        return _read(row)

    async def stop(self, ctx: AuthContext, user_id: UUID, teaching_id: UUID) -> BrowserTeachingRead:
        row = await self._owned(ctx, user_id, teaching_id, lock=True)
        if row.status == "recording" or row.status == "paused":
            row.status = "review"
            row.pause_reason = None
            row.revision += 1
            await self.db.flush()
        return _read(row)

    async def review(
        self, ctx: AuthContext, user_id: UUID, teaching_id: UUID, data: BrowserTeachingReview
    ) -> BrowserTeachingRead:
        row = await self._owned(ctx, user_id, teaching_id, lock=True)
        if row.revision != data.revision:
            raise ConcurrentChangeError(
                message="The teaching session changed. Reload it before continuing."
            )
        if row.status != "review":
            raise BadRequestError(message="Stop teaching before reviewing its steps.")
        names = [item.name for item in data.parameters]
        if len(names) != len(set(names)):
            raise BadRequestError(message="Parameter names must be unique.")
        input_steps = [event for event in row.events if event["action"] == "input_parameter"]
        if len(input_steps) != len(data.parameters):
            raise BadRequestError(message="Add one parameter for each captured input step.")
        parameter_ids = [str(item.event_id) for item in data.parameters]
        input_ids = {event["event_id"] for event in input_steps}
        if len(parameter_ids) != len(set(parameter_ids)) or set(parameter_ids) != input_ids:
            raise BadRequestError(message="Match each parameter to one captured input step.")
        if any(_SECRET_LABEL.search(item.label) for item in data.parameters):
            raise BadRequestError(message="Parameter labels cannot name credentials or secrets.")
        click_ids = {event["event_id"] for event in row.events if event["action"] == "click"}
        step_ids = [str(item.event_id) for item in data.steps]
        if len(step_ids) != len(set(step_ids)) or set(step_ids) != click_ids:
            raise BadRequestError(message="Add one reviewed label for each captured click.")
        if any(_SECRET_LABEL.search(item.reviewed_label) for item in data.steps):
            raise BadRequestError(message="Step labels cannot name credentials or secrets.")
        parameters = {str(item.event_id): item for item in data.parameters}
        step_labels = {str(item.event_id): item.reviewed_label for item in data.steps}
        events = [dict(event) for event in row.events]
        for event in events:
            if event["action"] == "input_parameter":
                parameter = parameters[event["event_id"]]
                event["parameter"] = parameter.name
                event["reviewed_label"] = parameter.label
            elif event["action"] == "click":
                event["reviewed_label"] = step_labels[event["event_id"]]
        row.events = events
        row.parameters = [item.model_dump(mode="json") for item in data.parameters]
        row.revision += 1
        await self.db.flush()
        return _read(row)

    async def propose(
        self, ctx: AuthContext, user_id: UUID, teaching_id: UUID, *, name: str, description: str
    ) -> BrowserTeachingRead:
        row = await self._owned(ctx, user_id, teaching_id, lock=True)
        agent = await agent_repo.get(self.db, row.agent_id, organization_id=ctx.organization_id)
        if agent is None or not await resolve_access(
            self.db, ctx, agent, Perm.AGENTS_RUN, resource_type=AGENT
        ):
            raise AuthorizationError(message="You cannot use this agent's browser.")
        if _SECRET_LABEL.search(name) or _SECRET_LABEL.search(description):
            raise BadRequestError(
                message="Skill names and descriptions cannot contain credentials or secrets."
            )
        if row.status == "proposed" and row.proposal_id is not None:
            return _read(row)
        if not settings.BROWSER_TEACHING_ENABLED:
            raise NotFoundError(message="Browser teaching is not enabled.")
        if row.status != "review" or not row.events:
            raise BadRequestError(
                message="Review a successful demonstration before proposing a skill."
            )
        if await live_browse.outcome(str(row.run_id), row.call_id) != "done":
            raise BadRequestError(
                message="The browser run did not finish successfully; no skill proposal was created."
            )
        # This proposal is deterministic and makes no model call; no budget is spent.
        canonical = json.dumps(
            {"origin": row.origin, "steps": row.events, "parameters": row.parameters},
            sort_keys=True,
        )
        source_hash = hashlib.sha256(canonical.encode()).hexdigest()
        previous = await skill_proposal_repo.get_by_browser_teaching(
            self.db, organization_id=ctx.organization_id, teaching_id=row.id
        )
        if previous is None:
            content = _procedure_text(row.origin, row.events, row.parameters)
            procedure = json.dumps(
                {
                    "version": 1,
                    "origin": row.origin,
                    "steps": row.events,
                    "parameters": row.parameters,
                    "source_hash": source_hash,
                },
                sort_keys=True,
            )
            proposal = await skill_proposal_repo.create(
                self.db,
                organization_id=ctx.organization_id,
                skill_id=None,
                agent_id=row.agent_id,
                conversation_id=None,
                name=name,
                description=description,
                content=content,
                resources={"browser-procedure.json": procedure},
                base_version=None,
                base_version_id=None,
                fork_id=None,
            )
            proposal.browser_teaching_id = row.id
            proposal.source = SkillVersionSource.BROWSER_TEACHING.value
            proposal.rationale = "Reviewed, lease-bound browser demonstration."
        else:
            proposal = previous
        row.proposal_id = proposal.id
        row.source_hash = source_hash
        row.status = "proposed"
        row.events = []
        row.revision += 1
        await self.db.flush()
        return _read(row)

    async def discard(self, ctx: AuthContext, user_id: UUID, teaching_id: UUID) -> None:
        row = await self._owned(ctx, user_id, teaching_id, lock=True)
        if row.status in {"proposed", "discarded", "expired"}:
            raise BadRequestError(message="This teaching session cannot be discarded now.")
        row.status = "discarded"
        row.events = []
        row.parameters = []
        row.revision += 1
        await self.db.flush()

    async def get(self, ctx: AuthContext, user_id: UUID, teaching_id: UUID) -> BrowserTeachingRead:
        return _read(await self._owned(ctx, user_id, teaching_id))

    @staticmethod
    async def procedure_for_origin(
        organization_id: UUID, run_id: UUID, agent_id: UUID, origin: str
    ) -> list[dict[str, Any]]:
        """Read only applied procedures on skills in this run's published agent version."""
        if not settings.BROWSER_TEACHING_ENABLED:
            return []
        try:
            exact_origin = safe_origin(origin)
        except BadRequestError:
            return []
        async with get_db_context() as db:
            run = await db.get(AgentRun, run_id)
            if (
                run is None
                or run.organization_id != organization_id
                or run.agent_id != agent_id
                or run.agent_version_id is None
            ):
                return []
            version = await db.get(AgentVersion, run.agent_version_id)
            if (
                version is None
                or version.organization_id != organization_id
                or version.agent_id != agent_id
            ):
                return []
            try:
                spec = AgentSpec.model_validate(version.spec)
            except ValidationError:
                return []
            procedures: list[dict[str, Any]] = []
            for skill_id in spec.skill_ids:
                binding = next(
                    (item for item in spec.skill_bindings if item.skill_id == skill_id), None
                )
                if binding is not None and binding.fork_id is not None:
                    stored = await skill_version_repo.get_at_time(
                        db,
                        organization_id=organization_id,
                        skill_id=skill_id,
                        fork_id=binding.fork_id,
                        at=run.created_at,
                    )
                elif binding is not None and binding.version is not None:
                    versions = await skill_version_repo.get_by_numbers(
                        db,
                        organization_id=organization_id,
                        skill_id=skill_id,
                        fork_id=None,
                        numbers=[binding.version],
                    )
                    stored = versions.get(binding.version)
                else:
                    stored = await skill_version_repo.get_at_time(
                        db,
                        organization_id=organization_id,
                        skill_id=skill_id,
                        fork_id=None,
                        at=run.created_at,
                    )
                if stored is None:
                    continue
                entry = stored.resources.get("browser-procedure.json")
                if entry is None or stored.source != SkillVersionSource.BROWSER_TEACHING.value:
                    continue
                proposal = (
                    await db.get(SkillProposal, stored.proposal_id) if stored.proposal_id else None
                )
                if (
                    proposal is None
                    or proposal.organization_id != organization_id
                    or proposal.status != ProposalStatus.APPLIED.value
                    or proposal.browser_teaching_id is None
                ):
                    continue
                body = await skill_version_repo.get_files(
                    db,
                    organization_id=organization_id,
                    skill_id=skill_id,
                    hashes=[entry["sha256"]],
                )
                raw = body.get(entry["sha256"])
                if raw is None:
                    continue
                try:
                    procedure = json.loads(raw)
                except TypeError, ValueError:
                    continue
                if _procedure_matches_origin(procedure, exact_origin):
                    procedures.append(procedure)
            return procedures

    @staticmethod
    async def end_lease(
        organization_id: UUID, user_id: UUID, run_id: UUID, call_id: str, lease_id: str
    ) -> None:
        """Pause capture when its exact human-control socket releases the browser."""
        async with get_db_context() as db:
            result = await db.execute(
                select(BrowserTeachingSession)
                .where(
                    BrowserTeachingSession.organization_id == organization_id,
                    BrowserTeachingSession.owner_user_id == user_id,
                    BrowserTeachingSession.run_id == run_id,
                    BrowserTeachingSession.call_id == call_id,
                    BrowserTeachingSession.lease_id == lease_id,
                    BrowserTeachingSession.status == "recording",
                )
                .with_for_update()
            )
            row = result.scalar_one_or_none()
            if row is not None:
                row.status = "paused"
                row.pause_reason = "lease_ended"
                row.revision += 1
                await db.flush()

    @staticmethod
    async def end_run(organization_id: UUID, run_id: UUID, call_id: str) -> None:
        """Stop every active capture when its managed browser run ends."""
        async with get_db_context() as db:
            result = await db.execute(
                select(BrowserTeachingSession)
                .where(
                    BrowserTeachingSession.organization_id == organization_id,
                    BrowserTeachingSession.run_id == run_id,
                    BrowserTeachingSession.call_id == call_id,
                    BrowserTeachingSession.status == "recording",
                )
                .with_for_update()
            )
            rows = result.scalars().all()
            for row in rows:
                row.status = "paused"
                row.pause_reason = "browser_finished"
                row.revision += 1
            if rows:
                await db.flush()

    async def _owned(
        self, ctx: AuthContext, user_id: UUID, teaching_id: UUID, *, lock: bool = False
    ) -> BrowserTeachingSession:
        query = select(BrowserTeachingSession).where(
            BrowserTeachingSession.id == teaching_id,
            BrowserTeachingSession.organization_id == ctx.organization_id,
            BrowserTeachingSession.owner_user_id == user_id,
        )
        if lock:
            query = query.with_for_update()
        row = (await self.db.execute(query)).scalar_one_or_none()
        if row is None:
            raise NotFoundError(message="Browser teaching session not found.")
        now = datetime.now(UTC)
        if ctx.personal_user_id != user_id:
            raise NotFoundError(message="Browser teaching session not found.")
        if not settings.BROWSER_TEACHING_ENABLED and row.status == "recording":
            row.status = "paused"
            row.pause_reason = "feature_disabled"
            row.revision += 1
            await self.db.flush()
        elif row.status == "recording" and row.capture_until <= now:
            row.status = "paused"
            row.pause_reason = "duration_limit"
            row.revision += 1
            await self.db.flush()
        elif row.status == "recording" and (
            await live_browse.lease_owner(str(row.run_id), row.call_id) != str(user_id)
            or await live_browse.lease_id(str(row.run_id), row.call_id) != row.lease_id
        ):
            row.status = "paused"
            row.pause_reason = "lease_ended"
            row.revision += 1
            await self.db.flush()
        if row.expires_at <= now and row.status not in {"proposed", "discarded", "expired"}:
            row.status = "expired"
            row.pause_reason = "expired"
            row.events = []
            row.parameters = []
            row.revision += 1
            await self.db.flush()
        return row


async def sweep_expired() -> int:
    """Erase expired transient steps in a separate worker transaction."""
    async with get_worker_db_context() as db:
        return await browser_teaching_repo.expire_due(db, now=datetime.now(UTC))


def _procedure_text(
    origin: str, events: list[dict[str, Any]], parameters: list[dict[str, str]]
) -> str:
    if any(_SECRET_LABEL.search(item.get("label", "")) for item in parameters):
        raise BadRequestError(message="Parameter labels cannot name credentials or secrets.")
    lines = [
        f"Use this reviewed procedure only at {origin}.",
        "Read the live page before each action. Match the role and structure again.",
        "Stop if the page does not match. Never claim a failed procedure succeeded.",
    ]
    for event in events:
        action = event["action"]
        label = event.get("reviewed_label", "")
        role = event.get("target_role")
        if action == "click":
            if not label:
                raise BadRequestError(
                    message="Review every click target before proposing a procedure."
                )
            target = f" ({role})" if role else ""
            lines.append(f"{event['sequence'] + 1}. Click the reviewed control {label}{target}.")
        elif action == "input_parameter":
            if not label or not event.get("parameter"):
                raise BadRequestError(
                    message="Review every input parameter before proposing a procedure."
                )
            lines.append(
                f"{event['sequence'] + 1}. Enter the requested {label} value for {event['parameter']}."
            )
        else:
            lines.append(f"{event['sequence'] + 1}. {action.replace('_', ' ')}.")
    if parameters:
        lines.append(
            "Ask the person for these values when required: "
            + ", ".join(f"{item['name']} ({item['label']})" for item in parameters)
            + "."
        )
    return "\n".join(lines)
