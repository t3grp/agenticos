# backup_probe — synthetic backup-helper prototype

**Status: prototype and test artifact. Live use is disabled. This is not a deployment and
gives no permission to touch any live system.** Tracked in issue #299.

The package holds small stdlib modules with injected I/O. It has no production transport,
no credentials handling, no database connector and no shell adapter. The only sink is a
deterministic in-memory fake.

Run from `scripts/` (a different `scripts` package may shadow the name elsewhere):

```
python -m unittest backup_probe.test_backup_probe    # stdlib, 96 tests, under 1 second
python -m backup_probe selftest                      # in-memory fixtures only
```

Any other command line prints `E_DISABLED` and exits 2 before it opens a file, process or
network connection. There is no enable-live flag. Importing a module has no side effect.
Errors are fixed codes (`core.CODES`); no traceback, message, path, body or argument is echoed.

## Supported APIs and limits

| Module | API | Limit or rule |
|---|---|---|
| `core` | `strict_int`, `ProbeError`, `safe_code`, `State`/`Machine`, `Ledger`, `HeldBuffer`, `remaining_after_parts`, `FakeTransport` | Ints only: bool, negative, float, NaN, str and None are refused. |
| `capture` | `Capture.run/upload/verify`, `Finish` | One aggregate byte cap over dump, config and manifest, not per file. |
| `restore` | `restore(...)`, `Expected`, `RestoreResult` | `chunk`, `max_plain_bytes` and a finite `timeout_s` are required. `trusted` is true only when every gate passed. |
| `preflight` | `evaluate`, `quote`, `ledger_for`, `cost_for`, `max_bytes_for_budget`, `proof_pgdata_floor`, `READ_ONLY_SQL` | Metadata in, verdict out. `live_permitted` is always false. |
| `fixtures` | in-memory source, decrypt stream, consumer | The fixture "cipher" is an XOR byte transform, not encryption. |

State machine: `NEW -> CAPTURING -> HELD -> UPLOADING -> UPLOADED -> VERIFIED`, plus
`FAILED` and `UNKNOWN`. Terminal states stay terminal. There is no reset and no implicit retry.

## Invariants

- HELD needs: a bounded `bytearray`, the producer exit and encrypt exit both a real `0`,
  exact cipher length, exact cipher SHA-256 and a well-formed dump SHA-256.
- Timeout, cancel, early EOF or any failure clears the held buffer and the sink is never called.
  Timeouts must be finite. The deadline and cancel flag are checked before and after every read and
  after `finish()`. A returned chunk larger than the requested size is refused.
- **Adapter limitation:** pure Python cannot interrupt a source, decrypt stream or consumer that
  blocks inside one call. `Capture` and `restore` check time only between calls and there is no
  real watchdog here. The injected adapter must bound its own blocking. The Boat harness does this
  with a timer that kills its own child processes.
- Ledger: spend is reserved atomically **before** a call, under a lock. There are four pools:
  write requests (`put`), read requests (`get`, `head`), bytes sent and bytes read. A write cannot
  borrow a read allowance or the reverse. `head` reserves zero body bytes. Partial, retried, lost
  and interrupted transfers are never refunded. Each byte pool must be at most `2 * cipher_cap`.
  A refused reservation spends nothing. `preflight.ledger_for(n)` builds a ledger whose limits are
  exactly the quote for `n` bytes.
- `FakeTransport.put` is create-only: an existing name is refused before the old bytes change.
  `get(name, limit)` refuses an object larger than `limit` before returning any bytes, so one
  reservation bounds both the read budget and memory.
- A lost or partial upload response is `UNKNOWN`. Nothing retries it.
- The sink API is `put`, `get`, `head` on bounded bytes and an opaque name. It takes no credentials.
- Restore checks ciphertext length and SHA, decrypt exit, plaintext SHA and consumer exit.
  Output stays quarantined until all pass; otherwise `consumer.abort()` discards it.
  Plaintext over `max_plain_bytes` is refused before it reaches the consumer, and an oversized
  decrypt piece is refused as malformed. Running out of space (consumer or plaintext cap) is
  `inconclusive_capacity`, separate from `truncated`, `corrupt_ciphertext`, `decrypt_failed`,
  `consumer_failed`, `timeout` and `plaintext_sha_mismatch`.
- `KeyboardInterrupt` and `SystemExit` get the same cleanup as errors in capture, upload, verify and
  restore: buffers are cleared, consumers and streams are aborted, an uncertain transfer is
  `UNKNOWN` and nothing retries. The original interrupt is re-raised.
- A failed restore aborts the stream and the consumer; both are always tried. If an abort raises,
  `RestoreResult.cleanup` is `cleanup_failed` (else `clean`), `trusted` stays false and no exception
  text is kept. The in-memory fixture consumer zeroes and clears its data on abort (best effort).
- `safe_code` and `ProbeError.__str__` revalidate the code against the allowlist on every use.
- Buffer zeroing is best effort. Python gives **no guaranteed memory erasure**, and this code
  does **not** lock private memory (no Windows or Linux page locking). `bytes` objects that a
  sink or fetch returns are not owned here and are not zeroed.

