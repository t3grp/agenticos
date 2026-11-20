"""Non-selectable room reaction capability, injected by trusted room runs only."""

from app.agents.capabilities._registry import (
    CapabilityBuildContext,
    CapabilityToolInfo,
    register,
)
from app.agents.capabilities.room_reactions._capability import RoomReactions
from app.agents.capabilities.room_reactions._contract import RESOURCE


@register(
    id="room_reactions",
    name="Room reactions",
    category="utility",
    description="React to a visible message in the current native room thread.",
    selectable=False,
    read_only=False,
    voice="never",
    tools=(
        CapabilityToolInfo(
            id="react_to_room_message",
            description="React as this responder to a visible message in the current room thread.",
            side_effecting=True,
            read_only=False,
        ),
    ),
)
def _build(ctx: CapabilityBuildContext) -> RoomReactions | None:
    port = ctx.resources.get(RESOURCE)
    return None if port is None else RoomReactions(port=port)


__all__ = ["RoomReactions"]
