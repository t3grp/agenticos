Warning: truncated output (original token count: 18871)
Total output lines: 1766

"""Where an organization's sandboxes run: registering one, and reaching it.

The refusals are the point, and they fall into two groups.

*Refused while a form is open.* An unknown kind, a container connection with no
address, a duplicate name. Each of these otherwise surfaces on an agent's first
tool call, where the reader is a user in a conversation and the person who can
fix it is the operator who filled this in.

*Refused at the moment of use.* A connection switched off, one whose vault entry
was deleted, one holding the wrong kind of credential. These are states a
deployment arrives at *after* an agent was published, so each says which rather
than failing as one generic error.

And the standing property behind all of it: the credential leaves this module
only as an argument to a client. Never a response, never a log line, never a
`repr`.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import (
    AlreadyExistsError,
    AuthorizationError,
    BadRequestError,
    NotFoundError,
)
from app.core.permissions import AuthContext, OrgRoleName
from app.core.secret_kinds import ApiKeySecret, AwsCredentialsSecret
from app.repositories import (
    agent_workspace_repo,
    organization_secret_repo,
    project_repo,
    sandbox_connection_repo,
)
from app.repositories import conversation as conversation_repo
from app.repositories import pending_sandbox_destroy as pending_destroy_repo
from app.schemas.sandbox_connection import (
    SandboxConnectionCreate,
    SandboxConnectionUpdate,
    SandboxProbeRequest,
    SandboxSessionUsage,
)
from app.services.sandbox_connection import (
    LOCAL_TOKEN_SECRET_NAME,
    ResolvedConnection,
    SandboxConnectionService,
    to_read,
)
from app.services.sandbox_runtimes import CATALOG

pytestmark = pytest.mark.anyio


def _ctx() -> AuthContext:
    return AuthContext(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role=OrgRoleName.OWNER)


def _row(**overrides: object) -> MagicMock:
    row = MagicMock(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        kind="docker",
        base_url="http://sandboxd:8080",
        secret_id=uuid.uuid4(),
        default_runtime=None,
        is_default=True,
        is_active=True,
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
        updated_at=None,
    )
    row.name = "Local Docker"
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


def _session(session_id: str, **overrides: object) -> dict[str, Any]:
    """One session as `sandboxd` answers with it.

    Whole rather than minimal, because the service builds a `SandboxSessionRead`
    from it now instead of handing the mapping on for the route to validate
    (#562) - so a fixture holding only the two keys a test asserts on describes a
    payload that would already have failed at the response model.
    """
    session = {
        "session_id": session_id,
        "runtime": "coding",
        "alive": True,
        "state": "running",
        "created_at": 1_756_000_000.0,
        "last_activity": 1_756_000_060.0,
        "idle_seconds": 60.0,
    }
    session.update(overrides)
    return session


def _service(monkeypatch, *, secret: Any = None) -> SandboxConnectionService:
    """A service whose vault answers with one unsealed secret.

    The vault itself is proven in `tests/test_secrets.py`; here the question is
    what this service does with what it gets back.
    """
    db = MagicMock()
    db.flush = AsyncMock()
    # `record_audit` reads the chain head and takes the per-org lock, both via
    # `execute`; the mock must await and answer the head read with an empty chain.
    db.execute = AsyncMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    service = SandboxConnectionService(db)
    resolved = {} if secret is None else {secret[0]: secret[1]}
    service.secrets = MagicMock()
    service.secrets.resolve_for_bindings = AsyncMock(return_value=resolved)
    service.secrets.require_view = AsyncMock()
    monkeypatch.setattr(project_repo, "active_moves_for_connection", AsyncMock(return_value=False))
    monkeypatch.setattr(sandbox_connection_repo, "clear_default", AsyncMock())
    # This file is about the connection and the audit entry `record_audit`
    # writes for real (#1598), not the `security_event` notification that now
    # follows it - covered separately in `tests/test_notifications.py`. Left
    # real, it would resolve a member/app-admin audience against `db.execute`,
    # wired only for the audit chain's own head-read shape.
    monkeypatch.setattr(
        "app.services.sandbox_connection.NotificationService", MagicMock(return_value=AsyncMock())
    )
    return service


class TestRegistering:
    async def test_the_first_connection_becomes_the_default(self, monkeypatch):
        """Otherwise the form succeeds and every agent then fails to find a host."""
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get_by_name", AsyncMock(return_value=None))
        monkeypatch.setattr(
            sandbox_connection_repo, "list_for_organization", AsyncMock(return_value=[])
        )
        created = AsyncMock(return_value=_row())
        monkeypatch.setattr(sandbox_connection_repo, "create", created)

        await service.create(
            _ctx(),
            SandboxConnectionCreate(
                name="Local Docker", kind="docker", base_url="http://sandboxd:8080"
            ),
        )

        assert created.await_args.kwargs["is_default"] is True

    async def test_a_later_connection_does_not_steal_the_default(self, monkeypatch):
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get_by_name", AsyncMock(return_value=None))
        monkeypatch.setattr(
            sandbox_connection_repo, "list_for_organization", AsyncMock(return_value=[_row()])
        )
        created = AsyncMock(return_value=_row(is_default=False))
        monkeypatch.setattr(sandbox_connection_repo, "create", created)

        await service.create(
            _ctx(), SandboxConnectionCreate(name="Big box", kind="docker", base_url="http://b:8080")
        )

        assert created.await_args.kwargs["is_default"] is False

    async def test_promoting_one_demotes_the_rest(self, monkeypatch):
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get_by_name", AsyncMock(return_value=None))
        monkeypatch.setattr(
            sandbox_connection_repo, "list_for_organization", AsyncMock(return_value=[_row()])
        )
        row = _row(is_default=True)
        monkeypatch.setattr(sandbox_connection_repo, "create", AsyncMock(return_value=row))
        cleared = AsyncMock()
        monkeypatch.setattr(sandbox_connection_repo, "clear_default", cleared)

        await service.create(
            _ctx(),
            SandboxConnectionCreate(
                name="Big box", kind="docker", base_url="http://b:8080", is_default=True
            ),
        )

        assert cleared.await_args.kwargs["except_id"] == row.id

    async def test_a_duplicate_name_is_refused_by_name(self, monkeypatch):
        """Two hosts called "Docker" is a Builder dropdown nobody can read."""
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get_by_name", AsyncMock(return_value=_row()))

        with pytest.raises(AlreadyExistsError):
            await service.create(
                _ctx(),
                SandboxConnectionCreate(
                    name="Local Docker", kind="docker", base_url="http://s:8080"
                ),
            )

    async def test_a_container_connection_without_an_address_is_refused(self, monkeypatch):
        """It would resolve, and every session opened on it would fail to connect."""
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get_by_name", AsyncMock(return_value=None))

        with pytest.raises(BadRequestError) as refused:
            await service.create(_ctx(), SandboxConnectionCreate(name="Big box", kind="docker"))

        assert refused.value.details == {
            "fields": [{"field": "base_url", "message": refused.value.message}]
        }

    async def test_daytona_needs_no_address_of_its_own(self, monkeypatch):
        """Their API has one, and asking an operator to type it invites a typo in
        the one field nothing validates."""
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get_by_name", AsyncMock(return_value=None))
        monkeypatch.setattr(
            sandbox_connection_repo, "list_for_organization", AsyncMock(return_value=[])
        )
        monkeypatch.setattr(
            sandbox_connection_repo,
            "create",
            AsyncMock(return_value=_row(kind="daytona", base_url=None)),
        )

        read = await service.create(_ctx(), SandboxConnectionCreate(name="Daytona", kind="daytona"))

        assert read.kind == "daytona"

    async def test_attaching_a_secret_the_caller_cannot_view_is_refused(self, monkeypatch):
        """A Builder holding `connections:manage` must not be able to name
        somebody else's private key and have the connection spend it for them."""
        service = _service(monkeypatch)
        service.secrets.require_view = AsyncMock(
            side_effect=AuthorizationError(message="You do not have access to that secret")
        )
        monkeypatch.setattr(sandbox_connection_repo, "get_by_name", AsyncMock(return_value=None))
        created = AsyncMock()
        monkeypatch.setattr(sandbox_connection_repo, "create", created)

        with pytest.raises(AuthorizationError):
            await service.create(
                _ctx(),
                SandboxConnectionCreate(
                    name="Big box",
                    kind="docker",
                    base_url="http://b:8080",
                    secret_id=uuid.uuid4(),
                ),
            )

        created.assert_not_awaited()

    async def test_attaching_a_secret_the_caller_may_view_is_allowed(self, monkeypatch):
        service = _service(monkeypatch)
        secret_id = uuid.uuid4()
        monkeypatch.setattr(sandbox_connection_repo, "get_by_name", AsyncMock(return_value=None))
        monkeypatch.setattr(
            sandbox_connection_repo, "list_for_organization", AsyncMock(return_value=[])
        )
        created = AsyncMock(return_value=_row(secret_id=secret_id))
        monkeypatch.setattr(sandbox_connection_repo, "create", created)

        await service.create(
            _ctx(),
            SandboxConnectionCreate(
                name="Big box", kind="docker", base_url="http://b:8080", secret_id=secret_id
            ),
        )

        service.secrets.require_view.assert_awaited_once()
        assert created.await_args.kwargs["secret_id"] == secret_id


class TestEditing:
    async def test_a_connection_from_another_organization_reads_as_missing(self, monkeypatch):
        """Not "forbidden": a probeable id is how a tenant boundary gets mapped."""
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=None))

        with pytest.raises(NotFoundError):
            await service.get(_ctx(), uuid.uuid4())

    async def test_renaming_onto_an_existing_name_is_refused(self, monkeypatch):
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=_row()))
        monkeypatch.setattr(sandbox_connection_repo, "get_by_name", AsyncMock(return_value=_row()))

        with pytest.raises(AlreadyExistsError):
            await service.update(_ctx(), uuid.uuid4(), SandboxConnectionUpdate(name="Big box"))

    async def test_keeping_the_same_name_is_not_a_duplicate_of_itself(self, monkeypatch):
        service = _service(monkeypatch)
        row = _row()
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        monkeypatch.setattr(
            sandbox_connection_repo, "update_connection", AsyncMock(return_value=row)
        )

        read = await service.update(
            _ctx(), row.id, SandboxConnectionUpdate(name="Local Docker", default_runtime="python")
        )

        assert read.name == "Local Docker"

    async def test_switching_a_container_connection_to_no_address_is_refused(self, monkeypatch):
        """The shape check runs against the row as it *will* be, not as it was."""
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=_row()))

        with pytest.raises(BadRequestError):
            await service.update(_ctx(), uuid.uuid4(), SandboxConnectionUpdate(base_url=None))

    async def test_a_row_holding_a_kind_this_build_does_not_know_is_refused(self, monkeypatch):
        """The schema keeps an unknown kind out of the API, so this is only
        reachable from the database - a row written by a build that had a third
        kind, edited by one that does not. Refusing beats saving an edit against
        a kind nothing can open a sandbox on."""
        service = _service(monkeypatch)
        monkeypatch.setattr(
            sandbox_connection_repo, "get", AsyncMock(return_value=_row(kind="kubernetes"))
        )

        with pytest.raises(BadRequestError) as refused:
            await service.update(
                _ctx(), uuid.uuid4(), SandboxConnectionUpdate(default_runtime="python")
            )

        assert refused.value.details["kind"] == "kubernetes"

    async def test_promoting_through_an_edit_demotes_the_rest(self, monkeypatch):
        service = _service(monkeypatch)
        row = _row(is_default=True)
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        monkeypatch.setattr(
            sandbox_connection_repo, "update_connection", AsyncMock(return_value=row)
        )
        cleared = AsyncMock()
        monkeypatch.setattr(sandbox_connection_repo, "clear_default", cleared)

        await service.update(_ctx(), row.id, SandboxConnectionUpdate(is_default=True))

        cleared.assert_awaited_once()

    async def test_changing_the_secret_to_one_the_caller_cannot_view_is_refused(self, monkeypatch):
        service = _service(monkeypatch)
        service.secrets.require_view = AsyncMock(
            side_effect=AuthorizationError(message="You do not have access to that secret")
        )
        row = _row()
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        updated = AsyncMock()
        monkeypatch.setattr(sandbox_connection_repo, "update_connection", updated)

        with pytest.raises(AuthorizationError):
            await service.update(_ctx(), row.id, SandboxConnectionUpdate(secret_id=uuid.uuid4()))

        updated.assert_not_awaited()

    async def test_detaching_the_secret_needs_no_view(self, monkeypatch):
        """Removing a credential is not spending one."""
        service = _service(monkeypatch)
        row = _row()
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        monkeypatch.setattr(
            sandbox_connection_repo, "update_connection", AsyncMock(return_value=row)
        )

        await service.update(_ctx(), row.id, SandboxConnectionUpdate(secret_id=None))

        service.secrets.require_view.assert_not_awaited()

    async def test_deleting_leaves_the_workspaces_alone(self, monkeypatch):
        """Their rows record what an agent did and where; forgetting a host is
        not a statement about that history."""
        service = _service(monkeypatch)
        row = _row()
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        deleted = AsyncMock()
        monkeypatch.setattr(sandbox_connection_repo, "delete", deleted)
        monkeypatch.setattr(project_repo, "names_on_connection", AsyncMock(return_value=[]))
        monkeypatch.setattr(pending_destroy_repo, "count_on_connection", AsyncMock(return_value=0))

        await service.delete(_ctx(), row.id)

        deleted.assert_awaited_once()

    async def test_a_connection_with_sandboxes_still_to_destroy_is_not_deleted(self, monkeypatch):
        """The tombstones would go with it, and they are the only record of
        sandboxes still billed on the provider (#2209)."""
        service = _service(monkeypatch)
        row = _row()
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        deleted = AsyncMock()
        monkeypatch.setattr(sandbox_connection_repo, "delete", deleted)
        monkeypatch.setattr(project_repo, "names_on_connection", AsyncMock(return_value=[]))
        monkeypatch.setattr(pending_destroy_repo, "count_on_connection", AsyncMock(return_value=2))

        with pytest.raises(BadRequestError) as refused:
            await service.delete(_ctx(), row.id)

        assert refused.value.details == {"pending_destroys": 2}
        deleted.assert_not_awaited()

    async def test_sandboxes_removed_by_hand_are_forgotten_and_the_connection_goes(
        self, monkeypatch
    ):
        """#2250: with a revoked credential nothing could ever clear the tombstones."""
        service = _service(monkeypatch)
        row = _row()
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        deleted = AsyncMock()
        monkeypatch.setattr(sandbox_connection_repo, "delete", deleted)
        monkeypatch.setattr(project_repo, "names_on_connection", AsyncMock(return_value=[]))
        forgot = AsyncMock(return_value=2)
        monkeypatch.setattr(pending_destroy_repo, "remove_on_connection", forgot)
        monkeypatch.setattr(pending_destroy_repo, "count_on_connection", AsyncMock(return_value=0))
        audited = AsyncMock()
        monkeypatch.setattr("app.services.sandbox_connection.record_audit", audited)

        await service.delete(_ctx(), row.id, forget_pending_destroys=True)

        forgot.assert_awaited_once_with(service.db, connection_id=row.id)
        deleted.assert_awaited_once()
        assert audited.await_args.kwargs["details"] == {
            "name": row.name,
            "forgotten_pending_destroys": 2,
        }

    async def test_a_tombstone_written_during_the_delete_refuses_it(self, monkeypatch):
        """#2250: the foreign key keeps the tombstone a concurrent deletion wrote."""
        from sqlalchemy.exc import IntegrityError

        service = _service(monkeypatch)
        row = _row()
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        monkeypatch.setattr(
            sandbox_connection_repo,
            "delete",
            AsyncMock(side_effect=IntegrityError("DELETE", {}, Exception("fk"))),
        )
        monkeypatch.setattr(project_repo, "names_on_connection", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            pending_destroy_repo, "count_on_connection", AsyncMock(side_effect=[0, 1])
        )

        with pytest.raises(BadRequestError) as refused:
            await service.delete(_ctx(), row.id)

        assert refused.value.details == {"pending_destroys": 1}

    @pytest.mark.security
    async def test_a_connection_a_project_keeps_its_files_on_is_not_deleted(self, monkeypatch):
        """Clearing the pin would hand the project the organization's default
        host - an empty workspace - and strand its files on this one."""
        service = _service(monkeypatch)
        row = _row()
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        deleted = AsyncMock()
        monkeypatch.setattr(sandbox_connection_repo, "delete", deleted)
        monkeypatch.setattr(project_repo, "names_on_connection", AsyncMock(return_value=["Launch"]))

        with pytest.raises(BadRequestError) as refused:
            await service.delete(_ctx(), row.id)

        assert refused.value.details == {"projects": ["Launch"]}
        deleted.assert_not_awaited()

    async def test_a_listing_never_carries_a_credential(self, monkeypatch):
        """`secret_id` is a reference the vault resolves under its own check; a
        value here would be a token in a browser."""
        service = _service(monkeypatch)
        monkeypatch.setattr(
            sandbox_connection_repo, "list_for_organization", AsyncMock(return_value=[_row()])
        )

        [read] = await service.list_connections(_ctx())

        assert "token" not in read.model_dump()
        assert "api_key" not in read.model_dump()


