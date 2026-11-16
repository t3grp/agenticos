"""Scoped FileRef, preflight and catalog coverage for workflow media nodes."""

import io
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError, SecretStr

from app.agents.capabilities.budget import BudgetScope, SpendLimit, record_ambient_cost
from app.core.config import settings
from app.core.secret_kinds import SecretKind
from app.services.image_models import image_providers
from app.services.workflow_execution import budget, context
from app.workflows import files
from app.workflows.contracts.io import FileRef
from app.workflows.contracts.results import Failed
from app.workflows.nodes.media import _handler as media

pytestmark = pytest.mark.anyio


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color=(12, 20, 30)).save(output, format="PNG")
    return output.getvalue()


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color=(12, 20, 30)).save(output, format="JPEG")
    return output.getvalue()


def _video_catalog(monkeypatch, *, api: str = "xai_videos"):
    model = SimpleNamespace(
        id="synthetic-video",
        resolutions=("720p",),
        aspect_ratios=("16:9",),
        min_seconds=2,
        max_seconds=10,
        duration_choosable=True,
        audio="optional",
        first_frame=True,
    )
    entry = SimpleNamespace(
        provider="synthetic",
        api=api,
        base_url="https://video.invalid",
        model=lambda model_id: model if model_id == model.id else None,
    )
    monkeypatch.setattr(media, "video_provider", lambda provider: entry if provider == "synthetic" else None)
    return entry, model


class _DbContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_exc_info):
        return False


@pytest.mark.security
@pytest.mark.parametrize("refusal", ["forged", "cross_tenant", "cross_run", "expired"])
async def test_file_loader_refuses_refs_the_current_run_cannot_read(monkeypatch, refusal):
    organization_id = uuid4()
    workflow_run_id = uuid4()
    calls = []
    stored_file_id = uuid4()
    stored_organization_id = uuid4() if refusal == "cross_tenant" else organization_id
    stored_workflow_run_id = uuid4() if refusal == "cross_run" else workflow_run_id
    expired = refusal == "expired"
    requested_file_id = uuid4() if refusal == "forged" else stored_file_id
    monkeypatch.setattr(
        context,
        "current",
        lambda: SimpleNamespace(organization_id=organization_id, workflow_run_id=workflow_run_id),
    )
    monkeypatch.setattr(files, "get_worker_db_context", _DbContext)

    async def get_for_run(_db, file_id, *, organization_id, workflow_run_id):
        calls.append((file_id, organization_id, workflow_run_id))
        if (
            file_id == stored_file_id
            and organization_id == stored_organization_id
            and workflow_run_id == stored_workflow_run_id
            and not expired
        ):
            return SimpleNamespace(
                byte_size=10, content_type="image/png", storage_path="synthetic/path"
            )
        return None

    monkeypatch.setattr(files.workflow_file_repo, "get_for_run", get_for_run)
    ref = FileRef(file_id=requested_file_id, content_type="image/png", byte_size=10)
    result = await files.load(ref, max_bytes=100)

    assert isinstance(result, Failed)
    assert result.error.code == "FILE_NOT_FOUND"
    assert calls == [(ref.file_id, organization_id, workflow_run_id)]


async def test_file_loader_checks_row_size_before_reading(monkeypatch):
    organization_id = uuid4()
    workflow_run_id = uuid4()
    ref = FileRef(file_id=uuid4(), content_type="image/png", byte_size=10)
    row = SimpleNamespace(byte_size=101, storage_path="synthetic/path")
    storage_calls = []
    monkeypatch.setattr(
        context,
        "current",
        lambda: SimpleNamespace(organization_id=organization_id, workflow_run_id=workflow_run_id),
    )
    monkeypatch.setattr(files, "get_worker_db_context", _DbContext)
    monkeypatch.setattr(
        files.workflow_file_repo,
        "get_for_run",
        lambda *_args, **_kwargs: _return(row),
    )
    monkeypatch.setattr(
        files, "get_file_storage", lambda: SimpleNamespace(load=_load(storage_calls))
    )

    result = await files.load(ref, max_bytes=100)

    assert isinstance(result, Failed)
    assert result.error.code == "FILE_TOO_LARGE"
    assert storage_calls == []


async def _return(value):
    return value


