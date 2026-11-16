"""Typed, scoped media inputs and gated generation steps."""

import io
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai.messages import BinaryImage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.capabilities.budget import (
    BudgetExceeded,
    BudgetGuard,
    SpendLedger,
    can_afford_ambient_call,
    guarded_by,
    metered_by,
)
from app.agents.capabilities.image_generation import ImageGenerationConfig
from app.core.config import settings
from app.core.permissions import AuthContext, Perm
from app.core.secret_kinds import ApiKeySecret, SecretKind, unseal_kind
from app.core.secret_purposes import get as get_secret_purpose
from app.core.vault import VaultScope
from app.db.models.organization_secret import OrganizationSecret
from app.db.session import get_worker_db_context
from app.repositories import organization_secret as organization_secret_repo
from app.repositories import workflow_media_operation as workflow_media_operation_repo
from app.services.access import SECRET, resolve_access
from app.services.image_generation import generate_image
from app.services.image_models import is_offered, resolved_model_id
from app.services.spend import BudgetExceeded as OrganizationBudgetExceeded
from app.services.spend import assert_organization_within_budget
from app.services.video_generation import (
    WORKFLOW_RESUMABLE_APIS,
    Clip,
    FirstFrame,
    cancel_video_operation,
    generate_video,
)
from app.services.video_models import VideoModel, VideoProvider, price_per_second
from app.services.video_models import by_provider as video_provider
from app.services.workflow_execution import budget as workflow_budget
from app.services.workflow_execution import context
from app.services.workflow_media_operations import (
    MediaOutput,
    ProviderMedia,
    ProviderReceipt,
)
from app.services.workflow_media_operations import (
    execute as execute_media_operation,
)
from app.workflows import files
from app.workflows.contracts.io import FileRef
from app.workflows.contracts.results import Completed, Failed, NodeResult, WorkflowError

IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
VIDEO_TYPES = frozenset({"video/mp4", "video/webm"})
MAX_FRAME_BYTES = 10 * 1024 * 1024


class AssetConfig(BaseModel):
    """Media kinds and bounds accepted by this typed asset input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted_types: tuple[Literal["image", "video"], ...] = Field(
        default=("image", "video"), title="Accepted file types"
    )
    max_bytes: int = Field(default=25_000_000, ge=1, le=300_000_000, title="Maximum file size")
    max_pixels: int = Field(default=40_000_000, ge=1, le=100_000_000, title="Maximum image pixels")


class AssetInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file: FileRef = Field(title="File")


class AssetOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file: FileRef
    media_type: str
    byte_size: int
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None


class GenerateImageConfig(ImageGenerationConfig):
    """Catalog image settings, an authorized API key, and its spend ceiling."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    secret_id: UUID = Field(
        title="Provider API key",
        json_schema_extra={"x-resource": "secret", "x-secret-kind": "api_key"},
    )
    max_cost_usd: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=6,
        title="Maximum estimated cost (USD)",
        description="The amount reserved before submission. Image pricing is not published in the model catalog.",
    )


class GenerateImageInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000, title="Prompt")


class MediaReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: UUID
    provider: str
    model: str
    submitted_at: datetime | None
    cost_source: Literal["provider_reported", "catalog_estimate", "configured_estimate"]
    outcome: Literal["stored"]
    provider_operation_id: str | None = None


class GenerateImageOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file: FileRef
    media_type: str
    width: int | None = None
    height: int | None = None
    receipt: MediaReceipt


