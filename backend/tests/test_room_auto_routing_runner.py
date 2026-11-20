"""Automatic routing uses the normal runner without inheriting a person's context."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agents.spec import AgentSpec, CapabilityBindingSpec, OrgMcpServerRef
from app.core.exceptions import BadRequestError
from app.core.permissions import AuthContext, OrgRoleName
from app.db.models.agent_run import RunSurface
from app.services.agent_runner import AgentRunnerService


def _db() -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    db.scalar = AsyncMock(return_value=0)
    db.get = AsyncMock(return_value=MagicMock(monthly_budget_usd=None))
    return db


def _ctx() -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        organization_id=uuid4(),
        role=OrgRoleName.OWNER,
    )


@pytest.mark.anyio
@pytest.mark.security
async def test_router_prepare_keeps_audit_person_but_builds_without_personal_identity() -> None:
    ctx = _ctx()
    service = AgentRunnerService(_db())
    version_id = uuid4()
    profile_id = uuid4()
    output_schema = {
        "type": "object",
        "properties": {"selected_ids": {"type": "array", "items": {"type": "string"}}},
        "required": ["selected_ids"],
    }
    agent = MagicMock(
        id=uuid4(),
        current_version_id=version_id,
        system_key=None,
        kind="agent",
    )
    spec = AgentSpec(
        name="Room router",
        model_profile_id=profile_id,
        instructions="Published instructions with personal context.",
        variables=[{"name": "private_note", "value": "do not pass through"}],
        capabilities=[
            CapabilityBindingSpec(id="guardrails"),
            CapabilityBindingSpec(id="thinking"),
            CapabilityBindingSpec(id="web_search"),
        ],
        mcp_servers=[OrgMcpServerRef(connection_id=uuid4())],
        output_schema=output_schema,
    )
    model_spec = MagicMock(
        label="organization model",
        profile_id=profile_id,
        provider="openai",
        model="router-model",
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
    resolve = AsyncMock(return_value=model_spec)
    create_run = AsyncMock(return_value=MagicMock(id=uuid4()))
    rendering = SimpleNamespace(for_the_run=lambda value: value, variables={})
    variable_renderer = AsyncMock(return_value=rendering)
    with (
        patch.object(
            service.registry,
            "get_pinned_spec",
            new=AsyncMock(return_value=(agent, spec, version_id)),
        ),
        patch.object(service.models, "resolve", new=resolve),
        patch.object(
            service,
            "_with_environment_observability",
            new=AsyncMock(return_value=spec),
        ),
        patch.object(service, "_variable_renderer", new=variable_renderer),
        patch.object(service.skills, "resolve_for_agent", new=AsyncMock(return_value=[])),
        patch.object(
            service.workspaces, "open", new=AsyncMock(return_value=None)
        ) as open_workspace,
        patch(
            "app.services.agent_runner.group_repo.capped_groups_for_member",
            new=AsyncMock(return_value=[]),
        ) as capped_groups,
        patch("app.services.agent_runner.agent_run_repo.create_run", new=create_run),
        patch("app.services.agent_runner.model_outputs", return_value=SimpleNamespace(chat=True)),
        patch("app.services.agent_runner.build_agent") as build,
    ):
        prepared = await service.prepare(
            ctx,
            agent.id,
            surface=RunSurface.WEB,
            version_id=version_id,
            model_profile_id=profile_id,
            output_schema=output_schema,
            decision_model=True,
            automatic_router=True,
            aside=True,
            run_limit_usd=Decimal("0.25"),
        )

    assert resolve.await_args.args[0].user_id is None
    assert variable_renderer.await_args.args[0].user_id is None
    assert create_run.await_args.kwargs["user_id"] == ctx.user_id
    assert capped_groups.await_args.kwargs["user_id"] == ctx.user_id
    assert open_workspace.await_args.kwargs["identity"].user_id is None
    assert prepared.admitted_as.audience_user_id is None
    built_with = build.call_args.kwargs
    assert built_with["user_id"] is None
    assert built_with["run_person"] is None
    assert built_with["audience"].user_id is None
    assert built_with["run_limit_usd"] == Decimal("0.05")
    assert built_with["request_approval"] is None
    assert built_with["extra_toolsets"] == []
    built_spec = build.call_args.args[0]
    assert [binding.id for binding in built_spec.capabilities] == ["guardrails", "thinking"]
    assert built_spec.mcp_servers == []
    assert built_spec.variables == []
    assert built_spec.output_schema == output_schema


@pytest.mark.anyio
@pytest.mark.security
@pytest.mark.parametrize(
    ("aside", "instructions_override"),
    [(False, None), (True, "Replace the published router prompt")],
)
async def test_router_prepare_requires_answer_only_and_pinned_instructions(
    aside: bool, instructions_override: str | None
) -> None:
    service = AgentRunnerService(_db())
    with pytest.raises(BadRequestError, match="Automatic routing requires a pinned web classifier"):
        await service.prepare(
            _ctx(),
            uuid4(),
            surface=RunSurface.WEB,
            version_id=uuid4(),
            model_profile_id=uuid4(),
            output_schema={"type": "object"},
            decision_model=True,
            automatic_router=True,
            aside=aside,
            instructions_override=instructions_override,
        )


@pytest.mark.anyio
@pytest.mark.security
@pytest.mark.parametrize(
    "origin",
    [
        {"routine_id": uuid4()},
        {"routine_source_policy_id": uuid4()},
        {"routine_activation_id": uuid4()},
        {"fired_as_creator": "schedule"},
        {"is_workflow": True},
    ],
)
async def test_router_prepare_refuses_an_actual_routine_or_workflow_origin(origin) -> None:
    service = AgentRunnerService(_db())
    with pytest.raises(BadRequestError, match="Automatic routing requires a pinned web classifier"):
        await service.prepare(
            replace(_ctx(), **origin),
            uuid4(),
            surface=RunSurface.WEB,
            version_id=uuid4(),
            model_profile_id=uuid4(),
            output_schema={"type": "object"},
            decision_model=True,
            automatic_router=True,
            aside=True,
        )


@pytest.mark.anyio
@pytest.mark.security
async def test_router_execute_uses_answer_only_without_user_aside_prefix() -> None:
    service = AgentRunnerService(_db())
    run = MagicMock()
    segment = SimpleNamespace(
        output='{"selected_ids": []}',
        run=run,
        generated_files=set(),
        tool_calls=[],
        structured={"selected_ids": []},
    )
    prepared = SimpleNamespace(
        admitted_as=SimpleNamespace(conversation_task_id=None),
        built=SimpleNamespace(deps=SimpleNamespace()),
        channel_author_context=None,
        spec=AgentSpec(name="Room router"),
        workspace=None,
        outbound=[],
        outbound_refused=[],
    )
    output_schema = {"type": "object", "properties": {"selected_ids": {"type": "array"}}}
    prompt = "Classify this bounded room burst."
    with (
        patch.object(service, "prepare", new=AsyncMock(return_value=prepared)) as prepare,
        patch.object(service, "_run", new=AsyncMock(return_value=segment)) as run_turn,
    ):
        await service.execute(
            _ctx(),
            uuid4(),
            prompt,
            surface=RunSurface.WEB,
            version_id=uuid4(),
            model_profile_id=uuid4(),
            output_schema=output_schema,
            decision_model=True,
            automatic_router=True,
            aside=True,
        )

    assert prepare.await_args.kwargs["aside"] is True
    assert prepare.await_args.kwargs["automatic_router"] is True
    assert run_turn.await_args.kwargs["user_prompt"] == prompt
