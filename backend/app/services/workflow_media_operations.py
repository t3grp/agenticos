"""Durable phase handling for billable workflow media calls.

This layer owns operation state, not provider authorization, budgets or files.
Callers reserve funds before entering it and provide scoped storage callbacks.
"""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, JsonValue

from app.db.session import get_worker_db_context
from app.repositories import workflow_media_operation as operation_repo
from app.repositories import workflow_run as workflow_run_repo
from app.services.workflow_execution import context, events
from app.workflows.contracts.io import FileRef
from app.workflows.contracts.results import Failed, WorkflowError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderReceipt:
    """One provider's durable operation id; never a response body or URL."""

    operation_id: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z0-9._-]{1,255}", self.operation_id) is None:
            raise ValueError("Provider receipt must be an opaque operation id")


@dataclass(frozen=True)
class ProviderMedia:
    """Bytes and verified media metadata returned by an adapter."""

    data: bytes
    content_type: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    cost_usd: Decimal | None = None
    cost_is_partial: bool = False
    cost_source: Literal["provider_reported", "catalog_estimate", "configured_estimate"] | None = (
        None
    )


class MediaAdapter(Protocol):
    """Provider adapter split into submission, receipt polling and cancellation."""

    async def submit(
        self, on_receipt: Callable[[ProviderReceipt], Awaitable[None]]
    ) -> ProviderReceipt | ProviderMedia: ...

    async def resume(self, receipt: ProviderReceipt) -> ProviderMedia: ...

    async def cancel(self, receipt: ProviderReceipt) -> None: ...


class MediaOutput(BaseModel):
    """A typed output cached after its workflow file is stored."""

    file: FileRef
    content_type: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    receipt: dict[str, JsonValue]


def request_hash(request: object) -> str:
    """Hash canonical request data without retaining prompts or source bytes."""
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _phase(
    phase: str,
    *,
    operation_id: UUID,
    expected_phase: str | None = None,
    receipt: ProviderReceipt | None = None,
    output: dict[str, object] | None = None,
    failure_code: str | None = None,
    reported_cost_usd: Decimal | None = None,
    cost_is_partial: bool = False,
) -> bool:
    """Commit one phase before the next external effect."""
    current = context.current()
    async with get_worker_db_context() as db:
        operation = await operation_repo.get_for_node_run_for_update(
            db, current.node_run_id, organization_id=current.organization_id
        )
        if operation is None or operation.id != operation_id:
            raise RuntimeError("Workflow media operation disappeared")
        if expected_phase is not None and operation.phase != expected_phase:
            return False
        allowed = {
            "prepared": {"submitting", "failed"},
            "submitting": {"submitted", "collecting", "unknown", "failed", "cancelled"},
            "submitted": {"collecting", "unknown", "failed", "cancelled"},
            "collecting": {"stored", "unknown", "failed", "cancelled"},
            "stored": set(),
            "unknown": set(),
            "failed": set(),
            "cancelled": set(),
        }
        if phase != operation.phase and phase not in allowed[operation.phase]:
            raise RuntimeError("Invalid workflow media operation phase change")
        update_data: dict[str, object] = {"phase": phase, "failure_code": failure_code}
        if phase == "submitting":
            update_data["submitted_at"] = datetime.now(UTC)
        if receipt is not None:
            update_data["provider_receipt"] = {"operation_id": receipt.operation_id}
            update_data["submitted_at"] = datetime.now(UTC)
        if output is not None:
            update_data["output"] = output
        if reported_cost_usd is not None:
            update_data["reported_cost_usd"] = reported_cost_usd
            update_data["cost_is_partial"] = cost_is_partial
        await operation_repo.update(db, operation=operation, update_data=update_data)
        run = await workflow_run_repo.get_run_by_id_for_update(db, current.workflow_run_id)
        if run is not None:
            await events.append(
                db,
                run=run,
                kind="media_operation_progress",
                node_run_id=current.node_run_id,
                payload={"phase": phase, "provider": operation.provider, "model": operation.model},
            )
    return True


