"""The Apps list the sidebar reads, against a real database.

`GET /apps` - the call behind the sidebar's Apps section and the Apps page - goes
to `ArtifactService.list_readable`. For a member of the organization, what comes
back must be their own pages, the organization-wide ones, and the ones shared
with them: never another member's private page. This test publishes both kinds
from a second member and reads the list as the first.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent
from app.db.models.artifact import Artifact, ArtifactMediaType
from app.db.models.group import Group, GroupMember
from app.db.models.organization import Organization, OrganizationMember
from app.db.models.resource_grant import GrantLevel, ResourceGrant, Visibility
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
        db.add(
            OrganizationMember(organization_id=organization.id, user_id=person.id, role="member")
        )
    agent = Agent(
        organization_id=organization.id,
        name="Reporter",
        created_by_user_id=author.id,
        slug=f"a-{uuid.uuid4().hex[:8]}",
    )
    db.add(agent)
    await db.flush()

    private_id = await _publish(
        db, organization, agent, author, "author-private", Visibility.PRIVATE
    )
    org_id = await _publish(db, organization, agent, author, "author-org", Visibility.ORG)

    ctx = ctx_for(reader, organization, role="member")
    listed = await ArtifactService(db).list_readable(ctx, limit=8)
    ids = {item.id for item in listed.items}

    assert org_id in ids
    assert private_id not in ids
    assert listed.total == 1

    sidebar = await ArtifactService(db).list_readable(ctx, mine_or_shared=True, limit=8)
    assert {item.id for item in sidebar.items} == {org_id}


async def test_an_admin_sidebar_list_leaves_out_another_members_private_app(
    db: AsyncSession, storage: LocalFileStorage
) -> None:
    """The admin's full list has the private page; the sidebar's `mine_or_shared` list does not."""
    admin = await _user(db, "admin")
    author = await _user(db, "author")
    organization = Organization(
        name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}", created_by_user_id=author.id
    )
    db.add(organization)
    await db.flush()
    for person, role in ((admin, "admin"), (author, "member")):
        db.add(OrganizationMember(organization_id=organization.id, user_id=person.id, role=role))
    agent = Agent(
        organization_id=organization.id,
        name="Reporter",
        created_by_user_id=author.id,
        slug=f"a-{uuid.uuid4().hex[:8]}",
    )
    db.add(agent)
    await db.flush()

    private_id = await _publish(
        db, organization, agent, author, "author-private", Visibility.PRIVATE
    )
    org_id = await _publish(db, organization, agent, author, "author-org", Visibility.ORG)

    ctx = ctx_for(admin, organization, role="admin")
    full = await ArtifactService(db).list_readable(ctx, limit=8)
    sidebar = await ArtifactService(db).list_readable(ctx, mine_or_shared=True, limit=8)

    assert {item.id for item in full.items} >= {private_id, org_id}
    assert private_id not in {item.id for item in sidebar.items}
    assert org_id in {item.id for item in sidebar.items}
    assert sidebar.total == 1


async def _workspace(
    db: AsyncSession, author: User, people: list[tuple[User, str]]
) -> tuple[Organization, Agent]:
    """An organization with the author and the given (member, role) pairs, and one agent."""
    organization = Organization(
        name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}", created_by_user_id=author.id
    )
    db.add(organization)
    await db.flush()
    for person, role in [(author, "member"), *people]:
        db.add(OrganizationMember(organization_id=organization.id, user_id=person.id, role=role))
    agent = Agent(
        organization_id=organization.id,
        name="Reporter",
        created_by_user_id=author.id,
        slug=f"a-{uuid.uuid4().hex[:8]}",
    )
    db.add(agent)
    await db.flush()
    return organization, agent


async def test_a_team_visible_app_is_listed_only_for_roles_that_reach_team_pages(
    db: AsyncSession, storage: LocalFileStorage
) -> None:
    """Team visibility is a role reach: ALL roles open the page, SHARED ones do not."""
    author = await _user(db, "author")
    member = await _user(db, "member")
    admin = await _user(db, "admin")
    organization, agent = await _workspace(db, author, [(member, "member"), (admin, "admin")])
    team_id = await _publish(db, organization, agent, author, "author-team", Visibility.TEAM)

    member_sidebar = await ArtifactService(db).list_readable(
        ctx_for(member, organization, role="member"), mine_or_shared=True, limit=8
    )
    assert team_id not in {item.id for item in member_sidebar.items}

    admin_ctx = ctx_for(admin, organization, role="admin")
    admin_sidebar = await ArtifactService(db).list_readable(admin_ctx, mine_or_shared=True, limit=8)
    assert {item.id for item in admin_sidebar.items} == {team_id}


async def test_a_group_granted_app_is_listed_for_its_members_only(
    db: AsyncSession, storage: LocalFileStorage
) -> None:
    """An app shared with a team (a group) is in that team's sidebar and not in anyone else's."""
    author = await _user(db, "author")
    teammate = await _user(db, "teammate")
    outsider = await _user(db, "outsider")
    organization, agent = await _workspace(db, author, [(teammate, "member"), (outsider, "member")])
    team = Group(organization_id=organization.id, name="Team T", created_by_user_id=author.id)
    db.add(team)
    await db.flush()
    db.add(GroupMember(group_id=team.id, user_id=teammate.id))
    private_id = await _publish(db, organization, agent, author, "for-team-t", Visibility.PRIVATE)
    db.add(
        ResourceGrant(
            organization_id=organization.id,
            subject_group_id=team.id,
            resource_type="artifact",
            resource_id=private_id,
            level=GrantLevel.READ.value,
            created_by_user_id=author.id,
        )
    )
    await db.flush()

    teammate_sidebar = await ArtifactService(db).list_readable(
        ctx_for(teammate, organization, role="member"), mine_or_shared=True, limit=8
    )
    assert {item.id for item in teammate_sidebar.items} == {private_id}

    outsider_sidebar = await ArtifactService(db).list_readable(
        ctx_for(outsider, organization, role="member"), mine_or_shared=True, limit=8
    )
    assert private_id not in {item.id for item in outsider_sidebar.items}


async def test_the_sidebar_lists_the_latest_published_first_then_by_name(
    db: AsyncSession, storage: LocalFileStorage
) -> None:
    author = await _user(db, "author")
    reader = await _user(db, "reader")
    organization, agent = await _workspace(db, author, [(reader, "member")])
    older = await _publish(db, organization, agent, author, "older", Visibility.ORG)
    newer = await _publish(db, organization, agent, author, "newer", Visibility.ORG)
    tie_a = await _publish(db, organization, agent, author, "tie-a", Visibility.ORG)
    tie_b = await _publish(db, organization, agent, author, "tie-b", Visibility.ORG)
    now = datetime.now(UTC)
    for artifact_id, when in (
        (older, now - timedelta(days=2)),
        (newer, now),
        (tie_a, now - timedelta(days=1)),
        (tie_b, now - timedelta(days=1)),
    ):
        row = await db.get(Artifact, artifact_id)
        assert row is not None
        row.published_at = when
    await db.flush()

    listed = await ArtifactService(db).list_readable(
        ctx_for(reader, organization, role="member"), mine_or_shared=True, limit=8
    )
    ids = [item.id for item in listed.items]
    assert ids == [newer, tie_a, tie_b, older]