class TestResolvingForARun:
    async def test_the_default_is_taken_when_the_spec_names_none(self, monkeypatch):
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get_default", AsyncMock(return_value=row))

        resolved = await service.resolve(_ctx(), None)

        assert resolved.token == "tok-12345678"
        assert resolved.kind == "docker"

    async def test_an_organization_with_no_connection_is_told_what_to_do(self, monkeypatch):
        """On a deployment running no sandbox service of its own to set up."""
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get_default", AsyncMock(return_value=None))
        monkeypatch.setattr(settings, "SANDBOXD_TOKEN", "")

        with pytest.raises(BadRequestError) as refused:
            await service.resolve(_ctx(), None)

        assert "'state' workspace" in refused.value.message

    async def test_a_connection_deleted_since_publish_says_so(self, monkeypatch):
        """A spec valid when it was published; the fix is in the Builder."""
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=None))

        with pytest.raises(BadRequestError) as refused:
            await service.resolve(_ctx(), uuid.uuid4())

        assert "no longer exists" in refused.value.message

    async def test_a_connection_switched_off_is_refused_rather_than_used(self, monkeypatch):
        """Off means an operator has said not to run agents there."""
        row = _row(is_active=False)
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))

        with pytest.raises(BadRequestError) as refused:
            await service.resolve(_ctx(), row.id)

        assert "switched off" in refused.value.message

    async def test_a_connection_switched_off_still_removes_its_sandboxes(self, monkeypatch):
        """Off stops new work; the sandboxes already there are still billed (#2209)."""
        row = _row(is_active=False)
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))

        resolved = await service.resolve_for_teardown(_ctx(), row.id)

        assert resolved.token == "tok-12345678"

    async def test_a_credential_deleted_from_the_vault_is_named_as_the_cause(self, monkeypatch):
        """`SET NULL` on the vault reference makes this reachable, and the
        connection is still there to be fixed."""
        row = _row(secret_id=None)
        service = _service(monkeypatch)
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))

        with pytest.raises(BadRequestError) as refused:
            await service.resolve(_ctx(), row.id)

        assert "no credential" in refused.value.message

    async def test_a_credenti…6871 tokens truncated…
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        ctx = _ctx()
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        monkeypatch.setattr(
            agent_workspace_repo,
            "list_for_organization",
            AsyncMock(
                return_value=[
                    MagicMock(
                        session_id="xc-mine",
                        agent_id=uuid.uuid4(),
                        conversation_id=mine,
                        scope="conversation",
                    ),
                    MagicMock(
                        session_id="xc-theirs",
                        agent_id=uuid.uuid4(),
                        conversation_id=theirs,
                        scope="conversation",
                    ),
                ]
            ),
        )
        monkeypatch.setattr(
            conversation_repo,
            "titles_for",
            AsyncMock(
                return_value={
                    mine: SimpleNamespace(title="Mine", user_id=ctx.user_id),
                    theirs: SimpleNamespace(title="Theirs", user_id=uuid.uuid4()),
                }
            ),
        )
        _serve(
            monkeypatch,
            _Response(
                200,
                {
                    "sessions": [
                        _session("xc-mine", tenant=str(ctx.organization_id)),
                        _session("xc-theirs", tenant=str(ctx.organization_id)),
                    ]
                },
            ),
        )

        by_id = {
            entry.session_id: entry for entry in (await service.sessions(ctx, row.id)).sessions
        }

        assert by_id["xc-mine"].conversation_is_callers is True
        assert by_id["xc-theirs"].conversation_is_callers is False

    async def test_a_run_scoped_sandbox_keeps_its_id_and_nothing_else(self, monkeypatch):
        """It has no row by design, so an unmatched session is normal."""
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        monkeypatch.setattr(
            agent_workspace_repo, "list_for_organization", AsyncMock(return_value=[])
        )
        ctx = _ctx()
        _serve(
            monkeypatch,
            _Response(200, {"sessions": [_session("xr-1", tenant=str(ctx.organization_id))]}),
        )

        [entry] = (await service.sessions(ctx, row.id)).sessions

        assert entry.session_id == "xr-1"
        assert entry.agent_id is None
        assert entry.conversation_id is None
        assert entry.scope is None

    @pytest.mark.security
    async def test_the_tenant_label_does_not_survive_into_a_row(self, monkeypatch):
        """It is how the filter knows whose a session is, and it is another
        organization's id when the session is theirs. A row is built from the
        daemon's mapping rather than the mapping being passed on, so the label is
        dropped once rather than by every caller remembering to (#562)."""
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        monkeypatch.setattr(
            agent_workspace_repo, "list_for_organization", AsyncMock(return_value=[])
        )
        ctx = _ctx()
        _serve(
            monkeypatch,
            _Response(200, {"sessions": [_session("mine", tenant=str(ctx.organization_id))]}),
        )

        listing = await service.sessions(ctx, row.id)

        assert "tenant" not in listing.model_dump()["sessions"][0]

    async def test_sampling_usage_is_asked_for_explicitly(self, monkeypatch):
        """The service pays a daemon round trip per sandbox for it, so a listing
        page must not do it on load."""
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        monkeypatch.setattr(
            agent_workspace_repo, "list_for_organization", AsyncMock(return_value=[])
        )
        seen = _serve(monkeypatch, _Response(200, {"sessions": []}))

        await service.sessions(_ctx(), row.id, usage=True)

        assert seen["url"].endswith("/sessions?usage=true")

    async def test_daytona_holds_no_sessions_of_ours_to_enumerate(self, monkeypatch):
        row = _row(kind="daytona", base_url=None)
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))

        listing = await service.sessions(_ctx(), row.id)

        assert listing.sessions == []
        assert listing.kind == "daytona"

    async def test_a_service_that_did_not_answer_is_reported(self, monkeypatch):
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        _serve(monkeypatch, OSError("connection refused"))

        with pytest.raises(BadRequestError) as refused:
            await service.sessions(_ctx(), row.id)

        assert "did not answer" in refused.value.message

    async def test_the_host_wide_total_counts_rows_the_org_view_filters_out(self, monkeypatch):
        """The point of the two counts: an operator whose own view is short of its
        ceiling can still see the host is full of another tenant's work. Taken
        before the filter, so they count every tenant while the rows count one."""
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        monkeypatch.setattr(
            agent_workspace_repo, "list_for_organization", AsyncMock(return_value=[])
        )
        ctx = _ctx()
        _serve(
            monkeypatch,
            _Response(
                200,
                {
                    "sessions": [
                        _session("mine", tenant=str(ctx.organization_id)),
                        _session("theirs-1", tenant=str(uuid.uuid4())),
                        _session("theirs-2", tenant=str(uuid.uuid4())),
                    ],
                    "limit": 40,
                    "open_limit": 100,
                },
            ),
        )

        listing = await service.sessions(ctx, row.id)

        assert [entry.session_id for entry in listing.sessions] == ["mine"]
        assert listing.host_session_count == 3
        assert listing.host_open_count == 3

    async def test_open_counts_every_session_but_resident_counts_only_the_running_ones(
        self, monkeypatch
    ):
        """`open_limit` bounds every session that exists, so `host_open_count` is
        the whole list; `limit` bounds only the resident ones, which the service
        marks `state == "running"`. A hibernated session frees its slot, and a
        crashed session keeps its resident slot until it is reaped and so still
        reads `running` while `alive` is false - `state`, not `alive`, is what the
        resident ceiling counts, or a full host would look like it had room."""
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        monkeypatch.setattr(
            agent_workspace_repo, "list_for_organization", AsyncMock(return_value=[])
        )
        _serve(
            monkeypatch,
            _Response(
                200,
                {
                    "sessions": [
                        _session("running"),
                        _session("hibernated", state="hibernated", alive=False),
                        _session("crashed", alive=False),
                    ],
                    "limit": 40,
                    "open_limit": 100,
                },
            ),
        )

        listing = await service.sessions(_ctx(), row.id)

        assert listing.host_open_count == 3
        assert listing.host_session_count == 2

    async def test_daytona_has_no_host_wide_totals_to_divide(self, monkeypatch):
        """It enforces no ceilings of ours, so there is nothing to be a numerator
        for; both counts default to `None` the way the ceilings already do."""
        row = _row(kind="daytona", base_url=None)
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))

        listing = await service.sessions(_ctx(), row.id)

        assert listing.host_session_count is None
        assert listing.host_open_count is None


