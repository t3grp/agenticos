# Agent reactions in shared rooms

Agents can acknowledge a room message with an attributed emoji. The acting agent
comes from the accepted room request, so a model cannot impersonate another
responder, choose another organization or post into another room through tool
arguments. Human reactions retain their existing identity and toggle behavior.

## Reference and scope

The behavior comparison uses [Agentes Chat at the pinned revision](https://github.com/agentes-chat/agentes-chat/tree/05468176f2af476513bea2bb1138d95d244d148b),
particularly its [chat tools](https://github.com/agentes-chat/agentes-chat/blob/05468176f2af476513bea2bb1138d95d244d148b/packages/engine/src/chat-tools.ts),
[feedback context](https://github.com/agentes-chat/agentes-chat/blob/05468176f2af476513bea2bb1138d95d244d148b/packages/engine/src/context/feedback.ts)
and automatic routing. T3OS uses its own room authority, agent runner, durable
submission queue and reaction events to provide that behavior.

This follow-up builds on the multiplayer foundation, selected thread context and
[AI responder selection](agentes-automatic-routing.md). Migration
`0352_room_agent_reactions` follows `0351_room_auto_routing`. Publishing a draft
PR does not apply either migration or deploy the feature.

## Trusted room tool

The internal `room_reactions` capability exposes one tool,
`react_to_room_message`. Its arguments are an emoji, an optional message ID and
an optional `reaction_only` flag. Omitting the message ID targets the human post
accepted for that responder. A supplied target must be a readable saved human
or assistant message in the same room and thread. The responder cannot react to
its own message.

The room worker binds the organization, room, submission, original requester,
accepted message, thread, agent and pinned version. The runner supplies the run
identity. The model supplies none of those authority fields. Each action checks
the current submission and run, the requester's current access, the responder's
eligibility and the target's visibility before writing.

Every top-level native room responder uses this binding, including additional
explicitly mentioned responders and responders selected automatically. Resumed
room work resolves its own submission again and rechecks authority. Delegated
subagents, private conversations, external channels, side questions, the router
classifier, evaluations, public entry points and Ask Lead do not receive the
native room reaction tool.

## Reaction-only completion

The default tool call allows a normal text answer alongside the reaction.
`reaction_only=true` is an explicit request to use one successful reaction to
the accepted human post as the complete reply. It suppresses an empty final
room reply only when that run completes successfully, has exactly that one
successful tool call and has no text output.

Text output, another tool call, an unsuccessful reaction, cancellation or failure
prevents this suppression. Existing assistant messages are never deleted. The
normal agent run, model usage and tool counts remain available even when the
room displays only the reaction. A successful empty reaction-only completion
has no assistant message or generic transcript tool-call row; its immutable
reaction action retains the tool-call identity and outcome. Other completion
paths preserve the usual transcript and tool-call records.

## Action provenance and idempotency

Agent actions are stored separately from human reaction rows. Each action keeps
the agent identity, frozen display name and pinned version together with its
run and source. Tool actions retain their submission and tool-call identity;
automatic actions retain their routing decision. These fields make the acting
agent visible without presenting a human user as the source.

A tool retry is identified by its run and tool-call ID. Repeating that identity
with the same target, emoji and completion mode returns the existing action.
Reusing it for another action is refused. Different runs may use the same
tool-call ID. An automatic action is unique to its routing decision and cannot
be replayed into another reaction after an uncertain router outcome.

The ledger is append-only for accepted actions. Repeated actions by one agent
with the same emoji contribute one visible agent to the aggregate. Different
agents remain distinct even when they have the same display name.

## Authorized run audit

The existing run transcript endpoint includes a separate `room_reactions` list
after its normal organization, run-view and source-context authorization checks.
The reaction service also checks the current viewer's access to each action's
room and target source context. This covers automatic router runs that have no
conversation ID. The list contains safe action metadata: target message, frozen
agent identity and version, emoji, completion mode, source, tool-call or decision
identity and creation time. It excludes message content and routing prompts.

Actions are ordered by creation time and stable action ID. The response includes
at most one hundred rows and sets `room_reactions_truncated` when more exist.
Both fields have empty defaults for older responses. The run timeline displays
these actions in their own audit section, without inventing a chat message.

## Aggregation and room display

The existing reaction summary keeps `emoji`, `count` and `reacted_by_me`, and
adds `agents: [{agent_id, name}]`. Its agent list defaults to empty for older
human-only data. Counts combine the signed-in human actors and distinct agent
identities. `reacted_by_me` continues to describe only the viewing human's own
selection, so an agent's reaction does not activate a person's toggle.

The transcript exposes agent attribution beside the reaction count, using the
saved name while preserving the stable agent ID. The same aggregate is used in
main rooms and side threads. Realtime events carry the room and message IDs
after commit; clients reload the authorized durable aggregate.

Human and agent actions share the ceiling of twenty distinct emoji per message.
The check and write use the existing room turn lock. Another actor may join an
emoji already present at that ceiling, while a twenty-first distinct emoji is
refused regardless of whether the actor is human or an agent.

## Conservative automatic acknowledgements

An enabled AI routing policy can return a bounded social intention in addition
to its responder decision. The router remains tool-free. The server decides
whether to persist a reaction and which accepted eligible agent can own it; the
model cannot post a reaction or choose an arbitrary target through this output.

The target is always the canonical accepted human message. Any selected text
responder suppresses the automatic reaction. Human-directed discussion and
conversation between humans are not automatically acknowledged by an agent.
Personal acknowledgement emoji require a proven addressed agent or prior
responder. Multiple named targets or conflicting trusted attribution signals
produce no action. General non-person emoji can use a deterministic ranking of
the accepted eligible candidates when no personal addressee is established.

Before persisting, the service rechecks the decision's current claim, its exact
completed router run, current policy, original requester, candidate snapshot,
agent version, room participation and inherited-context visibility. The
automatic action references that decision and router run even though the
classifier itself has no room transcript conversation.

## Validation and release boundary

The focused verification covers trusted actor binding, repeated tool IDs across
runs, additional responders, access changes, same-thread targets, the combined
emoji ceiling, aggregate attribution and the reaction-only completion cases.
Migration checks exercise real PostgreSQL DDL and refuse a downgrade that would
discard accepted agent actions. The PR records the exact commands and results.

These checks do not establish a deployed migration, live paid-provider behavior
or multi-browser acceptance. The feature is published as a separate draft
follow-up so those release gates remain explicit.
