"""Focused broker regressions; run in a disposable Linux container as root.

The fake CLI never authenticates or calls a model. The real-process tests need
the same UID/GID and process-signal capabilities as the native service.
"""

import asyncio
import base64
import contextlib
import errno
import json
import os
import resource
import signal
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

if sys.platform != "linux":
    raise unittest.SkipTest("The native terminal runtime is Linux-only")

import httpx
import native_runtime as runtime_module
import native_server as server
from fastapi.testclient import TestClient
from native_runtime import Home, Homes, Mode, NativeError, NativeRuntime, NativeSession, Owner
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

USER = "00000000-0000-4000-8000-000000000001"
OTHER_USER = "00000000-0000-4000-8000-000000000002"
ORG = "00000000-0000-4000-8000-000000000003"
OTHER_ORG = "00000000-0000-4000-8000-000000000004"
OWNER = Owner(ORG, USER)
OTHER_OWNER = Owner(ORG, OTHER_USER)
ORIGIN = "https://demo.example"
SERVICE_KEY = "test-service-key-" + "x" * 32
WS_PATH = "/native-subscriptions/terminal"


def bearer(expires: float) -> str:
    """An unsigned fixture; MockTransport supplies authentication, never a server."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expires}).encode()).rstrip(b"=").decode()
    return f"Bearer test.{payload}.fixture"


def inert_session(owner: Owner = OWNER) -> NativeSession:
    return NativeSession(owner, Home(Path("/unused"), 20001), "login", 120, 32)


class Backend:
    """Respond only to the two identity endpoints; forbid unexpected networking."""

    def __init__(self) -> None:
        self.me = {"id": USER, "is_active": True, "impersonation": None}
        self.permissions = {"organization_id": ORG, "user_id": USER}
        self.statuses: dict[str, int] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/v1/auth/me":
            body = self.me
        elif path == "/api/v1/me/permissions":
            body = self.permissions
        else:
            raise AssertionError(f"Unexpected backend path: {path}")
        return httpx.Response(self.statuses.get(path, 200), json=body)


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.backend = Backend()
        self.client = httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(self.backend))
        self.addAsyncCleanup(self.client.aclose)
        self.auth = server.Authenticator(self.client)

    async def denied(self, status: int, token: str = "Bearer fixture", org: str = ORG, *, minimum_ttl: int = 0) -> None:
        with self.assertRaises(NativeError) as caught:
            await self.auth.verify(token, org, minimum_ttl=minimum_ttl)
        self.assertEqual(caught.exception.status, status)

    async def test_verified_owner_and_both_forwarded_headers(self) -> None:
        lease = await self.auth.verify("Bearer fixture", ORG)
        self.assertEqual(lease.owner, OWNER)
        self.assertEqual(lease.bearer, "Bearer fixture")
        self.assertEqual(
            [request.url.path for request in self.backend.requests],
            ["/api/v1/auth/me", "/api/v1/me/permissions"],
        )
        for request in self.backend.requests:
            self.assertEqual(request.headers["authorization"], "Bearer fixture")
            self.assertEqual(request.headers["x-organization-id"], ORG)

    async def test_bad_local_admission_never_calls_backend(self) -> None:
        await self.denied(401, token="Basic fixture")
        await self.denied(403, org="not-a-uuid")
        self.assertEqual(self.backend.requests, [])

    async def test_inactive_impersonated_and_missing_impersonation_refused(self) -> None:
        valid = dict(self.backend.me)
        cases = [
            {**valid, "is_active": False},
            {**valid, "impersonation": {"session_id": "fixture"}},
            {"id": USER, "is_active": True},
        ]
        for data in cases:
            with self.subTest(data=data):
                self.backend.me = data
                await self.denied(403)

    async def test_org_and_user_must_both_match(self) -> None:
        for permissions in (
            {"organization_id": OTHER_ORG, "user_id": USER},
            {"organization_id": ORG, "user_id": OTHER_USER},
        ):
            with self.subTest(permissions=permissions):
                self.backend.permissions = permissions
                await self.denied(403)

    async def test_backend_refusals_fail_closed_at_either_step(self) -> None:
        for path in ("/api/v1/auth/me", "/api/v1/me/permissions"):
            for upstream, expected in ((401, 401), (403, 403), (404, 403), (429, 503), (500, 503)):
                with self.subTest(path=path, upstream=upstream):
                    self.backend.statuses = {path: upstream}
                    await self.denied(expected)

    async def test_http_expiry_margin_boundary(self) -> None:
        with patch("native_server.time.time", return_value=1000):
            await self.denied(401, token=bearer(1120), minimum_ttl=120)
            lease = await self.auth.verify(bearer(1121), ORG, minimum_ttl=120)
        self.assertEqual(lease.owner, OWNER)

    async def test_backend_network_failure_has_fixed_error(self) -> None:
        async def fail(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sensitive transport detail must not escape")

        async with httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(fail)) as client:
            with self.assertRaises(NativeError) as caught:
                await server.Authenticator(client).verify("Bearer fixture", ORG)
        self.assertEqual(caught.exception.code, "NATIVE_UNAVAILABLE")
        self.assertNotIn("sensitive", str(caught.exception))


class TicketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tickets = server.Tickets()
        self.lease = server.Lease(OWNER, "Bearer fixture")
        self.session = inert_session()

    def test_single_use_preserves_owner_and_generation(self) -> None:
        token = self.tickets.issue(self.lease, self.session)
        ticket = self.tickets.consume(token)
        self.assertEqual(ticket.lease, self.lease)
        self.assertEqual(ticket.generation, self.session.generation)
        with self.assertRaises(NativeError) as caught:
            self.tickets.consume(token)
        self.assertEqual(caught.exception.code, "NATIVE_TICKET_EXPIRED")

    def test_exact_expiry_boundary_without_sleep(self) -> None:
        with patch("native_server.time.monotonic", return_value=100):
            token = self.tickets.issue(self.lease, self.session)
        with patch("native_server.time.monotonic", return_value=145), self.assertRaises(NativeError):
            self.tickets.consume(token)
        self.assertNotIn(token, self.tickets.items)

    def test_outstanding_tickets_are_bounded_per_owner(self) -> None:
        issued = [self.tickets.issue(self.lease, self.session) for _ in range(6)]
        other = self.tickets.issue(server.Lease(OTHER_OWNER, "Bearer other"), inert_session(OTHER_OWNER))
        self.assertEqual(set(self.tickets.items), {*issued[-4:], other})


class StubRuntime:
    """The native process boundary is inert; HTTP, WS, auth and tickets stay real."""

    def __init__(self) -> None:
        self.session = inert_session()
        self.sessions = {OWNER: self.session}
        self.locks: dict[Owner, asyncio.Lock] = {}
        self.launch = AsyncMock(side_effect=self._launch)
        self.stop = AsyncMock()
        self.stop_locked = AsyncMock()
        self.sign_out = AsyncMock()
        self.close = AsyncMock()
        self.input = AsyncMock()
        self.resize = Mock()
        self.start = Mock(return_value=self.session)
        self.status = AsyncMock(
            return_value={
                "available": True,
                "running": True,
                "connected": False,
                "auth_method": None,
                "subscription_type": None,
                "session_mode": "login",
            }
        )

    def lock(self, owner: Owner) -> asyncio.Lock:
        return self.locks.setdefault(owner, asyncio.Lock())

    async def _launch(self, current: NativeSession) -> None:
        if current.queue is None:
            raise AssertionError("The transport must attach its output queue before launch")
        current.queue.put_nowait(b"ready")

    async def reap(self) -> None:
        await asyncio.Event().wait()


class AdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.backend = Backend()
        self.runtime = StubRuntime()
        self.token = bearer(int(time.time()) + 3600)
        self.headers = {
            "X-Native-Service-Key": SERVICE_KEY,
            "Authorization": self.token,
            "X-Organization-Id": ORG,
        }
        self.stack.enter_context(patch("native_server.Homes", return_value=object()))
        self.stack.enter_context(patch("native_server.NativeRuntime", return_value=self.runtime))
        config = server.Configuration(SERVICE_KEY, ORIGIN, backend_url="http://backend", state_dir=Path("/unused"))
        self.app = server.create_app(config, transport=httpx.MockTransport(self.backend))
        self.client = self.stack.enter_context(TestClient(self.app))
        self.service = self.app.state.native

    def issue(self) -> str:
        return self.service.tickets.issue(server.Lease(OWNER, self.token), self.runtime.session)

    def connect(self, ticket: str, *, origin: str = ORIGIN) -> WebSocketTestSession:
        return self.client.websocket_connect(
            WS_PATH,
            headers={"Origin": origin},
            subprotocols=["agenticos-native-v1", f"ticket.{ticket}"],
        )

    def refused(self, ticket: str, code: int, *, origin: str = ORIGIN) -> None:
        with self.assertRaises(WebSocketDisconnect) as caught, self.connect(ticket, origin=origin):
            self.fail("Unexpected WebSocket admission")
        self.assertEqual(caught.exception.code, code)

    def test_private_service_key_required_before_backend_calls(self) -> None:
        for key in ("", "incorrect"):
            headers = {**self.headers, "X-Native-Service-Key": key}
            self.assertEqual(self.client.get("/session", headers=headers).status_code, 403)
            self.assertEqual(self.client.post("/session", headers=headers, json={"action": "login"}).status_code, 403)
        self.assertEqual(self.backend.requests, [])
        response = self.client.get("/session", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_client_cannot_supply_owner_command_or_environment(self) -> None:
        for addition in ({"user_id": OTHER_USER}, {"command": "/bin/sh"}, {"env": {"HOME": "/root"}}):
            with self.subTest(addition=addition):
                response = self.client.post("/session", headers=self.headers, json={"action": "login", **addition})
                self.assertEqual(response.status_code, 400)
        self.runtime.start.assert_not_called()
        self.assertEqual(self.service.tickets.items, {})

    def test_origin_and_expired_ticket_refused_without_launch(self) -> None:
        self.refused(self.issue(), 4403, origin="https://other.example")
        expired = self.issue()
        vault = self.service.tickets
        vault.items[expired] = replace(vault.items[expired], expires=0)
        self.refused(expired, 4401)
        self.runtime.launch.assert_not_awaited()

    def test_missing_protocol_or_query_ticket_cannot_authenticate(self) -> None:
        ticket = self.issue()
        for path in (WS_PATH, f"{WS_PATH}?ticket={ticket}"):
            with (
                self.subTest(path=path),
                self.assertRaises(WebSocketDisconnect) as caught,
                self.client.websocket_connect(path, headers={"Origin": ORIGIN}, subprotocols=["agenticos-native-v1"]),
            ):
                self.fail("A ticket must arrive only in the required subprotocol")
            self.assertEqual(caught.exception.code, 4401)
        self.runtime.launch.assert_not_awaited()

    def test_real_upgrade_single_use_and_single_attachment(self) -> None:
        first = self.issue()
        with self.connect(first) as socket:
            self.assertEqual(socket.accepted_subprotocol, "agenticos-native-v1")
            self.assertEqual(socket.receive_bytes(), b"ready")
            self.refused(first, 4401)
            self.refused(self.issue(), 4403)
            self.assertEqual(self.runtime.launch.await_count, 1)

    def test_binary_input_closes_cleanly_without_key_error(self) -> None:
        with self.connect(self.issue()) as socket:
            self.assertEqual(socket.receive_bytes(), b"ready")
            socket.send_bytes(b"\x00\xff")
            with self.assertRaises(WebSocketDisconnect) as caught:
                socket.receive_text()
            self.assertEqual(caught.exception.code, 1008)
        self.runtime.input.assert_not_awaited()

    def test_revocation_owner_change_and_stale_generation(self) -> None:
        ticket = self.issue()
        self.backend.statuses = {"/api/v1/auth/me": 401}
        self.refused(ticket, 4401)
        self.backend.statuses = {"/api/v1/me/permissions": 403}
        self.refused(self.issue(), 4403)
        self.backend.statuses.clear()

        ticket = self.issue()
        self.backend.me["id"] = OTHER_USER
        self.backend.permissions["user_id"] = OTHER_USER
        self.refused(ticket, 4403)
        self.backend.me["id"] = USER
        self.backend.permissions["user_id"] = USER

        ticket = self.issue()
        self.runtime.sessions[OWNER] = inert_session()
        self.refused(ticket, 4403)
        self.runtime.launch.assert_not_awaited()

    def test_start_requires_an_explicitly_connected_native_identity(self) -> None:
        for account in (
            {"connected": False, "auth_method": None},
            {"connected": False, "auth_method": "api_key"},
            {"connected": "true", "auth_method": "claude.ai"},
        ):
            with self.subTest(account=account):
                self.runtime.status.return_value = account
                response = self.client.post("/session", headers=self.headers, json={"action": "start"})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {"code": "NATIVE_SIGN_IN_REQUIRED"})
        self.runtime.start.assert_not_called()

    def test_explicit_start_preserves_every_native_authentication_method(self) -> None:
        for method in ("claude.ai", "api_key", "console", "third_party"):
            with self.subTest(method=method):
                self.runtime.status.return_value = {"connected": True, "auth_method": method}
                response = self.client.post("/session", headers=self.headers, json={"action": "start"})
                self.assertEqual(response.status_code, 200)
                self.assertIsInstance(response.json()["ticket"], str)
                self.assertEqual(response.json()["expires_in"], 45)

    def test_native_onboarding_remains_available_without_sign_in(self) -> None:
        self.runtime.status.return_value = {"connected": False, "auth_method": None}
        response = self.client.post("/session", headers=self.headers, json={"action": "login"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json()["ticket"], str)


class QuotaTests(unittest.TestCase):
    def test_descriptor_exhaustion_refuses_scan_and_releases_owned_descriptors(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native-quota-") as directory:
            root = Path(directory)
            for index in range(100):
                (root / f"directory-{index}").mkdir()
            home = Home(root, os.getuid())
            before = set(os.listdir("/proc/self/fd"))
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            limit = max(int(value) for value in before) + 16
            if soft != resource.RLIM_INFINITY:
                limit = min(limit, soft)
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
                self.assertFalse(runtime_module.within_home_limit(home))
            finally:
                resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
            self.assertEqual(set(os.listdir("/proc/self/fd")), before)
            self.assertTrue(runtime_module.within_home_limit(home))


class ReaperTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_error_does_not_stop_other_owners_reaping(self) -> None:
        runtime = NativeRuntime(Mock(spec=Homes), cli_path="/unused")
        failing, succeeding = inert_session(), inert_session(OTHER_OWNER)
        for session in (failing, succeeding):
            session.created = time.monotonic() - 3601
            runtime.sessions[session.owner] = session
        completed = asyncio.Event()

        async def stop(session: NativeSession) -> None:
            if session is failing:
                session.stopping = True
                raise OSError(errno.EIO, "fixture cleanup failure")
            runtime.sessions.pop(session.owner)
            completed.set()

        with patch.object(runtime, "stop_locked", side_effect=stop):
            reaper = asyncio.create_task(runtime.reap())
            try:
                await asyncio.wait_for(completed.wait(), 5)
                self.assertFalse(reaper.done())
                self.assertIs(runtime.sessions.get(OWNER), failing)
                self.assertNotIn(OTHER_OWNER, runtime.sessions)
            finally:
                reaper.cancel()
                await asyncio.gather(reaper, return_exceptions=True)


FAKE_CLI = r"""import ctypes
import json
import os
import signal
import sys
import time
from pathlib import Path

