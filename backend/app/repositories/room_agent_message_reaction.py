"""Tenant-bound agent reaction actions and their idempotency lookups."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.room_agent_message_reaction import RoomAgentMessageReaction

MAX_RUN_REACTIONS_READ = 100


async def by_tool_call(
    db: AsyncSession,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    tool_call_id: str,
) -> RoomAgentMessageReaction | None:
    return await db.scalar(
        select(RoomAgentMessageReaction).where(
            RoomAgentMessageReaction.organization_id == organization_id,
            RoomAgentMessageReaction.conversation_id == conversation_id,
            RoomAgentMessageReaction.run_id == run_id,
            RoomAgentMessageReaction.tool_call_id == tool_call_id,
        )
    )


async def by_decision(
    db: AsyncSession,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    decision_id: UUID,
) -> RoomAgentMessageReaction | None:
    return await db.scalar(
        select(RoomAgentMessageReaction).where(
            RoomAgentMessageReaction.organization_id == organization_id,
            RoomAgentMessageReaction.conversation_id == conversation_id,
            RoomAgentMessageReaction.decision_id == decision_id,
        )
    )


async def add(db: AsyncSession, reaction: RoomAgentMessageReaction) -> None:
    db.add(reaction)
    await db.flush()


async def list_for_run(
    db: AsyncSession,
    *,
    organization_id: UUID,
    run_id: UUID,
) -> list[RoomAgentMessageReaction]:
    """Read a chronological page plus one row to detect truncation."""
    result = await db.scalars(
        select(RoomAgentMessageReaction)
        .where(
            RoomAgentMessageReaction.organization_id == organization_id,
            RoomAgentMessageReaction.run_id == run_id,
        )
        .order_by(
            RoomAgentMessageReaction.created_at.asc(),
            RoomAgentMessageReaction.id.asc(),
        )
        .limit(MAX_RUN_REACTIONS_READ + 1)
    )
    return list(result.all())
