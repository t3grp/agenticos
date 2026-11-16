"""Database guarantees for media submission claims and attempt spend."""

import asyncio
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.models.organization_secret import OrganizationSecret
from app.db.models.workflow_media_operation import WorkflowMediaOperation
from app.db.models.workflow_run import (
    DispatchOutbox,
    NodeAttempt,
    NodeAttemptStatus,
    NodeRun,
    WorkflowEvent,
    WorkflowRun,
)
from app.repositories import workflow_run as workflow_run_repo
from app.services import workflow_media_operations
from app.services.spend import organization_monthly_spend
from app.services.workflow_execution import context, dispatcher
from app.workflows._registry import REGISTRY, load_builtins
from app.workflows.contracts.io import FileRef
from app.workflows.contracts.results import Completed, Failed
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
        db.add_all([first_attempt, second_attempt])
        await db.flush()
        db.add(operation)
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


class _BillableFakeProvider:
    """Only submission creates a charge; resuming or replaying cannot charge."""

    def __init__(self, *, ambiguous: bool = False) -> None:
        self.submissions = 0
        self.charges: list[Decimal] = []
        self.ambiguous = ambiguous

    async def submit(
        self,
        on_receipt: Callable[[workflow_media_operations.ProviderReceipt], Awaitable[None]],
    ) -> workflow_media_operations.ProviderMedia:
        self.submissions += 1
        self.charges.append(Decimal("0.25"))
        if self.ambiguous:
            raise TimeoutError("Synthetic provider accepted the request but lost its reply")
        return workflow_media_operations.ProviderMedia(
            data=b"synthetic image bytes",
            content_type="image/png",
            cost_usd=Decimal("0.25"),
        )

    async def resume(
        self, receipt: workflow_media_operations.ProviderReceipt
    ) -> workflow_media_operations.ProviderMedia:
        raise AssertionError("The immediate fake provider cannot be polled")

    async def cancel(self, receipt: workflow_media_operations.ProviderReceipt) -> None:
        raise AssertionError("The immediate fake provider has no cancellable receipt")


