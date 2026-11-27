"""Focused stdlib tests. Run from scripts/: python -m unittest backup_probe.test_backup_probe"""

from __future__ import annotations

import contextlib
import io
import math
import os
import subprocess
import sys
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from . import preflight
from . import synthetic_postgres_restore as harness
from .__main__ import main
from .capture import Capture, Finish
from .core import (
    CapacityError,
    FakeTransport,
    HeldBuffer,
    Ledger,
    ObjectTooLarge,
    ProbeError,
    State,
    remaining_after_parts,
    safe_code,
    strict_int,
)
from .fixtures import MemConsumer, MemDecrypt, MemSource, MemStream, fixture_cipher, sha
from .restore import Expected, restore

CANARY = "CANARY-7f3a91"
PLAIN = b"synthetic-row-" * 100  # 1400 bytes
CAP = 4096


def ledger(requests: int = 8, sent: int = CAP, read: int = 2 * CAP, cap: int = CAP) -> Ledger:
    return Ledger(cipher_cap=cap, max_writes=requests, max_reads=requests, sent_bytes=sent, read_bytes=read)


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
        self.assertEqual(led.remaining()["writes"], 8)

    def test_ledger_egress_limit(self) -> None:
        self.assertEqual(code_of(lambda: ledger(read=2 * CAP + 1)), "E_BUDGET_CONFIG")
        self.assertEqual(code_of(lambda: ledger(sent=2 * CAP + 1)), "E_BUDGET_CONFIG")
        ledger(read=2 * CAP)


class LedgerBudget(unittest.TestCase):
    def test_exact_boundaries(self) -> None:
        led = ledger(requests=1, sent=10)
        led.reserve("put", 10)
        self.assertEqual(code_of(lambda: led.reserve("put", 1)), "E_BUDGET")
        led.reserve("head", 0)
        self.assertEqual(code_of(lambda: led.reserve("head", 0)), "E_BUDGET")

    def test_refused_reserve_spends_nothing(self) -> None:
        led = ledger(requests=3, sent=5)
        self.assertEqual(code_of(lambda: led.reserve("put", 6)), "E_BUDGET")
        self.assertEqual(led.remaining(), {"writes": 3, "reads": 3, "sent": 5, "read": 2 * CAP})

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
        self.assertEqual(led.remaining()["writes"], 7)


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
            chunk=65536,
            max_plain_bytes=CAP,
            timeout_s=10,
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
        self.assert_untrusted(res, "consumer_failed", cons)
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
        r = preflight.evaluate(
            self.meta(
                physical_db_bytes=preflight.MIB,
                largest_relation_bytes=0,
                relay_memory_cap_bytes=4 * preflight.GIB,
                proof_capacity_bytes=300 * preflight.MIB,
            )
        )
        self.assertEqual(r.cap_bytes, 300 * preflight.MIB)
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
        big = {"largest_relation_bytes": 0, "proof_capacity_bytes": 2 * preflight.GIB}
        ok = preflight.evaluate(self.meta(physical_db_bytes=limit, **big))
        self.assertEqual(ok.decision, "cannot_approve")
        over = preflight.evaluate(self.meta(physical_db_bytes=limit + 1, **big))
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

    def test_untrusted_cli_input_is_rejected_and_not_echoed(self) -> None:
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


