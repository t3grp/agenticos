"""Agent-facing room reaction action, backed by trusted run authority."""

from dataclasses import dataclass

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

from app.agents.capabilities.room_reactions._contract import RoomReactionsPort
from app.agents.capabilities.room_reactions._toolset import build_room_reactions
from app.agents.deps import AgentDeps


@dataclass
class RoomReactions(AbstractCapability[AgentDeps]):
    port: RoomReactionsPort

    def get_toolset(self) -> FunctionToolset[AgentDeps]:
        return build_room_reactions(self.port)
