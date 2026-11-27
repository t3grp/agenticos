"""Focused stdlib tests. Run from scripts/: python -m unittest backup_probe.test_backup_probe"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import unittest
from decimal import Decimal
from pathlib import Path

from . import preflight
from .__main__ import main
from .capture import Capture, Finish
from .core import (
    CapacityError,
    FakeTransport,
    HeldBuffer,
    Ledger,
    ProbeError,
    State,
    remaining_after_parts,
    safe_code,
    strict_int,
)
from .fixtures import MemConsumer, MemDecrypt, MemSource, fixture_cipher, sha
from .restore import Expected, restore

CANARY = "CANARY-7f3a91"
PLAIN = b"synthetic-row-" * 100  # 1400 bytes
CAP = 4096


def ledger(requests: int = 8, sent: int = CAP, read: int = 2 * CAP, cap: int = CAP) -> Ledger:
    return Ledger(cipher_cap=cap, max_requests=requests, sent_bytes=sent, read_bytes=read)


def capture(cap: int = CAP, led: Ledger | None = None, **kw: object) -> Capture:
    return Capture(cipher_cap=cap, ledger=led or ledger(cap=cap, sent=cap, read=2 * cap), timeout_s=10, **kw)  # type: ignore[arg-type]


def code_of(fn: object) -> str:
    try:
        fn()  # type: ignore[operator]
    except ProbeError as exc:
        return exc.code
    raise AssertionError("no ProbeError raised")


class StrictInts(unittest.TestCase):
    def test_rejects_bad_values(self) -> None:
        for bad in (True, False, -1, 1.0, float("nan"), "1", None, 10**3 + 0.5):
            self.assertEqual(code_of(lambda b=bad: strict_int(b)), "E_BAD_INT")
        self.assertEqual(strict_int(0), 0)

    def test_ledger_rejects_bad_ops_and_amounts(self) -> None:
        led = ledger()
        for op in ("delete", "", None, 1, "PUT"):
            self.assertEqual(code_of(lambda o=op: led.reserve(o, 1)), "E_BAD_OP")
        for n in (True, -1, 1.5, float("nan")):
            self.assertEqual(code_of(lambda x=n: led.reserve("put", x)), "E_BAD_INT")
        self.assertEqual(led.remaining()["requests"], 8)

    def test_ledger_egress_limit(self) -> None:
        self.assertEqual(code_of(lambda: ledger(read=2 * CAP + 1)), "E_BUDGET_CONFIG")
        self.assertEqual(code_of(lambda: ledger(sent=2 * CAP + 1)), "E_BUDGET_CONFIG")
        ledger(read=2 * CAP)


class LedgerBudget(unittest.TestCase):
    def test_exact_boundaries(self) -> None:
        led = ledger(requests=2, sent=10)
        led.reserve("put", 10)
        self.assertEqual(code_of(lambda: led.reserve("put", 1)), "E_BUDGET")
        led.reserve("head", 0)
        self.assertEqual(code_of(lambda: led.reserve("head", 0)), "E_BUDGET")

    def test_refused_reserve_spends_nothing(self) -> None:
        led = ledger(requests=3, sent=5)
        self.assertEqual(code_of(lambda: led.reserve("put", 6)), "E_BUDGET")
        self.assertEqual(led.remaining(), {"requests": 3, "sent": 5, "read": 2 * CAP})

    def test_concurrent_race_cannot_overspend(self) -> None:
        led = ledger(requests=1000, sent=100, read=100, cap=100)
        wins: list[int] = []
        barrier = threading.Barrier(16)

        def worker() -> None:
            barrier.wait()
            for _ in range(20):
                try:
                    led.reserve("put", 1)
                    wins.append(1)
                except ProbeError:
                    pass

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(wins), 100)
        self.assertEqual(led.remaining()["sent"], 0)

    def test_no_refund_on_lost_upload(self) -> None:
        led = ledger()
        cap = capture(led=led)
        cap.run(MemSource(PLAIN))
        n = cap.cipher_len
        sink = FakeTransport(put_outcome="partial")
        self.assertEqual(code_of(lambda: cap.upload(sink, "obj")), "E_UPLOAD_UNKNOWN")
        self.assertEqual(led.remaining()["sent"], CAP - n)
        self.assertEqual(led.remaining()["requests"], 7)


class CaptureCaps(unittest.TestCase):
    def test_exact_cap_plus_minus_one(self) -> None:
        n = len(PLAIN)
        capture(cap=n).run(MemSource(PLAIN))
        c = capture(cap=n - 1)
        self.assertEqual(code_of(lambda: c.run(MemSource(PLAIN))), "E_CAP")
        self.assertEqual(c.state, State.FAILED)
        capture(cap=n + 1).run(MemSource(PLAIN))

    def test_aggregate_across_parts(self) -> None:
        n = len(PLAIN)
        parts = {"config": b"c" * 10, "manifest": b"m" * 5}
        capture(cap=n + 15).run(MemSource(PLAIN), parts)
        c = capture(cap=n + 14)
        self.assertEqual(code_of(lambda: c.run(MemSource(PLAIN), parts)), "E_CAP")
        self.assertEqual(code_of(lambda: remaining_after_parts(10, {"config": b"x" * 11})), "E_CAP")
        self.assertEqual(code_of(lambda: remaining_after_parts(10, {"other": b"x"})), "E_BAD_PART")
        self.assertEqual(code_of(lambda: remaining_after_parts(10, {"config": "text"})), "E_BAD_PART")  # type: ignore[dict-item]

    def test_held_buffer_bound_and_clear(self) -> None:
        buf = HeldBuffer(4)
        buf.append(b"abcd")
        self.assertEqual(code_of(lambda: buf.append(b"e")), "E_CAP")
        view = buf.view()
        buf.clear()
        self.assertEqual(bytes(view), b"\x00" * 4)  # zeroed in place even with a live view
        view.release()
        self.assertEqual(code_of(lambda: buf.append(b"x")), "E_BAD_STATE")
        self.assertEqual(code_of(buf.view), "E_BAD_STATE")


class CaptureFailures(unittest.TestCase):
    def failing(self, source: MemSource, expect: str, **run_kw: object) -> Capture:
        c = capture()
        self.assertEqual(code_of(lambda: c.run(source, **run_kw)), expect)  # type: ignore[arg-type]
        self.assertEqual(c.state, State.FAILED)
        self.assertTrue(source.cancelled)
        self.assertEqual(code_of(lambda: c.upload(FakeTransport(), "obj")), "E_BAD_STATE")
        return c

    def test_producer_and_encrypt_failures(self) -> None:
        self.failing(MemSource(PLAIN, producer_status=1), "E_PRODUCER")
        self.failing(MemSource(PLAIN, encrypt_status=2), "E_ENCRYPT")

    def test_malformed_flags(self) -> None:
        for bad in (True, False, -1, 0.0, None, "0"):
            self.failing(MemSource(PLAIN, producer_status=bad), "E_BAD_INT")  # type: ignore[arg-type]
            self.failing(MemSource(PLAIN, encrypt_status=bad), "E_BAD_INT")  # type: ignore[arg-type]

    def test_early_eof_and_empty(self) -> None:
        self.failing(MemSource(PLAIN, early_eof_at=10), "E_TRUNCATED")
        self.failing(MemSource(PLAIN, early_eof_at=0), "E_TRUNCATED")

    def test_sha_mismatch_and_malformed_sha(self) -> None:
        self.failing(MemSource(PLAIN, cipher_sha="0" * 64), "E_SHA")
        self.failing(MemSource(PLAIN, dump_sha="XYZ"), "E_SHA")
        self.failing(MemSource(PLAIN, dump_sha=sha(b"x").upper()), "E_SHA")

    def test_timeout_and_cancel(self) -> None:
        ticks = iter([0.0, 5.0, 100.0, 200.0, 300.0])
        c = Capture(cipher_cap=CAP, ledger=ledger(), timeout_s=10, clock=lambda: next(ticks), chunk=100)
        src = MemSource(PLAIN)
        self.assertEqual(code_of(lambda: c.run(src)), "E_TIMEOUT")
        self.assertTrue(src.cancelled)
        ev = threading.Event()
        ev.set()
        self.failing(MemSource(PLAIN), "E_CANCELLED", cancel=ev)

    def test_failure_clears_held_output(self) -> None:
        c = capture()
        src = MemSource(PLAIN, producer_status=1)
        code_of(lambda: c.run(src))
        self.assertIsNotNone(c._buf)
        self.assertFalse(c._buf.valid)  # type: ignore[union-attr]
        self.assertEqual(len(c._buf), 0)  # type: ignore[arg-type]

    def test_source_exception_text_never_escapes(self) -> None:
        c = capture()
        err = code_of(lambda: c.run(MemSource(PLAIN, read_error=RuntimeError(CANARY))))
        self.assertEqual(err, "E_INTERNAL")
        self.assertNotIn(CANARY, repr(ProbeError(err)))

    def test_no_sink_call_after_failure(self) -> None:
        sink = FakeTransport()
        c = capture()
        code_of(lambda: c.run(MemSource(PLAIN, producer_status=1)))
        code_of(lambda: c.upload(sink, "obj"))
        code_of(lambda: c.verify(sink, "obj"))
        self.assertEqual(sink.calls, [])


class UploadFlow(unittest.TestCase):
    def test_happy_path_states(self) -> None:
        sink = FakeTransport()
        c = capture()
        self.assertEqual(c.state, State.NEW)
        c.run(MemSource(PLAIN))
        self.assertEqual(c.state, State.HELD)
        c.upload(sink, "obj")
        self.assertEqual(c.state, State.UPLOADED)
        c.verify(sink, "obj")
        self.assertEqual(c.state, State.VERIFIED)
        self.assertEqual(sink.objects["obj"], fixture_cipher(PLAIN))
        self.assertEqual({m for m, _ in sink.calls}, {"put", "get"})

    def test_reserve_before_call(self) -> None:
        led = ledger(requests=0)
        sink = FakeTransport()
        c = capture(led=led)
        c.run(MemSource(PLAIN))
        self.assertEqual(code_of(lambda: c.upload(sink, "obj")), "E_BUDGET")
        self.assertEqual(sink.calls, [])
        self.assertEqual(c.state, State.FAILED)

    def test_byte_budget_refusal_before_call(self) -> None:
        sink = FakeTransport()
        c = capture(led=ledger(sent=len(PLAIN) - 1))
        c.run(MemSource(PLAIN))
        self.assertEqual(code_of(lambda: c.upload(sink, "obj")), "E_BUDGET")
        self.assertEqual(sink.calls, [])

    def test_lost_response_is_unknown_without_retry(self) -> None:
        sink = FakeTransport(put_outcome="lose_response")
        c = capture()
        c.run(MemSource(PLAIN))
        self.assertEqual(code_of(lambda: c.upload(sink, "obj")), "E_UPLOAD_UNKNOWN")
        self.assertEqual(c.state, State.UNKNOWN)
        self.assertEqual(len(sink.calls), 1)
        self.assertEqual(code_of(lambda: c.upload(sink, "obj")), "E_BAD_STATE")
        self.assertEqual(len(sink.calls), 1)

    def test_sink_exception_text_never_escapes(self) -> None:
        class Evil(FakeTransport):
            def put(self, name: str, data: memoryview) -> None:
                raise RuntimeError(CANARY)

        c = capture()
        c.run(MemSource(PLAIN))
        self.assertEqual(code_of(lambda: c.upload(Evil(), "obj")), "E_UPLOAD_UNKNOWN")

    def test_verify_detects_tamper_and_truncation(self) -> None:
        for mutate in (
            lambda b: b[:-1],
            lambda b: b[:-1] + bytes([b[-1] ^ 1]),
            lambda b: b + b"x",
        ):
            sink = FakeTransport()
            c = capture()
            c.run(MemSource(PLAIN))
            c.upload(sink, "obj")
            sink.objects["obj"] = mutate(sink.objects["obj"])
            self.assertEqual(code_of(lambda c=c, sink=sink: c.verify(sink, "obj")), "E_VERIFY_MISMATCH")
            self.assertEqual(c.state, State.FAILED)

    def test_verify_lost_read_is_unknown_and_budgeted(self) -> None:
        sink = FakeTransport(get_outcome="lose")
        led = ledger()
        c = capture(led=led)
        c.run(MemSource(PLAIN))
        c.upload(sink, "obj")
        before = led.remaining()["read"]
        self.assertEqual(code_of(lambda: c.verify(sink, "obj")), "E_VERIFY_UNKNOWN")
        self.assertEqual(c.state, State.UNKNOWN)
        self.assertEqual(before - led.remaining()["read"], c.cipher_len)

    def test_verify_budget_refusal_keeps_state_and_skips_call(self) -> None:
        sink = FakeTransport()
        c = capture(led=ledger(read=0))
        c.run(MemSource(PLAIN))
        c.upload(sink, "obj")
        calls = len(sink.calls)
        self.assertEqual(code_of(lambda: c.verify(sink, "obj")), "E_BUDGET")
        self.assertEqual(c.state, State.UPLOADED)
        self.assertEqual(len(sink.calls), calls)

    def test_no_reset_from_terminal_states(self) -> None:
        c = capture()
        code_of(lambda: c.run(MemSource(PLAIN, producer_status=1)))
        self.assertEqual(code_of(lambda: c.run(MemSource(PLAIN))), "E_BAD_STATE")

    def test_fake_transport_surface_and_names(self) -> None:
        public = {n for n in dir(FakeTransport) if not n.startswith("_") and callable(getattr(FakeTransport, n))}
        self.assertEqual(public, {"put", "get", "head"})
        sink = FakeTransport()
        for bad in ("../x", "a b", "", "X" * 65, 5):
            self.assertEqual(code_of(lambda b=bad: sink.put(b, b"x")), "E_BAD_NAME")  # type: ignore[arg-type]

    def test_unexpected_finish_shape_rejected(self) -> None:
        class Odd(MemSource):
            def finish(self) -> Finish:  # type: ignore[override]
                return object()  # type: ignore[return-value]

        c = capture()
        self.assertEqual(code_of(lambda: c.run(Odd(PLAIN))), "E_BAD_SOURCE")


class RestoreFlow(unittest.TestCase):
    def stored(self, put: bytes = PLAIN) -> tuple[FakeTransport, Expected]:
        sink = FakeTransport()
        c = capture()
        c.run(MemSource(put))
        c.upload(sink, "obj")
        return sink, Expected(c.cipher_sha256, c.cipher_len, c.dump_sha256)

    def run_restore(
        self,
        sink: FakeTransport,
        exp: Expected,
        decrypt: MemDecrypt | None = None,
        consumer: MemConsumer | None = None,
        led: Ledger | None = None,
    ) -> tuple[object, MemConsumer]:
        consumer = consumer or MemConsumer()
        res = restore(
            sink=sink,
            name="obj",
            ledger=led or ledger(),
            expected=exp,
            decrypt=decrypt or MemDecrypt(),
            consumer=consumer,
        )
        return res, consumer

    def test_all_gates_pass(self) -> None:
        sink, exp = self.stored()
        res, consumer = self.run_restore(sink, exp)
        self.assertEqual((res.outcome, res.trusted), ("verified", True))  # type: ignore[attr-defined]
        self.assertEqual(bytes(consumer.data), PLAIN)
        self.assertFalse(consumer.aborted)

    def assert_untrusted(self, res: object, outcome: str, consumer: MemConsumer) -> None:
        self.assertEqual(res.outcome, outcome)  # type: ignore[attr-defined]
        self.assertFalse(res.trusted)  # type: ignore[attr-defined]
        self.assertTrue(consumer.aborted)  # quarantine discarded

    def test_truncation_and_corruption_are_distinct(self) -> None:
        sink, exp = self.stored()
        good = sink.objects["obj"]
        sink.objects["obj"] = good[:-1]
        res, cons = self.run_restore(sink, exp)
        self.assert_untrusted(res, "truncated", cons)
        sink.objects["obj"] = good[:-1] + bytes([good[-1] ^ 1])
        res, cons = self.run_restore(sink, exp)
        self.assert_untrusted(res, "corrupt_ciphertext", cons)
        sink.objects["obj"] = good + b"x"
        res, cons = self.run_restore(sink, exp)
        self.assert_untrusted(res, "corrupt_ciphertext", cons)
        self.assertEqual(bytes(cons.data), b"")  # nothing reached the consumer

    def test_capacity_is_inconclusive_not_corrupt(self) -> None:
        sink, exp = self.stored()
        res, cons = self.run_restore(sink, exp, consumer=MemConsumer(capacity=10))
        self.assert_untrusted(res, "inconclusive_capacity", cons)

    def test_decrypt_and_consumer_statuses(self) -> None:
        sink, exp = self.stored()
        res, cons = self.run_restore(sink, exp, decrypt=MemDecrypt(status=1))
        self.assert_untrusted(res, "decrypt_failed", cons)
        res, cons = self.run_restore(sink, exp, consumer=MemConsumer(status=3))
        self.assert_untrusted(res, "consumer_failed", cons)
        for bad in (True, False, None, 0.0):
            res, cons = self.run_restore(sink, exp, decrypt=MemDecrypt(status=bad))  # type: ignore[arg-type]
            self.assert_untrusted(res, "malformed_status", cons)
            res, cons = self.run_restore(sink, exp, consumer=MemConsumer(status=bad))  # type: ignore[arg-type]
            self.assert_untrusted(res, "malformed_status", cons)

    def test_plaintext_sha_mismatch(self) -> None:
        sink, exp = self.stored()
        wrong = Expected(exp.cipher_sha256, exp.cipher_len, sha(b"other"))
        res, cons = self.run_restore(sink, wrong)
        self.assert_untrusted(res, "plaintext_sha_mismatch", cons)

    def test_consumer_exception_does_not_leak_or_trust(self) -> None:
        sink, exp = self.stored()
        res, cons = self.run_restore(sink, exp, consumer=MemConsumer(write_error=RuntimeError(CANARY)))
        self.assert_untrusted(res, "decrypt_failed", cons)
        self.assertNotIn(CANARY, repr(res))

    def test_budget_and_fetch(self) -> None:
        sink, exp = self.stored()
        led = ledger(read=exp.cipher_len - 1)
        before = len(sink.calls)
        res, cons = self.run_restore(sink, exp, led=led)
        self.assert_untrusted(res, "budget_refused", cons)
        self.assertEqual(len(sink.calls), before)
        sink.get_outcome = "lose"
        res, cons = self.run_restore(sink, exp)
        self.assert_untrusted(res, "fetch_failed", cons)

    def test_malformed_expectation_refused(self) -> None:
        sink, exp = self.stored()
        for bad in (
            Expected("zz", exp.cipher_len, exp.dump_sha256),
            Expected(exp.cipher_sha256, True, exp.dump_sha256),  # type: ignore[arg-type]
            Expected(exp.cipher_sha256, 0, exp.dump_sha256),
        ):
            res, cons = self.run_restore(sink, bad)
            self.assert_untrusted(res, "bad_expectation", cons)

    def test_decrypt_buffer_is_cleared(self) -> None:
        sink, exp = self.stored()
        seen: list[memoryview] = []

        class Spy(MemDecrypt):
            def start(self, cipher: memoryview):  # type: ignore[no-untyped-def]
                seen.append(cipher)
                return super().start(cipher)

        self.run_restore(sink, exp, decrypt=Spy())
        with self.assertRaises(ValueError):
            bytes(seen[0])  # view released; underlying buffer no longer exposed


class Preflight(unittest.TestCase):
    META = {
        "physical_db_bytes": 100 * preflight.MIB,
        "largest_relation_bytes": 50 * preflight.MIB,
        "host_mem_available_bytes": 8 * preflight.GIB,
        "production_peak_bytes": 2 * preflight.GIB,
        "relay_memory_cap_bytes": 256 * preflight.MIB,
        "proof_capacity_bytes": 512 * preflight.MIB,
        "cost_budget_usd": Decimal("0.82822504064"),
        "logical_bound_bytes": None,
    }

    def meta(self, **kw: object) -> dict[str, object]:
        return {**self.META, **kw}

    def test_spec_costs_exact(self) -> None:
        self.assertEqual(preflight.cost_for(256 * preflight.MIB), Decimal("0.09662508672"))
        self.assertEqual(preflight.cost_for(3 * preflight.GIB), Decimal("0.82822504064"))
        self.assertEqual(preflight.max_bytes_for_budget(Decimal("0.09662508672")), 256 * preflight.MIB)

    def test_budget_search_boundaries(self) -> None:
        n = 10**8
        budget = preflight.cost_for(n)
        self.assertEqual(preflight.max_bytes_for_budget(budget), n)
        self.assertEqual(preflight.max_bytes_for_budget(budget - Decimal("1e-12")) < n, True)
        self.assertEqual(preflight.max_bytes_for_budget(Decimal("0.01")), 0)
        for bad in (0.5, "1", Decimal("NaN"), Decimal("-1")):
            self.assertEqual(code_of(lambda b=bad: preflight.max_bytes_for_budget(b)), "E_BAD_INT")  # type: ignore[arg-type]

    def test_unknown_bound_cannot_approve(self) -> None:
        res = preflight.evaluate(self.meta())
        self.assertEqual(res.decision, "cannot_approve")
        self.assertEqual(res.next_step, "separately_authorized_count_discard_pass_service_up")
        self.assertEqual(res.cap_bytes, 256 * preflight.MIB)  # min(cost, relay, proof)
        self.assertFalse(res.live_permitted)

    def test_cap_is_minimum_of_three(self) -> None:
        r = preflight.evaluate(self.meta(proof_capacity_bytes=10 * preflight.MIB))
        self.assertEqual(r.cap_bytes, 10 * preflight.MIB)
        r = preflight.evaluate(
            self.meta(relay_memory_cap_bytes=4 * preflight.GIB, proof_capacity_bytes=4 * preflight.GIB)
        )
        self.assertEqual(r.cap_bytes, preflight.max_bytes_for_budget(Decimal("0.82822504064")))

    def test_bound_vs_cap_exact(self) -> None:
        cap = 256 * preflight.MIB
        self.assertEqual(preflight.evaluate(self.meta(logical_bound_bytes=cap)).decision, "candidate")
        r = preflight.evaluate(self.meta(logical_bound_bytes=cap + 1))
        self.assertEqual((r.decision, r.reasons), ("refuse", ("logical_bound_exceeds_cap",)))
        self.assertFalse(preflight.evaluate(self.meta(logical_bound_bytes=cap)).live_permitted)

    def test_missing_unknown_malformed_refuse(self) -> None:
        for key in self.META:
            m = dict(self.META)
            del m[key]
            self.assertEqual(preflight.evaluate(m).reasons, ("missing_field",))
        self.assertEqual(preflight.evaluate(self.meta(extra=1)).reasons, ("unknown_field",))
        for bad in (True, -1, 1.5, "5", None):
            self.assertEqual(preflight.evaluate(self.meta(physical_db_bytes=bad)).reasons, ("malformed_field",))
        self.assertEqual(preflight.evaluate(self.meta(cost_budget_usd=0.5)).reasons, ("malformed_field",))
        self.assertEqual(
            preflight.evaluate(self.meta(largest_relation_bytes=10**12)).reasons, ("inconsistent_metadata",)
        )

    def test_proof_floor_and_host_memory(self) -> None:
        self.assertEqual(preflight.proof_pgdata_floor(10), 13 + 256 * preflight.MIB)
        self.assertEqual(preflight.proof_pgdata_floor(11), 15 + 256 * preflight.MIB)  # ceil(14.3)
        limit = (preflight.GIB - 256 * preflight.MIB) * 10 // 13  # largest db whose floor fits 1 GiB
        ok = preflight.evaluate(self.meta(physical_db_bytes=limit, largest_relation_bytes=0))
        self.assertEqual(ok.decision, "cannot_approve")
        over = preflight.evaluate(self.meta(physical_db_bytes=limit + 1, largest_relation_bytes=0))
        self.assertIn("proof_floor_exceeds_tmpfs", over.reasons)
        need = preflight.host_memory_needed(2 * preflight.GIB)
        self.assertEqual(need, 5 * preflight.GIB)
        self.assertEqual(preflight.evaluate(self.meta(host_mem_available_bytes=need)).decision, "cannot_approve")
        short = preflight.evaluate(self.meta(host_mem_available_bytes=need - 1))
        self.assertEqual(short.reasons, ("host_memory_insufficient",))

    def test_zero_capacity_refuses(self) -> None:
        self.assertIn("no_capacity", preflight.evaluate(self.meta(proof_capacity_bytes=0)).reasons)

    def test_sql_is_read_only_metadata(self) -> None:
        sql = preflight.READ_ONLY_SQL
        for needle in (
            "BEGIN READ ONLY",
            "statement_timeout = '5s'",
            "lock_timeout = '2s'",
            "pg_database_size",
            "LIMIT",
        ):
            self.assertIn(needle, sql)
        for banned in ("INSERT", "UPDATE", "DELETE", "COPY", "DROP", "c.relname,", "SELECT *"):
            self.assertNotIn(banned, sql.upper().replace("RELKIND", "") if banned == "UPDATE" else sql)


class DisabledEntryPoints(unittest.TestCase):
    ROOT = str(Path(__file__).resolve().parent.parent)

    def run_cli(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        return main(list(args), out), out.getvalue()

    def test_everything_but_selftest_is_disabled(self) -> None:
        attempts = (
            [],
            ["live"],
            ["capture", "--host", CANARY],
            ["restore", f"/nonexistent/{CANARY}"],
            ["upload", "--endpoint", CANARY],
            ["selftest", "--enable-live"],
            ["selftest", CANARY],
            ["--live"],
            ["preflight"],
        )
        for args in attempts:
            code, text = self.run_cli(*args)
            self.assertEqual((code, text), (2, "E_DISABLED\n"), args)

    def test_selftest_runs(self) -> None:
        self.assertEqual(self.run_cli("selftest"), (0, "selftest ok\n"))

    def test_disabled_refuses_before_opening_anything(self) -> None:
        import builtins
        import socket

        def boom(*_a: object, **_k: object) -> None:
            raise AssertionError("touched I/O")

        real = (builtins.open, socket.socket, subprocess.Popen, os.system)
        builtins.open, socket.socket, subprocess.Popen, os.system = boom, boom, boom, boom  # type: ignore[assignment]
        try:
            self.assertEqual(self.run_cli("capture", "/etc/passwd")[0], 2)
        finally:
            builtins.open, socket.socket, subprocess.Popen, os.system = real  # type: ignore[assignment]

    def subprocess_run(self, args: list[str], code: str | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "SYNTHETIC_CANARY": CANARY}
        cmd = [sys.executable, *(["-c", code] if code else ["-m", "backup_probe", *args])]
        return subprocess.run(cmd, cwd=self.ROOT, env=env, capture_output=True, text=True, timeout=20, check=False)

    def test_process_output_has_no_canary(self) -> None:
        proc = self.subprocess_run(["capture", f"--password={CANARY}", f"/{CANARY}"])
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (2, "E_DISABLED\n", ""))
        ok = self.subprocess_run(["selftest"])
        self.assertEqual((ok.returncode, ok.stdout, ok.stderr), (0, "selftest ok\n", ""))

    def test_import_has_no_side_effects(self) -> None:
        code = (
            "import builtins, os, socket, subprocess\n"
            "def boom(*a, **k): raise SystemExit('side effect')\n"
            "builtins.open = boom; socket.socket = boom; subprocess.Popen = boom; os.system = boom\n"
            "import backup_probe, backup_probe.core, backup_probe.capture, backup_probe.restore, "
            "backup_probe.preflight, backup_probe.fixtures, backup_probe.__main__\n"
            "print('imported')\n"
        )
        proc = self.subprocess_run([], code)
        self.assertEqual((proc.returncode, proc.stdout.strip(), proc.stderr), (0, "imported", ""))

    def test_safe_code_hides_messages(self) -> None:
        self.assertEqual(safe_code(RuntimeError(CANARY)), "E_INTERNAL")
        self.assertEqual(safe_code(ProbeError("not-a-code")), "E_INTERNAL")
        self.assertEqual(safe_code(CapacityError(CANARY)), "E_INTERNAL")
        self.assertNotIn(CANARY, str(ProbeError(CANARY)))


if __name__ == "__main__":
    unittest.main()
