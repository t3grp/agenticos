"""Execute migration0351 against PostgreSQL and preserve policy/decision evidence on rollback."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.db.base import Base
from app.db.models.conversation import Message
from app.db.models.room_auto_routing import RoomAutoRoutingDecision, RoomAutoRoutingPolicy
from app.db.models.room_submission import RoomSubmission
from tests.integration.test_room_submissions import seed

pytestmark = [pytest.mark.anyio, pytest.mark.security]

_TABLES = ("room_auto_routing_policies", "room_auto_routing_decisions")
_PATH = Path(__file__).parents[2] / "alembic" / "versions" / "0352_room_auto_routing.py"
_SPEC = importlib.util.spec_from_file_location("room_auto_routing_migration", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)


def _migrate(connection: Connection, direction: Literal["upgrade", "downgrade"]) -> None:
    context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
    with context.begin_transaction(), Operations.context(context):
        getattr(_MIGRATION, direction)()


async def _step(connection: AsyncConnection, direction: Literal["upgrade", "downgrade"]) -> None:
    await connection.commit()
    await connection.run_sync(lambda sync: _migrate(sync, direction))


def _schema(connection: Connection) -> dict[str, dict[str, object]]:
    inspector = inspect(connection)
    return {
        table: {
            "columns": sorted(
                (item["name"], str(item["type"]), item["nullable"], item["default"])
                for item in inspector.get_columns(table)
            ),
            "primary_key": inspector.get_pk_constraint(table),
            "unique_constraints": sorted(
                inspector.get_unique_constraints(table), key=lambda item: str(item["name"])
            ),
            "foreign_keys": sorted(
                inspector.get_foreign_keys(table), key=lambda item: str(item["name"])
            ),
            "checks": sorted(
                inspector.get_check_constraints(table), key=lambda item: str(item["name"])
            ),
            "indexes": sorted(inspector.get_indexes(table), key=lambda item: str(item["name"])),
        }
        for table in _TABLES
    }


def _routing_schema_diff(connection: Connection) -> list:
    def include_object(_object, name, object_type, _reflected, _compare_to):
        return object_type != "table" or name in _TABLES

    context = MigrationContext.configure(
        connection,
        opts={
            "target_metadata": Base.metadata,
            "include_object": include_object,
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    return compare_metadata(context, Base.metadata)


async def test_routing_revision_upgrade_and_empty_downgrade_match_the_models(
    engine: AsyncEngine,
) -> None:
    async with engine.connect() as connection:
        expected = await connection.run_sync(_schema)
        try:
            await _step(connection, "downgrade")
            remaining = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            assert not set(_TABLES) & remaining

            await _step(connection, "upgrade")
            assert await connection.run_sync(_schema) == expected
            assert await connection.run_sync(_routing_schema_diff) == []
            await _step(connection, "downgrade")
        finally:
            present = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            if not set(_TABLES) & present:
                await _step(connection, "upgrade")


@pytest.mark.parametrize("evidence", ["policy", "decision"])
async def test_routing_downgrade_refuses_retained_state_without_changing_schema(
    db: AsyncSession, evidence: str
) -> None:
    room = await seed(db)
    if evidence == "policy":
        db.add(
            RoomAutoRoutingPolicy(
                organization_id=room.ctx.organization_id,
                conversation_id=room.conversation.id,
                enabled=False,
                revision=1,
                quiet_window_ms=2500,
                max_responders=1,
            )
        )
    else:
        message = Message(
            id=uuid4(),
            conversation_id=room.conversation.id,
            author_user_id=room.user.id,
            role="user",
            content="Accepted before rollback",
        )
        db.add(message)
        await db.flush()
        submission = RoomSubmission(
            id=uuid4(),
            organization_id=room.ctx.organization_id,
            conversation_id=room.conversation.id,
            requester_id=room.user.id,
            message_id=message.id,
            dispatch="automatic",
            client_message_id=uuid4().hex,
            fingerprint="0" * 64,
            intent="message",
            status="posted",
        )
        db.add(submission)
        await db.flush()
        accepted_at = datetime.now(UTC)
        db.add(
            RoomAutoRoutingDecision(
                organization_id=room.ctx.organization_id,
                conversation_id=room.conversation.id,
                submission_id=submission.id,
                accepted_message_id=message.id,
                requester_id=room.user.id,
                accepted_at=accepted_at,
                ready_at=accepted_at,
                policy_revision=1,
                router_agent_id=room.agent.id,
                router_name="Room router",
                router_version_id=room.version.id,
                model_profile_id=uuid4(),
                model_profile_fingerprint="0" * 64,
                quiet_window_ms=2500,
                max_responders=1,
                candidate_snapshot=[],
                grouped_message_ids=[str(message.id)],
                status="pending",
                claim_generation=0,
            )
        )
    await db.flush()

    connection = await db.connection()
    before = await connection.run_sync(_schema)
    with pytest.raises(RuntimeError, match="Cannot discard automatic routing"):
        await connection.run_sync(lambda sync: _migrate(sync, "downgrade"))
    assert await connection.run_sync(_schema) == before
