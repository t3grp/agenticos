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
requests are rejected. The org-owned GitHub App's webhook sends Push and Pull
request events to the Worker at `/webhooks/github/agenticos` with JSON content
and SSL verification enabled. Install the App on `t3grp/agenticos` only. The
runtime posts the `armada` status to the exact event head SHA.

Check the `armada` status on the pull request's exact head SHA. Open the status
details for the task results; if no status appears, inspect recent webhook delivery
history for `t3grp/agenticos` and the Armada Worker logs. A green status means all
eight configured tasks passed for that commit.

## Credentials

Keep secret values in Azure Key Vault `t3-secrets` and the Armada/GitHub App
settings. Use these names only in operational configuration:

- `agenticos-armada-ci-connection`
- `agenticos-armada-app-id`
- `agenticos-armada-app-installation-id`
- `agenticos-armada-app-private-key`
- `agenticos-armada-app-webhook-secret`

The GitHub App is org-owned, subscribed only to Push and Pull request events, and
installed on `t3grp/agenticos` only. Give it Contents read, Metadata read, Pull
requests read and Commit statuses read/write permissions; leave other permissions
off. The Worker verifies `X-Hub-Signature-256` with the App webhook secret, signs
a short-lived RS256 App JWT, and exchanges it for an installation token restricted
to `agenticos` with Contents read and Commit statuses write. It caches that token
in memory until shortly before expiry and posts the status on the exact commit
SHA. CI tasks do not receive the App private key or installation token. The
setup script stores the App ID, installation ID and private key in Armada under
`AGENTICOS_ARMADA_APP_ID`, `AGENTICOS_ARMADA_APP_INSTALLATION_ID` and
`AGENTICOS_ARMADA_APP_PRIVATE_KEY`; it configures the webhook secret directly on
the runtime. No secret values are committed.

## Disable

Suspend or uninstall the GitHub App in the org settings, then revoke its private
key in the App settings. Remove the corresponding App credentials and webhook
secret from Key Vault and the Armada runtime. Keep the Armada connection only if
another integration uses it.
