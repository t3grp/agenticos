"""Authenticated native terminal transport; never a provider API or credential proxy."""

import asyncio
import base64
import contextlib
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from native_runtime import CLI_PATH, Homes, NativeError, NativeRuntime, NativeSession, Owner
from pydantic import BaseModel, ConfigDict, Field, ValidationError


@dataclass(frozen=True)
class Configuration:
    service_key: str = field(repr=False)
    site_origin: str
    backend_url: str = "http://agenticos-api:8000"
    state_dir: Path = Path("/state")
    cli_path: str = CLI_PATH
    maximum_sessions: int = 4

    @classmethod
    def from_environment(cls) -> "Configuration":
        key = os.environ.get("NATIVE_SESSION_SERVICE_KEY", "")
        origin = os.environ.get("NATIVE_SITE_ORIGIN", "").rstrip("/")
        if len(key) < 32 or not origin.startswith("https://"):
            raise RuntimeError("Native service requires its service key and HTTPS site origin")
        return cls(
            key,
            origin,
            os.environ.get("NATIVE_BACKEND_URL", "http://agenticos-api:8000").rstrip("/"),
            Path(os.environ.get("NATIVE_STATE_DIR", "/state")),
        )


@dataclass(frozen=True)
class Lease:
    owner: Owner
    bearer: str = field(repr=False)


class Authenticator:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def verify(self, bearer: str, organization_id: str, *, minimum_ttl: int = 0) -> Lease:
        try:
            organization_id = str(UUID(organization_id))
        except (ValueError, TypeError, AttributeError):
            raise NativeError("NATIVE_ACCESS_DENIED", 403) from None
        if not bearer.startswith("Bearer ") or len(bearer) > 16384:
            raise NativeError("NATIVE_AUTH_REQUIRED", 401)
        headers = {"Authorization": bearer, "X-Organization-Id": organization_id}
        try:
            me = await self.client.get("/api/v1/auth/me", headers=headers)
            self.require_success(me)
            data = me.json()
            user_id = str(UUID(data["id"]))
            if data.get("is_active") is not True or "impersonation" not in data or data["impersonation"] is not None:
                raise NativeError("NATIVE_ACCESS_DENIED", 403)
            membership = await self.client.get("/api/v1/me/permissions", headers=headers)
            self.require_success(membership)
            permissions = membership.json()
            if permissions.get("organization_id") != organization_id or permissions.get("user_id") != user_id:
                raise NativeError("NATIVE_ACCESS_DENIED", 403)
            if minimum_ttl:
                # The backend already authenticated this exact token. Its verified
                # expiry is used only to tighten admission and trigger cookie refresh.
                part = bearer.removeprefix("Bearer ").split(".")[1]
                claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
                expires = claims.get("exp")
                if type(expires) not in {int, float} or expires - time.time() <= minimum_ttl:
                    raise NativeError("NATIVE_AUTH_REQUIRED", 401)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            raise NativeError("NATIVE_UNAVAILABLE", 503) from None
        return Lease(Owner(organization_id, user_id), bearer)

    @staticmethod
    def require_success(response: httpx.Response) -> None:
        if response.status_code == 401:
            raise NativeError("NATIVE_AUTH_REQUIRED", 401)
        if response.status_code in {403, 404}:
            raise NativeError("NATIVE_ACCESS_DENIED", 403)
        if response.status_code != 200:
            raise NativeError("NATIVE_UNAVAILABLE", 503)


@dataclass(frozen=True)
class Ticket:
    lease: Lease
    generation: str
    expires: float


class Tickets:
    def __init__(self) -> None:
        self.items: dict[str, Ticket] = {}

    def issue(self, lease: Lease, session: NativeSession) -> str:
        now = time.monotonic()
        self.items = {key: value for key, value in self.items.items() if value.expires > now}
        # Bound outstanding tickets from repeated clicks/polls without extending a PTY's life.
        owned = [key for key, value in self.items.items() if value.lease.owner == lease.owner]
        for key in owned[:-3]:
            self.items.pop(key, None)
        ticket = secrets.token_urlsafe(32)
        self.items[ticket] = Ticket(lease, session.generation, now + 45)
        return ticket

    def consume(self, token: str) -> Ticket:
        value = self.items.pop(token, None)
        if value is None or value.expires <= time.monotonic():
            raise NativeError("NATIVE_TICKET_EXPIRED", 401)
        return value


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["login", "start", "stop", "sign_out", "ticket"]
    cols: int = Field(default=120, ge=40, le=240, strict=True)
    rows: int = Field(default=32, ge=10, le=80, strict=True)


