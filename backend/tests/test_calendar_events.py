"""Tests for the calendar view's read path.

What is worth holding here is what the view must never get wrong: a window it
refuses rather than silently clips, a provider it reports unavailable rather
than empty, a routine it shows without ever advancing, and a paused schedule
it hides while saying so. The external normalisation reuses the portal
adapters' meeting filters, so a meeting the account declined, cancelled or
holds alone never appears - and neither does an all-day event, which has no
time to place on a timeline.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import BadRequestError
from app.core.permissions import AuthContext, OrgRoleName
from app.db.models.agent import Agent
from app.db.models.agent_trigger import AgentTrigger
from app.db.models.mcp_connection import McpConnection
from app.services import calendar_events
from app.services.calendar_events import (
    CalendarEventsService,
    expand_schedule_occurrences,
    frequent_day_markers,
)

pytestmark = pytest.mark.anyio

_ORG = uuid.uuid4()
_CALLER = uuid.uuid4()
_NOW = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
_START = datetime(2026, 11, 26, 0, 0, tzinfo=UTC)
_END = datetime(2026, 11, 29, 0, 0, tzinfo=UTC)


def _ctx() -> AuthContext:
    return AuthContext(user_id=_CALLER, organization_id=_ORG, role=OrgRoleName.OWNER.value)


def _agent(name: str = "Nightly") -> Agent:
    return Agent(id=uuid.uuid4(), name=name)


def _trigger(**overrides: Any) -> AgentTrigger:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "organization_id": _ORG,
        "agent_id": uuid.uuid4(),
        "created_by_user_id": _CALLER,
        "is_active": True,
        "auto_stopped_at": None,
        "trigger_type": "schedule",
        "schedule_kind": "interval",
        "interval_seconds": 3600,
        "cron_expression": None,
        "time_zone": None,
        "name": None,
        "prompt": "summarise the day",
        "next_fire_at": datetime(2026, 11, 26, 10, 0, tzinfo=UTC),
        "last_fired_at": None,
        "one_shot": False,
        "ends_at": None,
        "result_mode": "report",
        "answer_to": "run_log",
    }
    defaults.update(overrides)
    return AgentTrigger(**defaults)


def _connection(catalog_key: str, *, status: str = "ACTIVE") -> McpConnection:
    return McpConnection(
        id=uuid.uuid4(),
        catalog_key=catalog_key,
        composio_status=status,
        account_email="person@example.com",
        scope="user",
        purpose="mcp",
        auth_type="composio",
    )


def _tool_result(payload: Any, *, is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        isError=is_error,
    )


def _google_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": "google-1",
        "iCalUID": "google-1@google.com",
        "summary": "Planning",
        "status": "confirmed",
        "start": {"dateTime": "2026-11-26T10:00:00+01:00"},
        "end": {"dateTime": "2026-11-26T11:00:00+01:00"},
        "organizer": {"email": "lead@example.com"},
        "attendees": [
            {"email": "person@example.com", "self": True, "responseStatus": "accepted"},
            {"email": "teammate@example.com", "responseStatus": "accepted"},
        ],
        "htmlLink": "https://calendar.google.com/event?eid=google-1",
    }
    event.update(overrides)
    return event


def _outlook_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": "outlook-1",
        "iCalUId": "outlook-1@outlook.com",
        "subject": "Review",
        "isCancelled": False,
        "isAllDay": False,
        "start": {"dateTime": "2026-11-26T14:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-11-26T15:00:00", "timeZone": "UTC"},
        "organizer": {"emailAddress": {"address": "lead@example.com"}},
        "attendees": [
            {
                "type": "required",
                "emailAddress": {"address": "teammate@example.com"},
            }
        ],
        "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/1"},
        "location": {"displayName": "Room 4"},
        "bodyPreview": "Weekly review",
        "webLink": "https://outlook.office.com/calendar/item/outlook-1",
    }
    event.update(overrides)
    return event


class _Service:
    """A calendar service whose outside world answers from fakes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, rows: list | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.calls: list[dict[str, Any]] = []
        self.connections: list[McpConnection] = []
        self.spec: Any = SimpleNamespace(
            url="https://backend.composio.dev/mcp/session",
            headers={"x-api-key": "test"},
            credential_origin="https://backend.composio.dev",
        )
        self.tool_result: Any = _tool_result({"items": []})
        self.tool_error: Exception | None = None
        monkeypatch.setattr(calendar_events, "visible_resource_ids", AsyncMock(return_value=None))
        monkeypatch.setattr(
            calendar_events.agent_trigger_repo,
            "list_schedules_for_calendar",
            AsyncMock(return_value=self.rows),
        )
        # side_effect lambdas, not return_value: the tests reassign
        # `connections` / `spec` after construction, and the mock must answer
        # with the current value, not the one bound at setup.
        monkeypatch.setattr(
            calendar_events.mcp_connection_repo,
            "list_composio_for_user",
            AsyncMock(side_effect=lambda *args, **kwargs: self.connections),
        )
        monkeypatch.setattr(
            calendar_events,
            "managed_connection_spec",
            AsyncMock(side_effect=lambda *args, **kwargs: self.spec),
        )

    async def _caller(
        self,
        url: str,
        headers: Any,
        tool_name: str,
        arguments: Any,
        *,
        credential_origin: str | None,
    ) -> Any:
        self.calls.append(
            {
                "url": url,
                "tool_name": tool_name,
                "arguments": arguments,
                "credential_origin": credential_origin,
            }
        )
        if self.tool_error is not None:
            raise self.tool_error
        return self.tool_result

    def service(self) -> CalendarEventsService:
        return CalendarEventsService(AsyncMock(), tool_caller=self._caller)