class GenerateVideoConfig(BaseModel):
    """Catalog video options and a credential for one accepted provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(title="Provider")
    model: str = Field(title="Video model")
    secret_id: UUID = Field(
        title="Provider API key",
        json_schema_extra={"x-resource": "secret", "x-secret-kind": "api_key"},
    )
    resolution: str | None = None
    aspect_ratio: str | None = None
    seconds: int | None = Field(default=None, ge=1, le=180)
    audio: bool | None = None
    timeout_seconds: int = Field(default=600, ge=60, le=1800, title="Timeout (seconds)")

    @model_validator(mode="after")
    def validate_catalog_options(self) -> GenerateVideoConfig:
        """Reject stale and unsupported provider settings before budget admission."""
        entry = video_provider(self.provider)
        model = entry.model(self.model) if entry is not None else None
        if model is None:
            raise ValueError("Choose a video provider and model from the current catalog")
        if self.resolution is not None and self.resolution not in model.resolutions:
            raise ValueError("The selected model does not support this resolution")
        if self.aspect_ratio is not None and self.aspect_ratio not in model.aspect_ratios:
            raise ValueError("The selected model does not support this aspect ratio")
        if self.seconds is not None and not model.min_seconds <= self.seconds <= model.max_seconds:
            raise ValueError("The selected model does not support this duration")
        if self.audio is not None and model.audio != "optional":
            raise ValueError("The selected model has no audio setting")
        return self

    @property
    def max_cost_usd(self) -> float | None:
        """The dated catalog estimate used as the workflow's reservation cap."""
        model = self.chosen_model()
        resolution = self.resolution or model.resolutions[0]
        seconds = (
            model.max_seconds
            if not model.duration_choosable
            else self.seconds or min(5, model.max_seconds)
        )
        price = price_per_second(model, resolution, on=datetime.now(UTC).date())
        return None if price is None else float(price * seconds)

    def chosen_model(self) -> VideoModel:
        """Return the selected catalog entry after Pydantic validation."""
        entry = video_provider(self.provider)
        if entry is None or (model := entry.model(self.model)) is None:
            raise ValueError("The selected video model is no longer offered")
        return model


class GenerateVideoInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000, title="Prompt")
    first_frame: FileRef | None = Field(default=None, title="First frame")


class GenerateVideoOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file: FileRef
    media_type: Literal["video/mp4"]
    duration_seconds: float | None = None
    receipt: MediaReceipt


def media_node_enabled(node_id: str, provider: str | None = None) -> bool:
    """Apply the staged feature gate at publish and run time."""
    stage = settings.WORKFLOW_MEDIA_NODE_STAGE
    if node_id == "asset.input":
        return stage in {"asset", "image"}
    if node_id == "image.generate":
        return stage == "image"
    if (
        node_id != "video.generate"
        or stage != "image"
        or provider is None
        or provider not in settings.WORKFLOW_MEDIA_VIDEO_ACCEPTED_PROVIDERS
    ):
        return False
    entry = video_provider(provider)
    return entry is not None and entry.api in WORKFLOW_RESUMABLE_APIS


def disabled(node_id: str) -> Failed:
    return Failed(
        error=WorkflowError(
            code="MEDIA_NODE_DISABLED",
            message="This media node is disabled by the deployment rollout setting",
            bypassable=False,
            details={"node_id": node_id},
        )
    )


async def _usable_api_key(
    db: AsyncSession, auth: AuthContext, secret_id: UUID, provider: str
) -> OrganizationSecret | None:
    """Check tenant, purpose, kind and current secret permission."""
    row = await organization_secret_repo.get(db, secret_id, organization_id=auth.organization_id)
    purpose = get_secret_purpose(provider)
    if (
        row is None
        or row.kind != SecretKind.API_KEY.value
        or row.purpose != provider
        or purpose is None
        or purpose.kind is not SecretKind.API_KEY
        or not await resolve_access(db, auth, row, Perm.SECRETS_VIEW, resource_type=SECRET)
    ):
        return None
    return row


async def check_image_resources(
    db: AsyncSession, auth: AuthContext, config: BaseModel
) -> list[tuple[str, str]]:
    if not isinstance(config, GenerateImageConfig):
        return []
    if not is_offered(config.provider, config.model):
        return [("model", "Choose an image provider and model from the current catalog")]
    if await _usable_api_key(db, auth, config.secret_id, config.provider) is None:
        return [("secret_id", "Choose an API key for this image provider that you can use")]
    return []


