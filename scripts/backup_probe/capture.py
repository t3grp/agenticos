"""Capture ciphertext into a bounded held buffer, then upload and verify via a fake sink.

PROTOTYPE ONLY. Source and sink are injected; this module opens nothing itself.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .core import (
    HeldBuffer,
    Ledger,
    Machine,
    ProbeError,
    State,
    is_sha256_hex,
    remaining_after_parts,
    safe_code,
    strict_int,
)


@dataclass(frozen=True)
class Finish:
    """What a capture source reports after EOF. Statuses are process exit codes."""

    producer_status: int
    encrypt_status: int
    dump_sha256: str
    cipher_sha256: str
    cipher_len: int


class Source(Protocol):
    def read(self, n: int) -> bytes: ...
    def finish(self) -> Finish: ...
    def cancel(self) -> None: ...


class Sink(Protocol):
    def put(self, name: str, data: memoryview) -> None: ...
    def get(self, name: str) -> bytes: ...


class Capture:
    def __init__(
        self,
        *,
        cipher_cap: int,
        ledger: Ledger,
        timeout_s: float,
        clock: Callable[[], float] = time.monotonic,
        chunk: int = 65536,
    ) -> None:
        self._cap = strict_int(cipher_cap, 1)
        self._chunk = strict_int(chunk, 1)
        if type(timeout_s) not in (int, float) or not timeout_s > 0:
            raise ProbeError("E_BAD_INT")
        self._timeout = timeout_s
        self._ledger = ledger
        self._clock = clock
        self._m = Machine()
        self._buf: HeldBuffer | None = None
        self.cipher_sha256 = ""
        self.dump_sha256 = ""
        self.cipher_len = 0

    @property
    def state(self) -> State:
        return self._m.state

    def _drop(self) -> None:
        if self._buf is not None:
            self._buf.clear()

    def _fail(self, exc: BaseException) -> ProbeError:
        """Invalidate held output and move to FAILED. Returns the coded error to raise."""
        self._drop()
        self._m.move(State.FAILED)
        return ProbeError(safe_code(exc))

    def run(
        self,
        source: Source,
        parts: Mapping[str, bytes] | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        """NEW -> CAPTURING -> HELD. Any failure invalidates the buffer. The sink is never called."""
        self._m.move(State.CAPTURING)
        try:
            self._buf = HeldBuffer(remaining_after_parts(self._cap, parts))
            self._read_all(source, cancel)
        except BaseException as exc:
            try:
                source.cancel()
            except Exception:
                pass
            err = self._fail(exc)
            if isinstance(exc, Exception):
                raise err from None
            raise
        self._m.move(State.HELD)

    def _read_all(self, source: Source, cancel: threading.Event | None) -> None:
        assert self._buf is not None
        deadline = self._clock() + self._timeout
        sha = hashlib.sha256()
        while True:
            if cancel is not None and cancel.is_set():
                raise ProbeError("E_CANCELLED")
            if self._clock() > deadline:
                raise ProbeError("E_TIMEOUT")
            chunk = source.read(self._chunk)
            if type(chunk) not in (bytes, bytearray):
                raise ProbeError("E_BAD_SOURCE")
            if not chunk:
                break
            self._buf.append(chunk)
            sha.update(chunk)
        fin = source.finish()
        if not isinstance(fin, Finish):
            raise ProbeError("E_BAD_SOURCE")
        if strict_int(fin.producer_status) != 0:
            raise ProbeError("E_PRODUCER")
        if strict_int(fin.encrypt_status) != 0:
            raise ProbeError("E_ENCRYPT")
        if not (is_sha256_hex(fin.dump_sha256) and is_sha256_hex(fin.cipher_sha256)):
            raise ProbeError("E_SHA")
        n = len(self._buf)
        if n == 0 or strict_int(fin.cipher_len) != n:
            raise ProbeError("E_TRUNCATED")
        if fin.cipher_sha256 != sha.hexdigest():
            raise ProbeError("E_SHA")
        self.cipher_sha256, self.dump_sha256, self.cipher_len = sha.hexdigest(), fin.dump_sha256, n

    def upload(self, sink: Sink, name: str) -> None:
        """HELD -> UPLOADING -> UPLOADED. Budget is reserved first; a lost outcome is UNKNOWN."""
        self._m.require(State.HELD)
        assert self._buf is not None
        try:
            self._ledger.reserve("put", len(self._buf))
        except ProbeError as exc:
            raise self._fail(exc) from None
        self._m.move(State.UPLOADING)
        try:
            with self._buf.view() as view:
                sink.put(name, view)
        except Exception:
            self._drop()
            self._m.move(State.UNKNOWN)
            raise ProbeError("E_UPLOAD_UNKNOWN") from None
        self._drop()  # the stored copy is checked by SHA; the held copy is no longer needed
        self._m.move(State.UPLOADED)

    def verify(self, sink: Sink, name: str) -> None:
        """UPLOADED -> VERIFIED by reading back and comparing exact length and SHA."""
        self._m.require(State.UPLOADED)
        self._ledger.reserve("get", self.cipher_len)  # budget refusal leaves state UPLOADED
        try:
            body = sink.get(name)
        except Exception:
            self._m.move(State.UNKNOWN)
            raise ProbeError("E_VERIFY_UNKNOWN") from None
        if (
            type(body) is not bytes
            or len(body) != self.cipher_len
            or hashlib.sha256(body).hexdigest() != self.cipher_sha256
        ):
            self._m.move(State.FAILED)
            raise ProbeError("E_VERIFY_MISMATCH")
        self._m.move(State.VERIFIED)
