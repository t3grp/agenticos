"""`trigger.schedule` - registration and nothing else. See `_handler.py` and `README.md`.

Version 2 adds the tick's weekday, date and time in the workflow's timezone
to the output. Version 1 stays registered so a published graph pinned to it
still resolves, but it is not offered for new steps: its `available` is false,
so the picker skips it while validation and runs keep reading it.
"""

from app.workflows._registry import register
from app.workflows.contracts.definition import TRIGGER_CATEGORY, NodeDefinition, Port
from app.workflows.nodes._triggers import (
    ScheduleTriggerConfig,
    ScheduleTriggerOutput,
    ScheduleTriggerOutputV1,
)
from app.workflows.nodes.trigger_schedule._handler import handle, ports_for
from app.workflows.triggers import SCHEDULE

register(
    NodeDefinition(
        id=SCHEDULE,
        version=2,
        name="Schedule",
        category=TRIGGER_CATEGORY,
        description="Runs on a clock: every so often, or on a cron expression in UTC.",
        kind="action",
        config_schema=ScheduleTriggerConfig,
        input_schema=None,
        output_schema=ScheduleTriggerOutput,
        ports=(Port(id="out", label="Out", kind="output", schema=ScheduleTriggerOutput),),
        effect_kind="pure",
        retry_guarantee="idempotent",
        handler=handle,
        ports_for=ports_for,
    )
)

register(
    NodeDefinition(
        id=SCHEDULE,
        version=1,
        name="Schedule",
        category=TRIGGER_CATEGORY,
        description="Runs on a clock: every so often, or on a cron expression in UTC.",
        kind="action",
        config_schema=ScheduleTriggerConfig,
        input_schema=None,
        output_schema=ScheduleTriggerOutputV1,
        ports=(Port(id="out", label="Out", kind="output", schema=ScheduleTriggerOutputV1),),
        effect_kind="pure",
        retry_guarantee="idempotent",
        handler=handle,
        available=lambda: False,
    )
)
