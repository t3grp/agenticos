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

The expected Armada webhook runs when a commit is pushed to `t3`, and for
same-repository pull requests when they are `opened`, `synchronize`d or `reopened`.
It rejects fork pull requests. These triggers belong to the Armada Worker and
GitHub webhook configuration; this change does not modify GitHub Actions or
provision that infrastructure.

Check the `armada` status on the pull request's exact head SHA. Open the status
details for the task results; if no status appears, inspect recent webhook delivery
history for `t3grp/agenticos` and the Armada Worker logs. A green status means all
eight configured tasks passed for that commit.

## Credentials

Secret values must stay in Azure Key Vault `t3-secrets` and the Armada/GitHub
settings. Use these names only in operational configuration:

- `agenticos-armada-ci-connection`
- `agenticos-armada-ci-github-token`
- `agenticos-armada-webhook-github-token`
- `agenticos-armada-webhook-signing-secret`

The contents/status token is limited to repository contents read and commit-status
write. The webhook administration token stays outside CI containers. No task in this
configuration requests a secret.

## Disable

Disable the integration by removing the repository webhook in Armada and GitHub.
Revoke the scoped tokens and signing secret in their respective settings and remove
the corresponding `agenticos-armada-*` vault entries when they are no longer needed.
