"""Room-safe automatic-routing receipts never widen message or identity access."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.permissions import AuthContext
from app.db.models.conversation import Conversation, Message
from app.db.models.organization import OrganizationMember
from app.db.models.room_auto_routing import RoomAutoRoutingDecision
from app.db.models.room_participant import RoomParticipant
from app.db.models.room_submission import RoomSubmission
from app.schemas.room_thread import RoomThreadRangeCreate
from app.services.room_auto_routing import RoomAutoRoutingService
from app.services.room_thread import RoomThreadService
from tests.integration.test_room_submissions import Room, seed

pytestmark = [pytest.mark.anyio, pytest.mark.security]


async def _message(
    db: AsyncSession,
    room: Room,
    content: str,
    *,
    thread_root_message_id: UUID | None = None,
    role: str = "user",
) -> Message:
    message = Message(
        id=uuid4(),
        conversation_id=room.conversation.id,
        role=role,
        content=content,
        author_user_id=room.user.id if role == "user" else None,
        thread_root_message_id=thread_root_message_id,
        created_at=datetime.now(UTC),
    )
    db.add(message)
    await db.flush()
    return message


async def _submission(
    db: AsyncSession,
    room: Room,
    message: Message,
    *,
    thread_root_message_id: UUID | None = None,
) -> RoomSubmission:
    submission = RoomSubmission(
        id=uuid4(),
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        requester_id=room.user.id,
        message_id=message.id,
        dispatch="automatic",
        thread_root_message_id=thread_root_message_id,
        client_message_id=message.client_message_id or uuid4().hex,
        fingerprint=uuid4().hex,
        intent="message",
        status="posted",
    )
    db.add(submission)
    await db.flush()
    return submission


async def _decision(
    db: AsyncSession,
    room: Room,
    message: Message,
    submission: RoomSubmission,
    *,
    grouped_message_ids: list[UUID] | None = None,
    thread_root_message_id: UUID | None = None,
    status: str = "pending",
    decision_output: dict[str, object] | None = None,
    candidate_snapshot: list[dict[str, object]] | None = None,
    max_responders: int = 2,
    continued_into_id: UUID | None = None,
) -> RoomAutoRoutingDecision:
    now = datetime.now(UTC)
    decision = RoomAutoRoutingDecision(
        id=uuid4(),
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        submission_id=submission.id,
        accepted_message_id=message.id,
        requester_id=room.user.id,
        thread_root_message_id=thread_root_message_id,
        accepted_at=now,
        ready_at=now,
        policy_revision=1,
        router_agent_id=room.agent.id,
        router_name="Triage Router",
        router_version_id=room.version.id,
        model_profile_id=uuid4(),
        model_profile_fingerprint="f" * 64,
        quiet_window_ms=2500,
        max_responders=max_responders,
        candidate_snapshot=candidate_snapshot or [],
        grouped_message_ids=[str(message_id) for message_id in grouped_message_ids or [message.id]],
        status=status,
        decision_output=decision_output,
        continued_into_id=continued_into_id,
        finished_at=(
            now if status in {"selected", "abstained", "failed", "unknown", "continued"} else None
        ),
    )
    db.add(decision)
    await db.flush()
    return decision


@pytest.mark.parametrize("invalid_kind", ["other_room", "assistant"])
async def test_receipts_reject_wrong_room_and_nonhuman_message_ids(
    db: AsyncSession, invalid_kind: str
) -> None:
    room = await seed(db)
    if invalid_kind == "other_room":
        other_room = await seed(db)
        invalid = await _message(db, other_room, "Other room message")
    else:
        invalid = await _message(db, room, "Agent answer", role="assistant")

    with pytest.raises(NotFoundError, match="Room message not found"):
        await RoomAutoRoutingService(db).receipts(room.ctx, room.conversation.id, [invalid.id])


async def test_legacy_human_message_without_author_does_not_reject_receipt_batch(
    db: AsyncSession,
) -> None:
    room = await seed(db)
    legacy = await _message(db, room, "Legacy human post")
    legacy.author_user_id = None
    await db.flush()

    result = await RoomAutoRoutingService(db).receipts(room.ctx, room.conversation.id, [legacy.id])

    assert result.items == []


async def test_receipt_is_withheld_when_reader_loses_inherited_source_access(
    db: AsyncSession,
) -> None:
    room = await seed(db)
    source = room.conversation
    source.project_id = None
    source.room_visibility = "private"
    destination = Conversation(
        id=uuid4(),
        organization_id=room.ctx.organization_id,
        user_id=room.user.id,
        room_visibility="private",
    )
    db.add(destination)
    db.add_all(
        [
            RoomParticipant(
                organization_id=room.ctx.organization_id,
                conversation_id=source.id,
                user_id=room.user.id,
            ),
            RoomParticipant(
                organization_id=room.ctx.organization_id,
                conversation_id=destination.id,
                user_id=room.user.id,
            ),
        ]
    )
    await db.flush()
    source_first = await _message(db, room, "Private source note")
    source_last = await _message(db, room, "Private source answer", role="assistant")
    context = await RoomThreadService(db).create_context(
        room.ctx,
        destination.id,
        RoomThreadRangeCreate(
            source_conversation_id=source.id,
            start_message_id=source_first.id,
            end_message_id=source_last.id,
        ),
    )

    destination_room = Room(
        ctx=room.ctx,
        user=room.user,
        conversation=destination,
        agent=room.agent,
        version=room.version,
        member=room.member,
    )
    accepted = Message(
        id=uuid4(),
        conversation_id=destination.id,
        role="user",
        content="Route this reply",
        author_user_id=room.user.id,
        thread_root_message_id=context.root_message_id,
        created_at=datetime.now(UTC),
    )
    db.add(accepted)
    await db.flush()
    submission = await _submission(
        db,
        destination_room,
        accepted,
        thread_root_message_id=context.root_message_id,
    )
    await _decision(
        db,
        destination_room,
        accepted,
        submission,
        thread_root_message_id=context.root_message_id,
    )

    reader = type(room.user)(
        id=uuid4(),
        email=f"{uuid4()}@example.com",
        hashed_password="x",
        is_active=True,
    )
    db.add(reader)
    await db.flush()
    source_membership = RoomParticipant(
        organization_id=room.ctx.organization_id,
        conversation_id=source.id,
        user_id=reader.id,
    )
    db.add_all(
        [
            source_membership,
            OrganizationMember(
                id=uuid4(),
                organization_id=room.ctx.organization_id,
                user_id=reader.id,
                role="member",
            ),
            RoomParticipant(
                organization_id=room.ctx.organization_id,
                conversation_id=destination.id,
                user_id=reader.id,
            ),
        ]
    )
    await db.flush()
    reader_ctx = AuthContext(
        user_id=reader.id,
        organization_id=room.ctx.organization_id,
        role="member",
    )

    result = await RoomAutoRoutingService(db).receipts(reader_ctx, destination.id, [accepted.id])

    assert [item.accepted_message_id for item in result.items] == [accepted.id]

    await db.delete(source_membership)
    await db.flush()

    with pytest.raises(NotFoundError, match="Conversation not found"):
        await RoomAutoRoutingService(db).receipts(reader_ctx, destination.id, [accepted.id])


async def test_continued_burst_has_one_canonical_receipt_with_chronological_group(
    db: AsyncSession,
) -> None:
    room = await seed(db)
    first = await _message(db, room, "First part")
    first_submission = await _submission(db, room, first)
    latest = await _message(db, room, "Latest part")
    latest_submission = await _submission(db, room, latest)
    latest_decision = await _decision(
        db, room, latest, latest_submission, grouped_message_ids=[first.id, latest.id]
    )
    await _decision(
        db,
        room,
        first,
        first_submission,
        status="continued",
        continued_into_id=latest_decision.id,
    )

    result = await RoomAutoRoutingService(db).receipts(
        room.ctx, room.conversation.id, [latest.id, first.id]
    )

    assert len(result.items) == 1
    receipt = result.items[0]
    assert receipt.accepted_message_id == latest.id
    assert receipt.grouped_message_ids == [first.id, latest.id]
    assert receipt.status == "pending"


async def test_selected_receipt_with_identity_outside_snapshot_becomes_unknown(
    db: AsyncSession,
) -> None:
    room = await seed(db)
    message = await _message(db, room, "Choose a responder")
    submission = await _submission(db, room, message)
    candidate_id = uuid4()
    await _decision(
        db,
        room,
        message,
        submission,
        status="selected",
        decision_output={"selected_ids": [str(uuid4())], "reason": "private detail"},
        candidate_snapshot=[
            {
                "agent_id": str(candidate_id),
                "version_id": str(room.version.id),
                "name": "Eligible agent",
                "role": None,
                "description": "",
            }
        ],
    )

    result = await RoomAutoRoutingService(db).receipts(room.ctx, room.conversation.id, [message.id])

    assert len(result.items) == 1
    assert result.items[0].status == "unknown"
    assert result.items[0].selected_agents == []


async def test_public_receipt_omits_reason_profile_and_internal_ledger_fields(
    db: AsyncSession,
) -> None:
    room = await seed(db)
    message = await _message(db, room, "Choose one responder")
    submission = await _submission(db, room, message)
    candidate_id = uuid4()
    profile_id = uuid4()
    row = await _decision(
        db,
        room,
        message,
        submission,
        status="selected",
        decision_output={
            "selected_ids": [str(candidate_id)],
            "reason": "private routing explanation that must not escape",
        },
        candidate_snapshot=[
            {
                "agent_id": str(candidate_id),
                "version_id": str(room.version.id),
                "name": "Eligible agent",
                "role": None,
                "description": "",
            }
        ],
    )
    row.model_profile_id = profile_id
    row.model_profile_fingerprint = "e" * 64
    row.error_code = "internal_private_error"
    await db.flush()

    result = await RoomAutoRoutingService(db).receipts(room.ctx, room.conversation.id, [message.id])

    assert len(result.items) == 1
    receipt = result.items[0]
    payload = receipt.model_dump(mode="json")
    serialized = json.dumps(payload)
    assert set(payload) == {
        "accepted_message_id",
        "grouped_message_ids",
        "status",
        "router_agent_id",
        "router_name",
        "selected_agents",
        "accepted_at",
        "finished_at",
    }
    assert receipt.selected_agents[0].name == "Eligible agent"
    assert "private routing explanation" not in serialized
    assert str(profile_id) not in serialized
    assert "model_profile" not in serialized
    assert "router_run_id" not in serialized
    assert "internal_private_error" not in serialized