async def _execution_fixture(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> tuple[SeededRun, NodeRun, WorkflowMediaOperation, uuid.UUID]:
    seeded, node_run, operation = await _seed_operation(engine)
    async with seeded.factory() as db:
        secret = OrganizationSecret(
            id=uuid.uuid4(),
            organization_id=seeded.org.id,
            name="Synthetic media test key",
            kind="api_key",
            purpose="custom",
            sealed_secret="synthetic-not-a-credential",
        )
        db.add(secret)
        await db.flush()
        saved = await db.get(WorkflowMediaOperation, operation.id)
        assert saved is not None
        saved.secret_id = secret.id
        saved.secret_key_version = 1
        saved.request_hash = workflow_media_operations.request_hash({"prompt": "synthetic"})
        attempts = (
            await db.scalars(select(NodeAttempt).where(NodeAttempt.node_run_id == node_run.id))
        ).all()
        for attempt in attempts:
            attempt.cost = Decimal(0)
            attempt.status = NodeAttemptStatus.UNCERTAIN.value
        await db.commit()

    @asynccontextmanager
    async def worker_db_context() -> AsyncGenerator[AsyncSession]:
        async with seeded.factory.begin() as session:
            yield session

    monkeypatch.setattr(workflow_media_operations, "get_worker_db_context", worker_db_context)
    return seeded, node_run, operation, secret.id


def _dispatch_context(
    seeded: SeededRun, node_run: NodeRun, attempt_no: int
) -> context.DispatchContext:
    return context.DispatchContext(
        organization_id=seeded.org.id,
        workflow_run_id=seeded.run.id,
        node_run_id=node_run.id,
        node_instance_id=node_run.node_instance_id,
        attempt_no=attempt_no,
        auth=seeded.ctx,
        resumed_agent_run_id=None,
    )


async def _generate(
    seeded: SeededRun,
    node_run: NodeRun,
    secret_id: uuid.UUID,
    provider: _BillableFakeProvider,
    *,
    attempt_no: int = 1,
) -> tuple[workflow_media_operations.MediaOutput | Failed, Decimal]:
    async def store(
        media: workflow_media_operations.ProviderMedia,
    ) -> workflow_media_operations.MediaOutput:
        return workflow_media_operations.MediaOutput(
            file=FileRef(
                file_id=uuid.uuid4(), content_type=media.content_type, byte_size=len(media.data)
            ),
            content_type=media.content_type,
            receipt={},
        )

    with context.dispatching_as(_dispatch_context(seeded, node_run, attempt_no)) as scope:
        output = await workflow_media_operations.execute(
            provider="synthetic",
            model="synthetic-image",
            secret_id=secret_id,
            secret_key_version=1,
            request={"prompt": "synthetic"},
            estimated_cost_usd=Decimal("0.50"),
            adapter=provider,
            store=store,
        )
    return output, scope.cost


async def test_concurrent_workers_submit_and_charge_one_ready_job(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded, node_run, operation, secret_id = await _execution_fixture(engine, monkeypatch)
    provider = _BillableFakeProvider()
    original_phase = workflow_media_operations._phase
    both_ready = asyncio.Event()
    claim_count = 0

    async def phase_together(
        phase: str,
        *,
        operation_id: uuid.UUID,
        expected_phase: str | None = None,
        receipt: workflow_media_operations.ProviderReceipt | None = None,
        output: dict[str, object] | None = None,
        failure_code: str | None = None,
        reported_cost_usd: Decimal | None = None,
        cost_is_partial: bool = False,
    ) -> bool:
        nonlocal claim_count
        if phase == "submitting":
            claim_count += 1
            if claim_count == 2:
                both_ready.set()
            await both_ready.wait()
        return await original_phase(
            phase,
            operation_id=operation_id,
            expected_phase=expected_phase,
            receipt=receipt,
            output=output,
            failure_code=failure_code,
            reported_cost_usd=reported_cost_usd,
            cost_is_partial=cost_is_partial,
        )

    monkeypatch.setattr(workflow_media_operations, "_phase", phase_together)
    results = await asyncio.wait_for(
        asyncio.gather(
            _generate(seeded, node_run, secret_id, provider),
            _generate(seeded, node_run, secret_id, provider, attempt_no=2),
        ),
        timeout=20,
    )

    assert provider.submissions == 1
    assert provider.charges == [Decimal("0.25")]
    assert (
        sum(isinstance(output, workflow_media_operations.MediaOutput) for output, _cost in results)
        == 1
    )
    assert sum(isinstance(output, Failed) for output, _cost in results) == 1
    async with seeded.factory() as db:
        saved = await db.get(WorkflowMediaOperation, operation.id)
        assert saved is not None and saved.phase == "stored"
        assert saved.reported_cost_usd == Decimal("0.25")


async def test_ambiguous_provider_acceptance_is_never_submitted_or_charged_again(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded, node_run, operation, secret_id = await _execution_fixture(engine, monkeypatch)
    provider = _BillableFakeProvider(ambiguous=True)
    first, _cost = await _generate(seeded, node_run, secret_id, provider)
    second, _cost = await _generate(seeded, node_run, secret_id, provider, attempt_no=2)

    assert isinstance(first, Failed) and isinstance(second, Failed)
    assert first.error.code == second.error.code == "MEDIA_OUTCOME_UNKNOWN"
    assert provider.submissions == 1
    assert provider.charges == [Decimal("0.25")]
    async with seeded.factory() as db:
        saved = await db.get(WorkflowMediaOperation, operation.id)
        assert saved is not None and saved.phase == "unknown"
        assert saved.estimated_cost_usd == Decimal("0.50")


@pytest.mark.parametrize("replay_first", [False, True])
async def test_late_and_replacement_workers_concurrently_book_one_stored_result(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, replay_first: bool
) -> None:
    seeded, node_run, _operation, secret_id = await _execution_fixture(engine, monkeypatch)
    provider = _BillableFakeProvider()
    original, original_cost = await _generate(seeded, node_run, secret_id, provider)
    replay, replay_cost = await _generate(seeded, node_run, secret_id, provider, attempt_no=2)
    assert isinstance(original, workflow_media_operations.MediaOutput)
    assert isinstance(replay, workflow_media_operations.MediaOutput)
    assert original.file == replay.file
    load_builtins()
    definition = REGISTRY["image.generate"][1]
    handler = definition.handler
    assert handler is not None
    replacement_token = uuid.uuid4()
    async with seeded.factory() as db:
        attempts = (
            await db.scalars(
                select(NodeAttempt)
                .where(NodeAttempt.node_run_id == node_run.id)
                .order_by(NodeAttempt.attempt_no)
            )
        ).all()
        attempts[1].status = NodeAttemptStatus.IN_FLIGHT.value
        step = await db.get(NodeRun, node_run.id)
        assert step is not None
        step.status = "running"
        outbox = (
            await db.scalars(
                select(DispatchOutbox).where(DispatchOutbox.node_run_id == node_run.id)
            )
        ).one()
        outbox.status = "claimed"
        outbox.claimed_by = replacement_token
        outbox.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        await db.commit()
    both_settling = asyncio.Event()
    settle_count = 0

    async def settle(
        attempt: NodeAttempt, output: workflow_media_operations.MediaOutput, cost: Decimal
    ) -> None:
        nonlocal settle_count
        begun = dispatcher.BegunAttempt(
            workflow_run_id=seeded.run.id,
            node_run_id=node_run.id,
            node_instance_id=node_run.node_instance_id,
            organization_id=seeded.org.id,
            attempt_id=attempt.id,
            attempt_no=attempt.attempt_no,
            handler_config=None,
            handler_input=None,
            definition=definition,
            handler=handler,
            dispatch_context=_dispatch_context(seeded, node_run, attempt.attempt_no),
            timeout_seconds=None,
            retry_guarantee="none",
            dispatch_token=replacement_token if attempt.attempt_no == 2 else uuid.uuid4(),
        )
        async with seeded.factory.begin() as db:
            settle_count += 1
            if settle_count == 2:
                both_settling.set()
            await both_settling.wait()
            await dispatcher.settle(
                db,
                begun=begun,
                outcome=dispatcher.HandlerOutcome(
                    result=Completed(output=output), cost=cost, cost_is_partial=True
                ),
            )

    work = [(attempts[0], original, original_cost), (attempts[1], replay, replay_cost)]
    if replay_first:
        work.reverse()
    await asyncio.wait_for(asyncio.gather(*(settle(*item) for item in work)), timeout=20)
    await asyncio.wait_for(asyncio.gather(*(settle(*item) for item in work)), timeout=20)

    assert provider.submissions == 1
    assert provider.charges == [Decimal("0.25")]
    async with seeded.factory() as db:
        run = await db.get(WorkflowRun, seeded.run.id)
        assert run is not None and run.spent_cost == Decimal("0.25")
        assert run.cost_is_partial
        assert await workflow_run_repo.sum_attempt_costs(db, node_run_id=node_run.id) == Decimal(
            "0.25"
        )
        assert await organization_monthly_spend(db, seeded.org.id) == Decimal("0.25")
        replacement = await db.get(NodeAttempt, attempts[1].id)
        assert replacement is not None and replacement.status == NodeAttemptStatus.COMPLETED.value