class Quotes(unittest.TestCase):
    B = preflight.REQUEST_BLOCK

    def test_block_boundaries_distinguish_the_model(self) -> None:
        for n, blocks in ((0, 0), (1, 1), (self.B - 1, 1), (self.B, 1), (self.B + 1, 2), (2 * self.B + 1, 3)):
            q = preflight.quote(n)
            self.assertEqual((q.blocks, q.writes, q.reads), (blocks, 2 * (blocks + 10), 4 * (blocks + 10)))
            self.assertEqual(
                q.request_usd, Decimal(q.writes) * Decimal("0.05") / 10000 + Decimal(q.reads) * Decimal("0.004") / 10000
            )
        step = preflight.quote(self.B + 1).request_usd - preflight.quote(self.B).request_usd
        self.assertEqual(step, Decimal("0.0000116"))  # one more block = 2 writes + 4 reads
        self.assertEqual(preflight.quote(self.B).request_usd, preflight.quote(self.B - 1).request_usd)

    def test_spec_counts_and_totals(self) -> None:
        q = preflight.quote(256 * preflight.MIB)
        self.assertEqual((q.blocks, q.writes, q.reads), (64, 148, 296))
        self.assertEqual(q.total_usd, Decimal("0.09662508672"))
        q3 = preflight.quote(3 * preflight.GIB)
        self.assertEqual((q3.blocks, q3.writes, q3.reads), (768, 1556, 3112))
        self.assertEqual(q3.total_usd, Decimal("0.82822504064"))
        parts = q.request_usd + q.storage_usd + q.egress_usd + Decimal("0.01") + Decimal("0.02")
        self.assertEqual(parts, q.total_usd)

    def test_ledger_enforces_write_and_read_categories_separately(self) -> None:
        n = 10 * preflight.MIB
        q = preflight.quote(n)
        led = preflight.ledger_for(n)
        for _ in range(q.writes):
            led.reserve("put", 0)
        self.assertEqual(code_of(lambda: led.reserve("put", 0)), "E_BUDGET")
        self.assertEqual(led.remaining()["reads"], q.reads)  # reads cannot be spent as writes
        for _ in range(q.reads - 1):
            led.reserve("head", 0)
        led.reserve("get", 0)
        self.assertEqual(code_of(lambda: led.reserve("head", 0)), "E_BUDGET")
        led2 = preflight.ledger_for(n)
        led2.reserve("put", n)
        self.assertEqual(code_of(lambda: led2.reserve("put", 1)), "E_BUDGET")
        led2.reserve("get", 2 * n)
        self.assertEqual(code_of(lambda: led2.reserve("get", 1)), "E_BUDGET")

    def test_head_reserves_zero_bytes_in_its_category(self) -> None:
        led = ledger()
        before = led.remaining()
        led.reserve("head", 0)
        after = led.remaining()
        self.assertEqual((after["reads"], after["read"], after["sent"], after["writes"]),
                         (before["reads"] - 1, before["read"], before["sent"], before["writes"]))  # fmt: skip
        self.assertEqual(code_of(lambda: led.reserve("head", 1)), "E_BAD_INT")

    def test_evaluate_quote_matches_cap(self) -> None:
        res = preflight.evaluate(Preflight.META)
        assert res.quote is not None
        self.assertEqual(res.quote, preflight.quote(res.cap_bytes))


class ProofCapacity(unittest.TestCase):
    def test_floor_must_fit_supplied_proof_capacity(self) -> None:
        meta = {**Preflight.META}
        floor = preflight.proof_pgdata_floor(meta["physical_db_bytes"])  # type: ignore[arg-type]
        ok = preflight.evaluate({**meta, "proof_capacity_bytes": floor})
        self.assertEqual(ok.decision, "cannot_approve")
        short = preflight.evaluate({**meta, "proof_capacity_bytes": floor - 1})
        self.assertEqual((short.decision, short.reasons), ("refuse", ("proof_floor_exceeds_proof_capacity",)))
        tiny = preflight.evaluate({**meta, "proof_capacity_bytes": 1, "logical_bound_bytes": 1})
        self.assertEqual(tiny.decision, "refuse")  # a tiny proof capacity can never be a candidate


