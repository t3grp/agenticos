# Issue 115 synthetic component evidence

Baseline: `76ac222a4fd32a682e40707dc0fc7d172f8045c2`.

The before images render that commit's actual `TeamRoomResponder` in a synthetic
room frame. The after images render the changed responder and actual
`RoomLeadPanel`, with synthetic routing API data. The baseline copy changes only
its export name and UI import path for the isolated Vite harness. This is component
layout evidence, not full application E2E, live integration, or a deployment.

- Desktop: 1440 x 1000; before-desktop.png and after-desktop.png.
- Mobile: 390 x 844; before-mobile.png and after-mobile.png.
- Both widths passed a horizontal-overflow assertion.
- Synthetic acceptance removed the pending proposal at both widths.
- No provider call or external message was made.

## Verification and PR handoff

235 focused backend tests and 34 focused frontend tests passed. Six backend tests
use real PostgreSQL, including concurrent acceptance, lost-acknowledgment replay,
tenant constraints and the isolated migration body. TypeScript, scoped lint/types,
translation/route/comment guards, diff whitespace and strict documentation build passed.

The PR depends on #90 and targets `feat/multiplayer-subthreads-20261114` as draft
with `full-ci`. Engineer-owned #111 coordination must place #115/0331 after
#110/0330 in migration order and reverse that order for rollback. #111 is unchanged.
The directed migration parent 0330 is absent from this base: `alembic heads` fails
with `KeyError: '0330'`. A complete chain rehearsal and model parity check remain due.

Coverage attempts stop before collection on Pydantic's
`assert isinstance(info, ComputedFieldInfo)` under Python 3.15.0rc2. Coverage is
unverified; the gate is unchanged. No independent review, full-stack E2E, merge or
deployment acceptance is claimed.
