"""Runner admission and transcript suppression for room agent reactions."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agents.capabilities.room_reactions._capability import RoomReactions
from app.agents.capabilities.room_reactions._contract import RoomReactionRunContext
from app.agents.spec import AgentSpec
from app.core.exceptions import AuthorizationError
from app.core.permissions import AuthContext, OrgRoleName
from app.db.models.agent_run import RunSurface
from app.schemas.local_target import LocalTarget
from app.services.agent_runner import AgentRunnerService
from app.services.room_agent_message_reaction import RoomAgentReactionPort


def _db() -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    db.scalar = AsyncMock(return_value=0)
    db.get = AsyncMock(return_value=MagicMock(monthly_budget_usd=None))
    return db


def _setup():
    ctx = AuthContext(user_id=uuid4(), organization_id=uuid4(), role=OrgRoleName.OWNER)
    agent_id, version_id, profile_id = uuid4(), uuid4(), uuid4()
    conversation_id, submission_id, message_id = uuid4(), uuid4(), uuid4()
    context = RoomReactionRunContext(
        organization_id=ctx.organization_id,
        conversation_id=conversation_id,
        submission_id=submission_id,
        requester_id=ctx.user_id,
        accepted_message_id=message_id,
        agent_id=agent_id,
        agent_version_id=version_id,
        thread_root_message_id=None,
    )
    agent = MagicMock(
        id=agent_id,
        current_version_id=version_id,
        owner_user_id=ctx.user_id,
        system_key=None,
        kind="agent",
    )
    spec = AgentSpec(
        name="Responder",
        model_profile_id=profile_id,
        instructions="Respond normally.",
    )
    model_spec = MagicMock(
        label="organization model",
        profile_id=profile_id,
        provider="openai",
        model="responder-model",
        credential=SimpleNamespace(
            provider="openai",
            base_url=None,
            api=None,
            region=None,
            routing={},
            credential_id=None,
            authorization_generation=None,
        ),
        fallbacks=[],
        secret_id=None,
        params={},
    )
    service = AgentRunnerService(_db())
    run_id = uuid4()
    build = MagicMock()
    create_run = AsyncMock(return_value=MagicMock(id=run_id))
    resolve = AsyncMock(return_value=model_spec)
    rendering = SimpleNamespace(for_the_run=lambda value: value, variables={})
    variable_renderer = AsyncMock(return_value=rendering)
    patches = (
        patch.object(
            service.registry,
            "get_pinned_spec",
            new=AsyncMock(return_value=(agent, spec, version_id)),
        ),
        patch.object(
            service.registry,
            "get_candidate_spec",
            new=AsyncMock(return_value=(agent, spec, version_id)),
        ),
        patch.object(
            service.registry,
            "with_assistant_guidance",
            new=AsyncMock(return_value=spec),
        ),
        patch.object(service.models, "resolve", new=resolve),
        patch.object(
            service,
            "_with_environment_observability",
            new=AsyncMock(return_value=spec),
        ),
        patch.object(
            service,
            "_recorded_conversation_state",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    overhead=None,
                    reminder_state=None,
                    plan=None,
                )
            ),
        ),
        patch.object(service, "_variable_renderer", new=variable_renderer),
        patch.object(service.skills, "resolve_for_agent", new=AsyncMock(return_value=[])),
        patch.object(service.secrets, "resolve_for_bindings", new=AsyncMock(return_value={})),
        patch.object(service.workspaces, "open", new=AsyncMock(return_value=None)),
        patch(
            "app.services.agent_runner.group_repo.capped_groups_for_member",
            new=AsyncMock(return_value=[]),
        ),
        patch("app.services.agent_runner.agent_run_repo.create_run", new=create_run),
        patch("app.services.agent_runner.model_outputs", return_value=SimpleNamespace(chat=True)),
        patch(
            "app.services.agent_runner.ProjectService.for_run",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.agent_runner.conversation_repo.is_explicit_room",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.channel_actions.ChannelActionService.tools",
            new=AsyncMock(return_value=[]),
        ),
        patch("app.services.agent_runner.build_agent", new=build),
    )
    return (
        ctx,
        context,
        service,
        agent,
        spec,
        version_id,
        profile_id,
        run_id,
        resolve,
        build,
        patches,
    )


@pytest.mark.anyio
@pytest.mark.security
async def test_native_room_responder_prepares_with_server_bound_reaction_capability() -> None:
    (
        ctx,
        room_context,
        service,
        agent,
        _spec,
        version_id,
        profile_id,
        run_id,
        _resolve,
        build,
        patches,
    ) = _setup()
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        prepared = await service.prepare(
            ctx,
            agent.id,
            surface=RunSurface.WEB,
            conversation_id=room_context.conversation_id,
            version_id=version_id,
            model_profile_id=profile_id,
            acts_for_sender=True,
            room_reaction_context=room_context,
        )

    assert prepared.room_reaction_port.context == room_context
    assert prepared.room_reaction_port.run_id == run_id
    assert isinstance(prepared.room_reaction_port, RoomAgentReactionPort)
    assert build.call_args.kwargs["audience"].shared_transcript is True
    assert any(
        isinstance(capability, RoomReactions)
        for capability in build.call_args.kwargs["extra_capabilities"]
    )


@pytest.mark.anyio
@pytest.mark.security
@pytest.mark.parametrize(
    "unsafe",
    [
        "aside",
        "candidate_eval",
        "instruction_eval",
        "test",
        "isolation",
        "local",
        "compaction",
    ],
)
async def test_room_reaction_context_refuses_non_native_run_modes_before_model_resolution(
    unsafe: str,
) -> None:
    (
        ctx,
        room_context,
        service,
        agent,
        _spec,
        version_id,
        profile_id,
        _run_id,
        resolve,
        _build,
        patches,
    ) = _setup()
    parameters: dict[str, object] = {}
    if unsafe == "aside":
        parameters["aside"] = True
    elif unsafe == "candidate_eval":
        parameters["candidate"] = SimpleNamespace(skill_bodies={}, context_bodies={})
    elif unsafe == "instruction_eval":
        parameters["instructions_override"] = "Evaluation-only instructions"
    elif unsafe == "test":
        parameters["test"] = True
    elif unsafe == "local":
        parameters["local"] = LocalTarget(folder="C:\\room")
    elif unsafe == "compaction":
        parameters["compaction_only"] = True
    else:
        parameters["isolation"] = object()

    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        if unsafe == "compaction":
            stack.enter_context(
                patch(
                    "app.services.agent_runner._compaction_only",
                    return_value=SimpleNamespace(
                        capabilities=[
                            SimpleNamespace(id="compaction", config={"strategy": "summarize"})
                        ]
                    ),
                )
            )
        with pytest.raises(AuthorizationError, match="authorized top-level room responder"):
            await service.prepare(
                ctx,
                agent.id,
                surface=RunSurface.WEB,
                conversation_id=room_context.conversation_id,
                version_id=version_id,
                model_profile_id=profile_id,
                acts_for_sender=True,
                room_reaction_context=room_context,
                **parameters,
            )

    resolve.assert_not_awaited()
