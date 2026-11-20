"""PostgreSQL queue ordering and complete bounded burst snapshots."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.conversation import Message
from app.db.models.room_auto_routing import RoomAutoRoutingDecision
from app.db.models.room_submission import RoomSubmission
from app.repositories import room_auto_routing as repo
from app.repositories import room_submission as room_repo
from app.services.room_auto_routing import RoomAutoRoutingService
from tests.integration.test_room_submissions import Room, seed

pytestmark = [pytest.mark.anyio, pytest.mark.security]


async def _accepted_post(
    db: AsyncSession,
    room: Room,
    *,
    ordinal: int,
    assigned: bool = False,
) -> tuple[Message, RoomSubmission, datetime]:
    accepted_at = datetime.now(UTC) + timedelta(milliseconds=ordinal * 100)
    message = Message(
        id=uuid4(),
        conversation_id=room.conversation.id,
        client_message_id=f"auto-routing-{uuid4().hex}",
        role="user",
        content=f"Accepted post {ordinal}",
        author_user_id=room.user.id,
        created_at=accepted_at,
    )
    submission = RoomSubmission(
        id=uuid4(),
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        requester_id=room.user.id,
        message_id=message.id,
        parent_submission_id=None,
        dispatch="automatic",
        thread_root_message_id=None,
        client_message_id=message.client_message_id,
        fingerprint=uuid4().hex,
        intent="message",
        agent_id=room.agent.id if assigned else None,
        version_id=room.version.id if assigned else None,
        status="queued" if assigned else "posted",
        created_at=accepted_at,
    )
    db.add_all([message, submission])
    await db.flush()
    return message, submission, accepted_at


def _decision(
    room: Room,
    message: Message,
    submission: RoomSubmission,
    accepted_at: datetime,
    grouped_message_ids: list[UUID],
    model_profile_id: UUID,
) -> RoomAutoRoutingDecision:
    return RoomAutoRoutingDecision(
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        submission_id=submission.id,
        accepted_message_id=message.id,
        requester_id=room.user.id,
        thread_root_message_id=None,
        accepted_at=accepted_at,
        ready_at=accepted_at + timedelta(milliseconds=2500),
        policy_revision=1,
        router_agent_id=room.agent.id,
        router_name="Room router",
        router_version_id=room.version.id,
        model_profile_id=model_profile_id,
        model_profile_fingerprint="a" * 64,
        quiet_window_ms=2500,
        max_responders=1,
        candidate_snapshot=[],
        grouped_message_ids=[str(message_id) for message_id in grouped_message_ids],
    )


async def test_router_waits_for_only_earlier_active_room_submissions(db: AsyncSession) -> None:
    room = await seed(db)
    _, earlier, _ = await _accepted_post(db, room, ordinal=1, assigned=True)
    message, root, accepted_at = await _accepted_post(db, room, ordinal=2)
    decision = _decision(room, message, root, accepted_at, [message.id], uuid4())
    _, later, _ = await _accepted_post(db, room, ordinal=3, assigned=True)
    db.add(decision)
    await db.flush()

    assert await repo.has_earlier_active_submission(db, decision)
    assert await repo.has_pending_or_running_before_submission(db, later)
    assert not await repo.has_pending_or_running_before_submission(db, earlier)

    earlier.status = "completed"
    await db.flush()
    assert not await repo.has_earlier_active_submission(db, decision)

    decision.status = "selected"
    await db.flush()
    assert not await repo.has_pending_or_running_before_submission(db, later)


async def test_child_responder_keeps_its_accepted_group_position(db: AsyncSession) -> None:
    """A later human request cannot pass a sibling inserted after its own ordinal."""
    room = await seed(db)
    first_message, first_root, _ = await _accepted_post(db, room, ordinal=1, assigned=True)
    first_root.status = "completed"
    _, later_root, _ = await _accepted_post(db, room, ordinal=2, assigned=True)
    later_root.status = "completed"
    routed_message, routed_root, accepted_at = await _accepted_post(db, room, ordinal=3)
    decision = _decision(
        room, routed_message, routed_root, accepted_at, [routed_message.id], uuid4()
    )
    decision.ready_at = datetime.now(UTC) - timedelta(seconds=1)
    child = RoomSubmission(
        id=uuid4(),
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        requester_id=room.user.id,
        message_id=first_message.id,
        parent_submission_id=first_root.id,
        dispatch="automatic",
        thread_root_message_id=None,
        client_message_id=first_root.client_message_id,
        fingerprint=first_root.fingerprint,
        intent="message",
        agent_id=uuid4(),
        version_id=uuid4(),
        status="queued",
    )
    db.add_all([decision, child])
    await db.flush()

    assert child.ordinal > later_root.ordinal > first_root.ordinal
    assert await repo.has_earlier_active_submission(db, decision)

    later_root.status = "queued"
    await db.flush()
    head = await room_repo.head(db, room.conversation.id)
    ready = await room_repo.ready(db)
    due_routing = await repo.ready_decisions(
        db,
        organization_id=room.ctx.organization_id,
        now=datetime.now(UTC),
    )

    assert head is not None and head.id == child.id
    assert [item.id for item in ready] == [child.id]
    assert decision.id not in {item.id for item in due_routing}


@pytest.mark.parametrize(
    ("status", "error_code"),
    [("failed", "authority_changed"), ("unknown", "router_outcome_unknown")],
)
async def test_error_finalizer_reloads_claim_after_real_session_rollback(
    db: AsyncSession,
    status: Literal["failed", "unknown"],
    error_code: str,
) -> None:
    """An ORM instance expired by rollback cannot strand a claimed decision."""
    room = await seed(db)
    message, submission, accepted_at = await _accepted_post(db, room, ordinal=1)
    decision = _decision(room, message, submission, accepted_at, [message.id], uuid4())
    decision.ready_at = datetime.now(UTC) - timedelta(seconds=1)
    db.add(decision)
    await db.flush()
    claim_token = uuid4()
    claimed = await repo.claim_ready_decision(
        db,
        decision.id,
        organization_id=room.ctx.organization_id,
        now=datetime.now(UTC),
        claim_token=claim_token,
    )
    assert claimed is not None
    decision_id = claimed.id
    organization_id = claimed.organization_id
    claim_generation = claimed.claim_generation
    await db.commit()

    await RoomAutoRoutingService(db)._finish_after_error(
        decision_id=decision_id,
        organization_id=organization_id,
        claim_generation=claim_generation,
        claim_token=claim_token,
        status=status,
        error_code=error_code,
        router_run_id=None,
    )

    settled = await repo.get_decision(db, decision_id, organization_id=organization_id)
    assert settled is not None
    assert settled.status == status
    assert settled.error_code == error_code
    assert settled.decision_output is None
    assert (
        await db.scalar(
            select(RoomAutoRoutingDecision.decision_output.is_(None)).where(
                RoomAutoRoutingDecision.id == decision_id
            )
        )
        is True
    )


async def test_stale_claim_recovery_is_scoped_terminal_and_idempotent(db: AsyncSession) -> None:
    room = await seed(db)
    other_room = await seed(db)
    now = datetime.now(UTC)
    decisions = []
    for ordinal, (owner, status, age) in enumerate(
        [
            (room, "running", 120),
            (room, "running", 10),
            (room, "selected", 120),
            (other_room, "running", 120),
        ],
        start=1,
    ):
        message, submission, accepted_at = await _accepted_post(db, owner, ordinal=ordinal)
        decision = _decision(owner, message, submission, accepted_at, [message.id], uuid4())
        decision.status = status
        decision.claim_token = uuid4()
        decision.claim_generation = 1
        decision.claimed_at = now - timedelta(seconds=age)
        db.add(decision)
        decisions.append(decision)
    await db.flush()

    count = await repo.mark_stale_running_unknown(
        db,
        organization_id=room.ctx.organization_id,
        before=now - timedelta(seconds=90),
        now=now,
    )
    for decision in decisions:
        await db.refresh(decision)

    stale, fresh, completed, foreign = decisions
    assert count == 1
    assert stale.status == "unknown"
    assert stale.error_code == "worker_lost_after_claim"
    assert stale.finished_at == now
    assert (fresh.status, completed.status, foreign.status) == ("running", "selected", "running")
    assert (
        await repo.mark_stale_running_unknown(
            db,
            organization_id=room.ctx.organization_id,
            before=now - timedelta(seconds=90),
            now=now,
        )
        == 0
    )


async def test_three_accepted_posts_keep_one_complete_burst_snapshot(db: AsyncSession) -> None:
    room = await seed(db)
    profile_id = uuid4()
    decisions = []
    messages = []
    for ordinal in range(1, 4):
        message, submission, accepted_at = await _accepted_post(db, room, ordinal=ordinal)
        decision = _decision(room, message, submission, accepted_at, [message.id], profile_id)
        db.add(decision)
        await db.flush()

        earlier = await repo.pending_same_author_thread(db, latest=decision)
        grouped_ids = [
            UUID(message_id) for prior in earlier for message_id in prior.grouped_message_ids
        ]
        grouped_ids.append(message.id)
        for prior in earlier:
            assert await repo.continue_pending_decision(
                db,
                prior.id,
                organization_id=decision.organization_id,
                conversation_id=decision.conversation_id,
                continued_into_id=decision.id,
                finished_at=accepted_at,
            )
            prior.status = "continued"
            prior.continued_into_id = decision.id
        assert await repo.update_pending_group(
            db,
            decision.id,
            organization_id=decision.organization_id,
            conversation_id=decision.conversation_id,
            grouped_message_ids=grouped_ids,
            ready_at=decision.ready_at,
        )
        decision.grouped_message_ids = [str(value) for value in grouped_ids]
        messages.append(message)
        decisions.append(decision)

    first, second, latest = decisions
    assert first.status == second.status == "continued"
    assert first.continued_into_id == second.id
    assert second.continued_into_id == latest.id
    assert latest.status == "pending"
    assert latest.grouped_message_ids == [str(message.id) for message in messages]


async def test_nine_posts_leave_the_full_eight_post_group_pending(db: AsyncSession) -> None:
    room = await seed(db)
    profile_id = uuid4()
    decisions = []
    messages = []
    for ordinal in range(1, 10):
        message, submission, accepted_at = await _accepted_post(db, room, ordinal=ordinal)
        decision = _decision(room, message, submission, accepted_at, [message.id], profile_id)
        db.add(decision)
        await db.flush()

        earlier = await repo.pending_same_author_thread(db, latest=decision)
        grouped_ids = [
            UUID(message_id) for prior in earlier for message_id in prior.grouped_message_ids
        ]
        grouped_ids.append(message.id)
        for prior in earlier:
            assert await repo.continue_pending_decision(
                db,
                prior.id,
                organization_id=decision.organization_id,
                conversation_id=decision.conversation_id,
                continued_into_id=decision.id,
                finished_at=accepted_at,
            )
            prior.status = "continued"
            prior.continued_into_id = decision.id
        assert await repo.update_pending_group(
            db,
            decision.id,
            organization_id=decision.organization_id,
            conversation_id=decision.conversation_id,
            grouped_message_ids=grouped_ids,
            ready_at=decision.ready_at,
        )
        decision.grouped_message_ids = [str(value) for value in grouped_ids]
        decisions.append(decision)
        messages.append(message)

    eight_post_decision = decisions[7]
    ninth_decision = decisions[8]
    assert eight_post_decision.status == "pending"
    assert eight_post_decision.grouped_message_ids == [str(message.id) for message in messages[:8]]
    assert ninth_decision.status == "pending"
    assert ninth_decision.grouped_message_ids == [str(messages[8].id)]
