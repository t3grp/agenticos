"""Tenant-scoped persistence for automatic room-routing policy and receipts."""

import hashlib
import json
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models.credential import ModelProfile
from app.db.models.room_auto_routing import RoomAutoRoutingDecision, RoomAutoRoutingPolicy
from app.db.models.room_participant import RoomParticipant
from app.db.models.room_submission import RoomSubmission
from app.schemas.room_auto_routing import (
    RoomAutoRoutingDecisionCreate,
    RoomAutoRoutingDecisionReceipt,
)

_ACTIVE_RESPONDER_STATUSES = ("queued", "running", "awaiting_approval", "awaiting_answer")
_BLOCKING_ROUTER_STATUSES = ("pending", "running")


def model_profile_fingerprint(profile: ModelProfile) -> str:
    """Hash only the selected org profile's execution metadata, never a secret.

    Profiles are editable in place, so the ID alone cannot freeze a queued
    decision. Including the organization/profile identity, scope, provider/model,
    request settings, fallback order and secret reference lets the worker refuse
    a changed or repointed profile before resolving credentials or spending.
    The secret's value is stored elsewhere and is never read here.
    """
    snapshot = {
        "schema": 1,
        "id": str(profile.id),
        "organization_id": str(profile.organization_id),
        "usage_scope": profile.usage_scope,
        "personal_owner_user_id": (
            None if profile.personal_owner_user_id is None else str(profile.personal_owner_user_id)
        ),
        "provider": profile.provider,
        "model": profile.model,
        "secret_id": None if profile.secret_id is None else str(profile.secret_id),
        "runs_without_key": profile.runs_without_key,
        "base_url": profile.base_url,
        "api": profile.api,
        "region": profile.region,
        "context_length": profile.context_length,
        "params": profile.params,
        "fallback_profile_ids": profile.fallback_profile_ids,
    }
    encoded = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def policy(
    db: AsyncSession,
    organization_id: UUID,
    conversation_id: UUID,
    *,
    lock: bool = False,
) -> RoomAutoRoutingPolicy | None:
    query = select(RoomAutoRoutingPolicy).where(
        RoomAutoRoutingPolicy.organization_id == organization_id,
        RoomAutoRoutingPolicy.conversation_id == conversation_id,
    )
    if lock:
        query = query.with_for_update()
    return await db.scalar(query.execution_options(populate_existing=True))


async def save_policy(db: AsyncSession, row: RoomAutoRoutingPolicy) -> None:
    db.add(row)
    await db.flush()
    await db.refresh(row)


async def router_is_on_roster(
    db: AsyncSession,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
) -> bool:
    return bool(
        await db.scalar(
            select(
                exists().where(
                    RoomParticipant.organization_id == organization_id,
                    RoomParticipant.conversation_id == conversation_id,
                    RoomParticipant.agent_id == agent_id,
                )
            )
        )
    )


async def automatic_participants(
    db: AsyncSession, *, organization_id: UUID, conversation_id: UUID
) -> list[RoomParticipant]:
    """The room's current ordinary auto-reply agents, in stable roster order."""
    return list(
        (
            await db.scalars(
                select(RoomParticipant)
                .where(
                    RoomParticipant.organization_id == organization_id,
                    RoomParticipant.conversation_id == conversation_id,
                    RoomParticipant.agent_id.is_not(None),
                    RoomParticipant.auto_reply.is_(True),
                )
                .order_by(RoomParticipant.created_at, RoomParticipant.id)
            )
        ).all()
    )


async def decision_for_submission(
    db: AsyncSession,
    organization_id: UUID,
    conversation_id: UUID,
    submission_id: UUID,
    *,
    lock: bool = False,
) -> RoomAutoRoutingDecision | None:
    query = select(RoomAutoRoutingDecision).where(
        RoomAutoRoutingDecision.organization_id == organization_id,
        RoomAutoRoutingDecision.conversation_id == conversation_id,
        RoomAutoRoutingDecision.submission_id == submission_id,
    )
    if lock:
        query = query.with_for_update()
    return await db.scalar(query.execution_options(populate_existing=True))


