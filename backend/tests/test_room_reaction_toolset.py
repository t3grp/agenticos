"""The native reaction tool requires provider-recorded call identity."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agents.capabilities.room_reactions._toolset import build_room_reactions

pytestmark = [pytest.mark.anyio, pytest.mark.security]


async def test_missing_tool_call_identity_refuses_without_reaching_the_action_port() -> None:
    port = SimpleNamespace(react=AsyncMock())
    tool = build_room_reactions(port).tools["react_to_room_message"]

    result = await tool.function(SimpleNamespace(tool_call_id=None), "👍")

    assert result == "Room reactions require a recorded tool call"
    port.react.assert_not_awaited()


async def test_recorded_tool_call_identity_and_target_reach_the_trusted_port() -> None:
    message_id = uuid4()
    port = SimpleNamespace(react=AsyncMock(return_value="saved receipt"))
    tool = build_room_reactions(port).tools["react_to_room_message"]

    result = await tool.function(
        SimpleNamespace(tool_call_id="provider-call-1"),
        "👍",
        message_id=message_id,
        reaction_only=True,
    )

    assert result == "saved receipt"
    port.react.assert_awaited_once_with(
        "👍",
        message_id=message_id,
        reaction_only=True,
        tool_call_id="provider-call-1",
    )
