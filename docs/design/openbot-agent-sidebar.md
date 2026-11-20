# Agent identity beside chat

The behavior reference is OpenBot.run v0.74.0 at
[`8fc29851e68976b5d408fda0aeaf5d60f3826326`](https://github.com/nightly-labs/openbot/tree/8fc29851e68976b5d408fda0aeaf5d60f3826326).
Its agent settings panel exposes a picture, name, short title and description.
T3OS uses its existing agent registry and publication flow for those controls.

## User behavior

The desktop chat settings pane offers picture upload, replacement and removal,
plus the existing fallback color picker. Picture and color changes take effect
immediately. The pane explains that these differ from versioned draft changes.

The chat settings pane and full Builder both edit a short title of up to eighty
characters. Save, discard, reset and publication use the existing draft flow.
The agent picker and empty conversation show the title from the current published
version. Reading an agent list never projects a title from its current draft.

## Storage and access

Spec version 16 adds `title`, defaulting to an empty string. Validation trims
surrounding whitespace before checking the length. Old JSONB specs remain readable;
there is no database backfill or SQL migration for this field.

Appearance changes require the existing per-agent edit decision, including resource
grants. The DELETE avatar route delegates that decision to the registry service.
It locks the row, clears its picture reference, and schedules best-effort file
cleanup after commit. A failed database update schedules no deletion. A storage
failure after commit does not restore a stale image reference.

Published title lookups use the current version ids of already-visible agents.
No new discovery endpoint or permission is introduced.

## Review and validation

Focused coverage exercises legacy specs, bounds and whitespace, published-vs-draft
projection, edit denial, idempotent removal, transaction ordering and cleanup failure.
The sidebar integration tests exercise saving and discarding titles and the protected
removal endpoint; appearance component tests cover upload, removal and color reset.

The identity slice is reviewed separately from generated appearance controls,
personal folders, notification preferences, model fallback overrides and runtime
chat policies. Those features have their own changes and validation. Local checks
do not establish deployed browser acceptance, and publication remains a draft PR.
