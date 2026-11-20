"""The routing classifier cannot inherit the responder's tools or standing context."""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.agents.room_auto_routing import automatic_routing_spec
from app.agents.spec import AgentSpec, CapabilityBindingSpec, OrgMcpServerRef

pytestmark = pytest.mark.security


def test_classifier_has_only_supplied_context_and_preserves_published_governance() -> None:
    guardrails = CapabilityBindingSpec(id="guardrails", config={"redact_secrets_in": True})
    thinking = CapabilityBindingSpec(id="thinking")
    schema = {"type": "object", "properties": {"selected_ids": {"type": "array"}}}
    spec = AgentSpec(
        name="Room router",
        instructions="Read {{private_note}} and browse my signed-in account.",
        variables=[{"name": "private_note", "value": "Personal standing context"}],
        capabilities=[
            guardrails,
            thinking,
            CapabilityBindingSpec(id="web_search"),
            CapabilityBindingSpec(id="memory"),
            CapabilityBindingSpec(id="council"),
        ],
        mcp_servers=[OrgMcpServerRef(connection_id=uuid4())],
        skill_ids=[uuid4()],
        context_ids=[uuid4()],
        collection_ids=[uuid4()],
        code_mode={"enabled": True},
        budget={"monthly_usd": Decimal("5")},
        output_schema=schema,
        model_settings={"max_tokens": 12000, "timeout": 120},
        max_steps=50,
    )

    routed = automatic_routing_spec(spec, instructions="Choose eligible responders only.")

    assert routed.instructions == "Choose eligible responders only."
    assert routed.capabilities == [guardrails, thinking]
    assert not routed.variables
    assert not any(
        (
            routed.mcp_servers,
            routed.subagents,
            routed.external_agents,
            routed.skill_ids,
            routed.skill_bindings,
            routed.context_ids,
            routed.collection_ids,
            routed.table_bindings,
            routed.reviewers,
        )
    )
    assert not routed.code_mode.enabled
    assert routed.budget == spec.budget
    assert routed.observability == spec.observability
    assert routed.output_schema == schema
    assert routed.max_steps == 1
    assert routed.model_settings.max_tokens == 1024
    assert routed.model_settings.timeout == 8
    assert spec.instructions.startswith("Read {{private_note}}")
    assert spec.context_ids and spec.code_mode.enabled and spec.max_steps == 50


@pytest.mark.parametrize("max_tokens,timeout", [(None, None), (300, 2), (5000, 120)])
def test_classifier_never_widens_a_stricter_published_model_limit(
    max_tokens: int | None, timeout: float | None
) -> None:
    spec = AgentSpec(
        name="Router",
        model_settings={"max_tokens": max_tokens, "timeout": timeout},
    )

    routed = automatic_routing_spec(spec, instructions="Classify.")

    assert routed.model_settings.max_tokens == min(max_tokens or 1024, 1024)
    assert routed.model_settings.timeout == min(timeout or 8, 8)


def test_classifier_does_not_enable_a_disabled_guardrail() -> None:
    spec = AgentSpec(
        name="Router", capabilities=[CapabilityBindingSpec(id="guardrails", enabled=False)]
    )

    assert automatic_routing_spec(spec, instructions="Classify.").capabilities == []
