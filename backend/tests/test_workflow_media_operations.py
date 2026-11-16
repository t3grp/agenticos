"""Fake provider coverage for durable media submission and receipt handling."""

import asyncio
import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services import workflow_media_operations as media_operations
from app.services.workflow_execution import context
from app.workflows.contracts.io import FileRef
from app.workflows.contracts.results import Failed

pytestmark = pytest.mark.anyio


class _DbContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_exc_info):
        return False


class _FakeAdapter:
    def __init__(self, *, result=None, submission_error=None, resume_error=None):
        self.result = result or media_operations.ProviderMedia(
            data=b"synthetic image bytes",
            content_type="image/png",
            width=2,
            height=2,
            cost_usd=Decimal("0.10"),
        )
        self.submission_error = submission_error
        self.resume_error = resume_error
        self.submissions = 0
        self.resumes: list[str] = []
        self.cancellations: list[str] = []

    async def submit(self, on_receipt):
        self.submissions += 1
        if self.submission_error is not None:
            raise self.submission_error
        receipt = media_operations.ProviderReceipt("job-42")
        await on_receipt(receipt)
        return receipt

    async def resume(self, receipt):
        self.resumes.append(receipt.operation_id)
        if self.resume_error is not None:
            raise self.resume_error
        return self.result

    async def cancel(self, receipt):
        self.cancellations.append(receipt.operation_id)


def _operation(**updates):
    values = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "workflow_run_id": uuid4(),
        "node_run_id": uuid4(),
        "node_attempt_id": uuid4(),
        "request_hash": media_operations.request_hash({"prompt": "synthetic"}),
        "provider": "synthetic",
        "model": "synthetic-image",
        "secret_id": SECRET_ID,
        "secret_key_version": 3,
        "phase": "prepared",
        "provider_receipt": None,
        "output": None,
        "estimated_cost_usd": Decimal("0.50"),
        "reported_cost_usd": None,
        "cost_is_partial": False,
        "submitted_at": None,
        "failure_code": None,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _output() -> media_operations.MediaOutput:
    return media_operations.MediaOutput(
        file=FileRef(file_id=uuid4(), content_type="image/png", byte_size=4),
        content_type="image/png",
        width=2,
        height=2,
        receipt={},
    )


@pytest.fixture
def operation_store(monkeypatch):
    state: dict[str, object] = {"operation": None}
    current = SimpleNamespace(
        organization_id=uuid4(),
        workflow_run_id=uuid4(),
        node_run_id=uuid4(),
        attempt_no=1,
    )
    monkeypatch.setattr(context, "current", lambda: current)
    monkeypatch.setattr(media_operations, "get_worker_db_context", _DbContext)
    monkeypatch.setattr(
        media_operations.workflow_run_repo,
        "get_latest_attempt",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=SimpleNamespace(id=uuid4())),
    )
    monkeypatch.setattr(
        media_operations.workflow_run_repo,
        "get_run_by_id_for_update",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )

    async def get(_db, node_run_id, *, organization_id):
        operation = state["operation"]
        if operation is not None and operation.node_run_id == node_run_id:
            return operation
        return None

    async def create(_db, **kwargs):
        operation = _operation(**kwargs)
        state["operation"] = operation
        return operation

    async def update(_db, *, operation, update_data):
        for key, value in update_data.items():
            setattr(operation, key, value)
        return operation

    monkeypatch.setattr(media_operations.operation_repo, "get_for_node_run_for_update", get)
    monkeypatch.setattr(media_operations.operation_repo, "create", create)
    monkeypatch.setattr(media_operations.operation_repo, "update", update)
    return state, current


async def _execute(adapter, store=None):
    return await media_operations.execute(
        provider="synthetic",
        model="synthetic-image",
        secret_id=SECRET_ID,
        secret_key_version=3,
        request={"prompt": "synthetic"},
        estimated_cost_usd=Decimal("0.50"),
        adapter=adapter,
        store=store or (lambda _media: asyncio.sleep(0, result=_output())),
    )


SECRET_ID = uuid4()


