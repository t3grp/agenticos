"""Service tests for browser-teaching state, authority and procedure discovery."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    AuthorizationError,
    BadRequestError,
    ConcurrentChangeError,
    NotFoundError,
)
from app.core.permissions import AuthContext
from app.db.models.browser_teaching import BrowserTeachingSession
from app.db.models.skill_proposal import ProposalStatus
from app.db.models.skill_version import SkillVersionSource
from app.schemas.browser_teaching import (
    BrowserTeachingAction,
    BrowserTeachingParameter,
    BrowserTeachingReview,
    BrowserTeachingStart,
    BrowserTeachingStepReview,
)
from app.services import browser_teaching as service_module
from app.services.browser_teaching import BrowserTeachingService

pytestmark = pytest.mark.anyio


def _session(
    *, status: str = "recording", events: list[dict[str, object]] | None = None
) -> BrowserTeachingSession:
    now = datetime.now(UTC)
    return BrowserTeachingSession(
        id=uuid4(),
        organization_id=uuid4(),
        owner_user_id=uuid4(),
        agent_id=uuid4(),
        run_id=uuid4(),
        call_id="synthetic-call",
        lease_id="synthetic-lease",
        idempotency_key="synthetic-start",
        origin="https://portal.example.test",
        status=status,
        revision=1,
        events=events or [],
        parameters=[],
        expires_at=now + timedelta(hours=1),
        capture_until=now + timedelta(minutes=5),
    )


def _context(row: BrowserTeachingSession, **changes: object) -> AuthContext:
    return AuthContext(
        user_id=row.owner_user_id,
        organization_id=row.organization_id,
        role="owner",
        **changes,
    )


def _session_db(row: BrowserTeachingSession | None = None) -> AsyncMock:
    db = AsyncMock()
    db.add = Mock()
    db.execute.return_value = Mock(scalar_one_or_none=lambda: row)
    db.begin_nested = Mock(side_effect=lambda: _yield_db(db))
    return db


@asynccontextmanager
async def _yield_db(db: AsyncMock):
    yield db


def _enable(monkeypatch: pytest.MonkeyPatch, enabled: bool = True) -> None:
    monkeypatch.setattr(service_module.settings, "BROWSER_TEACHING_ENABLED", enabled)


def _browser_meta(row: BrowserTeachingSession) -> SimpleNamespace:
    return SimpleNamespace(
        organization_id=row.organization_id,
        agent_id=row.agent_id,
        user_id=str(row.owner_user_id),
    )


async def _start_dependencies(
    monkeypatch: pytest.MonkeyPatch, row: BrowserTeachingSession, *, access: bool = True
) -> None:
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "recording_for_browser",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        service_module.live_browse, "metadata", AsyncMock(return_value=_browser_meta(row))
    )
    monkeypatch.setattr(
        service_module.live_browse, "lease_owner", AsyncMock(return_value=str(row.owner_user_id))
    )
    monkeypatch.setattr(
        service_module.live_browse, "lease_id", AsyncMock(return_value=row.lease_id)
    )
    monkeypatch.setattr(
        service_module.live_browse,
        "origin",
        AsyncMock(return_value="https://portal.example.test/tasks?token=synthetic"),
    )
    monkeypatch.setattr(service_module.agent_repo, "get", AsyncMock(return_value=object()))
    monkeypatch.setattr(service_module, "resolve_access", AsyncMock(return_value=access))


async def test_start_refuses_when_the_deployment_flag_is_off(monkeypatch) -> None:
    row = _session()
    _enable(monkeypatch, False)
    service = BrowserTeachingService(_session_db())

    with pytest.raises(NotFoundError):
        await service.start(
            _context(row),
            row.owner_user_id,
            BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
            str(uuid4()),
        )


@pytest.mark.parametrize("identity", ["api_key", "impersonation"])
async def test_start_requires_the_person_who_holds_the_browser(monkeypatch, identity: str) -> None:
    row = _session()
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row)
    service = BrowserTeachingService(_session_db())
    context = (
        _context(row, api_key_id=uuid4())
        if identity == "api_key"
        else _context(row, is_impersonated=True)
    )

    with pytest.raises(AuthorizationError, match="signed-in lease owner"):
        await service.start(
            context,
            row.owner_user_id,
            BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
            str(uuid4()),
        )


@pytest.mark.parametrize("failure", ["missing", "organization", "agent", "user", "owner", "lease"])
async def test_start_refuses_a_browser_that_the_member_does_not_own(
    monkeypatch, failure: str
) -> None:
    row = _session()
    _enable(monkeypatch)
    meta = _browser_meta(row)
    owner = str(row.owner_user_id)
    lease = row.lease_id
    if failure == "missing":
        meta = None
    elif failure == "organization":
        meta.organization_id = uuid4()
    elif failure == "agent":
        meta.agent_id = None
    elif failure == "user":
        meta.user_id = str(uuid4())
    elif failure == "owner":
        owner = str(uuid4())
    else:
        lease = str(uuid4())
    monkeypatch.setattr(service_module.live_browse, "metadata", AsyncMock(return_value=meta))
    monkeypatch.setattr(service_module.live_browse, "lease_owner", AsyncMock(return_value=owner))
    monkeypatch.setattr(service_module.live_browse, "lease_id", AsyncMock(return_value=lease))
    service = BrowserTeachingService(_session_db())

    with pytest.raises(AuthorizationError, match="Take over"):
        await service.start(
            _context(row),
            row.owner_user_id,
            BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
            str(uuid4()),
        )


@pytest.mark.security
@pytest.mark.parametrize("agent_exists, access", [(False, True), (True, False)])
async def test_start_requires_current_permission_to_run_the_agent(
    monkeypatch, agent_exists: bool, access: bool
) -> None:
    row = _session()
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row, access=access)
    monkeypatch.setattr(
        service_module.agent_repo,
        "get",
        AsyncMock(return_value=object() if agent_exists else None),
    )
    service = BrowserTeachingService(_session_db())

    with pytest.raises(AuthorizationError, match="agent's browser"):
        await service.start(
            _context(row),
            row.owner_user_id,
            BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
            str(uuid4()),
        )


async def test_start_stores_only_the_origin_and_returns_the_new_session(monkeypatch) -> None:
    row = _session()
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row)
    db = _session_db()

    async def assign_id(value: BrowserTeachingSession) -> None:
        value.id = value.id or uuid4()

    db.refresh.side_effect = assign_id
    service = BrowserTeachingService(db)
    result = await service.start(
        _context(row),
        row.owner_user_id,
        BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
        str(uuid4()),
    )

    assert result.status == "recording"
    assert result.origin == row.origin
    assert result.id is not None
    created = db.add.call_args.args[0]
    assert created.origin == "https://portal.example.test"
    assert "synthetic" not in created.origin


@pytest.mark.parametrize("mismatch", ["owner", "run", "call", "lease"])
async def test_start_does_not_reuse_an_idempotency_key_for_another_browser(
    monkeypatch, mismatch: str
) -> None:
    row = _session()
    existing = _session()
    if mismatch == "owner":
        existing.owner_user_id = uuid4()
    elif mismatch == "run":
        existing.run_id = uuid4()
    elif mismatch == "call":
        existing.call_id = "different-call"
    else:
        existing.lease_id = "different-lease"
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row)
    service = BrowserTeachingService(_session_db(existing))

    with pytest.raises(AuthorizationError, match="another browser"):
        await service.start(
            _context(row),
            row.owner_user_id,
            BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
            existing.idempotency_key,
        )


async def test_start_reuses_an_identical_idempotent_request(monkeypatch) -> None:
    row = _session()
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row)
    service = BrowserTeachingService(_session_db(row))

    result = await service.start(
        _context(row),
        row.owner_user_id,
        BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
        row.idempotency_key,
    )

    assert result.id == row.id
    assert result.status == "recording"


async def test_start_reuses_the_active_session_when_a_retry_uses_a_new_key(
    monkeypatch,
) -> None:
    row = _session()
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row)
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "recording_for_browser",
        AsyncMock(return_value=[row]),
    )
    db = _session_db()

    async def assign_id(value: BrowserTeachingSession) -> None:
        value.id = value.id or uuid4()

    db.refresh.side_effect = assign_id
    service = BrowserTeachingService(db)

    result = await service.start(
        _context(row),
        row.owner_user_id,
        BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
        str(uuid4()),
    )

    assert result.id == row.id
    assert result.status == "recording"
    db.add.assert_not_called()


@pytest.mark.parametrize("stale_state", ["expired", "duration_limit", "lease_lost"])
async def test_start_pauses_a_stale_recording_before_creating_a_new_session(
    monkeypatch, stale_state: str
) -> None:
    active = _session()
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, active)
    if stale_state == "expired":
        active.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        active.events = [{"event_id": str(uuid4())}]
    elif stale_state == "duration_limit":
        active.capture_until = datetime.now(UTC) - timedelta(seconds=1)
    else:
        active.lease_id = "old-socket"
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "recording_for_browser",
        AsyncMock(return_value=[active]),
    )
    db = _session_db()

    async def assign_id(value: BrowserTeachingSession) -> None:
        value.id = value.id or uuid4()

    db.refresh.side_effect = assign_id
    service = BrowserTeachingService(db)

    result = await service.start(
        _context(active),
        active.owner_user_id,
        BrowserTeachingStart(
            run_id=active.run_id, call_id=active.call_id, lease_id="synthetic-lease"
        ),
        str(uuid4()),
    )

    assert result.id != active.id
    assert result.status == "recording"
    assert active.status == ("expired" if stale_state == "expired" else "paused")
    assert active.pause_reason == stale_state
    if stale_state == "expired":
        assert active.events == []
    db.add.assert_called_once()


async def test_start_pauses_duplicate_active_rows_but_reuses_one(monkeypatch) -> None:
    row = _session()
    duplicate = _session()
    duplicate.run_id = row.run_id
    duplicate.call_id = row.call_id
    duplicate.owner_user_id = row.owner_user_id
    duplicate.lease_id = row.lease_id
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row)
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "recording_for_browser",
        AsyncMock(return_value=[row, duplicate]),
    )
    service = BrowserTeachingService(_session_db())

    result = await service.start(
        _context(row),
        row.owner_user_id,
        BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
        str(uuid4()),
    )

    assert result.id == row.id
    assert duplicate.status == "paused"
    assert duplicate.pause_reason == "lease_lost"


async def test_start_reuses_the_winning_recording_after_a_unique_conflict(monkeypatch) -> None:
    row = _session()
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row)
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "get_by_idempotency_key",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "recording_for_browser",
        AsyncMock(side_effect=[[], [row]]),
    )
    db = _session_db()
    db.flush.side_effect = IntegrityError("INSERT", {}, Exception("unique conflict"))
    service = BrowserTeachingService(db)

    result = await service.start(
        _context(row),
        row.owner_user_id,
        BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
        str(uuid4()),
    )

    assert result.id == row.id
    assert result.status == "recording"
    db.begin_nested.assert_called_once()


async def test_start_reuses_an_idempotent_winner_after_a_unique_conflict(monkeypatch) -> None:
    row = _session()
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row)
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "get_by_idempotency_key",
        AsyncMock(side_effect=[None, row]),
    )
    db = _session_db()
    db.flush.side_effect = IntegrityError("INSERT", {}, Exception("unique conflict"))
    service = BrowserTeachingService(db)

    result = await service.start(
        _context(row),
        row.owner_user_id,
        BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
        row.idempotency_key,
    )

    assert result.id == row.id
    assert result.status == "recording"


async def test_start_refuses_an_idempotency_conflict_for_another_browser(monkeypatch) -> None:
    row = _session()
    other = _session()
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row)
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "get_by_idempotency_key",
        AsyncMock(side_effect=[None, other]),
    )
    db = _session_db()
    db.flush.side_effect = IntegrityError("INSERT", {}, Exception("unique conflict"))
    service = BrowserTeachingService(db)

    with pytest.raises(AuthorizationError, match="another browser"):
        await service.start(
            _context(row),
            row.owner_user_id,
            BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
            str(uuid4()),
        )


async def test_start_refuses_a_unique_conflict_owned_by_another_lease(monkeypatch) -> None:
    row = _session()
    other = _session()
    other.organization_id = row.organization_id
    other.run_id = row.run_id
    other.call_id = row.call_id
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row)
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "get_by_idempotency_key",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "recording_for_browser",
        AsyncMock(side_effect=[[], [other]]),
    )
    db = _session_db()
    db.flush.side_effect = IntegrityError("INSERT", {}, Exception("unique conflict"))
    service = BrowserTeachingService(db)

    with pytest.raises(ConcurrentChangeError, match="Another teaching session"):
        await service.start(
            _context(row),
            row.owner_user_id,
            BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
            str(uuid4()),
        )


async def test_start_reraises_an_unrelated_integrity_error(monkeypatch) -> None:
    row = _session()
    _enable(monkeypatch)
    await _start_dependencies(monkeypatch, row)
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "get_by_idempotency_key",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        service_module.browser_teaching_repo,
        "recording_for_browser",
        AsyncMock(side_effect=[[], []]),
    )
    error = IntegrityError("INSERT", {}, Exception("unrelated constraint"))
    db = _session_db()
    db.flush.side_effect = error
    service = BrowserTeachingService(db)

    with pytest.raises(IntegrityError) as caught:
        await service.start(
            _context(row),
            row.owner_user_id,
            BrowserTeachingStart(run_id=row.run_id, call_id=row.call_id, lease_id=row.lease_id),
            str(uuid4()),
        )

    assert caught.value is error


async def test_record_input_ignores_unknown_actions_without_opening_a_session(monkeypatch) -> None:
    async def forbidden_context():
        raise AssertionError("unknown actions must not open a database session")

    monkeypatch.setattr("app.db.session.get_db_context", forbidden_context)
    await BrowserTeachingService.record_input(
        uuid4(), uuid4(), uuid4(), "call", "lease", uuid4(), "key", "https://portal.example.test"
    )


async def test_record_input_appends_a_redacted_step_and_flushes_it(monkeypatch) -> None:
    row = _session()
    _enable(monkeypatch)
    db = _session_db(row)
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))
    monkeypatch.setattr(
        service_module.live_browse,
        "lease_owner",
        AsyncMock(return_value=str(row.owner_user_id)),
    )
    monkeypatch.setattr(
        service_module.live_browse, "lease_id", AsyncMock(return_value=row.lease_id)
    )

    await BrowserTeachingService.record_input(
        row.organization_id,
        row.owner_user_id,
        row.run_id,
        row.call_id,
        row.lease_id,
        uuid4(),
        "click",
        row.origin + "/invoice?token=synthetic",
        {"role": "button", "fingerprint": "a" * 64},
    )

    assert len(row.events) == 1
    assert row.events[0]["origin"] == row.origin
    assert "synthetic" not in str(row.events)
    db.flush.assert_awaited_once()


async def test_record_input_does_not_flush_a_replayed_event(monkeypatch) -> None:
    event_id = uuid4()
    row = _session(
        events=[
            {
                "event_id": str(event_id),
                "sequence": 0,
                "action": "click",
                "origin": "https://portal.example.test",
            }
        ]
    )
    _enable(monkeypatch)
    db = _session_db(row)
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))
    monkeypatch.setattr(
        service_module.live_browse, "lease_owner", AsyncMock(return_value=str(row.owner_user_id))
    )
    monkeypatch.setattr(
        service_module.live_browse, "lease_id", AsyncMock(return_value=row.lease_id)
    )

    await BrowserTeachingService.record_input(
        row.organization_id,
        row.owner_user_id,
        row.run_id,
        row.call_id,
        row.lease_id,
        event_id,
        "click",
        row.origin,
    )

    assert len(row.events) == 1
    db.flush.assert_not_awaited()


async def test_record_input_pauses_when_the_deployment_flag_is_turned_off(monkeypatch) -> None:
    row = _session()
    _enable(monkeypatch, False)
    db = _session_db(row)
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))

    await BrowserTeachingService.record_input(
        row.organization_id,
        row.owner_user_id,
        row.run_id,
        row.call_id,
        row.lease_id,
        uuid4(),
        "click",
        row.origin,
    )

    assert row.status == "paused"
    assert row.pause_reason == "feature_disabled"
    assert not row.events
    db.flush.assert_awaited_once()


async def test_record_input_returns_when_no_recording_session_exists(monkeypatch) -> None:
    _enable(monkeypatch)
    db = _session_db(None)
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))

    await BrowserTeachingService.record_input(
        uuid4(), uuid4(), uuid4(), "call", "lease", uuid4(), "click", "https://portal.example.test"
    )

    db.flush.assert_not_awaited()


async def test_pause_and_stop_close_capture_without_changing_the_steps(monkeypatch) -> None:
    row = _session(
        events=[
            {
                "event_id": str(uuid4()),
                "sequence": 0,
                "action": "scroll",
                "origin": "https://portal.example.test",
            }
        ]
    )
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    context = _context(row)

    paused = await service.transition(
        context, row.owner_user_id, row.id, BrowserTeachingAction(action="pause", revision=1)
    )
    stopped = await service.stop(context, row.owner_user_id, row.id)

    assert paused.status == "paused"
    assert stopped.status == "review"
    assert len(stopped.steps) == 1


async def test_transition_rejects_a_stale_revision_or_terminal_status(monkeypatch) -> None:
    row = _session(status="review")
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))

    with pytest.raises(ConcurrentChangeError):
        await service.transition(
            _context(row),
            row.owner_user_id,
            row.id,
            BrowserTeachingAction(action="pause", revision=2),
        )
    with pytest.raises(BadRequestError, match="no longer accepts"):
        await service.transition(
            _context(row),
            row.owner_user_id,
            row.id,
            BrowserTeachingAction(action="pause", revision=1),
        )


@pytest.mark.security
async def test_resume_requires_feature_flag_agent_permission_and_exact_lease(monkeypatch) -> None:
    row = _session(status="paused")
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    context = _context(row)
    data = BrowserTeachingAction(action="resume", revision=1)

    _enable(monkeypatch, False)
    with pytest.raises(NotFoundError):
        await service.transition(context, row.owner_user_id, row.id, data)

    _enable(monkeypatch)
    monkeypatch.setattr(service_module.agent_repo, "get", AsyncMock(return_value=object()))
    monkeypatch.setattr(service_module, "resolve_access", AsyncMock(return_value=False))
    with pytest.raises(AuthorizationError, match="agent's browser"):
        await service.transition(context, row.owner_user_id, row.id, data)

    monkeypatch.setattr(service_module, "resolve_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service_module.live_browse, "lease_owner", AsyncMock(return_value="other"))
    monkeypatch.setattr(
        service_module.live_browse, "lease_id", AsyncMock(return_value="other-lease")
    )
    with pytest.raises(AuthorizationError, match="Take over"):
        await service.transition(context, row.owner_user_id, row.id, data)


async def test_resume_with_current_agent_authority_and_lease_restarts_capture(monkeypatch) -> None:
    row = _session(status="paused")
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    monkeypatch.setattr(service_module.agent_repo, "get", AsyncMock(return_value=object()))
    monkeypatch.setattr(service_module, "resolve_access", AsyncMock(return_value=True))
    monkeypatch.setattr(
        service_module.live_browse, "lease_owner", AsyncMock(return_value=str(row.owner_user_id))
    )
    monkeypatch.setattr(
        service_module.live_browse, "lease_id", AsyncMock(return_value=row.lease_id)
    )
    _enable(monkeypatch)

    result = await service.transition(
        _context(row), row.owner_user_id, row.id, BrowserTeachingAction(action="resume", revision=1)
    )

    assert result.status == "recording"
    assert result.pause_reason is None
    assert result.revision == 2


@pytest.mark.security
async def test_review_refuses_wrong_steps_duplicate_parameters_and_secret_labels(
    monkeypatch,
) -> None:
    event_id = uuid4()
    click_id = uuid4()
    row = _session(
        status="review",
        events=[
            {
                "event_id": str(event_id),
                "sequence": 0,
                "action": "input_parameter",
                "origin": "https://portal.example.test",
            },
            {
                "event_id": str(click_id),
                "sequence": 1,
                "action": "click",
                "origin": "https://portal.example.test",
            },
        ],
    )
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    context = _context(row)

    wrong_parameter = BrowserTeachingReview(
        revision=1,
        steps=[BrowserTeachingStepReview(event_id=click_id, reviewed_label="Open invoices")],
        parameters=[BrowserTeachingParameter(event_id=uuid4(), name="invoice", label="Invoice")],
    )
    with pytest.raises(BadRequestError, match="Match each parameter"):
        await service.review(context, row.owner_user_id, row.id, wrong_parameter)

    wrong_click = BrowserTeachingReview(
        revision=1,
        steps=[],
        parameters=[BrowserTeachingParameter(event_id=event_id, name="invoice", label="Invoice")],
    )
    with pytest.raises(BadRequestError, match="reviewed label for each captured click"):
        await service.review(context, row.owner_user_id, row.id, wrong_click)

    secret = BrowserTeachingReview(
        revision=1,
        steps=[BrowserTeachingStepReview(event_id=click_id, reviewed_label="Password button")],
        parameters=[BrowserTeachingParameter(event_id=event_id, name="invoice", label="Invoice")],
    )
    with pytest.raises(BadRequestError, match="Step labels"):
        await service.review(context, row.owner_user_id, row.id, secret)


async def test_review_requires_a_captured_review_state_and_unique_parameter_names(
    monkeypatch,
) -> None:
    row = _session(status="recording")
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    context = _context(row)
    review = BrowserTeachingReview(revision=1, steps=[], parameters=[])

    with pytest.raises(BadRequestError, match="Stop teaching"):
        await service.review(context, row.owner_user_id, row.id, review)

    row.status = "review"
    event_id = uuid4()
    row.events = [
        {
            "event_id": str(event_id),
            "sequence": 0,
            "action": "input_parameter",
            "origin": row.origin,
        },
        {
            "event_id": str(uuid4()),
            "sequence": 1,
            "action": "input_parameter",
            "origin": row.origin,
        },
    ]
    duplicate_names = BrowserTeachingReview(
        revision=1,
        steps=[],
        parameters=[
            BrowserTeachingParameter(event_id=uuid4(), name="invoice", label="Invoice"),
            BrowserTeachingParameter(event_id=uuid4(), name="invoice", label="Invoice again"),
        ],
    )
    with pytest.raises(BadRequestError, match="unique"):
        await service.review(context, row.owner_user_id, row.id, duplicate_names)


async def test_review_requires_one_safe_parameter_per_captured_input(monkeypatch) -> None:
    event_id = uuid4()
    row = _session(
        status="review",
        events=[
            {
                "event_id": str(event_id),
                "sequence": 0,
                "action": "input_parameter",
                "origin": "https://portal.example.test",
            }
        ],
    )
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    context = _context(row)

    with pytest.raises(BadRequestError, match="one parameter"):
        await service.review(
            context,
            row.owner_user_id,
            row.id,
            BrowserTeachingReview(revision=1, steps=[], parameters=[]),
        )

    unsafe = BrowserTeachingReview(
        revision=1,
        steps=[],
        parameters=[BrowserTeachingParameter(event_id=event_id, name="invoice", label="Password")],
    )
    with pytest.raises(BadRequestError, match="credentials or secrets"):
        await service.review(context, row.owner_user_id, row.id, unsafe)


async def test_stop_is_idempotent_for_a_closed_session(monkeypatch) -> None:
    row = _session(status="proposed")
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))

    result = await service.stop(_context(row), row.owner_user_id, row.id)

    assert result.status == "proposed"
    assert result.revision == 1


async def test_discard_erases_review_data_and_refuses_terminal_sessions(monkeypatch) -> None:
    row = _session(status="review", events=[{"event_id": str(uuid4())}])
    row.parameters = [{"name": "invoice"}]
    db = _session_db()
    service = BrowserTeachingService(db)
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))

    await service.discard(_context(row), row.owner_user_id, row.id)

    assert row.status == "discarded"
    assert row.events == []
    assert row.parameters == []
    assert row.revision == 2

    with pytest.raises(BadRequestError, match="cannot be discarded"):
        await service.discard(_context(row), row.owner_user_id, row.id)


async def test_propose_refuses_unavailable_authority_bad_labels_or_incomplete_runs(
    monkeypatch,
) -> None:
    row = _session(status="review", events=[{"action": "click", "event_id": str(uuid4())}])
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    monkeypatch.setattr(service_module.agent_repo, "get", AsyncMock(return_value=None))
    context = _context(row)

    with pytest.raises(AuthorizationError):
        await service.propose(context, row.owner_user_id, row.id, name="Invoices", description="")

    monkeypatch.setattr(service_module.agent_repo, "get", AsyncMock(return_value=object()))
    monkeypatch.setattr(service_module, "resolve_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service_module.live_browse, "outcome", AsyncMock(return_value="blocked"))
    with pytest.raises(BadRequestError, match="credentials"):
        await service.propose(context, row.owner_user_id, row.id, name="Password", description="")

    _enable(monkeypatch, False)
    with pytest.raises(NotFoundError):
        await service.propose(context, row.owner_user_id, row.id, name="Invoices", description="")

    _enable(monkeypatch)
    row.status = "paused"
    with pytest.raises(BadRequestError, match="Review a successful demonstration"):
        await service.propose(context, row.owner_user_id, row.id, name="Invoices", description="")
    row.status = "review"
    row.events = []
    with pytest.raises(BadRequestError, match="Review a successful demonstration"):
        await service.propose(context, row.owner_user_id, row.id, name="Invoices", description="")
    row.events = [{"action": "click", "event_id": str(uuid4())}]
    with pytest.raises(BadRequestError, match="did not finish successfully"):
        await service.propose(context, row.owner_user_id, row.id, name="Invoices", description="")


async def test_propose_requires_a_successful_browser_run(monkeypatch) -> None:
    event_id = str(uuid4())
    row = _session(
        status="review",
        events=[
            {
                "event_id": event_id,
                "sequence": 0,
                "action": "click",
                "origin": "https://portal.example.test",
                "reviewed_label": "Open invoices",
            }
        ],
    )
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    monkeypatch.setattr(service_module.agent_repo, "get", AsyncMock(return_value=object()))
    monkeypatch.setattr(service_module, "resolve_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service_module.live_browse, "outcome", AsyncMock(return_value="blocked"))
    _enable(monkeypatch)

    with pytest.raises(BadRequestError, match="did not finish successfully"):
        await service.propose(
            _context(row), row.owner_user_id, row.id, name="Invoices", description=""
        )


async def test_propose_creates_one_deterministic_proposal_and_erases_capture(monkeypatch) -> None:
    event_id = str(uuid4())
    row = _session(
        status="review",
        events=[
            {
                "event_id": event_id,
                "sequence": 0,
                "action": "click",
                "origin": "https://portal.example.test",
                "target_role": "button",
                "reviewed_label": "Open invoices",
            }
        ],
    )
    proposal = SimpleNamespace(id=uuid4(), source=None, rationale=None, browser_teaching_id=None)
    db = _session_db()
    service = BrowserTeachingService(db)
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    monkeypatch.setattr(service_module.agent_repo, "get", AsyncMock(return_value=object()))
    monkeypatch.setattr(service_module, "resolve_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service_module.live_browse, "outcome", AsyncMock(return_value="done"))
    monkeypatch.setattr(
        service_module.skill_proposal_repo,
        "get_by_browser_teaching",
        AsyncMock(return_value=None),
    )
    create = AsyncMock(return_value=proposal)
    monkeypatch.setattr(service_module.skill_proposal_repo, "create", create)
    _enable(monkeypatch)

    result = await service.propose(
        _context(row), row.owner_user_id, row.id, name="Invoices", description="Open a report."
    )

    assert result.status == "proposed"
    assert result.proposal_id == proposal.id
    assert row.events == []
    assert row.source_hash is not None
    assert proposal.browser_teaching_id == row.id
    assert proposal.source == SkillVersionSource.BROWSER_TEACHING.value
    assert "Open invoices" in create.call_args.kwargs["content"]
    assert (
        "https://portal.example.test"
        in create.call_args.kwargs["resources"]["browser-procedure.json"]
    )
    db.flush.assert_awaited_once()


async def test_propose_reuses_existing_provenance_and_existing_proposals(monkeypatch) -> None:
    row = _session(status="review", events=[{"event_id": str(uuid4()), "action": "click"}])
    existing = SimpleNamespace(id=uuid4())
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    monkeypatch.setattr(service_module.agent_repo, "get", AsyncMock(return_value=object()))
    monkeypatch.setattr(service_module, "resolve_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service_module.live_browse, "outcome", AsyncMock(return_value="done"))
    monkeypatch.setattr(
        service_module.skill_proposal_repo,
        "get_by_browser_teaching",
        AsyncMock(return_value=existing),
    )
    create = AsyncMock()
    monkeypatch.setattr(service_module.skill_proposal_repo, "create", create)
    _enable(monkeypatch)

    result = await service.propose(
        _context(row), row.owner_user_id, row.id, name="Invoices", description=""
    )

    assert result.proposal_id == existing.id
    assert result.status == "proposed"
    create.assert_not_awaited()

    row.status = "proposed"
    row.proposal_id = existing.id
    _enable(monkeypatch, False)
    already = await service.propose(
        _context(row), row.owner_user_id, row.id, name="Invoices", description=""
    )
    assert already.status == "proposed"


async def test_procedure_discovery_is_disabled_and_refuses_bad_run_inputs(monkeypatch) -> None:
    org_id, run_id, agent_id = uuid4(), uuid4(), uuid4()
    _enable(monkeypatch, False)
    assert (
        await BrowserTeachingService.procedure_for_origin(
            org_id, run_id, agent_id, "https://portal.example.test"
        )
        == []
    )

    _enable(monkeypatch)
    assert (
        await BrowserTeachingService.procedure_for_origin(
            org_id, run_id, agent_id, "http://portal.example.test"
        )
        == []
    )

    db = AsyncMock()
    db.get.return_value = None
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))
    assert (
        await BrowserTeachingService.procedure_for_origin(
            org_id, run_id, agent_id, "https://portal.example.test"
        )
        == []
    )


@pytest.mark.parametrize("mismatch", ["organization", "agent", "version"])
async def test_procedure_discovery_refuses_a_run_from_another_context(
    monkeypatch, mismatch
) -> None:
    org_id, run_id, agent_id, version_id = uuid4(), uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        organization_id=org_id,
        agent_id=agent_id,
        agent_version_id=version_id,
        created_at=datetime.now(UTC),
    )
    if mismatch == "organization":
        run.organization_id = uuid4()
    elif mismatch == "agent":
        run.agent_id = uuid4()
    else:
        run.agent_version_id = None
    db = AsyncMock()
    db.get.return_value = run
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))
    _enable(monkeypatch)

    assert (
        await BrowserTeachingService.procedure_for_origin(
            org_id, run_id, agent_id, "https://portal.example.test"
        )
        == []
    )
    if mismatch == "version":
        db.get.assert_awaited_once()


async def test_procedure_discovery_refuses_a_missing_or_mismatched_agent_version(
    monkeypatch,
) -> None:
    org_id, run_id, agent_id, version_id = uuid4(), uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        organization_id=org_id,
        agent_id=agent_id,
        agent_version_id=version_id,
        created_at=datetime.now(UTC),
    )
    db = AsyncMock()
    db.get.side_effect = [run, None]
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))
    _enable(monkeypatch)

    assert (
        await BrowserTeachingService.procedure_for_origin(
            org_id, run_id, agent_id, "https://portal.example.test"
        )
        == []
    )

    db.get.side_effect = [
        run,
        SimpleNamespace(organization_id=uuid4(), agent_id=agent_id, spec={}),
    ]
    assert (
        await BrowserTeachingService.procedure_for_origin(
            org_id, run_id, agent_id, "https://portal.example.test"
        )
        == []
    )


async def test_procedure_discovery_refuses_an_invalid_agent_spec(monkeypatch) -> None:
    org_id, run_id, agent_id, version_id = uuid4(), uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        organization_id=org_id,
        agent_id=agent_id,
        agent_version_id=version_id,
        created_at=datetime.now(UTC),
    )
    version = SimpleNamespace(organization_id=org_id, agent_id=agent_id, spec={})
    db = AsyncMock()
    db.get.side_effect = [run, version]
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))
    monkeypatch.setattr(
        service_module.AgentSpec,
        "model_validate",
        Mock(side_effect=ValidationError.from_exception_data("AgentSpec", [])),
    )
    _enable(monkeypatch)

    assert (
        await BrowserTeachingService.procedure_for_origin(
            org_id, run_id, agent_id, "https://portal.example.test"
        )
        == []
    )


@pytest.mark.parametrize("binding_kind", ["default", "fork", "version"])
async def test_procedure_discovery_uses_the_run_bound_skill_version(
    monkeypatch, binding_kind: str
) -> None:
    org_id, run_id, agent_id, version_id, skill_id = (uuid4(), uuid4(), uuid4(), uuid4(), uuid4())
    proposal_id = uuid4()
    run = SimpleNamespace(
        organization_id=org_id,
        agent_id=agent_id,
        agent_version_id=version_id,
        created_at=datetime.now(UTC),
    )
    version = SimpleNamespace(organization_id=org_id, agent_id=agent_id, spec={})
    if binding_kind == "default":
        binding = None
    elif binding_kind == "fork":
        binding = SimpleNamespace(skill_id=skill_id, fork_id=uuid4(), version=None)
    else:
        binding = SimpleNamespace(skill_id=skill_id, fork_id=None, version=7)
    spec = SimpleNamespace(
        skill_ids=[skill_id], skill_bindings=[] if binding is None else [binding]
    )
    stored = SimpleNamespace(
        resources={"browser-procedure.json": {"sha256": "hash"}},
        source=SkillVersionSource.BROWSER_TEACHING.value,
        proposal_id=proposal_id,
    )
    proposal = SimpleNamespace(
        organization_id=org_id,
        status=ProposalStatus.APPLIED.value,
        browser_teaching_id=uuid4(),
    )
    procedure = {
        "version": 1,
        "origin": "https://portal.example.test",
        "steps": [],
        "parameters": [],
    }
    db = AsyncMock()
    db.get.side_effect = [run, version, proposal]
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))
    monkeypatch.setattr(service_module.AgentSpec, "model_validate", Mock(return_value=spec))
    get_at_time = AsyncMock(return_value=stored)
    get_by_numbers = AsyncMock(return_value={7: stored})
    monkeypatch.setattr(service_module.skill_version_repo, "get_at_time", get_at_time)
    monkeypatch.setattr(service_module.skill_version_repo, "get_by_numbers", get_by_numbers)
    monkeypatch.setattr(
        service_module.skill_version_repo,
        "get_files",
        AsyncMock(return_value={"hash": json.dumps(procedure)}),
    )
    _enable(monkeypatch)

    result = await BrowserTeachingService.procedure_for_origin(
        org_id, run_id, agent_id, "https://portal.example.test/invoices?token=synthetic"
    )

    assert result == [procedure]
    if binding_kind == "version":
        get_by_numbers.assert_awaited_once()
    else:
        get_at_time.assert_awaited_once()


@pytest.mark.parametrize(
    "stored_state",
    [
        "missing",
        "resource",
        "source",
        "proposal",
        "organization",
        "pending",
        "unlinked",
        "file",
        "json",
        "origin",
    ],
)
async def test_procedure_discovery_skips_unapproved_or_unreadable_resources(
    monkeypatch, stored_state: str
) -> None:
    org_id, run_id, agent_id, version_id, skill_id = (uuid4(), uuid4(), uuid4(), uuid4(), uuid4())
    proposal_id = uuid4()
    run = SimpleNamespace(
        organization_id=org_id,
        agent_id=agent_id,
        agent_version_id=version_id,
        created_at=datetime.now(UTC),
    )
    version = SimpleNamespace(organization_id=org_id, agent_id=agent_id, spec={})
    spec = SimpleNamespace(skill_ids=[skill_id], skill_bindings=[])
    stored = SimpleNamespace(
        resources={"browser-procedure.json": {"sha256": "hash"}},
        source=SkillVersionSource.BROWSER_TEACHING.value,
        proposal_id=proposal_id,
    )
    proposal = SimpleNamespace(
        organization_id=org_id,
        status=ProposalStatus.APPLIED.value,
        browser_teaching_id=uuid4(),
    )
    procedure = {
        "version": 1,
        "origin": "https://portal.example.test",
        "steps": [],
        "parameters": [],
    }
    if stored_state == "missing":
        stored = None
    elif stored_state == "resource":
        stored.resources = {}
    elif stored_state == "source":
        stored.source = "edit"
    elif stored_state == "proposal":
        proposal = None
    elif stored_state == "organization":
        proposal.organization_id = uuid4()
    elif stored_state == "pending":
        proposal.status = ProposalStatus.PENDING.value
    elif stored_state == "unlinked":
        proposal.browser_teaching_id = None
    elif stored_state == "file":
        procedure_body = {}
    elif stored_state == "json":
        procedure_body = {"hash": "{"}
    else:
        procedure["origin"] = "https://other.example.test"
        procedure_body = {"hash": json.dumps(procedure)}
    if stored_state not in {"file", "json", "origin"}:
        procedure_body = {"hash": json.dumps(procedure)}
    db = AsyncMock()
    db.get.side_effect = [run, version, proposal]
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))
    monkeypatch.setattr(service_module.AgentSpec, "model_validate", Mock(return_value=spec))
    monkeypatch.setattr(
        service_module.skill_version_repo, "get_at_time", AsyncMock(return_value=stored)
    )
    monkeypatch.setattr(
        service_module.skill_version_repo,
        "get_files",
        AsyncMock(return_value=procedure_body),
    )
    _enable(monkeypatch)

    result = await BrowserTeachingService.procedure_for_origin(
        org_id, run_id, agent_id, "https://portal.example.test"
    )

    assert result == []


async def test_get_returns_only_the_owned_session(monkeypatch) -> None:
    row = _session(status="review")
    service = BrowserTeachingService(_session_db())
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))

    result = await service.get(_context(row), row.owner_user_id, row.id)

    assert result.id == row.id


async def test_end_lease_pauses_only_the_matching_active_row(monkeypatch) -> None:
    row = _session()
    db = _session_db(row)
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))

    await BrowserTeachingService.end_lease(
        row.organization_id, row.owner_user_id, row.run_id, row.call_id, row.lease_id
    )

    assert row.status == "paused"
    assert row.pause_reason == "lease_ended"
    db.flush.assert_awaited_once()


async def test_end_lease_returns_when_no_matching_capture_is_active(monkeypatch) -> None:
    db = _session_db(None)
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))

    await BrowserTeachingService.end_lease(uuid4(), uuid4(), uuid4(), "call", "lease")

    db.flush.assert_not_awaited()


async def test_end_run_pauses_every_active_capture_and_flushes_once(monkeypatch) -> None:
    rows = [_session(), _session()]
    db = AsyncMock()
    result = Mock()
    result.scalars.return_value.all.return_value = rows
    db.execute.return_value = result
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))

    await BrowserTeachingService.end_run(rows[0].organization_id, rows[0].run_id, rows[0].call_id)

    assert [row.pause_reason for row in rows] == ["browser_finished", "browser_finished"]
    db.flush.assert_awaited_once()


async def test_end_run_does_not_flush_when_no_capture_is_active(monkeypatch) -> None:
    db = AsyncMock()
    result = Mock()
    result.scalars.return_value.all.return_value = []
    db.execute.return_value = result
    monkeypatch.setattr(service_module, "get_db_context", lambda: _yield_db(db))

    await BrowserTeachingService.end_run(uuid4(), uuid4(), "call")

    db.flush.assert_not_awaited()


async def test_sweep_expired_reports_the_repository_count(monkeypatch) -> None:
    db = AsyncMock()
    monkeypatch.setattr(service_module, "get_worker_db_context", lambda: _yield_db(db))
    expire = AsyncMock(return_value=3)
    monkeypatch.setattr(service_module.browser_teaching_repo, "expire_due", expire)

    assert await service_module.sweep_expired() == 3
    expire.assert_awaited_once()