class TestSamplingOneSandbox:
    """What a usage report can afford: one session, not the listing.

    The service samples each sandbox individually, so asking for all of them to
    find the one a turn used would cost a round trip per sandbox the organization
    has open - on every turn.
    """

    async def test_the_memory_of_one_session_comes_back(self, monkeypatch):
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        ctx = _ctx()
        seen = _serve(
            monkeypatch,
            _Response(
                200,
                {
                    "session_id": "xc-1",
                    "tenant": str(ctx.organization_id),
                    "usage": {
                        "memory_bytes": 512,
                        "memory_limit_bytes": 2048,
                        "cpu_percent": 12.5,
                        "pids": 7,
                    },
                },
            ),
        )

        usage = await service.session_usage(ctx, row.id, "xc-1")

        # Every field the daemon sampled, because a model drops what it does not
        # declare - so this is the assertion that a value stops reaching a client
        # the moment somebody forgets to add it here (#562).
        assert usage.memory_bytes == 512
        assert usage.memory_limit_bytes == 2048
        assert usage.cpu_percent == 12.5
        assert usage.pids == 7
        assert seen["url"].endswith("/sessions/xc-1?usage=true")

    async def test_another_organizations_sandbox_reads_as_missing(self, monkeypatch):
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        _serve(monkeypatch, _Response(200, {"session_id": "theirs", "tenant": str(uuid.uuid4())}))

        with pytest.raises(NotFoundError):
            await service.session_usage(_ctx(), row.id, "theirs")

    async def test_a_session_that_reported_nothing_is_an_empty_answer(self, monkeypatch):
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        ctx = _ctx()
        _serve(
            monkeypatch, _Response(200, {"session_id": "xc-1", "tenant": str(ctx.organization_id)})
        )

        usage = await service.session_usage(ctx, row.id, "xc-1")

        assert usage.memory_bytes is None
        assert usage.memory_limit_bytes is None

    async def test_daytona_reports_no_memory_of_ours(self, monkeypatch):
        row = _row(kind="daytona", base_url=None)
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))

        assert await service.session_usage(_ctx(), row.id, "any") == SandboxSessionUsage()