async def test_known_provider_receipt_is_saved_and_polled_once(operation_store, monkeypatch):
    costs = []
    monkeypatch.setattr(
        context, "report_cost", lambda amount, *, partial=False: costs.append((amount, partial))
    )
    state, _current = operation_store
    adapter = _FakeAdapter()

    output = await _execute(adapter)

    assert isinstance(output, media_operations.MediaOutput)
    assert adapter.submissions == 1
    assert adapter.resumes == ["job-42"]
    assert state["operation"].phase == "stored"
    assert state["operation"].provider_receipt == {"operation_id": "job-42"}
    assert output.receipt["provider_operation_id"] == "job-42"
    assert costs == [(Decimal("0.10"), False)]
    assert b"synthetic image bytes" not in str(state["operation"].output).encode()


async def test_a_known_receipt_resumes_without_a_second_submission(operation_store):
    state, current = operation_store
    state["operation"] = _operation(
        node_run_id=current.node_run_id,
        phase="submitted",
        provider_receipt={"operation_id": "job-prior"},
    )
    adapter = _FakeAdapter()

    output = await _execute(adapter)

    assert isinstance(output, media_operations.MediaOutput)
    assert adapter.submissions == 0
    assert adapter.resumes == ["job-prior"]
    assert state["operation"].phase == "stored"


async def test_a_stored_output_replays_its_cost_without_a_provider_call(
    operation_store, monkeypatch
):
    state, current = operation_store
    stored = _output()
    state["operation"] = _operation(
        node_run_id=current.node_run_id,
        phase="stored",
        output=stored.model_dump(mode="json"),
        reported_cost_usd=Decimal("0.10"),
        cost_is_partial=False,
    )
    costs = []
    monkeypatch.setattr(
        context, "report_cost", lambda amount, *, partial=False: costs.append((amount, partial))
    )
    adapter = _FakeAdapter()

    output = await _execute(adapter)

    assert isinstance(output, media_operations.MediaOutput)
    assert adapter.submissions == 0
    assert adapter.resumes == []
    assert costs == [(Decimal("0.10"), False)]


async def test_an_ambiguous_submission_is_not_sent_again(operation_store):
    state, current = operation_store
    state["operation"] = _operation(node_run_id=current.node_run_id, phase="submitting")
    adapter = _FakeAdapter()

    result = await _execute(adapter)

    assert isinstance(result, Failed)
    assert result.error.code == "MEDIA_OUTCOME_UNKNOWN"
    assert adapter.submissions == 0
    assert state["operation"].phase == "submitting"


async def test_concurrent_prepared_calls_only_one_submits(operation_store, monkeypatch):
    state, current = operation_store
    state["operation"] = _operation(node_run_id=current.node_run_id)
    original_get = media_operations.operation_repo.get_for_node_run_for_update
    both_read = asyncio.Event()
    read_count = 0

    async def get_snapshot_then_live(_db, node_run_id, *, organization_id):
        nonlocal read_count
        if read_count < 2:
            read_count += 1
            snapshot = SimpleNamespace(**vars(state["operation"]))
            if read_count == 2:
                both_read.set()
            await both_read.wait()
            return snapshot
        return await original_get(_db, node_run_id, organization_id=organization_id)

    monkeypatch.setattr(
        media_operations.operation_repo, "get_for_node_run_for_update", get_snapshot_then_live
    )

    class _ConcurrentAdapter(_FakeAdapter):
        async def submit(self, on_receipt):
            self.submissions += 1
            await asyncio.sleep(0)
            receipt = media_operations.ProviderReceipt("job-42")
            await on_receipt(receipt)
            return receipt

    adapter = _ConcurrentAdapter()
    first = asyncio.create_task(_execute(adapter))
    second = asyncio.create_task(_execute(adapter))

    results = await asyncio.gather(first, second)

    assert adapter.submissions == 1
    assert sum(isinstance(result, media_operations.MediaOutput) for result in results) == 1
    assert sum(isinstance(result, Failed) for result in results) == 1
    assert state["operation"].phase == "stored"


