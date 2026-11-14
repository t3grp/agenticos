# add atomic linked-record edits

Status: claimed implementation draft. Owner: shermzy.
Requested on 15 November 2026 (Asia/Singapore). Base: t3 at 080c1294.

## Contract

Add typed add/remove link changes to record updates and use them in the table UI, preserving relationships hidden from the editor.

## Acceptance checks

- Validate explicit added/removed targets, source Write and protected security-parent changes.
- Preserve hidden existing links and enforce final link-count limits.
- Reject duplicate, conflicting, unknown and archived-field deltas.
- Retain revision conflicts, idempotency, transactional history and outbox behavior.
- UI sends deltas and keeps edits available after a conflict.

## Dependencies and ownership

Depends on `fix/tables-access-hardening-20261115`, #99.
Preserve the agreed row Read/Write contract: Write includes Read and updates to
ordinary fields, while deletion, sharing and schema administration stay separate.
Coordinate through this PR before overlapping implementation. This initial
commit records the implementation contract; application changes follow after
all draft PRs have been opened and claimed.

## Validation and rollout

Use focused deterministic tests and the exact revision's CI. Database constraint
and migration behavior requires disposable PostgreSQL integration tests. Preserve
existing data, revisions, history, idempotency and tenant checks. No production
configuration, migration, merge or deployment is authorized by this plan alone.
