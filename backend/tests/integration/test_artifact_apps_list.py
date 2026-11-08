"""The Apps list the sidebar reads, against a real database.

`GET /apps` - the call behind the sidebar's Apps section and the Apps page - goes
to `ArtifactService.list_readable`. For a member of the organization, what comes
back must be their own pages, the organization-wide ones, and the ones shared
with them: never another member's private page. This test publishes both kinds
from a second member and reads the list as the first.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent
from app.db.models.artifact import Artifact, ArtifactMediaType
from app.db.models.organization import Organization, OrganizationMember
from app.db.models.resource_grant import Visibility
from app.db.models.user import User
from app.services import artifact as artifacts
from app.services.artifact import ArtifactService
from app.services.file_storage import LocalFileStorage
from tests.integration.virtual_table_support import ctx_for

pytestmark = pytest.mark.anyio


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[LocalFileStorage]:
    local = LocalFileStorage(tmp_path)
    with (
        patch("app.services.artifact.get_file_storage", return_value=local),
        patch("app.services.file_storage.get_file_storage", return_value=local),
    ):
        yield local


async def _user(db: AsyncSession, label: str) -> User:
    user = User(email=f"{label}-{uuid.uuid4()}@example.com", hashed_password="x", full_name=label)
    db.add(user)
    await db.flush()
    return user


async def _publish(
    db: AsyncSession,
    organization: Organization,
    agent: Agent,
    owner: User,
    name: str,
    visibility: Visibility,
) -> uuid.UUID:
    published = await artifacts.publish_with(
        db,
        organization_id=organization.id,
        agent_id=agent.id,
        owner_user_id=owner.id,
        run_id=None,
        name=name,
        title=name.replace("-", " ").title(),
        media_type=ArtifactMediaType.HTML,
        data=b"<h1>app</h1>",
    )
    row = await db.get(Artifact, published.artifact_id)
    assert row is not None
    row.visibility = visibility.value
    await db.flush()
    return row.id


async def test_a_member_lists_org_wide_apps_and_not_another_members_private_app(
    db: AsyncSession, storage: LocalFileStorage
) -> None:
    reader = await _user(db, "reader")
    author = await _user(db, "author")
    organization = Organization(
        name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}", created_by_user_id=author.id
    )
    db.add(organization)
    await db.flush()
    for person in (reader, author):
        db.add(OrganizationMember(organization_id=organization.id, user_id=person.id, role="member"))
    agent = Agent(
        organization_id=organization.id,
        name="Reporter",
        created_by_user_id=author.id,
        slug=f"a-{uuid.uuid4().hex[:8]}",
    )
    db.add(agent)
    await db.flush()

    private_id = await _publish(db, organization, agent, author, "author-private", Visibility.PRIVATE)
    org_id = await _publish(db, organization, agent, author, "author-org", Visibility.ORG)

    ctx = ctx_for(reader, organization, role="member")
    listed = await ArtifactService(db).list_readable(ctx, limit=8)
    ids = {item.id for item in listed.items}

    assert org_id in ids
    assert private_id not in ids
    assert listed.total == 1
