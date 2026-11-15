"""Tenant-scoped routing records, serialized by the room's transaction lock."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.conversation import Message
from app.db.models.room_routing import RoomLeadPolicy, RoomRoutingProposal, RoomTaskLink
from app.db.models.room_submission import RoomSubmission


async def policy(
    db: AsyncSession, organization_id: UUID, conversation_id: UUID
) -> RoomLeadPolicy | None:
    return await db.scalar(
        select(RoomLeadPolicy)
        .where(
            RoomLeadPolicy.organization_id == organization_id,
            RoomLeadPolicy.conversation_id == conversation_id,
        )
        .execution_options(populate_existing=True)
    )


async def proposals(
    db: AsyncSession, organization_id: UUID, conversation_id: UUID
) -> list[RoomRoutingProposal]:
    return list(
        (
            await db.scalars(
                select(RoomRoutingProposal)
                .where(
                    RoomRoutingProposal.organization_id == organization_id,
                    RoomRoutingProposal.conversation_id == conversation_id,
                )
                .order_by(RoomRoutingProposal.created_at.desc())
                .limit(50)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def proposal(
    db: AsyncSession, organization_id: UUID, conversation_id: UUID, proposal_id: UUID
) -> RoomRoutingProposal | None:
    return await db.scalar(
        select(RoomRoutingProposal)
        .where(
            RoomRoutingProposal.organization_id == organization_id,
            RoomRoutingProposal.conversation_id == conversation_id,
            RoomRoutingProposal.id == proposal_id,
        )
        .execution_options(populate_existing=True)
    )


async def for_submission(
    db: AsyncSession, organization_id: UUID, submission_id: UUID
) -> RoomRoutingProposal | None:
    return await db.scalar(
        select(RoomRoutingProposal).where(
            RoomRoutingProposal.organization_id == organization_id,
            RoomRoutingProposal.source_submission_id == submission_id,
        )
    )


async def links(
    db: AsyncSession, organization_id: UUID, conversation_id: UUID
) -> list[RoomTaskLink]:
    return list(
        (
            await db.scalars(
                select(RoomTaskLink)
                .where(
                    RoomTaskLink.organization_id == organization_id,
                    RoomTaskLink.conversation_id == conversation_id,
                )
                .order_by(RoomTaskLink.created_at.desc())
                .limit(100)
            )
        ).all()
    )


async def for_task(db: AsyncSession, organization_id: UUID, task_id: UUID) -> RoomTaskLink | None:
    return await db.scalar(
        select(RoomTaskLink).where(
            RoomTaskLink.organization_id == organization_id,
            RoomTaskLink.task_id == task_id,
        )
    )


async def save(db: AsyncSession, row: RoomLeadPolicy | RoomRoutingProposal | RoomTaskLink) -> None:
    db.add(row)
    await db.flush()
    await db.refresh(row)


async def lead_answer(
    db: AsyncSession, submission: RoomSubmission, message_id: UUID, answer: str
) -> None:
    message = await db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.run_id == submission.run_id,
            Message.conversation_id == submission.conversation_id,
            Message.role == "assistant",
            Message.thread_root_message_id.is_not_distinct_from(submission.thread_root_message_id),
        )
    )
    if message is None:
        raise RuntimeError("The lead answer lost its accepted room/thread binding")
    message.content = answer
    await db.flush()
    await db.refresh(message)
