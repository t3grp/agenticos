"""The calendar view's read path: external meetings, routine occurrences, reminders.

One timeline over three sources, read-only throughout. External events are read
live per view through the viewer's own Composio connection - the same read-only
actions an agent's calendar tools already use, executed here over a short-lived
MCP session instead of inside a run. Routines are expanded from their stored
next fire; nothing here advances `next_fire_at` - only the heartbeat claim does
that - and nothing here writes to `agent_triggers` at all.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cron import cron_next
from app.core.exceptions import BadRequestError
from app.core.permissions import AuthContext, Perm
from app.db.models.agent import Agent
from app.db.models.agent_trigger import AgentTrigger, ScheduleKind
from app.repositories import agent_trigger as agent_trigger_repo
from app.repositories import mcp_connection as mcp_connection_repo
from app.schemas.calendar import (
    CalendarEvent,
    CalendarEventSource,
    CalendarEventsRead,
    CalendarSourceRead,
    CalendarSourceStatus,
)
from app.services.access import AGENT, visible_resource_ids
from app.services.app_connections.runtime import managed_connection_spec
from app.services.portals import _calendar as portal_calendar
from app.services.portals.google_calendar import _meeting as google_meeting
from app.services.portals.outlook_calendar import _meeting as outlook_meeting

logger = logging.getLogger(__name__)

#: A window wider than this cannot fan out into thousands of provider calls.
MAX_WINDOW_DAYS = 90
#: Occurrences materialised per routine per window, so a dense cadence cannot
#: flood the view.
MAX_OCCURRENCES_PER_ROUTINE = 100
#: Provider events kept per source per window; the portal adapters cap the same way.
MAX_EXTERNAL_EVENTS = 250
#: An interval shorter than this has no meaningful calendar occurrence.
FREQUENT_INTERVAL_SECONDS = 3600
#: A provider round-trip gets longer than a probe's connect budget.
_MCP_TIMEOUT_SECS = 30.0


#: `mcp_session` yields an initialised MCP client session; the tool call returns
#: the provider's raw result. Injected so tests can answer without Composio.
class ToolCaller(Protocol):
    """One MCP tool call, wherever the session behind it comes from."""

    async def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        credential_origin: str | None,
    ) -> Any: ...


class _Provider:
    """One external calendar, as the Composio catalog names it."""

    def __init__(self, *, key: str, catalog_key: str, tool_name: str) -> None:
        self.key = key
        self.catalog_key = catalog_key
        self.tool_name = tool_name


_GOOGLE = _Provider(
    key=CalendarEventSource.GOOGLE_CALENDAR.value,
    catalog_key="composio-googlecalendar",
    tool_name="GOOGLECALENDAR_EVENTS_LIST",
)
_OUTLOOK = _Provider(
    key=CalendarEventSource.OUTLOOK_CALENDAR.value,
    catalog_key="composio-outlook",
    tool_name="OUTLOOK_LIST_EVENTS",
)
_PROVIDERS = (_GOOGLE, _OUTLOOK)


async def _call_mcp_tool(
    url: str,
    headers: Mapping[str, str],
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    credential_origin: str | None = None,
) -> Any:
    """One tool call over a short-lived MCP session, without a run."""
    from app.agents.mcp import mcp_session

    async with mcp_session(
        url,
        dict(headers),
        timeout=_MCP_TIMEOUT_SECS,
        credential_origin=credential_origin,
    ) as session:
        return await session.call_tool(tool_name, dict(arguments))


def expand_schedule_occurrences(
    trigger: AgentTrigger, *, window_start: datetime, window_end: datetime
) -> list[datetime]:
    """When a schedule fires inside the window, computed, never stored.

    Starts from the stored `next_fire_at` and steps forward with the same
    evaluator the claim path uses (`cron_next` for cron, `+ interval` for an
    interval). A one-shot yields its single fire. A paused schedule yields
    nothing - its occurrences are not on the calendar. `ends_at` bounds the
    expansion, and the per-routine cap stops a dense cadence flooding the view.
    Pure: it reads the row and returns instants; it never advances anything.
    """
    if trigger.next_fire_at is None:
        return []
    if not trigger.is_active or trigger.auto_stopped_at is not None:
        return []
    if trigger.one_shot:
        if window_start <= trigger.next_fire_at <= window_end:
            return [trigger.next_fire_at]
        return []
    zone = trigger.time_zone or "UTC"
    occurrences: list[datetime] = []
    current = trigger.next_fire_at
    while len(occurrences) < MAX_OCCURRENCES_PER_ROUTINE and current <= window_end:
        if current >= window_start and (trigger.ends_at is None or current <= trigger.ends_at):
            occurrences.append(current)
        if trigger.ends_at is not None and current >= trigger.ends_at:
            break
        if trigger.schedule_kind == ScheduleKind.CRON.value:
            current = cron_next(trigger.cron_expression or "", now=current, time_zone=zone)
        else:
            current = current + timedelta(seconds=trigger.interval_seconds or 0)
    return occurrences


def frequent_day_markers(
    trigger: AgentTrigger, *, window_start: datetime, window_end: datetime
) -> list[datetime]:
    """One marker per day for a routine that fires more often than hourly.

    An "every 5 minutes" routine has no meaningful calendar occurrence, so the
    view shows a single marker at local noon on each day in the window rather
    than hundreds of events - and rather than hiding the routine silently.
    """
    zone = ZoneInfo(trigger.time_zone or "UTC")
    start_day = window_start.astimezone(zone).date()
    end_day = window_end.astimezone(zone).date()
    markers: list[datetime] = []
    day = start_day
    while day <= end_day:
        markers.append(datetime(day.year, day.month, day.day, 12, 0, tzinfo=zone).astimezone(UTC))
        day += timedelta(days=1)
    return markers


def _routine_title(trigger: AgentTrigger, agent: Agent) -> str:
    if trigger.name:
        return trigger.name
    if trigger.one_shot and trigger.prompt:
        return trigger.prompt.splitlines()[0][:120]
    return agent.name


class CalendarEventsService:
    """The calendar view, served from the three sources it shows."""

    def __init__(self, db: AsyncSession, *, tool_caller: ToolCaller | None = None) -> None:
        self.db = db
        self._tool_caller = tool_caller or _call_mcp_tool

    async def list_events(
        self,
        ctx: AuthContext,
        *,
        window_start: datetime,
        window_end: datetime,
        providers: Sequence[str] = (),
    ) -> CalendarEventsRead:
        """Every event in the window, with each source's outcome reported.

        A window that is empty, backwards or wider than
        :data:`MAX_WINDOW_DAYS` is refused as a 422, not silently clipped. A
        provider that cannot be read reports its lane `unavailable` with the
        reason - never an empty list pretending the person is free, and never a
        500 for a revoked token.
        """
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=UTC)
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=UTC)
        if window_start >= window_end:
            raise BadRequestError(
                message="the calendar window must start before it ends",
                details={
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                },
            )
        if (window_end - window_start).days > MAX_WINDOW_DAYS:
            raise BadRequestError(
                message="the calendar window is wider than 90 days",
                details={"days": (window_end - window_start).days},
            )
        wanted = tuple(providers) or tuple(provider.key for provider in _PROVIDERS)
        known = {provider.key for provider in _PROVIDERS}
        unknown = sorted(set(wanted) - known)
        if unknown:
            raise BadRequestError(
                message="unknown calendar provider",
                details={"providers": unknown},
            )
        events: list[CalendarEvent] = []
        sources: dict[str, CalendarSourceRead] = {}
        routine_events, reminder_events, hidden = await self._routine_events(
            ctx, window_start=window_start, window_end=window_end
        )
        events.extend(routine_events)
        events.extend(reminder_events)
        sources[CalendarEventSource.ROUTINE.value] = CalendarSourceRead(
            status=CalendarSourceStatus.LOADED if routine_events else CalendarSourceStatus.EMPTY,
            reason=f"{hidden} paused schedule(s) hidden" if hidden else None,
            count=len(routine_events),
        )
        sources[CalendarEventSource.REMINDER.value] = CalendarSourceRead(
            status=CalendarSourceStatus.LOADED if reminder_events else CalendarSourceStatus.EMPTY,
            count=len(reminder_events),
        )
        for provider in _PROVIDERS:
            if provider.key not in wanted:
                continue
            provider_events, status = await self._external_events(
                ctx, provider, window_start=window_start, window_end=window_end
            )
            events.extend(provider_events)
            sources[provider.key] = status
        events.sort(key=lambda event: event.start)
        return CalendarEventsRead(
            events=events,
            window_start=window_start,
            window_end=window_end,
            sources=sources,
        )

    async def _routine_events(
        self,
        ctx: AuthContext,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[list[CalendarEvent], list[CalendarEvent], int]:
        """Occurrences of the schedules the caller may see, split by source."""
        visible = await visible_resource_ids(
            self.db, ctx, resource_type=AGENT, perm=Perm.AGENTS_VIEW
        )
        rows = await agent_trigger_repo.list_schedules_for_calendar(
            self.db,
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            see_all=visible is None,
            shared_ids=visible or [],
        )
        routines: list[CalendarEvent] = []
        reminders: list[CalendarEvent] = []
        hidden = 0
        for trigger, agent in rows:
            if not trigger.is_active or trigger.auto_stopped_at is not None:
                hidden += 1
                continue
            if (
                not trigger.one_shot
                and trigger.schedule_kind == ScheduleKind.INTERVAL.value
                and (trigger.interval_seconds or 0) < FREQUENT_INTERVAL_SECONDS
            ):
                occurrences = frequent_day_markers(
                    trigger, window_start=window_start, window_end=window_end
                )
                for marker in occurrences:
                    routines.append(
                        CalendarEvent(
                            id=f"routine:{trigger.id}:{marker.date().isoformat()}:frequent",
                            title=f"{_routine_title(trigger, agent)} (runs frequently)",
                            start=marker,
                            end=None,
                            source=CalendarEventSource.ROUTINE,
                            recurring=True,
                            url=None,
                            agent_id=agent.id,
                            agent_name=agent.name,
                            trigger_id=trigger.id,
                            frequent=True,
                        )
                    )
                continue
            for occurrence in expand_schedule_occurrences(
                trigger, window_start=window_start, window_end=window_end
            ):
                is_reminder = trigger.one_shot is True
                event = CalendarEvent(
                    id=f"routine:{trigger.id}"
                    if is_reminder
                    else f"routine:{trigger.id}:{occurrence.isoformat()}",
                    title=_routine_title(trigger, agent),
                    start=occurrence,
                    end=None,
                    source=CalendarEventSource.REMINDER
                    if is_reminder
                    else CalendarEventSource.ROUTINE,
                    recurring=not is_reminder,
                    url=None,
                    agent_id=agent.id,
                    agent_name=agent.name,
                    trigger_id=trigger.id,
                )
                (reminders if is_reminder else routines).append(event)
        return routines, reminders, hidden

    async def _external_events(
        self,
        ctx: AuthContext,
        provider: _Provider,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[list[CalendarEvent], CalendarSourceRead]:
        """One provider's meetings in the window, normalised to one shape."""
        if ctx.user_id is None:
            # A caller with no user behind the request has no connection to
            # read from - the lane is unavailable, not empty.
            return [], CalendarSourceRead(
                status=CalendarSourceStatus.UNAVAILABLE,
                reason="no connected account",
                count=0,
            )
        connections = await mcp_connection_repo.list_composio_for_user(self.db, user_id=ctx.user_id)
        connection = next(
            (
                row
                for row in connections
                if row.catalog_key == provider.catalog_key and row.composio_status == "ACTIVE"
            ),
            None,
        )
        if connection is None:
            return [], CalendarSourceRead(
                status=CalendarSourceStatus.UNAVAILABLE,
                reason="no connected account",
                count=0,
            )
        try:
            spec = await managed_connection_spec(
                connection,
                name="calendar",
                allowed_tools=[provider.tool_name],
                approval="none",
            )
        except Exception:
            logger.info("Calendar provider %s could not be verified", provider.key)
            return [], CalendarSourceRead(
                status=CalendarSourceStatus.UNAVAILABLE,
                reason="the connected account could not be verified",
                count=0,
            )
        if spec is None:
            return [], CalendarSourceRead(
                status=CalendarSourceStatus.UNAVAILABLE,
                reason="the connected account is not usable",
                count=0,
            )
        arguments: Mapping[str, Any] = (
            {
                "calendarId": "primary",
                "timeMin": window_start.isoformat(),
                "timeMax": window_end.isoformat(),
                "singleEvents": True,
                "maxResults": MAX_EXTERNAL_EVENTS,
            }
            if provider is _GOOGLE
            else {
                "startDateTime": window_start.isoformat(),
                "endDateTime": window_end.isoformat(),
            }
        )
        try:
            result = await self._tool_caller(
                spec.url,
                spec.headers,
                provider.tool_name,
                arguments,
                credential_origin=spec.credential_origin,
            )
        except Exception as exc:
            logger.info("Calendar provider %s could not be read: %s", provider.key, exc)
            return [], CalendarSourceRead(
                status=CalendarSourceStatus.UNAVAILABLE,
                reason="the calendar could not be read",
                count=0,
            )
        if getattr(result, "isError", False):
            return [], CalendarSourceRead(
                status=CalendarSourceStatus.UNAVAILABLE,
                reason="the calendar provider refused the read",
                count=0,
            )
        items = _tool_result_items(result)
        if items is None:
            return [], CalendarSourceRead(
                status=CalendarSourceStatus.UNAVAILABLE,
                reason="the calendar provider answered in an unexpected shape",
                count=0,
            )
        meetings: list[portal_calendar.Meeting] = []
        for item in items[:MAX_EXTERNAL_EVENTS]:
            if provider is _GOOGLE:
                meeting = google_meeting(item)
            else:
                meeting = outlook_meeting(item, account=connection.account_email or "")
            if meeting is not None:
                meetings.append(meeting)
        events = [
            CalendarEvent(
                id=f"ext:{provider.key}:{meeting.meeting_id}",
                title=meeting.subject or "(No title)",
                start=meeting.starts_at,
                end=meeting.ends_at,
                source=CalendarEventSource(provider.key),
                recurring=False,
                url=meeting.url or meeting.join_url or None,
                agent_id=None,
                agent_name=None,
                trigger_id=None,
            )
            for meeting in meetings
        ]
        return events, CalendarSourceRead(
            status=CalendarSourceStatus.LOADED if events else CalendarSourceStatus.EMPTY,
            count=len(events),
        )


def _tool_result_items(result: Any) -> list[Any] | None:
    """The provider events out of an MCP tool result, or `None` if unreadable.

    Composio answers with the provider's own listing shape - a list, or an
    object holding `items` - sometimes wrapped once more under `data`.
    Anything else is an unexpected shape, not an empty calendar.
    """
    texts = [
        block.text
        for block in getattr(result, "content", []) or []
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)
    ]
    if not texts:
        return None
    try:
        payload = json.loads("".join(texts))
    except ValueError, TypeError:
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return items
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
    return None
