# Browser credential Vault

**Status: default-disabled backend runtime checkpoint; issue #112 remains open.**
Typed storage, owner-scoped selection, durable plans, a single live native writer,
explicit shared-use policy and synthetic Chromium acceptance are implemented.
The runtime fills selected fields and offers a separate, human-confirmed native
form submission. Deployment acceptance and the complete permission-aware Vault
and browser-panel UI remain release requirements. General automation after a
credential fill and saving a new cookie session from a protected tab are not
supported by this checkpoint.

Browser records use the existing secret create, replacement, listing and delete
routes. Creation defaults to `usage_scope=personal` and `visibility=private`;
broader creation requests are refused. Hints are empty, including for short valid
passwords. Replacing a payload requires the whole payload and rotates its
authorization generation. Removing a password does not delete a separately saved
cookie session. Shared use requires the dedicated policy endpoint, an authorized
envelope rewrap and a new generation.

The original design baseline was T3OS
`080c12941ed06cce2e92b6b03910e94ace36469e`, inspected on 14 November 2026.
The benchmark was [OpenBot v0.71.0](https://github.com/nightly-labs/openbot/releases/tag/v0.71.0)
and its [documented browser Vault additions](https://openbot.run/changelog).
This is an independent implementation in T3OS's existing architecture.

## Scope and existing boundaries

Save a login for exact sites, select it without exposing its values to the model,
fill an observed login form and supply a time-based one-time password (TOTP) when
the site asks. A contact/address profile uses the same field-selective flow.
Payment-card storage and payment submission remain outside this feature.

Only the provisioned server browser is eligible. Do not attach a personal browser
or read a password manager, clipboard, browsing history or the user's computer.
API keys, OAuth connections and existing saved cookie sessions keep their
separate paths. A cookie session is not converted into a password.

| Existing module | Integration |
|---|---|
| `app/core/vault.py` | Existing envelope encryption and key rotation; no new master key or encryption mechanism. |
| `app/core/secret_kinds.py` | Typed browser payloads and schemas. |
| `app/services/organization_secret.py` | Owner-checked creation, replacement and deletion; browser values never use generic transport readers. |
| `app/services/browser_credentials/` | Policy, authority, IDs-only plans, lease and single native writer. |
| `app/agents/capabilities/browser_use/` | Existing owned view, input lease, observed elements, protected output and bounded credential request tool. |
| `app/services/live_browse.py` | Strict cache purge and protected event publication before values reach the page. |
| `app/services/browser_sign_in.py` | Separate member-sealed cookie/local-storage sessions. |
| Vault forms and hosted browser panel | Dedicated picker, exact destination/field confirmation, protected state and explicit human handoff; required UI integration. |

Paths in that table are under `backend/`. Read [Secrets and the vault](../secrets.md)
and [Permissions](../permissions.md) for existing access contracts.
[PR #89](https://github.com/t3grp/agenticos/pull/89) is the hosted-panel UX
dependency; integrate its accepted implementation without assuming its release
state or republishing agents.

## Supported user flow and its limits

The backend supports this sequence. The dedicated picker and confirmation
controls must be integrated before enabling it for end users.

1. Save a **Browser login** with a label, username, password and exact allowed
   origins. Store a TOTP seed only when the owner deliberately chooses to do so.
2. Bind that credential to a permitted agent's provisioned browser. The binding
   is frozen in the published version; current policy can subtract authority.
3. In a private live browser run, request eligible metadata for the current
   observed origin. Select the credential and numbered fields.
4. The owning controller prepares the real controls and exposes the exact
   canonical origin and selected field names for the acting person to confirm.
   A generic tool approval cannot authorize the secret write.
5. Before writing values, stop previews, erase cached frames and enable protected
   output. The owning controller fills only those selected native controls.
   The writer does not call form submission during this step.
6. To submit, choose a separate **Submit this form** action tied to the exact
   successful, unexpired fill. The endpoint records a new approval and attempt
   before the same controller calls native `requestSubmit` once. Only same-origin
   native POST forms are supported. A `submitted` receipt acknowledges that
   action; it does not establish that sign-in succeeded.
7. A later same-origin TOTP challenge needs fresh observed handles, a new document
   identity and another confirmation. Output remains protected.
8. If the page requires CAPTCHA, SSO, ambiguous controls or unrestricted
   interaction, end the protected hosted session and direct the person to the
   canonical site in their normal browser. This human handoff does not transfer
   the hosted cookie jar or resume an unprotected credential-bearing agent tab.

Confirmation authorizes delivery of the selected values to the exact approved
origin. Its scripts can react to input events or poll the DOM and may send or
submit those values themselves. The separate submission guarantee covers the
T3OS writer; it does not promise to stop a compromised approved website from
acting on values it has received.

Protected tabs do not offer **Keep me signed in**, screenshots or free-text page
reading. Existing saved cookie sessions remain separate and are not removed when
a password is deleted. The UI must explain this limitation rather than imply
that filling a login enables general post-login automation. The protected view
can show fixed control roles, canonical origin and bounded receipts, and lets the
person decline or close. It cannot show unrestricted page content for human
takeover either: that content may already contain reflected values.

## Stored payloads and origins

Payloads are discriminated, extra-forbidden Pydantic models. Value fields use
`SecretStr`; metadata responses and validation errors never reveal them.

| Kind | Sealed fields | Allowed selection metadata |
|---|---|---|
| `browser_login` | Username, password, exact allowed origins, optional TOTP seed/algorithm/digits/period. | Label, kind, owner/scope, allowed origins, available field names and `has_totp`; empty password/seed hints. |
| `browser_profile` | Name, email, phone, reviewed address fields and exact allowed origins. | Label, kind, owner/scope, allowed origins and available field names; no profile values. |

Accept only canonical HTTPS origins without paths, queries, fragments, userinfo
or wildcards. Hostnames and ports are normalized once. Private IP destinations,
browser-internal pages and non-public DNS resolutions are refused. A future
enterprise intranet policy needs separate review. The top-level page and actual
form destination must both match the exact allowed origin.

TOTP accepts validated Base32 seeds, SHA1/SHA256/SHA512, six or eight digits and
30- or 60-second periods. No opaque `otpauth://` import URL enters the API or logs.
Codes are generated at the final write and are not retained. Under four seconds
of remaining validity yields `totp_window_unavailable`; the person obtains a
fresh plan in a new window. A missing replacement password never means preserve
the old value.

## Ownership, sharing and final authority

A personal record has an immutable owner, `usage_scope=personal` and
`visibility=private`. The private run must act as that exact active owner.
`VaultScope.browser_member(organization_id, user_id)` binds both identities
through the existing envelope mechanism. Creation, replacement, native use and
`vault-rotate` share the scope resolver. Changing either identity prevents
unsealing. Generic secret injection, transport readers and ownership adoption
refuse browser kinds.

Organization-shared use has a separate rollout gate. The immutable owner, with
`secrets:edit`, must explicitly name **both** permitted agents and active
members. The dedicated policy endpoint rewraps between member-and-organization
scope and organizational scope, replaces grants and rotates the generation in
one transaction. Generic metadata editing or resource sharing cannot transfer
browser use. Identical policy replacement preserves its generation. Revoking to
personal scope remains available when filling is disabled.

The explicitly permitted acting member confirms each exact shared fill and each
submission. No additional owner decision per use is required. Metadata visibility
alone never authorizes use. Current owner and acting-member membership, named
agent/member grants, private run provenance, frozen published binding and
`authorization_generation` are checked at the final effect boundary, including
for already approved plans. Shared rooms, delegated runs, API-key callers,
unattended contexts, impersonation and publisher fallback are refused.

The `browser_credential_ids` capability binding is validated at publication and
limited to the provisioned built-in browser. Current published bindings may
subtract authority from a frozen run; later additions cannot silently expand it.
The credential row is locked through the bounded final effect. Payload
replacement and policy rewrap take that same row lock, so generation changes
cannot pass the writer's final check concurrently.

## IDs-only API and bounded controller

Requests never contain a CDP URL, JavaScript, CSS selector, destination URL or
credential value. A plan request identifies existing observed elements:

```json
{
  "request_id": "<new idempotency UUID>",
  "call_id": "<owning browser call>",
  "tab_id": "<observed tab>",
  "secret_id": "<eligible vault UUID>",
  "authorization_generation": "<current generation UUID>",
  "fields": [
    {"field": "username", "element_id": 0},
    {"field": "password", "element_id": 1}
  ]
}
```

The run ID comes from the route. Normal browser snapshots number controls from
zero; request IDs address that exact observation, never an arbitrary selector.
The live controller stores actual node references in an isolated-world closure
and produces opaque document and field identities. It checks visibility,
connection, hit-testing, native control type, exact form action and same-document
identity again before delivering selected values. Hidden, readonly, replaced, ambiguous or
cross-origin controls are refused.

| Surface | Contract |
|---|---|
| Existing secret creation/replacement routes | Validate and seal typed values; return metadata only. No reveal route. |
| `GET /v1/runs/{run_id}/browser-credentials?call_id=...&tab_id=...` | Eligible metadata for the current owned browser and live origin. |
| `POST /v1/runs/{run_id}/browser-credential-plans` | Queue an IDs-only request; return 202. Does not approve or execute filling. |
| `GET /v1/runs/{run_id}/browser-credential-plans/{id}` | Read this acting person's receipt; expire abandoned work without retry. |
| `POST /v1/runs/{run_id}/browser-credential-plans/{id}/confirmation` | Exact `document_id`, `authorization_generation` and `approve`; confirm or decline as the acting person. |
| `POST /v1/runs/{run_id}/browser-credential-plans/{filled_id}/submission` | Separate explicit human action with new `request_id`, exact filled document and generation; return its child plan. |
| `GET /v1/runs/{run_id}/browser-credential-plans/{filled_id}/submission` | Read the one existing submission child or `null`; never confirms, creates or retries an attempt. |
| `GET/PUT /v1/secrets/{secret_id}/browser-policy` | Immutable-owner-only policy. PUT requires `generation`, `mode` and both `agent_ids`/`member_ids` for shared use. |
| `browser_credential_options` | Eligible metadata from the owning live controller. |
| `browser_fill_credential` | Request by IDs and keep that controller alive during a bounded wait for dedicated human confirmation. |

All routes send `Cache-Control: no-store`. Receipts contain IDs, operation and
parent fill, field names, canonical origin, opaque document identity, expiry,
bounded state/reason and `output_protected`. Native handles, selectors, arbitrary
page errors and protocol payloads are not API output.

A filled parent exposes `submission_expires_at`, derived from its successful
completion plus the existing 60-second submission window. Its earlier
`expires_at` is the fill confirmation deadline. The separate Submit action both
creates and confirms its child; it needs no second confirmation. After a lost
response or reload, read the existing child with GET before offering any retry.
Retain the same request UUID for an explicit retry of an uncertain request.
Every filled parent permits at most one submission child, including after a
terminal or unknown outcome. No polling path issues a new POST.

The request tool itself is non-side-effecting: its IDs cannot approve a write.
The actual native effect always uses `ApprovalService` with `always_ask`.
Claim checks the exact actor, run, agent, tool, capability and plan. The generic
approval queue contains only the plan ID and fixed explanation. Deciding that
generic item cannot replace the dedicated document/generation confirmation.
Native handles are never parked for unrelated approvals or resumed after restart.

Plans expire in 60 seconds. The controller lease lasts 90 seconds and renews
every 20; advertised current origin lasts at most five seconds. The request tool
waits at most 130 seconds and the controller closes after 150 idle seconds.
Effects have a 20-second bound and individual protocol requests a five-second
bound. Closing or parking the owning native tab tears down its controller.
Disconnecting a viewing WebSocket only closes that view; use the existing Stop
run action to end the hosted run during a human handoff.

The durable claim commits before the external effect. A second transaction
rechecks current authority and holds the credential lock through that effect.
A timeout, cancellation or lost acknowledgement after claiming becomes terminal
`unknown`; it never retries automatically. A successful fill may have only one
submission child. An unknown submission cannot be reopened. A fresh process,
document, lease or credential generation requires new observation and confirmation.

## Sticky plaintext and output boundary

The server decrypts typed payloads in memory when it derives origin and field
metadata during eligibility, request, preparation and claim checks. Decryption
is not deferred until the final native call. Those decrypted values never enter
model arguments, tool results, API or event output, messages, audit payloads,
exports, stored plans or error details. Only explicitly selected fields are
delivered to the page, after confirmation, output protection, durable claim and
the final current-authority and generation checks.

Before the first write, stop and join the preview stream, clear the cached last
image, and transactionally remove Redis frame, location and trace caches.
Publish a fixed `browser_protected` event to every earlier call bound to that page
before writing values. Failure of any view's purge refuses the write. Every later
guarded event also carries `output_protected=true`; clients must treat it as sticky,
including on reconnect snapshots. Cached publication and teardown share a lock:
an in-flight old frame finishes before all view caches are purged and cannot be
replayed during later teardown. Reconnect checks protection again after subscribing,
and the socket drops queued pictures when protection arrives.

Protection is sticky for the tab's lifetime, including redirects, errors and
uncertain outcomes. Text reads return a fixed explanation. Locations expose only
the first approved canonical origin. Screenshots, cookie capture and arbitrary
JavaScript reads are unavailable. Isolated-world snapshots return fixed control
roles and fresh IDs without values, labels, options, titles, body text, errors or
URL paths/queries. General browser actions requiring unrestricted DOM output are
unavailable. Teaching and artifact paths must respect this boundary rather than
create another raw capture path.

The real Chromium fixture reflects a synthetic password into body text, labels,
an image, the title and URL query. Protected outputs omit every reflection;
preview and cookie capture make no protocol calls. Navigation or Submit never
restores generic capture. This deliberately limited observation interface does
not claim safe unrestricted automation after login.

The reviewed optional transport logs raw protocol parameters, replies, events and
exception messages. `_transport_privacy.py` therefore installs permanent filters
before importing or connecting it. Exact loggers `websockets.client`,
`cdp_use.client` and `cdp_use.cdp.registry`, their descendants and every
configured sink stay protected for the complete connection lifetime. Missing
filters, a newly unprotected sink or an unreviewed package version refuse
credential admission. Fixed application reasons remain available. Raw protocol
debugging cannot be enabled on a credential-bearing connection.

## Tests and remaining acceptance

Focused cases live in `backend/tests/test_browser_credential_*.py`,
`backend/tests/integration/test_browser_credential_*.py` and
`backend/tests/api/test_browser_credential_routes.py`. They cover storage
scopes, shared rewraps, policy/membership/generation changes after approval and
claim, durable attempts, expired/lost leases, output failures, cancellation,
transport filtering and normalized refusal payloads. Existing browser, secret
escape and saved-session regressions remain required.

`test_browser_credential_chromium.py` uses one sandboxed, disposable Chromium
process and synthetic credentials. CDP Fetch fulfills only the exact synthetic
origin and refuses other page requests. Set `T3OS_TEST_CHROMIUM_EXECUTABLE` to a
reviewed browser for this explicit acceptance batch; its release receipt must
show zero skipped cases. The disposable profile uses the OS temporary directory
so Windows sandbox access does not depend on repository-drive ACLs.

The native batch executes real isolated-world JavaScript through `CdpPage`:
index-zero login fields, selected-only filling, separate submission, TOTP/profile
stages, hidden/replaced controls, changed documents and hostile secret reflection.
It uses no real site, personal profile, live credential or paid model.

UI acceptance must still verify schema-driven forms, accessible labels,
translations, account/organization switching, exact destination and field review,
decline/close, unknown-without-retry, cached-frame clearing and the documented
human handoff. Broader safe post-login automation and cookie persistence require
a separately reviewed continuation design. This checkpoint does not complete
that part of #112.

## Reviewed native dependencies

Command shapes were checked against Chrome DevTools Protocol commit
[`dd55cb67621a728bba3d23e056f2de2ca1e7c906`](https://github.com/ChromeDevTools/devtools-protocol/tree/dd55cb67621a728bba3d23e056f2de2ca1e7c906),
specifically `pdl/js_protocol.pdl` and `pdl/domains/Page.pdl`. The writer uses
an isolated-world object-bound closure, native setters and explicit object
release; a recycled execution-context number is not a document identity.

Credential admission requires exact locked `cdp-use==1.4.5` and
`websockets==17.4`. The reviewed cdp-use wheel SHA-256 is
`8f8e2435e3a20e4009d2974144192cf3c132f6c2971338e156198814d9b91ecb`.
Its `cdp_use/client.py` and `cdp_use/cdp/registry.py` establish the payload
logging boundary. An upgrade requires a new source/logging review and native
acceptance before changing the version constants. The synthetic batch tests the
real browser and application commands through a small transport bridge; it does
not replace deployment acceptance of the installed optional transport, Redis
purge and panel behavior.

The locked websockets 17.4 source archive has SHA-256
`09d3939b70df0c8ad2da145ebd50978bccaee1d565e838b0e7921a34d38571eb`.
Its `src/websockets/asyncio/client.py` chooses `websockets.client` and passes that
logger to `ClientProtocol`; `asyncio/connection.py` wraps the same logger with a
`LoggerAdapter`, while `protocol.py` logs frames and failures through it. The
upstream cdp-use filter is appended to the logger and does not remove the guard
installed before import. Tests cover this ordering and all reviewed namespaces,
including records from late callbacks and child loggers at changed levels.

## Rollout, migration and rollback

No existing credential is converted automatically. Migration
`0333_browser_credential_plans` follows `0332_room_task_handoffs`, retaining
published `0326_provider_account_pools` and `0331_merge_core_accounts` history.
It adds four policy/grant/plan tables and tenant keys `uq_agents_org_id`,
`uq_agent_runs_org_id` and `uq_organization_secrets_org_id`. Key indexes build
concurrently outside transactional DDL and then attach as constraints. Active
builds and unexpected same-name objects refuse; only an exact valid standalone
index is reused, and only an exact invalid leftover may be rebuilt. Allow for
short constraint-attachment locks and rehearse on a restored database. Do not
wrap concurrent index DDL in an outer migration transaction.

`BROWSER_CREDENTIAL_FILL_ENABLED` defaults false. Enabling also requires the
organization in `BROWSER_CREDENTIAL_ORGANIZATION_IDS`; optionally restrict users
with `BROWSER_CREDENTIAL_USER_IDS`. TOTP, profiles and shared use require separate
`BROWSER_CREDENTIAL_TOTP_ENABLED`, `BROWSER_CREDENTIAL_PROFILE_ENABLED` and
`BROWSER_CREDENTIAL_SHARED_ENABLED` flags, all false by default. The published
capability must use the built-in browser at exactly `BROWSER_SIGN_IN_CDP_URL`.
Personal/native-device browsers and arbitrary CDP endpoints are ineligible.

Enable only a synthetic organization after migration, dependency installation,
UI integration and deployment-level output/cache tests. Then admit selected users
explicitly. Environment flags are not evidence that those checks passed.

Disabling blocks new and pending use. Sealed records remain manageable, rotatable
and deletable through their normal authorized paths. Existing cookie sessions
remain separate. Downgrade of 0333 refuses any policy or plan evidence, including
requested, filled and unknown work. Deleting evidence to force rollback would
discard the record preventing unsafe retries; that is not an automatic rollback
strategy. Do not deploy an older reader that cannot safely parse browser metadata
without an explicit compatibility plan.
