# Armada CI

Armada runs the checks in `.armada.json` on isolated Linux workers. The repository
is public, so the expected webhook accepts only pull requests from this repository;
fork pull requests are rejected.

## Checks

| Task | Check |
| --- | --- |
| `backend-ruff-check` | `ruff check` for the backend, shared scripts and updater |
| `backend-ruff-format` | `ruff format --check` for those same paths |
| `backend-pytest` | Backend tests outside `backend/tests/integration`, with up to four pytest workers |
| `alembic-single-head` | Confirms Alembic reports exactly one migration head; it does not apply migrations |
| `frontend-typecheck` | TypeScript `tsc --noEmit` via `bun run type-check` |
| `frontend-vitest` | Vitest with the configured coverage thresholds |
| `frontend-prettier` | Prettier check over frontend scripts, source and E2E specs |
| `frontend-eslint` | ESLint with the repository's zero-warning setting |

The checks use Node 24.21.0, Python 3.15.0rc2, uv 0.12.7 and Bun 1.3.5. The setup
script verifies the SHA-256 digest of each downloaded runtime archive. It installs
`tzdata` and sets UTC because the test suite uses system timezone data.

The frontend is locked by `frontend/bun.lock` and its GitHub Actions workflow uses
Bun. There is no `package-lock.json`, so `npm ci` cannot install this checkout;
`armada-install.sh` uses `bun install --frozen-lockfile` to match the repository's
lockfile and existing CI.

## Left out of the GitHub Actions workflow

Armada's first cut does not run PostgreSQL or Valkey services, database-backed
integration tests, migration upgrade/downgrade tests, `alembic check` against a live
database, MinIO-backed tests, or browser tests (Playwright). It also omits the
backend type/dead-code/dependency checks and guard scripts, spelling and pre-commit
checks, frontend i18n and dependency checks, frontend build and build packaging,
the backend coverage gate, coverage aggregation and Codecov upload,
security/dependency/license audits, security-test reporting, documentation
build/slug checks, and release-scope reuse. The single-head check only checks
migration graph shape; it does not replace the database migration checks.

## Triggers and status

The existing `t3grp/armada-runner-runtime` Worker accepts pushes to `t3` and
same-repository pull requests on `opened`, `synchronize` and `reopened`; fork pull
requests are rejected. Add one repository webhook manually in Settings → Webhooks:

- Payload URL: `https://t3-armada-github-runners.sherman-tansx.workers.dev/webhooks/github/agenticos`
- Content type: `application/json`
- Events: Pushes and Pull requests only
- SSL verification: on

No webhook-administration token or webhook-creation script is used.

Check the `armada` status on the pull request's exact head SHA. Open the status
details for the task results; if no status appears, inspect recent webhook delivery
history for `t3grp/agenticos` and the Armada Worker logs. A green status means all
eight configured tasks passed for that commit.

## Credentials

Keep secret values in Azure Key Vault `t3-secrets` and the Armada/GitHub settings.
Use these names only in operational configuration:

- `agenticos-armada-ci-connection`
- `agenticos-armada-ci-github-token`
- `agenticos-armada-webhook-signing-secret`

The fine-grained CI token is restricted to `t3grp/agenticos` with Contents read and
Commit statuses read/write (Metadata read is automatic). The Worker stores it in
Armada as `AGENTICOS_ARMADA_CI_GITHUB_TOKEN` for fetching commits and posting
statuses. CI tasks do not request this secret. The webhook signing secret is
configured in the reusable Armada Worker; no secret values are committed.

## Renewal

The fine-grained PAT expires 90 days after it was created. Its exact expiry date is
shown on GitHub's token settings page. The Key Vault entry itself has no expiry.
Before the PAT expires, regenerate it with the same scopes, have Azure Ops update
`agenticos-armada-ci-github-token` in `t3-secrets`, then confirm a green `armada`
status on the next commit.

## Disable

Remove the repository webhook from GitHub and the Armada runtime. Revoke the PAT
and signing secret in their respective settings and remove the corresponding
`agenticos-armada-*` Key Vault entries when they are no longer needed. Keep the
Armada connection only if another integration uses it.
