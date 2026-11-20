"""Regression coverage for identity fields exposed in the chat sidebar."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents.spec import SPEC_VERSION, AgentSpec
from app.core.exceptions import NotFoundError
from app.core.permissions import AuthContext, OrgRoleName
from app.db.models.agent import AgentKind, AgentStatus
from app.db.models.resource_grant import Visibility
from app.services.agent_registry import AgentRegistryService

REGISTRY_PATH = "app.services.agent_registry"


def _context(role: str = OrgRoleName.OWNER) -> AuthContext:
    return AuthContext(user_id=uuid4(), organization_id=uuid4(), role=role)


def _db() -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


def test_a_stored_v15_spec_loads_through_the_runtime_path_with_an_empty_title() -> None:
    """Published JSONB is read by model_validate; adding a default needs no SQL backfill."""
    stored = {
        "spec_version": 15,
        "name": "Support",
        "description": "Existing published description",
        "instructions": "Answer carefully.",
        "capabilities": [],
    }

    loaded = AgentSpec.model_validate(stored)

    assert SPEC_VERSION >= 16
    assert loaded.spec_version == 15
    assert loaded.title == ""
    assert AgentSpec.model_validate(loaded.model_dump(mode="json")) == loaded


def test_a_short_title_is_trimmed_and_an_eighty_character_title_is_allowed() -> None:
    assert AgentSpec(name="Support", title="  Team lead  ").title == "Team lead"
    assert AgentSpec(name="Support", title="x" * 80).title == "x" * 80


def test_a_title_longer_than_eighty_characters_is_refused() -> None:
    with pytest.raises(ValidationError):
        AgentSpec(name="Support", title="x" * 81)


@pytest.mark.anyio
async def test_agent_listing_projects_the_frozen_title_while_the_draft_has_an_edit() -> None:
    ctx = _context()
    version_id = uuid4()
    agent_id = uuid4()
    agent = SimpleNamespace(
        id=agent_id,
        organization_id=ctx.organization_id,
        owner_user_id=ctx.user_id,
        visibility=Visibility.PRIVATE.value,
        status=AgentStatus.PUBLISHED.value,
        workflow_id=None,
        system_key=None,
        kind=AgentKind.AGENT,
        slug="support",
        name="Support",
        description=None,
        has_avatar=False,
        avatar_color=None,
        categories=[],
        tags=[],
        current_version_id=version_id,
        shipped_template=None,
        draft_spec={"spec_version": 16, "name": "Support", "title": "Unpublished draft title"},
        created_at=None,
        updated_at=None,
    )
    service = AgentRegistryService(_db())
    service._visible_scope = AsyncMock(return_value=(True, []))
    title_lookup = AsyncMock(return_value={version_id: "Published title"})

    with (
        patch(f"{REGISTRY_PATH}.agent_repo.list_visible", new=AsyncMock(return_value=([agent], 1))),
        patch(
            f"{REGISTRY_PATH}.resource_grant_repo.count_for_resources",
            new=AsyncMock(return_value={}),
        ),
        patch(
            f"{REGISTRY_PATH}.agent_exposure_repo.active_surfaces_for_agents",
            new=AsyncMock(return_value={}),
        ),
        patch(f"{REGISTRY_PATH}.agent_repo.published_budget_caps", new=AsyncMock(return_value={})),
        patch(f"{REGISTRY_PATH}.agent_repo.published_starters", new=AsyncMock(return_value={})),
        patch(
            f"{REGISTRY_PATH}.agent_repo.published_starter_icons",
            new=AsyncMock(return_value={}),
        ),
        patch(
            f"{REGISTRY_PATH}.agent_repo.published_starter_titles",
            new=AsyncMock(return_value={}),
        ),
        patch(
            f"{REGISTRY_PATH}.agent_repo.published_agent_titles",
            new=title_lookup,
            create=True,
        ),
        patch(
            f"{REGISTRY_PATH}.agent_repo.published_capability_bindings",
            new=AsyncMock(return_value={}),
        ),
        patch(
            f"{REGISTRY_PATH}.agent_repo.published_coding_agents", new=AsyncMock(return_value={})
        ),
        patch(
            f"{REGISTRY_PATH}.agent_repo.published_local_workspaces",
            new=AsyncMock(return_value=set()),
        ),
        patch(
            f"{REGISTRY_PATH}.agent_repo.published_model_profiles",
            new=AsyncMock(return_value={}),
        ),
        patch(
            f"{REGISTRY_PATH}.accessible_ids",
            new=AsyncMock(return_value={agent_id}),
        ),
        patch(
            f"{REGISTRY_PATH}.groups_sharing",
            new=AsyncMock(return_value={agent_id: []}),
        ),
        patch.object(service, "_context_windows", new=AsyncMock(return_value={})),
    ):
        rows, total = await service.list_agents(ctx)

    assert total == 1
    assert rows[0].published_title == "Published title"
    assert agent.draft_spec["title"] == "Unpublished draft title"
    title_lookup.assert_awaited_once()
    assert title_lookup.call_args.kwargs["version_ids"] == [version_id]


@pytest.mark.anyio
async def test_removing_an_avatar_requires_agent_edit() -> None:
    ctx = _context(OrgRoleName.MEMBER)
    agent_id = uuid4()
    agent = SimpleNamespace(
        id=agent_id,
        organization_id=ctx.organization_id,
        owner_user_id=uuid4(),
        visibility=Visibility.PRIVATE.value,
        workflow_id=None,
        status=AgentStatus.PUBLISHED.value,
        system_key=None,
        avatar_url="avatars/agents/agent-a/picture.png",
    )
    storage = MagicMock(delete=AsyncMock())
    update = AsyncMock(return_value=agent)

    with (
        patch(f"{REGISTRY_PATH}.agent_repo.get", new=AsyncMock(return_value=agent)),
        patch(
            "app.services.access.resource_grant_repo.get_level",
            new=AsyncMock(return_value=None),
        ),
        patch(f"{REGISTRY_PATH}.agent_repo.update", new=update),
        patch(f"{REGISTRY_PATH}.get_file_storage", return_value=storage),
        pytest.raises(NotFoundError),
    ):
        await AgentRegistryService(_db()).remove_avatar(ctx, agent_id)

    storage.delete.assert_not_awaited()
    update.assert_not_awaited()


@pytest.mark.anyio
async def test_removing_an_absent_avatar_is_idempotent() -> None:
    ctx = _context()
    agent_id = uuid4()
    agent = SimpleNamespace(
        id=agent_id,
        organization_id=ctx.organization_id,
        owner_user_id=ctx.user_id,
        visibility=Visibility.PRIVATE.value,
        workflow_id=None,
        status=AgentStatus.PUBLISHED.value,
        system_key=None,
        avatar_url=None,
    )
    storage = MagicMock(delete=AsyncMock())
    update = AsyncMock(return_value=agent)

    with (
        patch(f"{REGISTRY_PATH}.agent_repo.get", new=AsyncMock(return_value=agent)),
        patch(f"{REGISTRY_PATH}.agent_repo.update", new=update),
        patch(f"{REGISTRY_PATH}.get_file_storage", return_value=storage),
    ):
        result = await AgentRegistryService(_db()).remove_avatar(ctx, agent_id)

    assert result is agent
    storage.delete.assert_not_awaited()
    assert update.call_args.kwargs["update_data"] == {"avatar_url": None}


@pytest.mark.anyio
async def test_removal_clears_the_database_reference_when_storage_cleanup_fails() -> None:
    ctx = _context()
    agent_id = uuid4()
    avatar_path = "avatars/agents/agent-a/picture.png"
    agent = SimpleNamespace(
        id=agent_id,
        organization_id=ctx.organization_id,
        owner_user_id=ctx.user_id,
        visibility=Visibility.PRIVATE.value,
        workflow_id=None,
        status=AgentStatus.PUBLISHED.value,
        system_key=None,
        avatar_url=avatar_path,
    )
    storage = MagicMock(delete=AsyncMock(side_effect=OSError("object store unavailable")))
    update = AsyncMock(return_value=agent)

    with (
        patch(f"{REGISTRY_PATH}.agent_repo.get", new=AsyncMock(return_value=agent)),
        patch(f"{REGISTRY_PATH}.agent_repo.update", new=update),
        patch("app.services.file_storage.get_file_storage", return_value=storage),
        patch(f"{REGISTRY_PATH}.spawn_after_commit") as defer,
    ):
        result = await AgentRegistryService(_db()).remove_avatar(ctx, agent_id)
        storage.delete.assert_not_awaited()
        defer.assert_called_once()
        await defer.call_args.args[1]

    assert result is agent
    storage.delete.assert_awaited_once_with(avatar_path)
    assert update.call_args.kwargs["update_data"] == {"avatar_url": None}


@pytest.mark.anyio
async def test_failed_avatar_update_does_not_schedule_file_deletion() -> None:
    ctx = _context()
    agent_id = uuid4()
    agent = SimpleNamespace(avatar_url="avatars/agents/agent-a/picture.png")
    service = AgentRegistryService(_db())
    service.get = AsyncMock(return_value=agent)

    with (
        patch(
            f"{REGISTRY_PATH}.agent_repo.update",
            new=AsyncMock(side_effect=RuntimeError("rollback")),
        ),
        patch(f"{REGISTRY_PATH}.spawn_after_commit") as defer,
        pytest.raises(RuntimeError, match="rollback"),
    ):
        await service.remove_avatar(ctx, agent_id)

    defer.assert_not_called()
