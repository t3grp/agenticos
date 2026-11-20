"""Parked room submissions rejoin their ordered turn before resumed work."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import AlreadyExistsError, AuthorizationError
from app.db.locks import LockScope
from app.services.room_submission import RoomSubmissionService

pytestmark = [pytest.mark.anyio, pytest.mark.security]


def _row(status: str = "awaiting_approval") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        conversation_id=uuid4(),
        requester_id=uuid4(),
        message_id=uuid4(),
        thread_root_message_id=None,
        agent_id=uuid4(),
        version_id=uuid4(),
        intent="message",
        run_id=uuid4(),
        status=status,
        error=None,
    )


def _patch_service(service: RoomSubmissionService, row: SimpleNamespace, **changes):
    values = {
        "for_run": AsyncMock(return_value=row),
        "get": AsyncMock(return_value=row),
        "head": AsyncMock(return_value=row),
        "save": AsyncMock(),
        "authority": AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock())),
        "hold": AsyncMock(),
        "announce": MagicMock(),
    }
    values.update(changes)
    stack = (
        patch("app.services.room_submission.room_repo.for_run", values["for_run"]),
        patch("app.services.room_submission.room_repo.get", values["get"]),
        patch("app.services.room_submission.room_repo.head", values["head"]),
        patch("app.services.room_submission.room_repo.save", values["save"]),
        patch.object(service, "authority", values["authority"]),
        patch("app.services.room_submission.hold_subject", values["hold"]),
        patch("app.services.room_submission.announce_request", values["announce"]),
    )
    return stack, values


@pytest.mark.parametrize("status", ["awaiting_approval", "awaiting_answer"])
async def test_resume_revalidates_and_reactivates_the_same_head(status: str) -> None:
    service = RoomSubmissionService(MagicMock())
    row = _row(status)
    stack, mocks = _patch_service(service, row)
    with ExitStack() as patch_stack:
        for patcher in stack:
            patch_stack.enter_context(patcher)
        await service.mark_resumed_running(row.run_id, row.organization_id)

    assert row.status == "running"
    mocks["hold"].assert_awaited_once_with(
        service.db,
        LockScope.CONVERSATION_TURN,
        row.conversation_id,
    )
    mocks["get"].assert_awaited_once_with(
        service.db,
        row.id,
        organization_id=row.organization_id,
        lock=True,
    )
    mocks["authority"].assert_awaited_once_with(row)
    mocks["save"].assert_awaited_once_with(service.db, row)
    mocks["announce"].assert_called_once()


@pytest.mark.parametrize("invalid", ["organization", "run", "later_head"])
async def test_resume_refuses_wrong_scope_run_or_non_head_without_mutation(invalid: str) -> None:
    service = RoomSubmissionService(MagicMock())
    row = _row()
    requested_run_id = row.run_id
    for_run = AsyncMock(return_value=None if invalid == "organization" else row)
    head = AsyncMock(return_value=SimpleNamespace(id=uuid4()) if invalid == "later_head" else row)
    if invalid == "run":
        row.run_id = uuid4()
    stack, mocks = _patch_service(service, row, for_run=for_run, head=head)
    with ExitStack() as patch_stack:
        for patcher in stack:
            patch_stack.enter_context(patcher)
        with pytest.raises(AlreadyExistsError):
            await service.mark_resumed_running(requested_run_id, row.organization_id)

    assert row.status == "awaiting_approval"
    mocks["save"].assert_not_awaited()
    mocks["announce"].assert_not_called()
    mocks["authority"].assert_not_awaited()


async def test_resume_refusal_after_authority_change_keeps_the_submission_parked() -> None:
    service = RoomSubmissionService(MagicMock())
    row = _row("awaiting_answer")
    authority = AsyncMock(side_effect=AuthorizationError(message="Membership changed"))
    stack, mocks = _patch_service(service, row, authority=authority)
    with ExitStack() as patch_stack:
        for patcher in stack:
            patch_stack.enter_context(patcher)
        with pytest.raises(AuthorizationError):
            await service.mark_resumed_running(row.run_id, row.organization_id)

    assert row.status == "awaiting_answer"
    mocks["save"].assert_not_awaited()
    mocks["announce"].assert_not_called()


async def test_repeated_running_resume_is_idempotent_but_rechecks_authority() -> None:
    service = RoomSubmissionService(MagicMock())
    row = _row("running")
    stack, mocks = _patch_service(service, row)
    with ExitStack() as patch_stack:
        for patcher in stack:
            patch_stack.enter_context(patcher)
        await service.mark_resumed_running(row.run_id, row.organization_id)

    assert row.status == "running"
    mocks["authority"].assert_awaited_once_with(row)
    mocks["save"].assert_not_awaited()
    mocks["announce"].assert_not_called()
