# AgenticOS Armada Worker configuration

AgenticOS reuses the existing [`t3grp/armada-runner-runtime` Worker](https://github.com/t3grp/armada-runner-runtime/blob/main/worker/src/worker.ts).
Its webhook handler verifies `X-Hub-Signature-256`; the
[event filter](https://github.com/t3grp/armada-runner-runtime/blob/main/worker/src/hooks.ts)
accepts pushes to `t3` and same-repo pull requests on `opened`, `synchronize` and
`reopened`, rejecting fork PRs. The CI driver posts the `armada` status to the
exact commit SHA. The upstream
[webhook tests](https://github.com/t3grp/armada-runner-runtime/blob/main/worker/tests/hooks.test.ts)
cover signatures, event filtering and fork rejection.

`setup_runtime.py` configures the existing Armada deployment with the CI PAT and
webhook signing secret by name. It calls only the Armada API; it does not create
or modify a GitHub webhook and it does not deploy a Worker. The repository hook is
added manually after the Worker is live, with:

- Payload URL: the existing Armada Worker URL plus `/webhooks/github/agenticos`
- Content type: `application/json`
- Events: Pushes and Pull requests
- SSL verification: enabled

For the current runtime, the payload URL is
`https://t3-armada-github-runners.sherman-tansx.workers.dev/webhooks/github/agenticos`.
Use the secret `agenticos-armada-webhook-signing-secret`. Do not create a webhook
administration token; the repository webhook is added once by hand.

The repository hook runs on `t3` pushes. The runtime ignores other branches and
PR actions, and refuses fork pull requests.

## Secret names

Load values from Azure Key Vault `t3-secrets` into the following private process
environment variables before running the script. Do not put values in files,
terminal arguments, or logs.

| Environment variable | Key Vault secret name |
| --- | --- |
| `AGENTICOS_ARMADA_CI_CONNECTION_JSON` | `agenticos-armada-ci-connection` |
| `AGENTICOS_ARMADA_CI_GITHUB_TOKEN` | `agenticos-armada-ci-github-token` |
| `AGENTICOS_ARMADA_WEBHOOK_SIGNING_SECRET` | `agenticos-armada-webhook-signing-secret` |

The script stores the PAT in Armada under `AGENTICOS_ARMADA_CI_GITHUB_TOKEN` and
configures the webhook secret directly in Armada. It never prints either value.

## PAT renewal

The fine-grained PAT expires 90 days after it was created; its exact expiry date is
shown in GitHub's token settings page. The Key Vault entry itself has no expiry.
Before the PAT expires, regenerate it with the same scopes, have Azure Ops update
`agenticos-armada-ci-github-token` in `t3-secrets`, then confirm a green `armada`
status on the next commit.