async def check_video_resources(
    db: AsyncSession, auth: AuthContext, config: BaseModel
) -> list[tuple[str, str]]:
    if not isinstance(config, GenerateVideoConfig):
        return []
    entry = video_provider(config.provider)
    if (
        config.provider not in settings.WORKFLOW_MEDIA_VIDEO_ACCEPTED_PROVIDERS
        or entry is None
        or entry.api not in WORKFLOW_RESUMABLE_APIS
    ):
        return [("provider", "This video provider has no verified workflow acceptance receipt")]
    if await _usable_api_key(db, auth, config.secret_id, config.provider) is None:
        return [("secret_id", "Choose an API key for this video provider that you can use")]
    return []


def _failed(code: str, message: str, *, bypassable: bool = False) -> Failed:
    return Failed(error=WorkflowError(code=code, message=message, bypassable=bypassable))


def _image_cost(ledger: SpendLedger, estimate: Decimal) -> tuple[Decimal, bool]:
    """Use the configured estimate as a floor when model usage is unpriced."""
    if ledger.has_unpriced_models:
        return max(ledger.total_usd, estimate), True
    return ledger.total_usd, False


async def _api_key(config: GenerateImageConfig | GenerateVideoConfig) -> tuple[str, int] | None:
    current = context.current()
    async with get_worker_db_context() as db:
        row = await _usable_api_key(db, current.auth, config.secret_id, config.provider)
        if row is None:
            return None
        key = unseal_kind(
            row.sealed_secret,
            model=ApiKeySecret,
            scope=VaultScope.organization(current.organization_id),
            key_version=row.key_version,
        )
        return key.api_key.get_secret_value(), row.key_version


async def _asset(ref: FileRef, *, max_bytes: int) -> tuple[bytes, str] | Failed:
    stored = await files.load(ref, max_bytes=max_bytes)
    if isinstance(stored, Failed):
        return stored
    if len(stored.data) != ref.byte_size or stored.content_type != ref.content_type:
        return files.not_found()
    if len(stored.data) > max_bytes:
        return files.failed("FILE_TOO_LARGE", "The file exceeds this media node's byte limit")
    detected = files.sniff(stored.data)
    if detected == "application/octet-stream":
        if len(stored.data) >= 12 and stored.data[4:8] == b"ftyp":
            detected = "video/mp4"
        elif stored.data.startswith(b"\x1aE\xdf\xa3"):
            detected = "video/webm"
    if detected not in IMAGE_TYPES | VIDEO_TYPES:
        return files.failed("UNSUPPORTED_MEDIA_TYPE", "This file is not a supported image or video")
    if detected != stored.content_type:
        return files.failed(
            "FILE_TYPE_MISMATCH", "The file bytes do not match their stored media type"
        )
    if detected in IMAGE_TYPES:
        try:
            with Image.open(io.BytesIO(stored.data)) as image:
                width, height = image.size
                image.verify()
            if width * height > settings.CHAT_IMAGE_MAX_PIXELS:
                return files.failed("IMAGE_TOO_LARGE", "The image exceeds the pixel limit")
        except UnidentifiedImageError, OSError, Image.DecompressionBombError:
            return files.failed("IMAGE_CORRUPT", "The image bytes do not match an image")
    else:
        width = height = None
    return stored.data, detected


async def handle_asset(config: BaseModel | None, node_input: BaseModel | None) -> NodeResult:
    if not media_node_enabled("asset.input"):
        return disabled("asset.input")
    if not isinstance(config, AssetConfig) or not isinstance(node_input, AssetInput):
        return _failed("ASSET_NOT_CONFIGURED", "Choose a file and its accepted media types")
    loaded = await _asset(node_input.file, max_bytes=config.max_bytes)
    if isinstance(loaded, Failed):
        return loaded
    data, media_type = loaded
    kind = "image" if media_type in IMAGE_TYPES else "video"
    if kind not in config.accepted_types:
        return _failed("UNSUPPORTED_MEDIA_TYPE", "This media type is not enabled for this asset")
    width = height = None
    if kind == "image":
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
        if width * height > config.max_pixels:
            return files.failed("IMAGE_TOO_LARGE", "The image exceeds this asset's pixel limit")
    return Completed[AssetOutput](
        output=AssetOutput(
            file=node_input.file,
            media_type=media_type,
            byte_size=len(data),
            width=width,
            height=height,
        )
    )


