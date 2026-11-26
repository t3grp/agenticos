"""The calendar view's read shape: one event per row, whatever its source.

External meetings, routine occurrences and reminders arrive here in a single
shape so the console renders one timeline. The view is read-only: nothing in
this module creates, moves or deletes anything, on either side.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema


class CalendarEventSource(StrEnum):
    """Where one calendar event comes from."""

    GOOGLE_CALENDAR = "google_calendar"
    OUTLOOK_CALENDAR = "outlook_calendar"
    ROUTINE = "routine"
    REMINDER = "reminder"


class CalendarSourceStatus(StrEnum):
    """How one source lane ended up for this window."""

    LOADED = "loaded"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"


class CalendarEvent(BaseSchema):
    """One event on the calendar, from any source."""

    id: str
    title: str
    start: datetime
    end: datetime | None = None
    source: CalendarEventSource
    recurring: bool = False
    url: str | None = None
    agent_id: UUID | None = None
    agent_name: str | None = None
    trigger_id: UUID | None = None
    # A routine whose interval is under an hour has no meaningful calendar
    # occurrence, so the view shows one marker per day instead of hundreds of
    # events. The marker says so; it is never silently hidden.
    frequent: bool = False


class CalendarSourceRead(BaseSchema):
    """One source lane's outcome for the window."""

    status: CalendarSourceStatus
    reason: str | None = None
    count: int = 0


class CalendarEventsRead(BaseSchema):
    """The window the calendar asked for, and what each source returned."""

    events: list[CalendarEvent] = Field(default_factory=list)
    window_start: datetime
    window_end: datetime
    sources: dict[str, CalendarSourceRead] = Field(default_factory=dict)
