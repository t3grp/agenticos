"""The schedule a trigger node accepts, and a chat run's answer (#1792).

The database CHECK says the same things about an exposure's columns; these are
the problems a publish reports on the node instead of an IntegrityError, and
the one branch of a chat answer the end-to-end suite does not reach - a run
with a text answer.
"""

import uuid
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.core.permissions import AuthContext, OrgRoleName
from app.db.models.workflow_run import WorkflowRunStatus
from app.services.workflow_execution import delivery
from app.workflows.nodes._triggers import ScheduleTriggerConfig

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "fields",
    [
        {"schedule_kind": "interval", "interval_seconds": None},
        {"schedule_kind": "interval", "interval_seconds": 30},
        {"schedule_kind": "cron"},
        {"schedule_kind": "cron", "cron_expression": "0 0 31 2 *"},
        {"schedule_kind": "cron", "cron_expression": "not cron"},
    ],
    ids=[
        "interval-without-seconds",
        "interval-under-the-floor",
        "cron-without-expression",
        "cron-that-never-fires",
        "cron-that-does-not-parse",
    ],
)
def test_a_schedule_trigger_that_could_never_fire_is_refused(fields):
    with pytest.raises(ValidationError):
        ScheduleTriggerConfig.model_validate(fields)


def test_a_schedule_trigger_defaults_to_hourly():
    config = ScheduleTriggerConfig()
    assert (config.schedule_kind, config.interval_seconds, config.input) == ("interval", 3600, {})
    # A cron schedule keeps the interval's default, which switching it on ignores.
    assert (
        ScheduleTriggerConfig(schedule_kind="cron", cron_expression="0 9 * * 1-5").cron_expression
        == "0 9 * * 1-5"
    )


async def test_a_chat_run_with_a_text_answer_says_it_in_the_message():
    run = MagicMock(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        reply_conversation_id=uuid.uuid4(),
        parent_node_run_id=None,
        status="failed",
        output={"text": "Three leads are ready."},
        error={"code": "X", "message": "The last step failed"},
    )
    with (
        patch.object(delivery.conversation_repo, "save_workflow_message", new=AsyncMock()) as write,
        patch.object(delivery.workflow_repo, "get", new=AsyncMock(return_value=None)),
    ):
        await delivery.deliver_result(MagicMock(), run=run)
    kwargs = write.await_args.kwargs
    assert kwargs["content"] == "Three leads are ready."
    # A workflow deleted under a finishing run leaves the card without a name.
    card, words = kwargs["parts"]
    assert "workflow_name" not in card
    assert card["error"] == "The last step failed"
    assert words == {"type": "text", "text": "Three leads are ready."}


@pytest.mark.parametrize("name", [None, "Planner"])
async def test_a_chat_run_is_saved_before_it_finishes(name: str | None):
    run = MagicMock(id=uuid.uuid4(), workflow_id=uuid.uuid4(), status=WorkflowRunStatus.RUNNING)
    conversation_id, organization_id = uuid.uuid4(), uuid.uuid4()
    workflow = None if name is None else MagicMock(name=name)
    if workflow is not None:
        workflow.name = name
    with (
        patch.object(delivery.conversation_repo, "save_workflow_message", new=AsyncMock()) as write,
        patch.object(delivery.workflow_repo, "get", new=AsyncMock(return_value=workflow)),
    ):
        await delivery.deliver_started(
            MagicMock(), run=run, conversation_id=conversation_id, organization_id=organization_id
        )
    assert write.await_args is not None
    kwargs = write.await_args.kwargs
    assert kwargs["conversation_id"] == conversation_id
    assert kwargs["workflow_run_id"] == run.id
    assert kwargs["content"] == ""
    assert kwargs["parts"][0]["status"] == "running"
    assert kwargs["parts"][0].get("workflow_name") == name


async def test_a_runs_files_are_listed_from_what_the_query_answers():
    """The listing's own return, which the end-to-end suites reach only after an await
    the coverage tracer loses."""
    from app.repositories import workflow_file as workflow_file_repo

    row = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    db = MagicMock(execute=AsyncMock(return_value=result))
    assert await workflow_file_repo.list_for_run(
        db, workflow_run_id=uuid.uuid4(), organization_id=uuid.uuid4()
    ) == [row]


@pytest.mark.security
@pytest.mark.parametrize("active", [None, False])
async def test_background_authority_refuses_a_user_lost_after_membership_read(monkeypatch, active):
    import importlib
    from types import SimpleNamespace

    module = importlib.import_module("app.services.workflow_exposure")
    monkeypatch.setattr(
        module.member_repo, "get_active", AsyncMock(return_value=SimpleNamespace(role="viewer"))
    )
    user = None if active is None else SimpleNamespace(is_active=False, is_app_admin=True)
    monkeypatch.setattr(module.user_repo, "get_by_id", AsyncMock(return_value=user))
    exposure = SimpleNamespace(
        execution_principal_user_id=uuid.uuid4(), organization_id=uuid.uuid4()
    )
    assert await module.WorkflowExposureService(MagicMock())._fire_context(exposure) is None


