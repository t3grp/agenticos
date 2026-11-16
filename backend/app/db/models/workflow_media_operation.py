"""Durable provider state for one workflow media operation.

Provider request bodies and responses do not belong here. The request hash is
for correlation; the receipt contains only a provider operation identifier.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

WorkflowMediaPhase = Literal[
    "prepared", "submitting", "submitted", "collecting", "stored", "unknown", "failed", "cancelled"
]


class WorkflowMediaOperation(Base, TimestampMixin):
    """One billable media operation, retained across dispatch attempts."""

    __tablename__ = "workflow_media_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    node_run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("node_runs.id", ondelete="CASCADE"), nullable=False
    )
    node_attempt_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("node_attempts.id", ondelete="CASCADE"), nullable=False
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organization_secrets.id", ondelete="SET NULL"),
        nullable=True,
    )
    secret_key_version: Mapped[int | None] = mapped_column(nullable=True)
    phase: Mapped[WorkflowMediaPhase] = mapped_column(
        String(16), nullable=False, default="prepared"
    )
    provider_receipt: Mapped[dict[str, str] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    output: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    reported_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    cost_is_partial: Mapped[bool] = mapped_column(nullable=False, default=False)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("node_run_id", name="uq_workflow_media_operation_node_run"),
        CheckConstraint(
            "phase IN ('prepared', 'submitting', 'submitted', 'collecting', 'stored', 'unknown', 'failed', 'cancelled')",
            name="ck_workflow_media_operation_phase",
        ),
        CheckConstraint(
            "estimated_cost_usd IS NULL OR estimated_cost_usd >= 0",
            name="ck_workflow_media_operation_estimated_cost",
        ),
        CheckConstraint(
            "reported_cost_usd IS NULL OR reported_cost_usd >= 0",
            name="ck_workflow_media_operation_reported_cost",
        ),
        Index("ix_workflow_media_operation_run", "organization_id", "workflow_run_id"),
    )

    def __repr__(self) -> str:
        return f"<WorkflowMediaOperation(id={self.id}, phase={self.phase})>"