async def get_decision(
    db: AsyncSession,
    decision_id: UUID,
    *,
    organization_id: UUID,
    lock: bool = False,
) -> RoomAutoRoutingDecision | None:
    """Load one tenant-scoped receipt, optionally refreshing and locking it."""
    query = select(RoomAutoRoutingDecision).where(
        RoomAutoRoutingDecision.id == decision_id,
        RoomAutoRoutingDecision.organization_id == organization_id,
    )
    if lock:
        query = query.with_for_update()
    return await db.scalar(query.execution_options(populate_existing=True))


async def decisions_for_grouped_messages(
    db: AsyncSession,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    message_ids: list[UUID],
) -> list[RoomAutoRoutingDecision]:
    """Find canonical (non-continued) decisions touching a visible room page.

    The JSON membership checks are bounded by the caller's 100 message IDs and
    remain scoped to the authorized organization and room. A continued row is
    deliberately hidden; its newer destination decision carries the complete
    stable message group.
    """
    if not message_ids:
        return []
    membership = [
        RoomAutoRoutingDecision.grouped_message_ids.contains([str(message_id)])
        for message_id in message_ids
    ]
    result = await db.scalars(
        select(RoomAutoRoutingDecision)
        .where(
            RoomAutoRoutingDecision.organization_id == organization_id,
            RoomAutoRoutingDecision.conversation_id == conversation_id,
            RoomAutoRoutingDecision.status != "continued",
            or_(*membership),
        )
        .order_by(RoomAutoRoutingDecision.accepted_at, RoomAutoRoutingDecision.id)
    )
    return list(result.all())


async def has_earlier_active_submission(
    db: AsyncSession, decision: RoomAutoRoutingDecision
) -> bool:
    """Whether an active responder request precedes this router post in its room.

    The target-less root post stays `posted` while its decision waits, so it is
    not visible to the ordinary room queue head. This check deliberately looks
    up its root ordinal and only blocks on queued/running/parked responders with
    a lower ordinal in the same tenant and conversation.
    """
    root = aliased(RoomSubmission)
    earlier = aliased(RoomSubmission)
    earlier_parent = aliased(RoomSubmission)
    earlier_order = func.coalesce(earlier_parent.ordinal, earlier.ordinal)
    earlier_active = (
        select(earlier.id)
        .where(
            earlier.organization_id == decision.organization_id,
            earlier.conversation_id == decision.conversation_id,
            earlier.status.in_(_ACTIVE_RESPONDER_STATUSES),
            earlier_order < root.ordinal,
        )
        .select_from(earlier)
        .outerjoin(
            earlier_parent,
            earlier_parent.id == earlier.parent_submission_id,
        )
    )
    statement = select(
        exists().where(
            root.id == decision.submission_id,
            root.organization_id == decision.organization_id,
            root.conversation_id == decision.conversation_id,
            root.parent_submission_id.is_(None),
            earlier_active.exists(),
        )
    )
    return bool(await db.scalar(statement))


async def has_pending_or_running_before_submission(
    db: AsyncSession, submission: RoomSubmission
) -> bool:
    """Whether an older accepted router post still blocks this responder row.

    Call from the queue/admission guard while holding the room turn lock. This
    keeps later agent requests behind a pending/running automatic decision even
    though that root post is intentionally not an active `RoomSubmission` yet.
    """
    root = aliased(RoomSubmission)
    candidate_root_ordinal = (
        select(RoomSubmission.ordinal)
        .where(
            RoomSubmission.id == (submission.parent_submission_id or submission.id),
            RoomSubmission.organization_id == submission.organization_id,
            RoomSubmission.conversation_id == submission.conversation_id,
        )
        .scalar_subquery()
    )
    statement = select(
        exists().where(
            RoomAutoRoutingDecision.organization_id == submission.organization_id,
            RoomAutoRoutingDecision.conversation_id == submission.conversation_id,
            RoomAutoRoutingDecision.status.in_(_BLOCKING_ROUTER_STATUSES),
            RoomAutoRoutingDecision.submission_id == root.id,
            root.organization_id == submission.organization_id,
            root.conversation_id == submission.conversation_id,
            root.parent_submission_id.is_(None),
            root.ordinal < candidate_root_ordinal,
        )
    )
    return bool(await db.scalar(statement))


