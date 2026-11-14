# AgenticOS Armada Worker configuration

AgenticOS reuses the existing [`t3grp/armada-runner-runtime` Worker](https://github.com/t3grp/armada-runner-runtime/blob/main/worker/src/worker.ts).
Its webhook handler verifies `X-Hub-Signature-256`; the
[event filter](https://github.com/t3grp/armada-runner-runtime/blob/main/worker/src/hooks.ts)
accepts pushes to `t3` and same-repository pull requests on `opened`, `synchronize`
and `reopened`, and rejects fork PRs. The CI driver posts the `armada` status to
the exact commit SHA. Runtime tests cover signature checks, event filtering and
fork rejection.

`setup_runtime.py` configures the existing Armada deployment with the GitHub App
credentials and webhook secret. It calls only the Armada API; it does not create
a GitHub App, create or modify a webhook, or deploy a Worker. Configure the App's
own webhook in its settings with:

- Payload URL: the existing Armada Worker URL plus `/webhooks/github/agenticos`
- Content type: `application/json`
- Events: Push and Pull request
- SSL verification: enabled
- Webhook secret: `agenticos-armada-app-webhook-secret`

Install the org-owned App on `t3grp/agenticos` only. Give it Contents read,
Metadata read, Pull requests read and Commit statuses read/write permissions,
and subscribe only to Push and Pull request events. The runtime ignores branches
other than `t3`, accepts only `opened`, `synchronize` and `reopened` PR actions,
and refuses fork pull requests.

## Secret names

Load values from Azure Key Vault `t3-secrets` into these private process
environment variables before running the script. Do not put values in files,
terminal arguments or logs.

| Environment variable | Key Vault secret name |
| --- | --- |
| `AGENTICOS_ARMADA_CI_CONNECTION_JSON` | `agenticos-armada-ci-connection` |
| `AGENTICOS_ARMADA_APP_ID` | `agenticos-armada-app-id` |
| `AGENTICOS_ARMADA_APP_INSTALLATION_ID` | `agenticos-armada-app-installation-id` |
| `AGENTICOS_ARMADA_APP_PRIVATE_KEY` | `agenticos-armada-app-private-key` |
| `AGENTICOS_ARMADA_APP_WEBHOOK_SECRET` | `agenticos-armada-app-webhook-secret` |

The script stores App credentials in Armada under the uppercase names shown in
`setup_runtime.py` and configures the webhook secret on the runtime. It never
prints values. The Worker signs an RS256 App JWT, exchanges it for a token scoped
to `agenticos` with Contents read and Commit statuses write, caches it in memory,
and posts status updates on the exact commit SHA. The private key and installation
token stay in the Worker.

Do not place secret values in the repository, command-line arguments or logs.