def _load(calls):
    async def load(path):
        calls.append(path)
        return b""

    return load


async def test_asset_refuses_forged_type_and_size_metadata(monkeypatch):
    data = _png()
    ref = FileRef(file_id=uuid4(), content_type="image/png", byte_size=len(data) + 1)
    monkeypatch.setattr(
        files,
        "load",
        lambda *_args, **_kwargs: _stored(data, "image/png"),
    )
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "asset")

    result = await media.handle_asset(media.AssetConfig(), media.AssetInput(file=ref))

    assert isinstance(result, Failed)
    assert result.error.code == "FILE_NOT_FOUND"


async def test_asset_enforces_actual_bytes_after_load(monkeypatch):
    data = _png()
    ref = FileRef(file_id=uuid4(), content_type="image/png", byte_size=len(data))
    monkeypatch.setattr(files, "load", lambda *_args, **_kwargs: _stored(data, "image/png"))
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "asset")

    result = await media.handle_asset(
        media.AssetConfig(max_bytes=len(data) - 1), media.AssetInput(file=ref)
    )

    assert isinstance(result, Failed)
    assert result.error.code == "FILE_TOO_LARGE"


async def test_asset_rejects_bytes_that_disagree_with_stored_type(monkeypatch):
    data = _jpeg()
    ref = FileRef(file_id=uuid4(), content_type="image/png", byte_size=len(data))
    monkeypatch.setattr(files, "load", lambda *_args, **_kwargs: _stored(data, "image/png"))
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "asset")

    result = await media.handle_asset(media.AssetConfig(), media.AssetInput(file=ref))

    assert isinstance(result, Failed)
    assert result.error.code == "FILE_TYPE_MISMATCH"


async def test_asset_refuses_a_truncated_image(monkeypatch):
    data = _png()[:-10]
    ref = FileRef(file_id=uuid4(), content_type="image/png", byte_size=len(data))
    monkeypatch.setattr(files, "load", lambda *_args, **_kwargs: _stored(data, "image/png"))
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "asset")

    result = await media.handle_asset(media.AssetConfig(), media.AssetInput(file=ref))

    assert isinstance(result, Failed)
    assert result.error.code == "IMAGE_CORRUPT"


async def _stored(data: bytes, content_type: str):
    return files.StoredFile(data=data, content_type=content_type, filename="synthetic")


async def test_asset_sniffs_actual_bytes_and_returns_typed_file_ref(monkeypatch):
    data = _png()
    ref = FileRef(file_id=uuid4(), content_type="image/png", byte_size=len(data))
    monkeypatch.setattr(files, "load", lambda *_args, **_kwargs: _stored(data, "image/png"))
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "asset")

    result = await media.handle_asset(
        media.AssetConfig(accepted_types=("image",)), media.AssetInput(file=ref)
    )

    assert result.output.file == ref
    assert result.output.media_type == "image/png"
    assert result.output.width == 2
    assert result.output.height == 2


async def test_asset_rejects_unsupported_bytes_even_when_the_file_claims_an_image(monkeypatch):
    data = b"not an image"
    ref = FileRef(file_id=uuid4(), content_type="image/png", byte_size=len(data))
    monkeypatch.setattr(files, "load", lambda *_args, **_kwargs: _stored(data, "image/png"))
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "asset")

    result = await media.handle_asset(media.AssetConfig(), media.AssetInput(file=ref))

    assert isinstance(result, Failed)
    assert result.error.code == "UNSUPPORTED_MEDIA_TYPE"


async def test_asset_enforces_pixel_limit_before_returning_metadata(monkeypatch):
    data = _png()
    ref = FileRef(file_id=uuid4(), content_type="image/png", byte_size=len(data))
    monkeypatch.setattr(files, "load", lambda *_args, **_kwargs: _stored(data, "image/png"))
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "asset")

    result = await media.handle_asset(
        media.AssetConfig(accepted_types=("image",), max_pixels=3),
        media.AssetInput(file=ref),
    )

    assert isinstance(result, Failed)
    assert result.error.code == "IMAGE_TOO_LARGE"


