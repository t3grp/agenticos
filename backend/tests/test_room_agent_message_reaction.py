"""Agent reactions keep server-bound identities and narrow transcript suppression."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import AuthorizationError, ConflictError
from app.repositories import room_agent_message_reaction as reaction_repo
from app.repositories import room_message_reaction as aggregate_repo
from app.services import room_agent_message_reaction as reaction_service_module
from app.services.room_agent_message_reaction import (
    RoomAgentReactionService,
    should_suppress_empty_reaction_reply,
)
from app.services.room_auto_reaction import RoomAutoReaction


@pytest.mark.anyio
async def test_each_primary_responder_persists_its_own_trusted_identity(monkeypatch) -> None:
    organization_id, conversation_id, requester_id, accepted_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    first_agent_id, second_agent_id = uuid4(), uuid4()
    first_version_id, second_version_id = uuid4(), uuid4()
    first_submission_id, second_submission_id = uuid4(), uuid4()
    first_run_id, second_run_id = uuid4(), uuid4()
    first_context = reaction_service_module.RoomReactionRunContext(
        organization_id=organization_id,
        conversation_id=conversation_id,
        submission_id=first_submission_id,
        requester_id=requester_id,
        accepted_message_id=accepted_id,
        agent_id=first_agent_id,
        agent_version_id=first_version_id,
        thread_root_message_id=None,
    )
    second_context = reaction_service_module.RoomReactionRunContext(
        organization_id=organization_id,
        conversation_id=conversation_id,
        submission_id=second_submission_id,
        requester_id=requester_id,
        accepted_message_id=accepted_id,
        agent_id=second_agent_id,
        agent_version_id=second_version_id,
        thread_root_message_id=None,
    )
    accepted = SimpleNamespace(
        id=accepted_id,
        role="user",
        author_user_id=requester_id,
        agent_id=None,
        thread_root_message_id=None,
    )
    submissions = [
        SimpleNamespace(
            id=first_submission_id,
            conversation_id=conversation_id,
            requester_id=requester_id,
            message_id=accepted_id,
            thread_root_message_id=None,
            agent_id=first_agent_id,
            version_id=first_version_id,
            status="running",
            intent="message",
            run_id=first_run_id,
        ),
        SimpleNamespace(
            id=second_submission_id,
            conversation_id=conversation_id,
            requester_id=requester_id,
            message_id=accepted_id,
            thread_root_message_id=None,
            agent_id=second_agent_id,
            version_id=second_version_id,
            status="running",
            intent="message",
            run_id=second_run_id,
        ),
    ]
    runs = [
        SimpleNamespace(
            status="running",
            surface="web",
            conversation_id=conversation_id,
            user_id=requester_id,
            agent_id=first_agent_id,
            agent_version_id=first_version_id,
            parent_run_id=None,
        ),
        SimpleNamespace(
            status="running",
            surface="web",
            conversation_id=conversation_id,
            user_id=requester_id,
            agent_id=second_agent_id,
            agent_version_id=second_version_id,
            parent_run_id=None,
        ),
    ]
    db = SimpleNamespace(scalar=AsyncMock(side_effect=runs))
    saved = []

    async def save(_db, row):
        saved.append(row)

    monkeypatch.setattr(reaction_service_module, "hold_subject", AsyncMock())
    monkeypatch.setattr(
        reaction_service_module.room_repo, "get", AsyncMock(side_effect=submissions)
    )
    monkeypatch.setattr(
        reaction_service_module.RoomSubmissionService,
        "authority",
        AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace(), accepted)),
    )
    target_context_read = AsyncMock(return_value=None)
    monkeypatch.setattr(
        reaction_service_module.RoomThreadContextReadService,
        "assert_history",
        target_context_read,
    )
    monkeypatch.setattr(
        reaction_service_module.reaction_repo,
        "get_room_message",
        AsyncMock(return_value=accepted),
    )
    monkeypatch.setattr(reaction_repo, "by_tool_call", AsyncMock(return_value=None))
    monkeypatch.setattr(reaction_repo, "add", AsyncMock(side_effect=save))
    monkeypatch.setattr(
        reaction_service_module.AgentRegistryService,
        "get_pinned_spec",
        AsyncMock(
            side_effect=[
                (None, SimpleNamespace(name="Ada"), None),
                (None, SimpleNamespace(name="Theo"), None),
            ]
        ),
    )
    monkeypatch.setattr(
        reaction_service_module.RoomAgentReactionService,
        "_check_emoji_limit",
        AsyncMock(),
    )
    monkeypatch.setattr(
        reaction_service_module, "spawn_after_commit", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(reaction_service_module.chat_room, "room_reaction", lambda *_args: None)

    service = RoomAgentReactionService(db)
    await service.record_tool(
        context=first_context,
        run_id=first_run_id,
        message_id=accepted_id,
        emoji="👍",
        tool_call_id="first-call",
        reaction_only=False,
    )
    await service.record_tool(
        context=second_context,
        run_id=second_run_id,
        message_id=accepted_id,
        emoji="👍",
        tool_call_id="second-call",
        reaction_only=False,
    )

    assert [(row.agent_id, row.agent_version_id, row.run_id) for row in saved] == [
        (first_agent_id, first_version_id, first_run_id),
        (second_agent_id, second_version_id, second_run_id),
    ]
    assert [row.agent_name for row in saved] == ["Ada", "Theo"]
    assert [call.kwargs["responder_agent_id"] for call in target_context_read.await_args_list] == [
        first_agent_id,
        second_agent_id,
    ]


@pytest.mark.anyio
async def test_automatic_reaction_binds_decision_run_and_frozen_candidate(monkeypatch) -> None:
    organization_id, conversation_id, requester_id, submission_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    accepted_message_id = uuid4()
    decision_id, claim_token, router_run_id = uuid4(), uuid4(), uuid4()
    router_agent_id, router_version_id = uuid4(), uuid4()
    actor_agent_id, actor_version_id = uuid4(), uuid4()
    decision = SimpleNamespace(
        id=decision_id,
        organization_id=organization_id,
        conversation_id=conversation_id,
        submission_id=submission_id,
        accepted_message_id=accepted_message_id,
        requester_id=requester_id,
        thread_root_message_id=None,
        status="running",
        claim_token=claim_token,
        claim_generation=4,
        router_run_id=router_run_id,
        router_agent_id=router_agent_id,
        router_version_id=router_version_id,
        policy_revision=7,
        candidate_snapshot=[
            {
                "agent_id": str(actor_agent_id),
                "version_id": str(actor_version_id),
                "name": "Ada frozen",
            }
        ],
    )
    router_run = SimpleNamespace(
        status="completed",
        surface="web",
        conversation_id=None,
        agent_id=router_agent_id,
        agent_version_id=router_version_id,
        user_id=requester_id,
        parent_run_id=None,
    )
    root_submission = SimpleNamespace(
        id=submission_id,
        parent_submission_id=None,
        status="posted",
        message_id=accepted_message_id,
        requester_id=requester_id,
        conversation_id=conversation_id,
    )
    accepted_message = SimpleNamespace(
        id=accepted_message_id,
        role="user",
        author_user_id=requester_id,
        thread_root_message_id=None,
        agent_id=None,
    )
    requester = SimpleNamespace()
    room = SimpleNamespace(id=conversation_id, organization_id=organization_id)
    policy = SimpleNamespace(
        enabled=True,
        revision=7,
        router_agent_id=router_agent_id,
        router_version_id=router_version_id,
    )
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[decision, router_run]))
    saved = []

    async def save(_db, row):
        saved.append(row)

    monkeypatch.setattr(reaction_service_module, "hold_subject", AsyncMock())
    monkeypatch.setattr(
        reaction_service_module.room_repo, "get", AsyncMock(return_value=root_submission)
    )
    monkeypatch.setattr(
        reaction_service_module,
        "acting_member",
        AsyncMock(return_value=requester),
    )
    monkeypatch.setattr(
        reaction_service_module.RoomSubmissionService,
        "room",
        AsyncMock(return_value=room),
    )
    monkeypatch.setattr(
        reaction_service_module.RoomThreadContextAccessService,
        "assert_responder_visible",
        AsyncMock(return_value=None),
    )
    target_context_read = AsyncMock(return_value=None)
    monkeypatch.setattr(
        reaction_service_module.RoomThreadContextReadService,
        "assert_history",
        target_context_read,
    )
    monkeypatch.setattr(
        reaction_service_module.RoomParticipantService,
        "validate_mentions",
        AsyncMock(return_value=[SimpleNamespace(auto_reply=True)]),
    )
    monkeypatch.setattr(
        reaction_service_module.auto_repo,
        "policy",
        AsyncMock(return_value=policy),
    )
    monkeypatch.setattr(
        reaction_service_module.AgentRegistryService,
        "get_runnable_spec",
        AsyncMock(return_value=(None, SimpleNamespace(name="Ada current"), uuid4())),
    )
    monkeypatch.setattr(
        reaction_service_module.AgentRegistryService,
        "get_pinned_spec",
        AsyncMock(return_value=(None, SimpleNamespace(name="Ada frozen"), actor_version_id)),
    )
    monkeypatch.setattr(reaction_service_module, "cloud_agent", lambda _spec: None)
    monkeypatch.setattr(
        reaction_service_module.reaction_repo,
        "get_room_message",
        AsyncMock(return_value=accepted_message),
    )
    monkeypatch.setattr(
        reaction_service_module.agent_reaction_repo,
        "by_decision",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        reaction_service_module.agent_reaction_repo, "add", AsyncMock(side_effect=save)
    )
    monkeypatch.setattr(
        reaction_service_module.RoomAgentReactionService,
        "_check_emoji_limit",
        AsyncMock(),
    )
    monkeypatch.setattr(reaction_service_module, "spawn_after_commit", lambda *_a, **_k: None)
    monkeypatch.setattr(reaction_service_module.chat_room, "room_reaction", lambda *_a: None)

    row = await RoomAgentReactionService(db).record_automatic(
        organization_id=organization_id,
        conversation_id=conversation_id,
        decision_id=decision_id,
        claim_token=claim_token,
        claim_generation=4,
        router_run_id=router_run_id,
        requester_id=requester_id,
        thread_root_message_id=None,
        accepted_message_id=accepted_message_id,
        social_action=RoomAutoReaction(
            message_id=accepted_message_id,
            agent_id=actor_agent_id,
            reaction="👍",
        ),
        selected_agent_ids=[],
    )

    assert row in saved
    assert (row.agent_id, row.agent_version_id, row.agent_name) == (
        actor_agent_id,
        actor_version_id,
        "Ada frozen",
    )
    assert (row.run_id, row.decision_id, row.source) == (
        router_run_id,
        decision_id,
        "automatic",
    )
    target_context_read.assert_awaited_once_with(
        destination=room,
        root_message_id=accepted_message_id,
        actor=requester,
        responder_agent_id=actor_agent_id,
    )


@pytest.mark.anyio
async def test_automatic_reaction_rejects_stale_claim_generation(monkeypatch) -> None:
    decision = SimpleNamespace(
        status="running",
        claim_token=uuid4(),
        claim_generation=5,
        router_run_id=uuid4(),
        requester_id=uuid4(),
        thread_root_message_id=None,
        accepted_message_id=uuid4(),
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=decision))
    monkeypatch.setattr(reaction_service_module, "hold_subject", AsyncMock())

    with pytest.raises(ConflictError):
        await RoomAgentReactionService(db).record_automatic(
            organization_id=uuid4(),
            conversation_id=uuid4(),
            decision_id=uuid4(),
            claim_token=decision.claim_token,
            claim_generation=4,
            router_run_id=decision.router_run_id,
            requester_id=decision.requester_id,
            thread_root_message_id=None,
            accepted_message_id=decision.accepted_message_id,
            social_action=RoomAutoReaction(
                message_id=decision.accepted_message_id,
                agent_id=uuid4(),
                reaction="👍",
            ),
            selected_agent_ids=[],
        )


def test_reaction_only_suppression_needs_one_successful_empty_completion() -> None:
    successful = {"reaction-call"}
    assert should_suppress_empty_reaction_reply(
        run_status="completed",
        output="",
        called_tool_call_ids=["reaction-call"],
        successful_reaction_only_tool_call_ids=successful,
    )
    assert not should_suppress_empty_reaction_reply(
        run_status="cancelled",
        output="",
        called_tool_call_ids=["reaction-call"],
        successful_reaction_only_tool_call_ids=successful,
    )
    assert not should_suppress_empty_reaction_reply(
        run_status="failed",
        output="",
        called_tool_call_ids=["reaction-call"],
        successful_reaction_only_tool_call_ids=successful,
    )
    assert not should_suppress_empty_reaction_reply(
        run_status="completed",
        output="I reacted and here is the answer.",
        called_tool_call_ids=["reaction-call"],
        successful_reaction_only_tool_call_ids=successful,
    )
    # A normal reaction is deliberately absent from the successful reaction-only set.
    assert not should_suppress_empty_reaction_reply(
        run_status="completed",
        output="",
        called_tool_call_ids=["normal-reaction-call"],
        successful_reaction_only_tool_call_ids=successful,
    )
    # Mixed work retains the useful completion even if one tool asked for reaction-only.
    assert not should_suppress_empty_reaction_reply(
        run_status="completed",
        output="",
        called_tool_call_ids=["reaction-call", "other-call"],
        successful_reaction_only_tool_call_ids=successful,
    )


@pytest.mark.anyio
async def test_aggregate_counts_distinct_agents_and_keeps_human_reaction_state(monkeypatch) -> None:
    from app.schemas.room_reaction import RoomMessageReactionRead

    message_id, user_id, first_agent, second_agent = uuid4(), uuid4(), uuid4(), uuid4()
    first = datetime.now(UTC)
    human_row = SimpleNamespace(
        message_id=message_id,
        emoji="🎉",
        count=1,
        reacted_by_me=True,
        first_created_at=first,
    )
    agent_rows = [
        SimpleNamespace(
            message_id=message_id,
            emoji="🎉",
            agent_id=first_agent,
            agent_name="Ada",
            created_at=first,
        ),
        SimpleNamespace(
            message_id=message_id,
            emoji="🎉",
            agent_id=first_agent,
            agent_name="Ada (old snapshot)",
            created_at=first,
        ),
        SimpleNamespace(
            message_id=message_id,
            emoji="🎉",
            agent_id=second_agent,
            agent_name="Theo",
            created_at=first,
        ),
    ]
    db = SimpleNamespace(execute=AsyncMock(side_effect=[[human_row], agent_rows]))

    aggregates = await aggregate_repo.list_aggregates(
        db,
        organization_id=uuid4(),
        conversation_id=uuid4(),
        message_ids=[message_id],
        user_id=user_id,
    )

    result = aggregates[message_id][0]
    assert isinstance(result, RoomMessageReactionRead)
    assert result.count == 3  # one human plus two distinct agent actors
    assert result.reacted_by_me
    assert [(item.agent_id, item.name) for item in result.agents] == [
        (first_agent, "Ada"),
        (second_agent, "Theo"),
    ]


@pytest.mark.anyio
async def test_run_audit_projection_is_scoped_bounded_and_safe(monkeypatch) -> None:
    organization_id, run_id, conversation_id, message_id = uuid4(), uuid4(), uuid4(), uuid4()
    ctx = SimpleNamespace(organization_id=organization_id)
    created_at = datetime.now(UTC)
    rows = [
        SimpleNamespace(
            conversation_id=conversation_id,
            message_id=message_id,
            agent_id=uuid4(),
            agent_name=f"Agent {index}",
            agent_version_id=uuid4(),
            emoji="👍",
            reaction_only=index % 2 == 0,
            source="tool" if index % 2 == 0 else "automatic",
            tool_call_id=f"call-{index}" if index % 2 == 0 else None,
            decision_id=None if index % 2 == 0 else uuid4(),
            created_at=created_at,
        )
        for index in range(101)
    ]
    repository_read = AsyncMock(return_value=rows)
    monkeypatch.setattr(
        reaction_service_module.agent_reaction_repo,
        "list_for_run",
        repository_read,
    )
    room = SimpleNamespace(id=conversation_id)
    room_read = AsyncMock(return_value=room)
    monkeypatch.setattr(reaction_service_module.RoomSubmissionService, "room", room_read)
    target_message = SimpleNamespace(id=message_id, thread_root_message_id=None)
    target_read = AsyncMock(return_value=target_message)
    monkeypatch.setattr(reaction_service_module.reaction_repo, "get_room_message", target_read)
    history_read = AsyncMock(return_value=None)
    monkeypatch.setattr(
        reaction_service_module.RoomThreadContextReadService,
        "assert_history",
        history_read,
    )

    projected, truncated = await RoomAgentReactionService(object()).list_for_run(
        ctx,
        organization_id=organization_id,
        run_id=run_id,
    )

    assert repository_read.await_args.kwargs == {
        "organization_id": organization_id,
        "run_id": run_id,
    }
    assert len(projected) == 100
    assert truncated
    assert projected[0].agent_name == "Agent 0"
    assert projected[1].source == "automatic"
    room_read.assert_awaited_once_with(ctx, conversation_id)
    assert target_read.await_count == 101
    history_read.assert_awaited_once_with(
        destination=room,
        root_message_id=message_id,
        actor=ctx,
    )
    assert set(projected[0].model_dump()) == {
        "message_id",
        "agent_id",
        "agent_name",
        "agent_version_id",
        "emoji",
        "reaction_only",
        "source",
        "tool_call_id",
        "decision_id",
        "created_at",
    }


@pytest.mark.anyio
async def test_run_audit_projection_rechecks_current_room_access(monkeypatch) -> None:
    organization_id, run_id, conversation_id = uuid4(), uuid4(), uuid4()
    ctx = SimpleNamespace(organization_id=organization_id)
    repository_read = AsyncMock(return_value=[SimpleNamespace(conversation_id=conversation_id)])
    monkeypatch.setattr(
        reaction_service_module.agent_reaction_repo,
        "list_for_run",
        repository_read,
    )
    room_read = AsyncMock(side_effect=AuthorizationError(message="Room access was removed"))
    monkeypatch.setattr(reaction_service_module.RoomSubmissionService, "room", room_read)

    with pytest.raises(AuthorizationError, match="Room access was removed"):
        await RoomAgentReactionService(object()).list_for_run(
            ctx,
            organization_id=organization_id,
            run_id=run_id,
        )