class CaptureTimeouts(unittest.TestCase):
    def test_non_finite_and_bad_timeouts_refused(self) -> None:
        for bad in (math.inf, -math.inf, math.nan, True, 0, -1, "1", None):
            self.assertEqual(
                code_of(lambda b=bad: Capture(cipher_cap=CAP, ledger=ledger(), timeout_s=b)),  # type: ignore[arg-type]
                "E_BAD_INT",
            )

    def clocked(self) -> tuple[list[float], Capture]:
        now = [0.0]
        c = Capture(cipher_cap=CAP, ledger=ledger(), timeout_s=10, clock=lambda: now[0])
        return now, c

    def test_late_finish_cannot_reach_held(self) -> None:
        now, c = self.clocked()

        class Late(MemSource):
            def finish(self) -> Finish:
                now[0] = 100.0
                return super().finish()

        self.assertEqual(code_of(lambda: c.run(Late(PLAIN))), "E_TIMEOUT")
        self.assertEqual(c.state, State.FAILED)

    def test_late_read_is_not_kept(self) -> None:
        now, c = self.clocked()

        class Late(MemSource):
            def read(self, n: int) -> bytes:
                now[0] = 100.0
                return super().read(n)

        self.assertEqual(code_of(lambda: c.run(Late(PLAIN))), "E_TIMEOUT")
        self.assertEqual(len(c._buf), 0)  # type: ignore[arg-type]

    def test_cancel_after_read_and_finish(self) -> None:
        ev = threading.Event()

        class Cancels(MemSource):
            def finish(self) -> Finish:
                ev.set()
                return super().finish()

        c = capture()
        self.assertEqual(code_of(lambda: c.run(Cancels(PLAIN), cancel=ev)), "E_CANCELLED")
        self.assertEqual(c.state, State.FAILED)

    def test_bad_clock_values_refused(self) -> None:
        for bad in (math.nan, math.inf, "1", None, True):
            c = Capture(cipher_cap=CAP, ledger=ledger(), timeout_s=10, clock=lambda b=bad: b)  # type: ignore[misc]
            self.assertEqual(code_of(lambda c=c: c.run(MemSource(PLAIN))), "E_BAD_SOURCE")

    def test_oversized_chunk_from_source_refused(self) -> None:
        class Big(MemSource):
            def read(self, n: int) -> bytes:
                return b"x" * (n + 1)

        c = capture(chunk=100)
        self.assertEqual(code_of(lambda: c.run(Big(PLAIN))), "E_BAD_SOURCE")
        self.assertEqual(len(c._buf), 0)  # type: ignore[arg-type]


class BoundedGet(unittest.TestCase):
    def test_sink_refuses_oversize_before_returning(self) -> None:
        sink = FakeTransport()
        sink.objects["o"] = b"x" * 11
        with self.assertRaises(ObjectTooLarge):
            sink.get("o", 10)
        self.assertEqual(sink.get("o", 11), b"x" * 11)
        self.assertEqual(sink.calls, [("get", 10), ("get", 11)])  # never more than the limit
        for bad in (True, -1, 1.5, None):
            self.assertEqual(code_of(lambda b=bad: sink.get("o", b)), "E_BAD_INT")  # type: ignore[arg-type]

    def test_verify_oversize_is_mismatch_and_reservation_kept(self) -> None:
        led = ledger()
        sink = FakeTransport()
        c = capture(led=led)
        c.run(MemSource(PLAIN))
        c.upload(sink, "obj")
        sink.objects["obj"] += b"x"
        before = led.remaining()["read"]
        self.assertEqual(code_of(lambda: c.verify(sink, "obj")), "E_VERIFY_MISMATCH")
        self.assertEqual(c.state, State.FAILED)
        self.assertEqual(before - led.remaining()["read"], c.cipher_len)
        self.assertEqual(sink.calls[-1], ("get", c.cipher_len))

    def test_restore_oversize_is_corrupt_and_reservation_kept(self) -> None:
        sink = FakeTransport()
        c = capture()
        c.run(MemSource(PLAIN))
        c.upload(sink, "obj")
        sink.objects["obj"] += b"x" * 1000
        led = ledger()
        before = led.remaining()["read"]
        res = restore(
            sink=sink, name="obj", ledger=led, expected=Expected(c.cipher_sha256, c.cipher_len, c.dump_sha256),
            decrypt=MemDecrypt(), consumer=MemConsumer(), chunk=4096, max_plain_bytes=CAP, timeout_s=10,
        )  # fmt: skip
        self.assertEqual((res.outcome, res.trusted), ("corrupt_ciphertext", False))
        self.assertEqual(before - led.remaining()["read"], c.cipher_len)
        self.assertEqual(sink.calls[-1], ("get", c.cipher_len))


