"""Saved browser credentials stay owner-bound and reach only selected fields."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.agents.spec import AgentSpec
from app.core.exceptions import AuthorizationError, BadRequestError
from app.core.permissions import AuthContext, OrgRoleName
from app.core.secret_kinds import (
    BrowserLoginSecret,
    BrowserProfileSecret,
    BrowserTotpSecret,
    browser_page_origin,
    seal_secret,
    unseal_kind,
)
from app.core.vault import browser_credential_scope
from app.db.models.organization_secret import OrganizationSecret
from app.db.models.resource_grant import Visibility
from app.services.agent_registry import AgentRegistryService
from app.services.organization_secret import (
    OrganizationSecretService,
    SecretSharingGuard,
    _browser_fill_values,
    _totp_code,
)
from app.services.sharing import ShareChange


def test_browser_page_origin_discards_path_query_and_fragment() -> None:
    assert (
        browser_page_origin("https://Portal.Example.test:443/login?next=%2Faccount#signin")
        == "https://portal.example.test"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://portal.example.test/login",
        "https://user:password@portal.example.test/login",
        "https://127.0.0.1/login",
        "https://[::ffff:192.168.1.20]/login",
        "https://localhost/login",
    ],
)
@pytest.mark.security
def test_browser_page_origin_refuses_non_public_or_ambiguous_urls(url: str) -> None:
    with pytest.raises(ValueError):
        browser_page_origin(url)


@pytest.mark.security
def test_browser_login_unwrap_is_bound_to_its_organization_and_owner() -> None:
    organization_id = uuid4()
    owner_id = uuid4()
    value = BrowserLoginSecret(
        username="ada@example.test",
        password="a-private-password",
        allowed_origins=["https://portal.example.test"],
    )
    scope = browser_credential_scope(organization_id, owner_id, personal=True)
    sealed = seal_secret(value, scope=scope)

    assert (
        unseal_kind(
            sealed.ciphertext,
            model=BrowserLoginSecret,
            scope=scope,
            key_version=sealed.key_version,
        )
        == value
    )
    for other_scope in (
        browser_credential_scope(uuid4(), owner_id, personal=True),
        browser_credential_scope(organization_id, uuid4(), personal=True),
    ):
        with pytest.raises(BadRequestError):
            unseal_kind(
                sealed.ciphertext,
                model=BrowserLoginSecret,
                scope=other_scope,
                key_version=sealed.key_version,
            )


@pytest.mark.security
def test_browser_secret_repr_and_serialized_metadata_hide_values() -> None:
    password = "a-private-password"
    totp_seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    login = BrowserLoginSecret(
        username="ada@example.test",
        password=password,
        allowed_origins=["https://portal.example.test"],
        totp={"seed": totp_seed},
    )
    profile_value = "Ada Lovelace"
    profile = BrowserProfileSecret(
        full_name=profile_value,
        allowed_origins=["https://portal.example.test"],
    )

    for rendered in (
        repr(login),
        str(login.model_dump()),
        repr(profile),
        str(profile.model_dump()),
    ):
        assert password not in rendered
        assert totp_seed not in rendered
        assert profile_value not in rendered


@pytest.mark.security
def test_browser_origins_are_canonical_and_must_not_point_to_private_addresses() -> None:
    login = BrowserLoginSecret(
        username="ada@example.test",
        password="a-private-password",
        allowed_origins=["HTTPS://Portal.Example.TEST:443/"],
    )
    assert login.allowed_origins == ("https://portal.example.test",)

    with pytest.raises(ValidationError):
        BrowserLoginSecret(
            username="ada@example.test",
            password="a-private-password",
            allowed_origins=["https://192.168.1.20"],
        )


def test_login_fill_can_send_only_a_password_for_a_second_step() -> None:
    login = BrowserLoginSecret(
        username="ada@example.test",
        password="a-private-password",
        allowed_origins=["https://portal.example.test"],
    )

    assert _browser_fill_values(login, {"password": 4})["password"].get_secret_value() == (
        "a-private-password"
    )


def test_profile_fill_returns_only_selected_fields() -> None:
    profile = BrowserProfileSecret(
        full_name="Ada Lovelace",
        email="ada@example.test",
        phone="+1 555 0100",
        allowed_origins=["https://portal.example.test"],
    )

    assert _browser_fill_values(profile, {"email": 3})["email"].get_secret_value() == (
        "ada@example.test"
    )


@pytest.mark.security
def test_login_fill_refuses_unrequested_or_unavailable_fields() -> None:
    login = BrowserLoginSecret(
        username="ada@example.test",
        password="a-private-password",
        allowed_origins=["https://portal.example.test"],
    )

    with pytest.raises(BadRequestError):
        _browser_fill_values(login, {"username": 1})
    with pytest.raises(BadRequestError):
        _browser_fill_values(login, {"totp": 2})


def test_totp_code_matches_the_rfc_6238_sha1_test_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    login = BrowserLoginSecret(
        username="ada@example.test",
        password="a-private-password",
        allowed_origins=["https://portal.example.test"],
        totp=BrowserTotpSecret(
            seed="GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
            algorithm="SHA1",
            digits=8,
            period_seconds=30,
        ),
    )
    monkeypatch.setattr("app.services.organization_secret.time.time", lambda: 59)

    assert _totp_code(login) == "94287082"


@pytest.mark.anyio
@pytest.mark.security
async def test_browser_picker_hides_a_binding_removed_from_the_current_agent_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    agent_id = uuid4()
    bound_id = uuid4()
    revoked_id = uuid4()
    version_id = uuid4()
    db = MagicMock()
    agent = SimpleNamespace(current_version_id=version_id)
    spec = AgentSpec(
        name="Synthetic browser agent",
        capabilities=[
            {
                "id": "browser_use",
                "config": {
                    "connection": "built_in",
                    "browser_credential_ids": [str(bound_id)],
                },
            }
        ],
    )
    version = SimpleNamespace(spec=spec.model_dump(mode="json"))
    row = SimpleNamespace(
        id=bound_id,
        organization_id=organization_id,
        kind="browser_login",
        authorization_generation=uuid4(),
        name="Synthetic portal",
        usage_scope="organization",
        visibility="org",
        owner_user_id=None,
    )
    login = BrowserLoginSecret(
        username="synthetic-user@example.test",
        password="synthetic-password-value",
        allowed_origins=["https://portal.example.test"],
        account_policy="named_agents",
        authorized_agent_ids=[agent_id],
    )
    get_many = AsyncMock(return_value={bound_id: row})
    monkeypatch.setattr(
        "app.services.organization_secret.member_repo.get_active",
        AsyncMock(return_value=SimpleNamespace(id=user_id)),
    )
    monkeypatch.setattr(
        "app.services.organization_secret.agent_repo.get", AsyncMock(return_value=agent)
    )
    monkeypatch.setattr(
        "app.services.organization_secret.agent_repo.get_version",
        AsyncMock(return_value=version),
    )
    monkeypatch.setattr(
        "app.services.organization_secret.organization_secret_repo.get_many", get_many
    )
    service = OrganizationSecretService(db)
    monkeypatch.setattr(service, "_unseal_browser_row", MagicMock(return_value=login))

    options = await service.browser_credentials(
        organization_id=organization_id,
        user_id=user_id,
        agent_id=agent_id,
        secret_ids=(revoked_id, bound_id),
        origin="https://portal.example.test",
    )

    assert [option.id for option in options] == [bound_id]
    get_many.assert_awaited_once_with(db, [bound_id], organization_id=organization_id)


@pytest.mark.anyio
@pytest.mark.security
async def test_removed_member_gets_no_browser_credential_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id, user_id, agent_id, secret_id = (uuid4() for _ in range(4))
    db = MagicMock()
    get_active = AsyncMock(return_value=None)
    get_many = AsyncMock()
    monkeypatch.setattr("app.services.organization_secret.member_repo.get_active", get_active)
    monkeypatch.setattr(
        "app.services.organization_secret.organization_secret_repo.get_many", get_many
    )
    service = OrganizationSecretService(db)

    options = await service.browser_credentials(
        organization_id=organization_id,
        user_id=user_id,
        agent_id=agent_id,
        secret_ids=(secret_id,),
        origin="https://portal.example.test",
    )

    assert options == []
    get_active.assert_awaited_once_with(db, organization_id=organization_id, user_id=user_id)
    get_many.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.security
async def test_a_fill_refuses_a_revoked_binding_before_opening_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id, user_id, agent_id, secret_id, version_id = (uuid4() for _ in range(5))
    db = MagicMock()
    spec = AgentSpec(name="Synthetic browser agent", capabilities=[])
    agent = SimpleNamespace(current_version_id=version_id)
    version = SimpleNamespace(spec=spec.model_dump(mode="json"))
    get_secret = AsyncMock()
    unseal = MagicMock()
    monkeypatch.setattr(
        "app.services.organization_secret.member_repo.get_active",
        AsyncMock(return_value=SimpleNamespace(id=user_id)),
    )
    monkeypatch.setattr(
        "app.services.organization_secret.agent_repo.get", AsyncMock(return_value=agent)
    )
    monkeypatch.setattr(
        "app.services.organization_secret.agent_repo.get_version",
        AsyncMock(return_value=version),
    )
    monkeypatch.setattr("app.services.organization_secret.organization_secret_repo.get", get_secret)
    service = OrganizationSecretService(db)
    monkeypatch.setattr(service, "_unseal_browser_row", unseal)

    with pytest.raises(AuthorizationError) as refused:
        await service.fill_browser_credential(
            organization_id=organization_id,
            user_id=user_id,
            agent_id=agent_id,
            run_id=uuid4(),
            secret_id=secret_id,
            expected_generation=uuid4(),
            origin="https://portal.example.test",
            fields={"password": 0},
        )

    assert refused.value.message == "Browser credential is not available for this run"
    get_secret.assert_not_awaited()
    unseal.assert_not_called()


@pytest.mark.anyio
@pytest.mark.security
async def test_a_fill_refuses_a_stale_authorization_generation_before_unsealing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id, user_id, agent_id, secret_id, version_id = (uuid4() for _ in range(5))
    expected_generation = uuid4()
    db = MagicMock()
    spec = AgentSpec(
        name="Synthetic browser agent",
        capabilities=[
            {
                "id": "browser_use",
                "config": {
                    "connection": "built_in",
                    "browser_credential_ids": [str(secret_id)],
                },
            }
        ],
    )
    agent = SimpleNamespace(current_version_id=version_id)
    version = SimpleNamespace(spec=spec.model_dump(mode="json"))
    row = SimpleNamespace(
        id=secret_id,
        organization_id=organization_id,
        kind="browser_login",
        authorization_generation=uuid4(),
        usage_scope="personal",
        visibility="private",
        owner_user_id=user_id,
    )
    get_secret = AsyncMock(return_value=row)
    unseal = MagicMock()
    monkeypatch.setattr(
        "app.services.organization_secret.member_repo.get_active",
        AsyncMock(return_value=SimpleNamespace(id=user_id)),
    )
    monkeypatch.setattr(
        "app.services.organization_secret.agent_repo.get", AsyncMock(return_value=agent)
    )
    monkeypatch.setattr(
        "app.services.organization_secret.agent_repo.get_version",
        AsyncMock(return_value=version),
    )
    monkeypatch.setattr("app.services.organization_secret.organization_secret_repo.get", get_secret)
    service = OrganizationSecretService(db)
    monkeypatch.setattr(service, "_unseal_browser_row", unseal)

    with pytest.raises(AuthorizationError) as refused:
        await service.fill_browser_credential(
            organization_id=organization_id,
            user_id=user_id,
            agent_id=agent_id,
            run_id=uuid4(),
            secret_id=secret_id,
            expected_generation=expected_generation,
            origin="https://portal.example.test",
            fields={"password": 0},
        )

    assert refused.value.message == "Browser credential is not available for this run"
    unseal.assert_not_called()


async def _validate_browser_binding(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ctx: AuthContext,
    agent_id: UUID,
    owner_user_id: UUID | None,
    usage_scope: str,
    visibility: Visibility,
    login: BrowserLoginSecret,
) -> list[str]:
    monkeypatch.setattr("app.core.config.settings.BROWSER_CDP_ALLOWED_HOSTS", ["browser"])
    secret_id = uuid4()
    sealed = seal_secret(
        login,
        scope=browser_credential_scope(
            ctx.organization_id, owner_user_id, personal=usage_scope == "personal"
        ),
    )
    row = SimpleNamespace(
        id=secret_id,
        organization_id=ctx.organization_id,
        kind="browser_login",
        owner_user_id=owner_user_id,
        usage_scope=usage_scope,
        visibility=visibility.value,
        sealed_secret=sealed.ciphertext,
        key_version=sealed.key_version,
    )
    monkeypatch.setattr(
        "app.services.agent_registry.organization_secret_repo.get_many",
        AsyncMock(return_value={secret_id: row}),
    )
    monkeypatch.setattr(
        "app.services.agent_registry.credential_repo.get_profile",
        AsyncMock(
            return_value=SimpleNamespace(
                label="Synthetic model",
                provider="openai",
                model="gpt-4.1",
                base_url=None,
                usage_scope="organization",
                personal_owner_user_id=None,
                secret_id=uuid4(),
                fallback_profile_ids=[],
                runs_without_key=False,
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.agent_registry.credential_repo.get_profiles_by_ids",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr("app.services.agent_registry.resolve_access", AsyncMock(return_value=True))
    monkeypatch.setattr("app.services.agent_registry.skill_repo.hold", AsyncMock())
    monkeypatch.setattr("app.services.agent_registry.agent_repo.get", AsyncMock(return_value=None))
    spec = AgentSpec(
        name="Synthetic browser agent",
        model_profile_id=uuid4(),
        capabilities=[
            {
                "id": "browser_use",
                "config": {
                    "cdp_url": "http://browser:9222",
                    "browser_credential_ids": [str(secret_id)],
                },
            }
        ],
    )

    try:
        await AgentRegistryService(MagicMock()).validate_spec(ctx, spec, agent_id=agent_id)
    except BadRequestError as refused:
        return refused.details["problems"]
    return []


def _publisher(*, user_id: UUID | None = None, organization_id: UUID | None = None) -> AuthContext:
    return AuthContext(
        user_id=user_id or uuid4(),
        organization_id=organization_id or uuid4(),
        role=OrgRoleName.OWNER,
    )


@pytest.mark.anyio
@pytest.mark.security
async def test_a_personal_login_uses_the_owner_bound_scope_and_keeps_audit_metadata_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _publisher()
    login = BrowserLoginSecret(
        username="synthetic-user@example.test",
        password="synthetic-password-value",
        allowed_origins=["https://portal.example.test"],
    )
    created = SimpleNamespace(id=uuid4())
    create = AsyncMock(return_value=created)
    audit = AsyncMock()
    monkeypatch.setattr(
        "app.services.organization_secret.organization_secret_repo.get_by_name",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("app.services.organization_secret.organization_secret_repo.create", create)
    monkeypatch.setattr("app.services.organization_secret.record_audit", audit)
    monkeypatch.setattr(
        "app.services.organization_secret.NotificationService.hold_security_audience",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.organization_secret.NotificationService.security_event", AsyncMock()
    )

    await OrganizationSecretService(MagicMock()).create(
        ctx,
        name="Synthetic portal",
        value=login,
        visibility=Visibility.PRIVATE,
        usage_scope="personal",
    )

    stored = create.call_args.kwargs
    scope = browser_credential_scope(ctx.organization_id, ctx.user_id, personal=True)
    assert stored["owner_user_id"] == ctx.user_id
    assert stored["visibility"] == Visibility.PRIVATE.value
    assert stored["usage_scope"] == "personal"
    assert (
        unseal_kind(
            stored["sealed_secret"],
            model=BrowserLoginSecret,
            scope=scope,
            key_version=stored["key_version"],
        )
        == login
    )
    with pytest.raises(BadRequestError):
        unseal_kind(
            stored["sealed_secret"],
            model=BrowserLoginSecret,
            scope=browser_credential_scope(uuid4(), ctx.user_id, personal=True),
            key_version=stored["key_version"],
        )
    audit_details = audit.await_args.kwargs["details"]
    assert "synthetic-user@example.test" not in repr(audit_details)
    assert "synthetic-password-value" not in repr(audit_details)


@pytest.mark.anyio
@pytest.mark.security
async def test_a_named_agent_in_the_same_organization_can_be_saved_on_a_shared_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _publisher()
    agent_id = uuid4()
    login = BrowserLoginSecret(
        username="synthetic-user@example.test",
        password="synthetic-password-value",
        allowed_origins=["https://portal.example.test"],
        account_policy="named_agents",
        authorized_agent_ids=[agent_id],
    )
    created = SimpleNamespace(id=uuid4())
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(
        "app.services.organization_secret.organization_secret_repo.get_by_name",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("app.services.organization_secret.organization_secret_repo.create", create)
    monkeypatch.setattr(
        "app.services.organization_secret.agent_repo.get_many",
        AsyncMock(return_value={agent_id: SimpleNamespace(id=agent_id)}),
    )
    monkeypatch.setattr(
        "app.services.organization_secret.NotificationService.hold_security_audience",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.organization_secret.NotificationService.security_event", AsyncMock()
    )
    monkeypatch.setattr(
        "app.services.organization_secret.record_audit",
        AsyncMock(return_value=SimpleNamespace(id=uuid4())),
    )

    await OrganizationSecretService(MagicMock()).create(
        ctx,
        name="Synthetic shared portal",
        value=login,
        visibility=Visibility.ORG,
        usage_scope="organization",
    )

    stored = create.call_args.kwargs
    assert stored["owner_user_id"] is None
    assert stored["visibility"] == Visibility.ORG.value
    assert stored["usage_scope"] == "organization"
    assert (
        unseal_kind(
            stored["sealed_secret"],
            model=BrowserLoginSecret,
            scope=browser_credential_scope(ctx.organization_id, None, personal=False),
            key_version=stored["key_version"],
        )
        == login
    )


@pytest.mark.anyio
@pytest.mark.security
async def test_shared_browser_rotation_rechecks_agents_and_changes_authorization_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _publisher()
    agent_id, secret_id, old_generation = (uuid4() for _ in range(3))
    login = BrowserLoginSecret(
        username="synthetic-user@example.test",
        password="synthetic-password-value",
        allowed_origins=["https://portal.example.test"],
        account_policy="named_agents",
        authorized_agent_ids=[agent_id],
    )
    row = SimpleNamespace(
        id=secret_id,
        organization_id=ctx.organization_id,
        name="Synthetic shared portal",
        kind="browser_login",
        usage_scope="organization",
        visibility=Visibility.ORG.value,
        owner_user_id=None,
        purpose="custom",
        authorization_generation=old_generation,
        hint="",
    )
    updated = AsyncMock(return_value=row)
    get_many = AsyncMock(return_value={agent_id: SimpleNamespace(id=agent_id)})
    audit = AsyncMock(return_value=SimpleNamespace(id=uuid4()))

    class Notifications:
        def __init__(self, _db: object) -> None:
            pass

        async def hold_security_audience(self, _organization_id: UUID) -> list[UUID]:
            return []

        async def security_event(self, _entry: object, *, recipients: list[UUID]) -> None:
            assert recipients == []

    service = OrganizationSecretService(MagicMock())
    monkeypatch.setattr(service, "_get", AsyncMock(return_value=row))
    monkeypatch.setattr("app.services.organization_secret.organization_secret_repo.update", updated)
    monkeypatch.setattr("app.services.organization_secret.agent_repo.get_many", get_many)
    monkeypatch.setattr("app.services.organization_secret.NotificationService", Notifications)
    monkeypatch.setattr("app.services.organization_secret.record_audit", audit)

    await service.update(ctx, secret_id, value=login)

    update_data = updated.await_args.kwargs["update_data"]
    get_many.assert_awaited_once_with(service.db, [agent_id], organization_id=ctx.organization_id)
    assert update_data["authorization_generation"] != old_generation
    assert (
        unseal_kind(
            update_data["sealed_secret"],
            model=BrowserLoginSecret,
            scope=browser_credential_scope(ctx.organization_id, None, personal=False),
            key_version=update_data["key_version"],
        )
        == login
    )
    details = audit.await_args.kwargs["details"]
    assert "synthetic-user@example.test" not in repr(details)
    assert "synthetic-password-value" not in repr(details)


@pytest.mark.anyio
@pytest.mark.security
async def test_a_shared_login_cannot_store_an_agent_from_another_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _publisher()
    foreign_agent_id = uuid4()
    create = AsyncMock()
    get_many = AsyncMock(return_value={})
    monkeypatch.setattr(
        "app.services.organization_secret.organization_secret_repo.get_by_name",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("app.services.organization_secret.organization_secret_repo.create", create)
    monkeypatch.setattr("app.services.organization_secret.agent_repo.get_many", get_many)

    db = MagicMock()
    with pytest.raises(BadRequestError) as refused:
        await OrganizationSecretService(db).create(
            ctx,
            name="Synthetic shared portal",
            value=BrowserLoginSecret(
                username="synthetic-user@example.test",
                password="synthetic-password-value",
                allowed_origins=["https://portal.example.test"],
                account_policy="named_agents",
                authorized_agent_ids=[foreign_agent_id],
            ),
            visibility=Visibility.ORG,
            usage_scope="organization",
        )

    assert refused.value.details["fields"][0]["field"] == "value.authorized_agent_ids"
    get_many.assert_awaited_once_with(db, [foreign_agent_id], organization_id=ctx.organization_id)
    create.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.security
async def test_browser_credentials_cannot_use_generic_visibility_grants_or_owner_transfer() -> None:
    guard = SecretSharingGuard()
    ctx = _publisher()
    resource = MagicMock(spec=OrganizationSecret)
    resource.kind = "browser_login"
    resource.visibility = Visibility.PRIVATE.value
    resource.usage_scope = "personal"

    with pytest.raises(BadRequestError, match="typed replacement"):
        await guard.before_visibility(MagicMock(), ctx, resource, Visibility.ORG)
    with pytest.raises(BadRequestError, match="named-agent policy"):
        await guard.before_share(MagicMock(), ctx, resource, ShareChange(visibility=Visibility.ORG))
    with pytest.raises(BadRequestError, match="cannot be transferred"):
        await guard.adopt(MagicMock(), ctx, resource)


@pytest.mark.anyio
async def test_an_owner_can_bind_their_personal_browser_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _publisher()
    agent_id = uuid4()
    problems = await _validate_browser_binding(
        monkeypatch,
        ctx=ctx,
        agent_id=agent_id,
        owner_user_id=ctx.user_id,
        usage_scope="personal",
        visibility=Visibility.PRIVATE,
        login=BrowserLoginSecret(
            username="synthetic-user",
            password="synthetic-password",
            allowed_origins=["https://portal.example.test"],
        ),
    )
    assert not problems, problems


@pytest.mark.anyio
@pytest.mark.security
async def test_a_publisher_cannot_bind_another_members_personal_browser_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _publisher()
    problems = await _validate_browser_binding(
        monkeypatch,
        ctx=ctx,
        agent_id=uuid4(),
        owner_user_id=uuid4(),
        usage_scope="personal",
        visibility=Visibility.PRIVATE,
        login=BrowserLoginSecret(
            username="synthetic-user",
            password="synthetic-password",
            allowed_origins=["https://portal.example.test"],
        ),
    )
    assert any(
        "A personal browser login can only be bound by its owner" in problem for problem in problems
    )


@pytest.mark.anyio
@pytest.mark.security
async def test_a_shared_browser_login_requires_authorization_for_the_named_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _publisher()
    agent_id = uuid4()
    problems = await _validate_browser_binding(
        monkeypatch,
        ctx=ctx,
        agent_id=agent_id,
        owner_user_id=None,
        usage_scope="organization",
        visibility=Visibility.ORG,
        login=BrowserLoginSecret(
            username="synthetic-user",
            password="synthetic-password",
            allowed_origins=["https://portal.example.test"],
            account_policy="named_agents",
            authorized_agent_ids=[uuid4()],
        ),
    )
    assert any(
        "The shared account does not authorize this named agent" in problem for problem in problems
    )


@pytest.mark.anyio
async def test_an_authorized_agent_can_bind_a_shared_browser_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _publisher()
    agent_id = uuid4()
    problems = await _validate_browser_binding(
        monkeypatch,
        ctx=ctx,
        agent_id=agent_id,
        owner_user_id=None,
        usage_scope="organization",
        visibility=Visibility.ORG,
        login=BrowserLoginSecret(
            username="synthetic-user",
            password="synthetic-password",
            allowed_origins=["https://portal.example.test"],
            account_policy="named_agents",
            authorized_agent_ids=[agent_id],
        ),
    )
    assert not problems, problems
