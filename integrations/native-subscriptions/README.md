# Personal Claude subscriptions

This optional integration adds **Use a subscription** to AgenticOS. A signed-in
member can open the subscriptions page, sign in to Claude themselves and use the
unmodified Claude Code CLI in a private terminal.

The subscription terminal is separate from AgenticOS model profiles. It does not
supply a Pydantic AI provider, change an agent's model, attach AgenticOS tools or
mount organisation records. API-backed agents continue to use their existing
provider credentials. Native CLI login credentials stay in the native CLI's own
per-user home; this integration never reads or imports their contents.

Anthropic permits hosting an unmodified native Claude Code instance when each
end user signs in with their own account and is billed directly. Review the
[current legal guidance](https://code.claude.com/docs/en/legal-and-compliance)
and [authentication documentation](https://code.claude.com/docs/en/authentication)
before enabling this integration. A subscription does not grant API credits.
The CLI is installed from the official package with a pinned version and retains
its own authentication flow and provider notices.

## User flow

1. Choose **Use a subscription** from a model picker or Settings → Coding accounts.
2. Choose **Sign in**, then select the Claude account option in the native CLI to
   use a subscription. Follow its sign-in link and complete account confirmation
   yourself. No account is connected automatically.
3. Continue in the terminal after login. On a later visit, choose **Open session**
   or **Reconnect**. To change the active sign-in, enter "/login" in the terminal.
4. Use **Reconnect** to reattach to a running session, **Stop session** to end it
   while keeping the sign-in, or **Sign out** to invoke the CLI's native logout.

Each account is scoped to the current AgenticOS user and organisation. Changing
organisations closes the browser terminal; it never reuses another organisation's
home. The native CLI retains all of its own authentication methods. A Claude
account sign-in uses that account's subscription. An API or provider sign-in is
labelled as separately billed and requires the explicit **Open with separate
billing** action. There is no automatic billing fallback.

## Deployment

Build both images from this branch:

```sh
docker build -t agenticos-native-demo:subscriptions integrations/native-subscriptions
docker build -t agenticos-frontend-demo:subscriptions frontend
```

Merge `compose.yaml` with the existing demo compose file. The sample assumes the
`edge` network, the external `coolify` proxy network and an API service named
`agenticos-api`. Adapt the router name and certificate resolver to the installed
proxy. Only the exact WebSocket path is public; `/session` and `/health` remain on
the container network.

| Setting | Purpose |
| --- | --- |
| `NATIVE_SESSION_IMAGE` | Built broker image tag or immutable image reference |
| `NATIVE_FRONTEND_IMAGE` | Frontend image containing the subscription UI and BFF |
| `NATIVE_SESSION_SERVICE_KEY` | New random internal key, at least 32 characters; set identically in the frontend and broker |
| `PUBLIC_SITE_URL` | Existing HTTPS console origin |
| `NATIVE_PUBLIC_API_HOST` | Existing public API hostname, without a scheme |
| `NATIVE_BLOCKED_CIDRS` | Comma-separated public IPv4 addresses or CIDRs for this host and other infrastructure that native users must not reach |

Keep the service key in the deployment's secret store. Never commit a populated
environment file. The frontend forwards the existing namespaced access cookie
and selected organisation to the broker over the private network. The browser
does not receive the service key or an application bearer token.

The container must run with `init: true`, its own PID/network namespaces, a
read-only root, the listed small capability set, IPv6 disabled and a writable
`/state` volume. Do not mount the Docker socket, host directories, application
credentials, CRM records or another user's home.

At startup the broker installs a UID-scoped firewall and drops its network
administration capability before accepting requests. Native users can use the
Docker resolver and public TCP 443; private, loopback, link-local, metadata and
configured infrastructure addresses are blocked. The root broker can reach only
what its container network and deployment permit, including the fixed application
API needed to check identity. Changing these constraints requires a separate
deployment review.

The sample is intended for a trusted internal demo. Users have separate Linux
UIDs and homes inside one container, while sharing its kernel, memory limit and
process budget. This is not a per-user VM or container sandbox. The CLI can run
commands as its native user and can contact public HTTPS destinations. Operators
with root access to the host or volume can access native account data. The native
CLI's credential persistence is outside AgenticOS's application vault; use the
native **Sign out** action when a demo account is finished.

## Session and transport limits

- One running session and one attached writer per user/organisation.
- Four running sessions per broker, one broker worker, no horizontal replicas.
- One-hour maximum session life; 15-minute input-idle limit; 90-second detached limit.
- A 700-mode home and unique, non-reused UID for each user/organisation.
- Home limits of 512 MiB allocated storage and 20,000 entries; quota checks fail
  closed. They are periodic checks, not filesystem quotas.
- Bounded in-memory terminal replay; no broker transcript or access logging.
- Native helper output is bounded and never returned as raw authentication data.
- WebSocket tickets expire after 45 seconds, are single-use, and bind the verified
  user, organisation and native session generation.
- Origin and application membership are checked at connection; membership is
  checked again every 30 seconds. The browser renews its ticket every 60 seconds.
- Stop, expiry and revoked access terminate native descendants, including
  detached processes; UID cleanup uses pidfds to avoid PID-reuse mistakes.

The terminal itself is an authorised user interface to the native CLI. It can
show sign-in links or codes and the user's own terminal output. Do not record
terminal content, add it to analytics, or include it in support logs.

## Verification

Run the Linux runtime tests in a disposable root container with its own PID
namespace and no network. Never run the UID process-cleanup tests as root on the
host, and do not parallelise these fixtures.

```sh
docker run --rm --init --network none --read-only \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,exec,nosuid,nodev,size=268435456,mode=1777 \
  -v "$PWD/integrations/native-subscriptions:/app:ro" -w /app \
  --entrypoint python agenticos-native-demo:subscriptions \
  -m unittest -v test_native
```

From `frontend/`, run the native-subscriptions server/protocol/hook tests, terminal
tests, subscription workspace integration tests and model-profile-picker tests,
followed by TypeScript, scoped ESLint and the production image build. The fixtures
use a fake CLI and mocked authentication; they do not sign in to Anthropic or
make model calls.

For the deployed configuration, separately verify the startup firewall, removal
of network-admin capabilities, read-only root, cross-user home denial, anonymous
request refusal, WebSocket proxy route and the actual page in a signed-in browser.

## Rollback

Restore the previous frontend image and remove its two native-session environment
variables, then stop the native broker and remove its public WebSocket route.
Keep the state volume until authorised account logout or retention cleanup has
been completed. No database migration is required. Existing API model profiles,
organisations and contact tables are not modified by this integration.
