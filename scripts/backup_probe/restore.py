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


@dataclass(frozen=True)
class Expected:
    cipher_sha256: str
    cipher_len: int
    dump_sha256: str


@dataclass(frozen=True)
class RestoreResult:
    """trusted is True only when every gate passed. Otherwise the quarantine is discarded."""

    outcome: str
    trusted: bool


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


def _quiet(fn: Callable[[], object]) -> None:
    try:
        fn()
    except Exception:
        pass


def _bad(outcome: str, consumer: Consumer) -> RestoreResult:
    _quiet(consumer.abort)  # discard the quarantine target
    return RestoreResult(outcome, False)


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
        _quiet(consumer.abort)
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
        _abort_stream(stream)
        return _bad(TIMEOUT, consumer)
    except CapacityError:
        _abort_stream(stream)
        return _bad(INCONCLUSIVE, consumer)
    except _ConsumerFault:
        _abort_stream(stream)
        return _bad(CONSUMER_FAILED, consumer)
    except Exception:
        _abort_stream(stream)
        return _bad(DECRYPT_FAILED, consumer)
    except BaseException:
        _abort_stream(stream)
        _quiet(consumer.abort)
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
        _quiet(consumer.abort)
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


def _abort_stream(stream: Stream | None) -> None:
    if stream is not None:
        _quiet(stream.abort)
