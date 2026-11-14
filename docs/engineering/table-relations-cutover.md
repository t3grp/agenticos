# Relationship storage and cutover

Engineering rollout note for PR #101. This is a coordinated cutover, not a rolling
upgrade that can keep old table writers running.

## Storage contract

A relationship is identified by its existing source table and immutable column
ID. `virtual_table_relations` declares its destination table, archive state and
maximum number of destinations per source record. An omitted `link_limit` keeps
the existing limit, or defaults to 100 for a new field. A limit of 1 represents a
single destination; this release does not claim destination uniqueness or
one-to-one relationships.

`virtual_table_record_links` holds each connection once. Composite foreign keys
bind both endpoints to the same organization and to the tables declared by the
relationship. A source/field/target primary key prevents duplicate destinations.
A unique source/field/position plus a position bound enforces the declared
cardinality inside PostgreSQL, including concurrent attempts to exceed a limit
of 1. The source owns ordering; backlinks read the same edges. Active positions
are dense while retaining relative order; evidence keeps original rejected
positions. Narrowing counts active edges, including hidden valid destinations,
so quarantine entries do not prevent a valid cardinality change.

Normal writes synchronize edges at the existing record repository boundary,
inside the same transaction as the record, revision, history, outbox and receipt.
The source row lock and expected revision still serialize changes. The atomic
link-delta API in PR #100 supplies a merged internal value; preserved hidden
links remain in that internal value. Synchronization does not apply the caller's
read projection to storage.

Current reads project ordered edges before validating the public record shape.
Destination table and row authorization still runs after this internal
projection, including for labels, lookups and rollups. A receipt at an older
source revision keeps its original source snapshot and resolves destination
visibility at read time. It must not combine today's edges with an older
revision number.

The original JSON link representation remains available for immutable history,
legacy evidence and rollback. Direct writes outside the repository boundary are
unsupported after cutover. No per-user-table DDL runs on an API request.

## Archive, delete and quota behavior

Archiving a link field retains its definition and edges; restoring the same field
ID restores the same connections. Reverse traversal refuses an archived field.
Deleting a destination record removes incoming edges through a foreign-key
cascade and leaves the source record and its revision intact. Deleting a source
record removes its edges. Deleting a table removes its corresponding definitions
and edges; a reference does not give another table a second copy of a record.

Relationship definitions, edges and retained evidence count against organization
storage usage and the existing record-mutation quota transaction.

## Read-only reverse traversal

`GET /tables/{table_id}/records/{record_id}/backlinks` requires
`source_table_id` and `column_id`, with bounded `skip` and `limit`.

The service authorizes the destination table and record, the named source table,
and each source row before pagination. It returns source records using the
ordinary read pipeline. There is no hidden total, relation enumeration or reverse
write route. A backlink supplies neither a row grant nor source write permission.
Recursive permission inheritance, editable reciprocal fields, polymorphic
destinations and one-to-one destination constraints are outside this version.

## Backfill and evidence

Migration `0326_virtual_table_relations` follows the verified local head
`0311_room_submissions`. PR #82 carries a different migration line ending at
`0325_merge_t3_rooms_v173`; integration must rebase this unapplied revision or
add the appropriate merge revision and verify one resulting head before merge.
Never edit this migration after it has been applied to a deployed database.

The migration takes a write barrier, copies current schema definitions including
archived fields, and walks source records in 500-row keyset batches. It keeps
source IDs, column IDs, source revisions, schema versions, record JSON and
immutable history unchanged. Existing valid destinations become ordered edges.

`virtual_table_link_evidence` retains the original value, source revision and
position for malformed cells, invalid IDs, missing destinations and duplicate
destinations. IDs outside the declared destination table or organization are
recorded as unavailable destinations without copying foreign record content.
An over-limit cell is retained whole and produces no partial set of live edges.
This can make fewer links visible after cutover; an operator must inspect and
repair the retained evidence, not assume the migration resolved it.

An invalid relationship definition, including a missing or cross-organization
destination table, aborts the transaction. The migration does not guess a new
destination or silently drop an invalid field. Evidence has no record foreign
key, so deleting a record does not erase its migration evidence; deleting the
source table still removes that table's data.

## Deployment gate and rehearsal

1. Review the dependency stack: #103 link-visibility hardening, #99 explicit row
   Read/Write access and #100 atomic link deltas. Combine their tests with this
   PR's canonical read path and use edges in authorized link-filter predicates.
   Reconcile one-hop project membership against canonical edges on both SQL and
   single-record policy paths. Also add a schema-definition storage gate to the
   pre-existing table-create/schema-update paths: acquire storage before table
   locks and enforce before the table audit, since quota refusals audit in a
   separate transaction. These are remaining stack integration gates, not
   claims of complete cutover enforcement in this isolated PR.
2. On a disposable copy, rehearse the migration and inspect invalid definitions,
   evidence counts and storage growth. Check valid edge counts and ordered
   destinations against source values; resolve invalid definitions before
   scheduling cutover. Large installations need measured lock duration and
   migration disk headroom.
3. Stop every table writer, including API processes, imports, agents, workflow
   workers and background jobs. Drain in-flight transactions. Apply the
   migration, then start only application versions that synchronize edges.
4. Verify one Alembic head, target/source organization constraints, representative
   direct reads, bounded views, CSV, receipts, deltas and backlinks under a
   restricted member identity. Resume writers after these checks.
5. Keep the previous release and database backup available until verification
   finishes. A downgrade removes relationship storage and leaves source JSON
   intact, but old code does not enforce the new link limits or Read/Write
   policy contract. Rollback therefore needs the same write barrier and a
   deliberate application-version decision.

The local integration tests exercise this revision's upgrade, evidence copy and
downgrade inside the repository's disposable PostgreSQL fixture. They do not
substitute for a full-chain migration rehearsal and the required CI coverage
gate. No migration should run against a live deployment as part of this PR's
development or validation.
