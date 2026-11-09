"""Personal app consent lives on the same records agents already bind to.

An authorization attempt is sealed to its member and expires after ten minutes.
Starting a reconnect never replaces a working grant; only verified completion
promotes the new account and rotates its authorization generation.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    BadRequestError,
    ConfigurationError,
    ExternalServiceError,
    NotFoundError,
)
from app.core.vault import VaultScope, current_key_version, seal, unseal
from app.db.models.mcp_connection import McpConnection
from app.repositories import mcp_connection as connection_repo
from app.schemas.app_connection import (
    AppActionRead,
    AppCatalogEntry,
    AppCatalogRead,
    AppConnectionCallback,
    AppConnectionCreate,
    AppConnectionList,
    AppConnectionRead,
    AppConnectionStartRead,
)
from app.services.impersonation import refuse_binding_while_impersonating

from ._catalog import APPS, ComposioApp, get_app, get_app_by_catalog_key
from ._client import ComposioAccount, ComposioAccountMissing, ComposioClient, composio_user_id

_ATTEMPT_TTL = timedelta(minutes=10)
_MAX_ACCOUNTS = 50
_VERIFY_ERROR = "The app authorization could not be checked. Try checking the connection again."


class _PendingConsent(BaseModel):
    attempt_id: UUID
    user_id: UUID
    toolkit: str
    auth_config_id: str
    account_id: str
    expires_at: datetime
    identity_verified: bool = False


def is_configured(toolkit: str) -> bool:
    """The same operator gate for the browser catalog and runtime execution."""
    key = settings.COMPOSIO_API_KEY
    return bool(
        key is not None
        and key.get_secret_value().strip()
        and settings.COMPOSIO_AUTH_CONFIGS.get(toolkit, "").strip()
    )


def _auth_config(toolkit: str) -> str:
    if not is_configured(toolkit):
        raise ConfigurationError(
            message="Set COMPOSIO_API_KEY and this app's COMPOSIO_AUTH_CONFIGS entry to enable it"
        )
    return settings.COMPOSIO_AUTH_CONFIGS[toolkit].strip()


def _app(connection: McpConnection) -> ComposioApp:
    app = get_app_by_catalog_key(connection.catalog_key)
    if app is None:
        raise BadRequestError(message="This managed app is no longer in the supported catalog")
    return app


def _pending(connection: McpConnection, *, user_id: UUID) -> _PendingConsent:
    if not connection.oauth_pending_payload or not connection.oauth_state:
        raise BadRequestError(message="This connection attempt is no longer available")
    try:
        pending = _PendingConsent.model_validate_json(
            unseal(
                connection.oauth_pending_payload,
                scope=VaultScope.user(user_id),
                key_version=connection.secret_key_version,
            )
        )
    except ValidationError, BadRequestError:
        raise BadRequestError(message="This connection attempt is no longer available") from None
    if (
        pending.user_id != user_id
        or str(pending.attempt_id) != connection.oauth_state
        or pending.toolkit != _app(connection).toolkit
        or pending.expires_at.tzinfo is None
    ):
        raise BadRequestError(message="This connection attempt did not match the saved account")
    return pending


def _require_live(pending: _PendingConsent) -> None:
    if pending.expires_at <= datetime.now(UTC):
        raise BadRequestError(message="This connection attempt expired. Connect the app again.")


def _sealed_pending(connection: McpConnection, pending: _PendingConsent) -> str:
    return seal(
        pending.model_dump_json(),
        scope=VaultScope.user(pending.user_id),
        key_version=connection.secret_key_version,
    ).ciphertext


def _read(connection: McpConnection) -> AppConnectionRead:
    app = _app(connection)
    authorized = connection.account_authorized
    status: Literal["pending", "connected", "expired", "failed", "disabled"] = "pending"
    error = connection.last_error
    if not connection.is_enabled or connection.composio_status == "INACTIVE":
        status = "disabled"
    elif authorized:
        status = "connected"
    elif connection.composio_status in {"EXPIRED", "REVOKED"}:
        status = "expired"
    elif connection.composio_status == "FAILED":
        status = "failed"
    elif connection.oauth_pending_payload and connection.user_id is not None:
        try:
            if _pending(connection, user_id=connection.user_id).expires_at <= datetime.now(UTC):
                status = "expired"
        except BadRequestError:
            status = "failed"
            error = "This connection attempt is no longer available. Connect the app again."
    readiness: Literal["authorization_required", "authorization_verified", "unavailable"] = (
        "unavailable"
        if not is_configured(app.toolkit)
        or not connection.is_enabled
        or connection.last_status == "error"
        else "authorization_verified"
        if authorized
        else "authorization_required"
    )
    return AppConnectionRead(
        id=connection.id,
        catalog_key=app.catalog_key,
        toolkit=app.toolkit,
        name=app.name,
        label=connection.label,
        identity=connection.account_email,
        status=status,
        is_enabled=connection.is_enabled,
        is_default=connection.is_default,
        authorized=authorized,
        readiness=readiness,
        last_checked_at=connection.last_checked_at,
        last_error=error,
        created_at=connection.created_at,
    )


class AppConnectionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def catalog(self) -> AppCatalogRead:
        apps = [
            AppCatalogEntry(
                key=app.catalog_key,
                toolkit=app.toolkit,
                name=app.name,
                description=app.description,
                category=app.category,
                configured=is_configured(app.toolkit),
                actions=[
                    AppActionRead(name=action.name, label=action.label, read_only=action.read_only)
                    for action in app.actions
                ],
            )
            for app in APPS
        ]
        return AppCatalogRead(configured=any(app.configured for app in apps), apps=apps)

    async def list_for_user(self, *, user_id: UUID) -> AppConnectionList:
        connections = await connection_repo.list_composio_for_user(self.db, user_id=user_id)
        return AppConnectionList(
            items=[_read(connection) for connection in connections], total=len(connections)
        )

    async def _owned(self, *, user_id: UUID, connection_id: UUID) -> McpConnection:
        connection = await connection_repo.get_by_id_for_update(self.db, connection_id)
        if (
            connection is None
            or connection.scope != "user"
            or connection.user_id != user_id
            or connection.organization_id is not None
            or connection.purpose != "mcp"
            or connection.auth_type != "composio"
        ):
            raise NotFoundError(message="App connection not found")
        _app(connection)
        return connection

    async def connect(self, *, user_id: UUID, data: AppConnectionCreate) -> AppConnectionStartRead:
        refuse_binding_while_impersonating("Connecting an app")
        app = get_app(data.toolkit)
        auth_config_id = _auth_config(app.toolkit)
        await connection_repo.lock_account_owner(self.db, user_id=user_id)
        connections = await connection_repo.list_composio_for_user(self.db, user_id=user_id)
        if len(connections) >= _MAX_ACCOUNTS:
            raise BadRequestError(message="Remove an unused app connection before adding another")
        siblings = [
            connection for connection in connections if connection.catalog_key == app.catalog_key
        ]
        connection = await connection_repo.create(
            self.db,
            user_id=user_id,
            name=f"{app.toolkit}-{uuid4().hex[:12]}",
            # Stable identity only. Runtime creates a separately scoped MCP
            # endpoint after resolving this member's account and agent policy.
            url=f"https://backend.composio.dev/api/v3.1/toolkits/{app.toolkit}",
            auth_token=None,
            secret_key_version=current_key_version(),
            allowed_tools=[action.name for action in app.actions],
            auth_type="composio",
            catalog_key=app.catalog_key,
            label=data.label or (f"Account {len(siblings) + 1}" if siblings else None),
        )
        await connection_repo.update(
            self.db,
            db_connection=connection,
            update_data={
                "composio_auth_config_id": auth_config_id,
                "composio_status": "INITIATED",
                "is_default": not siblings,
            },
        )
        return await self._start(connection, user_id=user_id, auth_config_id=auth_config_id)

    async def reconnect(self, *, user_id: UUID, connection_id: UUID) -> AppConnectionStartRead:
        refuse_binding_while_impersonating("Connecting an app")
        await connection_repo.lock_account_owner(self.db, user_id=user_id)
        connection = await self._owned(user_id=user_id, connection_id=connection_id)
        return await self._start(
            connection, user_id=user_id, auth_config_id=_auth_config(_app(connection).toolkit)
        )

    async def _start(
        self, connection: McpConnection, *, user_id: UUID, auth_config_id: str
    ) -> AppConnectionStartRead:
        attempt_id = uuid4()
        callback_url = (
            f"{str(settings.FRONTEND_URL).rstrip('/')}/connectors?connection_attempt={attempt_id}"
        )
        async with ComposioClient() as client:
            # A second attempt replaces the pending grant, never the active one.
            # Clean it up first so abandoned reconnects do not accumulate grants.
            if connection.oauth_pending_payload:
                previous = _pending(connection, user_id=user_id)
                if previous.account_id != connection.composio_account_id:
                    await client.delete_account(previous.account_id)
            link = await client.link(
                user_id=composio_user_id(user_id),
                auth_config_id=auth_config_id,
                callback_url=callback_url,
            )
        expires_at = min(link.expires_at, datetime.now(UTC) + _ATTEMPT_TTL)
        if expires_at <= datetime.now(UTC):
            raise ExternalServiceError(
                message="The app connection provider returned an expired link"
            )
        pending = _PendingConsent(
            attempt_id=attempt_id,
            user_id=user_id,
            toolkit=_app(connection).toolkit,
            auth_config_id=auth_config_id,
            account_id=link.connected_account_id,
            expires_at=expires_at,
        )
        await connection_repo.update(
            self.db,
            db_connection=connection,
            update_data={
                "oauth_state": str(attempt_id),
                "oauth_pending_payload": _sealed_pending(connection, pending),
            },
        )
        return AppConnectionStartRead(
            connection_id=connection.id,
            attempt_id=attempt_id,
            redirect_url=link.redirect_url,
            expires_at=expires_at,
        )

    async def callback(self, *, user_id: UUID, data: AppConnectionCallback) -> AppConnectionRead:
        refuse_binding_while_impersonating("Connecting an app")
        await connection_repo.lock_account_owner(self.db, user_id=user_id)
        pending_candidates: list[tuple[McpConnection, _PendingConsent]] = []
        if data.attempt_id is not None:
            candidate = await connection_repo.get_by_oauth_state(self.db, str(data.attempt_id))
            if candidate is None:
                raise NotFoundError(message="Connection attempt not found")
            connection = await self._owned(user_id=user_id, connection_id=candidate.id)
            pending = _pending(connection, user_id=user_id)
            _require_live(pending)
            if (
                data.connected_account_id is not None
                and data.connected_account_id != pending.account_id
            ):
                raise BadRequestError(
                    message="The returned account did not match this connection attempt"
                )
            pending_candidates.append((connection, pending))
        else:
            # A project identity-verifier URL is static: Composio sends
            # session_uri without our per-attempt query string. Look only among
            # this member's live attempts, then match its signed response.
            for candidate in await connection_repo.list_composio_for_user(self.db, user_id=user_id):
                if not candidate.oauth_pending_payload:
                    continue
                try:
                    pending = _pending(candidate, user_id=user_id)
                except BadRequestError:
                    # An unrelated abandoned/corrupt attempt must not prevent
                    # the owner finishing a different account.
                    continue
                if pending.expires_at > datetime.now(UTC):
                    pending_candidates.append((candidate, pending))
        if data.session_uri is None:
            raise BadRequestError(
                message="Complete the app's identity verification before connecting"
            )
        if not pending_candidates:
            raise BadRequestError(message="No live connection attempt belongs to this account")
        async with ComposioClient() as client:
            completed = await client.complete_auth(
                session_uri=data.session_uri.get_secret_value(),
                user_id=composio_user_id(user_id),
            )
            matches = [
                (candidate, pending)
                for candidate, pending in pending_candidates
                if pending.account_id == completed.connected_account_id
                and pending.toolkit == completed.toolkit_slug
            ]
            if len(matches) != 1:
                raise BadRequestError(
                    message="The verified account did not match a saved connection attempt"
                )
            candidate, pending = matches[0]
            connection = await self._owned(user_id=user_id, connection_id=candidate.id)
            # Re-read under the row lock: an abandoned or replaced attempt must
            # not be resurrected by an older browser tab.
            current = _pending(connection, user_id=user_id)
            if current.attempt_id != pending.attempt_id:
                raise BadRequestError(message="This connection attempt has been replaced")
            if (
                data.connected_account_id is not None
                and data.connected_account_id != pending.account_id
            ):
                raise BadRequestError(
                    message="The returned account did not match this connection attempt"
                )
            pending.identity_verified = True
            await connection_repo.update(
                self.db,
                db_connection=connection,
                update_data={"oauth_pending_payload": _sealed_pending(connection, pending)},
            )
            return await self._finish(connection, user_id=user_id, client=client)

    async def _finish(
        self, connection: McpConnection, *, user_id: UUID, client: ComposioClient
    ) -> AppConnectionRead:
        refuse_binding_while_impersonating("Connecting an app")
        pending = _pending(connection, user_id=user_id)
        _require_live(pending)
        if not pending.identity_verified:
            return _read(connection)
        try:
            account = await client.verify_account(
                user_id=composio_user_id(user_id),
                toolkit=pending.toolkit,
                account_id=pending.account_id,
                auth_config_id=pending.auth_config_id,
            )
        except ExternalServiceError:
            # The identity session is single-use. Preserve its successful
            # verification on a transient metadata failure so Refresh can finish
            # the same saved attempt without accepting an unverified grant.
            await connection_repo.update(
                self.db,
                db_connection=connection,
                update_data={
                    "last_status": "error",
                    "last_error": _VERIFY_ERROR,
                    "last_checked_at": datetime.now(UTC),
                },
            )
            return _read(connection)
        if account.status != "ACTIVE" or account.is_disabled or account.auth_config.is_disabled:
            await connection_repo.update(
                self.db,
                db_connection=connection,
                update_data={
                    "last_status": "error",
                    "last_error": "The app has not granted access. Complete authorization or connect again.",
                    "last_checked_at": datetime.now(UTC),
                    **(
                        {"composio_status": account.status}
                        if not connection.composio_account_id
                        else {}
                    ),
                },
            )
            return _read(connection)
        previous_id = connection.composio_account_id
        if previous_id is not None and previous_id != account.id:
            try:
                await client.delete_account(previous_id)
            except ExternalServiceError:
                # Retain the working old grant and the verified pending attempt;
                # Refresh retries cleanup before switching to the new identity.
                await connection_repo.update(
                    self.db,
                    db_connection=connection,
                    update_data={
                        "last_status": "error",
                        "last_error": "The previous authorization could not be removed. Check the connection again.",
                    },
                )
                return _read(connection)
        await connection_repo.update(
            self.db,
            db_connection=connection,
            update_data={
                "composio_account_id": account.id,
                "composio_auth_config_id": pending.auth_config_id,
                "composio_status": "ACTIVE",
                "account_email": account.identity,
                "authorization_generation": uuid4(),
                "last_tools": None,
                "oauth_state": None,
                "oauth_pending_payload": None,
                "last_status": "ok",
                "last_error": None,
                "last_checked_at": datetime.now(UTC),
                "unreachable_since": None,
            },
        )
        return _read(connection)

    async def refresh(self, *, user_id: UUID, connection_id: UUID) -> AppConnectionRead:
        await connection_repo.lock_account_owner(self.db, user_id=user_id)
        connection = await self._owned(user_id=user_id, connection_id=connection_id)
        async with ComposioClient() as client:
            if connection.oauth_pending_payload:
                pending = _pending(connection, user_id=user_id)
                if pending.identity_verified and pending.expires_at > datetime.now(UTC):
                    return await self._finish(connection, user_id=user_id, client=client)
            if connection.composio_account_id is None or connection.composio_auth_config_id is None:
                return _read(connection)
            try:
                account = await client.verify_account(
                    user_id=composio_user_id(user_id),
                    toolkit=_app(connection).toolkit,
                    account_id=connection.composio_account_id,
                    auth_config_id=connection.composio_auth_config_id,
                )
            except ComposioAccountMissing:
                await connection_repo.update(
                    self.db,
                    db_connection=connection,
                    update_data={
                        "composio_status": "REVOKED",
                        "authorization_generation": uuid4(),
                        "last_status": "error",
                        "last_error": "The app authorization is no longer available. Connect again.",
                        "last_checked_at": datetime.now(UTC),
                    },
                )
            except ExternalServiceError:
                await connection_repo.update(
                    self.db,
                    db_connection=connection,
                    update_data={
                        "last_status": "error",
                        "last_error": _VERIFY_ERROR,
                        "last_checked_at": datetime.now(UTC),
                    },
                )
            else:
                await self._record_health(connection, account)
        return _read(connection)

    async def _record_health(self, connection: McpConnection, account: ComposioAccount) -> None:
        status = (
            "INACTIVE" if account.is_disabled or account.auth_config.is_disabled else account.status
        )
        await connection_repo.update(
            self.db,
            db_connection=connection,
            update_data={
                "composio_status": status,
                "account_email": account.identity or connection.account_email,
                "last_status": "ok" if status == "ACTIVE" else "error",
                "last_error": None if status == "ACTIVE" else "This app needs authorization again.",
                "last_checked_at": datetime.now(UTC),
                **(
                    {"authorization_generation": uuid4()}
                    if connection.composio_status != status
                    else {}
                ),
            },
        )

    async def delete(self, *, user_id: UUID, connection_id: UUID) -> None:
        await connection_repo.lock_account_owner(self.db, user_id=user_id)
        connection = await self._owned(user_id=user_id, connection_id=connection_id)
        accounts: set[str] = set()
        if connection.composio_account_id is not None:
            accounts.add(connection.composio_account_id)
        if connection.oauth_pending_payload:
            accounts.add(_pending(connection, user_id=user_id).account_id)
        if accounts:
            async with ComposioClient() as client:
                for account_id in sorted(accounts):
                    await client.delete_account(account_id)
        # Delete removes the credential from Composio and the personal binding
        # from T3OS. Provider-wide consent revocation is not universally supported.
        await connection_repo.delete(self.db, db_connection=connection)