@pytest.mark.parametrize(
    ("content_type", "data"),
    [
        ("video/mp4", b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00"),
        ("video/webm", b"\x1aE\xdf\xa3" + b"\x00" * 12),
    ],
)
async def test_asset_recognizes_video_container_signatures(monkeypatch, content_type, data):
    ref = FileRef(file_id=uuid4(), content_type=content_type, byte_size=len(data))
    monkeypatch.setattr(files, "load", lambda *_args, **_kwargs: _stored(data, content_type))
    monkeypatch.setattr(files, "sniff", lambda _data: "application/octet-stream")

    assert await media._asset(ref, max_bytes=100) == (data, content_type)


async def test_asset_returns_a_loader_refusal_without_reading_bytes(monkeypatch):
    ref = FileRef(file_id=uuid4(), content_type="image/png", byte_size=10)
    refusal = files.failed("FILE_NOT_FOUND", "synthetic refusal")
    monkeypatch.setattr(files, "load", lambda *_args, **_kwargs: _return(refusal))

    assert await media._asset(ref, max_bytes=100) is refusal


async def test_asset_refuses_disabled_and_misconfigured_nodes(monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "disabled")

    disabled = await media.handle_asset(None, None)
    assert isinstance(disabled, Failed) and disabled.error.code == "MEDIA_NODE_DISABLED"

    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "asset")
    misconfigured = await media.handle_asset(None, None)
    assert isinstance(misconfigured, Failed) and misconfigured.error.code == "ASSET_NOT_CONFIGURED"


async def test_asset_rejects_a_video_when_only_images_are_allowed(monkeypatch):
    data = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00"
    ref = FileRef(file_id=uuid4(), content_type="video/mp4", byte_size=len(data))
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "asset")
    monkeypatch.setattr(files, "load", lambda *_args, **_kwargs: _stored(data, "video/mp4"))
    monkeypatch.setattr(files, "sniff", lambda _data: "application/octet-stream")

    result = await media.handle_asset(
        media.AssetConfig(accepted_types=("image",)), media.AssetInput(file=ref)
    )

    assert isinstance(result, Failed)
    assert result.error.code == "UNSUPPORTED_MEDIA_TYPE"


async def test_asset_returns_video_metadata_without_image_dimensions(monkeypatch):
    data = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00"
    ref = FileRef(file_id=uuid4(), content_type="video/mp4", byte_size=len(data))
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "asset")
    monkeypatch.setattr(files, "load", lambda *_args, **_kwargs: _stored(data, "video/mp4"))
    monkeypatch.setattr(files, "sniff", lambda _data: "application/octet-stream")

    result = await media.handle_asset(
        media.AssetConfig(accepted_types=("video",)), media.AssetInput(file=ref)
    )

    assert result.output.media_type == "video/mp4"
    assert result.output.width is None
    assert result.output.height is None


async def test_disabled_generation_and_denied_key_make_no_provider_call(monkeypatch):
    provider = image_providers()[0]
    config = media.GenerateImageConfig(
        provider=provider.provider,
        model=provider.models[0].id,
        secret_id=uuid4(),
        max_cost_usd=Decimal("0.50"),
    )
    node_input = media.GenerateImageInput(prompt="synthetic fixture")
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "disabled")
    generation_calls = []
    monkeypatch.setattr(
        media, "generate_image", lambda **_kwargs: generation_calls.append("called")
    )

    disabled = await media.handle_image(config, node_input)
    assert isinstance(disabled, Failed)
    assert disabled.error.code == "MEDIA_NODE_DISABLED"

    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "image")
    monkeypatch.setattr(media, "_api_key", lambda *_args: _return(None))
    denied = await media.handle_image(config, node_input)
    assert isinstance(denied, Failed)
    assert denied.error.code == "SECRET_NOT_USABLE"
    assert generation_calls == []


def test_video_rollout_accepts_only_resumable_providers_with_a_receipt(monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "disabled")
    assert not media.media_node_enabled("asset.input")
    assert not media.media_node_enabled("image.generate")
    assert not media.media_node_enabled("video.generate", "xai")

    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "image")
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_VIDEO_ACCEPTED_PROVIDERS", [])
    assert not media.media_node_enabled("video.generate", "xai")
    assert not media.media_node_enabled("video.generate")
    assert not media.media_node_enabled("unknown.media.node", "xai")
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_VIDEO_ACCEPTED_PROVIDERS", ["removed-provider"])
    assert not media.media_node_enabled("video.generate", "removed-provider")

    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "asset")
    assert media.media_node_enabled("asset.input")
    assert not media.media_node_enabled("image.generate")

    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "image")
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_VIDEO_ACCEPTED_PROVIDERS", ["google"])
    assert not media.media_node_enabled("video.generate", "google")

    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_VIDEO_ACCEPTED_PROVIDERS", ["xai"])
    assert media.media_node_enabled("video.generate", "xai")