class TestOccurrenceExpansion:
    def test_a_cron_routine_expands_its_fires_inside_the_window(self) -> None:
        trigger = _trigger(
            schedule_kind="cron",
            cron_expression="0 9 * * *",
            interval_seconds=None,
            next_fire_at=datetime(2026, 11, 26, 9, 0, tzinfo=UTC),
        )
        occurrences = expand_schedule_occurrences(trigger, window_start=_START, window_end=_END)
        assert occurrences == [
            datetime(2026, 11, 26, 9, 0, tzinfo=UTC),
            datetime(2026, 11, 27, 9, 0, tzinfo=UTC),
            datetime(2026, 11, 28, 9, 0, tzinfo=UTC),
        ]

    def test_a_cron_routine_starting_before_the_window_advances_into_it(self) -> None:
        trigger = _trigger(
            schedule_kind="cron",
            cron_expression="0 9 * * *",
            interval_seconds=None,
            next_fire_at=datetime(2026, 11, 20, 9, 0, tzinfo=UTC),
        )
        occurrences = expand_schedule_occurrences(trigger, window_start=_START, window_end=_END)
        assert occurrences[0] == datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
        assert len(occurrences) == 3

    def test_a_cron_routine_across_a_dst_boundary_fires_at_nine_local(self) -> None:
        """Europe/Warsaw springs forward on 29 Mar 2026: nine local is 08:00 UTC
        before the change and 07:00 UTC after it. The occurrence is the wall
        time in the routine's zone, not a fixed UTC offset."""
        trigger = _trigger(
            schedule_kind="cron",
            cron_expression="0 9 * * *",
            interval_seconds=None,
            time_zone="Europe/Warsaw",
            next_fire_at=datetime(2026, 3, 28, 8, 0, tzinfo=UTC),
        )
        occurrences = expand_schedule_occurrences(
            trigger,
            window_start=datetime(2026, 3, 28, 0, 0, tzinfo=UTC),
            window_end=datetime(2026, 3, 31, 0, 0, tzinfo=UTC),
        )
        assert occurrences == [
            datetime(2026, 3, 28, 8, 0, tzinfo=UTC),
            datetime(2026, 3, 29, 7, 0, tzinfo=UTC),
            datetime(2026, 3, 30, 7, 0, tzinfo=UTC),
        ]

    def test_an_interval_routine_steps_from_its_next_fire(self) -> None:
        trigger = _trigger(
            interval_seconds=12 * 3600,
            next_fire_at=datetime(2026, 11, 26, 10, 0, tzinfo=UTC),
        )
        occurrences = expand_schedule_occurrences(trigger, window_start=_START, window_end=_END)
        assert occurrences == [
            datetime(2026, 11, 26, 10, 0, tzinfo=UTC),
            datetime(2026, 11, 26, 22, 0, tzinfo=UTC),
            datetime(2026, 11, 27, 10, 0, tzinfo=UTC),
            datetime(2026, 11, 27, 22, 0, tzinfo=UTC),
            datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
            datetime(2026, 11, 28, 22, 0, tzinfo=UTC),
        ]

    def test_a_dense_cadence_is_capped_at_one_hundred_occurrences(self) -> None:
        trigger = _trigger(
            schedule_kind="cron",
            cron_expression="* * * * *",
            interval_seconds=None,
            next_fire_at=datetime(2026, 11, 26, 0, 1, tzinfo=UTC),
        )
        occurrences = expand_schedule_occurrences(trigger, window_start=_START, window_end=_END)
        assert len(occurrences) == 100

    def test_ends_at_bounds_the_expansion(self) -> None:
        trigger = _trigger(
            schedule_kind="cron",
            cron_expression="0 9 * * *",
            interval_seconds=None,
            next_fire_at=datetime(2026, 11, 26, 9, 0, tzinfo=UTC),
            ends_at=datetime(2026, 11, 27, 9, 0, tzinfo=UTC),
        )
        occurrences = expand_schedule_occurrences(trigger, window_start=_START, window_end=_END)
        assert occurrences == [
            datetime(2026, 11, 26, 9, 0, tzinfo=UTC),
            datetime(2026, 11, 27, 9, 0, tzinfo=UTC),
        ]

    def test_a_paused_routine_expands_to_nothing(self) -> None:
        paused = _trigger(is_active=False)
        stopped = _trigger(auto_stopped_at=datetime(2026, 11, 25, 0, 0, tzinfo=UTC))
        assert expand_schedule_occurrences(paused, window_start=_START, window_end=_END) == []
        assert expand_schedule_occurrences(stopped, window_start=_START, window_end=_END) == []

    def test_a_trigger_with_no_next_fire_yields_nothing(self) -> None:
        trigger = _trigger(next_fire_at=None)
        assert expand_schedule_occurrences(trigger, window_start=_START, window_end=_END) == []

    def test_a_one_shot_routine_yields_its_single_fire_inside_the_window(self) -> None:
        trigger = _trigger(one_shot=True, next_fire_at=datetime(2026, 11, 27, 17, 0, tzinfo=UTC))
        assert expand_schedule_occurrences(trigger, window_start=_START, window_end=_END) == [
            datetime(2026, 11, 27, 17, 0, tzinfo=UTC)
        ]
        outside = _trigger(one_shot=True, next_fire_at=datetime(2026, 12, 1, 17, 0, tzinfo=UTC))
        assert expand_schedule_occurrences(outside, window_start=_START, window_end=_END) == []

    def test_frequent_markers_are_one_per_day_at_local_noon(self) -> None:
        # The window ends at midnight UTC on the 29th, which is already the
        # 29th in Singapore, so four local days are covered.
        trigger = _trigger(interval_seconds=300, time_zone="Asia/Singapore")
        markers = frequent_day_markers(trigger, window_start=_START, window_end=_END)
        assert markers == [
            datetime(2026, 11, 26, 4, 0, tzinfo=UTC),
            datetime(2026, 11, 27, 4, 0, tzinfo=UTC),
            datetime(2026, 11, 28, 4, 0, tzinfo=UTC),
            datetime(2026, 11, 29, 4, 0, tzinfo=UTC),
        ]


