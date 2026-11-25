"""`trigger.webhook`: a workflow a signed HTTP delivery starts.

Publishing a version with this trigger gives the workflow its own address and a
signing secret, shown once. Each delivery that verifies starts one run with its
JSON body; a retry that repeats the delivery id starts nothing.
"""

from pydantic import BaseModel

from app.workflows.contracts.definition import Port
from app.workflows.contracts.payload_schema import PayloadSchemaConfig, typed_trigger_output
from app.workflows.contracts.results import Completed, Failed, NodeResult
from app.workflows.nodes._triggers import WebhookTriggerOutput, run_input_as


class WebhookTriggerConfig(PayloadSchemaConfig):
    """The shape a delivery's body must have, when the author declares one.

    `payload_fields` / `payload_schema` declare the shape of `body`: a
    delivery that does not fit is refused at admission with
    `WORKFLOW_RUN_INPUT_INVALID` and starts no run. With neither declared
    the body stays any JSON object, as before.
    """


def ports_for(config: BaseModel | None) -> tuple[Port, ...]:
    """`out`, carrying a `body` typed by the declared fields when there are any."""
    typed = (
        typed_trigger_output(config, WebhookTriggerOutput, "body")
        if isinstance(config, WebhookTriggerConfig)
        else None
    )
    schema = typed if typed is not None else WebhookTriggerOutput
    return (Port(id="out", label="Out", kind="output", schema=schema),)


async def handle(config: BaseModel | None, node_input: BaseModel | None) -> NodeResult:
    """Hand on the delivery the run was started with."""
    delivery = run_input_as(WebhookTriggerOutput)
    if isinstance(delivery, Failed):
        return delivery
    return Completed[WebhookTriggerOutput](output=delivery)
