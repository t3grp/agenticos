"""The calendar view: one timeline over external meetings, routines, reminders.

A collection read with no single resource whose grants could widen the answer,
so it carries `require(agents:view)` as its coarse first door, exactly as the
org-wide `GET /triggers` does. The per-agent visibility filtering still happens
in the service. Read-only: the view displays; it creates, moves or deletes
nothing, on either side.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import Auth, CalendarEventsSvc, require
from app.core.permissions import Perm
from app.schemas.calendar import CalendarEventsRead

router = APIRouter()


@router.get(
    "/events",
    response_model=CalendarEventsRead,
    dependencies=[Depends(require(Perm.AGENTS_VIEW))],
)
async def list_calendar_events(
    ctx: Auth,
    service: CalendarEventsSvc,
    window_start: datetime = Query(..., alias="from", description="Window start, inclusive"),
    window_end: datetime = Query(..., alias="to", description="Window end, inclusive"),
    providers: str = Query(
        "google_calendar,outlook_calendar",
        description="Comma-separated provider keys to read",
    ),
) -> Any:
    """Every event in the window, with each source's outcome reported.

    External lanes read live through the viewer's own calendar connections;
    a lane that cannot be read reports `unavailable` with its reason rather
    than an empty day. Routine occurrences are expanded from their stored
    next fire, and one-shot routines appear as reminders.
    """
    wanted = tuple(part.strip() for part in providers.split(",") if part.strip())
    return await service.list_events(
        ctx,
        window_start=window_start,
        window_end=window_end,
        providers=wanted,
    )
