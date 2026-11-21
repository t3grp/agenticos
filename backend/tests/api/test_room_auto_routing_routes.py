"""HTTP bounds and authorization refusals for optional room routing settings and receipts."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.routes.v1 import room_auto_routing as routes
from app.core.exceptions import AuthorizationError, NotFoundError
from app.core.permissions import AuthContext
from app.main import app
from app.schemas.room_auto_routing import (
    RoomAutoRoutingDecisionListRead,
    RoomAutoRoutingPolicyRead,
)

pytestmark = [pytest.mark.anyio, pytest.mark.security]

_ORG = UUID("00000000-0000-4000-8000-000000000001")
_USER = UUID("00000000-0000-4000-8000-000000000002")
_ROOM = UUID("00000000-0000-4000-8000-000000000003")
_MESSAGE = UUID("00000000-0000-4000-8000-000000000004")
_POLICY_PATH = f"/api/v1/conversations/{_ROOM}/automatic-routing-policy"
_RECEIPTS_PATH = f"/api/v1/conversations/{_ROOM}/automatic-routing-decisions"


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    ctx = AuthContext(user_id=_USER, organization_id=_ORG, role="member")
    db = AsyncMock(spec=AsyncSession)

    async def session() -> AsyncIterator[AsyncSession]:
        yield db

    result = SimpleNamespace(
        ctx=ctx,
        receipts=AsyncMock(return_value=RoomAutoRoutingDecisionListRead(items=[])),
        policy=AsyncMock(return_value=RoomAutoRoutingPolicyRead(revision=0, can_edit=False)),
        update_policy=AsyncMock(
            return_value=RoomAutoRoutingPolicyRead(revision=5, can_edit=True, quiet_window_ms=0)
        ),
    )
    monkeypatch.setitem(app.dependency_overrides, deps.get_auth_context, lambda: ctx)
    monkeypatch.setitem(app.dependency_overrides, deps.get_db_session, session)
    monkeypatch.setattr(routes, "RoomAutoRoutingService", lambda _db: result)
    monkeypatch.setattr(routes, "RoomAutoRoutingPolicyService", lambda _db: result)
    return result


@pytest.fixture
async def client(services: SimpleNamespace) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.parametrize("count", [1, 100])
async def test_receipts_accept_a_bounded_page_of_repeated_query_parameters(
    client: AsyncClient, services: SimpleNamespace, count: int
) -> None:
    message_ids = [UUID(int=index + 100) for index in range(count)]
    response = await client.get(
        _RECEIPTS_PATH, params=[("message_id", str(value)) for value in message_ids]
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}
    services.receipts.assert_awaited_once_with(services.ctx, _ROOM, message_ids)


@pytest.mark.parametrize("count", [0, 101])
async def test_receipts_refuse_missing_or_oversized_message_id_lists_before_reading(
    client: AsyncClient, services: SimpleNamespace, count: int
) -> None:
    response = await client.get(
        _RECEIPTS_PATH,
        params=[("message_id", str(UUID(int=index + 100))) for index in range(count)],
    )

    assert response.status_code == 422, response.text
    services.receipts.assert_not_awaited()


async def test_receipts_refuse_a_malformed_message_identity_before_reading(
    client: AsyncClient, services: SimpleNamespace
) -> None:
    response = await client.get(_RECEIPTS_PATH, params={"message_id": "not-a-message-id"})

    assert response.status_code == 422, response.text
    services.receipts.assert_not_awaited()


async def test_receipt_message_access_refusal_remains_not_found_at_the_http_boundary(
    client: AsyncClient, services: SimpleNamespace
) -> None:
    services.receipts.side_effect = NotFoundError(message="Room message not found")
    response = await client.get(_RECEIPTS_PATH, params={"message_id": str(_MESSAGE)})

    assert response.status_code == 404, response.text
    services.receipts.assert_awaited_once_with(services.ctx, _ROOM, [_MESSAGE])
    assert "items" not in response.json()


async def test_policy_read_keeps_disabled_defaults_and_current_edit_authority(
    client: AsyncClient, services: SimpleNamespace
) -> None:
    response = await client.get(_POLICY_PATH)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "enabled": False,
        "revision": 0,
        "can_edit": False,
        "router_agent_id": None,
        "router_version_id": None,
        "model_profile_id": None,
        "quiet_window_ms": 2500,
        "max_responders": 1,
    }
    services.policy.assert_awaited_once_with(services.ctx, _ROOM)


async def test_policy_update_accepts_an_explicit_disable_and_expected_revision(
    client: AsyncClient, services: SimpleNamespace
) -> None:
    response = await client.put(
        _POLICY_PATH,
        json={
            "expected_revision": 4,
            "enabled": False,
            "router_agent_id": None,
            "model_profile_id": None,
            "quiet_window_ms": 0,
            "max_responders": 1,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 5
    assert response.json()["enabled"] is False
    services.update_policy.assert_awaited_once()
    ctx, room_id, body = services.update_policy.await_args.args
    assert (ctx, room_id) == (services.ctx, _ROOM)
    assert body.expected_revision == 4
    assert body.router_agent_id is None
    assert body.model_profile_id is None
    assert body.quiet_window_ms == 0


async def test_policy_cannot_be_enabled_without_choosing_router_and_model(
    client: AsyncClient, services: SimpleNamespace
) -> None:
    response = await client.put(_POLICY_PATH, json={"expected_revision": 0, "enabled": True})

    assert response.status_code == 422, response.text
    services.update_policy.assert_not_awaited()


async def test_policy_manager_refusal_is_not_acknowledged_as_a_saved_change(
    client: AsyncClient, services: SimpleNamespace
) -> None:
    services.update_policy.side_effect = AuthorizationError(message="Room management is required")
    response = await client.put(_POLICY_PATH, json={"expected_revision": 0, "enabled": False})

    assert response.status_code == 403, response.text
    services.update_policy.assert_awaited_once()
    assert "revision" not in response.json()