@dataclass
class Attachment:
    lease: Lease
    identifier: str = field(default_factory=lambda: uuid4().hex)
    revoked: bool = False


class NativeService:
    def __init__(self, config: Configuration, client: httpx.AsyncClient) -> None:
        self.config = config
        self.auth = Authenticator(client)
        self.runtime = NativeRuntime(Homes(config.state_dir), cli_path=config.cli_path, maximum=config.maximum_sessions)
        self.tickets = Tickets()

    async def admission(self, request: Request) -> Lease:
        supplied = request.headers.get("x-native-service-key", "")
        if not secrets.compare_digest(supplied, self.config.service_key):
            raise NativeError("NATIVE_ACCESS_DENIED", 403)
        return await self.auth.verify(
            request.headers.get("authorization", ""),
            request.headers.get("x-organization-id", ""),
            minimum_ttl=120,
        )

    async def control(self, lease: Lease, command: Control) -> dict[str, object]:
        if command.action == "start":
            status = await self.runtime.status(lease.owner)
            if status["connected"] is not True:
                raise NativeError("NATIVE_SIGN_IN_REQUIRED")
        result: dict[str, object] = {}
        async with self.runtime.lock(lease.owner):
            session = self.runtime.sessions.get(lease.owner)
            if session is not None and not session.running:
                await self.runtime.stop_locked(session)
                session = None
            if command.action in {"login", "start"}:
                mode = "login" if command.action == "login" else "session"
                session = self.runtime.start(lease.owner, mode, command.cols, command.rows)
            elif command.action == "stop" and session is not None:
                await self.runtime.stop_locked(session)
            elif command.action == "sign_out":
                await self.runtime.sign_out(lease.owner)
            elif command.action == "ticket" and session is None:
                raise NativeError("NATIVE_SESSION_ENDED")
            if command.action in {"login", "start", "ticket"} and session is not None:
                result.update(ticket=self.tickets.issue(lease, session), expires_in=45)
        result["status"] = await self.runtime.status(lease.owner)
        return result

    def ticket_session(self, ticket: Ticket) -> NativeSession:
        session = self.runtime.sessions.get(ticket.lease.owner)
        if session is None or session.generation != ticket.generation or not session.running:
            raise NativeError("NATIVE_SESSION_ENDED")
        return session

    async def terminal(self, socket: WebSocket) -> None:
        if socket.headers.get("origin") != self.config.site_origin:
            await socket.close(code=4403)
            return
        protocols = [value.strip() for value in socket.headers.get("sec-websocket-protocol", "").split(",")]
        supplied = [value[7:] for value in protocols if value.startswith("ticket.")]
        if len(protocols) != 2 or "agenticos-native-v1" not in protocols or len(supplied) != 1:
            await socket.close(code=4401)
            return
        try:
            ticket = self.tickets.consume(supplied[0])
            lease = await self.auth.verify(ticket.lease.bearer, ticket.lease.owner.organization_id)
            if lease.owner != ticket.lease.owner:
                raise NativeError("NATIVE_ACCESS_DENIED", 403)
            session = self.ticket_session(ticket)
            attachment = Attachment(lease)
            async with self.runtime.lock(lease.owner):
                if self.runtime.sessions.get(lease.owner) is not session:
                    raise NativeError("NATIVE_SESSION_ENDED")
                if session.attachment is not None:
                    raise NativeError("NATIVE_SESSION_IN_USE")
                session.attachment = attachment.identifier
                queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=32)
                session.queue = queue
                await socket.accept(subprotocol="agenticos-native-v1")
                if session.replay:
                    await socket.send_bytes(bytes(session.replay))
                await self.runtime.launch(session)
        except NativeError as error:
            await socket.close(code=4401 if error.status == 401 else 4403)
            return
        except (OSError, RuntimeError):
            with contextlib.suppress(RuntimeError):
                await socket.close(code=1011)
            if "session" in locals():
                await self.runtime.stop(session)
            return

        tasks = [
            asyncio.create_task(self.send_output(socket, session, queue)),
            asyncio.create_task(self.receive_input(socket, session, attachment)),
            asyncio.create_task(self.revalidate(socket, session, attachment)),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, NativeError, ValueError, OSError, RuntimeError):
            with contextlib.suppress(RuntimeError):
                await socket.close(code=1008)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if attachment.revoked:
                await self.runtime.stop(session)
            if session.attachment == attachment.identifier:
                session.attachment = None
                session.queue = None
                session.detached = time.monotonic()
            with contextlib.suppress(RuntimeError):
                await socket.close()

    async def send_output(self, socket: WebSocket, session: NativeSession, queue: asyncio.Queue[bytes | None]) -> None:
        while True:
            chunk = await queue.get()
            if chunk is None:
                code = session.process.returncode if session.process is not None else 0
                await socket.send_json({"type": "exit", "code": code if code is not None else 0})
                return
            await socket.send_bytes(chunk)

    async def receive_input(self, socket: WebSocket, session: NativeSession, attachment: Attachment) -> None:
        while True:
            event = await socket.receive()
            if event.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(event.get("code", 1000), event.get("reason", ""))
            message = event.get("text")
            if (
                event.get("type") != "websocket.receive"
                or not isinstance(message, str)
                or event.get("bytes") is not None
            ):
                raise NativeError("NATIVE_BAD_REQUEST", 400)
            if len(message.encode()) > 16384:
                raise NativeError("NATIVE_BAD_REQUEST", 400)
            try:
                frame = json.loads(message)
            except ValueError:
                raise NativeError("NATIVE_BAD_REQUEST", 400) from None
            if not isinstance(frame, dict) or session.attachment != attachment.identifier:
                raise NativeError("NATIVE_ACCESS_DENIED", 403)
            if self.runtime.sessions.get(session.owner) is not session or not session.running:
                raise NativeError("NATIVE_SESSION_ENDED")
            kind = frame.get("type")
            if kind == "input" and isinstance(frame.get("data"), str) and set(frame) == {"type", "data"}:
                await self.runtime.input(session, frame["data"])
            elif kind == "resize" and set(frame) == {"type", "cols", "rows"}:
                cols, rows = frame.get("cols"), frame.get("rows")
                if type(cols) is not int or type(rows) is not int or not 40 <= cols <= 240 or not 10 <= rows <= 80:
                    raise NativeError("NATIVE_BAD_REQUEST", 400)
                self.runtime.resize(session, cols, rows)
            elif kind == "reauth" and isinstance(frame.get("ticket"), str) and set(frame) == {"type", "ticket"}:
                ticket = self.tickets.consume(frame["ticket"])
                if ticket.lease.owner != session.owner or self.ticket_session(ticket) is not session:
                    raise NativeError("NATIVE_ACCESS_DENIED", 403)
                fresh = await self.auth.verify(ticket.lease.bearer, session.owner.organization_id)
                if fresh.owner != session.owner or session.attachment != attachment.identifier:
                    raise NativeError("NATIVE_ACCESS_DENIED", 403)
                attachment.lease = fresh
                await socket.send_json({"type": "authorized"})
            else:
                raise NativeError("NATIVE_BAD_REQUEST", 400)

    async def revalidate(self, socket: WebSocket, session: NativeSession, attachment: Attachment) -> None:
        while True:
            await asyncio.sleep(30)
            lease = attachment.lease
            try:
                current = await self.auth.verify(lease.bearer, lease.owner.organization_id)
                if current.owner != session.owner:
                    raise NativeError("NATIVE_ACCESS_DENIED", 403)
            except NativeError:
                # A renewal or replacement that won the race owns its own authorization.
                if attachment.lease is not lease or session.attachment != attachment.identifier:
                    continue
                attachment.revoked = True
                await self.runtime.stop(session)
                await socket.close(code=4401)
                return


