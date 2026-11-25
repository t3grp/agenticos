# trigger.schedule

A workflow that runs on a clock: every so often, or on a cron expression in UTC.
The cadence and the input every run starts with are this node's configuration;
publishing a version is what schedules it.

| Port | Kind | Schema | What it carries |
|---|---|---|---|
| `out` | output | `ScheduleTriggerOutput` | `fired_at`, `input`, `weekday`, `local_date`, `local_time` |

`weekday`, `local_date` and `local_time` are the tick in the workflow's
timezone - the English day name, the ISO date and the `HH:MM` time - derived
from `fired_at` when the tick is admitted, never author-supplied. Version 2
added them; version 1, which hands on only `fired_at` and `input`, stays
registered so a published graph pinned to it still resolves, but is not
offered for new steps.

A tick that finds the last run still going is skipped rather than stacked, and a
schedule whose member can no longer run the workflow is switched off and audited.
It does not catch up on ticks it missed while the worker was down.
