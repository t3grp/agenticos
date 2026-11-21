"""Opt-in automatic room routing policy and its durable one-shot decisions."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class RoomAutoRoutingPolicy(Base, TimestampMixin):
    """Per-room automatic routing settings, separate from project Ask Lead."""

    __tablename__ = "room_auto_routing_policies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_room_auto_routing_policy_conversation",
        ),
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint("quiet_window_ms BETWEEN 0 AND 10000", name="quiet_window_ms"),
        CheckConstraint("max_responders BETWEEN 1 AND 4", name="max_responders"),
        CheckConstraint(
            "(router_agent_id IS NULL) = (router_version_id IS NULL) AND "
            "(NOT enabled OR (router_agent_id IS NOT NULL AND model_profile_id IS NOT NULL))",
            name="router",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # The router is a normal room roster agent. Its version is pinned when this
    # policy is saved; model_profile_id is a required organization-owned override
    # when enabled, never the agent's potentially implicit personal default.
    router_agent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    router_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    model_profile_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    quiet_window_ms: Mapped[int] = mapped_column(Integer, default=2500, server_default="2500")
    max_responders: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    def __repr__(self) -> str:
        return f"<RoomAutoRoutingPolicy(conversation_id={self.conversation_id}, revision={self.revision})>"


RoomAutoRoutingStatus = Literal[
    "pending", "running", "selected", "abstained", "continued", "failed", "unknown"
]


class RoomAutoRoutingDecision(Base, TimestampMixin):
    """The immutable acceptance snapshot and one-shot router execution receipt.

    A `running` claim is never reclaimed. If the worker outcome is uncertain,
    recovery marks the row `unknown`; a paid call is never automatically replayed.
    """

    __tablename__ = "room_auto_routing_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_room_auto_routing_decision_conversation",
        ),
        ForeignKeyConstraint(
            ["organization_id", "conversation_id", "submission_id"],
            [
                "room_submissions.organization_id",
                "room_submissions.conversation_id",
                "room_submissions.id",
            ],
            ondelete="CASCADE",
            name="fk_room_auto_routing_decision_submission",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "accepted_message_id"],
            ["messages.conversation_id", "messages.id"],
            ondelete="CASCADE",
            name="fk_room_auto_routing_decision_message",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "thread_root_message_id"],
            ["messages.conversation_id", "messages.id"],
            ondelete="CASCADE",
            name="fk_room_auto_routing_decision_thread",
        ),
        ForeignKeyConstraint(
            ["organization_id", "conversation_id", "continued_into_id"],
            [
                "room_auto_routing_decisions.organization_id",
                "room_auto_routing_decisions.conversation_id",
                "room_auto_routing_decisions.id",
            ],
            ondelete="SET NULL (continued_into_id)",
            name="fk_room_auto_routing_decision_continued_into",
        ),
        UniqueConstraint(
            "organization_id", "conversation_id", "id", name="uq_room_auto_routing_decision_scope"
        ),
        UniqueConstraint(
            "organization_id",
            "conversation_id",
            "submission_id",
            name="uq_room_auto_routing_decision_submission",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'selected', 'abstained', 'continued', 'failed', 'unknown')",
            name="status",
        ),
        CheckConstraint("policy_revision >= 1", name="policy_revision"),
        CheckConstraint(
            "model_profile_fingerprint ~ '^[0-9a-f]{64}$'",
            name="model_profile_fingerprint",
        ),
        CheckConstraint("quiet_window_ms BETWEEN 0 AND 10000", name="quiet_window_ms"),
        CheckConstraint("max_responders BETWEEN 1 AND 4", name="max_responders"),
        CheckConstraint("claim_generation >= 0", name="claim_generation"),
        CheckConstraint(
            "(claim_token IS NULL AND claim_generation = 0) OR "
            "(claim_token IS NOT NULL AND claim_generation >= 1)",
            name="claim",
        ),
        CheckConstraint("status <> 'running' OR claim_token IS NOT NULL", name="running_claim"),
        CheckConstraint(
            "jsonb_typeof(candidate_snapshot) = 'array' "
            "AND jsonb_array_length(candidate_snapshot) <= 100 "
            "AND octet_length(candidate_snapshot::text) <= 262144",
            name="candidate_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(grouped_message_ids) = 'array' "
            "AND jsonb_array_length(grouped_message_ids) BETWEEN 1 AND 8",
            name="grouped_message_ids",
        ),
        CheckConstraint(
            "decision_output IS NULL OR (jsonb_typeof(decision_output) = 'object' "
            "AND octet_length(decision_output::text) <= 8192)",
            name="decision_output",
        ),
        Index(
            "room_auto_routing_ready_idx",
            "organization_id",
            "conversation_id",
            "ready_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "room_auto_routing_activity_idx",
            "organization_id",
            "conversation_id",
            "status",
        ),
        Index(
            "room_auto_routing_group_idx",
            "organization_id",
            "conversation_id",
            "requester_id",
            "thread_root_message_id",
            "accepted_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # The root, target-less RoomSubmission is the idempotent accepted post. It is
    # populated with responder submissions only after this decision is selected.
    submission_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    accepted_message_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    thread_root_message_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    router_agent_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    router_name: Mapped[str] = mapped_column(String(120), nullable=False)
    router_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    model_profile_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Hash of the selected organization's non-secret profile configuration as
    # accepted. The worker compares this before resolving or unsealing anything.
    model_profile_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    quiet_window_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    max_responders: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_snapshot: Mapped[list[dict[str, str | None]]] = mapped_column(JSONB, nullable=False)
    grouped_message_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[RoomAutoRoutingStatus] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    claim_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    claim_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    router_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), unique=True
    )
    decision_output: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    continued_into_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<RoomAutoRoutingDecision(id={self.id}, status={self.status})>"