@pytest.mark.parametrize(("seconds", "expected"), [(None, 0.5), (7, 0.7)])
def test_video_reservation_estimate_matches_the_requested_default_length(
    monkeypatch, seconds, expected
):
    model = SimpleNamespace(
        resolutions=("720p",),
        min_seconds=1,
        max_seconds=12,
        duration_choosable=True,
    )
    provider = SimpleNamespace(model=lambda _model_id: model)
    monkeypatch.setattr(media, "video_provider", lambda _provider: provider)
    monkeypatch.setattr(media, "price_per_second", lambda *_args, **_kwargs: Decimal("0.10"))
    config = media.GenerateVideoConfig(
        provider="synthetic",
        model="synthetic-video",
        secret_id=uuid4(),
        **({} if seconds is None else {"seconds": seconds}),
    )

    assert config.max_cost_usd == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "missing"},
        {"model": "missing"},
        {"resolution": "1080p"},
        {"aspect_ratio": "1:1"},
        {"seconds": 1},
        {"audio": True},
    ],
)
def test_video_config_rejects_stale_or_unsupported_catalog_choices(monkeypatch, overrides):
    _entry, model = _video_catalog(monkeypatch)
    if "audio" in overrides:
        model.audio = "always"

    with pytest.raises(ValidationError):
        media.GenerateVideoConfig(
            provider="synthetic",
            model="synthetic-video",
            secret_id=uuid4(),
            **overrides,
        )


def test_video_cost_estimate_is_unavailable_without_a_current_price(monkeypatch):
    _video_catalog(monkeypatch)
    monkeypatch.setattr(media, "price_per_second", lambda *_args, **_kwargs: None)
    config = media.GenerateVideoConfig(
        provider="synthetic", model="synthetic-video", secret_id=uuid4()
    )

    assert config.max_cost_usd is None


def test_video_chosen_model_refuses_a_model_removed_after_validation(monkeypatch):
    _video_catalog(monkeypatch)
    config = media.GenerateVideoConfig(
        provider="synthetic", model="synthetic-video", secret_id=uuid4()
    )
    monkeypatch.setattr(media, "video_provider", lambda _provider: None)

    with pytest.raises(ValueError, match="no longer offered"):
        config.chosen_model()


@pytest.mark.parametrize(
    ("kind", "row_purpose", "purpose_kind", "allowed", "usable"),
    [
        (None, "synthetic", SecretKind.API_KEY, True, False),
        ("oauth", "synthetic", SecretKind.API_KEY, True, False),
        (SecretKind.API_KEY.value, "another", SecretKind.API_KEY, True, False),
        (SecretKind.API_KEY.value, "synthetic", None, True, False),
        (SecretKind.API_KEY.value, "synthetic", SecretKind.GITHUB_OAUTH_APP, True, False),
        (SecretKind.API_KEY.value, "synthetic", SecretKind.API_KEY, False, False),
        (SecretKind.API_KEY.value, "synthetic", SecretKind.API_KEY, True, True),
    ],
)
async def test_usable_provider_key_requires_the_right_tenant_secret_and_permission(
    monkeypatch, kind, row_purpose, purpose_kind, allowed, usable
):
    org_id = uuid4()
    secret_id = uuid4()
    auth = SimpleNamespace(organization_id=org_id)
    row = (
        None
        if kind is None
        else SimpleNamespace(kind=kind, purpose=row_purpose, sealed_secret=b"ciphertext", key_version=2)
    )
    purpose = None if purpose_kind is None else SimpleNamespace(kind=purpose_kind)
    monkeypatch.setattr(
        media.organization_secret_repo,
        "get",
        lambda *_args, **_kwargs: _return(row),
    )
    monkeypatch.setattr(media, "get_secret_purpose", lambda _provider: purpose)
    monkeypatch.setattr(media, "resolve_access", lambda *_args, **_kwargs: _return(allowed))

    result = await media._usable_api_key(object(), auth, secret_id, "synthetic")

    assert (result is row) is usable