class TestReadingOneSessionsActivity:
    async def test_another_organizations_session_reads_as_missing(self, monkeypatch):
        """The log names every path read and command run. That is a description of
        somebody's work even with no contents in it."""
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        _serve(monkeypatch, _Response(200, {"session_id": "theirs", "tenant": str(uuid.uuid4())}))

        with pytest.raises(NotFoundError):
            await service.session_events(_ctx(), row.id, "theirs")

    async def test_our_own_session_comes_back_with_its_log(self, monkeypatch):
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        ctx = _ctx()
        seen = _serve(
            monkeypatch,
            [
                _Response(200, {"session_id": "xc-1", "tenant": str(ctx.organization_id)}),
                _Response(
                    200,
                    {
                        "events": [{"seq": 1, "at": 1.0, "op": "exec", "target": "/x", "ok": True}],
                        "latest_seq": 1,
                    },
                ),
            ],
        )

        log = await service.session_events(ctx, row.id, "xc-1", after=0)

        assert log.latest_seq == 1
        assert [event.op for event in log.events] == ["exec"]
        assert seen["url"].endswith("/sessions/xc-1/events?after=0")

    async def test_polling_asks_only_for_what_it_does_not_have(self, monkeypatch):
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        ctx = _ctx()
        seen = _serve(
            monkeypatch,
            [
                _Response(200, {"session_id": "xc-1", "tenant": str(ctx.organization_id)}),
                _Response(200, {"events": [], "latest_seq": 7}),
            ],
        )

        await service.session_events(ctx, row.id, "xc-1", after=7)

        assert seen["url"].endswith("after=7")

    async def test_a_session_the_service_forgot_is_a_404_rather_than_a_500(self, monkeypatch):
        """Reaped between the listing and the click, which is ordinary."""
        row = _row()
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))
        _serve(monkeypatch, _Response(404))

        with pytest.raises(NotFoundError):
            await service.session_events(_ctx(), row.id, "gone")

    async def test_daytona_has_no_activity_log_of_ours(self, monkeypatch):
        row = _row(kind="daytona", base_url=None)
        service = _service(
            monkeypatch, secret=(row.secret_id, ApiKeySecret(api_key="tok-12345678"))
        )
        monkeypatch.setattr(sandbox_connection_repo, "get", AsyncMock(return_value=row))

        log = await service.session_events(_ctx(), row.id, "any")

        assert log.events == []
        assert log.latest_seq == 0