async def test_archiving_pauses_only_the_registrations_that_are_running(monkeypatch):
    import importlib
    from types import SimpleNamespace

    module = importlib.import_module("app.services.workflow_exposure")
    running = SimpleNamespace(id=uuid.uuid4(), workflow_id=uuid.uuid4(), adapter="webhook")
    running.is_active = True
    paused = SimpleNamespace(id=uuid.uuid4(), workflow_id=running.workflow_id, adapter="schedule")
    paused.is_active = False
    update = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(
        module.workflow_exposure_repo,
        "list_for_workflow",
        AsyncMock(return_value=[paused, running]),
    )
    monkeypatch.setattr(module.workflow_exposure_repo, "update", update)
    monkeypatch.setattr(module, "record_audit", audit)
    ctx = AuthContext(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role=OrgRoleName.OWNER)

    await module.WorkflowExposureService(MagicMock()).pause(
        ctx, SimpleNamespace(id=running.workflow_id)
    )

    assert [call.kwargs["exposure"] for call in update.await_args_list] == [running]
    assert update.await_args.kwargs["update_data"] == {"is_active": False}
    assert [call.kwargs["action"] for call in audit.await_args_list] == ["workflow.exposure_paused"]


# A tick whose stored input no longer fits the schedule's declared schema


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _schedule_exposure(node_id: uuid.UUID, run_input: dict):
    from datetime import datetime
    from types import SimpleNamespace

    return SimpleNamespace(
        id=uuid.uuid4(),
        schedule_kind="interval",
        interval_seconds=3600,
        cron_expression=None,
        timezone="UTC",
        is_active=True,
        adapter="schedule",
        next_fire_at=datetime(2026, 11, 25, tzinfo=UTC),
        node_instance_id=node_id,
        workflow_version_id=uuid.uuid4(),
        run_input=run_input,
    )


def _schedule_version(node_id: uuid.UUID):
    from types import SimpleNamespace

    from app.workflows.contracts.payload_schema import PayloadField
    from app.workflows.graph.model import NodeInstance, NodePosition, WorkflowGraph

    config = ScheduleTriggerConfig(
        payload_fields=[PayloadField(name="region", type="text")],
        input={"region": "eu"},
    )
    graph = WorkflowGraph(
        entry_node_id=node_id,
        nodes=(
            NodeInstance(
                id=node_id,
                definition_id="trigger.schedule",
                definition_version=2,
                config=config.model_dump(mode="json"),
                layout=NodePosition(x=0, y=0),
            ),
        ),
    )
    return SimpleNamespace(id=uuid.uuid4(), graph=graph.model_dump(mode="json"))


async def test_a_tick_whose_stored_input_no_longer_fits_is_skipped(monkeypatch):
    import importlib
    from datetime import datetime
    from types import SimpleNamespace

    module = importlib.import_module("app.services.workflow_exposure")
    node_id = uuid.uuid4()
    exposure = _schedule_exposure(node_id, {"region": 5})
    ctx = AuthContext(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role=OrgRoleName.OWNER)
    workflow = SimpleNamespace(settings={})
    admit = AsyncMock()
    update = AsyncMock()
    monkeypatch.setattr(
        module.workflow_exposure_repo,
        "claim_due_schedules",
        AsyncMock(return_value=[exposure]),
    )
    monkeypatch.setattr(module.workflow_exposure_repo, "update", update)
    monkeypatch.setattr(module, "_queue_due_check", AsyncMock())
    db = MagicMock(begin_nested=MagicMock(return_value=_Savepoint()))
    service = module.WorkflowExposureService(db)
    monkeypatch.setattr(service, "_last_run_live", AsyncMock(return_value=False))
    monkeypatch.setattr(
        service,
        "_fire_context",
        AsyncMock(return_value=(ctx, workflow, _schedule_version(node_id))),
    )
    monkeypatch.setattr(service.runs, "admit_pinned", admit)

    admitted = await service.fire_due(now=datetime(2026, 11, 25, tzinfo=UTC))

    assert admitted == []
    assert admit.await_count == 0
    # The clock still advanced: the tick is spent, only the run is skipped.
    assert "next_fire_at" in update.await_args_list[0].kwargs["update_data"]


async def test_a_tick_whose_stored_input_fits_is_admitted(monkeypatch):
    import importlib
    from datetime import datetime
    from types import SimpleNamespace

    module = importlib.import_module("app.services.workflow_exposure")
    node_id = uuid.uuid4()
    exposure = _schedule_exposure(node_id, {"region": "eu"})
    ctx = AuthContext(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role=OrgRoleName.OWNER)
    workflow = SimpleNamespace(settings={})
    run, entry_node_run_id = SimpleNamespace(id=uuid.uuid4()), uuid.uuid4()
    admit = AsyncMock(return_value=(run, entry_node_run_id))
    update = AsyncMock()
    monkeypatch.setattr(
        module.workflow_exposure_repo,
        "claim_due_schedules",
        AsyncMock(return_value=[exposure]),
    )
    monkeypatch.setattr(module.workflow_exposure_repo, "update", update)
    monkeypatch.setattr(module, "_queue_due_check", AsyncMock())
    db = MagicMock(begin_nested=MagicMock(return_value=_Savepoint()))
    service = module.WorkflowExposureService(db)
    monkeypatch.setattr(service, "_last_run_live", AsyncMock(return_value=False))
    monkeypatch.setattr(
        service,
        "_fire_context",
        AsyncMock(return_value=(ctx, workflow, _schedule_version(node_id))),
    )
    monkeypatch.setattr(service.runs, "admit_pinned", admit)

    admitted = await service.fire_due(now=datetime(2026, 11, 25, tzinfo=UTC))

    assert admitted == [(run.id, entry_node_run_id)]
    assert admit.await_args.kwargs["run_input"]["input"] == {"region": "eu"}
    assert "fired_at" in admit.await_args.kwargs["run_input"]
    assert update.await_args.kwargs["update_data"]["last_fired_at"] == datetime(
        2026, 11, 25, tzinfo=UTC
    )