class _ImmediateAdapter:
    """Adapt an existing synchronous generation call without inventing a receipt."""

    def __init__(self, call: Callable[[], Awaitable[ProviderMedia]]) -> None:
        self.call = call

    async def submit(
        self, on_receipt: Callable[[ProviderReceipt], Awaitable[None]]
    ) -> ProviderMedia:
        return await self.call()

    async def resume(self, receipt: ProviderReceipt) -> ProviderMedia:
        raise RuntimeError("This adapter has no resumable provider receipt")

    async def cancel(self, receipt: ProviderReceipt) -> None:
        return None


class _VideoWorkflowAdapter:
    """Persist an async job id before polling the existing video client."""

    def __init__(
        self,
        *,
        entry: VideoProvider,
        model: VideoModel,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        api_key: str,
        timeout_seconds: int,
        duration: int | None,
        cost_per_second: Decimal,
        estimated_duration: int,
        audio: bool | None,
        first_frame: FirstFrame | None,
    ) -> None:
        self.entry = entry
        self.model = model
        self.prompt = prompt
        self.resolution = resolution
        self.aspect_ratio = aspect_ratio
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.duration = duration
        self.cost_per_second = cost_per_second
        self.estimated_duration = estimated_duration
        self.audio = audio
        self.first_frame = first_frame

    async def submit(
        self, on_receipt: Callable[[ProviderReceipt], Awaitable[None]]
    ) -> ProviderMedia:
        async def persist(provider_operation_id: str) -> None:
            await on_receipt(ProviderReceipt(provider_operation_id))

        clip = await self._generate(receipt_callback=persist)
        return self._media(clip)

    async def resume(self, receipt: ProviderReceipt) -> ProviderMedia:
        clip = await self._generate(resume_receipt=receipt.operation_id)
        return self._media(clip)

    async def cancel(self, receipt: ProviderReceipt) -> None:
        await cancel_video_operation(
            self.entry,
            model=self.model.id,
            api_key=self.api_key,
            receipt_id=receipt.operation_id,
        )

    def _media(self, clip: Clip) -> ProviderMedia:
        duration = (
            min(clip.seconds, self.model.max_seconds)
            if clip.seconds is not None and math.isfinite(clip.seconds) and clip.seconds > 0
            else None
        )
        charged_duration = duration if duration is not None else self.estimated_duration
        return ProviderMedia(
            data=clip.data,
            content_type="video/mp4",
            duration_seconds=clip.seconds,
            cost_usd=self.cost_per_second * Decimal(str(charged_duration)),
            cost_is_partial=duration is None,
            cost_source="catalog_estimate",
        )

    async def _generate(
        self,
        *,
        receipt_callback: Callable[[str], Awaitable[None]] | None = None,
        resume_receipt: str | None = None,
    ) -> Clip:
        return await generate_video(
            self.entry,
            model=self.model.id,
            prompt=self.prompt,
            resolution=self.resolution,
            aspect_ratio=self.aspect_ratio,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            duration=self.duration,
            audio=self.audio,
            first_frame=self.first_frame,
            receipt_callback=receipt_callback,
            resume_receipt=resume_receipt,
            cancel_on_interrupt=False,
        )


