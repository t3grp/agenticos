"""Schemas for audit log entries."""

from datetime import datetime
from typing import Any
from uuid import UUID

from app.schemas.base import BaseSchema


class AuditEntryRead(BaseSchema):
    """One recorded action."""

    id: UUID
    actor_user_id: UUID | None = None
    """Who did it, or `None` where the platform did - see `record_audit`."""
    impersonator_user_id: UUID | None = None
    """The administrator acting behind `actor_user_id`, or `None` when the actor
    was acting as themselves - an impersonated action names both (#943)."""
    action: str
    target_type: str | None = None
    target_id: str | None = None
    details: dict[str, Any] | None = None
    created_at: datetime | None = None
    actor_name: str | None = None
    """The actor's display name, resolved when the entry is read - the log
    itself stores UUIDs only, so a feed that shows one would show nobody."""
    sentence: str | None = None
    """The entry in plain language - "Agent X used send email, approved by
    W" - rendered from the action code when the entry is read. `None` where
    the entry was built without going through the read path."""


class AuditEntryList(BaseSchema):
    items: list[AuditEntryRead]
    total: int
