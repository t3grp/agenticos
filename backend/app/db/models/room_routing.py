"""Routing provenance only. Canonical task contents remain in Virtual Tables."""

from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class RoomLeadPolicy(Base, TimestampMixin):
    __tablename__ = "room_lead_policies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_room_policy_conversation",
        ),
    )
    conversation_id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)

    def __repr__(self) -> str:
        return f"<RoomLeadPolicy(conversation_id={self.conversation_id}, revision={self.revision})>"


class RoomRoutingProposal(Base, TimestampMixin):
    __tablename__ = "room_routing_proposals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_room_proposal_conversation",
        ),
        UniqueConstraint("organization_id", "source_submission_id"),
        UniqueConstraint(
            "organization_id", "conversation_id", "id", name="uq_room_proposal_scope_id"
        ),
        ForeignKeyConstraint(
            ["organization_id", "conversation_id", "source_submission_id"],
            [
                "room_submissions.organization_id",
                "room_submissions.conversation_id",
                "room_submissions.id",
            ],
            ondelete="CASCADE",
            name="fk_room_proposal_source_scope",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "thread_root_message_id"],
            ["messages.conversation_id", "messages.id"],
            ondelete="CASCADE",
            name="fk_room_proposal_thread_scope",
        ),
        CheckConstraint("status IN ('pending', 'accepted', 'discarded')", name="decision"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    conversation_id: Mapped[UUID] = mapped_column(index=True)
    source_submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("room_submissions.id", ondelete="CASCADE")
    )
    thread_root_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE")
    )
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    policy_revision: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    items: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    decision_key: Mapped[str | None] = mapped_column(String(128))
    selected_items: Mapped[list[str] | None] = mapped_column(JSONB)

    def __repr__(self) -> str:
        return f"<RoomRoutingProposal(id={self.id}, status={self.status})>"


class RoomTaskLink(Base, TimestampMixin):
    __tablename__ = "room_task_links"
    __table_args__ = (
        UniqueConstraint("organization_id", "proposal_id", "item_id"),
        ForeignKeyConstraint(
            ["organization_id", "conversation_id", "proposal_id"],
            [
                "room_routing_proposals.organization_id",
                "room_routing_proposals.conversation_id",
                "room_routing_proposals.id",
            ],
            ondelete="CASCADE",
            name="fk_room_task_proposal_scope",
        ),
        ForeignKeyConstraint(
            ["organization_id", "task_id"],
            ["virtual_table_records.organization_id", "virtual_table_records.id"],
            ondelete="CASCADE",
            name="fk_room_task_tenant",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "thread_root_message_id"],
            ["messages.conversation_id", "messages.id"],
            ondelete="CASCADE",
            name="fk_room_task_thread_scope",
        ),
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_room_task_conversation",
        ),
    )
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_links.record_id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    conversation_id: Mapped[UUID] = mapped_column(index=True)
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("room_routing_proposals.id", ondelete="CASCADE")
    )
    item_id: Mapped[UUID] = mapped_column()
    thread_root_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE")
    )
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reviewer_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    agent_id: Mapped[UUID] = mapped_column()
    version_id: Mapped[UUID] = mapped_column()
    accepted_revision: Mapped[int] = mapped_column(Integer)

    def __repr__(self) -> str:
        return f"<RoomTaskLink(task_id={self.task_id}, item_id={self.item_id})>"
