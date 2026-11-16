"""The shared Pydantic AI boundary for existing and workflow image tools."""

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.capabilities import NativeTool
from pydantic_ai.messages import BinaryImage
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.native_tools import ImageGenerationTool
from pydantic_ai.providers import infer_provider_class

from app.agents.capabilities._metered import MeteredModel

_INSTRUCTIONS = "Generate an image based on the user prompt. Do not ask clarifying questions."


def _build_model(model_id: str, api_key: str) -> Model:
    """Build an image caller with the provider key passed only to its client."""
    return infer_model(
        model_id,
        # Provider classes accept api_key although the abstract factory type omits it.
        provider_factory=lambda name: infer_provider_class(name)(
            api_key=api_key
        ),  # ty: ignore[unknown-argument]
    )


async def generate_image(
    *,
    model_id: str,
    api_key: str,
    tool_settings: dict[str, object],
    prompt: str,
    instrumentation: InstrumentationSettings | bool | None = None,
) -> BinaryImage:
    """Run one metered image request and return its bytes to the scoped caller."""
    agent: PydanticAgent[None, BinaryImage] = PydanticAgent(
        MeteredModel(_build_model(model_id, api_key)),
        output_type=BinaryImage,
        capabilities=[NativeTool(ImageGenerationTool(**tool_settings))],
        instructions=_INSTRUCTIONS,
    )
    agent.instrument = instrumentation
    result = await agent.run(prompt)
    return result.output
