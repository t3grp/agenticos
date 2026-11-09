# T3 integration fork

This is the T3 integration fork of [AgenticOS by Vstorm](https://github.com/vstorm-co/agenticos).
Keep the upstream platform easy to update while developing reusable integrations
and narrowly scoped platform fixes. Preserve upstream attribution and the
[Apache-2.0 license](LICENSE).

## Current state

The initial source baseline is upstream `v1.52.0`, commit
`836efd63eb9129cd7503d90280e4ac637661bd42`. The machine-readable record is
[integrations/upstream.json](integrations/upstream.json).

This initial change adds documentation and baseline metadata. It does not add an
adapter, change application behavior, or establish runtime compatibility.
GitHub Actions is disabled in this fork at repository level. No automatic update
or deployment schedule is configured.

## Branches

| Branch | Purpose |
| --- | --- |
| `main` | Clean mirror of upstream `main`; accept upstream fast-forwards only. |
| `t3` | Fork default branch and reviewed integration baseline. |
| `feat/<integration>` or `fix/<topic>` | One scoped change, reviewed in a pull request to `t3`. |
| `sync/upstream-<version>` | Candidate upstream update, tested before merging into `t3`. |
| `upstream/<fix>` | A reusable fix based on upstream `main`, prepared for contribution. |

Keep T3 commits off `main`. Do not force-update shared branches or routinely
rebase `t3`. Use a merge commit for an upstream-update PR to preserve upstream
ancestry; squash ordinary feature PRs. Always name the repository and target
branch when opening a PR so a fork change is not submitted upstream by accident.

## Local setup

Use `origin` for this fork and `upstream` for Vstorm. In a new clone of the fork:

```sh
git remote add upstream https://github.com/vstorm-co/agenticos.git
git remote set-url --push upstream DISABLED
git config remote.pushDefault origin
gh repo set-default t3grp/agenticos
git fetch upstream main
git switch t3
```

The disabled upstream push URL is a local guard against accidental pushes.
Contributions are pushed to a branch in this fork and proposed through a PR.
If using the sparse checkout created during initial setup, run
`git sparse-checkout disable` before application development to materialize the
remaining source files. It downloads missing files and does not install software.

## Receive upstream updates

1. Review the upstream release notes, security fixes and migration requirements.
   Upstream keeps its major version at 1, including changes requiring operator
   action; a `1.x` version alone is not proof of compatibility.
2. Refresh only the clean mirror with
   `gh repo sync t3grp/agenticos --branch main`. If it refuses a divergent branch,
   inspect the divergence; do not add `--force`.
3. Fetch `origin/main`, `origin/t3` and the selected upstream release tag. Confirm
   the tag's commit against its release record and review the changes from the
   current baseline, including newly added workflow files.
4. Create `sync/upstream-<version>` from current `origin/t3` and merge the selected
   release tag. Resolve conflicts on this branch. Do not merge an unreviewed
   moving branch into a deployment.
5. Update `integrations/upstream.json` with the selected release and full commit.
   Leave compatibility as `not_tested` until actual adapter checks have passed;
   record their commands, results and exact code revisions in the PR.
6. Run the relevant contract tests and the upstream checks required for the
   changed code. Include permission denial, stale data, unavailable services and
   retry behavior. Use deterministic fixtures for model-dependent tests.
7. Open the update PR explicitly against `t3grp/agenticos`, base `t3`. After
   review and passing checks, merge with upstream ancestry intact. Promotion to
   any running deployment is a separate step with its own acceptance evidence.

This is a documented manual update procedure, not an installed sync automation.

## Contribute improvements upstream

Prepare general bug fixes, security improvements and reusable extension points
on a fresh branch based on `upstream/main`. Include a focused regression test
and follow upstream's contribution guidance. Keep fork policy, deployment
configuration and client-specific implementation out of that change.

Submit only the small relevant patch when an upstream contribution is in scope.
After upstream accepts it, use the normal update process to receive it and remove
any superseded local workaround. Preserve authorship, license notices and clear
release attribution; do not present a custom T3 build as an upstream release.

## Workflows and releases

Inherited workflows include upstream-specific image namespaces, package
publishing, Pages, deployment and optional AI review. Repository environment
approval rules and secrets do not become a safe fork configuration merely by
copying their YAML. Keep Actions disabled until a deliberate CI setup is reviewed.

Before enabling CI, select approved runners, separate checks from publishing,
audit all workflow triggers and permissions, and keep deployment credentials out
of untrusted PR jobs. Review new upstream workflows on every update. Do not add
model credentials merely to satisfy the inherited optional AI reviewer.

External MCP adapters can use explicitly pinned upstream images. A platform code
change requires T3-built images and an explicit deployment override: the upstream
Compose files still reference upstream images. Use a distinct T3 image namespace
and record the source commit and image digest before deployment. Copying the
source repository does not deploy its changes.

## Integration boundary

Read [integrations/README.md](integrations/README.md) before adding an adapter.
Keep this public fork limited to reusable code, synthetic fixtures and neutral
examples. Store client-specific business rules, credentials, production settings,
real documents and operational records in their approved private systems.

Reference the upstream [MCP guide](docs/mcp.md),
[HTTP API](docs/api.md), [testing guide](docs/testing.md), and
[branching guide](docs/branching.md) for their current contracts.

## Floating assistant integration

The floating assistant's page-sharing controls and agent selector are described in
[the console guide](docs/console.md#the-assistant). Embedded apps publish their
current view through the bounded setter in
[the app guide](docs/artifacts.md#sharing-context-with-the-assistant).

The frontend attaches a fresh `page_context` to each submitted question while
sharing is enabled. Every console page has a route/title fallback; mounted pages
may register explicit details with `useRegisterAssistantPageContext`. Primary
app viewers register their app identity, title and version. Richer app details
require `window.agenticos.setAssistantContext(details)` when the view changes.
Context registration reads no form values, full page text, query values or browser
selection automatically.

The WebSocket session validates the bounded reference object before saving an
accepted question and appends it as untrusted model input. Page identities do not
grant access, select an agent or trigger a resource fetch. The selected agent
still runs under the authenticated caller's existing permissions. The context
note is not a new transcript field; it is model input subject to the normal run,
trace and compaction behavior. Client outboxes retain the submitted snapshot with
queued questions.

Queued questions freeze the page, selected agent, project and local workspace
after the applicable access gate. Regeneration is a new submission using the
current page and selected agent. The floating chat uses its own conversation
scope, and account or organization changes remount the widget. App registrations
also track the current document nonce and clear when that document changes.

Release the frontend and backend changes together in T3-built images. No database
migration or new dependency is required. Follow the existing validation and
promotion process above; creating a feature branch does not update a running
deployment.
