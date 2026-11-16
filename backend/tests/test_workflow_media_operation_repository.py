"""Data-access coverage for durable workflow media operations."""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workflow_media_operation import WorkflowMediaOperation
from app.repositories import workflow_media_operation as media_repo

pytestmark = pytest.mark.anyio


def _db() -> AsyncSession:
    return MagicMock(spec=AsyncSession)


async def test_get_for_node_run_returns_the_tenant_scoped_operation():
    operation = SimpleNamespace()
    result = MagicMock()
    result.scalar_one_or_none.return_value = operation
    db = _db()
    db.execute = AsyncMock(return_value=result)

    found = await media_repo.get_for_node_run(db, uuid4(), organization_id=uuid4())

    assert found is operation
    db.execute.assert_awaited_once()


async def test_get_for_node_run_for_update_returns_the_locked_operation():
    operation = SimpleNamespace()
    result = MagicMock()
    result.scalar_one_or_none.return_value = operation
    db = _db()
    db.execute = AsyncMock(return_value=result)

    found = await media_repo.get_for_node_run_for_update(db, uuid4(), organization_id=uuid4())

    assert found is operation
    db.execute.assert_awaited_once()


async def test_phase_lookup_skips_an_empty_batch_and_returns_requested_rows():
    db = _db()
    db.execute = AsyncMock()

    assert await media_repo.phases_for_node_runs(db, [], organization_id=uuid4()) == {}
    db.execute.assert_not_awaited()

    node_run_id = uuid4()
    result = MagicMock()
    result.all.return_value = [(node_run_id, "submitted")]
    db.execute.return_value = result

    phases = await media_repo.phases_for_node_runs(db, [node_run_id], organization_id=uuid4())

    assert phases == {node_run_id: "submitted"}
    db.execute.assert_awaited_once()


@pytest.mark.parametrize(("stored", "expected"), [(Decimal("1.25"), Decimal("1.25")), (0, 0)])
async def test_unsettled_estimate_sum_is_a_decimal(stored, expected):
    result = MagicMock()
    result.scalar_one.return_value = stored
    db = _db()
    db.execute = AsyncMock(return_value=result)

    total = await media_repo.sum_unsettled_estimates(db, workflow_run_id=uuid4())

    assert total == expected
    assert isinstance(total, Decimal)


async def test_create_flushes_and_refreshes_the_operation():
    db = _db()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    organization_id = uuid4()
    run_id = uuid4()
    node_run_id = uuid4()
    attempt_id = uuid4()

    operation = await media_repo.create(
        db,
        organization_id=organization_id,
        workflow_run_id=run_id,
        node_run_id=node_run_id,
        node_attempt_id=attempt_id,
        request_hash="0" * 64,
        provider="synthetic",
        model="synthetic-model",
        secret_id=None,
        secret_key_version=None,
        estimated_cost_usd=Decimal("0.50"),
    )

    assert operation.organization_id == organization_id
    assert operation.workflow_run_id == run_id
    assert operation.node_run_id == node_run_id
    assert operation.node_attempt_id == attempt_id
    assert operation.phase == "prepared"
    assert operation.estimated_cost_usd == Decimal("0.50")
    db.add.assert_called_once_with(operation)
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(operation)


async def test_update_flushes_and_refreshes_only_the_given_fields():
    operation = SimpleNamespace(phase="prepared", failure_code=None)
    db = _db()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    updated = await media_repo.update(
        db,
        operation=operation,
        update_data={"phase": "submitted", "failure_code": None},
    )

    assert updated is operation
    assert operation.phase == "submitted"
    assert operation.failure_code is None
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(operation)


def test_operation_repr_exposes_only_its_id_and_phase():
    operation = WorkflowMediaOperation(id=uuid4(), phase="prepared")

    assert repr(operation) == f"<WorkflowMediaOperation(id={operation.id}, phase=prepared)>"