@pytest.mark.security
def test_the_read_model_carries_the_reference_and_not_the_secret():
    row = _row()

    read = to_read(row)

    assert read.secret_id == row.secret_id
    assert read.base_url == "http://sandboxd:8080"


class TestAnAddressThePlatformWillFetch:
    """What `base_url` refuses, and what it deliberately still allows.

    This field is the input to a server-side GET that carries the connection's
    token and hands the body back, so an unvalidated string made the API container
    a fetch proxy for its own network. One validator covers all three schemas
    that carry an address - a connection created, one edited, and the probe, which
    is the only one taking an address straight from a request body.
    """

    @pytest.mark.parametrize(
        "address",
        [
            "http://sandboxd:8080",
            "http://localhost:8080",
            "https://sandbox.internal.example.com",
            # RFC1918 stays allowed on purpose: a sandbox service's legitimate
            # address is private, so refusing this range would refuse the
            # deployment this project documents.
            "http://10.1.2.3:8080",
        ],
    )
    def test_a_real_service_address_is_accepted(self, address):
        assert SandboxProbeRequest(base_url=address).base_url == address

    @pytest.mark.parametrize(
        ("address", "because"),
        [
            ("file:///etc/passwd", "http"),
            ("gopher://x/1", "http"),
            ("http://", "host"),
            ("not-a-url", "http"),
            # The one target where a single unauthenticated GET is worth
            # something, blocked by address and by every name that means it.
            ("http://169.254.169.254/latest/meta-data/", "link-local"),
            ("http://metadata.google.internal/computeMetadata/v1/", "metadata"),
            ("http://[fe80::1]:8080", "link-local"),
        ],
    )
    def test_an_address_the_platform_must_not_fetch_is_refused(self, address, because):
        with pytest.raises(ValidationError) as refused:
            SandboxProbeRequest(base_url=address)

        assert because in str(refused.value)

    def test_the_same_rule_applies_to_a_stored_connection(self):
        """Not only to the probe. The probe is the loud one, but `create` and
        `update` store an address that `resolve` then fetches on every run."""
        with pytest.raises(ValidationError):
            SandboxConnectionCreate(
                name="Metadata", kind="docker", base_url="http://169.254.169.254"
            )
        with pytest.raises(ValidationError):
            SandboxConnectionUpdate(base_url="http://169.254.169.254")

    def test_an_unset_address_is_still_allowed(self):
        """A Daytona connection has no address at all, and `update` means
        unchanged rather than empty."""
        assert SandboxConnectionCreate(name="Cloud", kind="daytona").base_url is None
        assert SandboxConnectionUpdate().base_url is None


