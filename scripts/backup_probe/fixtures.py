"""In-memory synthetic fixtures for tests and selftest. No key material, no I/O.

The fixture "cipher" is a fixed XOR byte transform; it is not encryption.
"""

from __future__ import annotations

import hashlib

from .capture import Finish
from .core import CapacityError

XOR = 0x5A


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_cipher(plain: bytes) -> bytes:
    return bytes(b ^ XOR for b in plain)


class MemSource:
    def __init__(
        self,
        plain: bytes,
        *,
        producer_status: int = 0,
        encrypt_status: int = 0,
        early_eof_at: int | None = None,
        read_error: BaseException | None = None,
        dump_sha: str | None = None,
        cipher_sha: str | None = None,
    ) -> None:
        self.cipher = fixture_cipher(plain)
        self._pos = 0
        self._plain = plain
        self.producer_status = producer_status
        self.encrypt_status = encrypt_status
        self.early_eof_at = early_eof_at
        self.read_error = read_error
        self.dump_sha = dump_sha or sha(plain)
        self.cipher_sha = cipher_sha or sha(self.cipher)
        self.cancelled = False

    def read(self, n: int) -> bytes:
        if self.read_error is not None:
            raise self.read_error
        end = len(self.cipher) if self.early_eof_at is None else self.early_eof_at
        chunk = self.cipher[self._pos : min(self._pos + n, end)]
        self._pos += len(chunk)
        return chunk

    def finish(self) -> Finish:
        return Finish(self.producer_status, self.encrypt_status, self.dump_sha, self.cipher_sha, len(self.cipher))

    def cancel(self) -> None:
        self.cancelled = True


class MemStream:
    def __init__(self, plain: bytes, status: int | bool = 0) -> None:
        self._plain, self._pos, self._status = plain, 0, status
        self.aborted = False

    def read(self, n: int) -> bytes:
        chunk = self._plain[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def finish(self) -> int:
        return self._status  # type: ignore[return-value]

    def abort(self) -> None:
        self.aborted = True


class MemDecrypt:
    def __init__(self, status: int | bool = 0) -> None:
        self.status = status
        self.last: MemStream | None = None

    def start(self, cipher: memoryview) -> MemStream:
        self.last = MemStream(fixture_cipher(bytes(cipher)), self.status)
        return self.last


class MemConsumer:
    def __init__(
        self, *, capacity: int | None = None, status: int | bool = 0, write_error: BaseException | None = None
    ) -> None:
        self.data = bytearray()
        self.capacity, self.status, self.write_error = capacity, status, write_error
        self.aborted = False

    def write(self, chunk: bytes) -> None:
        if self.write_error is not None:
            raise self.write_error
        if self.capacity is not None and len(self.data) + len(chunk) > self.capacity:
            raise CapacityError
        self.data += chunk

    def finish(self) -> int:
        return self.status  # type: ignore[return-value]

    def abort(self) -> None:
        self.aborted = True
        self.data[:] = bytes(len(self.data))  # best-effort zeroing, not secure erasure
        self.data.clear()
