"""Personal native CLI processes. Credentials belong only to the CLI's own home."""

import asyncio
import contextlib
import ctypes
import fcntl
import hashlib
import json
import os
import resource
import signal
import sqlite3
import stat
import struct
import termios
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

CLI_PATH = "/usr/local/bin/claude"
MAX_REPLAY_BYTES = 65536
MAX_HOME_BYTES = 512 * 1024 * 1024
MAX_HOME_ENTRIES = 20000
MAX_PENDING_HOME_DIRECTORIES = 256
Mode = Literal["login", "session"]


class NativeError(Exception):
    """A fixed public error code; never includes process output or credentials."""

    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Owner:
    organization_id: str
    user_id: str

    @property
    def key(self) -> str:
        return hashlib.sha256(f"{self.organization_id}:{self.user_id}".encode()).hexdigest()


@dataclass(frozen=True)
class Home:
    path: Path
    uid: int


class Homes:
    """A root-owned registry assigns permanent, non-recycled operating-system IDs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.users = root / "users"
        self.metadata = root / "metadata"
        root.mkdir(mode=0o711, parents=True, exist_ok=True)
        self.users.mkdir(mode=0o711, exist_ok=True)
        self.metadata.mkdir(mode=0o700, exist_ok=True)
        for path, mode in ((root, 0o711), (self.users, 0o711), (self.metadata, 0o700)):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
                raise RuntimeError("Invalid native state directory")
            path.chmod(mode)
        self.db = sqlite3.connect(self.metadata / "owners.sqlite3")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS owners (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_key TEXT UNIQUE NOT NULL)"
        )
        self.db.commit()

    def get(self, owner: Owner, *, create: bool = False) -> Home | None:
        row = self.db.execute("SELECT id FROM owners WHERE owner_key = ?", (owner.key,)).fetchone()
        if row is None:
            if not create:
                return None
            cursor = self.db.execute("INSERT INTO owners(owner_key) VALUES (?)", (owner.key,))
            self.db.commit()
            row = (cursor.lastrowid,)
        uid = 20000 + int(row[0])
        if uid >= 2147483647:
            raise NativeError("NATIVE_SESSION_LIMIT", 429)
        path = self.users / owner.key
        if not path.exists():
            if not create:
                return None
            path.mkdir(mode=0o700)
            os.chown(path, uid, uid)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise NativeError("NATIVE_ACCESS_DENIED", 403)
        return Home(path, uid)

    def close(self) -> None:
        self.db.close()


def native_environment(home: Home) -> dict[str, str]:
    """Explicit allowlist: never inherit service keys, API keys or OAuth tokens."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home.path),
        "CLAUDE_CONFIG_DIR": str(home.path / ".claude"),
        "USER": f"native{home.uid}",
        "LOGNAME": f"native{home.uid}",
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "LANG": "C.UTF-8",
        "DISABLE_AUTOUPDATER": "1",
    }


def process_limits(*, terminal: bool = False) -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (96, 96))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (3600, 3600))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot enforce native process privileges")
    if terminal:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def terminal_limits() -> None:
    process_limits(terminal=True)