class _Response:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _NotJson(_Response):
    """A 200 whose body is not JSON, which is what a web server answers.

    `httpx` raises `json.JSONDecodeError` here, and that is a `ValueError` - so
    the guard catches the class rather than importing the library's own.
    """

    def __init__(self) -> None:
        super().__init__(200)

    def json(self) -> Any:
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


def _serve(monkeypatch, answer: _Response | Exception | list[_Response]) -> dict[str, Any]:
    """Stand in for the sandbox service, and record how it was called.

    A list answers successive requests in order, which is what the activity log
    needs: it checks the session's tenant before it fetches the log.
    """
    import httpx

    seen: dict[str, Any] = {}
    queue = list(answer) if isinstance(answer, list) else None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None):
            seen.update(url=url, headers=headers)
            # Every address, not only the last: a probe that tries two in order is
            # only proven correct by which one it tried first.
            seen.setdefault("urls", []).append(url)
            if queue is not None:
                return queue.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())
    return seen


class TestTheDeploymentsOwnSandbox:
    """A deployment that runs `sandboxd` sets the connection up itself (#2070)."""

    def _setup(self, monkeypatch, *, url: str | None, owner: Any = "owner") -> tuple[Any, Any, Any]:
        service = _service(monkeypatch)
        monkeypatch.setattr(settings, "SANDBOXD_TOKEN", "sbx-token")
        made = SimpleNamespace(id=uuid.uuid4())
        defaults = [None, made]
        monkeypatch.setattr(
            sandbox_connection_repo,
            "get_default",
            AsyncMock(side_effect=lambda *a, **k: defaults.pop(0)),
        )
        monkeypatch.setattr(
            "app.services.sandbox_connection.member_repo.first_owner_id",
            AsyncMock(return_value=uuid.uuid4() if owner else None),
        )
        monkeypatch.setattr(
            SandboxConnectionService, "_first_answering", AsyncMock(return_value=url)
        )
        store = AsyncMock(return_value=SimpleNamespace(secret_id=uuid.uuid4()))
        create = AsyncMock()
        monkeypatch.setattr(service, "store_local_credential", store)
        monkeypatch.setattr(service, "create", create)
        return service, made, create

    async def test_it_is_registered_as_the_owner_and_becomes_the_default(self, monkeypatch):
        service, made, create = self._setup(monkeypatch, url="http://sandboxd:8080")

        assert await service.ensure_local_connection(uuid.uuid4()) is made

        owner, data = create.await_args.args
        assert owner.role == OrgRoleName.OWNER
        assert (data.base_url, data.kind, data.is_default) == (
            "http://sandboxd:8080",
            "docker",
            True,
        )

    async def test_nothing_is_set_up_without_a_service_answering_or_an_owner(self, monkeypatch):
        service, _made, create = self._setup(monkeypatch, url=None)
        assert await service.ensure_local_connection(uuid.uuid4()) is None

        service, _made, create = self._setup(monkeypatch, url="http://sandboxd:8080", owner=None)
        assert await service.ensure_local_connection(uuid.uuid4()) is None
        create.assert_not_awaited()

    async def test_a_race_for_the_name_uses_the_row_the_other_request_made(self, monkeypatch):
        service, made, create = self._setup(monkeypatch, url="http://sandboxd:8080")
        create.side_effect = AlreadyExistsError(message="taken")

        assert await service.ensure_local_connection(uuid.uuid4()) is made

    async def test_an_existing_default_is_left_alone(self, monkeypatch):
        service = _service(monkeypatch)
        existing = SimpleNamespace(id=uuid.uuid4())
        monkeypatch.setattr(
            sandbox_connection_repo, "get_default", AsyncMock(return_value=existing)
        )

        assert await service.ensure_local_connection(uuid.uuid4()) is existing

    async def test_the_list_sets_it_up_before_listing(self, monkeypatch):
        service = _service(monkeypatch)
        ensure = AsyncMock(return_value=None)
        monkeypatch.setattr(service, "ensure_local_connection", ensure)
        monkeypatch.setattr(
            sandbox_connection_repo, "list_for_organization", AsyncMock(return_value=[])
        )

        assert await service.list_connections(_ctx()) == []
        ensure.assert_awaited_once()
