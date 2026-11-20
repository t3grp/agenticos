# Room reactions

The native room runner injects this capability into an authorized top-level
responder. It is not selectable in an agent specification. The server binds the
organization, requester, accepted message, room, thread, agent, published version,
and run; none of those identities can be supplied by the model.

`react_to_room_message` accepts an emoji, an optional visible target message, and
`reaction_only`. With no target it reacts to the accepted human post. Every write
rechecks the live submission, run, room membership, target visibility, and selected
context authority. It refuses the agent's own message. A reaction-only action can
target only the accepted human message.

Actions are append-only and idempotent for the recorded run/tool-call identity.
They retain a frozen agent name and version. Human reactions stay in their existing
table. Aggregates count each agent once per emoji, include agent attribution, and
keep `reacted_by_me` specific to the human reader. Humans and agents share the
twenty-distinct-emoji limit on each message.

A successful run whose only work is one reaction-only action can finish without
an empty assistant row. Previous segments, text, another tool, failure, and
cancellation retain the ordinary transcript. The action ledger and run usage
remain available through the authorized, bounded run audit.

The optional automatic router uses the same action service with a separate
decision identity. It can react only to the accepted human post, only when no
responders were selected, and only with server-proven agent attribution.
