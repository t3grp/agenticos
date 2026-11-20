"""HTTP settings for automatic room routing."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import Auth, DBSession
from app.schemas.room_auto_routing import (
    RoomAutoRoutingDecisionListRead,
    RoomAutoRoutingPolicyRead,
    RoomAutoRoutingPolicyWrite,
)
from app.services.room_auto_routing import RoomAutoRoutingService
from app.services.room_auto_routing_policy import RoomAutoRoutingPolicyService

router = APIRouter()


@router.get(
    "/{conversation_id}/automatic-routing-decisions",
    response_model=RoomAutoRoutingDecisionListRead,
)
async def read_automatic_routing_decisions(
    conversation_id: UUID,
    db: DBSession,
    ctx: Auth,
    message_id: list[UUID] = Query(..., min_length=1, max_length=100),
) -> Any:
    """Return bounded, safe receipts for messages on a readable room transcript page."""
    return await RoomAutoRoutingService(db).receipts(ctx, conversation_id, message_id)


@router.get(
    "/{conversation_id}/automatic-routing-policy",
    response_model=RoomAutoRoutingPolicyRead,
)
async def read_automatic_routing_policy(conversation_id: UUID, db: DBSession, ctx: Auth) -> Any:
    """Return the room's automatic-routing settings and whether this member can edit them."""
    return await RoomAutoRoutingPolicyService(db).policy(ctx, conversation_id)


@router.put(
    "/{conversation_id}/automatic-routing-policy",
    response_model=RoomAutoRoutingPolicyRead,
)
async def update_automatic_routing_policy(
    conversation_id: UUID,
    body: RoomAutoRoutingPolicyWrite,
    db: DBSession,
    ctx: Auth,
) -> Any:
    """Save a whole policy using its expected revision."""
    return await RoomAutoRoutingPolicyService(db).update_policy(ctx, conversation_id, body)
