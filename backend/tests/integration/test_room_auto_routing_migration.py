"""PostgreSQL enforces the automatic-routing receipt's one-shot claim boundary."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.conversation import Message
from app.db.models.room_auto_routing import RoomAutoRoutingDecision, RoomAutoRoutingPolicy
from app.db.models.room_submission import RoomSubmission
from tests.integration.test_room_submissions import Room, seed

pytestmark = [pytest.mark.anyio, pytest.mark.security]


async def test_database_rejects_enabled_policy_without_pinned_router_or_model(
    db: AsyncSession,
) -> None:
    room = await seed(db)
    policy = RoomAutoRoutingPolicy(
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        enabled=True,
        revision=1,
        quiet_window_ms=2500,
        max_responders=1,
    )
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(policy)
            await db.flush()


async def test_database_rejects_running_decision_without_claim(db: AsyncSession) -> None:
    room: Room = await seed(db)
    message = Message(
        id=uuid4(),
        conversation_id=room.conversation.id,
        role="user",
        content="Route this request",
    )
    db.add(message)
    await db.flush()
    submission = RoomSubmission(
        id=uuid4(),
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        requester_id=room.user.id,
        message_id=message.id,
        dispatch="automatic",
        thread_root_message_id=None,
        client_message_id=f"auto-{uuid4()}",
        fingerprint=uuid4().hex,
        intent="message",
        status="posted",
    )
    db.add(submission)
    await db.flush()
    decision = RoomAutoRoutingDecision(
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        submission_id=submission.id,
        accepted_message_id=message.id,
        requester_id=room.user.id,
        accepted_at=datetime.now(UTC),
        ready_at=datetime.now(UTC),
        policy_revision=1,
        router_agent_id=room.agent.id,
        router_name="Room router",
        router_version_id=room.version.id,
        model_profile_id=uuid4(),
        model_profile_fingerprint="0" * 64,
        quiet_window_ms=2500,
        max_responders=1,
        candidate_snapshot=[],
        grouped_message_ids=[str(message.id)],
        status="running",
        claim_token=None,
        claim_generation=0,
    )
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(decision)
            await db.flush()