## Preflight: metadata first, no outage

`preflight.evaluate` is a pure function of numbers a human supplies. `READ_ONLY_SQL` is
reviewable text only: `BEGIN READ ONLY`, local `statement_timeout` 5s and `lock_timeout` 2s,
`pg_database_size`, and a bounded list of the largest table, index and TOAST sizes by kind.
It reads no row text and no relation names. Nothing here executes it.

- Physical size is **not** a mathematical upper bound for a compressed dump.
- With an unknown logical bound the verdict is `cannot_approve`. The proposed next step is a
  separately authorized count-and-discard pass while the service stays up. Never stop the
  service just to size a dump.
- Selected cap is `min(cost limit, relay memory, verified proof capacity)`.
- `quote(n)` uses `Decimal` and assumes no free bandwidth. Block size is 4 MiB, `B = ceil(n / block)`,
  writes `W = 2 * (B + 10)`, reads `R = 4 * (B + 10)`. Request cost is `W * 0.05 / 10000 +
  R * 0.004 / 10000`. Storage is `(n / 1e9) * 0.02 * (7 / 28)`. Paid egress is `(n / 1e9) * 0.12 * 2`.
  Add a 0.01 metadata reserve and a 0.02 software-key reserve. The quote exposes `blocks`, `writes`
  and `reads`, and `ledger_for` turns them into ledger limits. Example: 256 MiB gives
  `B=64, W=148, R=296` and 0.09662508672 USD. 3 GiB gives `B=768, W=1556, R=3112` and
  0.82822504064 USD. Tests also check `block-1`, `block` and `block+1` bytes, so the block model
  is verified and not only fitted to the examples. A quote is a cost ceiling, not an allocation request.
- Proof PGDATA floor is `ceil(1.3 * physical) + 256 MiB`. It must fit both 1 GiB tmpfs and the
  supplied `proof_capacity_bytes`; either failure refuses. The proof
  container is 2 GiB in total including tmpfs, with swap equal to memory. The host needs
  `MemAvailable >= 2 GiB + documented production peak + 1 GiB`.
- A missing, unknown or malformed field refuses. A `candidate` verdict proves nothing about
  live memory safety.

## Verification status

| Area | Status |
|---|---|
| Unit tests (96, stdlib) | Run on a workstation, all pass. |
| Manual mutation probe: 37 single-line mutations of boundary checks, 34 caught | Historical, author-only claim made at commit 2d7. Not verified at the current pin. No retained script, so no mutation claim is made for current code. |
| Harness orchestration with a spy `Popen` | Unit tested: no fixture plaintext, key-file contents or canary reaches argv, env or output. Public key and cert paths are passed on purpose. |
| `verify_boat.sh`, `synthetic_postgres_restore.py` | Boat run at the previous pin c95af9fa5 passed (92 units, 19 scenarios). That is an observed result for that pin only. The latest pin needs a new Boat run before any claim. Needs Boat: PostgreSQL 16, OpenSSL CMS, Linux, non-root user. |
| Real `pg_dump`, RSA-3072 OAEP CMS, `pg_restore`, cleanup cases, timing under 600s | **Untested** until Boat runs it. |
| Any live host, transport, vault or restore | **Not built and not tested.** |

`verify_boat.sh` refuses unless `BACKUP_PROBE_BOAT=1` on Linux as non-root. It installs
dependencies inside Boat only, then runs the unit tests and the harness. The harness uses a
temporary cluster on a unix socket, synthetic rows and throwaway keys generated in its own
temp directory. It encrypts with a throwaway RSA-3072 recipient (OAEP, SHA-256, AES-256, CMS
`-stream`) and checks the recipient algorithms in the stored CMS. The proof does one read of the
stored copy through the same ledger as capture: cipher SHA, decrypt, dump SHA and a real
`pg_restore` into a quarantine database. A second read is kept for a retry or rollback test only.
It prints versions, outcome words, counts and request and byte totals only.
One `Env` owns every child process, helper thread and the temp dir. It kills and reaps children,
closes pipes, joins threads, stops the cluster (falling back to its own postmaster pid), removes
only its own directory, and reports `cleanup FAILED` rather than hiding a leftover. Synthetic cases
cover init failure after `pg_ctl start`, a hung child with an error, and `KeyboardInterrupt`.
`verify_boat.sh` forwards `TERM` to Python so the same cleanup runs, and checks for leftovers.
No key material or ciphertext is committed.

## Gates required before any real use

This is a source-only prototype. Nothing here implies approval. Each needs its own explicit approval. None is granted by this prototype.

1. Independent review of this code and a passing Boat run.
2. Private-memory locking, designed and tested on the target OS.
3. Host selection and a sized, approved capture window (count-and-discard pass first).
4. Recipient and key approval, with key custody outside this repository.
5. A real transport designed with its own budget, retry and credential model.
6. A restore drill into an isolated target, with all integrity gates passing.