async def test_a_submission_error_hides_secret_response_and_url(operation_store, caplog):
    state, _current = operation_store
    adapter = _FakeAdapter(
        submission_error=RuntimeError("api_key=secret https://download.invalid/?sig=secret")
    )

    with caplog.at_level(logging.WARNING):
        result = await _execute(adapter)

    assert isinstance(result, Failed)
    assert result.error.code == "MEDIA_OUTCOME_UNKNOWN"
    assert "secret" not in result.error.message
    assert "download.invalid" not in caplog.text
    assert state["operation"].phase == "unknown"
    assert state["operation"].output is None


async def test_a_storage_failure_keeps_receipt_and_blocks_retry(operation_store):
    state, _current = operation_store
    adapter = _FakeAdapter()

    async def fail_storage(_media):
        raise OSError("private response payload")

    first = await _execute(adapter, fail_storage)
    second = await _execute(adapter)

    assert isinstance(first, Failed) and isinstance(second, Failed)
    assert state["operation"].phase == "unknown"
    assert state["operation"].provider_receipt == {"operation_id": "job-42"}
    assert adapter.submissions == 1


async def test_cancellation_stops_polling_and_keeps_outcome_unknown(operation_store, monkeypatch):
    state, _current = operation_store
    adapter = _FakeAdapter(resume_error=asyncio.CancelledError())
    costs = []
    monkeypatch.setattr(
        context, "report_cost", lambda amount, *, partial=False: costs.append((amount, partial))
    )

    with pytest.raises(asyncio.CancelledError):
        await _execute(adapter)

    assert adapter.submissions == 1
    assert adapter.cancellations == ["job-42"]
    assert state["operation"].phase == "unknown"
    assert costs == []


async def test_changed_request_does_not_reuse_an_existing_operation(operation_store):
    state, current = operation_store
    state["operation"] = _operation(
        node_run_id=current.node_run_id,
        request_hash=media_operations.request_hash({"prompt": "different"}),
    )
    adapter = _FakeAdapter()

    result = await _execute(adapter)

    assert isinstance(result, Failed)
    assert adapter.submissions == 0


def test_provider_receipts_reject_urls_and_unbounded_values():
    with pytest.raises(ValueError):
        media_operations.ProviderReceipt("https://provider.invalid/download?token=secret")
    with pytest.raises(ValueError):
        media_operations.ProviderReceipt("a" * 256)


async def test_phase_update_refuses_a_missing_operation(operation_store):
    with pytest.raises(RuntimeError, match="operation disappeared"):
        await media_operations._phase("submitting", operation_id=uuid4())


async def test_phase_update_refuses_an_invalid_transition(operation_store):
    state, current = operation_store
    state["operation"] = _operation(node_run_id=current.node_run_id, phase="stored")

    with pytest.raises(RuntimeError, match="Invalid workflow media operation phase change"):
        await media_operations._phase("submitting", operation_id=state["operation"].id)


async def test_phase_update_emits_progress_for_a_live_run(operation_store, monkeypatch):
    state, current = operation_store
    state["operation"] = _operation(node_run_id=current.node_run_id)
    run = SimpleNamespace(id=current.workflow_run_id)
    emitted = []

    async def get_run(*_args, **_kwargs):
        return run

    async def append_event(_db, *, run, kind, node_run_id, payload):
        emitted.append((run, kind, node_run_id, payload))

    monkeypatch.setattr(media_operations.workflow_run_repo, "get_run_by_id_for_update", get_run)
    monkeypatch.setattr(media_operations.events, "append", append_event)

    await media_operations._phase(
        "submitting", operation_id=state["operation"].id, expected_phase="prepared"
    )

    assert emitted == [
        (
            run,
            "media_operation_progress",
            current.node_run_id,
            {"phase": "submitting", "provider": "synthetic", "model": "synthetic-image"},
        )
    ]


async def test_creation_refuses_to_submit_when_the_node_has_no_attempt(
    operation_store, monkeypatch
):
    monkeypatch.setattr(
        media_operations.workflow_run_repo,
        "get_latest_attempt",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )

    with pytest.raises(RuntimeError, match="no active node attempt"):
        await _execute(_FakeAdapter())