async def handle_image(config: BaseModel | None, node_input: BaseModel | None) -> NodeResult:
    if not media_node_enabled("image.generate"):
        return disabled("image.generate")
    if not isinstance(config, GenerateImageConfig) or not isinstance(
        node_input, GenerateImageInput
    ):
        return _failed("IMAGE_NOT_CONFIGURED", "Choose an image model, key and cost ceiling")
    key = await _api_key(config)
    if key is None:
        return _failed("SECRET_NOT_USABLE", "The image provider key is gone or no longer usable")
    api_key, key_version = key
    current = context.current()
    async with get_worker_db_context() as db:
        previous = await workflow_media_operation_repo.get_for_node_run(
            db, current.node_run_id, organization_id=current.organization_id
        )
    cached = previous is not None and previous.phase == "stored" and previous.output is not None
    estimate = (
        previous.estimated_cost_usd
        if cached and previous.estimated_cost_usd is not None
        else config.max_cost_usd
    )
    if (
        not cached
        and current.budget_remaining is not None
        and Decimal(current.budget_remaining) < estimate
    ):
        return _failed("BUDGET_EXCEEDED", "The workflow has less budget than this image estimate")
    ledger = SpendLedger()
    limits = []
    try:
        async with get_worker_db_context() as db:
            if cached:
                limits = []
            else:
                await assert_organization_within_budget(db, current.organization_id)
                limits = await workflow_budget.provider_limits(
                    db, organization_id=current.organization_id, remaining=current.budget_remaining
                )
        guard = BudgetGuard(ledger=ledger, provider=config.provider, limits=limits)

        async def generate() -> ProviderMedia:
            image: BinaryImage = await generate_image(
                model_id=resolved_model_id(config.provider, config.model),
                api_key=api_key,
                tool_settings=config.to_tool_kwargs(),
                prompt=node_input.prompt,
            )
            cost, partial = _image_cost(ledger, estimate)
            return ProviderMedia(
                data=image.data,
                content_type=image.media_type,
                cost_usd=cost,
                cost_is_partial=partial,
            )

        async def store(media: ProviderMedia) -> MediaOutput:
            if (
                media.content_type not in IMAGE_TYPES
                or files.sniff(media.data) != media.content_type
            ):
                raise ValueError("Provider image bytes do not match their media type")
            with Image.open(io.BytesIO(media.data)) as image:
                width, height = image.size
                image.verify()
            if width * height > settings.CHAT_IMAGE_MAX_PIXELS:
                raise ValueError("Provider image exceeds the pixel limit")
            file_ref = await files.save(
                media.data,
                content_type=media.content_type,
                filename=f"image.{media.content_type.split('/')[-1]}",
            )
            return MediaOutput(
                file=file_ref,
                content_type=media.content_type,
                width=width,
                height=height,
                receipt={},
            )

        with metered_by(ledger), guarded_by(guard):
            if not await can_afford_ambient_call(estimate):
                return _failed(
                    "BUDGET_EXCEEDED", "The image generation estimate exceeds the available budget"
                )
            output = await execute_media_operation(
                provider=config.provider,
                model=config.model,
                secret_id=config.secret_id,
                secret_key_version=key_version,
                request={"prompt": node_input.prompt, "config": config.model_dump(mode="json")},
                estimated_cost_usd=estimate,
                estimate_source="configured_estimate",
                adapter=_ImmediateAdapter(generate),
                store=store,
            )
    except BudgetExceeded, OrganizationBudgetExceeded:
        return _failed(
            "BUDGET_EXCEEDED", "The image generation estimate exceeds the available budget"
        )
    if isinstance(output, Failed):
        return output
    return Completed[GenerateImageOutput](
        output=GenerateImageOutput(
            file=output.file,
            media_type=output.content_type,
            width=output.width,
            height=output.height,
            receipt=MediaReceipt.model_validate(output.receipt),
        )
    )


