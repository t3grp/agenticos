"""Restore verification: ciphertext gates, decrypt, plaintext SHA, consumer, quarantine.

PROTOTYPE ONLY. Decrypt adapter and consumer are injected fixtures.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .capture import Sink
from .core import CapacityError, HeldBuffer, Ledger, is_sha256_hex, strict_int

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
BAD_EXPECTED = "bad_expectation"


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
    chunk: int = 65536,
) -> RestoreResult:
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
        body = sink.get(name)
    except Exception:
        return _bad(FETCH_FAILED, consumer)
    if type(body) is not bytes or len(body) < want_len:
        return _bad(TRUNCATED, consumer)
    if len(body) > want_len or hashlib.sha256(body).hexdigest() != expected.cipher_sha256:
        return _bad(CORRUPT, consumer)
    held = HeldBuffer(want_len)
    held.append(body)
    del body
    try:
        return _decrypt_into(held, expected, decrypt, consumer, chunk)
    finally:
        held.clear()


def _decrypt_into(
    held: HeldBuffer, expected: Expected, decrypt: Decrypt, consumer: Consumer, chunk: int
) -> RestoreResult:
    sha = hashlib.sha256()
    stream: Stream | None = None
    try:
        with held.view() as view:
            stream = decrypt.start(view)
            while True:
                piece = stream.read(chunk)
                if type(piece) not in (bytes, bytearray):
                    raise TypeError
                if not piece:
                    break
                sha.update(piece)
                consumer.write(bytes(piece))
            decrypt_status = stream.finish()
    except CapacityError:
        if stream is not None:
            _quiet(stream.abort)
        return _bad(INCONCLUSIVE, consumer)
    except Exception:
        if stream is not None:
            _quiet(stream.abort)
        return _bad(DECRYPT_FAILED, consumer)
    try:
        consumer_status = consumer.finish()
    except CapacityError:
        return _bad(INCONCLUSIVE, consumer)
    except Exception:
        return _bad(CONSUMER_FAILED, consumer)
    if type(decrypt_status) is not int or type(consumer_status) is not int:
        return _bad(BAD_STATUS, consumer)
    if decrypt_status != 0:
        return _bad(DECRYPT_FAILED, consumer)
    if consumer_status != 0:
        return _bad(CONSUMER_FAILED, consumer)
    if sha.hexdigest() != expected.dump_sha256:
        return _bad(PLAINTEXT_MISMATCH, consumer)
    return RestoreResult(VERIFIED, True)