async def test_api_key_unseals_only_a_currently_usable_secret(monkeypatch):
    org_id = uuid4()
    row = SimpleNamespace(sealed_secret=b"ciphertext", key_version=3)
    monkeypatch.setattr(
        context,
        "current",
        lambda: SimpleNamespace(
            organization_id=org_id,
            auth=SimpleNamespace(organization_id=org_id),
        ),
    )
    monkeypatch.setattr(media, "get_worker_db_context", _DbContext)
    monkeypatch.setattr(media, "_usable_api_key", lambda *_args: _return(row))
    monkeypatch.setattr(
        media,
        "unseal_kind",
        lambda *_args, **_kwargs: SimpleNamespace(api_key=SecretStr("synthetic-key")),
    )
    config = media.GenerateImageConfig(
        provider=image_providers()[0].provider,
        model=image_providers()[0].models[0].id,
        secret_id=uuid4(),
        max_cost_usd=Decimal("0.50"),
    )

    assert await media._api_key(config) == ("synthetic-key", 3)


async def test_image_resource_check_refuses_stale_models_and_unusable_keys(monkeypatch):
    provider = image_providers()[0]
    valid = media.GenerateImageConfig(
        provider=provider.provider,
        model=provider.models[0].id,
        secret_id=uuid4(),
        max_cost_usd=Decimal("0.50"),
    )
    stale = media.GenerateImageConfig.model_construct(
        provider=provider.provider,
        model="removed-model",
        secret_id=uuid4(),
        max_cost_usd=Decimal("0.50"),
    )
    monkeypatch.setattr(media, "_usable_api_key", lambda *_args: _return(None))

    assert await media.check_image_resources(object(), object(), SimpleNamespace()) == []
    assert await media.check_image_resources(object(), object(), stale) == [
        ("model", "Choose an image provider and model from the current catalog")
    ]
    assert await media.check_image_resources(object(), object(), valid) == [
        ("secret_id", "Choose an API key for this image provider that you can use")
    ]
    monkeypatch.setattr(media, "_usable_api_key", lambda *_args: _return(object()))
    assert await media.check_image_resources(object(), object(), valid) == []


async def test_video_resource_check_requires_an_accepted_resumable_provider_and_key(monkeypatch):
    entry, _model = _video_catalog(monkeypatch, api="gemini_files")
    config = media.GenerateVideoConfig.model_construct(
        provider="synthetic", model="synthetic-video", secret_id=uuid4()
    )
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_VIDEO_ACCEPTED_PROVIDERS", ["synthetic"])

    assert await media.check_video_resources(object(), object(), SimpleNamespace()) == []
    assert await media.check_video_resources(object(), object(), config) == [
        ("provider", "This video provider has no verified workflow acceptance receipt")
    ]

    entry.api = "xai_videos"
    assert await media.check_video_resources(object(), object(), config) == [
        ("secret_id", "Choose an API key for this video provider that you can use")
    ]
    monkeypatch.setattr(media, "_usable_api_key", lambda *_args: _return(object()))
    assert await media.check_video_resources(object(), object(), config) == []


def test_unpriced_image_usage_keeps_the_configured_estimate_as_a_cost_floor():
    ledger = SimpleNamespace(total_usd=Decimal("0.02"), has_unpriced_models=True)

    assert media._image_cost(ledger, Decimal("0.50")) == (Decimal("0.50"), True)


def test_priced_image_usage_keeps_the_metered_cost():
    ledger = SimpleNamespace(total_usd=Decimal("0.12"), has_unpriced_models=False)

    assert media._image_cost(ledger, Decimal("0.50")) == (Decimal("0.12"), False)