class CreateOnlyPut(unittest.TestCase):
    def test_existing_object_is_never_modified(self) -> None:
        sink = FakeTransport()
        sink.put("o", b"old-ciphertext")
        self.assertEqual(code_of(lambda: sink.put("o", b"new")), "E_EXISTS")
        for outcome in ("partial", "lose_response", "error"):
            sink.put_outcome = outcome
            with self.assertRaises(ProbeError):  # refused up front, whatever the injected outcome
                sink.put("o", b"replacement-bytes")
            self.assertEqual(sink.objects["o"], b"old-ciphertext")

    def test_duplicate_name_upload_is_unknown_and_keeps_old_bytes(self) -> None:
        sink = FakeTransport()
        sink.objects["obj"] = b"previous"
        c = capture()
        c.run(MemSource(PLAIN))
        self.assertEqual(code_of(lambda: c.upload(sink, "obj")), "E_UPLOAD_UNKNOWN")
        self.assertEqual((c.state, sink.objects["obj"]), (State.UNKNOWN, b"previous"))


class InterruptCleanup(unittest.TestCase):
    def test_capture_run_interrupt_clears_and_fails(self) -> None:
        for exc in (KeyboardInterrupt(), SystemExit(3)):
            c = capture()
            src = MemSource(PLAIN, read_error=exc)
            with self.assertRaises(type(exc)):
                c.run(src)
            self.assertEqual(c.state, State.FAILED)
            self.assertTrue(src.cancelled)
            self.assertFalse(c._buf.valid)  # type: ignore[union-attr]

    def test_upload_interrupt_is_unknown_without_retry(self) -> None:
        for exc in (KeyboardInterrupt(), SystemExit(3)):
            led = ledger()

            class Interrupted(FakeTransport):
                def put(self, name: str, data: memoryview, exc: BaseException = exc) -> None:  # type: ignore[override]
                    self.calls.append(("put", len(data)))
                    raise exc

            sink = Interrupted()
            c = capture(led=led)
            c.run(MemSource(PLAIN))
            with self.assertRaises(type(exc)):
                c.upload(sink, "obj")
            self.assertEqual(c.state, State.UNKNOWN)
            self.assertFalse(c._buf.valid)  # type: ignore[union-attr]
            self.assertEqual(led.remaining()["sent"], CAP - c.cipher_len)  # no refund
            self.assertEqual(code_of(lambda c=c, sink=sink: c.upload(sink, "obj")), "E_BAD_STATE")
            self.assertEqual(len(sink.calls), 1)

    def test_verify_interrupt_is_unknown(self) -> None:
        for exc in (KeyboardInterrupt(), SystemExit(3)):
            sink = FakeTransport()
            led = ledger()
            c = capture(led=led)
            c.run(MemSource(PLAIN))
            c.upload(sink, "obj")

            def boom(name: str, limit: int, exc: BaseException = exc) -> bytes:
                raise exc

            sink.get = boom  # type: ignore[method-assign]
            before = led.remaining()["read"]
            with self.assertRaises(type(exc)):
                c.verify(sink, "obj")
            self.assertEqual(c.state, State.UNKNOWN)
            self.assertEqual(before - led.remaining()["read"], c.cipher_len)

    def stored(self) -> tuple[FakeTransport, Expected]:
        sink = FakeTransport()
        c = capture()
        c.run(MemSource(PLAIN))
        c.upload(sink, "obj")
        return sink, Expected(c.cipher_sha256, c.cipher_len, c.dump_sha256)

    def call(self, sink: FakeTransport, exp: Expected, decrypt: MemDecrypt, consumer: MemConsumer) -> None:
        restore(
            sink=sink, name="obj", ledger=ledger(), expected=exp, decrypt=decrypt, consumer=consumer,
            chunk=65536, max_plain_bytes=CAP, timeout_s=10,
        )  # fmt: skip

    def test_restore_interrupts_abort_everything_and_propagate(self) -> None:
        sink, exp = self.stored()

        class ReadBoom(MemDecrypt):
            def start(self, cipher: memoryview):  # type: ignore[no-untyped-def]
                stream = super().start(cipher)

                def boom(n: int) -> bytes:
                    raise KeyboardInterrupt

                stream.read = boom  # type: ignore[method-assign]
                return stream

        dec = ReadBoom()
        cons = MemConsumer()
        with self.assertRaises(KeyboardInterrupt):
            self.call(sink, exp, dec, cons)
        self.assertTrue(dec.last.aborted and cons.aborted)  # type: ignore[union-attr]

        dec, cons = MemDecrypt(), MemConsumer(write_error=SystemExit(2))
        with self.assertRaises(SystemExit):
            self.call(sink, exp, dec, cons)
        self.assertTrue(dec.last.aborted and cons.aborted)  # type: ignore[union-attr]

        class FinishBoom(MemConsumer):
            def finish(self) -> int:
                raise KeyboardInterrupt

        cons = FinishBoom()
        with self.assertRaises(KeyboardInterrupt):
            self.call(sink, exp, MemDecrypt(), cons)
        self.assertTrue(cons.aborted)

    def test_restore_fetch_interrupt_aborts_consumer_and_keeps_reservation(self) -> None:
        sink, exp = self.stored()

        def boom(name: str, limit: int) -> bytes:
            raise KeyboardInterrupt

        sink.get = boom  # type: ignore[method-assign]
        led, cons = ledger(), MemConsumer()
        before = led.remaining()["read"]
        with self.assertRaises(KeyboardInterrupt):
            restore(
                sink=sink, name="obj", ledger=led, expected=exp, decrypt=MemDecrypt(), consumer=cons,
                chunk=65536, max_plain_bytes=CAP, timeout_s=10,
            )  # fmt: skip
        self.assertTrue(cons.aborted)
        self.assertEqual(before - led.remaining()["read"], exp.cipher_len)


