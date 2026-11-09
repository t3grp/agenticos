"""The small, typed subset of Composio v3.1 that T3OS uses.

The project credential never leaves the backend. Provider responses can contain
OAuth credentials, so parse only the fields below and never forward their body or
exception text. The provider hosts account credentials; our vault holds only a
short-lived, owner-bound record of the consent attempt.
"""

from datetime import datetime
from types import TracebackType
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

from app.core.config import settings
from app.core.exceptions import ConfigurationError, ExternalServiceError

_BASE_URL = "https://backend.composio.dev/api/v3.1"
_PROVIDER_ERROR = "The app connection provider could not complete this request. Try again."


def composio_user_id(user_id: UUID) -> str:
    """A member's global identity, independent of their currently open workspace."""
    return f"{settings.COMPOSIO_USER_NAMESPACE}:{user_id}"


class _Toolkit(BaseModel):
    slug: str


class _AuthConfig(BaseModel):
    id: str
    is_disabled: bool = False


class _IdentityValue(BaseModel):
    display_name: str | None = Field(default=None, alias="displayName")


class _IdentityState(BaseModel):
    val: _IdentityValue = Field(default_factory=_IdentityValue)


class ComposioAccount(BaseModel):
    """Safe account metadata, deliberately excluding tokens and provider errors."""

    id: str
    toolkit: _Toolkit
    auth_config: _AuthConfig
    user_id: str | None = None
    status: Literal[
        "INITIALIZING", "INITIATED", "ACTIVE", "FAILED", "EXPIRED", "INACTIVE", "REVOKED"
    ]
    is_disabled: bool
    state: _IdentityState | None = None

    @property
    def identity(self) -> str | None:
        value = self.state.val.display_name if self.state is not None else None
        if not value:
            return None
        # A provider's display name is useful to distinguish accounts, but is
        # still untrusted text. It is neither a verified email nor a credential.
        cleaned = "".join(char for char in value if char.isprintable()).strip()
        return cleaned[:254] or None


class _Accounts(BaseModel):
    items: list[ComposioAccount]


class ComposioLink(BaseModel):
    connected_account_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    redirect_url: str
    expires_at: datetime


class ComposioCompletedAuth(BaseModel):
    connected_account_id: str
    toolkit_slug: str


class _McpEndpoint(BaseModel):
    url: str


class _Session(BaseModel):
    mcp: _McpEndpoint


class ComposioAccountMissing(ExternalServiceError):
    """The owner-scoped provider query no longer contains the saved account."""

    message = "This app authorization is no longer available. Connect the account again."
    code = "APP_AUTHORIZATION_MISSING"