home = Path.home()
arguments = sys.argv[1:]
if arguments == ["auth", "status", "--json"]:
    fixture = home / "fake-status.json"
    result = json.loads(fixture.read_text()) if fixture.exists() else {
        "loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"
    }
    print(json.dumps(result), flush=True)
    raise SystemExit(0)
if arguments == ["auth", "logout"]:
    raise SystemExit(0)
if arguments in (["test-block"], ["test-spam"]):
    (home / "fake-helper.pid").write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if arguments == ["test-spam"]:
        sys.stdout.write("x" * 100000)
        sys.stdout.flush()
    while True:
        time.sleep(60)

fields = {}
for line in Path("/proc/self/status").read_text().splitlines():
    if line.startswith(("NoNewPrivs:", "CapEff:", "CapPrm:", "CapInh:", "CapAmb:")):
        key, value = line.split(":", 1)
        fields[key] = value.strip()
print("NATIVE_READY " + json.dumps({
    "uid": os.getuid(), "gid": os.getgid(), "groups": os.getgroups(),
    "pid": os.getpid(), "sid": os.getsid(0), "tty": os.isatty(0),
    "tty_owner": os.fstat(0).st_uid, "argv": arguments, "privileges": fields,
    "inherited_secrets": any(name in os.environ for name in (
        "NATIVE_SESSION_SERVICE_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"
    )),
}), flush=True)
for raw in sys.stdin:
    command = raw.rstrip("\r\n")
    if command.startswith("ECHO "):
        print("NATIVE_ECHO " + command[5:], flush=True)
    elif command.startswith("PROBE "):
        try:
            with open(command[6:], "rb") as handle:
                handle.read(1)
        except PermissionError:
            result = "denied"
        except OSError as error:
            result = "error:" + str(error.errno)
        else:
            result = "readable"
        print("NATIVE_PROBE " + result, flush=True)
    elif command == "IGNORE_TERM":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        print("NATIVE_IGNORING", flush=True)
    elif command == "DETACH":
        reader, writer = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(reader)
            os.setsid()
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            libc = ctypes.CDLL(None)
            if libc.prctl(4, 0, 0, 0, 0) != 0 or libc.prctl(3, 0, 0, 0, 0) != 0:
                os._exit(2)
            null = os.open("/dev/null", os.O_RDWR)
            for descriptor in (0, 1, 2):
                os.dup2(null, descriptor)
            if null > 2:
                os.close(null)
            os.write(writer, b"1")
            os.close(writer)
            while True:
                time.sleep(60)
        os.close(writer)
        ready = os.read(reader, 1)
        os.close(reader)
        if ready != b"1":
            raise SystemExit(3)
        print("NATIVE_CHILD " + str(child), flush=True)
    elif command == "EXIT":
        break
