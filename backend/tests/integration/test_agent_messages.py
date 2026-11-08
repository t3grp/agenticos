"""Agent messages on real rows: the pair is one thread, reactions toggle, and
one organization cannot read another's threads.

A mock cannot tell you whether the canonical-pair CHECK rejects a row, whether
deleting an agent takes its threads with it, or whether the partial unique
index actually prevents a second identical reaction. Those are the guarantees
the schema is supposed to provide, and the only way to know is to ask Postgres.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.permissions import AuthContext, OrgRoleName
from app.db.models.agent import Agent
from app.repositories import agent_message_repo
from app.schemas.agent_message import AgentMessageReactionCreate, AgentMessageSend
from app.services.agent_messages import AgentMessageService
from tests.integration.virtual_table_support import make_org, make_user

pytestmark = pytest.mark.anyio


async def _agent(db: AsyncSession, org_id: uuid.UUID, *, slug: str) -> Agent:
    agent = Agent(
        organization_id=org_id,
        slug=slug,
        name=slug.replace("-", " ").title(),
        status="published",
    )
    db.add(agent)
    await db.flush()
    return agent


class TestThreads:
    async def test_the_pair_shares_one_thread_whichever_way_it_is_addressed(
        self, db: AsyncSession
    ) -> None:
        owner = await make_user(db)
        org = await make_org(db, owner=owner)
        first = await _agent(db, org.id, slug="researcher")
        second = await _agent(db, org.id, slug="summariser")
        ctx = AuthContext(
            user_id=owner.id, organization_id=org.id, role=OrgRoleName.OWNER
        )
        service = AgentMessageService(db)

        sent = await service.send(
            ctx,
            AgentMessageSend(
                sender_agent_id=first.id, recipient=second.slug, body="hello"
            ),
        )
        back = await service.send(
            ctx,
            AgentMessageSend(
                sender_agent_id=second.id, recipient=first.slug, body="hello back"
            ),
        )

        assert sent.thread_id == back.thread_id
        threads = await agent_message_repo.list_threads(
            db, organization_id=org.id, agent_ids=None
        )
        assert len(threads[0]) == 1

    async def test_a_thread_in_another_organization_reads_as_absent(
        self, db: AsyncSession
    ) -> None:
        owner = await make_user(db)
        org = await make_org(db, owner=owner)
        first = await _agent(db, org.id, slug="researcher")
        second = await _agent(db, org.id, slug="summariser")
        ctx = AuthContext(
            user_id=owner.id, organization_id=org.id, role=OrgRoleName.OWNER
        )
        service = AgentMessageService(db)
        sent = await service.send(
            ctx,
            AgentMessageSend(
                sender_agent_id=first.id, recipient=second.slug, body="hello"
            ),
        )

        stranger = await make_user(db)
        other_org = await make_org(db, owner=stranger)
        foreign = AuthContext(
            user_id=stranger.id,
            organization_id=other_org.id,
            role=OrgRoleName.OWNER,
        )

        with pytest.raises(NotFoundError):
            await service.get_thread(foreign, sent.thread_id)

    async def test_deleting_an_agent_takes_its_threads_with_it(
        self, db: AsyncSession
    ) -> None:
        owner = await make_user(db)
        org = await make_org(db, owner=owner)
        first = await _agent(db, org.id, slug="researcher")
        second = await _agent(db, org.id, slug="summariser")
        ctx = AuthContext(
            user_id=owner.id, organization_id=org.id, role=OrgRoleName.OWNER
        )
        service = AgentMessageService(db)
        sent = await service.send(
            ctx,
            AgentMessageSend(
                sender_agent_id=first.id, recipient=second.slug, body="hello"
            ),
        )

        await db.delete(first)
        await db.flush()

        assert (
            await agent_message_repo.get_thread(db, sent.thread_id) is None
        )


class TestReactions:
    async def test_the_same_emoji_twice_is_one_reaction_then_none(
        self, db: AsyncSession
    ) -> None:
        owner = await make_user(db)
        org = await make_org(db, owner=owner)
        first = await _agent(db, org.id, slug="researcher")
        second = await _agent(db, org.id, slug="summariser")
        ctx = AuthContext(
            user_id=owner.id, organization_id=org.id, role=OrgRoleName.OWNER
        )
        service = AgentMessageService(db)
        sent = await service.send(
            ctx,
            AgentMessageSend(
                sender_agent_id=first.id, recipient=second.slug, body="hello"
            ),
        )

        added = await service.react(
            ctx,
            message_id=sent.id,
            data=AgentMessageReactionCreate(emoji="👍"),
        )
        assert added.reacted is True
        assert len(added.reactions) == 1

        taken_back = await service.react(
            ctx,
            message_id=sent.id,
            data=AgentMessageReactionCreate(emoji="👍"),
        )
        assert taken_back.reacted is False
        assert taken_back.reactions == []
        assert (
            await agent_message_repo.list_reactions(db, message_ids=[sent.id])
            == []
        )
