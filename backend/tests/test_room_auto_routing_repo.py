"""Repository-level idempotency and contiguous burst grouping contracts."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models.credential import ModelProfile
from app.db.models.room_auto_routing import RoomAutoRoutingDecision
from app.db.models.room_submission import RoomSubmission
from app.repositories import room_auto_routing as repo
from app.schemas.room_auto_routing import RoomAutoRoutingDecisionCreate

pytestmark = pytest.mark.anyio


@pytest.mark.security
def test_model_profile_fingerprint_tracks_nonsecret_configuration():
    profile = ModelProfile(
        id=uuid4(),
        organization_id=uuid4(),
        label="Router model",
        provider="openai",
        model="gpt-4.1-mini",
        secret_id=uuid4(),
        personal_owner_user_id=None,
        runs_without_key=False,
        base_url=None,
        api="responses",
        region=None,
        context_length=128000,
        params={"temperature": 0.2},
        fallback_profile_ids=[],
    )
    original = repo.model_profile_fingerprint(profile)

    assert repo.model_profile_fingerprint(profile) == original
    profile.params = {"temperature": 0.3}
    assert repo.model_profile_fingerprint(profile) != original


async def test_create_decision_returns_existing_frozen_snapshot(monkeypatch):
    existing = RoomAutoRoutingDecision(id=uuid4(), status="pending")
    find = AsyncMock(return_value=existing)
    monkeypatch.setattr(repo, "decision_for_submission", find)
    db = MagicMock()
    db.flush = AsyncMock()
    accepted_message_id = uuid4()
    data = RoomAutoRoutingDecisionCreate(
        organization_id=uuid4(),
        conversation_id=uuid4(),
        submission_id=uuid4(),
        accepted_message_id=accepted_message_id,
        requester_id=uuid4(),
        accepted_at=datetime.now(UTC),
        ready_at=datetime.now(UTC) + timedelta(seconds=2),
        policy_revision=1,
        router_agent_id=uuid4(),
        router_name="Room router",
        router_version_id=uuid4(),
        model_profile_id=uuid4(),
        model_profile_fingerprint="0" * 64,
        quiet_window_ms=2500,
        max_responders=1,
        candidate_snapshot=[],
        grouped_message_ids=[accepted_message_id],
    )

    row, created = await repo.create_decision(db, data)

    assert row is existing and not created
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


async def test_pending_group_stops_at_the_first_explicit_post():
    organization_id, conversation_id, requester_id = uuid4(), uuid4(), uuid4()
    thread_id = uuid4()
    router_id, version_id, profile_id = uuid4(), uuid4(), uuid4()
    accepted_at = datetime.now(UTC)
    latest_submission = RoomSubmission(
        id=uuid4(),
        organization_id=organization_id,
        conversation_id=conversation_id,
        requester_id=requester_id,
        message_id=uuid4(),
        parent_submission_id=None,
        dispatch="automatic",
        intent="message",
        thread_root_message_id=thread_id,
        ordinal=10,
        created_at=accepted_at,
    )
    latest = RoomAutoRoutingDecision(
        id=uuid4(),
        organization_id=organization_id,
        conversation_id=conversation_id,
        submission_id=latest_submission.id,
        requester_id=requester_id,
        thread_root_message_id=thread_id,
        accepted_at=accepted_at,
        quiet_window_ms=2500,
        policy_revision=4,
        router_agent_id=router_id,
        router_version_id=version_id,
        model_profile_id=profile_id,
        model_profile_fingerprint="a" * 64,
        candidate_snapshot=[{"agent_id": str(uuid4())}],
        grouped_message_ids=[str(latest_submission.message_id)],
    )
    first = RoomSubmission(
        id=uuid4(),
        organization_id=organization_id,
        conversation_id=conversation_id,
        requester_id=requester_id,
        message_id=uuid4(),
        parent_submission_id=None,
        dispatch="automatic",
        intent="message",
        thread_root_message_id=thread_id,
        ordinal=9,
        created_at=accepted_at - timedelta(milliseconds=300),
    )
    first_decision = RoomAutoRoutingDecision(
        id=uuid4(),
        organization_id=organization_id,
        conversation_id=conversation_id,
        submission_id=first.id,
        requester_id=requester_id,
        thread_root_message_id=thread_id,
        accepted_at=first.created_at,
        quiet_window_ms=2500,
        policy_revision=4,
        router_agent_id=router_id,
        router_version_id=version_id,
        model_profile_id=profile_id,
        model_profile_fingerprint="a" * 64,
        candidate_snapshot=latest.candidate_snapshot,
        grouped_message_ids=[str(uuid4())],
        status="pending",
    )
    barrier = RoomSubmission(
        id=uuid4(),
        organization_id=organization_id,
        conversation_id=conversation_id,
        requester_id=requester_id,
        message_id=uuid4(),
        parent_submission_id=None,
        dispatch="explicit",
        intent="message",
        thread_root_message_id=thread_id,
        ordinal=8,
        created_at=accepted_at - timedelta(milliseconds=500),
    )
    barrier_decision = RoomAutoRoutingDecision(
        id=uuid4(), status="pending", candidate_snapshot=latest.candidate_snapshot
    )
    older = RoomSubmission(
        id=uuid4(),
        organization_id=organization_id,
        conversation_id=conversation_id,
        requester_id=requester_id,
        message_id=uuid4(),
        parent_submission_id=None,
        dispatch="automatic",
        intent="message",
        thread_root_message_id=thread_id,
        ordinal=7,
        created_at=accepted_at - timedelta(milliseconds=800),
    )
    older_decision = RoomAutoRoutingDecision(
        id=uuid4(), status="pending", candidate_snapshot=latest.candidate_snapshot
    )
    db = MagicMock()
    db.scalar = AsyncMock(return_value=latest_submission)
    result = MagicMock()
    result.all.return_value = [
        (first, first_decision),
        (barrier, barrier_decision),
        (older, older_decision),
    ]
    db.execute = AsyncMock(return_value=result)

    decisions = await repo.pending_same_author_thread(db, latest=latest)

    assert decisions == [first_decision]


async def test_room_activity_guard_answers_from_pending_or_running_status(monkeypatch):
    db = MagicMock()
    db.scalar = AsyncMock(return_value=1)

    active = await repo.room_has_pending_or_running(
        db, organization_id=uuid4(), conversation_id=uuid4()
    )

    assert active
    db.scalar.assert_awaited_once()


@pytest.mark.security
async def test_router_queue_helpers_return_their_tenant_scoped_existence_results():
    organization_id, conversation_id = uuid4(), uuid4()
    decision = RoomAutoRoutingDecision(
        id=uuid4(),
        organization_id=organization_id,
        conversation_id=conversation_id,
        submission_id=uuid4(),
    )
    submission = RoomSubmission(
        id=uuid4(),
        organization_id=organization_id,
        conversation_id=conversation_id,
        ordinal=2,
    )
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[True, False])

    assert await repo.has_earlier_active_submission(db, decision)
    assert not await repo.has_pending_or_running_before_submission(db, submission)
    assert db.scalar.await_count == 2
