"""Tests for the calendar route.

The handler is thin by design - the decision is the service's - so what is
worth asserting is only the part that is not delegation: that the provider
query string is parsed into the service's list, and that the answer is what
the service returned. That the route is gated on `agents:view` at all is
proven through the real app in `tests/api/test_platform_routes.py`.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.routes.v1.calendar import list_calendar_events
from app.core.permissions import AuthContext, OrgRoleName
from app.schemas.calendar import CalendarEventsRead

pytestmark = pytest.mark.anyio

_CTX = AuthContext(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role=OrgRoleName.OWNER.value)
_START = datetime(2026, 11, 26, 0, 0, tzinfo=UTC)
_END = datetime(2026, 11, 29, 0, 0, tzinfo=UTC)


async def test_the_route_returns_what_the_service_returned() -> None:
    read = CalendarEventsRead(events=[], window_start=_START, window_end=_END, sources={})
    service = MagicMock(list_events=AsyncMock(return_value=read))
    result = await list_calendar_events(_CTX, service, _START, _END, "google_calendar")
    assert result is read
    _, kwargs = service.list_events.call_args
    assert kwargs["providers"] == ("google_calendar",)
    assert kwargs["window_start"] == _START
    assert kwargs["window_end"] == _END


async def test_the_provider_list_is_split_and_trimmed() -> None:
    read = CalendarEventsRead(events=[], window_start=_START, window_end=_END, sources={})
    service = MagicMock(list_events=AsyncMock(return_value=read))
    await list_calendar_events(_CTX, service, _START, _END, "google_calendar, outlook_calendar ")
    _, kwargs = service.list_events.call_args
    assert kwargs["providers"] == ("google_calendar", "outlook_calendar")


async def test_an_empty_provider_list_means_both_providers() -> None:
    read = CalendarEventsRead(events=[], window_start=_START, window_end=_END, sources={})
    service = MagicMock(list_events=AsyncMock(return_value=read))
    await list_calendar_events(_CTX, service, _START, _END, "")
    _, kwargs = service.list_events.call_args
    assert kwargs["providers"] == ()
