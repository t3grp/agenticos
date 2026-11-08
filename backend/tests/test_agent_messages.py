"""Agent-to-agent messages: who may send, read, react, and what a thread shows."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AuthorizationError, BadRequestError, NotFoundError
from app.core.permissions import AuthContext, OrgRoleName, Perm
from app.db.models.agent_message import (
    AgentMessage,
    AgentMessageReaction,
    AgentMessageThread,
)
from app.schemas.agent_message import AgentMessageReactionCreate, AgentMessageSend
from app.services.agent_messages import AgentMessageService

pytestmark = pytest.mark.anyio

PATH = "app.services.agent_messages"
NOW = datetime(2026, 11, 9, 3, 0, tzinfo=UTC)


def _ctx(org: uuid.UUID, user: uuid.UUID) -> AuthContext:
    return AuthContext(user_id=user, organization_id=org, role=OrgRoleName.MEMBER)


def _agent(
    org: uuid.UUID, *, slug: str, status: str = "published", agent_id: uuid.UUID | None = None
) -> MagicMock:
    agent = MagicMock()
    agent.id = agent_id or uuid.uuid4()
    agent.organization_id = org
    agent.slug = slug
    agent.name = slug.replace("-", " ").title()
    agent.status = status
    return agent


def _thread(org: uuid.UUID, a: MagicMock, b: MagicMock) -> AgentMessageThread:
    return AgentMessageThread(
        id=uuid.uuid4(),
        organization_id=org,
        agent_a_id=a.id,
        agent_b_id=b.id,
        last_message_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _message(
    org: uuid.UUID, thread: AgentMessageThread, sender: MagicMock, *, body: str = "hello"
) -> AgentMessage:
    return AgentMessage(
        id=uuid.uuid4(),
        thread_id=thread.id,
        organization_id=org,
        sender_agent_id=sender.id,
        sender_user_id=None,
        body=body,
        reply_to_message_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _service() -> AgentMessageService:
    return AgentMessageService(MagicMock())


class TestSend:
    async def test_a_message_is_stored_in_the_pair_s_thread(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender, recipient = _agent(org, slug="researcher"), _agent(org, slug="summariser")
        thread = _thread(org, sender, recipient)
        stored = _message(org, thread, sender)

        async def create_message(_db: Any, **kwargs: Any) -> AgentMessage:
            assert kwargs["thread"] is thread
            return stored

        with (
            patch(f"{PATH}.agent_repo.get", new=AsyncMock(side_effect=[sender, recipient])),
            patch(f"{PATH}.resolve_access", new=AsyncMock(return_value=True)),
            patch(
                f"{PATH}.agent_message_repo.get_or_create_thread",
                new=AsyncMock(return_value=thread),
            ),
            patch(f"{PATH}.agent_message_repo.create_message", new=create_message),
            patch(f"{PATH}.agent_message_repo.touch_thread", new=AsyncMock()),
            patch(f"{PATH}.agent_message_repo.list_reactions", new=AsyncMock(return_value=[])),
            patch(
                f"{PATH}.agent_message_repo.list_attachments",
                new=AsyncMock(return_value=[]),
            ),
        ):
            sent = await _service().send(
                _ctx(org, user),
                AgentMessageSend(
                    sender_agent_id=sender.id, recipient=str(recipient.id), body="hello"
                ),
            )

        assert sent.thread_id == thread.id
        assert sent.sender_agent_slug == "researcher"
        assert sent.body == "hello"

    async def test_an_agent_cannot_message_itself(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender = _agent(org, slug="researcher")
        with (
            patch(f"{PATH}.agent_repo.get", new=AsyncMock(return_value=sender)),
            pytest.raises(BadRequestError),
        ):
            await _service().send(
                _ctx(org, user),
                AgentMessageSend(sender_agent_id=sender.id, recipient=str(sender.id), body="hello"),
            )

    async def test_a_draft_agent_cannot_be_messaged(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender = _agent(org, slug="researcher")
        recipient = _agent(org, slug="draft-one", status="draft")
        with (
            patch(
                f"{PATH}.agent_repo.get",
                new=AsyncMock(side_effect=[sender, recipient]),
            ),
            pytest.raises(BadRequestError),
        ):
            await _service().send(
                _ctx(org, user),
                AgentMessageSend(
                    sender_agent_id=sender.id, recipient=str(recipient.id), body="hello"
                ),
            )

    async def test_sending_needs_run_on_both_agents(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender, recipient = _agent(org, slug="researcher"), _agent(org, slug="summariser")

        async def may(_db: Any, _ctx: Any, agent: Any, _perm: Perm, **_kw: Any) -> bool:
            return agent.id == sender.id

        with (
            patch(
                f"{PATH}.agent_repo.get",
                new=AsyncMock(side_effect=[sender, recipient]),
            ),
            patch(f"{PATH}.resolve_access", new=may),
            pytest.raises(AuthorizationError),
        ):
            await _service().send(
                _ctx(org, user),
                AgentMessageSend(
                    sender_agent_id=sender.id, recipient=str(recipient.id), body="hello"
                ),
            )

    async def test_a_recipient_in_another_organization_is_not_found(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender = _agent(org, slug="researcher")
        foreign = _agent(uuid.uuid4(), slug="outsider")
        with (
            patch(
                f"{PATH}.agent_repo.get",
                new=AsyncMock(side_effect=[sender, None]),
            ),
            patch(
                f"{PATH}.agent_repo.get_by_slug",
                new=AsyncMock(return_value=foreign),
            ),
            pytest.raises(NotFoundError),
        ):
            await _service().send(
                _ctx(org, user),
                AgentMessageSend(sender_agent_id=sender.id, recipient="outsider", body="hello"),
            )

    async def test_a_reply_must_answer_a_message_in_the_same_thread(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender, recipient = _agent(org, slug="researcher"), _agent(org, slug="summariser")
        thread = _thread(org, sender, recipient)
        other_thread = _thread(org, sender, recipient)
        stray = _message(org, other_thread, recipient, body="elsewhere")
        with (
            patch(
                f"{PATH}.agent_repo.get",
                new=AsyncMock(side_effect=[sender, recipient]),
            ),
            patch(f"{PATH}.resolve_access", new=AsyncMock(return_value=True)),
            patch(
                f"{PATH}.agent_message_repo.get_or_create_thread",
                new=AsyncMock(return_value=thread),
            ),
            patch(
                f"{PATH}.agent_message_repo.get_message",
                new=AsyncMock(return_value=stray),
            ),
            pytest.raises(BadRequestError),
        ):
            await _service().send(
                _ctx(org, user),
                AgentMessageSend(
                    sender_agent_id=sender.id,
                    recipient=str(recipient.id),
                    body="hello",
                    reply_to_message_id=stray.id,
                ),
            )

    async def test_an_attachment_uploaded_by_somebody_else_is_refused(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender, recipient = _agent(org, slug="researcher"), _agent(org, slug="summariser")
        thread = _thread(org, sender, recipient)
        with (
            patch(
                f"{PATH}.agent_repo.get",
                new=AsyncMock(side_effect=[sender, recipient]),
            ),
            patch(f"{PATH}.resolve_access", new=AsyncMock(return_value=True)),
            patch(
                f"{PATH}.agent_message_repo.get_or_create_thread",
                new=AsyncMock(return_value=thread),
            ),
            patch(f"{PATH}.chat_file_repo.get_many", new=AsyncMock(return_value=[])),
            pytest.raises(NotFoundError),
        ):
            await _service().send(
                _ctx(org, user),
                AgentMessageSend(
                    sender_agent_id=sender.id,
                    recipient=str(recipient.id),
                    body="hello",
                    attachment_file_ids=[uuid.uuid4()],
                ),
            )

    async def test_eleven_attachments_are_refused(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender, recipient = _agent(org, slug="researcher"), _agent(org, slug="summariser")
        create_thread = AsyncMock()
        with (
            patch(
                f"{PATH}.agent_repo.get",
                new=AsyncMock(side_effect=[sender, recipient]),
            ),
            patch(f"{PATH}.resolve_access", new=AsyncMock(return_value=True)),
            patch(
                f"{PATH}.agent_message_repo.get_or_create_thread",
                new=create_thread,
            ),
            pytest.raises(BadRequestError),
        ):
            await _service().send(
                _ctx(org, user),
                AgentMessageSend.model_construct(
                    sender_agent_id=sender.id,
                    recipient=str(recipient.id),
                    body="hello",
                    reply_to_message_id=None,
                    attachment_file_ids=[uuid.uuid4() for _ in range(11)],
                ),
            )
        # The refusal happens before the thread is touched: no empty thread
        # is left behind for a send that never happened.
        assert create_thread.await_count == 0


class TestReact:
    async def test_the_same_emoji_twice_takes_the_reaction_back(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender, recipient = _agent(org, slug="researcher"), _agent(org, slug="summariser")
        thread = _thread(org, sender, recipient)
        message = _message(org, thread, sender)
        reaction = AgentMessageReaction(
            id=uuid.uuid4(),
            message_id=message.id,
            organization_id=org,
            emoji="👍",
            reactor_agent_id=None,
            reactor_user_id=user,
            created_at=NOW,
        )
        removed = AsyncMock()
        with (
            patch(
                f"{PATH}.agent_message_repo.get_message",
                new=AsyncMock(return_value=message),
            ),
            patch(
                f"{PATH}.agent_message_repo.get_thread",
                new=AsyncMock(return_value=thread),
            ),
            patch(
                f"{PATH}.agent_repo.get",
                new=AsyncMock(return_value=sender),
            ),
            patch(f"{PATH}.resolve_access", new=AsyncMock(return_value=True)),
            patch(
                f"{PATH}.agent_message_repo.get_reaction",
                new=AsyncMock(return_value=reaction),
            ),
            patch(f"{PATH}.agent_message_repo.remove_reaction", new=removed),
            patch(f"{PATH}.agent_message_repo.list_reactions", new=AsyncMock(return_value=[])),
        ):
            result = await _service().react(
                _ctx(org, user),
                message_id=message.id,
                data=AgentMessageReactionCreate(emoji="👍"),
            )

        removed.assert_awaited_once()
        assert result.reacted is False
        assert result.reactions == []

    async def test_an_agent_outside_the_thread_cannot_react_as_itself(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender, recipient = _agent(org, slug="researcher"), _agent(org, slug="summariser")
        outsider = _agent(org, slug="outsider")
        thread = _thread(org, sender, recipient)
        message = _message(org, thread, sender)
        with (
            patch(
                f"{PATH}.agent_message_repo.get_message",
                new=AsyncMock(return_value=message),
            ),
            patch(
                f"{PATH}.agent_message_repo.get_thread",
                new=AsyncMock(return_value=thread),
            ),
            patch(
                f"{PATH}.agent_repo.get",
                new=AsyncMock(side_effect=[sender, outsider]),
            ),
            patch(f"{PATH}.resolve_access", new=AsyncMock(return_value=True)),
            pytest.raises(BadRequestError),
        ):
            await _service().react(
                _ctx(org, user),
                message_id=message.id,
                data=AgentMessageReactionCreate(emoji="👍", as_agent_id=outsider.id),
            )

    async def test_a_message_in_another_thread_is_not_found_there(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender, recipient = _agent(org, slug="researcher"), _agent(org, slug="summariser")
        thread = _thread(org, sender, recipient)
        message = _message(org, thread, sender)
        with (
            patch(
                f"{PATH}.agent_message_repo.get_message",
                new=AsyncMock(return_value=message),
            ),
            pytest.raises(NotFoundError),
        ):
            await _service().react(
                _ctx(org, user),
                message_id=message.id,
                data=AgentMessageReactionCreate(emoji="👍"),
                thread_id=uuid.uuid4(),
            )


class TestListing:
    async def test_a_thread_shows_unread_until_the_reader_opens_it(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender, recipient = _agent(org, slug="researcher"), _agent(org, slug="summariser")
        thread = _thread(org, sender, recipient)
        last = _message(org, thread, sender, body="the latest word")
        with (
            patch(f"{PATH}.visible_resource_ids", new=AsyncMock(return_value=None)),
            patch(
                f"{PATH}.agent_message_repo.list_threads",
                new=AsyncMock(return_value=([thread], 1)),
            ),
            patch(
                f"{PATH}.agent_repo.list_by_ids",
                new=AsyncMock(return_value=([sender, recipient], 2)),
            ),
            patch(
                f"{PATH}.agent_message_repo.latest_messages",
                new=AsyncMock(return_value={thread.id: last}),
            ),
            patch(f"{PATH}.agent_message_repo.get_reads", new=AsyncMock(return_value={})),
        ):
            listed = await _service().list_threads(_ctx(org, user))

        assert listed.total == 1
        row = listed.items[0]
        assert row.agent_a.slug == "researcher"
        assert row.agent_b.slug == "summariser"
        assert row.last_message is not None and row.last_message.body == "the latest word"
        assert row.unread is True

    async def test_recipients_are_the_published_agents_the_caller_may_run(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        runnable = _agent(org, slug="researcher")
        with (
            patch(
                f"{PATH}.visible_resource_ids",
                new=AsyncMock(return_value=[runnable.id]),
            ),
            patch(
                f"{PATH}.agent_repo.list_visible_ids",
                new=AsyncMock(return_value={runnable.id}),
            ),
            patch(
                f"{PATH}.agent_repo.list_by_ids",
                new=AsyncMock(return_value=([runnable], 1)),
            ),
        ):
            recipients = await _service().list_recipients(_ctx(org, user))

        assert [item.slug for item in recipients.items] == ["researcher"]

    async def test_marking_a_thread_read_records_the_watermark(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        sender, recipient = _agent(org, slug="researcher"), _agent(org, slug="summariser")
        thread = _thread(org, sender, recipient)
        marked = AsyncMock()
        with (
            patch(
                f"{PATH}.agent_message_repo.get_thread",
                new=AsyncMock(return_value=thread),
            ),
            patch(
                f"{PATH}.agent_repo.get",
                new=AsyncMock(return_value=sender),
            ),
            patch(f"{PATH}.resolve_access", new=AsyncMock(return_value=True)),
            patch(f"{PATH}.agent_message_repo.mark_read", new=marked),
        ):
            await _service().mark_read(_ctx(org, user), thread.id)

        marked.assert_awaited_once()
        assert marked.await_args.kwargs["thread_id"] == thread.id
        assert marked.await_args.kwargs["user_id"] == user