def create_app(config: Configuration | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        selected = config or Configuration.from_environment()
        async with httpx.AsyncClient(
            base_url=selected.backend_url,
            timeout=10,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        ) as client:
            service = NativeService(selected, client)
            application.state.native = service
            reaper = asyncio.create_task(service.runtime.reap())
            try:
                yield
            finally:
                reaper.cancel()
                await asyncio.gather(reaper, return_exceptions=True)
                await service.runtime.close()

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @application.exception_handler(NativeError)
    async def native_error(_request: Request, error: NativeError) -> JSONResponse:
        return JSONResponse({"code": error.code}, status_code=error.status, headers={"Cache-Control": "no-store"})

    @application.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @application.get("/session")
    async def read_session(request: Request) -> JSONResponse:
        service: NativeService = request.app.state.native
        lease = await service.admission(request)
        result = await service.runtime.status(lease.owner)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @application.post("/session")
    async def control_session(request: Request) -> JSONResponse:
        service: NativeService = request.app.state.native
        lease = await service.admission(request)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                raise NativeError("NATIVE_BAD_REQUEST", 413)
        try:
            command = Control.model_validate_json(body)
        except ValidationError:
            raise NativeError("NATIVE_BAD_REQUEST", 400) from None
        result = await service.control(lease, command)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @application.websocket("/native-subscriptions/terminal")
    async def native_terminal(socket: WebSocket) -> None:
        service: NativeService = socket.app.state.native
        await service.terminal(socket)

    return application


app = create_app()