async def execute(
    *,
    provider: str,
    model: str,
    secret_id: UUID,
    secret_key_version: int,
    request: object,
    estimated_cost_usd: Decimal | None,
    estimate_source: Literal["catalog_estimate", "configured_estimate"] = "catalog_estimate",
    adapter: MediaAdapter,
    store: Callable[[ProviderMedia], Awaitable[MediaOutput]],
) -> MediaOutput | Failed:
    """Submit once, resume only a known receipt, and retain ambiguous spend."""
    current = context.current()
    digest = request_hash(request)
    async with get_worker_db_context() as db:
        operation = await operation_repo.get_for_node_run_for_update(
            db, current.node_run_id, organization_id=current.organization_id
        )
        if operation is None:
            attempt = await workflow_run_repo.get_latest_attempt(
                db, node_run_id=current.node_run_id
            )
            if attempt is None:
                raise RuntimeError("Workflow media operation has no active node attempt")
            operation = await operation_repo.create(
                db,
                organization_id=current.organization_id,
                workflow_run_id=current.workflow_run_id,
                node_run_id=current.node_run_id,
                node_attempt_id=attempt.id,
                request_hash=digest,
                provider=provider,
                model=model,
                secret_id=secret_id,
                secret_key_version=secret_key_version,
                estimated_cost_usd=estimated_cost_usd,
            )
        elif (
            operation.request_hash != digest
            or operation.provider != provider
            or operation.model != model
            or operation.secret_id != secret_id
            or operation.secret_key_version != secret_key_version
        ):
            return _unknown("A previous generation has a different request; start a new run")
        elif operation.phase == "stored" and operation.output is not None:
            if operation.reported_cost_usd is not None:
                context.report_cost(operation.reported_cost_usd, partial=operation.cost_is_partial)
            return MediaOutput.model_validate(operation.output)
        elif operation.phase in {"unknown", "failed", "cancelled"}:
            return _unknown("This generation cannot be submitted again from its current run")
        elif operation.phase == "submitting":
            return _unknown("The provider may have accepted this generation; it was not sent again")
        elif operation.phase not in {"prepared", "submitted", "collecting"}:
            return _unknown("This generation is not safe to resume")
        operation_id = operation.id
        existing_phase = operation.phase
        receipt_data = operation.provider_receipt
        submitted_at = operation.submitted_at

    receipt: ProviderReceipt | None = None
    if existing_phase in {"submitted", "collecting"}:
        if not isinstance(receipt_data, dict) or not isinstance(
            receipt_data.get("operation_id"), str
        ):
            await _phase("unknown", operation_id=operation_id, failure_code="RECEIPT_MISSING")
            return _unknown("The provider receipt is missing; this generation was not sent again")
        receipt = ProviderReceipt(operation_id=receipt_data["operation_id"])

    submission_started = receipt is None
    if receipt is None and not await _phase(
        "submitting", operation_id=operation_id, expected_phase="prepared"
    ):
        return _unknown("Another attempt already owns this generation; it was not sent again")

    async def persist_receipt(value: ProviderReceipt) -> None:
        nonlocal receipt, submitted_at
        if receipt is not None and receipt != value:
            raise RuntimeError("Provider returned a different receipt for this operation")
        receipt = value
        await _phase("submitted", operation_id=operation_id, receipt=value)
        submitted_at = datetime.now(UTC)

    try:
        if receipt is None:
            submitted_at = datetime.now(UTC)
            submission = await adapter.submit(persist_receipt)
            if isinstance(submission, ProviderReceipt):
                await persist_receipt(submission)
                media = await adapter.resume(submission)
            else:
                media = submission
        else:
            media = await adapter.resume(receipt)
        await _phase("collecting", operation_id=operation_id)
        output = await store(media)
    except asyncio.CancelledError:
        if receipt is not None:
            try:
                await adapter.cancel(receipt)
            except Exception:
                logger.warning(
                    "workflow_media_cancel_failed", extra={"operation_id": str(operation_id)}
                )
        await _phase("unknown", operation_id=operation_id, failure_code="CANCELLED_AFTER_SUBMIT")
        raise
    except Exception:
        # Provider and storage exception text can contain credentials, response
        # bodies or signed URLs. Persist only a stable code and log no exception.
        logger.warning("workflow_media_operation_failed", extra={"operation_id": str(operation_id)})
        phase = (
            "unknown"
            if receipt is not None or submission_started or existing_phase != "prepared"
            else "failed"
        )
        await _phase(phase, operation_id=operation_id, failure_code="PROVIDER_OR_STORAGE_FAILURE")
        return _unknown(
            "The provider outcome could not be confirmed; a new charge was not attempted"
        )

    receipt_view: dict[str, object] = {
        "operation_id": str(operation_id),
        "provider": provider,
        "model": model,
        "submitted_at": (submitted_at or datetime.now(UTC)).isoformat(),
        "cost_source": media.cost_source
        or (
            "provider_reported"
            if media.cost_usd is not None and not media.cost_is_partial
            else estimate_source
        ),
        "outcome": "stored",
    }
    if receipt is not None:
        receipt_view["provider_operation_id"] = receipt.operation_id
    output = output.model_copy(update={"receipt": receipt_view})
    reported_cost = (
        media.cost_usd if media.cost_usd is not None else estimated_cost_usd or Decimal(0)
    )
    reported_cost_is_partial = media.cost_is_partial or media.cost_usd is None
    await _phase(
        "stored",
        operation_id=operation_id,
        output=output.model_dump(mode="json"),
        reported_cost_usd=reported_cost,
        cost_is_partial=reported_cost_is_partial,
    )
    context.report_cost(reported_cost, partial=reported_cost_is_partial)
    return output


def _unknown(message: str) -> Failed:
    return Failed(
        error=WorkflowError(
            code="MEDIA_OUTCOME_UNKNOWN", message=message, retryable=False, bypassable=False
        )
    )
