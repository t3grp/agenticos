"""Boat-only synthetic integration harness. NOT RUN ON A WORKSTATION.

Throwaway PostgreSQL 16 cluster (initdb/pg_ctl, unix socket only, no TCP) holds synthetic
rows. Real pg_dump -> real OpenSSL CMS encrypt (RSA-3072 recipient, OAEP SHA-256, AES-256,
streaming) -> prototype capture -> fake block store -> ONE read of the stored copy that checks
cipher SHA, decrypts, checks the dump SHA and runs a real pg_restore into a quarantine database.
Throwaway keys are generated at run time inside the temp dir and removed with it. Output is tool
versions, outcome words and counts only; no key, plaintext or ciphertext is printed.

Every subprocess, thread and the temp dir belong to one Env, which reaps all of them on every
exit path (normal, exception, KeyboardInterrupt, SIGTERM, init failure) and reports whether
absence was verified. Cleanup problems are reported, never suppressed.

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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .capture import Capture, Finish
from .core import CapacityError, FakeTransport, Ledger, ProbeError
from .restore import Expected, restore

PG_BIN = os.environ.get("BACKUP_PROBE_PG_BIN", "/usr/lib/postgresql/16/bin")
BASE_PORT = 54329
CAP = 64 * 2**20
DEADLINE_S = 400  # whole harness; verify_boat.sh allows cleanup time on top
STEP_S = 60
REAP_S = 5
CHUNK = 65536
ROWS_A, ROWS_B = 5000, 2000
CMS_ENCRYPT_HEAD = ["-encrypt", "-binary", "-aes256", "-stream", "-outform", "DER"]
CMS_OAEP_KEYOPTS = ["rsa_padding_mode:oaep", "rsa_oaep_md:sha256", "rsa_mgf1_md:sha256"]


def encrypt_argv(cert: Path) -> list[str]:
    """OpenSSL 3 applies -keyopt to the PRECEDING recipient cert, so -recip must come first."""
    argv = ["openssl", "cms", *CMS_ENCRYPT_HEAD, "-recip", str(cert)]
    for opt in CMS_OAEP_KEYOPTS:
        argv += ["-keyopt", opt]
    return argv


COUNT_SQL = (
    "SELECT (SELECT count(*) FROM items) || '/' || (SELECT count(*) FROM notes) || ' ' || "
    "md5((SELECT string_agg(id || label || payload, ',' ORDER BY id) FROM items) || "
    "(SELECT string_agg(id || body, ',' ORDER BY id) FROM notes))"
)


class Terminated(BaseException):
    """Raised by the SIGTERM handler so finally blocks and context exits run."""


def reap_proc(proc: subprocess.Popen[bytes], failures: list[str] | None = None) -> None:
    """Kill if running, wait (bounded), close pipes. A process that will not die is reported."""
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=REAP_S)
    except Exception:
        if failures is not None:
            failures.append("process_not_reaped")
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if pipe is not None:
                pipe.close()
        except Exception:
            pass


class Env:
    """Owns the temp dir, the throwaway cluster, throwaway keys, and every child and thread."""

    def __init__(self, port: int = BASE_PORT, after_start: Callable[[], None] | None = None) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bp-synth-"))
        self.port = str(port)
        self.after_start = after_start  # test hook: runs right after pg_ctl start
        self.sock = self.tmp / "s"
        self.env = {
            "PATH": f"{PG_BIN}:/usr/bin:/bin",
            "LC_ALL": "C",
            "PGHOST": str(self.sock),
            "PGPORT": self.port,
            "PGUSER": "synth",
        }
        self.procs: list[subprocess.Popen[bytes]] = []
        self.threads: list[threading.Thread | threading.Timer] = []
        self.pg_pid: int | None = None
        self.cluster_started = False
        self.failures: list[str] = []
        self._cleaned = False
        self.stop = threading.Event()  # lets owned helper threads end during cleanup
        self.cert, self.key = self.tmp / "a.crt", self.tmp / "a.key"
        self.other_cert, self.other_key = self.tmp / "b.crt", self.tmp / "b.key"

    def popen(self, argv: list[str], **kw: object) -> subprocess.Popen[bytes]:
        proc = subprocess.Popen(argv, env=self.env, **kw)  # type: ignore[call-overload]
        self.procs.append(proc)
        return proc

    def thread(self, target: Callable[..., None], *args: object) -> threading.Thread:
        t = threading.Thread(target=target, args=args, daemon=True)
        self.threads.append(t)
        t.start()
        return t

    def run(self, cmd: list[str], *, check: bool = True, inp: bytes | None = None) -> str:
        proc = subprocess.run(cmd, env=self.env, input=inp, capture_output=True, timeout=STEP_S, check=False)
        if check and proc.returncode != 0:
            raise RuntimeError(f"step failed rc={proc.returncode}: {Path(cmd[0]).name}")
        return proc.stdout.decode("utf-8", "replace").strip()

    def psql(self, db: str, sql: str, *, check: bool = True) -> str:
        return self.run(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-t", "-A", "-d", db, "-c", sql], check=check)

    def live_children(self) -> int:
        return sum(1 for p in self.procs if p.poll() is None)

    def __enter__(self) -> Env:
        try:
            self.sock.mkdir()
            data = self.tmp / "data"
            self.run([f"{PG_BIN}/initdb", "-D", str(data), "-A", "trust", "-U", "synth", "--no-sync"])
            opts = f"-k {self.sock} -c listen_addresses='' -p {self.port} -c fsync=off"
            self.cluster_started = True  # set before start: a half-started cluster must still be stopped
            self.run(
                [
                    f"{PG_BIN}/pg_ctl",
                    "-D",
                    str(data),
                    "-o",
                    opts,
                    "-l",
                    str(self.tmp / "pg.log"),
                    "-w",
                    "-t",
                    "60",
                    "start",
                ]
            )
            self.pg_pid = int((data / "postmaster.pid").read_text().splitlines()[0])
            if self.after_start is not None:
                self.after_start()
            for crt, key in ((self.cert, self.key), (self.other_cert, self.other_key)):
                self.run(["openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-keyout", str(key),
                          "-out", str(crt), "-days", "1", "-subj", "/CN=synthetic"])  # fmt: skip
        except BaseException:
            self.cleanup()
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        self.cleanup()

    def cleanup(self) -> list[str]:
        """Idempotent. Returns the problems found; an empty list means absence was verified."""
        if self._cleaned:
            return self.failures
        self._cleaned = True
        self.stop.set()
        for t in self.threads:
            if isinstance(t, threading.Timer):
                t.cancel()
        for proc in self.procs:
            reap_proc(proc, self.failures)
        for t in self.threads:
            t.join(REAP_S)
            if t.is_alive():
                self.failures.append("thread_still_alive")
        self._stop_cluster()
        if self.tmp.name.startswith("bp-synth-") and self.tmp.parent == Path(tempfile.gettempdir()):
            try:
                shutil.rmtree(self.tmp)  # only this run's own directory; errors are reported
            except OSError:
                self.failures.append("tmpdir_not_removed")
        if self.tmp.exists():
            self.failures.append("tmpdir_present")
        if any(p.poll() is None for p in self.procs):
            self.failures.append("child_alive")
        return self.failures

    def _stop_cluster(self) -> None:
        if not self.cluster_started:
            return
        try:
            data = str(self.tmp / "data")
            subprocess.run([f"{PG_BIN}/pg_ctl", "-D", data, "-m", "immediate", "-w", "-t", "30", "stop"],
                           env=self.env, capture_output=True, timeout=STEP_S, check=False)  # fmt: skip
        except Exception:
            self.failures.append("pg_ctl_stop_failed")
        pid = self.pg_pid
        if pid is None:
            try:  # init died between pg_ctl start and reading the pid file
                pid = int((self.tmp / "data" / "postmaster.pid").read_text().splitlines()[0])
            except (OSError, ValueError, IndexError):
                return
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)  # the pid of the postmaster this Env started
            except OSError:
                pass
            if _wait_gone(pid):
                self.failures.append("postmaster_needed_kill")
            else:
                self.failures.append("postmaster_alive")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _wait_gone(pid: int) -> bool:
    for _ in range(REAP_S * 10):
        if not _pid_alive(pid):
            return True
        threading.Event().wait(0.1)
    return False


class PipelineSource:
    """pg_dump | openssl cms -encrypt. Reports statuses and independent SHAs at finish().

    Blocking is bounded here (watchdog kills both processes), not by Capture.
    """

    def __init__(self, env: Env, db: str, cert: Path) -> None:
        self.env = env
        self.dump = env.popen(["pg_dump", "-Fc", "-d", db], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.enc = env.popen(
            encrypt_argv(cert), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self.pump_broken = False
        self.dsha, self.csha, self.clen = hashlib.sha256(), hashlib.sha256(), 0
        self.watchdog = threading.Timer(STEP_S * 2, self.cancel)
        self.watchdog.daemon = True
        env.threads.append(self.watchdog)
        self.watchdog.start()
        self.pump = env.thread(self._pump)

    def _pump(self) -> None:
        assert self.dump.stdout is not None and self.enc.stdin is not None
        try:
            while block := self.dump.stdout.read(CHUNK):
                self.dsha.update(block)
                self.enc.stdin.write(block)
        except (OSError, ValueError):
            # Encryption stopped reading: pg_dump could block on an undrained stdout, so stop it now.
            self.pump_broken = True
            try:
                self.dump.kill()
            except OSError:
                pass
        finally:
            try:
                self.enc.stdin.close()
            except (OSError, ValueError):
                pass

    def read(self, n: int) -> bytes:
        assert self.enc.stdout is not None
        block = self.enc.stdout.read(n)
        self.csha.update(block)
        self.clen += len(block)
        return block

    def finish(self) -> Finish:
        """Bounded. A hung child is killed and reaped and reported as E_TIMEOUT; a broken pump
        reports E_ENCRYPT, not the SIGKILL status of the producer it had to stop."""
        try:
            self.pump.join(STEP_S)
            if self.pump.is_alive():
                raise ProbeError("E_TIMEOUT")
            pw, ew = self.dump.wait(timeout=STEP_S), self.enc.wait(timeout=STEP_S)
        except (ProbeError, subprocess.TimeoutExpired) as exc:
            self.cancel()
            raise ProbeError("E_TIMEOUT") from exc
        self.watchdog.cancel()
        if self.pump_broken and ew != 0:
            raise ProbeError("E_ENCRYPT")
        return Finish(pw, ew, self.dsha.hexdigest(), self.csha.hexdigest(), self.clen)

    def cancel(self) -> None:
        """Kill, reap and close both children. A child that survives is recorded in env.failures."""
        for p in (self.dump, self.enc):
            reap_proc(p, self.env.failures)


class CmsStream:
    def __init__(self, env: Env, cipher: memoryview, cert: Path, key: Path) -> None:
        self.proc = env.popen(
            ["openssl", "cms", "-decrypt", "-binary", "-inform", "DER", "-recip", str(cert), "-inkey", str(key)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        self.feeder = env.thread(self._feed, cipher)

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
            except (OSError, ValueError):
                pass

    def read(self, n: int) -> bytes:
        assert self.proc.stdout is not None
        return self.proc.stdout.read(n)

    def finish(self) -> int:
        self.feeder.join(STEP_S)
        return self.proc.wait(timeout=STEP_S)

    def abort(self) -> None:
        reap_proc(self.proc)  # kill, wait, close; the feeder ends on the broken pipe


class CmsDecrypt:
    def __init__(self, env: Env, cert: Path, key: Path) -> None:
        self.env, self.cert, self.key = env, cert, key

    def start(self, cipher: memoryview) -> CmsStream:
        return CmsStream(self.env, cipher, self.cert, self.key)


class PgRestoreConsumer:
    """pg_restore into a quarantine database. abort() reaps the process only."""

    def __init__(self, env: Env, db: str, limit: int | None = None) -> None:
        self.proc = env.popen(
            ["pg_restore", "--no-owner", "--exit-on-error", "-d", db],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
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
        reap_proc(self.proc)


@dataclass
class Session:
    cap: Capture
    sink: FakeTransport
    ledger: Ledger
    err: str | None


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

    @staticmethod
    def new_ledger() -> Ledger:
        """One session budget: 1 put, 2 gets (one proof read, one reserved for retry), 2 copies read."""
        return Ledger(cipher_cap=CAP, max_writes=1, max_reads=2, sent_bytes=CAP, read_bytes=2 * CAP)

    def stored(self, db: str = "src_synth", cert: Path | None = None) -> Session:
        led = self.new_ledger()
        cap = Capture(cipher_cap=CAP, ledger=led, timeout_s=DEADLINE_S)
        sink = FakeTransport()
        try:
            cap.run(PipelineSource(self.env, db, cert or self.env.cert))
            cap.upload(sink, "obj")
        except ProbeError as exc:
            return Session(cap, sink, led, exc.code)
        return Session(cap, sink, led, None)

    def restore_into(
        self, s: Session, *, wrong_key: bool = False, limit: int | None = None, dump_sha: str | None = None,
        led: Ledger | None = None,
    ) -> tuple[str, str]:  # fmt: skip
        self.n += 1
        db = f"quar_{self.n}"
        self.env.psql("postgres", f"CREATE DATABASE {db}")
        e = self.env
        cert, key = (e.other_cert, e.other_key) if wrong_key else (e.cert, e.key)
        res = restore(
            sink=s.sink, name="obj", ledger=led or s.ledger,
            expected=Expected(s.cap.cipher_sha256, s.cap.cipher_len, dump_sha or s.cap.dump_sha256),
            decrypt=CmsDecrypt(e, cert, key), consumer=PgRestoreConsumer(e, db, limit),
            chunk=CHUNK, max_plain_bytes=CAP, timeout_s=STEP_S * 2,
        )  # fmt: skip
        counts = e.psql(db, COUNT_SQL, check=False) if res.trusted else ""
        e.psql("postgres", f"DROP DATABASE {db}", check=False)  # quarantine always discarded here
        return res.outcome, counts

    def check(self, name: str, ok: bool, detail: str) -> None:
        print(f"scenario {name}: {detail} -> {'ok' if ok else 'FAIL'}", flush=True)
        if not ok:
            self.failures.append(name)

    def cms_algorithms(self, ciphertext: bytes) -> str:
        text = self.env.run(["openssl", "cms", "-cmsout", "-inform", "DER", "-print"], inp=ciphertext).lower()
        parts = [
            w
            for w, k in (("rsaesOaep", "rsaesoaep"), ("sha256", "sha256"), ("aes-256-cbc", "aes-256-cbc"))
            if k in text
        ]
        return "+".join(parts)

    def run_all(self) -> None:
        e = self.env
        self.seed()
        print(f"source rows: {self.src_counts.split()[0]}", flush=True)

        s = self.stored()
        self.check(
            "capture_upload", s.err is None, f"state={s.cap.state.value} bytes={s.cap.cipher_len} code={s.err or 'none'}"
        )
        if s.err is not None:
            return  # nothing was stored: later scenarios would only dereference a missing object
        algs = self.cms_algorithms(s.sink.objects["obj"])
        self.check("cms_recipient_algorithm", algs == "rsaesOaep+sha256+aes-256-cbc", f"cms={algs}")

        outcome, counts = self.restore_into(s)  # the one proof GET: SHA, decrypt, dump SHA, real restore
        content = "match" if counts == self.src_counts else "DIFFER"
        self.check("restore_roundtrip", outcome == "verified" and counts == self.src_counts,
                   f"outcome={outcome} counts={counts.split(' ')[0]} content={content}")  # fmt: skip
        rem = s.ledger.remaining()
        gets, got_bytes = 2 - rem["reads"], 2 * CAP - rem["read"]
        puts, sent_bytes = 1 - rem["writes"], CAP - rem["sent"]
        size = s.cap.cipher_len
        used = f"put_requests={puts} get_requests={gets} bytes_sent={sent_bytes} bytes_read={got_bytes}"
        self.check("same_pass_budget", (gets, got_bytes, puts, sent_bytes) == (1, size, 1, size), used)

        outcome, counts = self.restore_into(s)  # reserved second copy: retry/rollback only
        rem = s.ledger.remaining()
        self.check("retry_uses_reserved_copy", outcome == "verified" and rem["reads"] == 0,
                   f"outcome={outcome} get_requests_left={rem['reads']}")  # fmt: skip
        outcome, _ = self.restore_into(s)
        self.check("no_third_copy", outcome == "budget_refused", f"outcome={outcome}")

        good = s.sink.objects["obj"]
        s.sink.objects["obj"] = good[:-1] + bytes([good[-1] ^ 1])
        outcome, _ = self.restore_into(s, led=self.new_ledger())
        self.check("tampered_ciphertext", outcome == "corrupt_ciphertext", f"outcome={outcome}")
        s.sink.objects["obj"] = good[: len(good) // 2]
        outcome, _ = self.restore_into(s, led=self.new_ledger())
        self.check("truncated_ciphertext", outcome == "truncated", f"outcome={outcome}")
        s.sink.objects["obj"] = good

        outcome, _ = self.restore_into(s, dump_sha="0" * 64, led=self.new_ledger())
        self.check("wrong_plaintext_sha", outcome == "plaintext_sha_mismatch", f"outcome={outcome}")
        outcome, _ = self.restore_into(s, wrong_key=True, led=self.new_ledger())
        self.check("wrong_key_decrypt", outcome == "decrypt_failed", f"outcome={outcome}")
        outcome, _ = self.restore_into(s, limit=1000, led=self.new_ledger())
        self.check("capacity_inconclusive", outcome == "inconclusive_capacity", f"outcome={outcome}")
        self.check("capacity_no_children", e.live_children() == 0, f"live_children={e.live_children()}")

        bad_db = self.stored(db="no_such_db")
        self.check("failing_producer", bad_db.err == "E_PRODUCER" and bad_db.sink.calls == [],
                   f"code={bad_db.err} sink_calls={len(bad_db.sink.calls)}")  # fmt: skip
        bad_cert = self.stored(cert=e.tmp / "missing.crt")
        self.check("failing_encrypt", bad_cert.err in ("E_ENCRYPT", "E_PRODUCER") and bad_cert.sink.calls == [],
                   f"code={bad_cert.err} sink_calls={len(bad_cert.sink.calls)}")  # fmt: skip

        res = restore(  # failing consumer: restore into a database that does not exist
            sink=s.sink, name="obj", ledger=self.new_ledger(),
            expected=Expected(s.cap.cipher_sha256, s.cap.cipher_len, s.cap.dump_sha256),
            decrypt=CmsDecrypt(e, e.cert, e.key), consumer=PgRestoreConsumer(e, "no_such_db"),
            chunk=CHUNK, max_plain_bytes=CAP, timeout_s=STEP_S * 2,
        )  # fmt: skip
        self.check("failing_consumer", res.outcome == "consumer_failed" and not res.trusted, f"outcome={res.outcome}")
        self.check("scenario_children_reaped", e.live_children() == 0, f"live_children={e.live_children()}")


def _hang(env: Env) -> None:
    env.popen(["sleep", "600"])
    env.thread(lambda: env.stop.wait(600))  # a helper thread that ends only when cleanup signals it


def cleanup_cases(h: Harness) -> None:
    """Each case must leave no owned cluster, child, thread or temp dir behind."""

    def absent(env: Env, port_pid: int | None) -> tuple[bool, str]:
        gone = not env.tmp.exists() and (port_pid is None or not _pid_alive(port_pid)) and env.live_children() == 0
        return gone and not env.failures, ",".join(env.failures) or "none"

    def fail_after_start() -> None:
        raise RuntimeError("synthetic init failure after pg_ctl start")

    env = Env(BASE_PORT + 1, after_start=fail_after_start)
    try:
        env.__enter__()
    except RuntimeError:
        pass
    ok, detail = absent(env, env.pg_pid)
    h.check("cleanup_init_failure_after_start", ok and env.cluster_started, f"problems={detail}")

    env = Env(BASE_PORT + 2)
    pid = None
    try:
        with env:
            pid = env.pg_pid
            _hang(env)
            env.popen(["false"])
            raise RuntimeError("synthetic error with hung child")
    except RuntimeError:
        pass
    ok, detail = absent(env, pid)
    h.check("cleanup_hung_child_and_error", ok, f"problems={detail}")

    env = Env(BASE_PORT + 3)
    pid = None
    try:
        with env:
            pid = env.pg_pid
            _hang(env)
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    ok, detail = absent(env, pid)
    h.check("cleanup_keyboard_interrupt", ok, f"problems={detail}")


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

    def _term(_sig: int, _frame: object) -> None:
        raise Terminated

    signal.signal(signal.SIGALRM, _alarm)
    signal.signal(signal.SIGTERM, _term)
    signal.alarm(DEADLINE_S)
    harness: Harness | None = None
    code = 0
    cleanup_problems: list[str] = []
    try:
        env = Env()
        harness = Harness(env)
        try:
            with env:
                _versions(env)
                harness.run_all()
        finally:
            cleanup_problems = list(env.failures)
        cleanup_cases(harness)
    except TimeoutError:
        print("FAIL deadline", flush=True)
        code = 1
    except Terminated:
        print("FAIL terminated", flush=True)
        code = 143
    except KeyboardInterrupt:
        print("FAIL interrupted", flush=True)
        code = 130
    except Exception as exc:
        print(f"FAIL {type(exc).__name__}", flush=True)
        code = 1
    finally:
        signal.alarm(0)
    if cleanup_problems:
        print("cleanup FAILED: " + ",".join(cleanup_problems), flush=True)
        code = code or 1
    elif code == 0 or harness is not None:
        print("cleanup: owned cluster, children, threads and temp dir verified absent", flush=True)
    failed = harness.failures if harness else []
    print("FAILED: " + ",".join(failed) if failed else ("all scenarios ok" if code == 0 else "incomplete"))
    return 1 if failed else code


if __name__ == "__main__":
    sys.exit(main())
