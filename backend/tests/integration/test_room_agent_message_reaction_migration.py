"""Execute the 0352 reaction migration directly against PostgreSQL."""

import importlib.util
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, inspect, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.db.base import Base
from app.db.models.room_agent_message_reaction import RoomAgentMessageReaction
from app.db.models.room_submission import RoomSubmission
from tests.integration.test_room_submissions import post, seed

pytestmark = [pytest.mark.anyio, pytest.mark.security]

_TABLES = ("room_agent_message_reactions",)
_PATH = Path(__file__).parents[2] / "alembic" / "versions" / "0352_room_agent_reactions.py"
_SPEC = importlib.util.spec_from_file_location("room_agent_message_reaction_migration", _PATH)
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


def _reaction_schema_diff(connection: Connection) -> list:
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


async def test_reaction_revision_upgrade_and_empty_downgrade_match_the_model(
    engine: AsyncEngine,
) -> None:
    async with engine.connect() as connection:
        expected = await connection.run_sync(_schema)
        try:
            await _step(connection, "downgrade")
            remaining = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            assert _TABLES[0] not in remaining

            await _step(connection, "upgrade")
            assert await connection.run_sync(_schema) == expected
            assert await connection.run_sync(_reaction_schema_diff) == []
            await _step(connection, "downgrade")
        finally:
            present = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            if _TABLES[0] not in present:
                await _step(connection, "upgrade")


async def test_reaction_downgrade_refuses_retained_action_without_changing_schema(
    db: AsyncSession,
) -> None:
    room = await seed(db)
    posted = await post(db, room, "retain-agent-reaction", agent=False)
    submission = await db.scalar(
        select(RoomSubmission).where(
            RoomSubmission.message_id == posted.message.id,
            RoomSubmission.parent_submission_id.is_(None),
        )
    )
    assert submission is not None
    db.add(
        RoomAgentMessageReaction(
            id=uuid4(),
            organization_id=room.ctx.organization_id,
            conversation_id=room.conversation.id,
            message_id=posted.message.id,
            agent_id=room.agent.id,
            agent_name=room.agent.name,
            agent_version_id=room.version.id,
            run_id=uuid4(),
            source="tool",
            submission_id=submission.id,
            decision_id=None,
            tool_call_id="retained-call",
            reaction_only=False,
            emoji="👍",
        )
    )
    await db.flush()

    connection = await db.connection()
    before = await connection.run_sync(_schema)
    with pytest.raises(RuntimeError, match="Cannot discard"):
        await connection.run_sync(lambda sync: _migrate(sync, "downgrade"))
    assert await connection.run_sync(_schema) == before
    assert (
        await db.scalar(
            select(RoomAgentMessageReaction.id).where(
                RoomAgentMessageReaction.tool_call_id == "retained-call"
            )
        )
        is not None
    )
