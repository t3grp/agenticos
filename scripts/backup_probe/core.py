"""Shared pure logic: error codes, strict ints, state machine, ledger, buffer, fake sink.

PROTOTYPE ONLY. Nothing here opens a file, process or network connection.
"""

from __future__ import annotations

import enum
import re
import threading
from collections.abc import Mapping

CODES = frozenset(
    {
        "E_BAD_INT",
        "E_BAD_OP",
        "E_BAD_STATE",
        "E_BAD_PART",
        "E_BAD_SOURCE",
        "E_BAD_NAME",
        "E_BUDGET",
        "E_BUDGET_CONFIG",
        "E_CANCELLED",
        "E_CAP",
        "E_ENCRYPT",
        "E_PRODUCER",
        "E_SHA",
        "E_TIMEOUT",
        "E_TRUNCATED",
        "E_UPLOAD_UNKNOWN",
        "E_VERIFY_MISMATCH",
        "E_VERIFY_UNKNOWN",
        "E_DISABLED",
        "E_INTERNAL",
    }
)


class ProbeError(Exception):
    """The only error type that crosses the API. It carries a fixed code, never detail."""

    def __init__(self, code: str) -> None:
        self.code = code if code in CODES else "E_INTERNAL"
        super().__init__(self.code)

    def __str__(self) -> str:
        return self.code


class CapacityError(Exception):
    """Raised by a consumer when it runs out of space. Restore reports INCONCLUSIVE."""


class TransportLost(Exception):
    """A transfer ended without a trustworthy answer (lost response or partial transfer)."""


def safe_code(exc: BaseException) -> str:
    """Map any exception to an allowlisted code. The message is never read."""
    return exc.code if isinstance(exc, ProbeError) else "E_INTERNAL"


def strict_int(value: object, minimum: int = 0) -> int:
    """Accept only a real int >= minimum. Rejects bool, float, NaN, str and None."""
    if type(value) is not int or value < minimum:
        raise ProbeError("E_BAD_INT")
    return value


_SHA_RE = re.compile(r"[0-9a-f]{64}")


def is_sha256_hex(value: object) -> bool:
    return isinstance(value, str) and _SHA_RE.fullmatch(value) is not None


class State(enum.Enum):
    NEW = "NEW"
    CAPTURING = "CAPTURING"
    HELD = "HELD"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


_S = State
_TRANSITIONS = {
    _S.NEW: {_S.CAPTURING, _S.FAILED},
    _S.CAPTURING: {_S.HELD, _S.FAILED},
    _S.HELD: {_S.UPLOADING, _S.FAILED},
    _S.UPLOADING: {_S.UPLOADED, _S.FAILED, _S.UNKNOWN},
    _S.UPLOADED: {_S.VERIFIED, _S.FAILED, _S.UNKNOWN},
    _S.VERIFIED: set(),
    _S.FAILED: set(),
    _S.UNKNOWN: set(),
}


class Machine:
    """One-way state machine. There is no reset and no retry."""

    def __init__(self) -> None:
        self.state = State.NEW

    def move(self, new: State) -> None:
        if new not in _TRANSITIONS[self.state]:
            raise ProbeError("E_BAD_STATE")
        self.state = new

    def require(self, state: State) -> None:
        if self.state is not state:
            raise ProbeError("E_BAD_STATE")


class Ledger:
    """Request and byte budget. Spend is reserved atomically BEFORE a call and never refunded.

    Pools: total requests, bytes sent (put) and bytes read (get). Each byte pool must
    not exceed 2 * cipher_cap, so no hidden retry can multiply egress.
    """

    OPS = {"put": "sent", "get": "read", "head": None}

    def __init__(self, *, cipher_cap: int, max_requests: int, sent_bytes: int, read_bytes: int) -> None:
        cap = strict_int(cipher_cap, 1)
        self._left = {
            "requests": strict_int(max_requests),
            "sent": strict_int(sent_bytes),
            "read": strict_int(read_bytes),
        }
        if self._left["sent"] > 2 * cap or self._left["read"] > 2 * cap:
            raise ProbeError("E_BUDGET_CONFIG")
        self._lock = threading.Lock()

    def reserve(self, op: object, nbytes: object) -> None:
        if type(op) is not str or op not in self.OPS:
            raise ProbeError("E_BAD_OP")
        n = strict_int(nbytes)
        pool = self.OPS[op]
        with self._lock:
            if self._left["requests"] < 1 or (pool is not None and self._left[pool] < n):
                raise ProbeError("E_BUDGET")
            self._left["requests"] -= 1
            if pool is not None:
                self._left[pool] -= n

    def remaining(self) -> dict[str, int]:
        with self._lock:
            return dict(self._left)


class HeldBuffer:
    """Bounded bytearray for ciphertext. clear() zeroes best effort (not guaranteed erasure)."""

    def __init__(self, cap: int) -> None:
        self._cap = strict_int(cap)
        self._buf = bytearray()
        self.valid = True

    def __len__(self) -> int:
        return len(self._buf)

    def append(self, chunk: bytes | bytearray) -> None:
        if not self.valid:
            raise ProbeError("E_BAD_STATE")
        if len(self._buf) + len(chunk) > self._cap:
            raise ProbeError("E_CAP")
        self._buf += chunk

    def view(self) -> memoryview:
        if not self.valid:
            raise ProbeError("E_BAD_STATE")
        return memoryview(self._buf)

    def clear(self) -> None:
        self.valid = False
        self._buf[:] = bytes(len(self._buf))
        try:
            self._buf.clear()
        except BufferError:  # a live view keeps the (already zeroed) storage
            pass


_PART_NAMES = frozenset({"config", "manifest"})


def remaining_after_parts(cap: int, parts: Mapping[str, bytes] | None) -> int:
    """Bytes left for the dump stream once config/manifest parts count against ONE aggregate cap."""
    left = strict_int(cap)
    for name, body in (parts or {}).items():
        if name not in _PART_NAMES or type(body) not in (bytes, bytearray):
            raise ProbeError("E_BAD_PART")
        left -= len(body)
    if left < 0:
        raise ProbeError("E_CAP")
    return left


_NAME_RE = re.compile(r"[a-z0-9_.-]{1,64}")


class FakeTransport:
    """Deterministic in-memory sink. The only supported sink: put, get, head. No network."""

    def __init__(self, *, put_outcome: str = "ok", get_outcome: str = "ok") -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, int]] = []
        self.put_outcome = put_outcome
        self.get_outcome = get_outcome

    @staticmethod
    def _name(name: object) -> str:
        if not isinstance(name, str) or _NAME_RE.fullmatch(name) is None:
            raise ProbeError("E_BAD_NAME")
        return name

    def put(self, name: str, data: bytes | bytearray | memoryview) -> None:
        self._name(name)
        body = bytes(data)
        self.calls.append(("put", len(body)))
        if self.put_outcome == "lose_response":
            self.objects[name] = body
            raise TransportLost
        if self.put_outcome == "partial":
            self.objects[name] = body[: len(body) // 2]
            raise TransportLost
        if self.put_outcome == "error":
            raise RuntimeError("fixture put failure")
        self.objects[name] = body

    def get(self, name: str) -> bytes:
        self._name(name)
        self.calls.append(("get", len(self.objects.get(name, b""))))
        if self.get_outcome == "lose":
            raise TransportLost
        return self.objects[name]

    def head(self, name: str) -> int:
        self._name(name)
        self.calls.append(("head", 0))
        return len(self.objects[name])
