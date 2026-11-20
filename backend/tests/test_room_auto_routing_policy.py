"""Automatic routing stays disabled until an authorized manager opts in."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import AlreadyExistsError, AuthorizationError
from app.core.permissions import AuthContext
from app.db.models.conversation import Conversation
from app.db.models.room_auto_routing import RoomAutoRoutingPolicy
from app.schemas.room_auto_routing import (
    RoomAutoRoutingDecisionCreate,
    RoomAutoRoutingDecisionOutput,
    RoomAutoRoutingPolicyWrite,
)
from app.services import room_auto_routing_policy as module

pytestmark = pytest.mark.anyio


@pytest.fixture
def policy_service(monkeypatch):
    ctx = AuthContext(user_id=uuid4(), organization_id=uuid4(), role="owner")
    room = Conversation(
        id=uuid4(),
        organization_id=ctx.organization_id,
        project_id=None,
        room_visibility="private",
        is_archived=False,
    )
    service = module.RoomAutoRoutingPolicyService(MagicMock(info={}))
    service.rooms.room = AsyncMock(return_value=room)
    service.participants._can_manage = AsyncMock(return_value=True)
    monkeypatch.setattr(module.routing_repo, "policy", AsyncMock(return_value=None))
    return service, ctx, room


@pytest.mark.security
async def test_missing_policy_returns_defaults_and_current_edit_permission(policy_service):
    service, ctx, room = policy_service

    result = await service.policy(ctx, room.id)

    assert result.model_dump() == {
        "enabled": False,
        "revision": 0,
        "can_edit": True,
        "router_agent_id": None,
        "router_version_id": None,
        "model_profile_id": None,
        "quiet_window_ms": 2500,
        "max_responders": 1,
    }


async def test_first_save_creates_revision_one(policy_service, monkeypatch):
    service, ctx, room = policy_service
    save = AsyncMock()
    monkeypatch.setattr(module.routing_repo, "save_policy", save)
    monkeypatch.setattr(module, "hold_subject", AsyncMock())

    result = await service.update_policy(
        ctx,
        room.id,
        RoomAutoRoutingPolicyWrite(expected_revision=0, enabled=False),
    )

    assert result.revision == 1 and not result.enabled and result.can_edit
    save.assert_awaited_once()
    assert save.await_args.args[1].conversation_id == room.id


async def test_stale_policy_revision_is_refused_before_validation(policy_service, monkeypatch):
    service, ctx, room = policy_service
    monkeypatch.setattr(module, "hold_subject", AsyncMock())
    validate = AsyncMock()
    service._router_version = validate

    with pytest.raises(AlreadyExistsError, match="automatic routing policy changed"):
        await service.update_policy(
            ctx,
            room.id,
            RoomAutoRoutingPolicyWrite(expected_revision=1, enabled=False),
        )

    validate.assert_not_awaited()


async def test_non_manager_cannot_update_policy(policy_service, monkeypatch):
    service, ctx, room = policy_service
    service.participants._can_manage.return_value = False

    with pytest.raises(AuthorizationError, match="room manager"):
        await service.update_policy(
            ctx,
            room.id,
            RoomAutoRoutingPolicyWrite(expected_revision=0, enabled=False),
        )

    service.rooms.room.assert_awaited_once()


def test_policy_write_requires_router_and_profile_together():
    with pytest.raises(ValueError, match="both a room router"):
        RoomAutoRoutingPolicyWrite(
            expected_revision=0,
            enabled=True,
            router_agent_id=uuid4(),
            model_profile_id=None,
        )


def test_decision_output_rejects_duplicate_selected_agents():
    agent_id = uuid4()
    with pytest.raises(ValueError, match="unique"):
        RoomAutoRoutingDecisionOutput(selected_ids=[agent_id, agent_id])


def test_decision_snapshot_requires_accepted_message_in_bounded_group():
    message_id = uuid4()
    with pytest.raises(ValueError, match="accepted message"):
        RoomAutoRoutingDecisionCreate(
            organization_id=uuid4(),
            conversation_id=uuid4(),
            submission_id=uuid4(),
            accepted_message_id=message_id,
            requester_id=uuid4(),
            accepted_at="2026-11-20T00:00:00Z",
            ready_at="2026-11-20T00:00:02Z",
            policy_revision=1,
            router_agent_id=uuid4(),
            router_name="Router",
            router_version_id=uuid4(),
            model_profile_id=uuid4(),
            model_profile_fingerprint="0" * 64,
            quiet_window_ms=2500,
            max_responders=1,
            candidate_snapshot=[],
            grouped_message_ids=[uuid4()],
        )


async def test_disabled_policy_preserves_unavailable_saved_router_and_profile(
    policy_service, monkeypatch
):
    service, ctx, room = policy_service
    router_id = uuid4()
    version_id = uuid4()
    profile_id = uuid4()
    row = RoomAutoRoutingPolicy(
        organization_id=ctx.organization_id,
        conversation_id=room.id,
        enabled=True,
        revision=3,
        router_agent_id=router_id,
        router_version_id=version_id,
        model_profile_id=profile_id,
    )
    monkeypatch.setattr(module.routing_repo, "policy", AsyncMock(return_value=row))
    save = AsyncMock()
    monkeypatch.setattr(module.routing_repo, "save_policy", save)
    monkeypatch.setattr(module, "hold_subject", AsyncMock())
    validate_router = AsyncMock(side_effect=AssertionError("removed router was revalidated"))
    validate_profile = AsyncMock(side_effect=AssertionError("removed profile was revalidated"))
    service._router_version = validate_router
    service._validate_model_profile = validate_profile

    result = await service.update_policy(
        ctx,
        room.id,
        RoomAutoRoutingPolicyWrite(
            expected_revision=3,
            enabled=False,
            router_agent_id=router_id,
            model_profile_id=profile_id,
        ),
    )

    assert not result.enabled
    assert result.revision == 4
    assert result.router_agent_id == router_id
    assert result.router_version_id == version_id
    assert result.model_profile_id == profile_id
    validate_router.assert_not_awaited()
    validate_profile.assert_not_awaited()
    save.assert_awaited_once()


async def test_reenabling_saved_selection_revalidates_current_authority(
    policy_service, monkeypatch
):
    service, ctx, room = policy_service
    router_id = uuid4()
    profile_id = uuid4()
    row = RoomAutoRoutingPolicy(
        organization_id=ctx.organization_id,
        conversation_id=room.id,
        enabled=False,
        revision=4,
        router_agent_id=router_id,
        router_version_id=uuid4(),
        model_profile_id=profile_id,
    )
    monkeypatch.setattr(module.routing_repo, "policy", AsyncMock(return_value=row))
    monkeypatch.setattr(module, "hold_subject", AsyncMock())
    unavailable = AuthorizationError(message="router is no longer available")
    validate_router = AsyncMock(side_effect=unavailable)
    validate_profile = AsyncMock()
    service._router_version = validate_router
    service._validate_model_profile = validate_profile

    with pytest.raises(AuthorizationError, match="no longer available"):
        await service.update_policy(
            ctx,
            room.id,
            RoomAutoRoutingPolicyWrite(
                expected_revision=4,
                enabled=True,
                router_agent_id=router_id,
                model_profile_id=profile_id,
            ),
        )

    validate_router.assert_awaited_once_with(ctx, room, router_id)