class _Clock:
    """A callable clock that a fixture can jump past a deadline."""

    def __init__(self, now: list[float]) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now[0]

    def jump(self) -> None:
        self.now[0] = 100.0


class RestoreHardening(unittest.TestCase):
    stored = RestoreFlow.stored

    def call(self, sink: FakeTransport, exp: Expected, **kw: object) -> tuple[object, MemConsumer]:
        consumer = kw.pop("consumer", None) or MemConsumer()
        params: dict[str, object] = {"chunk": 65536, "max_plain_bytes": CAP, "timeout_s": 10}
        params.update(kw)
        decrypt = params.pop("decrypt", None) or MemDecrypt()
        res = restore(
            sink=sink,
            name="obj",
            ledger=ledger(),
            expected=exp,
            decrypt=decrypt,
            consumer=consumer,
            **params,  # type: ignore[arg-type]
        )
        return res, consumer  # type: ignore[return-value]

    def test_malformed_parameters_refused_before_any_transfer(self) -> None:
        sink, exp = self.stored()
        calls = len(sink.calls)
        cases: list[dict[str, object]] = (
            [{"chunk": v} for v in (0, True, -1, 1.5, None)]
            + [{"max_plain_bytes": v} for v in (0, True, -1, 1.5)]
            + [{"timeout_s": v} for v in (math.inf, math.nan, True, 0, -1, "1", None)]
        )
        for kw in cases:
            res, cons = self.call(sink, exp, **kw)
            self.assertEqual((res.outcome, res.trusted), ("bad_parameters", False), kw)  # type: ignore[attr-defined]
            self.assertTrue(cons.aborted)
        self.assertEqual(len(sink.calls), calls)

    def test_plaintext_cap_exact_and_refused_before_consumer(self) -> None:
        sink, exp = self.stored()
        res, cons = self.call(sink, exp, max_plain_bytes=len(PLAIN))
        self.assertEqual(res.outcome, "verified")  # type: ignore[attr-defined]
        res, cons = self.call(sink, exp, max_plain_bytes=len(PLAIN) - 1, chunk=1000)
        self.assertEqual((res.outcome, res.trusted), ("inconclusive_capacity", False))  # type: ignore[attr-defined]
        self.assertLessEqual(len(cons.data), len(PLAIN) - 1)
        self.assertEqual(len(cons.data), 1000)  # the piece that would exceed the cap never reached write()
        self.assertTrue(cons.aborted)

    def test_oversized_piece_rejected_before_consumer(self) -> None:
        sink, exp = self.stored()

        class WholeStream(MemStream):
            def read(self, n: int) -> bytes:
                data, self._pos = self._plain[self._pos :], len(self._plain)
                return data

        class Whole(MemDecrypt):
            def start(self, cipher: memoryview) -> MemStream:
                self.last = WholeStream(fixture_cipher(bytes(cipher)), self.status)
                return self.last

        res, cons = self.call(sink, exp, chunk=100, decrypt=Whole())
        self.assertEqual((res.outcome, res.trusted), ("decrypt_failed", False))  # type: ignore[attr-defined]
        self.assertEqual(bytes(cons.data), b"")

    def test_deadline_between_calls(self) -> None:
        sink, exp = self.stored()
        for where in ("read", "finish", "start"):
            now = [0.0]
            clock = _Clock(now)

            class Slow(MemDecrypt):
                def start(self, cipher: memoryview, where: str = where, clock: _Clock = clock):  # type: ignore[no-untyped-def]
                    if where == "start":
                        clock.jump()
                    stream = super().start(cipher)
                    orig_read, orig_finish = stream.read, stream.finish

                    def read(n: int) -> bytes:
                        if where == "read":
                            clock.jump()
                        return orig_read(n)

                    def finish() -> int:
                        if where == "finish":
                            clock.jump()
                        return orig_finish()

                    stream.read, stream.finish = read, finish  # type: ignore[method-assign]
                    return stream

            dec = Slow()
            res, cons = self.call(sink, exp, decrypt=dec, clock=clock)
            self.assertEqual((res.outcome, res.trusted), ("timeout", False), where)  # type: ignore[attr-defined]
            self.assertTrue(dec.last.aborted if where != "finish" else True)  # type: ignore[union-attr]
            if where != "finish":
                self.assertEqual(bytes(cons.data), b"")

    def test_bad_clock_is_timeout(self) -> None:
        sink, exp = self.stored()
        for bad in (math.nan, math.inf, "1", None):
            res, _ = self.call(sink, exp, clock=lambda b=bad: b)  # type: ignore[misc]
            self.assertEqual(res.outcome, "timeout")  # type: ignore[attr-defined]

    def test_capacity_is_not_conflated_with_crypto_or_read_faults(self) -> None:
        sink, exp = self.stored()
        res, _ = self.call(sink, exp, consumer=MemConsumer(capacity=10))
        self.assertEqual(res.outcome, "inconclusive_capacity")  # type: ignore[attr-defined]
        res, _ = self.call(sink, exp, decrypt=MemDecrypt(status=1))
        self.assertEqual(res.outcome, "decrypt_failed")  # type: ignore[attr-defined]
        res, _ = self.call(sink, exp, consumer=MemConsumer(write_error=OSError("disk")))
        self.assertEqual(res.outcome, "consumer_failed")  # type: ignore[attr-defined]


