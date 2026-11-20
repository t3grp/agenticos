"""Routing prompt metadata resolves quoted messages to their eligible speaker."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.room_auto_routing import RoomAutoRoutingService


@pytest.mark.security
def test_latest_quote_resolves_older_agent_across_intervening_responder() -> None:
    requester_id, agent_a_id, agent_b_id = uuid4(), uuid4(), uuid4()
    root_id, human_id, agent_a_message_id, agent_b_message_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    latest_message_id = uuid4()
    decision = SimpleNamespace(
        requester_id=requester_id,
        max_responders=1,
        candidate_snapshot=[
            {
                "agent_id": str(agent_a_id),
                "version_id": str(uuid4()),
                "name": "Mira",
                "role": "API reviewer",
                "description": "Reviews API behavior.",
            },
            {
                "agent_id": str(agent_b_id),
                "version_id": str(uuid4()),
                "name": "Jules",
                "role": "Documentation editor",
                "description": "Updates technical documentation.",
            },
        ],
    )
    history_rows = [
        SimpleNamespace(
            id=human_id,
            role="user",
            author_user_id=requester_id,
            content="Please review the retry handler.",
            guardrail_refused=False,
            agent_id=None,
            reply_to_message_id=None,
            thread_root_message_id=root_id,
        ),
        SimpleNamespace(
            id=agent_a_message_id,
            role="assistant",
            author_user_id=None,
            content="Use bounded exponential backoff.",
            guardrail_refused=False,
            agent_id=agent_a_id,
            reply_to_message_id=human_id,
            thread_root_message_id=root_id,
        ),
        SimpleNamespace(
            id=agent_b_message_id,
            role="assistant",
            author_user_id=None,
            content="I can update the operator documentation.",
            guardrail_refused=False,
            agent_id=agent_b_id,
            reply_to_message_id=human_id,
            thread_root_message_id=root_id,
        ),
    ]
    latest_message = SimpleNamespace(
        id=latest_message_id,
        content="Use your earlier backoff suggestion and apply it.",
        reply_to_message_id=agent_a_message_id,
    )
    latest_submission = SimpleNamespace(thread_root_message_id=root_id)

    prompt = RoomAutoRoutingService._routing_prompt(
        object.__new__(RoomAutoRoutingService),
        decision,
        [(latest_message, latest_submission, [])],
        history_rows,
    )
    body = json.loads(prompt)

    history = body["bounded_conversation_history"]
    assert [entry["message_id"] for entry in history] == [
        str(human_id),
        str(agent_a_message_id),
        str(agent_b_message_id),
    ]
    assert history[1]["speaker"] == {"kind": "agent", "id": str(agent_a_id), "name": "Mira"}
    assert body["latest_posts"][0]["message_id"] == str(latest_message_id)
    assert body["latest_posts"][0]["reply_to_message_id"] == str(agent_a_message_id)
