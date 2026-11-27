"""CLI wrapper. Only `selftest` (in-memory synthetic fixtures) runs; everything else is disabled.

There is no live mode, no enable flag and no option that opens a file, process or network.
Output is a fixed allowlisted code; no traceback, message, path or argument is echoed.
"""

from __future__ import annotations

import sys
from typing import TextIO

from .capture import Capture
from .core import FakeTransport, Ledger, ProbeError, State, safe_code
from .fixtures import MemConsumer, MemDecrypt, MemSource, sha
from .restore import Expected, restore


def _selftest() -> None:
    plain = b"synthetic-row-" * 1000
    cap = 1 << 20
    ledger = Ledger(cipher_cap=cap, max_requests=4, sent_bytes=cap, read_bytes=2 * cap)
    cap_run = Capture(cipher_cap=cap, ledger=ledger, timeout_s=30)
    sink = FakeTransport()
    cap_run.run(MemSource(plain))
    cap_run.upload(sink, "obj")
    cap_run.verify(sink, "obj")
    if cap_run.state is not State.VERIFIED:
        raise ProbeError("E_INTERNAL")
    consumer = MemConsumer()
    res = restore(
        sink=sink,
        name="obj",
        ledger=ledger,
        expected=Expected(cap_run.cipher_sha256, cap_run.cipher_len, sha(plain)),
        decrypt=MemDecrypt(),
        consumer=consumer,
    )
    if not res.trusted or bytes(consumer.data) != plain:
        raise ProbeError("E_INTERNAL")


def main(argv: list[str], out: TextIO) -> int:
    if argv != ["selftest"]:
        out.write("E_DISABLED\n")
        return 2
    try:
        _selftest()
    except Exception as exc:
        out.write(safe_code(exc) + "\n")
        return 1
    out.write("selftest ok\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdout))
