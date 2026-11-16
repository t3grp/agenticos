"""Database guarantees for media submission claims and attempt spend."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models.workflow_media_operation import WorkflowMediaOperation
from app.db.models.workflow_run import NodeAttempt, NodeAttemptStatus, NodeRun, WorkflowEvent
from app.repositories import workflow_run as workflow_run_repo
from app.services import workflow_media_operations
from app.workflows.graph.model import NodeInstance, NodePosition, WorkflowGraph
from tests.integration.workflow_run_support import SeededRun, seed_run

pytestmark = pytest.mark.anyio


async def _seed_operation(
    engine: AsyncEngine,
) -> tuple[SeededRun, NodeRun, WorkflowMediaOperation]:
    node_id = uuid.uuid4()
    graph = WorkflowGraph(
        entry_node_id=node_id,
        nodes=(
            NodeInstance(
                id=node_id,
                definition_id="image.generate",
                definition_version=1,
                config={},
                layout=NodePosition(x=0, y=0),
            ),
        ),
    )
    seeded = await seed_run(engine, graph)
    async with seeded.factory() as db:
        node_run = (
            await db.execute(select(NodeRun).where(NodeRun.workflow_run_id == seeded.run.id))
        ).scalar_one()
        first_attempt = NodeAttempt(
            id=uuid.uuid4(),
            organization_id=seeded.org.id,
            node_run_id=node_run.id,
            attempt_no=1,
            idempotency_key="media-attempt-one",
            retry_guarantee="none",
            status=NodeAttemptStatus.IN_FLIGHT.value,
            cost=Decimal("0.25"),
            cost_is_partial=False,
            started_at=datetime.now(UTC),
        )
        second_attempt = NodeAttempt(
            id=uuid.uuid4(),
            organization_id=seeded.org.id,
            node_run_id=node_run.id,
            attempt_no=2,
            idempotency_key="media-attempt-two",
            retry_guarantee="none",
            status=NodeAttemptStatus.FAILED.value,
            cost=Decimal("0.15"),
            cost_is_partial=False,
            started_at=datetime.now(UTC),
        )
        operation = WorkflowMediaOperation(
            id=uuid.uuid4(),
            organization_id=seeded.org.id,
            workflow_run_id=seeded.run.id,
            node_run_id=node_run.id,
            node_attempt_id=first_attempt.id,
            request_hash="0" * 64,
            provider="synthetic",
            model="synthetic-image",
            phase="prepared",
            estimated_cost_usd=Decimal("0.50"),
            cost_is_partial=False,
        )
        db.add_all([first_attempt, second_attempt, operation])
        await db.commit()
    return seeded, node_run, operation


async def test_locked_prepared_phase_allows_only_one_submission_claim(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
):
    seeded, node_run, operation = await _seed_operation(engine)
    current = SimpleNamespace(
        organization_id=seeded.org.id,
        workflow_run_id=seeded.run.id,
        node_run_id=node_run.id,
    )

    @asynccontextmanager
    async def worker_db_context():
        async with seeded.factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(workflow_media_operations, "get_worker_db_context", worker_db_context)
    monkeypatch.setattr(workflow_media_operations.context, "current", lambda: current)
    ready = asyncio.Event()

    async def claim():
        await ready.wait()
        return await workflow_media_operations._phase(
            "submitting", operation_id=operation.id, expected_phase="prepared"
        )

    first = asyncio.create_task(claim())
    second = asyncio.create_task(claim())
    ready.set()
    claimed = await asyncio.gather(first, second)

    assert sorted(claimed) == [False, True]
    async with seeded.factory() as db:
        saved = await db.get(WorkflowMediaOperation, operation.id)
        event_count = await db.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == seeded.run.id)
        )

    assert saved is not None and saved.phase == "submitting"
    assert event_count == 1


async def test_attempt_cost_sum_covers_all_recovery_attempts(engine: AsyncEngine):
    seeded, node_run, _operation = await _seed_operation(engine)

    async with seeded.factory() as db:
        total = await workflow_run_repo.sum_attempt_costs(db, node_run_id=node_run.id)

    assert total == Decimal("0.40")
