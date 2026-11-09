"""Provider boundary tests: exact accounts, fixed origins, no leaked response secrets."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.exceptions import ConfigurationError, ExternalServiceError
from app.services.app_connections import (
    ComposioAccount,
    ComposioAccountMissing,
    ComposioClient,
    composio_user_id,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def provider_settings(monkeypatch):
    monkeypatch.setattr(settings, "COMPOSIO_API_KEY", SecretStr("synthetic-project-secret"))
    monkeypatch.setattr(settings, "COMPOSIO_USER_NAMESPACE", "unit-test")


def account(**overrides):
    return {
        "id": "ca_expected",
        "user_id": "member-1",
        "toolkit": {"slug": "gmail"},
        "auth_config": {"id": "ac_expected"},
        "status": "ACTIVE",
        "is_disabled": False,
        "state": {
            "val": {"displayName": "  Person\nExample  ", "access_token": "never-return-this"}
        },
        **overrides,
    }


async def verify(client):
    return await client.verify_account(
        user_id="member-1", toolkit="gmail", account_id="ca_expected", auth_config_id="ac_expected"
    )


@pytest.mark.parametrize("key", [None, SecretStr(""), SecretStr("   ")])
def test_requires_nonempty_server_credential(monkeypatch, key):
    monkeypatch.setattr(settings, "COMPOSIO_API_KEY", key)
    with pytest.raises(ConfigurationError, match="COMPOSIO_API_KEY"):
        ComposioClient()


def test_global_member_identity_is_namespaced():
    member = uuid4()
    assert composio_user_id(member) == f"unit-test:{member}"


async def test_exact_owner_query_discards_credentials_and_sanitizes_identity():
    def handler(request):
        assert (
            str(request.url.copy_with(query=None))
            == "https://backend.composio.dev/api/v3.1/connected_accounts"
        )
        assert dict(request.url.params) == {
            "user_ids": "member-1",
            "toolkit_slugs": "gmail",
            "auth_config_ids": "ac_expected",
            "connected_account_ids": "ca_expected",
            "limit": "2",
            "account_type": "PRIVATE",
        }
        assert request.headers["x-api-key"] == "synthetic-project-secret"
        return httpx.Response(200, json={"items": [account()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with ComposioClient(http) as client:
            result = await verify(client)
        assert not http.is_closed  # An injected transport retains its own lifetime.
    assert result.identity == "PersonExample"
    assert "never-return-this" not in result.model_dump_json()


async def test_deprecated_user_response_can_be_absent_but_query_is_owner_scoped():
    payload = account()
    del payload["user_id"]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"items": [payload]})
        )
    ) as http:
        result = await verify(ComposioClient(http))
    assert result.user_id is None


@pytest.mark.parametrize(
    "override",
    [
        {"id": "ca_other"},
        {"user_id": "another-member"},
        {"toolkit": {"slug": "slack"}},
        {"auth_config": {"id": "ac_other"}},
    ],
)
async def test_provider_account_must_match_every_saved_identifier(override):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"items": [account(**override)]})
        )
    ) as http:
        with pytest.raises(ExternalServiceError, match="did not match"):
            await verify(ComposioClient(http))


@pytest.mark.parametrize(
    "items,error", [([], ComposioAccountMissing), ([account(), account()], ExternalServiceError)]
)
async def test_missing_or_ambiguous_account_is_not_adopted(items, error):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"items": items}))
    ) as http:
        with pytest.raises(error):
            await verify(ComposioClient(http))


@pytest.mark.parametrize(
    "status,body",
    [
        (500, "credential=do-not-leak"),
        (302, "https://evil.example/token"),
        (200, "invalid-secret-json"),
    ],
)
async def test_provider_failures_are_sanitized_and_redirects_not_followed(status, body):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text=body, headers={"location": "https://evil.example"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as http:
        with pytest.raises(ExternalServiceError) as caught:
            await verify(ComposioClient(http))
    assert len(requests) == 1
    assert body not in str(caught.value)
    assert "synthetic-project-secret" not in repr(caught.value)


async def test_network_exception_does_not_expose_request_text():
    def handler(request):
        raise httpx.ConnectError("credential=do-not-leak", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ExternalServiceError) as caught:
            await verify(ComposioClient(http))
    assert "do-not-leak" not in str(caught.value)


async def test_link_uses_server_user_and_returns_only_safe_link_fields():
    def handler(request):
        assert json.loads(request.content) == {
            "user_id": "member-1",
            "auth_config_id": "ac_expected",
            "callback_url": "https://app.example/connectors?connection_attempt=example",
        }
        return httpx.Response(
            201,
            json={
                "connected_account_id": "ca_expected",
                "redirect_url": "https://connect.composio.dev/link/synthetic",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                "link_token": "internal-link-secret",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ComposioClient(http).link(
            user_id="member-1",
            auth_config_id="ac_expected",
            callback_url="https://app.example/connectors?connection_attempt=example",
        )
    assert result.connected_account_id == "ca_expected"
    assert "internal-link-secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    "url",
    [
        "http://connect.composio.dev/link/x",
        "https://evil.example/link/x",
        "https://user@connect.composio.dev/link/x",
        "https://connect.composio.dev:444/link/x",
        "https://connect.composio.dev/link/x#fragment",
        "https://connect.composio.dev:bad/link/x",
        "https://connect.composio.dev/link/\tx",
    ],
)
async def test_invalid_consent_destinations_are_refused(url):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                201,
                json={
                    "connected_account_id": "ca_expected",
                    "redirect_url": url,
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        )
    ) as http:
        with pytest.raises(ExternalServiceError, match="invalid link"):
            await ComposioClient(http).link(
                user_id="member-1",
                auth_config_id="ac_expected",
                callback_url="https://app.example/connectors",
            )


async def test_identity_session_is_posted_to_fixed_api_never_fetched():
    def handler(request):
        assert (
            str(request.url)
            == "https://backend.composio.dev/api/v3.1/connected_accounts/complete_auth"
        )
        assert json.loads(request.content) == {
            "session_uri": "https://untrusted.example/opaque",
            "user_id": "member-1",
        }
        return httpx.Response(
            200, json={"connected_account_id": "ca_expected", "toolkit_slug": "gmail"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ComposioClient(http).complete_auth(
            session_uri="https://untrusted.example/opaque", user_id="member-1"
        )
    assert result.toolkit_slug == "gmail"


@pytest.mark.parametrize("status", [200, 204, 404])
async def test_disconnect_is_idempotent_when_provider_already_removed_account(status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status))
    ) as http:
        await ComposioClient(http).delete_account("ca_expected")


@pytest.mark.parametrize("account_id", ["", "../another", "x?secret=1", "café"])
async def test_delete_identifier_cannot_change_request_path(account_id):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("must not call provider"))
    ) as http:
        with pytest.raises(ExternalServiceError, match="identifier"):
            await ComposioClient(http).delete_account(account_id)


async def test_direct_session_pins_exact_account_and_disables_ambient_execution():
    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == "https://backend.composio.dev/api/v3.1/tool_router/session"
        assert body == {
            "user_id": "member-1",
            "toolkits": {"enable": ["gmail"]},
            "tools": {"gmail": {"enable": ["GMAIL_FETCH_EMAILS"]}},
            "connected_accounts": {"gmail": ["ca_expected"]},
            "auth_configs": {"gmail": "ac_expected"},
            "manage_connections": {"enable": False},
            "workbench": {"enable": False},
            "preload": {"tools": "all"},
            "search": {"enable": False},
            "execute": {"enable_multi_execute": False},
            "proxy_execute": {"enable": False},
            "instant": False,
        }
        return httpx.Response(
            200,
            json={"mcp": {"url": "https://backend.composio.dev/tool_router/session/synthetic/mcp"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ComposioClient(http).create_session(
            user_id="member-1",
            toolkit="gmail",
            account_id="ca_expected",
            auth_config_id="ac_expected",
            tools=["GMAIL_FETCH_EMAILS"],
        )
    assert result.startswith("https://backend.composio.dev/")


async def test_session_cannot_default_to_all_tools_or_attach_key_to_foreign_host():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"mcp": {"url": "https://evil.example/mcp"}})
        )
    ) as http:
        client = ComposioClient(http)
        with pytest.raises(ValueError, match="nonempty"):
            await client.create_session(
                user_id="member-1",
                toolkit="gmail",
                account_id="ca_expected",
                auth_config_id="ac_expected",
                tools=[],
            )
        with pytest.raises(ExternalServiceError, match="invalid link"):
            await client.create_session(
                user_id="member-1",
                toolkit="gmail",
                account_id="ca_expected",
                auth_config_id="ac_expected",
                tools=["GMAIL_FETCH_EMAILS"],
            )


async def test_owned_http_client_is_closed_after_use():
    client = ComposioClient()
    async with client:
        assert not client._client.is_closed
    assert client._client.is_closed


async def test_naive_provider_expiration_is_refused():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                201,
                json={
                    "connected_account_id": "ca_expected",
                    "redirect_url": "https://connect.composio.dev/link/synthetic",
                    "expires_at": "2099-01-01T00:00:00",
                },
            )
        )
    ) as http:
        with pytest.raises(ExternalServiceError, match="expiration"):
            await ComposioClient(http).link(
                user_id="member-1",
                auth_config_id="ac_expected",
                callback_url="https://app.example/connectors",
            )


@pytest.mark.parametrize("network", [False, True])
async def test_delete_errors_are_sanitized_without_retry(network):
    calls = []

    def handler(request):
        calls.append(request)
        if network:
            raise httpx.ConnectError("private-server-secret", request=request)
        return httpx.Response(500, text="private-server-secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ExternalServiceError) as caught:
            await ComposioClient(http).delete_account("ca_expected")
    assert len(calls) == 1 and "private-server-secret" not in str(caught.value)


@pytest.mark.parametrize("state", [None, {"val": {}}, {"val": {"displayName": "\n\t "}}])
def test_missing_identity_does_not_invent_an_email(state):
    assert ComposioAccount.model_validate(account(state=state)).identity is None
