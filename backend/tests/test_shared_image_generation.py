"""The existing image tool and workflow node share this provider boundary."""

from types import SimpleNamespace

import pytest
from pydantic_ai.models.test import TestModel

from app.services import image_generation

pytestmark = pytest.mark.anyio


def test_model_builder_passes_the_key_to_the_inferred_provider(monkeypatch):
    class FakeProvider:
        def __init__(self, *, api_key: str) -> None:
            self.api_key = api_key

    monkeypatch.setattr(image_generation, "infer_provider_class", lambda _name: FakeProvider)
    monkeypatch.setattr(
        image_generation,
        "infer_model",
        lambda _model_id, *, provider_factory: provider_factory("synthetic"),
    )

    model = image_generation._build_model("synthetic:model", "synthetic-key")

    assert isinstance(model, FakeProvider)
    assert model.api_key == "synthetic-key"


async def test_generator_returns_the_image_from_the_configured_native_tool(monkeypatch):
    generated = SimpleNamespace(data=b"synthetic image", media_type="image/png")
    created = {}

    class FakeAgent:
        def __init__(self, model, *, output_type, capabilities, instructions) -> None:
            created.update(
                model=model,
                output_type=output_type,
                capabilities=capabilities,
                instructions=instructions,
            )
            self.instrument = None

        async def run(self, prompt: str):
            created["prompt"] = prompt
            return SimpleNamespace(output=generated)

    monkeypatch.setattr(image_generation, "_build_model", lambda *_args: TestModel())
    monkeypatch.setattr(image_generation, "ImageGenerationTool", lambda **kwargs: kwargs)
    monkeypatch.setattr(image_generation, "NativeTool", lambda tool: tool)
    monkeypatch.setattr(image_generation, "PydanticAgent", FakeAgent)

    image = await image_generation.generate_image(
        model_id="synthetic:model",
        api_key="synthetic-key",
        tool_settings={"quality": "standard"},
        prompt="synthetic prompt",
        instrumentation=True,
    )

    assert image is generated
    assert created["model"].wrapped.__class__ is TestModel
    assert created["prompt"] == "synthetic prompt"
    assert created["instructions"] == image_generation._INSTRUCTIONS
    assert created["capabilities"] == [{"quality": "standard"}]