class TestToolCaller:
    async def test_the_default_caller_opens_one_mcp_session_and_calls_the_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from contextlib import asynccontextmanager

        from app.agents import mcp as mcp_module
        from app.services.calendar_events import _call_mcp_tool

        seen: dict[str, object] = {}

        class _Session:
            async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
                seen["name"] = name
                seen["arguments"] = arguments
                return "called"

        @asynccontextmanager
        async def _fake_session(url: str, headers: dict[str, str], **kwargs: Any):  # type: ignore[no-untyped-def]
            seen["url"] = url
            seen["headers"] = headers
            seen["timeout"] = kwargs.get("timeout")
            seen["credential_origin"] = kwargs.get("credential_origin")
            yield _Session()

        monkeypatch.setattr(mcp_module, "mcp_session", _fake_session)
        result = await _call_mcp_tool(
            "https://backend.composio.dev/mcp/session",
            {"x-api-key": "test"},
            "GOOGLECALENDAR_EVENTS_LIST",
            {"calendarId": "primary"},
            credential_origin="https://backend.composio.dev",
        )
        assert result == "called"
        assert seen["name"] == "GOOGLECALENDAR_EVENTS_LIST"
        assert seen["arguments"] == {"calendarId": "primary"}
        assert seen["credential_origin"] == "https://backend.composio.dev"