async def test_image_budget_reservation_denial_happens_before_provider_call(monkeypatch):
    provider = image_providers()[0]
    config = media.GenerateImageConfig(
        provider=provider.provider,
        model=provider.models[0].id,
        secret_id=uuid4(),
        max_cost_usd=Decimal("0.50"),
    )
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "image")
    monkeypatch.setattr(media, "_api_key", lambda *_args: _return(("synthetic-key", 1)))
    monkeypatch.setattr(media, "get_worker_db_context", _DbContext)
    monkeypatch.setattr(
        media.workflow_media_operation_repo,
        "get_for_node_run",
        lambda *_args, **_kwargs: _return(None),
    )
    monkeypatch.setattr(
        context,
        "current",
        lambda: SimpleNamespace(
            organization_id=uuid4(),
            workflow_run_id=uuid4(),
            node_run_id=uuid4(),
            budget_remaining=Decimal("0.49"),
        ),
    )
    generation_calls = []
    monkeypatch.setattr(
        media, "generate_image", lambda **_kwargs: generation_calls.append("called")
    )

    result = await media.handle_image(config, media.GenerateImageInput(prompt="synthetic"))

    assert isinstance(result, Failed)
    assert result.error.code == "BUDGET_EXCEEDED"
    assert generation_calls == []


async def _return(value):
    return value


def test_generation_nodes_are_spenders_and_keep_their_versioned_contracts():
    from app.workflows._registry import get

    image = get("image.generate", 1)
    video = get("video.generate", 1)
    asset = get("asset.input", 1)
    old_transform = get("image.transform", 1)

    assert image.spends_budget is True and image.retry_guarantee == "none"
    assert video.spends_budget is True and video.retry_guarantee == "none"
    assert asset.retry_guarantee == "idempotent"
    assert old_transform.version == 1
    assert (
        media.GenerateImageConfig.model_json_schema()["properties"]["secret_id"]["x-secret-kind"]
        == "api_key"
    )


async def test_parallel_reservations_leave_only_unreserved_workflow_budget(monkeypatch):
    async def held(_db, *, workflow_run_id):
        return Decimal("0.60")

    async def children(_db, *, workflow_run_id):
        return Decimal("0.10")

    monkeypatch.setattr(budget.workflow_run_repo, "sum_in_flight_reservations", held)
    monkeypatch.setattr(budget.workflow_run_repo, "sum_live_child_reservations", children)
    monkeypatch.setattr(
        budget.workflow_media_operation_repo,
        "sum_unsettled_estimates",
        lambda *_args, **_kwargs: _return(Decimal(0)),
    )
    run = SimpleNamespace(
        id=uuid4(), budget_limit=Decimal("1"), spent_cost=Decimal("0.10"), child_cost=Decimal("0")
    )

    assert await budget.unreserved(object(), run) == Decimal("0.20")


def _stored_media_output(content_type: str) -> media.MediaOutput:
    receipt = media.MediaReceipt(
        operation_id=uuid4(),
        provider="synthetic",
        model="synthetic-model",
        submitted_at=None,
        cost_source="configured_estimate",
        outcome="stored",
    )
    return media.MediaOutput(
        file=FileRef(file_id=uuid4(), content_type=content_type, byte_size=100),
        content_type=content_type,
        width=2,
        height=2,
        duration_seconds=5.0,
        receipt=receipt.model_dump(mode="json"),
    )


