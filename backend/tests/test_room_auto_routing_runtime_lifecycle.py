"""The one-shot routing worker links runs and dispatches only current snapshots."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import ConflictError
from app.core.permissions import AuthContext
from app.db.models.agent_run import RunStatus, RunSurface
from app.schemas.room_auto_routing import RoomAutoRoutingDecisionReceipt
from app.services import agent_runner as agent_runner_module
from app.services import room_auto_routing as module

pytestmark = [pytest.mark.anyio, pytest.mark.security]


def _harness(monkeypatch):
    db = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    org_id, conversation_id, requester_id = uuid4(), uuid4(), uuid4()
    accepted_message_id = uuid4()
    router_id, router_version_id, profile_id = uuid4(), uuid4(), uuid4()
    selected = [uuid4(), uuid4()]
    selected_versions = [uuid4(), uuid4()]
    decision_id, submission_id = uuid4(), uuid4()
    router_run_id = uuid4()
    decision = SimpleNamespace(
        id=decision_id,
        organization_id=org_id,
        conversation_id=conversation_id,
        submission_id=submission_id,
        accepted_message_id=accepted_message_id,
        requester_id=requester_id,
        thread_root_message_id=None,
        status="pending",
        claim_token=None,
        claim_generation=0,
        router_agent_id=router_id,
        router_version_id=router_version_id,
        model_profile_id=profile_id,
        model_profile_fingerprint="a" * 64,
        policy_revision=3,
        quiet_window_ms=2500,
        max_responders=2,
        grouped_message_ids=[str(accepted_message_id)],
        candidate_snapshot=[
            {
                "agent_id": str(agent_id),
                "version_id": str(version_id),
                "name": f"Room responder {index}",
                "role": "specialist",
                "description": "A frozen eligible responder.",
            }
            for index, (agent_id, version_id) in enumerate(
                zip(selected, selected_versions, strict=True), start=1
            )
        ],
    )
    message = SimpleNamespace(id=accepted_message_id)
    root = SimpleNamespace(
        id=submission_id,
        organization_id=org_id,
        conversation_id=conversation_id,
        requester_id=requester_id,
        message_id=accepted_message_id,
        parent_submission_id=None,
        status="posted",
        client_message_id="accepted-client-message",
        fingerprint="f" * 64,
        agent_id=None,
        version_id=None,
        dispatch="automatic",
    )
    room = SimpleNamespace(id=conversation_id, organization_id=org_id)
    ctx = AuthContext(user_id=requester_id, organization_id=org_id, role="owner")
    grouped = [(message, root, [])]
    history = object()
    history_rows = [message]
    service = module.RoomAutoRoutingService(db)
    service.rooms.queue = AsyncMock()
    service._hold_context_scope = AsyncMock(return_value={conversation_id})
    service._prepare_classification = AsyncMock(
        return_value=(ctx, room, grouped, history, history_rows)
    )
    service._routing_prompt = MagicMock(return_value="bounded authorized room context")
    service._validate_snapshot = AsyncMock(return_value=(room, grouped))
    service._lock_model_profile = AsyncMock()

    async def get_decision(*_args, **_kwargs):
        return decision

    async def claim_decision(_db, _decision_id, *, claim_token: UUID, **_kwargs):
        decision.status = "running"
        decision.claim_token = claim_token
        decision.claim_generation = 1
        return decision

    async def finish_decision(
        _db, _decision_id, *, receipt: RoomAutoRoutingDecisionReceipt, **_kwargs
    ):
        decision.status = receipt.status
        decision.router_run_id = receipt.router_run_id
        decision.decision_output = (
            None
            if receipt.decision_output is None
            else receipt.decision_output.model_dump(mode="json")
        )
        decision.error_code = receipt.error_code
        return True

    monkeypatch.setattr(module.auto_repo, "get_decision", AsyncMock(side_effect=get_decision))
    monkeypatch.setattr(module.auto_repo, "claim_ready_decision", claim_decision)
    monkeypatch.setattr(module.auto_repo, "finish_decision", finish_decision)
    monkeypatch.setattr(
        module.auto_repo, "has_earlier_active_submission", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        module.auto_repo, "has_pending_or_running_before_submission", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(module.room_repo, "get", AsyncMock(return_value=root))
    monkeypatch.setattr(module.room_repo, "active_count", AsyncMock(return_value=0))
    monkeypatch.setattr(module.room_repo, "save", AsyncMock())
    monkeypatch.setattr(module.room_repo, "add", AsyncMock())
    monkeypatch.setattr(module, "announce_request", MagicMock())
    monkeypatch.setattr(module, "acting_member", AsyncMock(return_value=ctx))
    monkeypatch.setattr(
        module,
        "RoomThreadContextAccessService",
        lambda _db: SimpleNamespace(assert_responder_visible=AsyncMock()),
    )
    router_run = SimpleNamespace(id=router_run_id, status=RunStatus.COMPLETED.value)
    monkeypatch.setattr(module.agent_run_repo, "get_run", AsyncMock(return_value=router_run))
    return SimpleNamespace(
        db=db,
        service=service,
        decision=decision,
        ctx=ctx,
        root=root,
        room=room,
        grouped=grouped,
        history=history,
        history_rows=history_rows,
        router_id=router_id,
        router_version_id=router_version_id,
        profile_id=profile_id,
        selected=selected,
        selected_versions=selected_versions,
        router_run=router_run,
        router_run_id=router_run_id,
    )


def _install_runner(
    monkeypatch, harness, *, selected_ids, provider_calls, fail_before_provider=False
):
    class FakeAgentRunnerService:
        def __init__(self, _db):
            pass

        async def execute(self, _ctx, agent_id, prompt, **kwargs):
            assert agent_id == harness.router_id
            assert prompt
            assert kwargs["surface"] is RunSurface.WEB
            assert kwargs["conversation_id"] is None
            assert kwargs["version_id"] == harness.router_version_id
            assert kwargs["model_profile_id"] == harness.profile_id
            assert kwargs["run_limit_usd"] == Decimal("0.05")
            assert kwargs["automatic_router"] is True
            await kwargs["on_run_open"](harness.router_run)
            await kwargs["check_model_request"](harness.db)
            if fail_before_provider:
                raise AssertionError("authority fence should have rejected before provider call")
            provider_calls.append(harness.router_run_id)
            kwargs["structured"].append(
                {"selected_ids": [str(agent_id) for agent_id in selected_ids]}
            )
            if kwargs["result_authority_check"] is not None:
                await kwargs["result_authority_check"](harness.db)
            return "", harness.router_run

    monkeypatch.setattr(agent_runner_module, "AgentRunnerService", FakeAgentRunnerService)


async def test_selected_decision_links_one_router_run_and_queues_only_frozen_candidates(
    monkeypatch,
):
    harness = _harness(monkeypatch)
    provider_calls: list[UUID] = []
    _install_runner(
        monkeypatch,
        harness,
        selected_ids=harness.selected,
        provider_calls=provider_calls,
    )

    await harness.service.run_decision(
        harness.decision.organization_id,
        harness.decision.id,
    )

    assert provider_calls == [harness.router_run_id]
    assert harness.decision.status == "selected"
    assert harness.decision.router_run_id == harness.router_run_id
    assert harness.root.agent_id == harness.selected[0]
    assert harness.root.version_id == harness.selected_versions[0]
    assert harness.root.status == "queued"
    assert harness.service.rooms.queue.await_count == 2
    child = module.room_repo.add.await_args.args[1]
    assert child.agent_id == harness.selected[1]
    assert child.version_id == harness.selected_versions[1]
    assert child.parent_submission_id == harness.root.id
    assert child.status == "queued"
    assert child.message_id == harness.decision.accepted_message_id

    await harness.service.run_decision(
        harness.decision.organization_id,
        harness.decision.id,
    )

    assert provider_calls == [harness.router_run_id]
    assert harness.service.rooms.queue.await_count == 2


async def test_uncertain_failure_after_router_run_is_unknown_and_never_replayed(monkeypatch):
    harness = _harness(monkeypatch)
    provider_calls: list[UUID] = []

    class UncertainRunner:
        def __init__(self, _db):
            pass

        async def execute(self, _ctx, _agent_id, _prompt, **kwargs):
            await kwargs["on_run_open"](harness.router_run)
            await kwargs["check_model_request"](harness.db)
            provider_calls.append(harness.router_run_id)
            raise TimeoutError

    monkeypatch.setattr(agent_runner_module, "AgentRunnerService", UncertainRunner)

    await harness.service.run_decision(harness.decision.organization_id, harness.decision.id)

    assert provider_calls == [harness.router_run_id]
    assert harness.decision.status == "unknown"
    assert harness.decision.router_run_id == harness.router_run_id
    assert harness.service.rooms.queue.await_count == 0

    await harness.service.run_decision(harness.decision.organization_id, harness.decision.id)

    assert provider_calls == [harness.router_run_id]
    assert harness.service.rooms.queue.await_count == 0


async def test_changed_authority_stops_before_provider_invocation(monkeypatch):
    harness = _harness(monkeypatch)
    provider_calls: list[UUID] = []
    _install_runner(
        monkeypatch,
        harness,
        selected_ids=harness.selected,
        provider_calls=provider_calls,
        fail_before_provider=True,
    )
    harness.service._validate_snapshot = AsyncMock(
        side_effect=ConflictError(message="Automatic routing policy changed")
    )

    await harness.service.run_decision(harness.decision.organization_id, harness.decision.id)

    assert provider_calls == []
    assert harness.decision.status == "failed"
    assert harness.decision.error_code == "authority_changed"
    assert harness.service.rooms.queue.await_count == 0


async def test_authority_revoked_before_dispatch_does_not_queue_responders(monkeypatch):
    harness = _harness(monkeypatch)
    provider_calls: list[UUID] = []
    _install_runner(
        monkeypatch,
        harness,
        selected_ids=harness.selected,
        provider_calls=provider_calls,
    )
    checks = 0

    async def validate(*_args, **_kwargs):
        nonlocal checks
        checks += 1
        if checks == 3:
            raise ConflictError(message="The responder audience changed")
        return harness.room, harness.grouped

    harness.service._validate_snapshot = AsyncMock(side_effect=validate)

    await harness.service.run_decision(harness.decision.organization_id, harness.decision.id)

    assert provider_calls == [harness.router_run_id]
    assert checks == 3
    assert harness.decision.status == "failed"
    assert harness.decision.error_code == "authority_changed"
    assert harness.root.status == "posted"
    assert harness.service.rooms.queue.await_count == 0
    module.room_repo.add.assert_not_awaited()
