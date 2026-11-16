"""Data access for expired browser teaching payloads."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.browser_teaching import BrowserTeachingSession


async def get_by_idempotency_key(
    db: AsyncSession, *, organization_id: UUID, idempotency_key: str
) -> BrowserTeachingSession | None:
    result = await db.execute(
        select(BrowserTeachingSession).where(
            BrowserTeachingSession.organization_id == organization_id,
            BrowserTeachingSession.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


async def recording_for_browser(
    db: AsyncSession, *, organization_id: UUID, run_id: UUID, call_id: str
) -> list[BrowserTeachingSession]:
    """Active teaching sessions for one managed browser, oldest first and locked."""
    result = await db.execute(
        select(BrowserTeachingSession)
        .where(
            BrowserTeachingSession.organization_id == organization_id,
            BrowserTeachingSession.run_id == run_id,
            BrowserTeachingSession.call_id == call_id,
            BrowserTeachingSession.status == "recording",
        )
        .order_by(BrowserTeachingSession.created_at, BrowserTeachingSession.id)
        .with_for_update()
    )
    return list(result.scalars().all())


async def expire_due(db: AsyncSession, *, now: datetime) -> int:
    result = await db.execute(
        update(BrowserTeachingSession)
        .where(
            BrowserTeachingSession.expires_at <= now,
            BrowserTeachingSession.status.notin_(["proposed", "discarded", "expired"]),
        )
        .values(
            status="expired",
            events=[],
            parameters=[],
            revision=BrowserTeachingSession.revision + 1,
        )
    )
    # AsyncSession.execute is typed as Result; DML results expose rowcount at runtime.
    return int(getattr(result, "rowcount", 0) or 0)
