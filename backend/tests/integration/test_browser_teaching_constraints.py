"""The database allows one active teaching recording per managed browser."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent
from app.db.models.agent_run import AgentRun
from app.db.models.browser_teaching import BrowserTeachingSession
from app.db.models.organization import Organization
from app.db.models.user import User

pytestmark = pytest.mark.anyio

_INDEX = "uq_browser_teaching_recording_per_browser"
_MIGRATION_PATH = (
    Path(__file__).parents[2] / "alembic" / "versions" / "0332_browser_teaching_recording_guard.py"
)


def _migration_module() -> ModuleType:
    spec = spec_from_file_location("browser_teaching_recording_guard", _MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load browser teaching recording migration.")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply_migration_cycle(connection: Connection) -> None:
    module = _migration_module()
    upgrade = cast(Callable[[], None], module.upgrade)
    downgrade = cast(Callable[[], None], module.downgrade)
    with Operations.context(MigrationContext.configure(connection)):
        from alembic import op

        op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
        try:
            upgrade()
            upgrade()
            assert _INDEX in {
                index["name"]
                for index in inspect(connection).get_indexes("browser_teaching_sessions")
            }
            downgrade()
            downgrade()
            assert _INDEX not in {
                index["name"]
                for index in inspect(connection).get_indexes("browser_teaching_sessions")
            }
        finally:
            upgrade()


def _session(
    *,
    organization_id: UUID,
    owner_user_id: UUID,
    agent_id: UUID,
    run_id: UUID,
    status: str,
) -> BrowserTeachingSession:
    now = datetime.now(UTC)
    return BrowserTeachingSession(
        id=uuid4(),
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        agent_id=agent_id,
        run_id=run_id,
        call_id="synthetic-browser-call",
        lease_id=uuid4().hex,
        idempotency_key=uuid4().hex,
        origin="https://portal.example.test",
        status=status,
        revision=1,
        events=[],
        parameters=[],
        expires_at=now + timedelta(hours=1),
        capture_until=now + timedelta(minutes=5),
    )


async def test_only_one_active_teaching_recording_exists_per_browser(db: AsyncSession) -> None:
    user = User(email=f"{uuid4().hex}@example.com", hashed_password="synthetic", is_active=True)
    db.add(user)
    await db.flush()
    organization = Organization(
        name="Teaching constraints",
        slug=f"teaching-{uuid4().hex[:12]}",
        created_by_user_id=user.id,
    )
    db.add(organization)
    await db.flush()
    agent = Agent(
        id=uuid4(),
        organization_id=organization.id,
        name="Teaching test",
        slug=f"teaching-{uuid4().hex[:12]}",
    )
    db.add(agent)
    await db.flush()
    run = AgentRun(
        id=uuid4(),
        organization_id=organization.id,
        agent_id=agent.id,
        status="running",
        input_tokens=0,
    )
    db.add(run)
    await db.flush()

    active = _session(
        organization_id=organization.id,
        owner_user_id=user.id,
        agent_id=agent.id,
        run_id=run.id,
        status="recording",
    )
    db.add(active)
    await db.flush()

    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                _session(
                    organization_id=organization.id,
                    owner_user_id=user.id,
                    agent_id=agent.id,
                    run_id=run.id,
                    status="recording",
                )
            )
            await db.flush()

    paused = _session(
        organization_id=organization.id,
        owner_user_id=user.id,
        agent_id=agent.id,
        run_id=run.id,
        status="paused",
    )
    db.add(paused)
    await db.flush()


async def test_recording_guard_migration_upgrade_and_downgrade_are_idempotent(
    db: AsyncSession,
) -> None:
    connection = await db.connection()
    await connection.run_sync(_apply_migration_cycle)
