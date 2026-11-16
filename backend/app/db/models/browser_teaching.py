"""A lease-bound browser demonstration awaiting a person's review."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class BrowserTeachingSession(Base, TimestampMixin):
    """One synthetic-safe demonstration tied to its owner, run and browser call."""

    __tablename__ = "browser_teaching_sessions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "idempotency_key", name="browser_teaching_sessions_org_key"
        ),
        Index("ix_browser_teaching_owner_status", "organization_id", "owner_user_id", "status"),
        Index(
            "uq_browser_teaching_recording_per_browser",
            "organization_id",
            "run_id",
            "call_id",
            unique=True,
            postgresql_where=text("status = 'recording'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    origin: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="recording")
    pause_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    parameters: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False, default=list)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("skill_proposals.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capture_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<BrowserTeachingSession(id={self.id}, status={self.status})>"
