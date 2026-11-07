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

Build both images from the same reviewed revision:

```sh
docker build -t agenticos-native-demo:subscriptions integrations/native-subscriptions
docker build -t agenticos-frontend-demo:subscriptions frontend
```

The examples use `pull_policy: never`, so both selected images must already be
present on the target Docker host. Use immutable image references for a release.

### Separate Coolify resources

Use two Coolify Service resources: the existing AgenticOS application and a new
resource containing only the native broker, such as
`agenticos-native-subscriptions-demo`. Use [compose.yaml](compose.yaml) as the
new resource's complete source. Do not append the broker to the application's
Coolify Compose definition or copy the application's environment into it.

The tested Coolify 4.6.0 Service deployment
[adds its generated resource `.env` to every service](https://github.com/coollabsio/coolify/blob/v4.6.0/bootstrap/helpers/parsers.php#L3609-L3617).
That parser provides no per-service opt-out: an empty `env_file` list still
receives `.env`. An explicit Compose `environment:` list therefore does not
isolate a broker placed in the application resource from database, cache, signing
or vault credentials. The separate resource makes its generated environment
contain only native-service settings and the dedicated internal key.

Create both resources on the same Docker host. The broker example defines its
own `edge` bridge and joins the external `coolify` network through `proxy`. The
existing API and frontend must also join `coolify`; the
[demo application compose](../coolify-demo/compose.yml) already includes those
connections. Review Coolify's
[source and deployable Compose](https://coolify.io/docs/services/configuration/docker-compose)
views before starting the resource: generated container names, environments,
networks and routes determine what runs.

Configure only these integration values in the **native resource**:

| Setting | Purpose |
| --- | --- |
| `NATIVE_SESSION_IMAGE` | Built broker image tag or immutable image reference |
| `NATIVE_SESSION_SERVICE_KEY` | Dedicated random internal key, at least 32 characters; set the same value in the existing application resource |
| `NATIVE_SITE_ORIGIN` | Exact existing HTTPS console origin, matching the frontend's `PUBLIC_SITE_URL` |
| `NATIVE_BACKEND_URL` | `http://agenticos-api-REPLACE_WITH_APP_RESOURCE_UUID:8000`; use the actual scoped API container name from the existing resource |
| `NATIVE_PUBLIC_API_HOST` | Existing public API hostname, without a scheme |
| `NATIVE_BLOCKED_CIDRS` | Comma-separated public IPv4 addresses or CIDRs for this host and other infrastructure that native users must not reach |

Only the four runtime settings under the broker's `environment:` entry are
needed by its process. Keep application database, cache, signing, vault and model
provider credentials out of this resource's environment settings and secret
manager bindings. Never commit a populated environment file.

Merge [frontend.override.yaml](frontend.override.yaml) into the **existing
application resource**. Retain its frontend networks, routes, other environment
values and existing dependency on its own API. Configure these values there:

| Setting | Purpose |
| --- | --- |
| `NATIVE_FRONTEND_IMAGE` | Built frontend image containing the subscription UI and BFF |
| `NATIVE_SESSION_URL` | `http://agenticos-native-subscriptions-REPLACE_WITH_NATIVE_RESOURCE_UUID:8080`; use the separate broker's actual scoped container name |
| `NATIVE_SESSION_SERVICE_KEY` | The same dedicated internal key as the native resource |

The placeholders refer to two different Coolify resources. Copy the exact
container names from their deployable Compose definitions; unscoped aliases such
as `agenticos-api` can collide on a shared network. The URLs are internal HTTP
base URLs with their container ports, without `/api/v1` or `/session` suffixes.

Do not add a cross-resource `depends_on`: Compose can only refer to services in
its own project. Start with the existing API healthy, deploy and check the native
resource, then deploy the updated frontend. A failed or unavailable broker is
reported by the subscriptions page rather than starting a session automatically.

Keep the existing `PUBLIC_SITE_URL`, `PUBLIC_API_URL` and `PUBLIC_WS_URL`. The
broker's router shares the public API hostname and matches only
`/native-subscriptions/terminal`; `/session` and `/health` remain private. Adapt
the Traefik router/service label names and certificate resolver to the installed
proxy, using unique label names if multiple brokers share it. Do not add a
hostname-only route to the broker or publish port 8080 on the host.

The frontend forwards the namespaced access cookie and selected organisation to
the broker over the container network. The browser's native terminal uses a
one-use ticket; it receives neither the internal service key nor an application
bearer token from this integration. Store the dedicated key in protected settings
for each resource. Coolify may share it among the existing application resource's
containers; the broker must still receive no application credentials.

### Plain Docker Compose

Outside Coolify, the broker and frontend overlay can be merged into the same
application project when the orchestrator does not inject a shared environment
file into every container. Keep the broker's environment explicit, set
`NATIVE_BACKEND_URL=http://agenticos-api:8000` and
`NATIVE_SESSION_URL=http://agenticos-native-subscriptions:8080`, and attach the
three services to an appropriate shared container network.

The supplied proxy network and labels target Coolify's Traefik setup. Adapt them
to the local reverse proxy and route the exact terminal path at `PUBLIC_WS_URL`
to the broker, preserving the browser's Origin header. The private demo's direct
API tunnel alone does not provide this additional WebSocket route. Keep the same
origin checks, service key and container isolation in either deployment layout.

### Container isolation

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
Check the broker's rendered environment names for application credential leakage,
its separate resource and state volume, and both scoped internal DNS targets.
Inspect names and presence checks without printing secret values.

## Rollback

Restore the previous frontend image in the application resource and remove its
two native-session environment variables, then stop the separate native resource
and remove its public WebSocket route. Keep that resource's state volume until
authorised account logout or retention cleanup has been completed. No database
migration is required. Existing API model profiles, organisations and contact
tables are not modified by this integration.