async def test_image_generation_returns_its_typed_media_output(monkeypatch):
    provider = image_providers()[0]
    config = media.GenerateImageConfig(
        provider=provider.provider,
        model=provider.models[0].id,
        secret_id=uuid4(),
        max_cost_usd=Decimal("0.50"),
    )
    stored_file = FileRef(file_id=uuid4(), content_type="image/png", byte_size=100)
    organization_id = uuid4()
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "image")
    monkeypatch.setattr(media, "_api_key", lambda *_args: _return(("synthetic-key", 1)))
    monkeypatch.setattr(media, "get_worker_db_context", _DbContext)
    monkeypatch.setattr(
        media.workflow_media_operation_repo,
        "get_for_node_run",
        lambda *_args, **_kwargs: _return(None),
    )
    monkeypatch.setattr(media, "assert_organization_within_budget", lambda *_args: _return(None))
    monkeypatch.setattr(
        media.workflow_budget, "provider_limits", lambda *_args, **_kwargs: _return([])
    )
    observed_costs = []
    monkeypatch.setattr(
        context,
        "current",
        lambda: SimpleNamespace(
            organization_id=organization_id,
            workflow_run_id=uuid4(),
            node_run_id=uuid4(),
            budget_remaining=Decimal("1.00"),
        ),
    )

    async def generate_image(**_kwargs):
        record_ambient_cost("synthetic-unpriced-image", None)
        return SimpleNamespace(data=_png(), media_type="image/png")

    async def save_file(_data, *, content_type, filename):
        assert content_type == stored_file.content_type
        return stored_file

    async def execute(**kwargs):
        image = await kwargs["adapter"].submit(lambda _receipt: _return(None))
        observed_costs.append((image.cost_usd, image.cost_is_partial))
        stored = await kwargs["store"](image)
        receipt = media.MediaReceipt(
            operation_id=uuid4(),
            provider=provider.provider,
            model=config.model,
            submitted_at=None,
            cost_source="configured_estimate",
            outcome="stored",
        )
        return stored.model_copy(update={"receipt": receipt.model_dump(mode="json")})

    monkeypatch.setattr(media, "execute_media_operation", execute)
    monkeypatch.setattr(media, "generate_image", generate_image)
    monkeypatch.setattr(media.files, "save", save_file)

    result = await media.handle_image(config, media.GenerateImageInput(prompt="synthetic"))

    assert result.output.file == stored_file
    assert result.output.media_type == "image/png"
    assert result.output.receipt.provider == provider.provider
    assert observed_costs == [(Decimal("0.50"), True)]


@pytest.mark.parametrize(
    ("organization_spend", "allowed"),
    [(Decimal("0.25"), True), (Decimal("0.75"), False)],
)
async def test_video_generation_checks_estimate_and_maps_stored_output(
    monkeypatch, organization_spend, allowed
):
    model = SimpleNamespace(
        id="synthetic-video",
        resolutions=("720p",),
        aspect_ratios=("16:9",),
        min_seconds=1,
        max_seconds=10,
        duration_choosable=True,
        audio="optional",
        first_frame=False,
    )
    entry = SimpleNamespace(
        provider="synthetic",
        api="xai_videos",
        base_url="https://video.invalid",
        model=lambda model_id: model if model_id == model.id else None,
    )
    monkeypatch.setattr(media, "video_provider", lambda _provider: entry)
    monkeypatch.setattr(media, "price_per_second", lambda *_args, **_kwargs: Decimal("0.10"))
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_NODE_STAGE", "image")
    monkeypatch.setattr(settings, "WORKFLOW_MEDIA_VIDEO_ACCEPTED_PROVIDERS", ["synthetic"])
    config = media.GenerateVideoConfig(
        provider="synthetic", model=model.id, secret_id=uuid4(), seconds=5
    )
    output = _stored_media_output("video/mp4")
    organization_id = uuid4()

    async def spent() -> Decimal:
        return organization_spend

    org_limit = SpendLimit(
        scope=BudgetScope.ORGANIZATION,
        limit_usd=Decimal("1.00"),
        period_spend=spent,
    )
    monkeypatch.setattr(media, "_api_key", lambda *_args: _return(("synthetic-key", 1)))
    monkeypatch.setattr(media, "get_worker_db_context", _DbContext)
    monkeypatch.setattr(
        media.workflow_media_operation_repo,
        "get_for_node_run",
        lambda *_args, **_kwargs: _return(None),
    )
    monkeypatch.setattr(
        media.workflow_budget,
        "provider_limits",
        lambda *_args, **_kwargs: _return([org_limit]),
    )
    monkeypatch.setattr(
        context,
        "current",
        lambda: SimpleNamespace(
            organization_id=organization_id,
            workflow_run_id=uuid4(),
            node_run_id=uuid4(),
            budget_remaining=Decimal("1.00"),
        ),
    )

    execute_calls = []

    async def execute(**_kwargs):
        execute_calls.append(True)
        return output

    monkeypatch.setattr(media, "execute_media_operation", execute)

    result = await media.handle_video(config, media.GenerateVideoInput(prompt="synthetic"))

    if not allowed:
        assert isinstance(result, Failed)
        assert result.error.code == "BUDGET_EXCEEDED"
        assert execute_calls == []
        return

    assert execute_calls == [True]
    assert result.output.file == output.file
    assert result.output.media_type == "video/mp4"
    assert result.output.duration_seconds == 5.0
    assert str(result.output.receipt.operation_id) == output.receipt["operation_id"]