def within_home_limit(home: Home) -> bool:
    """Count allocated blocks without following symlinks; an incomplete scan refuses use."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    pending: list[int] = []
    entries = 0
    size = 0
    try:
        pending.append(os.open(home.path, flags))
        while pending:
            descriptor = pending.pop()
            try:
                with os.scandir(descriptor) as scan:
                    for entry in scan:
                        entries += 1
                        if entries > MAX_HOME_ENTRIES:
                            return False
                        try:
                            info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        size += info.st_blocks * 512
                        if size > MAX_HOME_BYTES:
                            return False
                        if stat.S_ISDIR(info.st_mode):
                            if len(pending) >= MAX_PENDING_HOME_DIRECTORIES:
                                return False
                            try:
                                pending.append(os.open(entry.name, flags, dir_fd=descriptor))
                            except (FileNotFoundError, NotADirectoryError):
                                continue
            finally:
                os.close(descriptor)
        return True
    except OSError:
        # ELOOP, descriptor exhaustion and filesystem errors cannot establish that
        # this home is within quota. The caller refuses or stops only this session.
        return False
    finally:
        for descriptor in pending:
            os.close(descriptor)


def signal_native_user(uid: int, sig: signal.Signals) -> int:
    """Reap detached descendants too, with pidfds guarding against PID reuse."""
    if not 20000 < uid < 2147483647:
        raise RuntimeError("Invalid native process identity")
    live = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        descriptor = None
        try:
            descriptor = os.pidfd_open(int(entry.name))
            # Non-dumpable processes have root-owned /proc directories even after
            # dropping UID. Read the kernel's actual UID fields, never that inode.
            status_text = (entry / "status").read_text(encoding="ascii")
            uid_line = next(line for line in status_text.splitlines() if line.startswith("Uid:"))
            state_line = next(line for line in status_text.splitlines() if line.startswith("State:"))
            if int(uid_line.split()[1]) == uid and state_line.split()[1] not in {"Z", "X"}:
                signal.pidfd_send_signal(descriptor, sig)
                live += 1
        except (ProcessLookupError, FileNotFoundError):
            continue
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return live


@dataclass
class NativeSession:
    owner: Owner
    home: Home
    mode: Mode
    cols: int
    rows: int
    generation: str = field(default_factory=lambda: uuid4().hex)
    created: float = field(default_factory=time.monotonic)
    last_input: float = field(default_factory=time.monotonic)
    detached: float = field(default_factory=time.monotonic)
    process: asyncio.subprocess.Process | None = None
    master: int | None = None
    attachment: str | None = None
    queue: asyncio.Queue[bytes | None] | None = None
    replay: bytearray = field(default_factory=bytearray, repr=False)
    waiter: asyncio.Task[None] | None = None
    overflow: bool = False
    stopping: bool = False

    @property
    def running(self) -> bool:
        return not self.stopping and (self.process is None or self.process.returncode is None)


class NativeRuntime:
    def __init__(self, homes: Homes, *, cli_path: str = CLI_PATH, maximum: int = 4) -> None:
        self.homes = homes
        self.cli_path = cli_path
        self.maximum = maximum
        self.sessions: dict[Owner, NativeSession] = {}
        self.locks: dict[Owner, asyncio.Lock] = {}
        self.probes: dict[Owner, tuple[float, asyncio.Task[dict[str, object]]]] = {}

    def lock(self, owner: Owner) -> asyncio.Lock:
        return self.locks.setdefault(owner, asyncio.Lock())

    async def status(self, owner: Owner) -> dict[str, object]:
        home = self.homes.get(owner)
        account: dict[str, object] = {"connected": False, "auth_method": None, "subscription_type": None}
        if home is not None:
            cached = self.probes.get(owner)
            if cached is None or (cached[1].done() and time.monotonic() - cached[0] > 5):
                cached = (time.monotonic(), asyncio.create_task(self.probe_account(home)))
                self.probes[owner] = cached
            try:
                account = await asyncio.shield(cached[1])
            except BaseException:
                if cached[1].done() and self.probes.get(owner) is cached:
                    self.probes.pop(owner, None)
                raise
        session = self.sessions.get(owner)
        result: dict[str, object] = {
            "available": True,
            "running": session is not None and session.running,
            **account,
            "session_mode": session.mode if session is not None and session.running else None,
        }
        return result

    async def probe_account(self, home: Home) -> dict[str, object]:
        result: dict[str, object] = {"connected": False, "auth_method": None, "subscription_type": None}
        output, _ = await self.command(home, "auth", "status", "--json")
        try:
            data = json.loads(output)
        except (ValueError, UnicodeDecodeError):
            raise NativeError("NATIVE_SESSION_FAILED", 502) from None
        if not isinstance(data, dict):
            raise NativeError("NATIVE_SESSION_FAILED", 502)
        result["connected"] = data.get("loggedIn") is True
        method = data.get("authMethod")
        if method in {"claude.ai", "api_key", "console", "third_party"}:
            result["auth_method"] = method
        plan = data.get("subscriptionType")
        if plan in {"free", "pro", "max", "ultra", "team", "enterprise"}:
            result["subscription_type"] = plan
        return result

    async def command(self, home: Home, *arguments: str) -> tuple[bytes, int]:
        process = await asyncio.create_subprocess_exec(
            self.cli_path,
            *arguments,
            cwd=home.path,
            env=native_environment(home),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            user=home.uid,
            group=home.uid,
            extra_groups=(),
            start_new_session=True,
            close_fds=True,
            preexec_fn=process_limits,
        )

        async def read_bounded() -> bytes:
            output = bytearray()
            if process.stdout is None:
                raise NativeError("NATIVE_SESSION_FAILED", 502)
            while chunk := await process.stdout.read(4096):
                output.extend(chunk)
                if len(output) > 65536:
                    raise NativeError("NATIVE_SESSION_FAILED", 502)
            await process.wait()
            return bytes(output)

        async def finish() -> None:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

        try:
            output = await asyncio.wait_for(read_bounded(), timeout=10)
        except TimeoutError:
            raise NativeError("NATIVE_SESSION_FAILED", 502) from None
        finally:
            cleanup = asyncio.create_task(finish())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
        return output, process.returncode if process.returncode is not None else -1

    def start(self, owner: Owner, mode: Mode, cols: int, rows: int) -> NativeSession:
        existing = self.sessions.get(owner)
        if existing is not None:
            if existing.stopping:
                raise NativeError("NATIVE_SESSION_ENDED")
            return existing
        if len(self.sessions) >= self.maximum:
            raise NativeError("NATIVE_SESSION_LIMIT", 429)
        home = self.homes.get(owner, create=True)
        if home is None:
            raise NativeError("NATIVE_SESSION_FAILED", 502)
        if not within_home_limit(home):
            raise NativeError("NATIVE_HOME_LIMIT")
        session = NativeSession(owner, home, mode, cols, rows)
        self.sessions[owner] = session
        return session

    async def launch(self, session: NativeSession) -> None:
        if session.process is not None:
            return
        master, slave = os.openpty()
        os.fchown(slave, session.home.uid, session.home.uid)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", session.rows, session.cols, 0, 0))
        # Both lifecycle modes retain the CLI's own onboarding and account choices.
        try:
            session.process = await asyncio.create_subprocess_exec(
                self.cli_path,
                cwd=session.home.path,
                env=native_environment(session.home),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                user=session.home.uid,
                group=session.home.uid,
                extra_groups=(),
                start_new_session=True,
                close_fds=True,
                preexec_fn=terminal_limits,
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        session.master = master
        asyncio.get_running_loop().add_reader(master, self.read_output, session)
        session.waiter = asyncio.create_task(self.wait_exit(session))

    def read_output(self, session: NativeSession) -> None:
        if session.master is None:
            return
        try:
            data = os.read(session.master, 8192)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            asyncio.get_running_loop().remove_reader(session.master)
            return
        session.replay.extend(data)
        if len(session.replay) > MAX_REPLAY_BYTES:
            del session.replay[:-MAX_REPLAY_BYTES]
        if session.queue is not None:
            try:
                session.queue.put_nowait(data)
            except asyncio.QueueFull:
                session.overflow = True

    async def wait_exit(self, session: NativeSession) -> None:
        if session.process is None:
            return
        await session.process.wait()
        self.read_output(session)
        if session.queue is not None:
            try:
                session.queue.put_nowait(None)
            except asyncio.QueueFull:
                session.overflow = True

    def resize(self, session: NativeSession, cols: int, rows: int) -> None:
        session.cols, session.rows = cols, rows
        if session.master is not None:
            fcntl.ioctl(session.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    async def input(self, session: NativeSession, data: str) -> None:
        try:
            encoded = data.encode()
        except UnicodeEncodeError:
            raise NativeError("NATIVE_BAD_REQUEST", 400) from None
        if len(encoded) > 8192 or session.master is None:
            raise NativeError("NATIVE_BAD_REQUEST", 400)
        descriptor = session.master

        async def write_all() -> None:
            offset = 0
            loop = asyncio.get_running_loop()
            while offset < len(encoded):
                if session.master != descriptor:
                    raise NativeError("NATIVE_SESSION_ENDED")
                try:
                    offset += os.write(descriptor, encoded[offset:])
                except BlockingIOError:
                    ready: asyncio.Future[None] = loop.create_future()

                    def writable(target: asyncio.Future[None] = ready) -> None:
                        if not target.done():
                            target.set_result(None)

                    loop.add_writer(descriptor, writable)
                    try:
                        await ready
                    finally:
                        loop.remove_writer(descriptor)

        try:
            await asyncio.wait_for(write_all(), timeout=5)
        except (TimeoutError, OSError):
            raise NativeError("NATIVE_SESSION_FAILED", 502) from None
        session.last_input = time.monotonic()

    async def stop(self, session: NativeSession) -> None:
        async with self.lock(session.owner):
            await self.stop_locked(session)

    async def stop_locked(self, session: NativeSession) -> None:
        if self.sessions.get(session.owner) is not session:
            return
        session.stopping = True
        cleanup = asyncio.create_task(self.finish_stop(session))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # Request cancellation must not leave an untracked native process.
            await cleanup
            raise
        finally:
            if (
                cleanup.done()
                and not cleanup.cancelled()
                and cleanup.exception() is None
                and self.sessions.get(session.owner) is session
            ):
                self.sessions.pop(session.owner)

    async def finish_stop(self, session: NativeSession) -> None:
        if session.queue is not None:
            while not session.queue.empty():
                session.queue.get_nowait()
            session.queue.put_nowait(None)
        if session.master is not None:
            asyncio.get_running_loop().remove_reader(session.master)
            os.close(session.master)
            session.master = None
        signal_native_user(session.home.uid, signal.SIGTERM)
        if session.process is not None and session.process.returncode is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(session.process.wait(), 2)
        for _attempt in range(20):
            if signal_native_user(session.home.uid, signal.SIGKILL) == 0:
                break
            await asyncio.sleep(0.05)
        else:
            # Keep this generation tracked and refuse replacement while cleanup
            # retries. A forking descendant must not become an unowned process.
            raise NativeError("NATIVE_SESSION_FAILED", 503)
        if session.process is not None:
            await session.process.wait()
        if session.waiter is not None and not session.waiter.done():
            session.waiter.cancel()
            await asyncio.gather(session.waiter, return_exceptions=True)
        session.replay.clear()

    async def sign_out(self, owner: Owner) -> None:
        session = self.sessions.get(owner)
        if session is not None:
            await self.stop_locked(session)
        home = self.homes.get(owner)
        if home is not None:
            _, code = await self.command(home, "auth", "logout")
            if code != 0:
                raise NativeError("NATIVE_SESSION_FAILED", 502)
        cached = self.probes.pop(owner, None)
        if cached is not None:
            cached[1].cancel()
            await asyncio.gather(cached[1], return_exceptions=True)

    async def reap(self) -> None:
        last_disk_check = time.monotonic()
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            disk_check = now - last_disk_check >= 60
            if disk_check:
                last_disk_check = now
            for session in tuple(self.sessions.values()):
                async with self.lock(session.owner):
                    if self.sessions.get(session.owner) is not session:
                        continue
                    now = time.monotonic()
                    expired = (
                        not session.running
                        or session.overflow
                        or now - session.created >= 3600
                        or now - session.last_input >= 900
                        or (session.attachment is None and now - session.detached >= 90)
                        or (disk_check and not within_home_limit(session.home))
                    )
                    if expired:
                        try:
                            await self.stop_locked(session)
                        except (NativeError, OSError):
                            # Keep failed cleanup tracked for a retry without losing
                            # lifetime and idle enforcement for the remaining owners.
                            continue

    async def close(self) -> None:
        probes = [entry[1] for entry in self.probes.values()]
        for probe in probes:
            probe.cancel()
        await asyncio.gather(*probes, return_exceptions=True)
        for session in tuple(self.sessions.values()):
            await self.stop(session)
        self.homes.close()