def _checked_url(value: str, *, host: str) -> str:
    """Validate an exact origin before returning a link or attaching a credential."""
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == host
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and not any(char.isspace() or ord(char) < 32 for char in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ExternalServiceError(message="The app connection provider returned an invalid link")
    return value


class ComposioClient:
    """Fixed-origin HTTP with no automatic redirects or mutation retries."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        key = settings.COMPOSIO_API_KEY
        if key is None or not key.get_secret_value().strip():
            raise ConfigurationError(message="Set COMPOSIO_API_KEY to enable app connections")
        self.api_key: SecretStr = key
        self._client = client or httpx.AsyncClient(timeout=30.0, follow_redirects=False)
        self._owns_client = client is None

    async def __aenter__(self) -> ComposioClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._owns_client:
            await self._client.aclose()

    def mcp_headers(self) -> dict[str, str]:
        """Backend transport only; callers must validate every MCP destination."""
        return {"x-api-key": self.api_key.get_secret_value()}

    async def _request[T: BaseModel](
        self,
        method: str,
        path: str,
        schema: type[T],
        *,
        body: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> T:
        try:
            response = await self._client.request(
                method,
                f"{_BASE_URL}{path}",
                headers=self.mcp_headers(),
                json=body,
                params=params,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise ExternalServiceError(message=_PROVIDER_ERROR) from None
        if not 200 <= response.status_code < 300:
            # Neither the upstream body nor exception string is safe: either
            # may repeat a credential, consent URL, or another person's data.
            raise ExternalServiceError(message=_PROVIDER_ERROR)
        try:
            return schema.model_validate_json(response.content)
        except ValidationError:
            raise ExternalServiceError(
                message="The app connection provider returned an invalid response"
            ) from None

    async def link(self, *, user_id: str, auth_config_id: str, callback_url: str) -> ComposioLink:
        link = await self._request(
            "POST",
            "/connected_accounts/link",
            ComposioLink,
            body={
                "user_id": user_id,
                "auth_config_id": auth_config_id,
                "callback_url": callback_url,
            },
        )
        _checked_url(link.redirect_url, host="connect.composio.dev")
        if link.expires_at.tzinfo is None:
            raise ExternalServiceError(
                message="The app connection provider returned an invalid expiration"
            )
        return link

    async def verify_account(
        self, *, user_id: str, toolkit: str, account_id: str, auth_config_id: str
    ) -> ComposioAccount:
        """Resolve the exact saved grant through an owner-scoped provider query.

        Composio deprecated the response's ``user_id`` field. The server-side
        user filter therefore carries the owner check even when that field is
        omitted; if present, it must also match. Never query an account by id
        alone and infer its owner from a browser callback.
        """
        result = await self._request(
            "GET",
            "/connected_accounts",
            _Accounts,
            params={
                "user_ids": user_id,
                "toolkit_slugs": toolkit,
                "auth_config_ids": auth_config_id,
                "connected_account_ids": account_id,
                "limit": "2",
                "account_type": "PRIVATE",
            },
        )
        if not result.items:
            raise ComposioAccountMissing()
        if len(result.items) != 1:
            raise ExternalServiceError(message="The app account did not match the saved connection")
        account = result.items[0]
        if (
            account.id != account_id
            or account.toolkit.slug != toolkit
            or account.auth_config.id != auth_config_id
            or account.user_id not in (None, user_id)
        ):
            raise ExternalServiceError(message="The app account did not match the saved connection")
        return account

    async def complete_auth(self, *, session_uri: str, user_id: str) -> ComposioCompletedAuth:
        # session_uri is opaque input to the fixed provider API, never a URL
        # fetched by this server. The provider checks its signed, single-use
        # identity verification session against the authenticated T3OS member.
        return await self._request(
            "POST",
            "/connected_accounts/complete_auth",
            ComposioCompletedAuth,
            body={"session_uri": session_uri, "user_id": user_id},
        )

    async def delete_account(self, account_id: str) -> None:
        if not account_id or not all(
            char.isascii() and (char.isalnum() or char in "_-") for char in account_id
        ):
            raise ExternalServiceError(message="The saved app account identifier is invalid")
        try:
            response = await self._client.delete(
                f"{_BASE_URL}/connected_accounts/{account_id}",
                headers=self.mcp_headers(),
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise ExternalServiceError(message=_PROVIDER_ERROR) from None
        if response.status_code != 404 and not 200 <= response.status_code < 300:
            raise ExternalServiceError(message=_PROVIDER_ERROR)

    async def create_session(
        self,
        *,
        user_id: str,
        toolkit: str,
        account_id: str,
        auth_config_id: str,
        tools: list[str],
    ) -> str:
        """One explicitly selected account and an exact list of direct tools.

        Callers intersect their runtime policy before entering here. No
        discovery, connection manager, code executor, proxy, or batch meta-tool
        may expand that list after T3OS has applied its approval policy.
        """
        if not tools:
            raise ValueError("A managed app session requires an explicit nonempty tool list")
        result = await self._request(
            "POST",
            "/tool_router/session",
            _Session,
            body={
                "user_id": user_id,
                "toolkits": {"enable": [toolkit]},
                "tools": {toolkit: {"enable": tools}},
                "connected_accounts": {toolkit: [account_id]},
                "auth_configs": {toolkit: auth_config_id},
                "manage_connections": {"enable": False},
                "workbench": {"enable": False},
                "preload": {"tools": "all"},
                "search": {"enable": False},
                "execute": {"enable_multi_execute": False},
                "proxy_execute": {"enable": False},
                "instant": False,
            },
        )
        return _checked_url(result.mcp.url, host="backend.composio.dev")
