"""Boat-only synthetic integration harness. NOT RUN ON A WORKSTATION.

Throwaway PostgreSQL 16 cluster (initdb/pg_ctl, unix socket only, no TCP) holds synthetic
rows. Real pg_dump -> real OpenSSL CMS encrypt -> prototype capture -> fake block store ->
same-pass SHA/decrypt -> real pg_restore into a separate quarantine database.
Throwaway keys are generated at run time inside the temp dir and deleted with it. Output is
tool versions, outcome words and counts only; no key, plaintext or ciphertext is printed.

Run through verify_boat.sh (from scripts/): python3 -m backup_probe.synthetic_postgres_restore
"""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .capture import Capture, Finish
from .core import CapacityError, FakeTransport, Ledger, ProbeError, State
from .restore import Expected, restore

PG_BIN = os.environ.get("BACKUP_PROBE_PG_BIN", "/usr/lib/postgresql/16/bin")
PORT = "54329"
CAP = 64 * 2**20
DEADLINE_S = 540
STEP_S = 60
CHUNK = 65536
ROWS_A, ROWS_B = 5000, 2000
COUNT_SQL = (
    "SELECT (SELECT count(*) FROM items) || '/' || (SELECT count(*) FROM notes) || ' ' || "
    "md5((SELECT string_agg(id || label || payload, ',' ORDER BY id) FROM items) || "
    "(SELECT string_agg(id || body, ',' ORDER BY id) FROM notes))"
)


