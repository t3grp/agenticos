"""One bounded, server-attributed reaction operation for the current room thread."""

from uuid import UUID

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.agents.capabilities.room_reactions._contract import RoomReactionsPort
from app.agents.deps import AgentDeps
from app.core.exceptions import AppException, BadRequestError


def build_room_reactions(port: RoomReactionsPort) -> FunctionToolset[AgentDeps]:
    toolset: FunctionToolset[AgentDeps] = FunctionToolset()

    async def react_to_room_message(
        ctx: RunContext[AgentDeps],
        emoji: str,
        message_id: UUID | None = None,
        reaction_only: bool = False,
    ) -> str:
        """React as this responder to a visible message in the current room thread.

        Omit message_id to react to the accepted human message. Otherwise choose
        an existing human or agent message from this room and thread. The server
        supplies the acting agent, room, organization, pinned version and run;
        those identities are never parameters. Set reaction_only=true only when
        one successful reaction is the entire reply. It defaults to false, and
        only a successful solo reaction with no text output will omit an empty
        assistant transcript row.

        Returns:
            A receipt or a refusal that explains why the room reaction was not saved.
        """
        try:
            if ctx.tool_call_id is None:
                raise BadRequestError(message="Room reactions require a recorded tool call")
            return await port.react(
                emoji,
                message_id=message_id,
                reaction_only=reaction_only,
                tool_call_id=ctx.tool_call_id,
            )
        except AppException as error:
            return error.message

    toolset.add_function(react_to_room_message, takes_ctx=True)
    return toolset
