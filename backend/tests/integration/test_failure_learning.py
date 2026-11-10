"""The failure-learning loop's rows, against Postgres.

A mock cannot tell whether a partial unique index actually prevents a
second open entry, whether a `CHECK` rejects a misshapen scope, whether a
deleted entry nulls the cases that named it, or whether the mining
group-by leaves test runs and delegations out. Those are the guarantees
the schema and the mining queries are supposed to provide, and the only
way to know is to ask Postgres.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.agent import Agent
from app.db.models.agent_run import AgentRun, RunStatus, RunSurface
from app.db.models.conversation import Conversation, Message
from app.db.models.failure_learning import (
    AgentEvalCase,
    AgentFailureLedger,
    AgentInstructionProposal,
)
from app.db.models.message_rating import MessageRating
from app.repositories import agent_run as agent_run_repo
from app.repositories import failure_learning as failure_learning_repo
from tests.integration.workflow_run_support import seed_member

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
_ERROR = "The run did not finish (ModelHTTPError, HTTP 400) - retry it."
_MODEL = "anthropic:claude-sonnet-4-5"


async def _agent(db, org) -> Agent:
    agent = Agent(
        id=uuid.uuid4(),
        organization_id=org.id,
        slug=f"clerk-{uuid.uuid4().hex[:8]}",
        name="Clerk",
        draft_spec={},
    )
    db.add(agent)
    await db.flush()
    return agent


async def _run(db, org, agent, **overrides) -> AgentRun:
    row = {
        "id": uuid.uuid4(),
        "organization_id": org.id,
        "agent_id": agent.id,
        "status": RunStatus.FAILED.value,
        "surface": RunSurface.WEB.value,
        "started_at": _NOW - timedelta(days=1),
        "model_label": _MODEL,
        "error": _ERROR,
    }
    row.update(overrides)
    run = AgentRun(**row)
    db.add(run)
    await db.flush()
    return run


async def _conversation(db, org) -> Conversation:
    conversation = Conversation(id=uuid.uuid4(), organization_id=org.id, user_id=None)
    db.add(conversation)
    await db.flush()
    return conversation


async def _entry(db, org, **overrides) -> AgentFailureLedger:
    row = {
        "id": uuid.uuid4(),
        "organization_id": org.id,
        "agent_id": None,
        "scope": "agent",
        "pattern_key": "failed:ModelHTTPError:400",
        "title": "400s",
        "summary": "Keeps failing.",
        "evidence": {"count": 3},
        "status": "draft",
    }
    row.update(overrides)
    entry = AgentFailureLedger(**row)
    db.add(entry)
    await db.flush()
    return entry


class TestMiningGroups:
    async def test_test_runs_and_delegations_are_not_mined(self, db) -> None:
        """The loop's own tries and a delegation's child runs are not failures to learn from."""
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        await _run(db, org, agent)
        await _run(db, org, agent, is_test=True)
        await _run(db, org, agent, parent_run_id=uuid.uuid4())
        await _run(db, org, agent, status=RunStatus.COMPLETED.value, error=None)

        groups = await agent_run_repo.failure_groups(
            db, organization_id=org.id, since=_NOW - timedelta(days=7), until=_NOW
        )

        assert len(groups) == 1
        assert groups[0].count == 1
        assert groups[0].status == "failed"
        assert groups[0].error == _ERROR

    async def test_a_group_outside_the_window_is_not_mined(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        await _run(db, org, agent, started_at=_NOW - timedelta(days=30))

        groups = await agent_run_repo.failure_groups(
            db, organization_id=org.id, since=_NOW - timedelta(days=7), until=_NOW
        )

        assert groups == []

    async def test_samples_are_the_newest_five_of_the_group(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        runs = [
            await _run(db, org, agent, started_at=_NOW - timedelta(days=day))
            for day in (6, 5, 4, 3, 2, 1)
        ]

        samples = await agent_run_repo.failure_group_samples(
            db,
            organization_id=org.id,
            agent_id=agent.id,
            model_label=_MODEL,
            status="failed",
            error=_ERROR,
        )

        assert samples == [runs[5].id, runs[4].id, runs[3].id, runs[2].id, runs[1].id]
        assert runs[0].id not in samples

    async def test_a_stalled_run_is_running_with_a_stale_heartbeat(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        stale = await _run(
            db,
            org,
            agent,
            status=RunStatus.RUNNING.value,
            error=None,
            heartbeat_at=_NOW - timedelta(minutes=30),
        )
        await _run(
            db,
            org,
            agent,
            status=RunStatus.RUNNING.value,
            error=None,
            heartbeat_at=_NOW - timedelta(minutes=1),
        )
        await _run(
            db,
            org,
            agent,
            status=RunStatus.FAILED.value,
            heartbeat_at=_NOW - timedelta(minutes=30),
        )

        stalled = await agent_run_repo.stalled_runs(
            db, organization_id=org.id, stale_before=_NOW - timedelta(minutes=10)
        )

        assert [run.id for run in stalled] == [stale.id]

    async def test_a_down_rated_answer_names_its_nearest_run(self, db) -> None:
        """The run that produced the answer is the latest one started before it."""
        user, org = await seed_member(db)
        agent = await _agent(db, org)
        conversation = await _conversation(db, org)
        earlier = await _run(
            db,
            org,
            agent,
            conversation_id=conversation.id,
            started_at=_NOW - timedelta(hours=2),
        )
        await _run(
            db,
            org,
            agent,
            conversation_id=conversation.id,
            started_at=_NOW - timedelta(hours=3),
        )
        message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            role="assistant",
            content="Here is the refund.",
            agent_id=agent.id,
            created_at=_NOW - timedelta(hours=1),
        )
        db.add(message)
        await db.flush()
        db.add(MessageRating(id=uuid.uuid4(), message_id=message.id, user_id=user.id, rating=-1))
        await db.flush()

        rows = await agent_run_repo.down_rated_messages(
            db, organization_id=org.id, since=_NOW - timedelta(days=7), until=_NOW
        )

        assert len(rows) == 1
        assert rows[0].run_id == earlier.id
        assert rows[0].message_id == message.id

    async def test_a_down_rated_answer_with_no_earlier_run_is_dropped(self, db) -> None:
        """Legacy messages written before any run was linked have no attribution."""
        user, org = await seed_member(db)
        agent = await _agent(db, org)
        conversation = await _conversation(db, org)
        message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            role="assistant",
            content="Here is the refund.",
            agent_id=agent.id,
            created_at=_NOW - timedelta(hours=1),
        )
        db.add(message)
        await db.flush()
        db.add(MessageRating(id=uuid.uuid4(), message_id=message.id, user_id=user.id, rating=-1))
        await db.flush()

        rows = await agent_run_repo.down_rated_messages(
            db, organization_id=org.id, since=_NOW - timedelta(days=7), until=_NOW
        )

        assert rows == []


class TestLedgerConstraints:
    async def test_a_second_open_entry_for_the_same_pattern_is_rejected(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        await _entry(db, org, agent_id=agent.id)

        with pytest.raises(IntegrityError):
            await _entry(db, org, agent_id=agent.id)
        await db.rollback()

    async def test_a_dismissed_entry_frees_the_pattern(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        await _entry(db, org, agent_id=agent.id, status="dismissed")

        entry = await _entry(db, org, agent_id=agent.id)

        assert entry.status == "draft"

    async def test_a_model_entry_and_an_agent_entry_do_not_collide(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        await _entry(db, org, agent_id=agent.id)
        model_entry = await _entry(db, org, agent_id=None, scope="model", model=_MODEL)

        assert model_entry.scope == "model"

    async def test_a_bad_scope_is_rejected(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        with pytest.raises(IntegrityError):
            await _entry(db, org, agent_id=agent.id, scope="everywhere")
        await db.rollback()

    async def test_an_agent_entry_without_an_agent_is_rejected(self, db) -> None:
        _, org = await seed_member(db)
        with pytest.raises(IntegrityError):
            await _entry(db, org, agent_id=None, scope="agent")
        await db.rollback()

    async def test_a_model_entry_with_an_agent_is_rejected(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        with pytest.raises(IntegrityError):
            await _entry(db, org, agent_id=agent.id, scope="model", model=_MODEL)
        await db.rollback()

    async def test_a_model_entry_without_a_model_is_rejected(self, db) -> None:
        _, org = await seed_member(db)
        with pytest.raises(IntegrityError):
            await _entry(db, org, agent_id=None, scope="model", model=None)
        await db.rollback()


class TestLedgerAndCaseRows:
    async def test_deleting_an_entry_nulls_its_cases(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        entry = await _entry(db, org, agent_id=agent.id)
        case = await failure_learning_repo.create_case(
            db,
            organization_id=org.id,
            agent_id=agent.id,
            ledger_entry_id=entry.id,
            name="Refund loop",
            position=1,
            input={"message": "Refund me"},
            expectation={"kind": "not_contains", "field": "text", "value": "draft"},
            model_profile_id=None,
            source_run_id=None,
            created_by_user_id=None,
        )

        await db.delete(entry)
        await db.flush()
        await db.refresh(case)

        assert case.ledger_entry_id is None

    async def test_deleting_a_source_run_nulls_the_case(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        run = await _run(db, org, agent)
        case = await failure_learning_repo.create_case(
            db,
            organization_id=org.id,
            agent_id=agent.id,
            ledger_entry_id=None,
            name="Refund loop",
            position=1,
            input={"message": "Refund me"},
            expectation={"kind": "not_contains", "field": "text", "value": "draft"},
            model_profile_id=None,
            source_run_id=run.id,
            created_by_user_id=None,
        )

        await db.delete(run)
        await db.flush()
        await db.refresh(case)

        assert case.source_run_id is None

    async def test_a_model_entry_reads_through_any_agent(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        entry = await _entry(db, org, agent_id=None, scope="model", model=_MODEL)

        found = await failure_learning_repo.get_entry(
            db, entry.id, organization_id=org.id, agent_id=agent.id
        )

        assert found is not None
        assert found.id == entry.id

    async def test_cases_list_in_position_order(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        for position in (2, 1):
            await failure_learning_repo.create_case(
                db,
                organization_id=org.id,
                agent_id=agent.id,
                ledger_entry_id=None,
                name=f"Case {position}",
                position=position,
                input={"message": "Refund me"},
                expectation={"kind": "not_contains", "field": "text", "value": "draft"},
                model_profile_id=None,
                source_run_id=None,
                created_by_user_id=None,
            )

        cases = await failure_learning_repo.list_cases(
            db, organization_id=org.id, agent_id=agent.id
        )

        assert [case.position for case in cases] == [1, 2]
        assert isinstance(cases[0], AgentEvalCase)


async def _proposal(db, org, agent, **overrides):
    row = {
        "id": uuid.uuid4(),
        "organization_id": org.id,
        "agent_id": agent.id,
        "target_type": "instructions",
        "old_text": "Draft a PR.",
        "new_text": "Never draft a PR.",
        "rationale": "The agent drafts when told to publish.",
        "status": "draft",
    }
    row.update(overrides)
    proposal = AgentInstructionProposal(**row)
    db.add(proposal)
    await db.flush()
    return proposal


class TestProposalRows:
    async def test_a_try_names_its_proposal(self, db) -> None:
        """Migration 0305's foreign key: a try records the proposal it tried."""
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        proposal = await _proposal(db, org, agent)

        run = await failure_learning_repo.create_run(
            db,
            organization_id=org.id,
            agent_id=agent.id,
            proposal_id=proposal.id,
            started_by_user_id=None,
            cases=[],
        )

        assert run.proposal_id == proposal.id

    async def test_deleting_a_proposal_nulls_the_try_and_the_entry(self, db) -> None:
        """Both deferred foreign keys are SET NULL, so a proposal can go
        without taking its try or its entry with it."""
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        proposal = await _proposal(db, org, agent)
        run = await failure_learning_repo.create_run(
            db,
            organization_id=org.id,
            agent_id=agent.id,
            proposal_id=proposal.id,
            started_by_user_id=None,
            cases=[],
        )
        entry = await _entry(
            db, org, agent_id=agent.id, status="resolved", resolved_proposal_id=proposal.id
        )

        await db.delete(proposal)
        await db.flush()
        await db.refresh(run)
        await db.refresh(entry)

        assert run.proposal_id is None
        assert entry.resolved_proposal_id is None

    async def test_a_skill_proposal_without_a_skill_is_rejected(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        with pytest.raises(IntegrityError):
            await _proposal(db, org, agent, target_type="skill", skill_id=None)
        await db.rollback()

    async def test_an_instructions_proposal_naming_a_skill_is_rejected(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        with pytest.raises(IntegrityError):
            await _proposal(db, org, agent, target_type="instructions", skill_id=uuid.uuid4())
        await db.rollback()

    async def test_a_bad_status_is_rejected(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        with pytest.raises(IntegrityError):
            await _proposal(db, org, agent, status="pending")
        await db.rollback()

    async def test_proposals_list_by_status(self, db) -> None:
        _, org = await seed_member(db)
        agent = await _agent(db, org)
        await _proposal(db, org, agent)
        await _proposal(db, org, agent, status="applied")

        applied = await failure_learning_repo.list_proposals(
            db, organization_id=org.id, agent_id=agent.id, status="applied"
        )
        all_proposals = await failure_learning_repo.list_proposals(
            db, organization_id=org.id, agent_id=agent.id
        )

        assert len(applied) == 1
        assert applied[0].status == "applied"
        assert len(all_proposals) == 2
