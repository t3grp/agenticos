# Automatic room routing

This follow-up adds optional AI responder selection to shared rooms. A manager
chooses a current room agent and an organization model profile to decide which
eligible agents should answer an unmentioned human post. The router can select
none, one or several responders. It does not answer the person itself.

The behavior comparison stays pinned to
[Agentes Chat `0546817`](https://github.com/agentes-chat/agentes-chat/tree/05468176f2af476513bea2bb1138d95d244d148b),
particularly its [router](https://github.com/agentes-chat/agentes-chat/blob/05468176f2af476513bea2bb1138d95d244d148b/packages/engine/src/router.ts)
and [router decision policy](https://github.com/agentes-chat/agentes-chat/blob/05468176f2af476513bea2bb1138d95d244d148b/packages/engine/src/router-jev.ts).
The implementation uses T3OS's existing agent runner, organization models,
permissions, run budgets and durable room queue.

## Settings and existing behavior

AI responder selection is optional and disabled when no policy exists. Turning
it off preserves the room's existing per-agent automatic reply settings and
deterministic selection. Newly added agents remain mention-only by default.
Explicit agent requests, human or agent mentions, and `Ask lead` keep their
existing paths and do not acquire additional responders from this router.

The room manager selects a current, published, cloud-compatible room agent as
the router. That agent need not be opted into automatic replies itself. Only
currently eligible agents whose room participation enables automatic replies
can be selected as responders. The router may also be one of those responders
when it is independently eligible.

The model profile must belong to the organization. The policy has an expected
revision, a quiet window from zero to ten seconds, and a maximum of one to four
responders. The default quiet window is 2.5 seconds and the default maximum is
one. Saving a stale revision fails rather than overwriting another manager's
change. Editing authority comes from the room's actual access policy.

The participant panel exposes these settings and explains that the selected
organization model is used for paid routing calls. Policy queries, candidate
lists and receipts remain scoped to the current account, organization and
conversation; changing scope discards a stale editor or result.

## Acceptance and ordering

Accepting a post saves the human message, targetless root request, routing
decision and durable job in one transaction. It does not call a model in the
HTTP request. The decision freezes the requester, accepted thread, policy
revision, router version, eligible agent versions and bounded display metadata.
It also freezes a fingerprint of the selected model profile's configuration;
decrypted credentials are never stored in the decision.

During the quiet window, up to eight contiguous posts from the same person,
thread and policy can become one decision. The newest post is the canonical
accepted message. Older decision rows point to their continuation. A mention,
different author, thread or policy breaks the group. A full eight-message group
stays intact when a ninth post starts another group.

Automatic decisions and ordinary room requests share ordering fences. An older
request settles before a later router runs, and an older routing decision settles
before later ordinary work overtakes it. Responder requests use the existing
ordered room queue, including multiple responders to the same accepted post.

## Model execution and authority

A worker claims a pending decision once. It rechecks the original requester's
membership, room write access, router and candidate versions, current policy,
model profile fingerprint, accepted message fingerprints and inherited context
before constructing the routing prompt. Private and project source rooms also
authorize the actual router and each selected responder.

The routing prompt contains bounded message text, attachment names, candidate
descriptions and recent conversation context. Message text and agent descriptions
are untrusted input. The structured output can choose only accepted candidate
IDs and at most the configured number of responders. A result that needs no
agent or is already answered dispatches none.

The normal agent runner records the router's run and usage. Trusted router mode
removes personal memory, private model context, arbitrary tools, integrations,
delegation and mutable workspace access. It retains applicable published
guardrails. The run has a US$0.05 budget, an eight-second deadline, one model step
and bounded output. The caller remains the original requester for audit; the
router does not impersonate a publisher or a public caller.

Current authority is checked again at provider calls and before accepting the
result. Source conversations are locked in stable order for the final decision.
Changes to the source graph, transcript, policy, membership, profile or candidate
set invalidate the accepted decision rather than silently selecting a new set.
Only then are responder requests saved and enqueued atomically.

## Outcomes and receipts

The durable statuses are `pending`, `running`, `selected`, `abstained`,
`continued`, `failed` and `unknown`. A claimed call is never automatically
replayed after an uncertain worker or provider outcome. Recovery may enqueue
still-pending work or close an abandoned running claim as unknown; it does not
repeat a paid call whose outcome cannot be established.

Visible receipts identify the router, selected responders and terminal outcome.
A read requests at most 100 human message IDs from the current transcript page.
Continuation groups resolve to a canonical receipt, rendered once for the
visible group. Pending or running receipts can poll for completion; an unknown
receipt is terminal and does not imply a retry.

Receipt reads recheck the room and inherited source policy. Responses contain
only accepted message IDs, group IDs, status, router and selected-agent display
snapshots, and timestamps. Internal prompts, model reasoning, provider
configuration, requester details and run records are not exposed through this
API. An invalid stored identity becomes an unknown result rather than an
invented responder.

## Implementation map

| Concern | Location |
|---|---|
| Policy and receipt endpoints | `app/api/routes/v1/room_auto_routing.py` |
| Policy authority and validation | `app/services/room_auto_routing_policy.py` |
| Snapshot, claim, grouping and ordering | `app/repositories/room_auto_routing.py` |
| Classification, current authority and responder dispatch | `app/services/room_auto_routing.py` |
| Restricted runner specification | `app/agents/room_auto_routing.py` and trusted agent-runner integration |
| Durable execution and recovery | `app/worker/tasks/room_auto_routing_tasks.py` and the room reconciler |
| Settings and visible receipts | `room-auto-routing-*` frontend components and scoped hooks |

Migration `0351_room_auto_routing` adds the policy and decision tables after
`0350_room_thread_context`. Database constraints bind each decision to its room,
submission and accepted message and bound the saved snapshots. Downgrade refuses
to erase used routing state. The schema is released with matching API and worker
code; production migration is a separate operation.

## Scope and verification

This change follows the [multiplayer foundation](agentes-chat-parity.md) and
[selected thread context](agentes-thread-ranges.md). Agent-attributed reactions,
social reaction classification and the native room reaction tool remain a
separate follow-up. A routing abstention does not invent a human reaction.

The PR records the exact focused policy, queue, runner, receipt, frontend and
PostgreSQL migration checks. Tests use fake model execution and disposable
PostgreSQL. They do not establish live provider behavior, multi-browser
acceptance or deployment. TypeScript compilation, frontend builds and manual CI
remain outside this phase's authorization.