class Env:
    """Owns the temp dir, the throwaway cluster and the throwaway keys."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bp-synth-"))
        self.sock = self.tmp / "s"
        self.sock.mkdir()
        self.env = {
            "PATH": f"{PG_BIN}:/usr/bin:/bin",
            "LC_ALL": "C",
            "PGHOST": str(self.sock),
            "PGPORT": PORT,
            "PGUSER": "synth",
        }
        self.started = False
        self.cert = self.tmp / "a.crt"
        self.key = self.tmp / "a.key"
        self.other_cert = self.tmp / "b.crt"
        self.other_key = self.tmp / "b.key"

    def run(self, cmd: list[str], *, check: bool = True) -> str:
        proc = subprocess.run(cmd, env=self.env, capture_output=True, timeout=STEP_S, check=False)
        if check and proc.returncode != 0:
            raise RuntimeError(f"step failed rc={proc.returncode}: {Path(cmd[0]).name}")
        return proc.stdout.decode("utf-8", "replace").strip()

    def psql(self, db: str, sql: str, *, check: bool = True) -> str:
        return self.run(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-t", "-A", "-d", db, "-c", sql], check=check)

    def __enter__(self) -> Env:
        data = self.tmp / "data"
        self.run([f"{PG_BIN}/initdb", "-D", str(data), "-A", "trust", "-U", "synth", "--no-sync"])
        opts = f"-k {self.sock} -c listen_addresses='' -p {PORT} -c fsync=off"
        self.started = True
        self.run(
            [f"{PG_BIN}/pg_ctl", "-D", str(data), "-o", opts, "-l", str(self.tmp / "pg.log"), "-w", "-t", "60", "start"]
        )
        for crt, key in ((self.cert, self.key), (self.other_cert, self.other_key)):
            self.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key),
                    "-out",
                    str(crt),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=synthetic",
                ]
            )
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.started:
            subprocess.run(
                [f"{PG_BIN}/pg_ctl", "-D", str(self.tmp / "data"), "-m", "immediate", "-w", "-t", "30", "stop"],
                env=self.env,
                capture_output=True,
                timeout=STEP_S,
                check=False,
            )
        if self.tmp.name.startswith("bp-synth-") and self.tmp.parent == Path(tempfile.gettempdir()):
            shutil.rmtree(self.tmp, ignore_errors=True)  # only this run's own directory


def _kill(*procs: subprocess.Popen[bytes]) -> None:
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass


class PipelineSource:
    """pg_dump | openssl cms -encrypt. Reports statuses and independent SHAs at finish()."""

    def __init__(self, env: Env, db: str, cert: Path) -> None:
        self.dump = subprocess.Popen(
            ["pg_dump", "-Fc", "-d", db], env=env.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self.enc = subprocess.Popen(
            ["openssl", "cms", "-encrypt", "-binary", "-aes256", "-outform", "DER", "-recip", str(cert)],
            env=env.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.dsha, self.csha, self.clen = hashlib.sha256(), hashlib.sha256(), 0
        self.watchdog = threading.Timer(STEP_S * 2, self.cancel)
        self.watchdog.daemon = True
        self.watchdog.start()
        self.pump = threading.Thread(target=self._pump, daemon=True)
        self.pump.start()

    def _pump(self) -> None:
        assert self.dump.stdout is not None and self.enc.stdin is not None
        try:
            while block := self.dump.stdout.read(CHUNK):
                self.dsha.update(block)
                self.enc.stdin.write(block)
        except OSError:
            pass
        finally:
            try:
                self.enc.stdin.close()
            except OSError:
                pass

    def read(self, n: int) -> bytes:
        assert self.enc.stdout is not None
        block = self.enc.stdout.read(n)
        self.csha.update(block)
        self.clen += len(block)
        return block

    def finish(self) -> Finish:
        self.pump.join(STEP_S)
        pw, ew = self.dump.wait(timeout=STEP_S), self.enc.wait(timeout=STEP_S)
        self.watchdog.cancel()
        return Finish(pw, ew, self.dsha.hexdigest(), self.csha.hexdigest(), self.clen)

    def cancel(self) -> None:
        _kill(self.dump, self.enc)


class CmsStream:
    def __init__(self, env: Env, cipher: memoryview, cert: Path, key: Path) -> None:
        self.proc = subprocess.Popen(
            ["openssl", "cms", "-decrypt", "-binary", "-inform", "DER", "-recip", str(cert), "-inkey", str(key)],
            env=env.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.feeder = threading.Thread(target=self._feed, args=(cipher,), daemon=True)
        self.feeder.start()

    def _feed(self, cipher: memoryview) -> None:
        assert self.proc.stdin is not None
        try:
            for i in range(0, len(cipher), CHUNK):
                self.proc.stdin.write(cipher[i : i + CHUNK])
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    def read(self, n: int) -> bytes:
        assert self.proc.stdout is not None
        return self.proc.stdout.read(n)

    def finish(self) -> int:
        self.feeder.join(STEP_S)
        return self.proc.wait(timeout=STEP_S)

    def abort(self) -> None:
        _kill(self.proc)


class CmsDecrypt:
    def __init__(self, env: Env, cert: Path, key: Path) -> None:
        self.env, self.cert, self.key = env, cert, key

    def start(self, cipher: memoryview) -> CmsStream:
        return CmsStream(self.env, cipher, self.cert, self.key)


class PgRestoreConsumer:
    """pg_restore into a quarantine database. abort() kills the process only."""

    def __init__(self, env: Env, db: str, limit: int | None = None) -> None:
        self.proc = subprocess.Popen(
            ["pg_restore", "--no-owner", "--exit-on-error", "-d", db],
            env=env.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.limit, self.total = limit, 0

    def write(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if self.limit is not None and self.total > self.limit:
            raise CapacityError
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(chunk)
        except OSError:
            pass  # pg_restore already exited; finish() reports its status

    def finish(self) -> int:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        return self.proc.wait(timeout=STEP_S)

    def abort(self) -> None:
        _kill(self.proc)


class Harness:
    def __init__(self, env: Env) -> None:
        self.env = env
        self.failures: list[str] = []
        self.n = 0
        self.src_counts = ""

    def seed(self) -> None:
        e = self.env
        e.psql("postgres", "CREATE DATABASE src_synth")
        e.psql(
            "src_synth",
            "CREATE TABLE items(id int PRIMARY KEY, label text, payload text);"
            "CREATE TABLE notes(id int PRIMARY KEY, body text);"
            f"INSERT INTO items SELECT g, 'label-' || g, md5(g::text) FROM generate_series(1, {ROWS_A}) g;"
            f"INSERT INTO notes SELECT g, repeat(md5(g::text), 4) FROM generate_series(1, {ROWS_B}) g;",
        )
        self.src_counts = e.psql("src_synth", COUNT_SQL)

    def new_ledger(self) -> Ledger:
        return Ledger(cipher_cap=CAP, max_requests=8, sent_bytes=CAP, read_bytes=2 * CAP)

    def stored(self, db: str = "src_synth", cert: Path | None = None) -> tuple[Capture, FakeTransport, str | None]:
        """Capture and upload. Returns (capture, sink, error code or None)."""
        cap = Capture(cipher_cap=CAP, ledger=self.new_ledger(), timeout_s=DEADLINE_S)
        sink = FakeTransport()
        try:
            cap.run(PipelineSource(self.env, db, cert or self.env.cert))
            cap.upload(sink, "obj")
        except ProbeError as exc:
            return cap, sink, exc.code
        return cap, sink, None

    def restore_into(
        self,
        cap: Capture,
        sink: FakeTransport,
        *,
        key: Path | None = None,
        limit: int | None = None,
        dump_sha: str | None = None,
    ) -> tuple[str, str]:
        self.n += 1
        db = f"quar_{self.n}"
        self.env.psql("postgres", f"CREATE DATABASE {db}")
        cert = self.env.other_cert if key else self.env.cert
        res = restore(
            sink=sink,
            name="obj",
            ledger=self.new_ledger(),
            expected=Expected(cap.cipher_sha256, cap.cipher_len, dump_sha or cap.dump_sha256),
            decrypt=CmsDecrypt(self.env, cert, key or self.env.key),
            consumer=PgRestoreConsumer(self.env, db, limit),
        )
        counts = self.env.psql(db, COUNT_SQL, check=False) if res.trusted else ""
        self.env.psql("postgres", f"DROP DATABASE {db}", check=False)  # quarantine always discarded here
        return res.outcome, counts

    def check(self, name: str, ok: bool, detail: str) -> None:
        print(f"scenario {name}: {detail} -> {'ok' if ok else 'FAIL'}", flush=True)
        if not ok:
            self.failures.append(name)

    def run_all(self) -> None:
        e = self.env
        self.seed()
        print(f"source rows: {self.src_counts.split()[0]}", flush=True)

        cap, sink, err = self.stored()
        self.check(
            "capture_upload",
            err is None and cap.state is State.UPLOADED,
            f"state={cap.state.value} bytes={cap.cipher_len}",
        )
        cap.verify(sink, "obj")
        self.check("verify_readback", cap.state is State.VERIFIED, f"state={cap.state.value}")
        outcome, counts = self.restore_into(cap, sink)
        content = "match" if counts == self.src_counts else "DIFFER"
        self.check(
            "restore_roundtrip",
            outcome == "verified" and counts == self.src_counts,
            f"outcome={outcome} counts={counts.split(' ')[0]} content={content}",
        )

        good = sink.objects["obj"]
        sink.objects["obj"] = good[:-1] + bytes([good[-1] ^ 1])
        outcome, _ = self.restore_into(cap, sink)
        self.check("tampered_ciphertext", outcome == "corrupt_ciphertext", f"outcome={outcome}")
        sink.objects["obj"] = good[: len(good) // 2]
        outcome, _ = self.restore_into(cap, sink)
        self.check("truncated_ciphertext", outcome == "truncated", f"outcome={outcome}")
        sink.objects["obj"] = good

        outcome, _ = self.restore_into(cap, sink, dump_sha="0" * 64)
        self.check("wrong_plaintext_sha", outcome == "plaintext_sha_mismatch", f"outcome={outcome}")
        outcome, _ = self.restore_into(cap, sink, key=e.other_key)
        self.check("wrong_key_decrypt", outcome == "decrypt_failed", f"outcome={outcome}")
        outcome, _ = self.restore_into(cap, sink, limit=1000)
        self.check("capacity_inconclusive", outcome == "inconclusive_capacity", f"outcome={outcome}")

        cap2, sink2, err = self.stored(db="no_such_db")
        self.check(
            "failing_producer",
            err == "E_PRODUCER" and cap2.state is State.FAILED and sink2.calls == [],
            f"code={err} sink_calls={len(sink2.calls)}",
        )
        cap3, sink3, err = self.stored(cert=e.tmp / "missing.crt")
        self.check(
            "failing_encrypt",
            err in ("E_ENCRYPT", "E_PRODUCER") and cap3.state is State.FAILED and sink3.calls == [],
            f"code={err} sink_calls={len(sink3.calls)}",
        )

        res = restore(  # failing consumer: restore into a database that does not exist
            sink=sink,
            name="obj",
            ledger=self.new_ledger(),
            expected=Expected(cap.cipher_sha256, cap.cipher_len, cap.dump_sha256),
            decrypt=CmsDecrypt(e, e.cert, e.key),
            consumer=PgRestoreConsumer(e, "no_such_db"),
        )
        self.check("failing_consumer", res.outcome == "consumer_failed" and not res.trusted, f"outcome={res.outcome}")


def _versions(env: Env) -> None:
    for label, cmd in (
        ("python", [sys.executable, "--version"]),
        ("openssl", ["openssl", "version"]),
        ("initdb", [f"{PG_BIN}/initdb", "--version"]),
        ("pg_dump", ["pg_dump", "--version"]),
        ("pg_restore", ["pg_restore", "--version"]),
    ):
        print(f"{label}: {env.run(cmd)}", flush=True)


def main() -> int:
    if os.environ.get("BACKUP_PROBE_BOAT") != "1" or not sys.platform.startswith("linux") or os.geteuid() == 0:
        print("E_DISABLED")
        return 3

    def _alarm(_sig: int, _frame: object) -> None:
        raise TimeoutError

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(DEADLINE_S)
    try:
        with Env() as env:
            _versions(env)
            harness = Harness(env)
            harness.run_all()
    except TimeoutError:
        print("FAIL deadline", flush=True)
        return 1
    except Exception as exc:
        print(f"FAIL {type(exc).__name__}", flush=True)
        return 1
    finally:
        signal.alarm(0)
    print("FAILED: " + ",".join(harness.failures) if harness.failures else "all scenarios ok")
    return 1 if harness.failures else 0


if __name__ == "__main__":
    sys.exit(main())