class MutableErrorCode(unittest.TestCase):
    def test_mutated_code_is_revalidated_on_every_use(self) -> None:
        evil_str = type("S", (str,), {})("E_CAP")
        for bad in (CANARY, ["E_CAP"], None, 5, evil_str):
            err = ProbeError("E_CAP")
            err.code = bad  # type: ignore[assignment]
            self.assertEqual(safe_code(err), "E_INTERNAL")
            self.assertEqual(str(err), "E_INTERNAL")
        ok = ProbeError("E_CAP")
        ok.code = "E_BUDGET"
        self.assertEqual(safe_code(ok), "E_BUDGET")
        self.assertEqual(ProbeError(CANARY).code, "E_INTERNAL")  # type: ignore[arg-type]

    def test_mutated_error_from_source_cannot_smuggle_text(self) -> None:
        err = ProbeError("E_CAP")
        err.code = CANARY  # type: ignore[assignment]
        c = capture()
        self.assertEqual(code_of(lambda: c.run(MemSource(PLAIN, read_error=err))), "E_INTERNAL")


class HarnessOrchestration(unittest.TestCase):
    """The real orchestration classes run against a spy Popen. No process, key or network is used."""

    PLAINTEXT = b"PLAINROW-" + CANARY.encode()
    KEYBODY = "KEYBODY-" + CANARY

    class FakeProc:
        def __init__(self, stdout: bytes | None = None, *, alive: bool = False, stubborn: bool = False) -> None:
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(stdout) if stdout is not None else None
            self.stderr = None
            self.returncode: int | None = None if alive else 0
            self.stubborn = stubborn

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            if not self.stubborn:
                self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:
            if self.returncode is None:
                raise subprocess.TimeoutExpired("fake", timeout or 0)
            return self.returncode

    def test_no_plaintext_key_or_canary_reaches_argv_env_or_output(self) -> None:
        seen: list[tuple[list[str], dict[str, str]]] = []

        def fake_popen(argv: list[str], **kw: object) -> object:
            seen.append((list(argv), dict(kw.get("env") or {})))  # type: ignore[call-overload]
            if argv[0] == "pg_dump":
                return self.FakeProc(self.PLAINTEXT)
            if "-encrypt" in argv:
                return self.FakeProc(b"C" * 40)
            if "-decrypt" in argv:
                return self.FakeProc(self.PLAINTEXT)
            return self.FakeProc()

        env = harness.Env()
        out, err = io.StringIO(), io.StringIO()
        try:
            env.key.write_text(self.KEYBODY)  # a key file the harness must only ever name, never read
            with (
                mock.patch.object(harness.subprocess, "Popen", fake_popen),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                led = Ledger(cipher_cap=CAP, max_writes=1, max_reads=2, sent_bytes=CAP, read_bytes=2 * CAP)
                cap = Capture(cipher_cap=CAP, ledger=led, timeout_s=30)
                cap.run(harness.PipelineSource(env, "src_synth", env.cert))
                sink = FakeTransport()
                cap.upload(sink, "obj")
                res = restore(
                    sink=sink, name="obj", ledger=led,
                    expected=Expected(cap.cipher_sha256, cap.cipher_len, sha(self.PLAINTEXT)),
                    decrypt=harness.CmsDecrypt(env, env.cert, env.key),
                    consumer=harness.PgRestoreConsumer(env, "quar_1"),
                    chunk=4096, max_plain_bytes=CAP, timeout_s=30,
                )  # fmt: skip
            self.assertEqual((res.outcome, res.trusted), ("verified", True))
            self.assertEqual(env.cleanup(), [])
        finally:
            env.cleanup()
        self.assertGreaterEqual(len(seen), 4)
        secrets = (self.PLAINTEXT.decode(), CANARY, self.KEYBODY)
        for argv, child_env in seen:
            blob = "\x00".join([*argv, *child_env, *child_env.values()])
            for secret in secrets:
                self.assertNotIn(secret, blob)
            self.assertLessEqual(set(child_env), {"PATH", "LC_ALL", "PGHOST", "PGPORT", "PGUSER"})
        for text in (out.getvalue(), err.getvalue()):
            self.assertNotIn(CANARY, text)
        encrypt = next(a for a, _ in seen if "-encrypt" in a)
        for flag in ("-stream", "-aes256", "-encrypt", "-binary"):
            self.assertIn(flag, encrypt)
        recip = encrypt.index("-recip")
        self.assertEqual(encrypt.count("-recip"), 1)
        self.assertEqual(encrypt[recip + 1], str(env.cert))
        positions = []
        for opt in ("rsa_padding_mode:oaep", "rsa_oaep_md:sha256", "rsa_mgf1_md:sha256"):
            at = encrypt.index(opt)
            self.assertEqual(encrypt[at - 1], "-keyopt")
            positions.append(at)
        self.assertEqual(positions, sorted(positions))
        self.assertGreater(min(positions), recip + 1)  # keyopt applies to the PRECEDING cert
        decrypt_argv = next(a for a, _ in seen if "-decrypt" in a)
        self.assertEqual(decrypt_argv[decrypt_argv.index("-inkey") + 1], str(env.key))  # a path, not contents

    def test_cleanup_reaps_children_threads_and_tmpdir(self) -> None:
        env = harness.Env()
        proc = self.FakeProc(alive=True)
        env.procs.append(proc)  # type: ignore[arg-type]
        env.thread(lambda: env.stop.wait(30))
        self.assertEqual(env.live_children(), 1)
        self.assertEqual(env.cleanup(), [])
        self.assertEqual((proc.poll(), env.tmp.exists(), env.live_children()), (-9, False, 0))
        self.assertEqual(env.cleanup(), [])  # idempotent

    def test_cleanup_reports_what_it_cannot_remove(self) -> None:
        env = harness.Env()
        env.procs.append(self.FakeProc(alive=True, stubborn=True))  # type: ignore[arg-type]
        problems = env.cleanup()
        self.assertIn("process_not_reaped", problems)
        self.assertIn("child_alive", problems)

    def test_init_failure_cleans_up_and_still_raises(self) -> None:
        env = harness.Env()
        with mock.patch.object(harness, "PG_BIN", "/nonexistent-pg-bin"), self.assertRaises(OSError):
            env.__enter__()
        self.assertFalse(env.tmp.exists())
        self.assertEqual(env.failures, [])

    def test_exit_paths_run_cleanup_for_interrupts(self) -> None:
        for exc in (KeyboardInterrupt, SystemExit, harness.Terminated):
            env = harness.Env()
            env.procs.append(self.FakeProc(alive=True))  # type: ignore[arg-type]
            env.__exit__(exc, exc(), None)
            self.assertEqual((env.tmp.exists(), env.live_children(), env.failures), (False, 0, []))


class BrokenPipeStdin:
    def write(self, _data: bytes) -> int:
        raise BrokenPipeError

    def close(self) -> None:
        pass


class PipelineFailures(unittest.TestCase):
    """PipelineSource and run_all failure paths with injected fake children. No real process."""

    Proc = HarnessOrchestration.FakeProc

    def source(self, dump: object, enc: object) -> tuple[harness.Env, harness.PipelineSource]:
        procs = iter([dump, enc])
        env = harness.Env()
        self.addCleanup(env.cleanup)
        with mock.patch.object(harness.subprocess, "Popen", lambda *_a, **_k: next(procs)):
            return env, harness.PipelineSource(env, "src_synth", env.cert)

    def capture(self) -> Capture:
        led = Ledger(cipher_cap=CAP, max_writes=1, max_reads=2, sent_bytes=CAP, read_bytes=2 * CAP)
        return Capture(cipher_cap=CAP, ledger=led, timeout_s=30)

    def test_exited_encrypt_stops_producer_and_reports_encrypt(self) -> None:
        dump = self.Proc(b"D" * 100, alive=True)
        enc = self.Proc(b"")
        enc.returncode = 1
        enc.stdin = BrokenPipeStdin()
        _env, src = self.source(dump, enc)
        cap = self.capture()
        self.assertEqual(code_of(lambda: cap.run(src)), "E_ENCRYPT")
        self.assertEqual(cap.state.value, "FAILED")
        self.assertEqual(dump.returncode, -9)

    def test_hung_producer_is_killed_and_reported_as_timeout(self) -> None:
        dump = self.Proc(b"", alive=True)
        env, src = self.source(dump, self.Proc(b""))
        self.assertEqual(code_of(lambda: self.capture().run(src)), "E_TIMEOUT")
        self.assertEqual(dump.returncode, -9)
        self.assertEqual(env.failures, [])

    def test_unkillable_child_is_recorded_not_hidden(self) -> None:
        dump = self.Proc(b"", alive=True, stubborn=True)
        env, src = self.source(dump, self.Proc(b""))
        self.assertEqual(code_of(lambda: self.capture().run(src)), "E_TIMEOUT")
        self.assertIn("process_not_reaped", env.failures)
        self.assertIn("process_not_reaped", env.cleanup())

    def test_run_all_stops_with_code_when_capture_fails(self) -> None:
        env = harness.Env()
        self.addCleanup(env.cleanup)
        h = harness.Harness(env)
        failed = harness.Session(self.capture(), FakeTransport(), h.new_ledger(), "E_ENCRYPT")
        out = io.StringIO()
        with (
            mock.patch.object(h, "seed", lambda: setattr(h, "src_counts", "5000/2000 x")),
            mock.patch.object(h, "stored", lambda **_k: failed),
            mock.patch.object(h, "cms_algorithms", side_effect=AssertionError("must not run")),
            contextlib.redirect_stdout(out),
        ):
            h.run_all()
        self.assertEqual(h.failures, ["capture_upload"])
        self.assertIn("code=E_ENCRYPT", out.getvalue())
        self.assertNotIn("KeyError", out.getvalue())


if __name__ == "__main__":
    unittest.main()
