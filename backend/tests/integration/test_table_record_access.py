"""Real SQL proof for per-row Read/Write, group membership, expiry and revocation."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, BadRequestError, NotFoundError
from app.core.permissions import AuthContext, Perm
from app.db.models.group import Group, GroupMember
from app.db.models.organization import OrganizationMember
from app.db.models.resource_grant import GrantLevel
from app.repositories import resource_grant_repo
from app.schemas.table_record_access import RecordGrantUpdate
from app.schemas.virtual_table import (
    ColumnInput,
    RecordCountQuery,
    RecordCreate,
    RecordQuery,
    RecordRead,
    RecordUpdate,
    RecordUpsert,
    RowPolicyUpdate,
    TableCreate,
    TableRead,
    TableUpdate,
)
from app.services.virtual_tables import VirtualTableService
from tests.integration.virtual_table_support import ctx_for, make_org, make_user

pytestmark = pytest.mark.anyio


@dataclass
class SharedRows:
    service: VirtualTableService
    owner: AuthContext
    reader: AuthContext
    table: TableRead
    first: RecordRead
    second: RecordRead


@pytest.fixture
async def rows(db: AsyncSession) -> SharedRows:
    owner, reader = await make_user(db), await make_user(db)
    org = await make_org(db, owner=owner)
    db.add(OrganizationMember(organization_id=org.id, user_id=reader.id, role="viewer"))
    await db.flush()
    service = VirtualTableService(db)
    owner_ctx, reader_ctx = ctx_for(owner, org), ctx_for(reader, org, "viewer")
    table = await service.create_table(
        owner_ctx,
        TableCreate(
            name="Records", visibility="org", columns=[ColumnInput(label="Title", type="text")]
        ),
    )
    key = str(table.columns[0].id)
    first = (
        await service.create_record(
            owner_ctx, table.id, RecordCreate(external_id="first", values={key: "Visible"})
        )
    ).record
    second = (
        await service.create_record(
            owner_ctx, table.id, RecordCreate(external_id="second", values={key: "Hidden"})
        )
    ).record
    await service.set_row_policy(owner_ctx, table.id, RowPolicyUpdate(rule="grants"))
    return SharedRows(service, owner_ctx, reader_ctx, table, first, second)


async def grant(rows: SharedRows, level: str, **extra):
    return await rows.service.set_record_grant(
        rows.owner,
        rows.table.id,
        rows.first.id,
        RecordGrantUpdate(subject_user_id=rows.reader.user_id, level=level, **extra),
    )


async def test_read_and_write_are_distinct_and_share_neither_schema_nor_deletion(rows):
    service, table, reader = rows.service, rows.table, rows.reader
    assert (await service.list_records(reader, table.id)).items == []
    await grant(rows, "read")
    listed = await service.list_records(reader, table.id, RecordQuery(limit=1))
    assert [record.id for record in listed.items] == [rows.first.id]
    assert not listed.has_more
    assert (await service.count_records(reader, table.id, RecordCountQuery())).count == 1
    catalog = await service.list_tables(reader)
    assert next(item.record_count for item in catalog.items if item.id == table.id) == 1
    assert await service.record_exists(reader, table.id, "first")
    assert not await service.record_exists(reader, table.id, "second")
    assert (await service.get_record_by_external_id(reader, table.id, "first")).access == "read"
    with pytest.raises(NotFoundError):
        await service.get_record(reader, table.id, rows.second.id)
    with pytest.raises(NotFoundError):
        await service.update_record(
            reader,
            table.id,
            rows.first.id,
            RecordUpdate(expected_revision=1, values={str(table.columns[0].id): "Denied"}),
        )
    await grant(rows, "write")
    written = await service.update_record(
        reader,
        table.id,
        rows.first.id,
        RecordUpdate(expected_revision=1, values={str(table.columns[0].id): "Updated"}),
    )
    assert written.record.access == "write"
    assert not written.record.can_delete and not written.record.can_manage_access
    assert not (await service.describe_table(reader, table.id)).can_edit
    with pytest.raises(NotFoundError):
        await service.delete_record(reader, table.id, rows.first.id, expected_revision=2)
    with pytest.raises(NotFoundError):
        await service.set_record_grant(
            reader,
            table.id,
            rows.first.id,
            RecordGrantUpdate(subject_user_id=reader.user_id, level="write"),
        )
    with pytest.raises(NotFoundError):
        await service.update_table(reader, table.id, TableUpdate(name="Escalated"))
    # Workflow paths call the same authorizer; an upsert's insert branch still needs table administration.
    await service.update_record_cells(
        reader, table.id, rows.first.id, {str(table.columns[0].id): "Workflow"}
    )
    await service.upsert_record_cells(
        reader, table.id, "first", {str(table.columns[0].id): "Upsert"}
    )
    with pytest.raises(NotFoundError):
        await service.upsert_record(reader, table.id, "new", RecordUpsert(values={}))


async def test_group_grants_track_membership_and_expiry_without_promoting_the_group(db, rows):
    await grant(rows, "read")
    group = Group(organization_id=rows.owner.organization_id, name="Support")
    db.add(group)
    await db.flush()
    membership = GroupMember(group_id=group.id, user_id=rows.reader.user_id)
    db.add(membership)
    await db.flush()
    shared = await rows.service.set_record_grant(
        rows.owner,
        rows.table.id,
        rows.first.id,
        RecordGrantUpdate(subject_group_id=group.id, level="write"),
    )
    group_grant = next(grant for grant in shared.grants if grant.subject_group_id == group.id)
    assert group_grant.level == "write"
    assert group_grant.expires_at > datetime.now(UTC) + timedelta(days=89)
    assert (
        await rows.service.get_record(rows.reader, rows.table.id, rows.first.id)
    ).access == "write"
    await db.delete(membership)
    await db.flush()
    assert (
        await rows.service.get_record(rows.reader, rows.table.id, rows.first.id)
    ).access == "read"
    await rows.service.revoke_record_grant(
        rows.owner, rows.table.id, rows.first.id, rows.reader.user_id, kind="user"
    )
    assert (await rows.service.list_records(rows.reader, rows.table.id)).items == []
    db.add(GroupMember(group_id=group.id, user_id=rows.reader.user_id))
    await db.flush()
    await resource_grant_repo.upsert_for_group(
        db,
        organization_id=rows.owner.organization_id,
        subject_group_id=group.id,
        resource_type="table_record",
        resource_id=rows.first.id,
        level=GrantLevel.EDIT,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert (await rows.service.list_records(rows.reader, rows.table.id)).items == []


async def test_revocation_applies_to_saved_write_receipts(db, rows):
    await grant(rows, "write")
    data = RecordUpdate(expected_revision=1, values={str(rows.table.columns[0].id): "Once"})
    written = await rows.service.update_record(
        rows.reader, rows.table.id, rows.first.id, data, operation_key="row-write"
    )
    assert not written.replayed
    replay = await rows.service.update_record(
        rows.reader, rows.table.id, rows.first.id, data, operation_key="row-write"
    )
    assert replay.replayed
    await grant(rows, "read")
    with pytest.raises(NotFoundError):
        await rows.service.update_record(
            rows.reader, rows.table.id, rows.first.id, data, operation_key="row-write"
        )
    assert (
        await rows.service.get_record(rows.reader, rows.table.id, rows.first.id)
    ).access == "read"
    await rows.service.revoke_record_grant(
        rows.owner, rows.table.id, rows.first.id, rows.reader.user_id, kind="user"
    )
    assert (await rows.service.list_records(rows.reader, rows.table.id)).items == []
    assert (
        await resource_grant_repo.record_levels(
            db,
            organization_id=rows.reader.organization_id,
            subject_user_id=rows.reader.user_id,
            record_ids=[rows.first.id],
        )
        == {}
    )


async def test_grant_subjects_must_belong_to_this_organization(db, rows):
    outsider = await make_user(db)
    other_org = await make_org(db, owner=outsider)
    group = Group(organization_id=other_org.id, name="Other group")
    db.add(group)
    await db.flush()
    for subject in ({"subject_user_id": outsider.id}, {"subject_group_id": group.id}):
        with pytest.raises(BadRequestError):
            await rows.service.set_record_grant(
                rows.owner,
                rows.table.id,
                rows.first.id,
                RecordGrantUpdate(level="write", **subject),
            )


async def test_current_org_membership_and_the_exact_namespace_are_required(db, rows):
    await resource_grant_repo.upsert(
        db,
        organization_id=rows.reader.organization_id,
        subject_user_id=rows.reader.user_id,
        resource_type="table",
        resource_id=rows.first.id,
        level=GrantLevel.EDIT,
    )
    assert (await rows.service.list_records(rows.reader, rows.table.id)).items == []
    await grant(rows, "write")
    await db.execute(
        delete(OrganizationMember).where(
            OrganizationMember.organization_id == rows.reader.organization_id,
            OrganizationMember.user_id == rows.reader.user_id,
        )
    )
    await db.flush()
    # Even a stale AuthContext cannot retain the explicit row grant.
    assert (await rows.service.list_records(rows.reader, rows.table.id)).items == []


async def test_api_key_ceiling_and_foreign_tenants_cannot_be_widened_by_a_row_grant(rows):
    await grant(rows, "write")
    key = AuthContext(
        user_id=rows.reader.user_id,
        organization_id=rows.reader.organization_id,
        role="viewer",
        api_key_id=uuid4(),
        key_scopes=frozenset({Perm.TABLES_VIEW}),
    )
    assert (await rows.service.get_record(key, rows.table.id, rows.first.id)).access == "read"
    with pytest.raises(NotFoundError):
        await rows.service.update_record_cells(key, rows.table.id, rows.first.id, {})
    foreign = AuthContext(user_id=rows.reader.user_id, organization_id=uuid4(), role="owner")
    with pytest.raises(NotFoundError):
        await rows.service.get_record(foreign, rows.table.id, rows.first.id)


async def test_a_writer_cannot_change_access_fields_or_parent_membership(db, rows):
    service, owner, reader = rows.service, rows.owner, rows.reader
    projects = await service.create_table(
        owner,
        TableCreate(
            name="Projects",
            visibility="org",
            columns=[
                ColumnInput(label="Title", type="text"),
                ColumnInput(label="Members", type="multi_user"),
            ],
        ),
    )
    title, members = [str(column.id) for column in projects.columns]
    project = (
        await service.create_record(
            owner,
            projects.id,
            RecordCreate(values={title: "Project", members: [str(owner.user_id)]}),
        )
    ).record
    tasks = await service.create_table(
        owner,
        TableCreate(
            name="Tasks",
            visibility="org",
            columns=[
                ColumnInput(label="Readers", type="user"),
                ColumnInput(label="Writers", type="multi_user"),
                ColumnInput(label="Project", type="link", link_table_id=projects.id),
            ],
        ),
    )
    read_id, write_id, project_id = [column.id for column in tasks.columns]
    await service.set_row_policy(
        owner,
        tasks.id,
        RowPolicyUpdate(
            rule="assignee",
            view_column_id=read_id,
            edit_column_id=write_id,
            view_project_link_column_id=project_id,
            view_projects_table_id=projects.id,
            view_project_members_column_id=UUID(members),
        ),
    )
    task = (
        await service.create_record(
            owner,
            tasks.id,
            RecordCreate(
                values={
                    str(read_id): str(owner.user_id),
                    str(write_id): [str(owner.user_id)],
                    str(project_id): [str(project.id)],
                }
            ),
        )
    ).record
    for table, row in ((projects, project), (tasks, task)):
        await service.set_record_grant(
            owner,
            table.id,
            row.id,
            RecordGrantUpdate(subject_user_id=reader.user_id, level="write"),
        )
    readable = await service.get_record(reader, projects.id, project.id)
    assert UUID(members) in readable.read_only_column_ids
    with pytest.raises(AuthorizationError):
        await service.update_record_cells(
            reader, projects.id, project.id, {members: [str(owner.user_id), str(reader.user_id)]}
        )
    for column_id, value in (
        (read_id, str(reader.user_id)),
        (write_id, [str(reader.user_id)]),
        (project_id, []),
    ):
        with pytest.raises(AuthorizationError):
            await service.update_record_cells(reader, tasks.id, task.id, {str(column_id): value})
    changed = await service.update_record_cells(reader, projects.id, project.id, {title: "Renamed"})
    assert changed.record.values[title] == "Renamed"


async def test_linked_records_and_lookups_follow_the_destination_row_grants(rows):
    service, owner, reader = rows.service, rows.owner, rows.reader
    source = await service.create_table(
        owner,
        TableCreate(
            name="Links",
            visibility="org",
            columns=[
                ColumnInput(label="Linked", type="link", link_table_id=rows.table.id),
            ],
        ),
    )
    link = str(source.columns[0].id)
    record = (
        await service.create_record(
            owner, source.id, RecordCreate(values={link: [str(rows.first.id), str(rows.second.id)]})
        )
    ).record
    assert (await service.get_record(reader, source.id, record.id)).links.get(link, []) == []
    await grant(rows, "read")
    visible = await service.get_record(reader, source.id, record.id)
    assert [linked.id for linked in visible.links[link]] == [rows.first.id]
    await service.set_record_grant(
        owner,
        source.id,
        record.id,
        RecordGrantUpdate(subject_user_id=reader.user_id, level="write"),
    )
    await service.update_record_cells(reader, source.id, record.id, {link: [str(rows.first.id)]})


async def test_explicit_null_clears_person_expiry_and_delete_removes_all_row_grants(db, rows):
    await grant(rows, "read", expires_at=datetime.now(UTC) + timedelta(days=1))
    updated = await grant(rows, "write", expires_at=None)
    assert updated.grants[0].expires_at is None
    await rows.service.delete_record(rows.owner, rows.table.id, rows.first.id, expected_revision=1)
    assert (
        await resource_grant_repo.list_for_resource(
            db,
            organization_id=rows.owner.organization_id,
            resource_type="table_record",
            resource_id=rows.first.id,
        )
        == []
    )