"""


def live_process(pid: int) -> bool:
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
    except FileNotFoundError:
        return False
    state = next(line.split()[1] for line in text.splitlines() if line.startswith("State:"))
    return state not in {"Z", "X"}


@unittest.skipUnless(os.geteuid() == 0, "Real UID isolation tests require a disposable root container")
class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="native-tests-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o711)
        self.fake_cli = self.root / "fake-claude"
        self.fake_cli.write_text(f"#!{sys.executable}\n" + FAKE_CLI, encoding="utf-8")
        self.fake_cli.chmod(0o755)
        self.homes = Homes(self.root / "state")
        self.runtime = NativeRuntime(self.homes, cli_path=str(self.fake_cli), maximum=4)
        self.pidfds: list[int] = []
        self.buffers: dict[str, bytearray] = {}

    async def asyncTearDown(self) -> None:
        # Retained pidfds target only processes created by this fixture, even if
        # a regression made them disappear from the runtime registry.
        for descriptor in self.pidfds:
            with contextlib.suppress(ProcessLookupError):
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        try:
            await asyncio.wait_for(self.runtime.close(), 8)
        finally:
            for descriptor in self.pidfds:
                os.close(descriptor)
            self.temporary.cleanup()

    def retain_pid(self, pid: int) -> None:
        with contextlib.suppress(ProcessLookupError):
            self.pidfds.append(os.pidfd_open(pid))

    async def line(self, session: NativeSession, prefix: bytes) -> bytes:
        if session.queue is None:
            self.fail("Session output was not attached")
        buffer = self.buffers.setdefault(session.generation, bytearray())
        async with asyncio.timeout(5):
            while True:
                while b"\n" in buffer:
                    index = buffer.index(b"\n")
                    value = bytes(buffer[:index]).rstrip(b"\r")
                    del buffer[: index + 1]
                    if value.startswith(prefix):
                        return value[len(prefix) :]
                chunk = await session.queue.get()
                if chunk is None:
                    self.fail(f"The fake CLI exited before producing {prefix!r}")
                buffer.extend(chunk)

    async def launch(self, mode: Mode = "session") -> tuple[NativeSession, dict[str, object]]:
        session = self.runtime.start(OWNER, mode, 120, 32)
        session.attachment = "fixture"
        session.queue = asyncio.Queue(maxsize=64)
        await asyncio.wait_for(self.runtime.launch(session), 5)
        if session.process is None:
            self.fail("The native process did not launch")
        self.retain_pid(session.process.pid)
        metadata = json.loads(await self.line(session, b"NATIVE_READY "))
        return session, metadata

    def owned_file(self, home: Home, name: str, value: str) -> Path:
        path = home.path / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        os.chown(path, home.uid, home.uid)
        return path

    async def wait_file(self, path: Path) -> str:
        async with asyncio.timeout(5):
            while True:
                try:
                    value = path.read_text(encoding="ascii")
                    if value:
                        return value
                except FileNotFoundError:
                    pass
                await asyncio.sleep(0.01)

    async def test_actual_pty_drops_identity_and_inherited_credentials(self) -> None:
        fixture_environment = {
            "NATIVE_SESSION_SERVICE_KEY": "test-only-service-secret",
            "ANTHROPIC_API_KEY": "test-only-api-secret",
            "CLAUDE_CODE_OAUTH_TOKEN": "test-only-oauth-secret",
        }
        with patch.dict(os.environ, fixture_environment):
            session, metadata = await self.launch()
        self.assertEqual(metadata["uid"], session.home.uid)
        self.assertEqual(metadata["gid"], session.home.uid)
        self.assertEqual(metadata["groups"], [])
        self.assertEqual(metadata["pid"], metadata["sid"])
        self.assertTrue(metadata["tty"])
        self.assertEqual(metadata["tty_owner"], session.home.uid)
        self.assertFalse(metadata["inherited_secrets"])
        self.assertEqual(metadata["privileges"]["NoNewPrivs"], "1")
        for field in ("CapEff", "CapPrm", "CapInh", "CapAmb"):
            self.assertEqual(int(metadata["privileges"][field], 16), 0)
        await self.runtime.input(session, "ECHO native-\u03bb\n")
        self.assertEqual(await self.line(session, b"NATIVE_ECHO "), "native-\u03bb".encode())

    async def test_login_preserves_unmodified_native_onboarding_without_arguments(self) -> None:
        _session, metadata = await self.launch("login")
        self.assertEqual(metadata["argv"], [])

    async def test_other_home_and_service_metadata_are_unreadable(self) -> None:
        session, _metadata = await self.launch()
        other = self.homes.get(OTHER_OWNER, create=True)
        if other is None:
            self.fail("The second test home was not created")
        own = self.owned_file(session.home, "own.txt", "own test fixture")
        private = self.owned_file(other, "private.txt", "other user's test fixture")
        metadata = self.homes.metadata / "service-fixture"
        metadata.write_text("test-only service fixture", encoding="utf-8")
        metadata.chmod(0o600)
        for path, expected in ((own, b"readable"), (private, b"denied"), (metadata, b"denied")):
            with self.subTest(path=path):
                await self.runtime.input(session, f"PROBE {path}\n")
                self.assertEqual(await self.line(session, b"NATIVE_PROBE "), expected)

    async def test_native_status_returns_only_approved_metadata(self) -> None:
        home = self.homes.get(OWNER, create=True)
        if home is None:
            self.fail("The test home was not created")
        self.owned_file(
            home,
            "fake-status.json",
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "subscriptionType": "max",
                    "email": "private@example.invalid",
                    "access_token": "test-only-do-not-return",
                }
            ),
        )
        status = await asyncio.wait_for(self.runtime.status(OWNER), 5)
        self.assertTrue(status["connected"])
        self.assertEqual(status["auth_method"], "claude.ai")
        self.assertEqual(status["subscription_type"], "max")
        serialized = json.dumps(status)
        self.assertNotIn("private@example.invalid", serialized)
        self.assertNotIn("test-only-do-not-return", serialized)
        self.assertNotIn("access_token", status)

    async def test_partial_nonblocking_input_is_delivered_completely(self) -> None:
        session, _metadata = await self.launch()
        descriptor = session.master
        original_write = os.write
        blocked_once = False

        def write_piece(fd: int, data: bytes) -> int:
            nonlocal blocked_once
            if fd == descriptor:
                if not blocked_once:
                    blocked_once = True
                    raise BlockingIOError(errno.EAGAIN, "fixture backpressure")
                return original_write(fd, data[:3])
            return original_write(fd, data)

        with patch("native_runtime.os.write", side_effect=write_piece):
            await asyncio.wait_for(self.runtime.input(session, "ECHO complete-paste\n"), 6)
        self.assertEqual(await self.line(session, b"NATIVE_ECHO "), b"complete-paste")

    async def test_detached_nondumpable_child_is_reaped(self) -> None:
        session, _metadata = await self.launch()
        await self.runtime.input(session, "DETACH\n")
        child = int(await self.line(session, b"NATIVE_CHILD "))
        self.retain_pid(child)
        self.assertTrue(live_process(child))
        await asyncio.wait_for(self.runtime.stop(session), 8)
        self.assertFalse(live_process(child))
        self.assertNotIn(OWNER, self.runtime.sessions)
        self.assertIsNone(session.master)

    async def test_cancelling_stop_still_reaps_the_process(self) -> None:
        session, _metadata = await self.launch()
        await self.runtime.input(session, "IGNORE_TERM\n")
        await self.line(session, b"NATIVE_IGNORING")
        term_sent = asyncio.Event()
        original = runtime_module.signal_native_user

        def observed(uid: int, sig: signal.Signals) -> int:
            result = original(uid, sig)
            if uid == session.home.uid and sig == signal.SIGTERM:
                term_sent.set()
            return result

        with patch("native_runtime.signal_native_user", side_effect=observed):
            stopping = asyncio.create_task(self.runtime.stop(session))
            await asyncio.wait_for(term_sent.wait(), 3)
            stopping.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(stopping, 8)
        self.assertIsNotNone(session.process)
        self.assertFalse(live_process(session.process.pid))
        self.assertNotIn(OWNER, self.runtime.sessions)
        self.assertIsNone(session.master)

    async def test_cancelling_helper_reaps_it_before_return(self) -> None:
        home = self.homes.get(OWNER, create=True)
        if home is None:
            self.fail("The test home was not created")
        task = asyncio.create_task(self.runtime.command(home, "test-block"))
        child = int(await self.wait_file(home.path / "fake-helper.pid"))
        self.retain_pid(child)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 8)
        self.assertFalse(live_process(child))

    async def test_helper_output_limit_terminates_without_waiting_for_timeout(self) -> None:
        home = self.homes.get(OWNER, create=True)
        if home is None:
            self.fail("The test home was not created")
        with self.assertRaises(NativeError) as caught:
            await asyncio.wait_for(self.runtime.command(home, "test-spam"), 5)
        self.assertEqual(caught.exception.code, "NATIVE_SESSION_FAILED")
        child = int((home.path / "fake-helper.pid").read_text(encoding="ascii"))
        self.assertFalse(live_process(child))


if __name__ == "__main__":
    unittest.main(verbosity=2)
