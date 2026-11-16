"""Cached pictures and concurrent event teardown cannot resurrect protected page output."""

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.agents.browser_events import BrowserEvent
from app.agents.capabilities.browser_use._protected import BrowserOutputGuard
from app.agents.capabilities.browser_use._toolset import _live
from app.services import live_browse
from tests.test_browser_credentials import PASSWORD
from tests.test_live_browse import _Pipe, _Store

pytestmark = [pytest.mark.anyio, pytest.mark.security]
ORIGIN = "https://portal.example.test"


class ProtectedPipe(_Pipe):
    async def execute(self) -> None:
        for name, args in self.ops:
            if name == "delete":
                for key in args:
                    self.store.values.pop(key, None)
                    self.store.lists.pop(key, None)
        await super().execute()


class ProtectedStore(_Store):
    def pipeline(self, *, transaction: bool = False):
        assert transaction
        return ProtectedPipe(self)


def protected_event() -> BrowserEvent:
    return BrowserEvent(
        kind="browser_protected",
        call_id="call",
        step=0,
        url=ORIGIN,
        title="Protected browser session",
    )


async def test_protection_removes_old_frame_location_and_trace_before_announcing(
    monkeypatch,
) -> None:
    store = ProtectedStore()
    monkeypatch.setattr(live_browse, "_redis", SimpleNamespace(raw=store))
    frame, location, trace = (
        live_browse._key("run", "call", part) for part in ("frame", "location", "trace")
    )
    store.values.update({frame: PASSWORD, location: PASSWORD})
    store.lists[trace] = [PASSWORD]
    event = protected_event()
    await live_browse.protect_view("run", "call", event)
    assert frame not in store.values
    assert store.values[location] == event.model_dump_json()
    assert store.lists[trace] == [event.model_dump_json()]
    assert store.published == [(live_browse._key("run", "call", "events"), event.model_dump_json())]
    assert PASSWORD not in repr(store.values) + repr(store.lists) + repr(store.published)
    assert live_browse.credential_redis().raw is store


@pytest.mark.parametrize("failure", ["unconfigured", "wrong_event", "image", "redis_error"])
async def test_protection_failure_is_not_swallowed_or_logged_as_success(
    monkeypatch, caplog, failure
) -> None:
    store = ProtectedStore()
    client = SimpleNamespace(raw=store)
    monkeypatch.setattr(live_browse, "_redis", None if failure == "unconfigured" else client)
    event = protected_event()
    if failure == "wrong_event":
        event = event.model_copy(update={"kind": "browser_frame"})
    elif failure == "image":
        event = event.model_copy(update={"image": base64.b64encode(PASSWORD.encode()).decode()})
    elif failure == "redis_error":
        pipe = ProtectedPipe(store)
        pipe.execute = AsyncMock(side_effect=RedisError(PASSWORD))
        monkeypatch.setattr(store, "pipeline", lambda **kwargs: pipe)
    with pytest.raises((RuntimeError, RedisError)):
        await live_browse.protect_view("run", "call", event)
    assert PASSWORD not in caplog.text
    assert store.published == []


async def make_sink(monkeypatch, guard: BrowserOutputGuard):
    deps = SimpleNamespace(
        browser_events=AsyncMock(),
        run_id=uuid4(),
        organization_id=uuid4(),
        agent_id=uuid4(),
        user_id=str(uuid4()),
        audience=None,
    )
    monkeypatch.setattr(live_browse, "available", lambda: True)
    monkeypatch.setattr(live_browse, "open_view", AsyncMock())
    monkeypatch.setattr(live_browse, "has_viewers", AsyncMock(return_value=False))
    monkeypatch.setattr(live_browse, "publish", AsyncMock())
    monkeypatch.setattr(live_browse, "protect_view", AsyncMock())
    sink, _, _ = await _live(deps, "call", "Synthetic private task", guard)
    return sink, deps.browser_events


async def test_cached_last_picture_is_cleared_and_never_replayed_on_teardown(monkeypatch) -> None:
    guard = BrowserOutputGuard()
    sink, chat = await make_sink(monkeypatch, guard)
    frame = BrowserEvent(
        kind="browser_frame",
        call_id="call",
        step=0,
        image=base64.b64encode(PASSWORD.encode()).decode(),
        url=ORIGIN + "/" + PASSWORD,
        title=PASSWORD,
    )
    assert await sink(frame) is False
    live_browse.publish.assert_not_awaited()
    guard.protect(ORIGIN)
    await sink(protected_event())
    await sink(frame)
    await sink(BrowserEvent(kind="browser_finished", call_id="call", step=1, reason=PASSWORD))
    live_browse.protect_view.assert_awaited_once()
    for call in live_browse.publish.await_args_list + chat.await_args_list:
        event = call.args[-1]
        assert event.image is None
        assert PASSWORD not in event.model_dump_json()
        assert event.url == ORIGIN
    assert live_browse.publish.await_count == 2


async def test_pending_publication_settles_before_clear_and_late_frames_remain_sanitized(
    monkeypatch,
) -> None:
    guard = BrowserOutputGuard()
    sink, _ = await make_sink(monkeypatch, guard)
    started, release = asyncio.Event(), asyncio.Event()
    order = []

    async def publish(*args):
        order.append("earlier frame")
        started.set()
        await release.wait()

    async def protect(*args):
        order.append("clear cached output")

    live_browse.publish.side_effect = publish
    live_browse.protect_view.side_effect = protect
    live_browse.has_viewers.return_value = True
    old = asyncio.create_task(
        sink(BrowserEvent(kind="browser_frame", call_id="call", step=0, image="c3ludGhldGlj"))
    )
    await started.wait()
    guard.protect(ORIGIN)
    protected = asyncio.create_task(sink(protected_event()))
    await asyncio.sleep(0)
    assert order == ["earlier frame"] and not protected.done()
    release.set()
    await old
    await protected
    assert order == ["earlier frame", "clear cached output"]
