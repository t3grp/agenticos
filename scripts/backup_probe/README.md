# backup_probe — synthetic backup-helper prototype

**Status: prototype and test artifact. Live use is disabled. This is not a deployment and
gives no permission to touch any live system.** Tracked in issue #299.

The package holds small stdlib modules with injected I/O. It has no production transport,
no credentials handling, no database connector and no shell adapter. The only sink is a
deterministic in-memory fake.

Run from `scripts/` (a different `scripts` package may shadow the name elsewhere):

```
python -m unittest backup_probe.test_backup_probe    # stdlib, about 1 second
python -m backup_probe selftest                      # in-memory fixtures only
```

Any other command line prints `E_DISABLED` and exits 2 before it opens a file, process or
network connection. There is no enable-live flag. Importing a module has no side effect.
Errors are fixed codes (`core.CODES`); no traceback, message, path, body or argument is echoed.

## Supported APIs and limits

| Module | API | Limit or rule |
|---|---|---|
| `core` | `strict_int`, `ProbeError`, `State`/`Machine`, `Ledger`, `HeldBuffer`, `remaining_after_parts`, `FakeTransport` | Ints only: bool, negative, float, NaN, str and None are refused. |
| `capture` | `Capture.run/upload/verify`, `Finish` | One aggregate byte cap over dump, config and manifest, not per file. |
| `restore` | `restore(...)`, `Expected`, `RestoreResult` | `trusted` is true only when every gate passed. |
| `preflight` | `evaluate`, `cost_for`, `max_bytes_for_budget`, `proof_pgdata_floor`, `READ_ONLY_SQL` | Metadata in, verdict out. `live_permitted` is always false. |
| `fixtures` | in-memory source, decrypt stream, consumer | The fixture "cipher" is an XOR byte transform, not encryption. |

State machine: `NEW -> CAPTURING -> HELD -> UPLOADING -> UPLOADED -> VERIFIED`, plus
`FAILED` and `UNKNOWN`. Terminal states stay terminal. There is no reset and no implicit retry.

## Invariants

- HELD needs: a bounded `bytearray`, the producer exit and encrypt exit both a real `0`,
  exact cipher length, exact cipher SHA-256 and a well-formed dump SHA-256.
- Timeout, cancel, early EOF or any failure clears the held buffer and the sink is never called.
- Ledger: spend is reserved atomically **before** a call, under a lock. It covers total
  requests and bytes (sent and read). Partial, retried and lost transfers are never refunded.
  Each byte pool must be at most `2 * cipher_cap`. A refused reservation spends nothing.
- A lost or partial upload response is `UNKNOWN`. Nothing retries it.
- The sink API is `put`, `get`, `head` on bounded bytes and an opaque name. It takes no credentials.
- Restore checks ciphertext length and SHA, decrypt exit, plaintext SHA and consumer exit.
  Output stays quarantined until all pass; otherwise `consumer.abort()` discards it.
  Running out of space is `inconclusive_capacity`, separate from `truncated`,
  `corrupt_ciphertext` and `plaintext_sha_mismatch`.
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
- Cost uses `Decimal` and assumes no free bandwidth: storage 0.02 USD per decimal GB-month for
  7 of 28 days, egress 0.12 USD per decimal GB for 2 copies, requests per 512 KiB block plus a
  fixed 80, a 0.01 metadata reserve and a 0.02 software-key reserve. Example: 256 MiB costs
  0.09662508672 USD and 3 GiB costs 0.82822504064 USD. These are tested values. They are a
  cost ceiling, not an allocation request.
- Proof PGDATA floor is `ceil(1.3 * physical) + 256 MiB`, at most 1 GiB tmpfs. The proof
  container is 2 GiB in total including tmpfs, with swap equal to memory. The host needs
  `MemAvailable >= 2 GiB + documented production peak + 1 GiB`.
- A missing, unknown or malformed field refuses. A `candidate` verdict proves nothing about
  live memory safety.

## Verification status

| Area | Status |
|---|---|
| Unit tests (53, stdlib) and a manual mutation probe of 16 boundary checks | Run on a workstation. All tests pass and every mutation was caught. |
| `verify_boat.sh`, `synthetic_postgres_restore.py` | **Written, not run.** They need Boat: PostgreSQL 16, OpenSSL CMS, Linux, non-root user. |
| Real `pg_dump`, CMS, `pg_restore`, timing under 600s | **Untested** until Boat runs it. |
| Any live host, transport, vault or restore | **Not built and not tested.** |

`verify_boat.sh` refuses unless `BACKUP_PROBE_BOAT=1` on Linux as non-root. It installs
dependencies inside Boat only, then runs the unit tests and the harness. The harness uses a
temporary cluster on a unix socket, synthetic rows and throwaway keys generated in its own
temp directory. It prints versions, outcome words and counts only, and removes only its own
directory and processes. No key material or ciphertext is committed.

## Gates required before any real use

Each needs its own explicit approval. None is granted by this prototype.

1. Independent review of this code and a passing Boat run.
2. Private-memory locking, designed and tested on the target OS.
3. Host selection and a sized, approved capture window (count-and-discard pass first).
4. Recipient and key approval, with key custody outside this repository.
5. A real transport designed with its own budget, retry and credential model.
6. A restore drill into an isolated target, with all integrity gates passing.
