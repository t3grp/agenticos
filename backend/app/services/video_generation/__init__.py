"""Video generation: one clip through the provider a binding chose.

The facade is :func:`generate_video`; the provider clients behind it are package
internals. Which providers and models exist, and what each can make, is
:mod:`app.services.video_models` (#2260).
"""

from app.services.video_generation.clients import MAX_BYTES as MAX_CLIP_BYTES
from app.services.video_generation.clients import WORKFLOW_RESUMABLE_APIS, Clip, FirstFrame
from app.services.video_generation.exceptions import VideoFailure, VideoProviderError
from app.services.video_generation.generation import cancel_video_operation, generate_video

__all__ = [
    "MAX_CLIP_BYTES",
    "WORKFLOW_RESUMABLE_APIS",
    "Clip",
    "FirstFrame",
    "VideoFailure",
    "VideoProviderError",
    "cancel_video_operation",
    "generate_video",
]
