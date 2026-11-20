"""Tenant-bound room message reactions and their batched transcript summaries."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.conversation import Conversation, Message
from app.db.models.room_agent_message_reaction import RoomAgentMessageReaction
from app.db.models.room_message_reaction import RoomMessageReaction
from app.schemas.room_reaction import (
    RoomMessageReactionAgentRead,
    RoomMessageReactionRead,
)


async def get_room_message(
    db: AsyncSession,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
) -> Message | None:
    """Find a visible human/assistant message inside the caller's tenant and room."""
    return await db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.organization_id == organization_id,
            Message.conversation_id == conversation_id,
            Message.id == message_id,
            Message.role.in_(("user", "assistant")),
        )
    )


async def get_for_user(
    db: AsyncSession,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    user_id: UUID,
    emoji: str,
) -> RoomMessageReaction | None:
    return await db.scalar(
        select(RoomMessageReaction).where(
            RoomMessageReaction.organization_id == organization_id,
            RoomMessageReaction.conversation_id == conversation_id,
            RoomMessageReaction.message_id == message_id,
            RoomMessageReaction.user_id == user_id,
            RoomMessageReaction.emoji == emoji,
        )
    )


async def distinct_emoji_count(
    db: AsyncSession,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
) -> int:
    human = select(RoomMessageReaction.emoji.label("emoji")).where(
        RoomMessageReaction.organization_id == organization_id,
        RoomMessageReaction.conversation_id == conversation_id,
        RoomMessageReaction.message_id == message_id,
    )
    agents = select(RoomAgentMessageReaction.emoji.label("emoji")).where(
        RoomAgentMessageReaction.organization_id == organization_id,
        RoomAgentMessageReaction.conversation_id == conversation_id,
        RoomAgentMessageReaction.message_id == message_id,
    )
    combined = human.union(agents).subquery()
    return await db.scalar(select(func.count()).select_from(combined)) or 0


async def has_emoji(
    db: AsyncSession,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    emoji: str,
) -> bool:
    return bool(
        await db.scalar(
            select(
                or_(
                    select(RoomMessageReaction.id)
                    .where(
                        RoomMessageReaction.organization_id == organization_id,
                        RoomMessageReaction.conversation_id == conversation_id,
                        RoomMessageReaction.message_id == message_id,
                        RoomMessageReaction.emoji == emoji,
                    )
                    .exists(),
                    select(RoomAgentMessageReaction.id)
                    .where(
                        RoomAgentMessageReaction.organization_id == organization_id,
                        RoomAgentMessageReaction.conversation_id == conversation_id,
                        RoomAgentMessageReaction.message_id == message_id,
                        RoomAgentMessageReaction.emoji == emoji,
                    )
                    .exists(),
                )
            )
        )
    )


async def add(db: AsyncSession, reaction: RoomMessageReaction) -> None:
    db.add(reaction)
    await db.flush()


async def remove(
    db: AsyncSession,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    user_id: UUID,
    emoji: str,
) -> bool:
    reaction = await get_for_user(
        db,
        organization_id=organization_id,
        conversation_id=conversation_id,
        message_id=message_id,
        user_id=user_id,
        emoji=emoji,
    )
    if reaction is None:
        return False
    await db.delete(reaction)
    await db.flush()
    return True


async def list_aggregates(
    db: AsyncSession,
    *,
    organization_id: UUID,
    conversation_id: UUID,
    message_ids: Sequence[UUID],
    user_id: UUID | None,
) -> dict[UUID, list[RoomMessageReactionRead]]:
    """Read one authorized page of human and agent emoji aggregates."""
    if not message_ids:
        return {}
    reacted_by_me = (
        func.bool_or(RoomMessageReaction.user_id == user_id)
        if user_id is not None
        else literal(False)
    )
    human_rows = await db.execute(
        select(
            RoomMessageReaction.message_id,
            RoomMessageReaction.emoji,
            func.count(RoomMessageReaction.id).label("count"),
            reacted_by_me.label("reacted_by_me"),
            func.min(RoomMessageReaction.created_at).label("first_created_at"),
        )
        .where(
            RoomMessageReaction.organization_id == organization_id,
            RoomMessageReaction.conversation_id == conversation_id,
            RoomMessageReaction.message_id.in_(message_ids),
        )
        .group_by(RoomMessageReaction.message_id, RoomMessageReaction.emoji)
        .order_by(func.min(RoomMessageReaction.created_at), RoomMessageReaction.emoji)
    )
    agent_rows = await db.execute(
        select(
            RoomAgentMessageReaction.message_id,
            RoomAgentMessageReaction.emoji,
            RoomAgentMessageReaction.agent_id,
            RoomAgentMessageReaction.agent_name,
            RoomAgentMessageReaction.created_at,
            RoomAgentMessageReaction.id,
        )
        .where(
            RoomAgentMessageReaction.organization_id == organization_id,
            RoomAgentMessageReaction.conversation_id == conversation_id,
            RoomAgentMessageReaction.message_id.in_(message_ids),
        )
        .order_by(
            RoomAgentMessageReaction.message_id,
            RoomAgentMessageReaction.emoji,
            RoomAgentMessageReaction.agent_id,
            RoomAgentMessageReaction.created_at,
            RoomAgentMessageReaction.id,
        )
        .distinct(
            RoomAgentMessageReaction.message_id,
            RoomAgentMessageReaction.emoji,
            RoomAgentMessageReaction.agent_id,
        )
    )
    keys: set[tuple[UUID, str]] = set()
    human_counts: dict[tuple[UUID, str], int] = {}
    reacted_by_me_values: dict[tuple[UUID, str], bool] = {}
    first_created: dict[tuple[UUID, str], datetime] = {}
    for row in human_rows:
        key = (row.message_id, row.emoji)
        keys.add(key)
        human_counts[key] = row.count
        reacted_by_me_values[key] = bool(row.reacted_by_me)
        first_created[key] = row.first_created_at
    agents_by_key: dict[tuple[UUID, str], dict[UUID, RoomMessageReactionAgentRead]] = {}
    for row in agent_rows:
        key = (row.message_id, row.emoji)
        keys.add(key)
        first_created[key] = min(first_created.get(key, row.created_at), row.created_at)
        agents_by_key.setdefault(key, {}).setdefault(
            row.agent_id,
            RoomMessageReactionAgentRead(agent_id=row.agent_id, name=row.agent_name),
        )
    grouped: dict[UUID, list[RoomMessageReactionRead]] = {}
    for message_id, emoji in sorted(keys, key=lambda item: (first_created[item], item[1])):
        key = (message_id, emoji)
        agents = list(agents_by_key.get(key, {}).values())
        grouped.setdefault(message_id, []).append(
            RoomMessageReactionRead(
                emoji=emoji,
                count=human_counts.get(key, 0) + len(agents),
                reacted_by_me=reacted_by_me_values.get(key, False),
                agents=agents,
            )
        )
    return grouped