async def test_a_stored_result_without_a_cost_replays_without_reporting_cost(
    operation_store, monkeypatch
):
    state, current = operation_store
    stored = _output()
    state["operation"] = _operation(
        node_run_id=current.node_run_id,
        phase="stored",
        output=stored.model_dump(mode="json"),
        reported_cost_usd=None,
    )
    report_cost = MagicMock()
    monkeypatch.setattr(context, "report_cost", report_cost)

    output = await _execute(_FakeAdapter())

    assert isinstance(output, media_operations.MediaOutput)
    report_cost.assert_not_called()


async def test_an_unsupported_operation_phase_is_not_resumed(operation_store):
    state, current = operation_store
    state["operation"] = _operation(node_run_id=current.node_run_id, phase="retired")
    adapter = _FakeAdapter()

    result = await _execute(adapter)

    assert isinstance(result, Failed)
    assert adapter.submissions == 0


async def test_a_submitted_operation_without_a_receipt_stays_unknown(operation_store):
    state, current = operation_store
    state["operation"] = _operation(
        node_run_id=current.node_run_id, phase="submitted", provider_receipt=None
    )
    adapter = _FakeAdapter()

    result = await _execute(adapter)

    assert isinstance(result, Failed)
    assert state["operation"].phase == "unknown"
    assert adapter.submissions == 0


async def test_a_provider_that_changes_its_receipt_is_never_polled(operation_store):
    class _ChangingReceiptAdapter(_FakeAdapter):
        async def submit(self, on_receipt):
            self.submissions += 1
            await on_receipt(media_operations.ProviderReceipt("job-first"))
            return media_operations.ProviderReceipt("job-second")

    state, _current = operation_store
    adapter = _ChangingReceiptAdapter()

    result = await _execute(adapter)

    assert isinstance(result, Failed)
    assert state["operation"].phase == "unknown"
    assert adapter.resumes == []


async def test_an_immediate_provider_receipt_is_not_invented(operation_store, monkeypatch):
    class _ImmediateAdapter:
        async def submit(self, on_receipt):
            return media_operations.ProviderMedia(
                data=b"synthetic",
                content_type="image/png",
                cost_usd=Decimal("0.10"),
            )

        async def resume(self, receipt):
            raise AssertionError("an immediate provider result does not resume")

        async def cancel(self, receipt):
            return None

    async def store(_media):
        return _output()

    monkeypatch.setattr(context, "report_cost", lambda *_args, **_kwargs: None)

    output = await media_operations.execute(
        provider="synthetic",
        model="synthetic-image",
        secret_id=SECRET_ID,
        secret_key_version=3,
        request={"prompt": "synthetic"},
        estimated_cost_usd=Decimal("0.50"),
        adapter=_ImmediateAdapter(),
        store=store,
    )

    assert isinstance(output, media_operations.MediaOutput)
    assert "provider_operation_id" not in output.receipt


async def test_cancellation_before_a_receipt_does_not_try_to_cancel_a_job(operation_store):
    class _CancelledBeforeReceiptAdapter(_FakeAdapter):
        async def submit(self, on_receipt):
            self.submissions += 1
            raise asyncio.CancelledError()

    state, _current = operation_store
    adapter = _CancelledBeforeReceiptAdapter()

    with pytest.raises(asyncio.CancelledError):
        await _execute(adapter)

    assert adapter.cancellations == []
    assert state["operation"].phase == "unknown"


async def test_cancellation_logs_when_provider_cleanup_fails(operation_store, caplog):
    class _CancelFailureAdapter(_FakeAdapter):
        async def cancel(self, receipt):
            raise RuntimeError("signed url must not be logged")

    state, _current = operation_store
    adapter = _CancelFailureAdapter(resume_error=asyncio.CancelledError())

    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        await _execute(adapter)

    assert "signed url" not in caplog.text
    assert "workflow_media_cancel_failed" in caplog.text
    assert state["operation"].phase == "unknown"
