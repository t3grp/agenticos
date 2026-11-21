"""A bounded, tool-free classifier spec for a room's optional router."""

from app.agents.spec import AgentSpec

ROUTER_MAX_OUTPUT_TOKENS = 1024
ROUTER_MODEL_TIMEOUT_SECONDS = 8.0


def automatic_routing_spec(spec: AgentSpec, *, instructions: str) -> AgentSpec:
    """Retain published governance while giving the classifier only its supplied input.

    A router selects a responder; it must not answer the room itself or reach
    the chosen agent's tools, personal connections, knowledge or standing context.
    The caller retains the pinned version on the metered run. This constrained
    runtime spec does not edit that published version.
    """
    return spec.model_copy(
        update={
            "instructions": instructions,
            "variables": [],
            "capabilities": [
                binding
                for binding in spec.capabilities
                if binding.enabled and binding.id in {"guardrails", "thinking"}
            ],
            "mcp_servers": [],
            "subagents": [],
            "external_agents": [],
            "skill_ids": [],
            "skill_bindings": [],
            "context_ids": [],
            "collection_ids": [],
            "table_bindings": [],
            "reviewers": [],
            "code_mode": spec.code_mode.model_copy(update={"enabled": False}),
            "max_steps": 1,
            "model_settings": spec.model_settings.model_copy(
                update={
                    "max_tokens": min(
                        spec.model_settings.max_tokens or ROUTER_MAX_OUTPUT_TOKENS,
                        ROUTER_MAX_OUTPUT_TOKENS,
                    ),
                    "timeout": min(
                        spec.model_settings.timeout or ROUTER_MODEL_TIMEOUT_SECONDS,
                        ROUTER_MODEL_TIMEOUT_SECONDS,
                    ),
                }
            ),
        }
    )
