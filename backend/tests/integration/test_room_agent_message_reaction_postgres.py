"""PostgreSQL coverage for agent reaction persistence, aggregates, and audit reads."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.background import discard_deferred
from app.core.exceptions import AuthorizationError, BadRequestError, ConflictError, NotFoundError
from app.core.permissions import AuthContext
from app.db.models.conversation import Message, ToolCall
from app.db.models.organization import OrganizationMember
from app.db.models.room_agent_message_reaction import RoomAgentMessageReaction
from app.db.models.room_auto_routing import RoomAutoRoutingDecision, RoomAutoRoutingPolicy
from app.db.models.room_message_reaction import RoomMessageReaction
from app.db.models.room_participant import RoomParticipant
from app.db.models.room_submission import RoomSubmission
from app.db.models.room_thread import RoomThread
from app.db.models.user import User
from app.repositories import agent_run as run_repo
from app.repositories import room_message_reaction as reaction_repo
from app.schemas.room_participant import RoomCreate, RoomParticipantRef
from app.services.room_agent_message_reaction import RoomAgentReactionService
from app.services.room_auto_reaction import RoomAutoReaction
from app.services.room_participant import RoomParticipantService
from app.services.room_submission import RoomSubmissionService
from tests.integration.test_room_submissions import Room, post, seed

pytestmark = [pytest.mark.anyio, pytest.mark.security]


async def _private_room(db: AsyncSession) -> Room:
    room = await seed(db)
    room.conversation = await RoomParticipantService(db).create_room(
        room.ctx,
        RoomCreate(
            title="Agent reaction room",
            visibility="private",
            participants=[RoomParticipantRef(kind="agent", id=room.agent.id)],
        ),
    )
    return room


async def _root_submission(db: AsyncSession, message_id) -> RoomSubmission:
    row = await db.scalar(
        select(RoomSubmission).where(
            RoomSubmission.message_id == message_id,
            RoomSubmission.parent_submission_id.is_(None),
        )
    )
    assert row is not None
    return row


async def _running_responder(db: AsyncSession, token: str):
    room = await _private_room(db)
    posted = await post(db, room, token)
    assert posted.request is not None
    submission = await db.scalar(
        select(RoomSubmission).where(RoomSubmission.id == posted.request.id)
    )
    assert submission is not None
    run = await run_repo.create_run(
        db,
        organization_id=room.ctx.organization_id,
        agent_id=room.agent.id,
        agent_version_id=room.version.id,
        user_id=room.user.id,
        conversation_id=room.conversation.id,
        surface="web",
        model_label="test",
        provider="test",
        started_at=datetime.now(UTC),
    )
    submission.status = "running"
    submission.run_id = run.id
    await db.flush()
    from app.agents.capabilities.room_reactions._contract import RoomReactionRunContext

    context = RoomReactionRunContext(
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        submission_id=submission.id,
        requester_id=room.user.id,
        accepted_message_id=posted.message.id,
        agent_id=room.agent.id,
        agent_version_id=room.version.id,
        thread_root_message_id=None,
    )
    return room, posted, submission, run, context


def _tool_reaction(
    *,
    organization_id,
    conversation_id,
    message_id,
    submission_id,
    agent_id,
    agent_name: str,
    version_id,
    run_id,
    tool_call_id: str,
    emoji: str,
    created_at: datetime | None = None,
) -> RoomAgentMessageReaction:
    return RoomAgentMessageReaction(
        id=uuid4(),
        organization_id=organization_id,
        conversation_id=conversation_id,
        message_id=message_id,
        agent_id=agent_id,
        agent_name=agent_name,
        agent_version_id=version_id,
        run_id=run_id,
        source="tool",
        submission_id=submission_id,
        decision_id=None,
        tool_call_id=tool_call_id,
        reaction_only=False,
        emoji=emoji,
        created_at=created_at or datetime.now(UTC),
    )


async def test_tool_reaction_persists_once_per_run_and_tool_call(db: AsyncSession) -> None:
    room, posted, submission, run, context = await _running_responder(db, "tool-reaction")
    service = RoomAgentReactionService(db)

    saved = await service.record_tool(
        context=context,
        run_id=run.id,
        message_id=posted.message.id,
        emoji="👍",
        tool_call_id="call-1",
        reaction_only=True,
    )
    retried = await service.record_tool(
        context=context,
        run_id=run.id,
        message_id=posted.message.id,
        emoji="👍",
        tool_call_id="call-1",
        reaction_only=True,
    )

    assert retried.id == saved.id
    assert saved.source == "tool" and saved.reaction_only
    assert saved.submission_id == submission.id and saved.run_id == run.id
    assert saved.tool_call_id == "call-1" and saved.emoji == "👍"
    assert (
        await db.scalar(
            select(func.count())
            .select_from(RoomAgentMessageReaction)
            .where(
                RoomAgentMessageReaction.organization_id == room.ctx.organization_id,
                RoomAgentMessageReaction.conversation_id == room.conversation.id,
                RoomAgentMessageReaction.run_id == run.id,
                RoomAgentMessageReaction.tool_call_id == "call-1",
            )
        )
        == 1
    )

    with pytest.raises(ConflictError, match="another result"):
        await service.record_tool(
            context=context,
            run_id=run.id,
            message_id=posted.message.id,
            emoji="🎉",
            tool_call_id="call-1",
            reaction_only=True,
        )
    discard_deferred(db)


@pytest.mark.parametrize("parked_status", ["awaiting_approval", "awaiting_answer"])
async def test_resumed_submission_reactivates_before_successful_tool_reaction(
    db: AsyncSession,
    parked_status: str,
) -> None:
    room, posted, submission, run, context = await _running_responder(
        db,
        f"resumed-tool-reaction-{parked_status}",
    )
    submission.status = parked_status
    run.status = "running"
    await db.flush()

    await RoomSubmissionService(db).mark_resumed_running(
        run.id,
        room.ctx.organization_id,
    )
    assert submission.status == "running"

    # The runner has already changed the run row in this same transaction,
    # before its owned callback reactivates the room and replays the tool.
    saved = await RoomAgentReactionService(db).record_tool(
        context=context,
        run_id=run.id,
        message_id=posted.message.id,
        emoji="👍",
        tool_call_id="resume-call-1",
        reaction_only=True,
    )

    assert saved.reaction_only and saved.submission_id == submission.id
    assert saved.run_id == run.id and saved.message_id == posted.message.id
    discard_deferred(db)


async def test_automatic_reaction_is_idempotent_by_decision(db: AsyncSession) -> None:
    room = await _private_room(db)
    posted = await post(db, room, "automatic-reaction", agent=False)
    root = await _root_submission(db, posted.message.id)
    participant = await db.scalar(
        select(RoomParticipant).where(
            RoomParticipant.organization_id == room.ctx.organization_id,
            RoomParticipant.conversation_id == room.conversation.id,
            RoomParticipant.agent_id == room.agent.id,
        )
    )
    assert participant is not None
    participant.auto_reply = True

    profile_id = uuid4()
    policy = RoomAutoRoutingPolicy(
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        enabled=True,
        revision=1,
        router_agent_id=room.agent.id,
        router_version_id=room.version.id,
        model_profile_id=profile_id,
        quiet_window_ms=2500,
        max_responders=1,
    )
    router_run = await run_repo.create_run(
        db,
        organization_id=room.ctx.organization_id,
        agent_id=room.agent.id,
        agent_version_id=room.version.id,
        user_id=room.user.id,
        conversation_id=None,
        surface="web",
        model_label="test-router",
        provider="test",
        started_at=datetime.now(UTC),
    )
    router_run.status = "completed"
    claim_token = uuid4()
    decision = RoomAutoRoutingDecision(
        id=uuid4(),
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        submission_id=root.id,
        accepted_message_id=posted.message.id,
        requester_id=room.user.id,
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        ready_at=datetime.now(UTC),
        policy_revision=1,
        router_agent_id=room.agent.id,
        router_name=room.agent.name,
        router_version_id=room.version.id,
        model_profile_id=profile_id,
        model_profile_fingerprint="a" * 64,
        quiet_window_ms=2500,
        max_responders=1,
        candidate_snapshot=[
            {
                "agent_id": str(room.agent.id),
                "version_id": str(room.version.id),
                "name": room.agent.name,
            }
        ],
        grouped_message_ids=[str(posted.message.id)],
        status="running",
        claim_token=claim_token,
        claim_generation=1,
        router_run_id=router_run.id,
    )
    db.add_all([policy, decision])
    await db.flush()

    service = RoomAgentReactionService(db)
    kwargs = {
        "organization_id": room.ctx.organization_id,
        "conversation_id": room.conversation.id,
        "decision_id": decision.id,
        "claim_token": claim_token,
        "claim_generation": 1,
        "router_run_id": router_run.id,
        "requester_id": room.user.id,
        "thread_root_message_id": None,
        "accepted_message_id": posted.message.id,
        "social_action": RoomAutoReaction(
            message_id=posted.message.id,
            agent_id=room.agent.id,
            reaction="👍",
        ),
        "selected_agent_ids": (),
    }
    saved = await service.record_automatic(**kwargs)
    retried = await service.record_automatic(**kwargs)

    assert retried.id == saved.id
    assert saved.source == "automatic" and not saved.reaction_only
    assert saved.decision_id == decision.id and saved.run_id == router_run.id
    assert saved.submission_id is None and saved.tool_call_id is None
    assert saved.agent_id == room.agent.id and saved.agent_name == room.agent.name

    conflicting = {
        **kwargs,
        "social_action": RoomAutoReaction(
            message_id=posted.message.id,
            agent_id=room.agent.id,
            reaction="🎉",
        ),
    }
    with pytest.raises(ConflictError, match="another social action"):
        await service.record_automatic(**conflicting)
    assert (
        await db.scalar(
            select(func.count())
            .select_from(RoomAgentMessageReaction)
            .where(RoomAgentMessageReaction.decision_id == decision.id)
        )
        == 1
    )
    discard_deferred(db)


async def test_combined_human_and_agent_aggregates_deduplicate_agents_and_enforce_twenty(
    db: AsyncSession,
) -> None:
    room, posted, submission, run, context = await _running_responder(db, "combined-aggregates")
    message_id = posted.message.id
    emojis = [chr(0x1F600 + index) for index in range(20)]
    human_rows = [
        RoomMessageReaction(
            organization_id=room.ctx.organization_id,
            conversation_id=room.conversation.id,
            message_id=message_id,
            user_id=room.user.id,
            emoji=emoji,
        )
        for emoji in emojis[:19]
    ]
    agent_a, agent_b = room.agent.id, uuid4()
    agent_rows = [
        _tool_reaction(
            organization_id=room.ctx.organization_id,
            conversation_id=room.conversation.id,
            message_id=message_id,
            submission_id=submission.id,
            agent_id=agent_a,
            agent_name="Responder",
            version_id=room.version.id,
            run_id=uuid4(),
            tool_call_id="aggregate-a-1",
            emoji=emojis[0],
        ),
        _tool_reaction(
            organization_id=room.ctx.organization_id,
            conversation_id=room.conversation.id,
            message_id=message_id,
            submission_id=submission.id,
            agent_id=agent_a,
            agent_name="Responder",
            version_id=room.version.id,
            run_id=uuid4(),
            tool_call_id="aggregate-a-2",
            emoji=emojis[0],
        ),
        _tool_reaction(
            organization_id=room.ctx.organization_id,
            conversation_id=room.conversation.id,
            message_id=message_id,
            submission_id=submission.id,
            agent_id=agent_b,
            agent_name="Second agent",
            version_id=room.version.id,
            run_id=uuid4(),
            tool_call_id="aggregate-b-1",
            emoji=emojis[0],
        ),
        _tool_reaction(
            organization_id=room.ctx.organization_id,
            conversation_id=room.conversation.id,
            message_id=message_id,
            submission_id=submission.id,
            agent_id=agent_a,
            agent_name="Responder",
            version_id=room.version.id,
            run_id=uuid4(),
            tool_call_id="aggregate-cap",
            emoji=emojis[19],
        ),
    ]
    db.add_all([*human_rows, *agent_rows])
    await db.flush()

    aggregates = await reaction_repo.list_aggregates(
        db,
        organization_id=room.ctx.organization_id,
        conversation_id=room.conversation.id,
        message_ids=[message_id],
        user_id=room.user.id,
    )
    by_emoji = {item.emoji: item for item in aggregates[message_id]}
    social = by_emoji[emojis[0]]
    assert social.count == 3
    assert social.reacted_by_me
    assert {agent.agent_id for agent in social.agents} == {agent_a, agent_b}
    assert {agent.name for agent in social.agents} == {"Responder", "Second agent"}
    assert (
        await reaction_repo.distinct_emoji_count(
            db,
            organization_id=room.ctx.organization_id,
            conversation_id=room.conversation.id,
            message_id=message_id,
        )
        == 20
    )

    with pytest.raises(BadRequestError, match="maximum number of reactions"):
        await RoomAgentReactionService(db).record_tool(
            context=context,
            run_id=run.id,
            message_id=message_id,
            emoji="🧠",
            tool_call_id="twenty-first-emoji",
            reaction_only=False,
        )
    discard_deferred(db)


async def test_run_audit_is_tenant_scoped_ordered_and_capped_with_lookahead(
    db: AsyncSession,
) -> None:
    room = await _private_room(db)
    posted = await post(db, room, "audit-first", agent=False)
    submission = await _root_submission(db, posted.message.id)
    router_run = await run_repo.create_run(
        db,
        organization_id=room.ctx.organization_id,
        agent_id=room.agent.id,
        agent_version_id=room.version.id,
        user_id=room.user.id,
        conversation_id=None,
        surface="web",
        model_label="test-router",
        provider="test",
        started_at=datetime.now(UTC),
    )
    router_run.status = "completed"
    first_at = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        _tool_reaction(
            organization_id=room.ctx.organization_id,
            conversation_id=room.conversation.id,
            message_id=posted.message.id,
            submission_id=submission.id,
            agent_id=room.agent.id,
            agent_name=room.agent.name,
            version_id=room.version.id,
            run_id=router_run.id,
            tool_call_id=f"audit-{index:03d}",
            emoji="👍",
            created_at=first_at + timedelta(seconds=index // 2),
        )
        for index in range(101)
    ]
    expected_page = [
        row.tool_call_id for row in sorted(rows, key=lambda item: (item.created_at, item.id))[:100]
    ]

    other = await seed(db)
    other_post = await post(db, other, "same-run-other-tenant", agent=False)
    other_submission = await _root_submission(db, other_post.message.id)
    rows.append(
        _tool_reaction(
            organization_id=other.ctx.organization_id,
            conversation_id=other.conversation.id,
            message_id=other_post.message.id,
            submission_id=other_submission.id,
            agent_id=other.agent.id,
            agent_name=other.agent.name,
            version_id=other.version.id,
            run_id=router_run.id,
            tool_call_id="audit-other-tenant",
            emoji="🎉",
            created_at=first_at,
        )
    )
    db.add_all(rows)
    await db.flush()

    page, truncated = await RoomAgentReactionService(db).list_for_run(
        room.ctx,
        organization_id=room.ctx.organization_id,
        run_id=router_run.id,
    )

    assert len(page) == 100 and truncated
    assert [item.tool_call_id for item in page] == expected_page
    assert [item.created_at for item in page] == sorted(item.created_at for item in page)
    assert all(item.message_id == posted.message.id for item in page)
    assert all(item.message_id != other_post.message.id for item in page)
    discard_deferred(db)


async def test_conversationless_run_read_refuses_private_room_and_inherited_source_access(
    db: AsyncSession,
) -> None:
    room = await _private_room(db)
    destination = room.conversation
    visible = await post(db, room, "audit-visible", agent=False)
    visible_submission = await _root_submission(db, visible.message.id)

    viewer = User(id=uuid4(), email=f"{uuid4()}@example.com", hashed_password="x", is_active=True)
    db.add(viewer)
    await db.flush()
    db.add(
        OrganizationMember(
            id=uuid4(),
            organization_id=room.ctx.organization_id,
            user_id=viewer.id,
            role="operator",
        )
    )
    await db.flush()
    denied_ctx = AuthContext(
        user_id=viewer.id,
        organization_id=room.ctx.organization_id,
        role="operator",
    )
    router_run = await run_repo.create_run(
        db,
        organization_id=room.ctx.organization_id,
        agent_id=room.agent.id,
        agent_version_id=room.version.id,
        user_id=room.user.id,
        conversation_id=None,
        surface="web",
        model_label="test-router",
        provider="test",
        started_at=datetime.now(UTC),
    )
    router_run.status = "completed"
    db.add(
        _tool_reaction(
            organization_id=room.ctx.organization_id,
            conversation_id=destination.id,
            message_id=visible.message.id,
            submission_id=visible_submission.id,
            agent_id=room.agent.id,
            agent_name=room.agent.name,
            version_id=room.version.id,
            run_id=router_run.id,
            tool_call_id="private-room-audit",
            emoji="👍",
        )
    )
    await db.flush()

    # RUNS_VIEW is an organization audit permission; it does not grant access to
    # the private room named by a conversationless trusted router run.
    with pytest.raises((AuthorizationError, NotFoundError)):
        await RoomAgentReactionService(db).list_for_run(
            denied_ctx,
            organization_id=room.ctx.organization_id,
            run_id=router_run.id,
        )

    # Give the same reader direct access to the destination. A quoted private
    # source still has to be readable by every current destination reader.
    db.add(
        RoomParticipant(
            organization_id=room.ctx.organization_id,
            conversation_id=destination.id,
            user_id=viewer.id,
            agent_id=None,
            auto_reply=False,
        )
    )
    source = await RoomParticipantService(db).create_room(
        room.ctx,
        RoomCreate(
            title="Private context source",
            visibility="private",
            participants=[RoomParticipantRef(kind="agent", id=room.agent.id)],
        ),
    )
    room.conversation = source
    source_post = await post(db, room, "source-history", agent=False)
    room.conversation = destination
    hidden = await post(db, room, "audit-hidden-context", agent=False)
    hidden_submission = await _root_submission(db, hidden.message.id)
    db.add(
        RoomThread(
            organization_id=room.ctx.organization_id,
            conversation_id=destination.id,
            root_message_id=hidden.message.id,
            state="open",
            context_source_conversation_id=source.id,
            context_start_message_id=source_post.message.id,
            context_end_message_id=source_post.message.id,
            context_first_ordinal=source_post.message.ordinal,
            context_last_ordinal=source_post.message.ordinal,
            context_excluded_message_ids=[],
            context_message_ids=[source_post.message.id],
            context_moved=False,
            context_used=False,
            context_revision=1,
        )
    )
    db.add_all(
        [
            _tool_reaction(
                organization_id=room.ctx.organization_id,
                conversation_id=destination.id,
                message_id=visible.message.id,
                submission_id=visible_submission.id,
                agent_id=room.agent.id,
                agent_name=room.agent.name,
                version_id=room.version.id,
                run_id=router_run.id,
                tool_call_id=f"lookahead-{index:03d}",
                emoji="👍",
                created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
            )
            for index in range(100)
        ]
    )
    db.add(
        _tool_reaction(
            organization_id=room.ctx.organization_id,
            conversation_id=destination.id,
            message_id=hidden.message.id,
            submission_id=hidden_submission.id,
            agent_id=room.agent.id,
            agent_name=room.agent.name,
            version_id=room.version.id,
            run_id=router_run.id,
            tool_call_id="lookahead-100-hidden-source",
            emoji="🎉",
            created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=100),
        )
    )
    await db.flush()

    source_reader = RoomParticipant(
        organization_id=room.ctx.organization_id,
        conversation_id=source.id,
        user_id=viewer.id,
        agent_id=None,
        auto_reply=False,
    )
    db.add(source_reader)
    await db.flush()

    readable_page, readable_truncated = await RoomAgentReactionService(db).list_for_run(
        denied_ctx,
        organization_id=room.ctx.organization_id,
        run_id=router_run.id,
    )
    assert len(readable_page) == 100 and readable_truncated

    # Current thread-context authority is part of the destination room read. A
    # later source revocation hides the destination receipt page altogether.
    await db.delete(source_reader)
    await db.flush()

    # Current inherited-context authority is part of reading the destination
    # room. After source access is revoked, neither receipts nor truncation
    # metadata are exposed.
    with pytest.raises(NotFoundError):
        await RoomAgentReactionService(db).list_for_run(
            denied_ctx,
            organization_id=room.ctx.organization_id,
            run_id=router_run.id,
        )
    discard_deferred(db)


async def test_solo_reaction_suppression_is_run_wide_across_resumed_segments(
    db: AsyncSession,
) -> None:
    """A latest segment with one reaction is not solo if this run already spoke or called."""
    from app.services.agent_runner import _room_reaction_suppression_allowed

    room = await _private_room(db)
    run_ids: list[UUID] = []
    for previous_text, previous_tool in (("Earlier answer", False), ("", True)):
        run = await run_repo.create_run(
            db,
            organization_id=room.ctx.organization_id,
            agent_id=room.agent.id,
            agent_version_id=room.version.id,
            user_id=room.user.id,
            conversation_id=room.conversation.id,
            surface="web",
            model_label="test-responder",
            provider="test",
            started_at=datetime.now(UTC),
        )
        run.status = "completed"
        run_ids.append(run.id)
        assistant = Message(
            id=uuid4(),
            conversation_id=room.conversation.id,
            run_id=run.id,
            role="assistant",
            content=previous_text,
            agent_id=room.agent.id,
            agent_version_id=room.version.id,
        )
        db.add(assistant)
        await db.flush()
        if previous_tool:
            db.add(
                ToolCall(
                    id=uuid4(),
                    message_id=assistant.id,
                    tool_call_id=f"earlier-call-{run.id}",
                    tool_name="search",
                    args={},
                    status="completed",
                    started_at=datetime.now(UTC),
                )
            )
            await db.flush()

        assert not await _room_reaction_suppression_allowed(
            db,
            run_id=run.id,
            run_status="completed",
            output="",
            called_tool_call_ids=["current-reaction-call"],
            successful_reaction_only_tool_call_ids={"current-reaction-call"},
        )

    fresh_run = await run_repo.create_run(
        db,
        organization_id=room.ctx.organization_id,
        agent_id=room.agent.id,
        agent_version_id=room.version.id,
        user_id=room.user.id,
        conversation_id=room.conversation.id,
        surface="web",
        model_label="test-responder",
        provider="test",
        started_at=datetime.now(UTC),
    )
    assert fresh_run.id not in run_ids
    assert await _room_reaction_suppression_allowed(
        db,
        run_id=fresh_run.id,
        run_status="completed",
        output="",
        called_tool_call_ids=["current-reaction-call"],
        successful_reaction_only_tool_call_ids={"current-reaction-call"},
    )
    discard_deferred(db)