class TestWindowValidation:
    async def test_a_window_that_ends_before_it_starts_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        with pytest.raises(BadRequestError):
            await harness.service().list_events(_ctx(), window_start=_END, window_end=_START)

    async def test_a_window_wider_than_ninety_days_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        with pytest.raises(BadRequestError):
            await harness.service().list_events(
                _ctx(),
                window_start=_START,
                window_end=_START + timedelta(days=91),
            )

    async def test_an_unknown_provider_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        harness = _Service(monkeypatch)
        with pytest.raises(BadRequestError):
            await harness.service().list_events(
                _ctx(), window_start=_START, window_end=_END, providers=("yahoo",)
            )

    async def test_naive_window_bounds_are_read_as_utc(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        read = await harness.service().list_events(
            _ctx(),
            window_start=_START.replace(tzinfo=None),
            window_end=_END.replace(tzinfo=None),
        )
        assert read.window_start == _START
        assert read.window_end == _END


class TestRoutineLanes:
    async def test_routine_and_reminder_events_share_one_timeline_sorted_by_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = _agent()
        cron = _trigger(
            schedule_kind="cron",
            cron_expression="0 9 * * *",
            interval_seconds=None,
            name="Morning brief",
            next_fire_at=datetime(2026, 11, 26, 9, 0, tzinfo=UTC),
        )
        reminder = _trigger(
            one_shot=True,
            name="Call the bank",
            next_fire_at=datetime(2026, 11, 26, 8, 0, tzinfo=UTC),
        )
        harness = _Service(monkeypatch, rows=[(cron, agent), (reminder, agent)])
        read = await harness.service().list_events(_ctx(), window_start=_START, window_end=_END)
        assert [event.title for event in read.events[:2]] == ["Call the bank", "Morning brief"]
        assert [event.source for event in read.events[:2]] == ["reminder", "routine"]
        assert read.events[0].start < read.events[1].start
        assert read.sources["reminder"].count == 1
        assert read.sources["routine"].count == 3
        assert read.sources["routine"].status == "loaded"

    async def test_a_paused_schedule_is_hidden_and_counted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = _agent()
        paused = _trigger(is_active=False, name="Paused")
        harness = _Service(monkeypatch, rows=[(paused, agent)])
        read = await harness.service().list_events(_ctx(), window_start=_START, window_end=_END)
        assert read.events == []
        assert read.sources["routine"].status == "empty"
        assert read.sources["routine"].reason == "1 paused schedule(s) hidden"

    async def test_a_sub_hourly_interval_shows_one_frequent_marker_per_day(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = _agent()
        busy = _trigger(interval_seconds=300, name="Heartbeat", time_zone="UTC")
        harness = _Service(monkeypatch, rows=[(busy, agent)])
        read = await harness.service().list_events(_ctx(), window_start=_START, window_end=_END)
        assert len(read.events) == 4
        assert all(event.frequent for event in read.events)
        assert all("(runs frequently)" in event.title for event in read.events)

    async def test_a_read_never_advances_next_fire_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = _agent()
        trigger = _trigger(
            schedule_kind="cron",
            cron_expression="0 9 * * *",
            interval_seconds=None,
            next_fire_at=datetime(2026, 11, 26, 9, 0, tzinfo=UTC),
        )
        harness = _Service(monkeypatch, rows=[(trigger, agent)])
        await harness.service().list_events(_ctx(), window_start=_START, window_end=_END)
        assert trigger.next_fire_at == datetime(2026, 11, 26, 9, 0, tzinfo=UTC)

    async def test_an_event_trigger_row_yields_no_occurrence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = _agent()
        event_trigger = _trigger(trigger_type="event", next_fire_at=None)
        harness = _Service(monkeypatch, rows=[(event_trigger, agent)])
        read = await harness.service().list_events(_ctx(), window_start=_START, window_end=_END)
        assert read.events == []

    async def test_a_caller_who_sees_only_some_agents_gets_only_those_schedules(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shared = [uuid.uuid4()]
        monkeypatch.setattr(
            calendar_events,
            "visible_resource_ids",
            AsyncMock(return_value=shared),
        )
        list_schedules = AsyncMock(return_value=[])
        monkeypatch.setattr(
            calendar_events.agent_trigger_repo,
            "list_schedules_for_calendar",
            list_schedules,
        )
        monkeypatch.setattr(
            calendar_events.mcp_connection_repo,
            "list_composio_for_user",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            calendar_events, "managed_connection_spec", AsyncMock(return_value=None)
        )
        service = CalendarEventsService(AsyncMock())
        await service.list_events(_ctx(), window_start=_START, window_end=_END)
        _, kwargs = list_schedules.call_args
        assert kwargs["see_all"] is False
        assert kwargs["shared_ids"] == shared


class TestExternalLanes:
    async def test_google_meetings_are_normalised_from_the_provider_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.tool_result = _tool_result(
            {
                "items": [
                    _google_event(),
                    _google_event(
                        id="google-2",
                        iCalUID="google-2@google.com",
                        summary="Declined",
                        attendees=[
                            {
                                "email": "person@example.com",
                                "self": True,
                                "responseStatus": "declined",
                            },
                            {"email": "teammate@example.com"},
                        ],
                    ),
                    _google_event(id="google-3", iCalUID="google-3@google.com", status="cancelled"),
                    _google_event(
                        id="google-4",
                        iCalUID="google-4@google.com",
                        summary="Solo focus",
                        attendees=[
                            {
                                "email": "person@example.com",
                                "self": True,
                                "responseStatus": "accepted",
                            }
                        ],
                    ),
                    _google_event(
                        id="google-5",
                        iCalUID="google-5@google.com",
                        summary="All day",
                        start={"date": "2026-11-26"},
                        end={"date": "2026-11-27"},
                    ),
                ]
            }
        )
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert [event.title for event in read.events] == ["Planning"]
        assert read.events[0].source == "google_calendar"
        assert read.events[0].start == datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
        assert read.events[0].url == "https://calendar.google.com/event?eid=google-1"
        assert read.sources["google_calendar"].count == 1
        assert read.sources["google_calendar"].status == "loaded"
        assert "outlook_calendar" not in read.sources
        assert harness.calls[0]["tool_name"] == "GOOGLECALENDAR_EVENTS_LIST"
        assert harness.calls[0]["arguments"]["singleEvents"] is True

    async def test_outlook_meetings_are_normalised_from_the_graph_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-outlook")]
        harness.tool_result = _tool_result(
            {
                "items": [
                    _outlook_event(),
                    _outlook_event(
                        id="outlook-2",
                        iCalUId="outlook-2@outlook.com",
                        subject="Declined",
                        responseStatus={"response": "declined"},
                    ),
                    _outlook_event(
                        id="outlook-3", iCalUId="outlook-3@outlook.com", isCancelled=True
                    ),
                ]
            }
        )
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("outlook_calendar",)
        )
        assert [event.title for event in read.events] == ["Review"]
        assert read.events[0].source == "outlook_calendar"
        assert read.events[0].start == datetime(2026, 11, 26, 14, 0, tzinfo=UTC)
        assert read.events[0].url == "https://outlook.office.com/calendar/item/outlook-1"
        assert harness.calls[0]["tool_name"] == "OUTLOOK_LIST_EVENTS"

    async def test_a_wrapped_data_payload_is_unwrapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.tool_result = _tool_result({"data": {"items": [_google_event()]}})
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert len(read.events) == 1

    async def test_a_bare_list_under_data_is_used_as_is(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.tool_result = _tool_result({"data": [_google_event()]})
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert len(read.events) == 1

    async def test_a_payload_with_no_events_shape_is_unexpected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.tool_result = _tool_result({"foo": 1})
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert read.sources["google_calendar"].status == "unavailable"
        assert "unexpected shape" in (read.sources["google_calendar"].reason or "")

    async def test_a_scalar_payload_is_unexpected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.tool_result = _tool_result(42)
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert read.sources["google_calendar"].status == "unavailable"
        assert "unexpected shape" in (read.sources["google_calendar"].reason or "")

    async def test_a_nameless_reminder_takes_its_title_from_its_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = _agent()
        reminder = _trigger(
            one_shot=True,
            name=None,
            prompt="Call the bank about the card\nThen check the balance",
            next_fire_at=datetime(2026, 11, 27, 17, 0, tzinfo=UTC),
        )
        harness = _Service(monkeypatch, rows=[(reminder, agent)])
        read = await harness.service().list_events(_ctx(), window_start=_START, window_end=_END)
        assert [event.title for event in read.events] == ["Call the bank about the card"]

    async def test_a_bare_list_payload_is_used_as_is(self, monkeypatch: pytest.MonkeyPatch) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.tool_result = _tool_result([_google_event()])
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert len(read.events) == 1

    async def test_no_connection_leaves_the_lane_unavailable_not_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert read.events == []
        assert read.sources["google_calendar"].status == "unavailable"
        assert read.sources["google_calendar"].reason == "no connected account"
        assert harness.calls == []

    async def test_an_unusable_connection_leaves_the_lane_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.spec = None
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert read.sources["google_calendar"].status == "unavailable"
        assert read.sources["google_calendar"].reason == "the connected account is not usable"
        assert harness.calls == []

    async def test_a_verification_failure_leaves_the_lane_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        monkeypatch.setattr(
            calendar_events,
            "managed_connection_spec",
            AsyncMock(side_effect=RuntimeError("provider down")),
        )
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert read.sources["google_calendar"].status == "unavailable"
        assert "could not be verified" in (read.sources["google_calendar"].reason or "")

    async def test_a_tool_failure_leaves_the_lane_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.tool_error = RuntimeError("connection reset")
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert read.sources["google_calendar"].status == "unavailable"
        assert "could not be read" in (read.sources["google_calendar"].reason or "")

    async def test_a_tool_error_result_leaves_the_lane_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.tool_result = _tool_result({"error": "forbidden"}, is_error=True)
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert read.sources["google_calendar"].status == "unavailable"
        assert "refused the read" in (read.sources["google_calendar"].reason or "")

    async def test_an_unexpected_tool_shape_leaves_the_lane_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.tool_result = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="not json")], isError=False
        )
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert read.sources["google_calendar"].status == "unavailable"
        assert "unexpected shape" in (read.sources["google_calendar"].reason or "")

    async def test_a_tool_result_with_no_text_leaves_the_lane_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.tool_result = SimpleNamespace(content=[], isError=False)
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert read.sources["google_calendar"].status == "unavailable"

    async def test_a_caller_with_no_user_behind_the_request_has_no_lane(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        anonymous = AuthContext(user_id=None, organization_id=_ORG, role=OrgRoleName.OWNER.value)
        read = await harness.service().list_events(
            anonymous, window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert read.sources["google_calendar"].status == "unavailable"
        assert read.sources["google_calendar"].reason == "no connected account"
        assert harness.calls == []

    async def test_an_empty_listing_reports_the_lane_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [_connection("composio-googlecalendar")]
        harness.tool_result = _tool_result({"items": []})
        read = await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("google_calendar",)
        )
        assert read.sources["google_calendar"].status == "empty"
        assert read.sources["google_calendar"].count == 0

    async def test_only_the_requested_provider_is_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Service(monkeypatch)
        harness.connections = [
            _connection("composio-googlecalendar"),
            _connection("composio-outlook"),
        ]
        harness.tool_result = _tool_result({"items": []})
        await harness.service().list_events(
            _ctx(), window_start=_START, window_end=_END, providers=("outlook_calendar",)
        )
        assert [call["tool_name"] for call in harness.calls] == ["OUTLOOK_LIST_EVENTS"]
