"""Synthetic routing decisions preserve requester authority and canonical ownership."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import (
    AlreadyExistsError,
    AuthorizationError,
    BadRequestError,
    NotFoundError,
)
from app.core.permissions import AuthContext
from app.db.models.room_routing import RoomLeadPolicy, RoomRoutingProposal
from app.schemas.room_routing import (
    AgentPin,
    HandoffItem,
    LeadPolicyUpdate,
    RoutingDecision,
    RoutingOutput,
)
from app.schemas.task import TaskRead
from app.services import room_routing as module

pytestmark = pytest.mark.anyio


@pytest.fixture
def routing(monkeypatch):
    org, room, requester, reviewer = uuid4(), uuid4(), uuid4(), uuid4()
    ctx = AuthContext(user_id=requester, organization_id=org, role="owner")
    other = AuthContext(user_id=reviewer, organization_id=org, role="member")
    outsider = AuthContext(user_id=uuid4(), organization_id=uuid4(), role="owner")
    pin = AgentPin(agent_id=uuid4(), version_id=uuid4())
    item = HandoffItem(
        item_id=uuid4(),
        title="Reconcile synthetic batch",
        expected_result="List mismatches for review",
        specialist=pin,
        accountable_user_id=requester,
        reviewer_user_id=reviewer,
    )
    policy = RoomLeadPolicy(
        organization_id=org,
        conversation_id=room,
        revision=2,
        configuration={
            "enabled": True,
            "lead": pin.model_dump(mode="json"),
            "specialists": [pin.model_dump(mode="json")],
            "max_items": 3,
        },
    )
    source = SimpleNamespace(
        id=uuid4(),
        organization_id=org,
        conversation_id=room,
        requester_id=requester,
        ask_lead=True,
        lead_policy_revision=2,
        status="completed",
        run_id=uuid4(),
        thread_root_message_id=uuid4(),
    )
    proposal = RoomRoutingProposal(
        id=uuid4(),
        organization_id=org,
        conversation_id=room,
        source_submission_id=source.id,
        thread_root_message_id=source.thread_root_message_id,
        requester_id=requester,
        policy_revision=2,
        revision=1,
        status="pending",
        items=[item.model_dump(mode="json")],
    )
    db = MagicMock()
    db.flush = AsyncMock()
    service = module.RoomRoutingService(db)
    topic = SimpleNamespace(project_id=uuid4(), is_archived=False)
    allowed = {requester, reviewer}

    async def get_room(caller, conversation_id, **kwargs):
        if (
            caller.organization_id != org
            or caller.subject_id not in allowed
            or conversation_id != room
        ):
            raise NotFoundError(message="Room not found", details={})
        return topic

    monkeypatch.setattr(service.rooms, "room", AsyncMock(side_effect=get_room))
    monkeypatch.setattr(service.rooms, "authority", AsyncMock(return_value=(ctx, topic, source)))
    monkeypatch.setattr(module, "hold_subject", AsyncMock())
    monkeypatch.setattr(module.repo, "policy", AsyncMock(return_value=policy))
    monkeypatch.setattr(
        module.repo,
        "proposal",
        AsyncMock(
            side_effect=lambda db, org_id, room_id, proposal_id: (
                proposal
                if org_id == org and room_id == room and proposal_id == proposal.id
                else None
            )
        ),
    )
    monkeypatch.setattr(module.repo, "for_submission", AsyncMock(return_value=None))
    monkeypatch.setattr(module.repo, "for_task", AsyncMock(return_value=None))
    writes = []

    async def save(db, row):
        writes.append(row)

    monkeypatch.setattr(module.repo, "save", AsyncMock(side_effect=save))
    monkeypatch.setattr(module.repo, "proposals", AsyncMock(return_value=[proposal]))
    monkeypatch.setattr(module.repo, "links", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.submission_repo, "get", AsyncMock(return_value=source))
    monkeypatch.setattr(module.conversation_repo, "get_thread_root", AsyncMock(return_value=source))
    registry = MagicMock(
        get_runnable_spec=AsyncMock(
            return_value=(None, SimpleNamespace(capabilities=[]), pin.version_id)
        ),
        get_pinned_spec=AsyncMock(
            return_value=(None, SimpleNamespace(capabilities=[]), pin.version_id)
        ),
    )
    monkeypatch.setattr(module, "AgentRegistryService", lambda db: registry)
    monkeypatch.setattr(
        module, "ProjectService", lambda db: SimpleNamespace(get=AsyncMock(return_value=topic))
    )
    monkeypatch.setattr(
        module.task_repo, "for_scope", AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    )
    monkeypatch.setattr(module, "board_allows", AsyncMock(return_value=True))
    monkeypatch.setattr(
        module,
        "member_context",
        AsyncMock(
            side_effect=lambda db, organization_id, user_id: (
                other if organization_id == org and user_id in allowed else None
            )
        ),
    )
    monkeypatch.setattr(
        module,
        "acting_member",
        AsyncMock(
            side_effect=lambda db, organization_id, user_id: (
                ctx if organization_id == org and requester in allowed else None
            )
        ),
    )
    created = []

    async def create(caller, data):
        task = TaskRead(
            **data.model_dump(exclude={"conversation_id", "external_id"}),
            id=uuid4(),
            table_id=uuid4(),
            board_id=uuid4(),
            revision=1,
            conversation_id=uuid4(),
            outcome_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        created.append(task)
        return task

    async def update(caller, task_id, data):
        task = next(task for task in created if task.id == task_id)
        task.next_actor = data.next_actor
        task.revision += 1
        return task

    monkeypatch.setattr(service.tasks, "create", AsyncMock(side_effect=create))
    monkeypatch.setattr(service.tasks, "update", AsyncMock(side_effect=update))
    monkeypatch.setattr(
        service.tasks,
        "get",
        AsyncMock(
            side_effect=lambda caller, task_id: next(task for task in created if task.id == task_id)
        ),
    )
    return SimpleNamespace(**locals())


def decision(r):
    return RoutingDecision(
        expected_revision=1, idempotency_key="lost-ack-1", item_ids=[r.item.item_id]
    )


async def test_acceptance_creates_one_canonical_task_and_replay_does_not_dispatch_again(routing):
    r = routing
    first = await r.service.decide(r.ctx, r.room, r.proposal.id, decision(r), accept=True)
    again = await r.service.decide(r.ctx, r.room, r.proposal.id, decision(r), accept=True)
    assert first.status == again.status == "accepted"
    assert len(r.created) == 1
    assert r.created[0].scope == "project"
    assert r.created[0].next_actor == "agent"
    assert r.service.tasks.update.await_count == 1
    assert (
        r.service.tasks.create.call_args.args[1].external_id
        == f"room-handoff:{r.proposal.id}:{r.item.item_id}"
    )
    link = r.writes[0]
    assert link.thread_root_message_id == r.source.thread_root_message_id
    assert link.reviewer_user_id == r.reviewer
    assert link.version_id == r.pin.version_id


async def test_discard_never_creates_or_dispatches_a_task(routing):
    r = routing
    data = RoutingDecision(expected_revision=1, idempotency_key="discard")
    result = await r.service.decide(r.ctx, r.room, r.proposal.id, data, accept=False)
    assert result.status == "discarded"
    assert not r.created
    r.service.tasks.update.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        "policy",
        "proposal",
        "source",
        "thread",
        "requester",
        "specialist",
        "reviewer",
        "board",
        "foreign_room",
        "foreign_tenant",
        "unknown_item",
        "duplicate_item",
    ],
)
async def test_changed_or_foreign_authority_refuses_before_any_dispatch(
    routing, change, monkeypatch
):
    r = routing
    ctx, room, data = r.ctx, r.room, decision(r)
    if change == "policy":
        r.policy.revision += 1
    elif change == "proposal":
        r.proposal.revision += 1
    elif change == "source":
        r.source.status = "cancelled"
    elif change == "thread":
        r.source.thread_root_message_id = uuid4()
    elif change == "requester":
        r.allowed.remove(r.requester)
    elif change == "specialist":
        r.registry.get_runnable_spec.side_effect = NotFoundError(
            message="Agent not found", details={}
        )
    elif change == "reviewer":
        r.allowed.remove(r.reviewer)
    elif change == "board":
        monkeypatch.setattr(module, "board_allows", AsyncMock(return_value=False))
    elif change == "foreign_room":
        room = uuid4()
    elif change == "foreign_tenant":
        ctx = r.outsider
    elif change == "unknown_item":
        data.item_ids = [uuid4()]
    elif change == "duplicate_item":
        data.item_ids *= 2
    with pytest.raises((AlreadyExistsError, AuthorizationError, BadRequestError, NotFoundError)):
        await r.service.decide(ctx, room, r.proposal.id, data, accept=True)
    assert not r.created
    r.service.tasks.update.assert_not_awaited()


async def test_another_room_member_cannot_accept_the_requesters_proposal(routing):
    r = routing
    with pytest.raises(NotFoundError):
        await r.service.decide(r.other, r.room, r.proposal.id, decision(r), accept=True)
    assert not r.created


async def test_same_proposal_with_a_different_decision_conflicts(routing):
    r = routing
    await r.service.decide(r.ctx, r.room, r.proposal.id, decision(r), accept=True)
    with pytest.raises(AlreadyExistsError):
        await r.service.decide(
            r.ctx,
            r.room,
            r.proposal.id,
            RoutingDecision(expected_revision=1, idempotency_key="discard"),
            accept=False,
        )
    assert len(r.created) == 1


async def test_model_output_is_only_a_proposal_until_human_acceptance(routing):
    r = routing
    await r.service.completed(
        r.source, RoutingOutput(answer="Please review", items=[r.item]).model_dump_json()
    )
    assert len(r.writes) == 1
    assert r.writes[0].status == "pending"
    assert not r.created
    r.service.tasks.update.assert_not_awaited()


async def test_policy_limits_model_output_and_never_dispatches_it(routing):
    r = routing
    r.policy.configuration = {**r.policy.configuration, "max_items": 1}
    other = r.item.model_copy(update={"item_id": uuid4()})
    with pytest.raises(BadRequestError):
        await r.service.completed(
            r.source, RoutingOutput(answer="Review", items=[r.item, other]).model_dump_json()
        )
    assert not r.writes and not r.created


async def test_cards_read_current_task_state_without_private_working_links(routing, monkeypatch):
    r = routing
    await r.service.decide(r.ctx, r.room, r.proposal.id, decision(r), accept=True)
    link = r.writes[0]
    r.created[0].status = "waiting_for_user"
    monkeypatch.setattr(module.repo, "links", AsyncMock(return_value=[link]))
    read = await r.service.read(r.ctx, r.room, link.thread_root_message_id)
    card = read.tasks[0].model_dump()
    assert card["status"] == "waiting_for_user"
    assert "conversation_id" not in card and "outcome_id" not in card and "description" not in card
    r.created[0].status = "cancelled"
    assert (await r.service.read(r.ctx, r.room, link.thread_root_message_id)).tasks[
        0
    ].status == "cancelled"
    assert not (await r.service.read(r.ctx, r.room, None)).tasks
    assert r.service.tasks.update.await_count == 1


async def test_disabled_routing_blocks_acceptance_but_keeps_cards(routing):
    r = routing
    r.policy.configuration = {**r.policy.configuration, "enabled": False}
    with pytest.raises(BadRequestError):
        await r.service.decide(r.ctx, r.room, r.proposal.id, decision(r), accept=True)
    assert not r.created


async def test_policy_revision_conflicts_and_unpublished_pins_are_refused(routing):
    r = routing
    with pytest.raises(AlreadyExistsError):
        await r.service.update_policy(r.ctx, r.room, LeadPolicyUpdate(expected_revision=1))
    r.registry.get_runnable_spec.side_effect = BadRequestError(
        message="Publish this agent first", details={}
    )
    with pytest.raises(BadRequestError):
        await r.service.update_policy(
            r.ctx, r.room, LeadPolicyUpdate(expected_revision=2, enabled=True, lead=r.pin)
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"answer": "x", "items": [{}]},
        {"answer": "x", "extra": "untrusted"},
        {"answer": "x", "items": [None] * 4},
    ],
)
def test_untrusted_output_has_a_bounded_closed_schema(payload):
    with pytest.raises(ValidationError):
        RoutingOutput.model_validate(payload)


async def test_accepts_only_selected_items_and_retains_other_items_as_decided_history(routing):
    r = routing
    extra = r.item.model_copy(update={"item_id": uuid4(), "title": "Second item"})
    r.proposal.items = [r.item.model_dump(mode="json"), extra.model_dump(mode="json")]
    await r.service.decide(r.ctx, r.room, r.proposal.id, decision(r), accept=True)
    assert len(r.created) == 1
    assert r.proposal.selected_items == [str(r.item.item_id)]


@pytest.mark.parametrize("change", ["revision", "scope", "assignment", "linked"])
async def test_existing_tasks_must_match_the_exact_reviewed_revision_and_assignment(
    routing, change, monkeypatch
):
    r = routing
    task = await r.service.tasks.create(
        r.ctx,
        module.TaskCreate(
            scope="project",
            scope_id=r.topic.project_id,
            title=r.item.title,
            description=r.item.expected_result,
            assignee_agent_id=r.pin.agent_id,
            assignee_user_id=r.requester,
            next_actor="user",
        ),
    )
    existing = r.item.model_copy(
        update={"existing_task_id": task.id, "expected_task_revision": task.revision}
    )
    if change == "revision":
        task.revision += 1
    elif change == "scope":
        task.scope_id = uuid4()
    elif change == "assignment":
        task.assignee_agent_id = uuid4()
    elif change == "linked":
        monkeypatch.setattr(module.repo, "for_task", AsyncMock(return_value=object()))
    with pytest.raises((AlreadyExistsError, NotFoundError)):
        await r.service.validate_item(r.ctx, r.room, existing)
    r.service.tasks.update.assert_not_awaited()


@pytest.mark.parametrize("change", ["member", "room", "thread", "agent", "actor"])
async def test_execution_rechecks_current_requester_room_thread_and_pinned_agent(routing, change):
    r = routing
    link = SimpleNamespace(
        requester_id=r.requester,
        conversation_id=r.room,
        thread_root_message_id=r.source.thread_root_message_id,
        agent_id=r.pin.agent_id,
        version_id=r.pin.version_id,
    )
    ctx = r.ctx
    if change == "member":
        r.allowed.remove(r.requester)
    elif change == "room":
        link.conversation_id = uuid4()
    elif change == "thread":
        module.conversation_repo.get_thread_root.return_value = None
    elif change == "agent":
        r.registry.get_pinned_spec.side_effect = NotFoundError(
            message="Version unavailable", details={}
        )
    elif change == "actor":
        ctx = r.other
    with pytest.raises((AuthorizationError, NotFoundError)):
        await r.service.execution_authority(ctx, link)


async def test_disabling_policy_does_not_cancel_already_accepted_work(routing):
    r = routing
    await r.service.decide(r.ctx, r.room, r.proposal.id, decision(r), accept=True)
    r.policy.configuration = {**r.policy.configuration, "enabled": False}
    await r.service.execution_authority(r.ctx, r.writes[0])
    assert len(r.created) == 1


async def test_hidden_agent_metadata_is_filtered_from_policy_and_proposals(routing):
    r = routing
    r.registry.get_runnable_spec.side_effect = NotFoundError(
        message="Agent unavailable", details={}
    )
    result = await r.service.read(r.ctx, r.room, r.source.thread_root_message_id)
    assert result.policy.lead is None and not result.policy.enabled
    assert result.policy.specialists == [] and result.proposals == []


async def test_revoked_task_authority_hides_acceptance_but_allows_discard(routing, monkeypatch):
    r = routing
    monkeypatch.setattr(module, "board_allows", AsyncMock(return_value=False))
    view = await r.service.read_one(r.ctx, r.room, r.proposal.id)
    assert view.can_decide and not view.can_accept
    result = await r.service.decide(
        r.ctx,
        r.room,
        r.proposal.id,
        RoutingDecision(expected_revision=1, idempotency_key="discard-revoked"),
        accept=False,
    )
    assert result.status == "discarded" and not r.created
