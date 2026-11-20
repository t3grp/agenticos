"""The private router gets bounded recent history with stable room speakers."""

import json
from uuid import uuid4

from app.db.models.conversation import Message
from app.schemas.room_auto_routing import RoomAutoRoutingCandidateSnapshot
from app.services.room_auto_routing import MAX_HISTORY_TEXT_CHARS, RoomAutoRoutingService


def _message(
    conversation_id,
    role: str,
    content: str,
    *,
    author_user_id=None,
    agent_id=None,
    reply_to_message_id=None,
) -> Message:
    return Message(
        id=uuid4(),
        conversation_id=conversation_id,
        role=role,
        content=content,
        author_user_id=author_user_id,
        agent_id=agent_id,
        reply_to_message_id=reply_to_message_id,
        thread_root_message_id=None,
        guardrail_refused=False,
    )


def test_history_keeps_recent_responder_and_restores_chronological_order():
    conversation_id = uuid4()
    requester_id = uuid4()
    other_member_id = uuid4()
    responder_id = uuid4()
    old = _message(
        conversation_id,
        "user",
        "old context " * 2500,
        author_user_id=other_member_id,
    )
    reply = _message(
        conversation_id,
        "assistant",
        "the recent agent reply",
        agent_id=responder_id,
    )
    latest = _message(
        conversation_id,
        "user",
        "the latest follow-up",
        author_user_id=requester_id,
        reply_to_message_id=reply.id,
    )

    history = RoomAutoRoutingService._history_text(
        [old, reply, latest],
        requester_id=requester_id,
        candidates=[
            RoomAutoRoutingCandidateSnapshot(
                agent_id=responder_id,
                version_id=uuid4(),
                name="Workspace agent",
                description="Public roster description",
            )
        ],
    )

    serialized = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
    assert len(serialized) <= MAX_HISTORY_TEXT_CHARS
    assert [item["text"] for item in history][-2:] == [
        "the recent agent reply",
        "the latest follow-up",
    ]
    assert history[0]["text"].startswith("old context ")
    assert history[-2]["speaker"] == {
        "kind": "agent",
        "id": str(responder_id),
        "name": "Workspace agent",
    }
    assert history[-1]["speaker"] == {"kind": "human", "label": "requester"}
    assert history[-1]["reply_to_message_id"] == str(reply.id)
    assert str(requester_id) not in serialized


def test_history_uses_stable_nonpersonal_labels_for_other_humans():
    conversation_id = uuid4()
    requester_id = uuid4()
    member_id = uuid4()
    first = _message(
        conversation_id,
        "user",
        "member message",
        author_user_id=member_id,
    )
    second = _message(
        conversation_id,
        "user",
        "requester message",
        author_user_id=requester_id,
    )

    history = RoomAutoRoutingService._history_text(
        [first, second], requester_id=requester_id, candidates=[]
    )

    assert [item["speaker"]["label"] for item in history] == ["room_member_1", "requester"]
    assert str(member_id) not in json.dumps(history)
