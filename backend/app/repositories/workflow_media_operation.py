"""Data access for durable workflow media operations."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workflow_media_operation import WorkflowMediaOperation, WorkflowMediaPhase
from app.db.models.workflow_run import NodeAttempt, NodeAttemptStatus


async def get_for_node_run(
    db: AsyncSession, node_run_id: UUID, *, organization_id: UUID
) -> WorkflowMediaOperation | None:
    """Find the operation for a node run inside its organization."""
    result = await db.execute(
        select(WorkflowMediaOperation).where(
            WorkflowMediaOperation.node_run_id == node_run_id,
            WorkflowMediaOperation.organization_id == organization_id,
        )
    )
    return result.scalar_one_or_none()


async def get_for_node_run_for_update(
    db: AsyncSession, node_run_id: UUID, *, organization_id: UUID
) -> WorkflowMediaOperation | None:
    """Lock an operation while changing its phase or receipt."""
    result = await db.execute(
        select(WorkflowMediaOperation)
        .where(
            WorkflowMediaOperation.node_run_id == node_run_id,
            WorkflowMediaOperation.organization_id == organization_id,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def phases_for_node_runs(
    db: AsyncSession, node_run_ids: list[UUID], *, organization_id: UUID
) -> dict[UUID, WorkflowMediaPhase]:
    """Return only the safe phase for each requested run row."""
    if not node_run_ids:
        return {}
    result = await db.execute(
        select(WorkflowMediaOperation.node_run_id, WorkflowMediaOperation.phase).where(
            WorkflowMediaOperation.node_run_id.in_(node_run_ids),
            WorkflowMediaOperation.organization_id == organization_id,
        )
    )
    return dict(result.all())


async def sum_unsettled_estimates(db: AsyncSession, *, workflow_run_id: UUID) -> Decimal:
    """Hold the estimate for accepted or ambiguous jobs with no live attempt."""
    in_flight = (
        select(NodeAttempt.id)
        .where(
            NodeAttempt.node_run_id == WorkflowMediaOperation.node_run_id,
            NodeAttempt.status == NodeAttemptStatus.IN_FLIGHT.value,
        )
        .exists()
    )
    result = await db.execute(
        select(func.coalesce(func.sum(WorkflowMediaOperation.estimated_cost_usd), 0)).where(
            WorkflowMediaOperation.workflow_run_id == workflow_run_id,
            WorkflowMediaOperation.phase.in_({"submitting", "submitted", "collecting", "unknown"}),
            ~in_flight,
        )
    )
    return Decimal(result.scalar_one() or 0)


async def create(
    db: AsyncSession,
    *,
    organization_id: UUID,
    workflow_run_id: UUID,
    node_run_id: UUID,
    node_attempt_id: UUID,
    request_hash: str,
    provider: str,
    model: str,
    secret_id: UUID | None,
    secret_key_version: int | None,
    estimated_cost_usd: Decimal | None,
) -> WorkflowMediaOperation:
    """Insert a prepared operation before any provider request."""
    operation = WorkflowMediaOperation(
        organization_id=organization_id,
        workflow_run_id=workflow_run_id,
        node_run_id=node_run_id,
        node_attempt_id=node_attempt_id,
        request_hash=request_hash,
        provider=provider,
        model=model,
        secret_id=secret_id,
        secret_key_version=secret_key_version,
        phase="prepared",
        estimated_cost_usd=estimated_cost_usd,
    )
    db.add(operation)
    await db.flush()
    await db.refresh(operation)
    return operation


async def update(
    db: AsyncSession,
    *,
    operation: WorkflowMediaOperation,
    update_data: dict[str, object],
) -> WorkflowMediaOperation:
    """Update phase metadata without storing provider payloads or credentials."""
    for field, value in update_data.items():
        setattr(operation, field, value)
    await db.flush()
    await db.refresh(operation)
    return operation
