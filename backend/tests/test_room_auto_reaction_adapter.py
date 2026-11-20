"""Automatic reaction adapter respects explicit human-directed mentions."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.room_auto_routing import RoomAutoRoutingService


@pytest.mark.anyio
@pytest.mark.security
async def test_explicit_user_mention_suppresses_same_name_agent_reaction():
    now = datetime.now(UTC)
    agent_id, human_id, requester_id, conversation_id, message_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    db = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=list)),
        scalar=AsyncMock(return_value=None),
    )
    service = object.__new__(RoomAutoRoutingService)
    service.db = db
    decision = SimpleNamespace(
        candidate_snapshot=[
            {
                "agent_id": agent_id,
                "version_id": uuid4(),
                "name": "Alex",
                "role": None,
                "description": "",
            }
        ],
        conversation_id=conversation_id,
        thread_root_message_id=None,
        requester_id=requester_id,
        accepted_at=now,
    )
    output = SimpleNamespace(
        scores=[SimpleNamespace(agent_id=agent_id, score=0.9)],
        social_intent=SimpleNamespace(react=True, emoji="❤️"),
        selected_ids=[],
    )
    accepted_message = SimpleNamespace(
        id=message_id,
        conversation_id=conversation_id,
        ordinal=2,
        role="user",
        author_user_id=requester_id,
        reply_to_message_id=None,
        content="Alex, that was lovely.",
        created_at=now,
        thread_root_message_id=None,
        room_mentions=[{"kind": "user", "id": str(human_id)}],
    )

    reaction = await service._social_action_for(
        decision,
        output,
        accepted_message=accepted_message,
    )

    assert reaction is None
    db.scalar.assert_not_awaited()
