"""Trusted current-room reaction actions."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

RESOURCE = "room_reactions"
CAPABILITY_ID = "room_reactions"
TOOL_ID = "react_to_room_message"


@dataclass(frozen=True, slots=True)
class RoomReactionRunContext:
    """Authority bound by a native room submission, never by tool arguments."""

    organization_id: UUID
    conversation_id: UUID
    submission_id: UUID
    requester_id: UUID
    accepted_message_id: UUID
    agent_id: UUID
    agent_version_id: UUID
    thread_root_message_id: UUID | None


class RoomReactionsPort(Protocol):
    async def react(
        self,
        emoji: str,
        *,
        message_id: UUID | None,
        reaction_only: bool,
        tool_call_id: str,
    ) -> str: ...
