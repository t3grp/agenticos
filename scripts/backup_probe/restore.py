"""Restore verification: ciphertext gates, decrypt, plaintext SHA, consumer, quarantine.

PROTOTYPE ONLY. Decrypt adapter and consumer are injected fixtures.

Limitation: deadline checks run between adapter calls. A decrypt stream or consumer that blocks
inside one call cannot be interrupted by pure Python; the adapter must bound its own blocking.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .capture import Sink
from .core import CapacityError, HeldBuffer, Ledger, ObjectTooLarge, is_sha256_hex, strict_int

VERIFIED = "verified"
CORRUPT = "corrupt_ciphertext"
TRUNCATED = "truncated"
FETCH_FAILED = "fetch_failed"
BUDGET = "budget_refused"
DECRYPT_FAILED = "decrypt_failed"
BAD_STATUS = "malformed_status"
CONSUMER_FAILED = "consumer_failed"
PLAINTEXT_MISMATCH = "plaintext_sha_mismatch"
INCONCLUSIVE = "inconclusive_capacity"
TIMEOUT = "timeout"
BAD_EXPECTED = "bad_expectation"
BAD_PARAMS = "bad_parameters"

CLEAN = "clean"
CLEANUP_FAILED = "cleanup_failed"  # fixed allowlist; never carries exception text


@dataclass(frozen=True)
class Expected:
    cipher_sha256: str
    cipher_len: int
    dump_sha256: str


@dataclass(frozen=True)
class RestoreResult:
    """trusted is True only when every gate passed. Otherwise the quarantine is discarded.

    cleanup is CLEAN or CLEANUP_FAILED. CLEANUP_FAILED means an abort raised, so the discard is
    unconfirmed; outcome still reports the primary failure.
    """

    outcome: str
    trusted: bool
    cleanup: str = CLEAN


class Stream(Protocol):
    def read(self, n: int) -> bytes: ...
    def finish(self) -> int: ...
    def abort(self) -> None: ...


class Decrypt(Protocol):
    def start(self, cipher: memoryview) -> Stream: ...


class Consumer(Protocol):
    def write(self, chunk: bytes) -> None: ...
    def finish(self) -> int: ...
    def abort(self) -> None: ...


class _Timeout(Exception):
    pass


class _Malformed(Exception):
    pass


class _ConsumerFault(Exception):
    pass


def _attempt(fn: Callable[[], object]) -> bool:
    """Run one cleanup step. False if it raised; the exception text is dropped on purpose."""
    try:
        fn()
    except Exception:
        return False
    return True


def _discard(consumer: Consumer, stream: Stream | None = None) -> str:
    """Abort stream then consumer, both always tried. Returns CLEAN or CLEANUP_FAILED."""
    ok = True
    if stream is not None:
        ok = _attempt(stream.abort)
    ok = _attempt(consumer.abort) and ok
    return CLEAN if ok else CLEANUP_FAILED


def _discard_on_interrupt(consumer: Consumer, stream: Stream | None = None) -> None:
    """Best-effort cleanup while an interrupt propagates. Nothing here may replace the interrupt."""
    for fn in (stream.abort if stream is not None else None, consumer.abort):
        if fn is None:
            continue
        try:
            fn()
        except BaseException:
            pass


def _bad(outcome: str, consumer: Consumer, stream: Stream | None = None) -> RestoreResult:
    return RestoreResult(outcome, False, _discard(consumer, stream))  # discard the quarantine target


def restore(
    *,
    sink: Sink,
    name: str,
    ledger: Ledger,
    expected: Expected,
    decrypt: Decrypt,
    consumer: Consumer,
    chunk: int,
    max_plain_bytes: int,
    timeout_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> RestoreResult:
    """One GET, then every gate. chunk, max_plain_bytes and timeout_s are required and finite."""
    try:
        strict_int(chunk, 1)
        strict_int(max_plain_bytes, 1)
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not timeout_s > 0:
            raise ValueError
    except Exception:
        return _bad(BAD_PARAMS, consumer)
    try:
        want_len = strict_int(expected.cipher_len, 1)
    except Exception:
        return _bad(BAD_EXPECTED, consumer)
    if not (is_sha256_hex(expected.cipher_sha256) and is_sha256_hex(expected.dump_sha256)):
        return _bad(BAD_EXPECTED, consumer)
    try:
        ledger.reserve("get", want_len)  # reserved before the transfer, never refunded
    except Exception:
        return _bad(BUDGET, consumer)
    try:
        body = sink.get(name, want_len)  # the sink refuses oversized objects before returning them
    except ObjectTooLarge:
        return _bad(CORRUPT, consumer)
    except Exception:
        return _bad(FETCH_FAILED, consumer)
    except BaseException:
        _discard_on_interrupt(consumer)
        raise
    if type(body) is not bytes or len(body) < want_len:
        return _bad(TRUNCATED, consumer)
    if len(body) > want_len or hashlib.sha256(body).hexdigest() != expected.cipher_sha256:
        return _bad(CORRUPT, consumer)
    held = HeldBuffer(want_len)
    held.append(body)
    del body
    try:
        return _decrypt_into(held, expected, decrypt, consumer, chunk, max_plain_bytes, timeout_s, clock)
    finally:
        held.clear()


def _decrypt_into(
    held: HeldBuffer,
    expected: Expected,
    decrypt: Decrypt,
    consumer: Consumer,
    chunk: int,
    max_plain: int,
    timeout_s: float,
    clock: Callable[[], float],
) -> RestoreResult:
    def now() -> float:
        value = clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise _Timeout
        return value

    deadline = 0.0

    def tick() -> None:
        if now() > deadline:
            raise _Timeout

    sha = hashlib.sha256()
    total = 0
    stream: Stream | None = None
    try:
        deadline = now() + timeout_s
        with held.view() as view:
            stream = decrypt.start(view)
            tick()
            while True:
                piece = stream.read(chunk)
                tick()
                if type(piece) not in (bytes, bytearray) or len(piece) > chunk:
                    raise _Malformed
                if not piece:
                    break
                if total + len(piece) > max_plain:
                    raise CapacityError  # refused before the consumer sees the piece
                total += len(piece)
                sha.update(piece)
                try:
                    consumer.write(bytes(piece))
                except CapacityError:
                    raise
                except Exception:
                    raise _ConsumerFault from None
            decrypt_status = stream.finish()
            tick()
    except _Timeout:
        return _bad(TIMEOUT, consumer, stream)
    except CapacityError:
        return _bad(INCONCLUSIVE, consumer, stream)
    except _ConsumerFault:
        return _bad(CONSUMER_FAILED, consumer, stream)
    except Exception:
        return _bad(DECRYPT_FAILED, consumer, stream)
    except BaseException:
        _discard_on_interrupt(consumer, stream)
        raise
    try:
        consumer_status = consumer.finish()
        tick()
    except _Timeout:
        return _bad(TIMEOUT, consumer)
    except CapacityError:
        return _bad(INCONCLUSIVE, consumer)
    except Exception:
        return _bad(CONSUMER_FAILED, consumer)
    except BaseException:
        _discard_on_interrupt(consumer)
        raise
    if type(decrypt_status) is not int or type(consumer_status) is not int:
        return _bad(BAD_STATUS, consumer)
    if decrypt_status != 0:
        return _bad(DECRYPT_FAILED, consumer)
    if consumer_status != 0:
        return _bad(CONSUMER_FAILED, consumer)
    if sha.hexdigest() != expected.dump_sha256:
        return _bad(PLAINTEXT_MISMATCH, consumer)
    return RestoreResult(VERIFIED, True)
