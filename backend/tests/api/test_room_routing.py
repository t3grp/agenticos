"""Routing endpoints use the signed-in requester and the room service boundary."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api import deps
from app.core.permissions import AuthContext
from app.main import app
from app.schemas.room_routing import LeadPolicyRead, RoomRoutingRead, RoutingProposalRead

pytestmark = pytest.mark.anyio


async def test_policy_and_thread_reads_forward_the_authenticated_room_scope(client):
    ctx = AuthContext(user_id=uuid4(), organization_id=uuid4(), role="member")
    room, thread = uuid4(), uuid4()
    policy = LeadPolicyRead()
    service = MagicMock(
        policy=AsyncMock(return_value=policy),
        read=AsyncMock(return_value=RoomRoutingRead(policy=policy, proposals=[], tasks=[])),
    )
    app.dependency_overrides[deps.get_auth_context] = lambda: ctx
    app.dependency_overrides[deps.get_room_routing_service] = lambda: service
    response = await client.get(f"/api/v1/conversations/{room}/lead-policy")
    assert response.status_code == 200 and response.json()["enabled"] is False
    response = await client.get(
        f"/api/v1/conversations/{room}/routing?thread_root_message_id={thread}"
    )
    assert response.status_code == 200
    service.read.assert_awaited_once_with(ctx, room, thread)


@pytest.mark.parametrize("action", ["accept", "discard"])
async def test_decision_routes_keep_the_requester_and_idempotency_contract(client, action):
    ctx = AuthContext(user_id=uuid4(), organization_id=uuid4(), role="member")
    room, proposal = uuid4(), uuid4()
    result = RoutingProposalRead(
        id=proposal,
        source_submission_id=uuid4(),
        thread_root_message_id=None,
        requester_id=ctx.user_id,
        revision=2,
        policy_revision=1,
        status="accepted" if action == "accept" else "discarded",
        items=[],
        can_decide=False,
    )
    service = MagicMock(decide=AsyncMock(return_value=result))
    app.dependency_overrides[deps.get_auth_context] = lambda: ctx
    app.dependency_overrides[deps.get_room_routing_service] = lambda: service
    response = await client.post(
        f"/api/v1/conversations/{room}/routing-proposals/{proposal}/{action}",
        json={"expected_revision": 1, "idempotency_key": "lost-ack", "item_ids": []},
    )
    assert response.status_code == 200
    assert service.decide.await_args.args[:3] == (ctx, room, proposal)
    assert service.decide.await_args.args[3].idempotency_key == "lost-ack"
    assert service.decide.await_args.kwargs["accept"] is (action == "accept")
