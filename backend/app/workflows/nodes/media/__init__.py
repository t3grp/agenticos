"""Asset and media-generation workflow nodes."""

from app.workflows._registry import register
from app.workflows.contracts.definition import NodeDefinition, Port
from app.workflows.nodes.media._handler import (
    AssetConfig,
    AssetInput,
    AssetOutput,
    GenerateImageConfig,
    GenerateImageInput,
    GenerateImageOutput,
    GenerateVideoConfig,
    GenerateVideoInput,
    GenerateVideoOutput,
    check_image_resources,
    check_video_resources,
    handle_asset,
    handle_image,
    handle_video,
)

__all__ = [
    "AssetConfig",
    "AssetInput",
    "AssetOutput",
    "GenerateImageConfig",
    "GenerateImageInput",
    "GenerateImageOutput",
    "GenerateVideoConfig",
    "GenerateVideoInput",
    "GenerateVideoOutput",
]

register(
    NodeDefinition(
        id="asset.input",
        version=1,
        name="Asset",
        category="files",
        description="Validate a run-scoped image or video file and pass its typed FileRef forward.",
        kind="action",
        config_schema=AssetConfig,
        input_schema=AssetInput,
        output_schema=AssetOutput,
        ports=(
            Port(id="in", label="In", kind="input", schema=AssetInput),
            Port(id="out", label="Out", kind="output", schema=AssetOutput),
        ),
        effect_kind="read",
        retry_guarantee="idempotent",
        handler=handle_asset,
    )
)

register(
    NodeDefinition(
        id="image.generate",
        version=1,
        name="Generate image",
        category="files",
        description="Generate one image using a catalog model and an authorized provider key.",
        kind="action",
        config_schema=GenerateImageConfig,
        input_schema=GenerateImageInput,
        output_schema=GenerateImageOutput,
        ports=(
            Port(id="in", label="In", kind="input", schema=GenerateImageInput),
            Port(id="out", label="Out", kind="output", schema=GenerateImageOutput),
        ),
        effect_kind="write",
        retry_guarantee="none",
        handler=handle_image,
        spends_budget=True,
        resource_check=check_image_resources,
    )
)

register(
    NodeDefinition(
        id="video.generate",
        version=1,
        name="Generate video",
        category="files",
        description="Generate one video using a dated catalog estimate and an accepted provider adapter.",
        kind="action",
        config_schema=GenerateVideoConfig,
        input_schema=GenerateVideoInput,
        output_schema=GenerateVideoOutput,
        ports=(
            Port(id="in", label="In", kind="input", schema=GenerateVideoInput),
            Port(id="out", label="Out", kind="output", schema=GenerateVideoOutput),
        ),
        effect_kind="write",
        retry_guarantee="none",
        handler=handle_video,
        spends_budget=True,
        resource_check=check_video_resources,
    )
)
