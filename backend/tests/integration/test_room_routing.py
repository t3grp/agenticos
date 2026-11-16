"""Real PostgreSQL room handoffs: one task, one job, and current audiences."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.background import discard_deferred
from app.core.exceptions import AlreadyExistsError, BadRequestError, NotFoundError
from app.db.models.background_job import BackgroundJob
from app.db.models.room_routing import RoomRoutingProposal, RoomTaskLink
from app.repositories import room_submission as submission_repo
from app.schemas.room_routing import (
    AgentPin,
    HandoffItem,
    LeadPolicyUpdate,
    RoutingDecision,
    RoutingOutput,
)
from app.schemas.room_submission import RoomMessageCreate
from app.schemas.task import BoardEnsure
from app.services.room_routing import RoomRoutingService
from app.services.room_submission import RoomSubmissionService
from app.services.tasks import TaskService
from tests.integration.test_room_submissions import seed

pytestmark = pytest.mark.anyio


async def prepared(db):
    room = await seed(db)
    pin = AgentPin(agent_id=room.agent.id, version_id=room.version.id)
    routing = RoomRoutingService(db)
    await TaskService(db).ensure_board(
        room.ctx, BoardEnsure(scope="project", scope_id=room.conversation.project_id)
    )
    policy = await routing.update_policy(
        room.ctx,
        room.conversation.id,
        LeadPolicyUpdate(expected_revision=0, enabled=True, lead=pin, specialists=[pin]),
    )
    posted = await RoomSubmissionService(db).post(
        room.ctx,
        room.conversation.id,
        RoomMessageCreate(
            client_message_id="ask-lead", content="Review this synthetic batch", intent="ask_lead"
        ),
        room.user,
    )
    source = await submission_repo.get(
        db, posted.request.id, organization_id=room.ctx.organization_id
    )
    # Synthetic completion: no provider, model, worker, or external message is run.
    source.status = "completed"
    from app.db.models.agent_run import AgentRun, RunStatus, RunSurface

    run = AgentRun(
        id=uuid4(),
        organization_id=room.ctx.organization_id,
        agent_id=room.agent.id,
        agent_version_id=room.version.id,
        user_id=room.user.id,
        conversation_id=room.conversation.id,
        surface=RunSurface.WEB.value,
        status=RunStatus.COMPLETED.value,
        input_tokens=0,
        output_tokens=0,
    )
    db.add(run)
    await db.flush()
    source.run_id = run.id
    await db.flush()
    item = HandoffItem(
        item_id=uuid4(),
        title="Review synthetic records",
        expected_result="List discrepancies for human review",
        specialist=pin,
        accountable_user_id=room.user.id,
        reviewer_user_id=room.user.id,
    )
    await routing.completed(
        source, RoutingOutput(answer="Please review", items=[item]).model_dump_json()
    )
    proposal = await db.scalar(
        select(RoomRoutingProposal).where(RoomRoutingProposal.source_submission_id == source.id)
    )
    return room, proposal, item, policy


async def test_two_concurrent_acceptors_and_lost_ack_create_one_dispatch(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        room, proposal, item, _ = await prepared(db)
        await db.commit()
        discard_deferred(db)
    decision = RoutingDecision(
        expected_revision=1, idempotency_key="same-lost-ack", item_ids=[item.item_id]
    )

    async def accept():
        async with factory() as db:
            result = await RoomRoutingService(db).decide(
                room.ctx, room.conversation.id, proposal.id, decision, accept=True
            )
            await db.commit()
            discard_deferred(db)
            return result.status

    assert await asyncio.gather(accept(), accept()) == ["accepted", "accepted"]
    assert await accept() == "accepted"
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(RoomTaskLink)) == 1
        assert (
            await db.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(BackgroundJob.kind == "fire-trigger")
            )
            == 1
        )
        cards = await RoomRoutingService(db).read(room.ctx, room.conversation.id, None)
        assert len(cards.tasks) == 1 and cards.tasks[0].status == "planned"
        payload = cards.tasks[0].model_dump()
        assert "outcome_id" not in payload and "conversation_id" not in payload


async def test_another_organization_cannot_read_or_accept_a_room_proposal(db):
    room, proposal, item, _ = await prepared(db)
    other = await seed(db)
    with pytest.raises(NotFoundError):
        await RoomRoutingService(db).read(other.ctx, room.conversation.id, None)
    with pytest.raises(NotFoundError):
        await RoomRoutingService(db).decide(
            other.ctx,
            room.conversation.id,
            proposal.id,
            RoutingDecision(
                expected_revision=1, idempotency_key="foreign", item_ids=[item.item_id]
            ),
            accept=True,
        )


async def test_changed_policy_refuses_real_acceptance_without_creating_a_task(db):
    room, proposal, item, policy = await prepared(db)
    await RoomRoutingService(db).update_policy(
        room.ctx,
        room.conversation.id,
        LeadPolicyUpdate(expected_revision=policy.revision, enabled=False),
    )
    with pytest.raises((AlreadyExistsError, BadRequestError)):
        await RoomRoutingService(db).decide(
            room.ctx,
            room.conversation.id,
            proposal.id,
            RoutingDecision(expected_revision=1, idempotency_key="stale", item_ids=[item.item_id]),
            accept=True,
        )
    assert await db.scalar(select(func.count()).select_from(RoomTaskLink)) == 0


async def test_migration_body_upgrades_and_downgrades_after_role_navigation(db):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    filename = Path(__file__).resolve().parents[2] / "alembic/versions/0331_room_lead_handoffs.py"
    spec = importlib.util.spec_from_file_location("room_handoff_migration", filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0331" and migration.down_revision == "0330_role_navigation"

    def cycle(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert "room_task_links" not in inspect(connection).get_table_names()
            migration.upgrade()
            assert "room_task_links" in inspect(connection).get_table_names()
            names = {
                column["name"] for column in inspect(connection).get_columns("room_submissions")
            }
            assert {"ask_lead", "lead_policy_revision"} <= names

    await (await db.connection()).run_sync(cycle)


async def test_downgrade_refuses_to_drop_accepted_dispatch_provenance(db):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    room, proposal, item, _ = await prepared(db)
    await RoomRoutingService(db).decide(
        room.ctx,
        room.conversation.id,
        proposal.id,
        RoutingDecision(expected_revision=1, idempotency_key="retain", item_ids=[item.item_id]),
        accept=True,
    )
    filename = Path(__file__).resolve().parents[2] / "alembic/versions/0331_room_lead_handoffs.py"
    spec = importlib.util.spec_from_file_location("room_handoff_migration_refusal", filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def refuse(connection):
        with (
            Operations.context(MigrationContext.configure(connection)),
            pytest.raises(RuntimeError, match="Reconcile accepted room handoffs"),
        ):
            migration.downgrade()

    await (await db.connection()).run_sync(refuse)
    assert await db.scalar(select(func.count()).select_from(RoomTaskLink)) == 1


async def test_database_rejects_cross_room_proposal_and_foreign_task_bindings(db):
    from sqlalchemy.exc import IntegrityError

    room, proposal, item, _ = await prepared(db)
    other = await seed(db)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            invalid = RoomRoutingProposal(
                id=uuid4(),
                organization_id=other.ctx.organization_id,
                conversation_id=other.conversation.id,
                source_submission_id=proposal.source_submission_id,
                requester_id=other.user.id,
                policy_revision=1,
                revision=1,
                status="pending",
                items=[],
            )
            db.add(invalid)
            await db.flush()
    assert await db.scalar(select(func.count()).select_from(RoomRoutingProposal)) == 1

    from app.schemas.task import TaskCreate

    foreign = await TaskService(db).create(
        other.ctx,
        TaskCreate(scope="project", scope_id=other.conversation.project_id, title="Foreign task"),
    )
    with pytest.raises(IntegrityError, match="fk_room_task_tenant"):
        async with db.begin_nested():
            db.add(
                RoomTaskLink(
                    task_id=foreign.id,
                    organization_id=room.ctx.organization_id,
                    conversation_id=room.conversation.id,
                    proposal_id=proposal.id,
                    item_id=item.item_id,
                    requester_id=room.user.id,
                    reviewer_user_id=room.user.id,
                    agent_id=room.agent.id,
                    version_id=room.version.id,
                    accepted_revision=1,
                )
            )
            await db.flush()