async def create_decision(
    db: AsyncSession, data: RoomAutoRoutingDecisionCreate
) -> tuple[RoomAutoRoutingDecision, bool]:
    """Create once per accepted root submission; retries get the original snapshot.

    Call while holding the conversation turn lock. The uniqueness constraint is a
    second line of defence; fields on an existing decision are never refreshed
    from a later policy, candidate roster or transcript.
    """
    existing = await decision_for_submission(
        db, data.organization_id, data.conversation_id, data.submission_id, lock=True
    )
    if existing is not None:
        return existing, False
    row = RoomAutoRoutingDecision(
        organization_id=data.organization_id,
        conversation_id=data.conversation_id,
        submission_id=data.submission_id,
        accepted_message_id=data.accepted_message_id,
        requester_id=data.requester_id,
        thread_root_message_id=data.thread_root_message_id,
        accepted_at=data.accepted_at,
        ready_at=data.ready_at,
        policy_revision=data.policy_revision,
        router_agent_id=data.router_agent_id,
        router_name=data.router_name,
        router_version_id=data.router_version_id,
        model_profile_id=data.model_profile_id,
        model_profile_fingerprint=data.model_profile_fingerprint,
        quiet_window_ms=data.quiet_window_ms,
        max_responders=data.max_responders,
        candidate_snapshot=[
            candidate.model_dump(mode="json") for candidate in data.candidate_snapshot
        ],
        grouped_message_ids=[str(message_id) for message_id in data.grouped_message_ids],
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row, True


async def ready_decisions(
    db: AsyncSession,
    *,
    organization_id: UUID | None,
    now: datetime,
    limit: int = 100,
) -> list[RoomAutoRoutingDecision]:
    """Select due decisions that do not overtake earlier work in their room."""
    root = aliased(RoomSubmission)
    earlier = aliased(RoomSubmission)
    earlier_parent = aliased(RoomSubmission)
    earlier_root = aliased(RoomSubmission)
    prior_decision = aliased(RoomAutoRoutingDecision)
    earlier_order = func.coalesce(earlier_parent.ordinal, earlier.ordinal)
    earlier_active = (
        select(earlier.id)
        .select_from(earlier)
        .outerjoin(earlier_parent, earlier_parent.id == earlier.parent_submission_id)
        .where(
            earlier.organization_id == RoomAutoRoutingDecision.organization_id,
            earlier.conversation_id == RoomAutoRoutingDecision.conversation_id,
            earlier.status.in_(_ACTIVE_RESPONDER_STATUSES),
            earlier_order < root.ordinal,
        )
    )
    prior_routing = (
        select(prior_decision.id)
        .select_from(prior_decision)
        .join(earlier_root, earlier_root.id == prior_decision.submission_id)
        .where(
            prior_decision.organization_id == RoomAutoRoutingDecision.organization_id,
            prior_decision.conversation_id == RoomAutoRoutingDecision.conversation_id,
            prior_decision.status.in_(_BLOCKING_ROUTER_STATUSES),
            earlier_root.ordinal < root.ordinal,
        )
    )
    query = (
        select(RoomAutoRoutingDecision)
        .join(root, root.id == RoomAutoRoutingDecision.submission_id)
        .where(
            RoomAutoRoutingDecision.status == "pending",
            RoomAutoRoutingDecision.ready_at <= now,
            ~exists(earlier_active),
            ~exists(prior_routing),
        )
        .order_by(RoomAutoRoutingDecision.ready_at, RoomAutoRoutingDecision.id)
        .limit(limit)
    )
    if organization_id is not None:
        query = query.where(RoomAutoRoutingDecision.organization_id == organization_id)
    return list((await db.scalars(query)).all())


async def claim_ready_decision(
    db: AsyncSession,
    decision_id: UUID,
    *,
    organization_id: UUID,
    now: datetime,
    claim_token: UUID,
) -> RoomAutoRoutingDecision | None:
    """Atomically claim a due pending decision; never reclaim running or unknown."""
    statement = (
        update(RoomAutoRoutingDecision)
        .where(
            RoomAutoRoutingDecision.id == decision_id,
            RoomAutoRoutingDecision.organization_id == organization_id,
            RoomAutoRoutingDecision.status == "pending",
            RoomAutoRoutingDecision.ready_at <= now,
        )
        .values(
            status="running",
            claim_token=claim_token,
            claim_generation=RoomAutoRoutingDecision.claim_generation + 1,
            claimed_at=now,
        )
        .returning(RoomAutoRoutingDecision)
        .execution_options(populate_existing=True)
    )
    return await db.scalar(statement)


async def finish_decision(
    db: AsyncSession,
    decision_id: UUID,
    *,
    organization_id: UUID,
    claim_token: UUID,
    claim_generation: int,
    receipt: RoomAutoRoutingDecisionReceipt,
    finished_at: datetime,
) -> bool:
    """Settle only the live claim whose token and generation started this call."""
    statement = (
        update(RoomAutoRoutingDecision)
        .where(
            RoomAutoRoutingDecision.id == decision_id,
            RoomAutoRoutingDecision.organization_id == organization_id,
            RoomAutoRoutingDecision.status == "running",
            RoomAutoRoutingDecision.claim_token == claim_token,
            RoomAutoRoutingDecision.claim_generation == claim_generation,
        )
        .values(
            status=receipt.status,
            grouped_message_ids=[str(message_id) for message_id in receipt.grouped_message_ids],
            decision_output=(
                None
                if receipt.decision_output is None
                else receipt.decision_output.model_dump(mode="json")
            ),
            router_run_id=receipt.router_run_id,
            error_code=receipt.error_code,
            finished_at=finished_at,
        )
        .returning(RoomAutoRoutingDecision.id)
    )
    return await db.scalar(statement) is not None


async def continue_pending_decision(
    db: AsyncSession,
    decision_id: UUID,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    continued_into_id: UUID,
    finished_at: datetime,
) -> bool:
    """Mark an unclaimed earlier burst item continued into a newer group."""
    statement = (
        update(RoomAutoRoutingDecision)
        .where(
            RoomAutoRoutingDecision.id == decision_id,
            RoomAutoRoutingDecision.organization_id == organization_id,
            RoomAutoRoutingDecision.conversation_id == conversation_id,
            RoomAutoRoutingDecision.status == "pending",
        )
        .values(
            status="continued",
            continued_into_id=continued_into_id,
            finished_at=finished_at,
        )
        .returning(RoomAutoRoutingDecision.id)
    )
    return await db.scalar(statement) is not None


async def mark_stale_running_unknown(
    db: AsyncSession,
    *,
    organization_id: UUID | None,
    before: datetime,
    now: datetime,
    limit: int = 100,
) -> int:
    """Close stale claims as unknown; never put them back into the queue."""
    stale_query = (
        select(RoomAutoRoutingDecision.id)
        .where(
            RoomAutoRoutingDecision.status == "running",
            RoomAutoRoutingDecision.claimed_at <= before,
        )
        .order_by(RoomAutoRoutingDecision.claimed_at, RoomAutoRoutingDecision.id)
        .limit(limit)
    )
    if organization_id is not None:
        stale_query = stale_query.where(RoomAutoRoutingDecision.organization_id == organization_id)
    stale_ids = stale_query
    statement = (
        update(RoomAutoRoutingDecision)
        .where(
            RoomAutoRoutingDecision.id.in_(stale_ids),
            RoomAutoRoutingDecision.status == "running",
            RoomAutoRoutingDecision.claimed_at <= before,
        )
        .values(status="unknown", error_code="worker_lost_after_claim", finished_at=now)
        .returning(RoomAutoRoutingDecision.id)
    )
    result = await db.scalars(statement)
    return len(result.all())


async def pending_same_author_thread(
    db: AsyncSession,
    *,
    latest: RoomAutoRoutingDecision,
    maximum_group_size: int = 8,
) -> list[RoomAutoRoutingDecision]:
    """Return only immediately preceding compatible pending posts in the burst.

    The latest decision owns the provider call. Any explicit request, Ask Lead
    request, different author/thread, missing decision or changed frozen policy
    between it and an older post is a barrier. The returned rows are chronological
    and exclude latest; the caller marks each one continued into latest while
    holding the conversation-turn lock.
    """
    if not 1 <= maximum_group_size <= 8:
        raise ValueError("maximum_group_size must be between one and eight")
    latest_group_size = len(latest.grouped_message_ids)
    if not 1 <= latest_group_size <= maximum_group_size:
        return []
    latest_submission = await db.scalar(
        select(RoomSubmission).where(
            RoomSubmission.organization_id == latest.organization_id,
            RoomSubmission.conversation_id == latest.conversation_id,
            RoomSubmission.id == latest.submission_id,
            RoomSubmission.parent_submission_id.is_(None),
        )
    )
    if latest_submission is None or maximum_group_size == 1:
        return []

    cutoff = latest.accepted_at - timedelta(milliseconds=latest.quiet_window_ms)
    query = (
        select(RoomSubmission, RoomAutoRoutingDecision)
        .outerjoin(
            RoomAutoRoutingDecision,
            (RoomAutoRoutingDecision.organization_id == RoomSubmission.organization_id)
            & (RoomAutoRoutingDecision.conversation_id == RoomSubmission.conversation_id)
            & (RoomAutoRoutingDecision.submission_id == RoomSubmission.id),
        )
        .where(
            RoomSubmission.organization_id == latest.organization_id,
            RoomSubmission.conversation_id == latest.conversation_id,
            RoomSubmission.parent_submission_id.is_(None),
            RoomSubmission.ordinal < latest_submission.ordinal,
            RoomSubmission.created_at >= cutoff,
            RoomSubmission.created_at <= latest.accepted_at,
        )
        .order_by(RoomSubmission.ordinal.desc())
        .limit(maximum_group_size - 1)
    )
    rows = (await db.execute(query)).all()
    collected: list[RoomAutoRoutingDecision] = []
    grouped_count = latest_group_size
    grouped_ids = {str(value) for value in latest.grouped_message_ids}
    for submission, decision in rows:
        if (
            submission.requester_id != latest.requester_id
            or submission.thread_root_message_id != latest.thread_root_message_id
            or submission.intent != "message"
            or submission.dispatch != "automatic"
            or decision is None
            or decision.status != "pending"
            or decision.policy_revision != latest.policy_revision
            or decision.router_agent_id != latest.router_agent_id
            or decision.router_version_id != latest.router_version_id
            or decision.model_profile_id != latest.model_profile_id
            or decision.model_profile_fingerprint != latest.model_profile_fingerprint
            or decision.quiet_window_ms != latest.quiet_window_ms
            or decision.max_responders != latest.max_responders
            or decision.candidate_snapshot != latest.candidate_snapshot
        ):
            break
        prior_ids = [str(value) for value in decision.grouped_message_ids]
        if (
            not prior_ids
            or grouped_count + len(prior_ids) > maximum_group_size
            or grouped_ids.intersection(prior_ids)
            or len(prior_ids) != len(set(prior_ids))
        ):
            # Keep the complete earlier pending group intact so it can be routed
            # on its own. Trimming it here would silently lose accepted posts.
            break
        grouped_count += len(prior_ids)
        grouped_ids.update(prior_ids)
        collected.append(decision)
    return list(reversed(collected))


async def update_pending_group(
    db: AsyncSession,
    decision_id: UUID,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    grouped_message_ids: list[UUID],
    ready_at: datetime,
) -> bool:
    """Update only the latest unclaimed pending burst decision."""
    if not 1 <= len(grouped_message_ids) <= 8 or len(set(grouped_message_ids)) != len(
        grouped_message_ids
    ):
        raise ValueError("grouped_message_ids must contain one to eight unique ids")
    statement = (
        update(RoomAutoRoutingDecision)
        .where(
            RoomAutoRoutingDecision.id == decision_id,
            RoomAutoRoutingDecision.organization_id == organization_id,
            RoomAutoRoutingDecision.conversation_id == conversation_id,
            RoomAutoRoutingDecision.status == "pending",
            RoomAutoRoutingDecision.claim_token.is_(None),
        )
        .values(
            grouped_message_ids=[str(message_id) for message_id in grouped_message_ids],
            ready_at=ready_at,
        )
        .returning(RoomAutoRoutingDecision.id)
    )
    return await db.scalar(statement) is not None


async def room_has_pending_or_running(
    db: AsyncSession, *, organization_id: UUID, conversation_id: UUID
) -> bool:
    return bool(
        await db.scalar(
            select(
                exists().where(
                    RoomAutoRoutingDecision.organization_id == organization_id,
                    RoomAutoRoutingDecision.conversation_id == conversation_id,
                    RoomAutoRoutingDecision.status.in_(("pending", "running")),
                )
            )
        )
    )
