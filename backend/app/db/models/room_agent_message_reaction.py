"""Append-only agent actions on native room messages."""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RoomAgentMessageReaction(Base):
    """One trusted agent reaction with frozen run and actor provenance."""

    __tablename__ = "room_agent_message_reactions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_room_agent_message_reaction_organization",
        ),
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_room_agent_message_reaction_conversation",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "message_id"],
            ["messages.conversation_id", "messages.id"],
            ondelete="CASCADE",
            name="fk_room_agent_message_reaction_message",
        ),
        ForeignKeyConstraint(
            ["organization_id", "conversation_id", "submission_id"],
            [
                "room_submissions.organization_id",
                "room_submissions.conversation_id",
                "room_submissions.id",
            ],
            ondelete="CASCADE",
            name="fk_room_agent_message_reaction_submission",
        ),
        ForeignKeyConstraint(
            ["organization_id", "conversation_id", "decision_id"],
            [
                "room_auto_routing_decisions.organization_id",
                "room_auto_routing_decisions.conversation_id",
                "room_auto_routing_decisions.id",
            ],
            ondelete="CASCADE",
            name="fk_room_agent_message_reaction_decision",
        ),
        CheckConstraint(
            "char_length(emoji) BETWEEN 1 AND 32",
            name="ck_room_agent_message_reaction_emoji_length",
        ),
        CheckConstraint(
            "(source = 'tool' AND submission_id IS NOT NULL AND decision_id IS NULL "
            "AND tool_call_id IS NOT NULL) OR "
            "(source = 'automatic' AND submission_id IS NULL AND decision_id IS NOT NULL "
            "AND tool_call_id IS NULL AND reaction_only = false)",
            name="ck_room_agent_message_reaction_provenance",
        ),
        CheckConstraint(
            "NOT reaction_only OR source = 'tool'",
            name="ck_room_agent_message_reaction_only_tool",
        ),
        Index(
            "ix_room_agent_message_reactions_message",
            "organization_id",
            "conversation_id",
            "message_id",
        ),
        Index(
            "ix_room_agent_message_reactions_run_audit",
            "organization_id",
            "run_id",
            "created_at",
            "id",
        ),
        Index(
            "uq_room_agent_message_reactions_tool_call",
            "organization_id",
            "conversation_id",
            "run_id",
            "tool_call_id",
            unique=True,
            postgresql_where=text("source = 'tool'"),
        ),
        Index(
            "uq_room_agent_message_reactions_decision",
            "organization_id",
            "conversation_id",
            "decision_id",
            unique=True,
            postgresql_where=text("decision_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Snapshots deliberately have no agent/version FKs; actions remain attributable
    # after the agent is removed from the roster or its pinned version is retired.
    agent_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_version_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Run provenance is retained even after a run's operational row is removed.
    run_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source: Mapped[Literal["tool", "automatic"]] = mapped_column(String(16), nullable=False)
    submission_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    decision_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    reaction_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    emoji: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<RoomAgentMessageReaction(message={self.message_id}, emoji={self.emoji!r}, "
            f"agent={self.agent_id}, run={self.run_id})>"
        )