async def handle_video(config: BaseModel | None, node_input: BaseModel | None) -> NodeResult:
    if not isinstance(config, GenerateVideoConfig) or not isinstance(
        node_input, GenerateVideoInput
    ):
        return _failed("VIDEO_NOT_CONFIGURED", "Choose a video model and provider key")
    if not media_node_enabled("video.generate", config.provider):
        return disabled("video.generate")
    key = await _api_key(config)
    if key is None:
        return _failed("SECRET_NOT_USABLE", "The video provider key is gone or no longer usable")
    api_key, key_version = key
    current = context.current()
    async with get_worker_db_context() as db:
        previous = await workflow_media_operation_repo.get_for_node_run(
            db, current.node_run_id, organization_id=current.organization_id
        )
    resume_existing = previous is not None and previous.phase in {
        "submitted",
        "collecting",
        "stored",
    }
    entry = video_provider(config.provider)
    model = config.chosen_model()
    if entry is None:
        return _failed("VIDEO_MODEL_UNAVAILABLE", "The video model is no longer in the catalog")
    resolution = config.resolution or model.resolutions[0]
    aspect = config.aspect_ratio or model.aspect_ratios[0]
    seconds = (
        model.max_seconds
        if not model.duration_choosable
        else config.seconds or min(5, model.max_seconds)
    )
    price = price_per_second(model, resolution, on=datetime.now(UTC).date())
    if resume_existing and previous is not None and previous.estimated_cost_usd is not None:
        estimate = previous.estimated_cost_usd
        price = estimate / Decimal(seconds)
    elif price is None:
        return _failed("VIDEO_PRICE_UNAVAILABLE", "This video model has no current catalog price")
    else:
        estimate = price * seconds
    if (
        not resume_existing
        and current.budget_remaining is not None
        and Decimal(current.budget_remaining) < estimate
    ):
        return _failed("BUDGET_EXCEEDED", "The workflow has less budget than this video estimate")
    frame: FirstFrame | None = None
    if not resume_existing and node_input.first_frame is not None:
        if not model.first_frame:
            return _failed("VIDEO_FRAME_UNSUPPORTED", "This video model cannot start from an image")
        loaded = await _asset(node_input.first_frame, max_bytes=MAX_FRAME_BYTES)
        if isinstance(loaded, Failed):
            return loaded
        data, media_type = loaded
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            return _failed(
                "VIDEO_FRAME_UNSUPPORTED", "This video model does not accept this image type"
            )
        frame = FirstFrame(data=data, mime_type=media_type)

    limits = []
    try:
        async with get_worker_db_context() as db:
            if not resume_existing:
                limits = await workflow_budget.provider_limits(
                    db, organization_id=current.organization_id, remaining=current.budget_remaining
                )
    except OrganizationBudgetExceeded:
        return _failed("BUDGET_EXCEEDED", "The organization's monthly budget is exhausted")
    if not resume_existing:
        guard = BudgetGuard(ledger=SpendLedger(), provider=config.provider, limits=limits)
        if not await guard.can_afford_next_request(estimate):
            return _failed(
                "BUDGET_EXCEEDED", "The video generation estimate exceeds the available budget"
            )

    async def store(media: ProviderMedia) -> MediaOutput:
        if not (len(media.data) >= 12 and media.data[4:8] == b"ftyp"):
            raise ValueError("Provider video bytes are not an MP4")
        file_ref = await files.save(media.data, content_type="video/mp4", filename="video.mp4")
        return MediaOutput(
            file=file_ref,
            content_type="video/mp4",
            duration_seconds=media.duration_seconds,
            receipt={},
        )

    output = await execute_media_operation(
        provider=config.provider,
        model=config.model,
        secret_id=config.secret_id,
        secret_key_version=key_version,
        request={"prompt": node_input.prompt, "config": config.model_dump(mode="json")},
        estimated_cost_usd=estimate,
        adapter=_VideoWorkflowAdapter(
            entry=entry,
            model=model,
            prompt=node_input.prompt,
            resolution=resolution,
            aspect_ratio=aspect,
            api_key=api_key,
            timeout_seconds=config.timeout_seconds,
            duration=seconds if model.duration_choosable else None,
            cost_per_second=price,
            estimated_duration=seconds,
            audio=config.audio,
            first_frame=frame,
        ),
        store=store,
    )
    if isinstance(output, Failed):
        return output
    return Completed[GenerateVideoOutput](
        output=GenerateVideoOutput(
            file=output.file,
            media_type=output.content_type,
            duration_seconds=output.duration_seconds,
            receipt=MediaReceipt.model_validate(output.receipt),
        )
    )
