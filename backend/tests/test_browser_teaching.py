"""Synthetic checks for the managed-browser teaching privacy boundary."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.agents.browser_events import BrowserEvent
from app.core.exceptions import BadRequestError, ConcurrentChangeError, NotFoundError
from app.core.permissions import AuthContext
from app.db.models.browser_teaching import BrowserTeachingSession
from app.db.models.skill_proposal import SkillProposal
from app.repositories import browser_teaching as browser_teaching_repo
from app.schemas.browser_teaching import (
    BrowserTeachingParameter,
    BrowserTeachingReview,
    BrowserTeachingStepReview,
)
from app.services import browser_teaching as service_module
from app.services.browser_teaching import (
    BYTE_LIMIT,
    EVENT_LIMIT,
    BrowserTeachingService,
    _append_event,
    _procedure_matches_origin,
    _procedure_text,
    _public_step,
    safe_origin,
)


def _session(*, events: list[dict[str, object]] | None = None) -> BrowserTeachingSession:
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
        status="recording",
        revision=1,
        events=events or [],
        parameters=[],
        expires_at=now + timedelta(hours=1),
        capture_until=now + timedelta(minutes=5),
    )


def test_origin_discards_url_tokens_and_path() -> None:
    origin = safe_origin(
        "https://portal.example.test/reset/secret-path?access_token=synthetic#one-time-code"
    )

    assert origin == "https://portal.example.test"
    assert "synthetic" not in origin
    assert "one-time-code" not in origin


def test_origin_keeps_only_supported_authority_parts() -> None:
    assert safe_origin("https://[2001:db8::1]:8443/private") == "https://[2001:db8::1]:8443"


def test_origin_refuses_invalid_port_and_host_without_echoing_them() -> None:
    with pytest.raises(BadRequestError):
        safe_origin("https://portal.example.test:invalid")

    invalid_host = chr(0xD800)
    with pytest.raises(BadRequestError):
        safe_origin(f"https://{invalid_host}.example.test")


@pytest.mark.parametrize(
    "url", ["http://portal.example.test", "https://person:password@example.test"]
)
def test_unsupported_origin_is_refused_without_echoing_it(url: str) -> None:
    with pytest.raises(BadRequestError) as error:
        safe_origin(url)

    assert "password" not in str(error.value)
    assert "portal.example.test" not in str(error.value)


def test_stored_step_uses_only_role_and_structural_fingerprint() -> None:
    secret = "synthetic-password-DO-NOT-STORE"
    step = _public_step(
        uuid4(),
        0,
        "input_parameter",
        f"https://portal.example.test/?token={secret}",
        {
            "role": "textbox",
            "fingerprint": "a" * 64,
            "label": secret,
            "value": secret,
            "dom_text": secret,
        },
    )

    serialized = str(step)
    assert step == {
        "event_id": step["event_id"],
        "sequence": 0,
        "action": "input_parameter",
        "origin": "https://portal.example.test",
        "target_role": "textbox",
        "target_fingerprint": "a" * 64,
        "parameter": "input_1",
    }
    assert secret not in serialized
    assert "dom_text" not in serialized
    assert "value" not in serialized


def test_stored_step_discards_unrecognized_target_details() -> None:
    step = _public_step(
        uuid4(),
        0,
        "click",
        "https://portal.example.test",
        {"role": "application-secret", "fingerprint": "not-a-hash"},
    )

    assert "target_role" not in step
    assert "target_fingerprint" not in step


def test_replayed_command_id_does_not_duplicate_a_step() -> None:
    row = _session()
    event_id = uuid4()
    event = {
        "event_id": str(event_id),
        "sequence": 0,
        "action": "click",
        "origin": row.origin,
    }
    assert _append_event(
        row,
        event_id=event_id,
        action="click",
        url=row.origin,
        target=None,
        now=datetime.now(UTC),
        lease_owner=str(row.owner_user_id),
        lease_id=row.lease_id,
    )
    revision = row.revision
    assert not _append_event(
        row,
        event_id=event_id,
        action="click",
        url=row.origin,
        target=None,
        now=datetime.now(UTC),
        lease_owner=str(row.owner_user_id),
        lease_id=row.lease_id,
    )
    assert row.revision == revision
    assert len(row.events) == 1
    assert row.events[0]["event_id"] == event["event_id"]


def test_other_user_or_tab_pauses_without_recording() -> None:
    row = _session()

    changed = _append_event(
        row,
        event_id=uuid4(),
        action="click",
        url=row.origin,
        target=None,
        now=datetime.now(UTC),
        lease_owner=str(uuid4()),
        lease_id="another-tab",
    )

    assert changed
    assert row.events == []
    assert row.status == "paused"
    assert row.pause_reason == "lease_lost"


def test_capture_pauses_before_storing_steps_from_another_origin() -> None:
    row = _session()

    changed = _append_event(
        row,
        event_id=uuid4(),
        action="click",
        url="https://other.example.test/private?token=synthetic",
        target={"role": "button", "fingerprint": "a" * 64},
        now=datetime.now(UTC),
        lease_owner=str(row.owner_user_id),
        lease_id=row.lease_id,
    )

    assert changed
    assert row.events == []
    assert row.status == "paused"
    assert row.pause_reason == "origin_changed"


def test_event_and_byte_limits_pause_instead_of_truncating() -> None:
    row = _session(events=[{"event_id": str(uuid4())} for _ in range(EVENT_LIMIT)])
    _append_event(
        row,
        event_id=uuid4(),
        action="click",
        url=row.origin,
        target=None,
        now=datetime.now(UTC),
        lease_owner=str(row.owner_user_id),
        lease_id=row.lease_id,
    )
    assert row.status == "paused"
    assert row.pause_reason == "event_limit"
    assert len(row.events) == EVENT_LIMIT

    oversized = _session(events=[{"event_id": str(uuid4()), "origin": "x" * BYTE_LIMIT}])
    _append_event(
        oversized,
        event_id=uuid4(),
        action="click",
        url=oversized.origin,
        target=None,
        now=datetime.now(UTC),
        lease_owner=str(oversized.owner_user_id),
        lease_id=oversized.lease_id,
    )
    assert oversized.status == "paused"
    assert oversized.pause_reason == "size_limit"
    assert len(oversized.events) == 1


def test_expired_capture_rejects_input_and_removes_no_prior_step() -> None:
    row = _session(events=[{"event_id": str(uuid4())}])
    row.capture_until = datetime.now(UTC) - timedelta(seconds=1)

    changed = _append_event(
        row,
        event_id=uuid4(),
        action="click",
        url=row.origin,
        target=None,
        now=datetime.now(UTC),
        lease_owner=str(row.owner_user_id),
        lease_id=row.lease_id,
    )

    assert changed
    assert row.status == "paused"
    assert row.pause_reason == "duration_limit"
    assert len(row.events) == 1


@pytest.mark.parametrize("refusal", ["unsupported", "paused", "expired"])
def test_capture_ignores_unsupported_or_closed_session_input(refusal: str) -> None:
    row = _session()
    action = "click"
    if refusal == "unsupported":
        action = "submit"
    elif refusal == "paused":
        row.status = "paused"
    else:
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    changed = _append_event(
        row,
        event_id=uuid4(),
        action=action,
        url=row.origin,
        target=None,
        now=datetime.now(UTC),
        lease_owner=str(row.owner_user_id),
        lease_id=row.lease_id,
    )

    assert not changed
    assert row.events == []


def test_capture_pauses_when_the_event_url_cannot_be_sanitized() -> None:
    row = _session()

    changed = _append_event(
        row,
        event_id=uuid4(),
        action="click",
        url="https://portal.example.test:invalid",
        target=None,
        now=datetime.now(UTC),
        lease_owner=str(row.owner_user_id),
        lease_id=row.lease_id,
    )

    assert changed
    assert row.status == "paused"
    assert row.pause_reason == "origin_changed"
    assert row.events == []


@pytest.mark.security
def test_procedure_is_bounded_and_refuses_secret_parameter_labels() -> None:
    events = [
        {
            "sequence": 0,
            "action": "input_parameter",
            "target_role": "textbox",
            "parameter": "invoice_number",
            "reviewed_label": "Invoice number",
        },
        {
            "sequence": 1,
            "action": "click",
            "target_role": "button",
            "reviewed_label": "Open invoices",
        },
    ]
    parameters = [{"name": "invoice_number", "label": "Invoice number"}]
    body = _procedure_text("https://portal.example.test", events, parameters)

    assert "https://portal.example.test" in body
    assert "Read the live page" in body
    assert "invoice_number" in body
    assert "Open invoices" in body
    assert "password" not in body.lower()
    with pytest.raises(BadRequestError):
        _procedure_text(
            "https://portal.example.test", events, [{"name": "secret", "label": "Password"}]
        )


@pytest.mark.parametrize(
    "event",
    [
        {"sequence": 0, "action": "click", "reviewed_label": ""},
        {"sequence": 0, "action": "input_parameter", "reviewed_label": "Invoice"},
    ],
)
def test_procedure_refuses_unreviewed_clicks_and_inputs(event: dict[str, object]) -> None:
    with pytest.raises(BadRequestError):
        _procedure_text("https://portal.example.test", [event], [])


def test_procedure_keeps_other_reviewed_actions_as_plain_steps() -> None:
    body = _procedure_text(
        "https://portal.example.test", [{"sequence": 0, "action": "checkpoint"}], []
    )

    assert "1. checkpoint." in body


def test_procedure_lookup_requires_exact_origin_and_a_typed_resource() -> None:
    procedure = {
        "version": 1,
        "origin": "https://portal.example.test",
        "steps": [{"sequence": 0, "action": "click"}],
        "parameters": [],
    }

    assert _procedure_matches_origin(procedure, "https://portal.example.test")
    assert not _procedure_matches_origin(procedure, "https://sub.portal.example.test")
    assert not _procedure_matches_origin({**procedure, "version": 2}, "https://portal.example.test")
    assert not _procedure_matches_origin(
        {**procedure, "origin": "https://other.example.test"}, "https://portal.example.test"
    )


def test_browser_teaching_proposal_provenance_is_unique() -> None:
    index = next(
        index
        for index in SkillProposal.__table__.indexes
        if index.name == "ix_skill_proposals_browser_teaching"
    )
    assert index.unique


def test_browser_control_lease_is_preserved_on_browser_events() -> None:
    lease = "synthetic-control-lease"
    event = BrowserEvent(
        kind="browser_held",
        call_id="synthetic-call",
        step=0,
        by_user_id="synthetic-owner",
        control_id=lease,
    )

    assert event.control_id == lease


@pytest.mark.anyio
async def test_recording_for_browser_returns_the_locked_active_sessions() -> None:
    sessions = [_session(), _session()]
    result = Mock()
    result.scalars.return_value.all.return_value = sessions
    db = AsyncMock()
    db.execute.return_value = result

    active = await browser_teaching_repo.recording_for_browser(
        db,
        organization_id=sessions[0].organization_id,
        run_id=sessions[0].run_id,
        call_id=sessions[0].call_id,
    )

    assert active == sessions


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (SimpleNamespace(rowcount=3), 3),
        (SimpleNamespace(rowcount=0), 0),
        (SimpleNamespace(), 0),
    ],
)
@pytest.mark.anyio
async def test_expire_due_returns_affected_rows_or_zero(result, expected: int) -> None:
    db = AsyncMock()
    db.execute.return_value = result

    count = await browser_teaching_repo.expire_due(db, now=datetime.now(UTC))

    assert count == expected


@pytest.mark.anyio
async def test_review_serializes_parameter_ids_and_checks_the_revision(monkeypatch) -> None:
    event_id = uuid4()
    click_id = uuid4()
    row = _session(
        events=[
            {
                "event_id": str(event_id),
                "sequence": 0,
                "action": "input_parameter",
                "origin": "https://portal.example.test",
                "parameter": "input_1",
            },
            {
                "event_id": str(click_id),
                "sequence": 1,
                "action": "click",
                "origin": "https://portal.example.test",
            },
            {
                "event_id": str(uuid4()),
                "sequence": 2,
                "action": "scroll",
                "origin": "https://portal.example.test",
            },
        ]
    )
    row.status = "review"
    db = AsyncMock()
    service = BrowserTeachingService(db)
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    ctx = AuthContext(user_id=row.owner_user_id, organization_id=row.organization_id, role="owner")
    review = BrowserTeachingReview(
        revision=row.revision,
        steps=[BrowserTeachingStepReview(event_id=click_id, reviewed_label="Open invoices")],
        parameters=[
            BrowserTeachingParameter(
                event_id=event_id, name="invoice_number", label="Invoice number"
            )
        ],
    )

    result = await service.review(ctx, row.owner_user_id, row.id, review)

    assert row.events[0]["parameter"] == "invoice_number"
    assert row.parameters == [
        {"event_id": str(event_id), "name": "invoice_number", "label": "Invoice number"}
    ]
    assert row.events[1]["reviewed_label"] == "Open invoices"
    assert row.events[2]["action"] == "scroll"
    assert result.parameters[0].event_id == event_id
    assert row.revision == 2


@pytest.mark.anyio
async def test_review_refuses_a_stale_revision(monkeypatch) -> None:
    row = _session()
    row.status = "review"
    row.revision = 3
    db = AsyncMock()
    service = BrowserTeachingService(db)
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=row))
    ctx = AuthContext(user_id=row.owner_user_id, organization_id=row.organization_id, role="owner")

    with pytest.raises(ConcurrentChangeError, match="changed"):
        await service.review(
            ctx,
            row.owner_user_id,
            row.id,
            BrowserTeachingReview(revision=2, steps=[], parameters=[]),
        )

    assert row.revision == 3


@pytest.mark.anyio
async def test_owner_api_keys_cannot_read_unfinished_teaching(monkeypatch) -> None:
    row = _session()
    db = AsyncMock()
    db.execute.return_value = Mock(scalar_one_or_none=lambda: row)
    service = BrowserTeachingService(db)
    ctx = AuthContext(
        user_id=row.owner_user_id,
        organization_id=row.organization_id,
        role="owner",
        api_key_id=uuid4(),
    )

    with pytest.raises(NotFoundError) as error:
        await service._owned(ctx, row.owner_user_id, row.id)

    assert "not found" in str(error.value).lower()


@pytest.mark.anyio
async def test_missing_teaching_session_is_not_found_even_when_locked() -> None:
    db = AsyncMock()
    db.execute.return_value = Mock(scalar_one_or_none=lambda: None)
    service = BrowserTeachingService(db)
    organization_id, user_id, teaching_id = uuid4(), uuid4(), uuid4()
    context = AuthContext(user_id=user_id, organization_id=organization_id, role="owner")

    with pytest.raises(NotFoundError, match="not found"):
        await service._owned(context, user_id, teaching_id, lock=True)


@pytest.mark.anyio
async def test_owned_pauses_a_recording_after_its_capture_deadline(monkeypatch) -> None:
    row = _session()
    row.capture_until = datetime.now(UTC) - timedelta(seconds=1)
    db = AsyncMock()
    db.execute.return_value = Mock(scalar_one_or_none=lambda: row)
    service = BrowserTeachingService(db)
    context = AuthContext(
        user_id=row.owner_user_id, organization_id=row.organization_id, role="owner"
    )
    monkeypatch.setattr("app.services.browser_teaching.settings.BROWSER_TEACHING_ENABLED", True)

    await service._owned(context, row.owner_user_id, row.id, lock=True)

    assert row.status == "paused"
    assert row.pause_reason == "duration_limit"
    assert row.revision == 2
    db.flush.assert_awaited_once()


@pytest.mark.parametrize("lease_change", ["owner", "socket"])
@pytest.mark.anyio
async def test_owned_pauses_a_recording_when_its_lease_changes(
    monkeypatch, lease_change: str
) -> None:
    row = _session()
    db = AsyncMock()
    db.execute.return_value = Mock(scalar_one_or_none=lambda: row)
    service = BrowserTeachingService(db)
    context = AuthContext(
        user_id=row.owner_user_id, organization_id=row.organization_id, role="owner"
    )
    owner = "another-owner" if lease_change == "owner" else str(row.owner_user_id)
    lease = "another-socket" if lease_change == "socket" else row.lease_id
    monkeypatch.setattr("app.services.browser_teaching.settings.BROWSER_TEACHING_ENABLED", True)
    monkeypatch.setattr(service_module.live_browse, "lease_owner", AsyncMock(return_value=owner))
    monkeypatch.setattr(service_module.live_browse, "lease_id", AsyncMock(return_value=lease))

    await service._owned(context, row.owner_user_id, row.id)

    assert row.status == "paused"
    assert row.pause_reason == "lease_ended"
    db.flush.assert_awaited_once()


@pytest.mark.anyio
async def test_owned_erases_an_expired_review_payload(monkeypatch) -> None:
    row = _session(events=[{"event_id": str(uuid4())}])
    row.status = "review"
    row.parameters = [{"name": "invoice"}]
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db = AsyncMock()
    db.execute.return_value = Mock(scalar_one_or_none=lambda: row)
    service = BrowserTeachingService(db)
    context = AuthContext(
        user_id=row.owner_user_id, organization_id=row.organization_id, role="owner"
    )
    monkeypatch.setattr("app.services.browser_teaching.settings.BROWSER_TEACHING_ENABLED", True)

    await service._owned(context, row.owner_user_id, row.id)

    assert row.status == "expired"
    assert row.pause_reason == "expired"
    assert row.events == []
    assert row.parameters == []
    assert row.revision == 2
    db.flush.assert_awaited_once()


@pytest.mark.anyio
async def test_disabling_teaching_pauses_an_active_session(monkeypatch) -> None:
    row = _session()
    db = AsyncMock()
    db.execute.return_value = Mock(scalar_one_or_none=lambda: row)
    service = BrowserTeachingService(db)
    ctx = AuthContext(user_id=row.owner_user_id, organization_id=row.organization_id, role="owner")
    monkeypatch.setattr("app.services.browser_teaching.settings.BROWSER_TEACHING_ENABLED", False)

    await service._owned(ctx, row.owner_user_id, row.id)

    assert row.status == "paused"
    assert row.pause_reason == "feature_disabled"
    assert row.revision == 2
