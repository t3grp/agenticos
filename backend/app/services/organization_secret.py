"""Organization secrets - storage, rotation, and the one place they are opened.

Everything above this deals in secret ids; everything below deals in ciphertext.
There is deliberately no method that returns a plaintext to a caller who could
keep one. Narrow runtime readers unseal, and each hands what it opened straight to the thing
that had to have it: :meth:`OrganizationSecretService.resolve_for_bindings`, called
by the runner; :meth:`OrganizationSecretService.listing_key`, which returns a
bearer token for one outbound catalog request;
:meth:`OrganizationSecretService.transcription_key`, which returns the key a
transcription request names; :meth:`OrganizationSecretService.voice_call_key`,
which opens the key a voice call was held with only to hang that call up (#54);
and :meth:`OrganizationSecretService.oauth_app`, which hands
an OAuth client's secret to the provider's own token endpoint inside the connect
flow and to nowhere else. `smtp_email_service` opens one permitted SMTP account
straight into its transport, never returning credentials to an API caller.
fill_browser_credential opens one eligible browser login or profile as masked
values for the provisioned browser writer; it never returns them to the model.
"""

import asyncio
import base64
import hashlib
import hmac
import struct
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import secret_purposes
from app.core.audit import record_audit
from app.core.exceptions import (
    AlreadyExistsError,
    AuthorizationError,
    BadRequestError,
    NotFoundError,
)
from app.core.field_errors import refused_field
from app.core.permissions import AuthContext, Perm
from app.core.secret_kinds import (
    BROWSER_SECRET_KINDS,
    ApiKeySecret,
    BrowserLoginSecret,
    BrowserProfileSecret,
    BrowserTotpSecret,
    GithubOAuthAppSecret,
    GoogleOAuthAppSecret,
    MicrosoftOAuthAppSecret,
    SecretKind,
    SecretValue,
    SmtpAccountSecret,
    StorableSecret,
    canonical_browser_origin,
    describe_kind,
    seal_secret,
    unseal_kind,
    unseal_secret,
)
from app.core.secret_purposes import CUSTOM
from app.core.vault import VaultScope, browser_credential_scope
from app.db.models.organization_secret import OrganizationSecret
from app.db.models.resource_grant import Visibility
from app.repositories import agent as agent_repo
from app.repositories import member_repo, organization_secret_repo, resource_grant_repo
from app.schemas.resource_grant import as_visibility
from app.schemas.secret import SecretSummary, SecretUsage
from app.services.access import (
    SECRET,
    OwnedResource,
    accessible_ids,
    resolve_access,
    visible_resource_ids,
)
from app.services.notifications import NotificationService
from app.services.sharing import Deferred, ShareChange, register_sharing_guard

if TYPE_CHECKING:
    from app.services.email.service import EmailService

# The credentials a connect flow spends: separate kinds even where the fields are
# identical, because a kind names what a credential is for.
OAuthAppSecret = GithubOAuthAppSecret | GoogleOAuthAppSecret | MicrosoftOAuthAppSecret


@dataclass(frozen=True)
class BrowserCredentialOption:
    """Safe metadata for an agent's currently eligible browser credential."""

    id: UUID
    name: str
    kind: Literal["browser_login", "browser_profile"]
    origin: str
    authorization_generation: UUID
    has_totp: bool = False
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrowserCredentialValues:
    """Secret values for the browser toolset, always wrapped in masked strings."""

    fields: Mapping[str, SecretStr]


class OrganizationSecretService:
    """Manage the secrets an organization's capabilities may reference."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _check_purpose(purpose: str, kind: SecretKind) -> None:
        """Refuse a purpose this deployment does not offer, or the wrong shape.

        The shape check is the one that matters: a purpose says which service
        the key is for, and every service knows what a credential for it looks
        like. Storing an AWS pair as an OpenAI key produces a vault entry that
        reads correctly and fails at the first run. `custom` is the one that
        takes any shape: it names no service, so it has no shape to insist on -
        and it is how a credential for something the catalog does not know, an
        HTTP API's for one, is stored at all.
        """
        entry = secret_purposes.get(purpose)
        if entry is None:
            raise BadRequestError(
                message=f"Unknown purpose: {purpose}", details={"purpose": purpose}
            )
        if entry.id != CUSTOM and entry.kind is not kind:
            raise BadRequestError(
                message=(f"{entry.label} needs a {entry.kind.value} credential, not {kind.value}"),
                details={"purpose": purpose, "expected_kind": entry.kind.value},
            )

    async def list_secrets(
        self, ctx: AuthContext, *, purposes: list[str] | None = None
    ) -> list[SecretSummary]:
        """The secrets this caller may see, with who put each one there and what
        it is holding up.

        Visibility is resolved here rather than left to the route, because "a
        member sees their own keys and the organization's" is a property of the
        vault and not of one endpoint. `purposes` narrows it further - the
        Tavily keys for a web-search slot, the OpenRouter ones for a model
        picker.

        Both are answered here rather than left to the caller because both are
        the difference between a list of names and a list somebody can act on:
        a key nothing binds is one nobody can account for, and a key whose owner
        has left is one nobody will rotate.

        Three extra queries for the page, not three per row: the emails, the
        grant counts and the agent usage each resolve in one grouped lookup,
        however many secrets the organization has.
        """
        # `None` is the role already reaching every key; anything else is the
        # extra ids a grant opened up. One call answers both, so the scope is
        # never read twice and the two answers cannot disagree.
        shared = await visible_resource_ids(
            self.db, ctx, resource_type=SECRET, perm=Perm.SECRETS_VIEW
        )
        secrets = await organization_secret_repo.list_secrets(
            self.db,
            organization_id=ctx.organization_id,
            user_id=ctx.subject_id,
            see_all=shared is None,
            shared_ids=[] if shared is None else shared,
            purposes=purposes,
            personal_user_id=ctx.personal_user_id,
        )
        people = await member_repo.get_identities_for_users(
            self.db,
            organization_id=ctx.organization_id,
            # Both columns, one lookup: a personal key's owner and whoever
            # stored it are usually the same person and occasionally are not.
            user_ids=[
                user_id
                for secret in secrets
                for user_id in (secret.created_by_user_id, secret.owner_user_id)
                if user_id
            ],
        )
        # How far each key reaches, in one grouped query rather than one per
        # row. A listing that only says "private" cannot distinguish a key
        # nobody else has from one five people were handed.
        shared_counts = await resource_grant_repo.count_for_resources(
            self.db,
            organization_id=ctx.organization_id,
            resource_type=SECRET.key,
            resource_ids=[secret.id for secret in secrets],
        )
        # Which agents bind each key, in one grouped query rather than one per
        # row: a thirty-secret vault loaded its page in thirty-one queries (#953).
        usage = await organization_secret_repo.agents_using_for_secrets(
            self.db,
            organization_id=ctx.organization_id,
            secret_ids=[secret.id for secret in secrets],
        )
        editable = await accessible_ids(
            self.db, ctx, secrets, Perm.SECRETS_EDIT, resource_type=SECRET
        )
        rows: list[SecretSummary] = []
        for secret in secrets:
            owner = people.get(secret.owner_user_id) if secret.owner_user_id else None
            author = people.get(secret.created_by_user_id) if secret.created_by_user_id else None
            owner = owner or member_repo.MemberIdentity(email=None, avatar_url=None)
            author = author or member_repo.MemberIdentity(email=None, avatar_url=None)
            used = usage.get(secret.id, [])
            rows.append(
                SecretSummary(
                    id=secret.id,
                    can_edit=secret.id in editable,
                    name=secret.name,
                    description=secret.description,
                    kind=SecretKind(secret.kind),
                    usage_scope="personal" if secret.usage_scope == "personal" else "organization",
                    hint=secret.hint,
                    purpose=secret.purpose,
                    visibility=as_visibility(secret.visibility),
                    owner_user_id=secret.owner_user_id,
                    owner_email=owner.email,
                    created_by_user_id=secret.created_by_user_id,
                    created_by_email=author.email,
                    created_by_avatar_url=author.avatar_url,
                    shared_with=shared_counts.get(secret.id, 0),
                    used_by=[
                        SecretUsage(kind="agent", id=agent_id, name=name) for agent_id, name in used
                    ],
                    created_at=secret.created_at,
                    updated_at=secret.updated_at,
                )
            )
        return rows

    async def create(
        self,
        ctx: AuthContext,
        *,
        name: str,
        value: StorableSecret,
        description: str | None = None,
        purpose: str = CUSTOM,
        visibility: Visibility = Visibility.ORG,
        usage_scope: Literal["organization", "personal"] = "organization",
    ) -> OrganizationSecret:
        """Seal and store a secret.

        `purpose` is what the key is for - a provider, a service, or `custom`.
        It is what lets the model picker offer the providers this organization
        can actually reach, and what lets a capability ask for the right key
        rather than for "an API key".

        `visibility` decides who else sees it. A private key belongs to the
        person who added it; an organization one is the team's. Both are
        legitimate: a shared OpenAI account and somebody's own trial key are
        different things, and forcing either into the other's shape is what the
        single-visibility vault did.

        Raises:
            AlreadyExistsError: If the name is taken. The name plus four
                characters is the whole of what anyone can see of a stored
                secret, so two sharing one is a pair nobody can tell apart -
                including the person about to point an agent at the wrong one.
            BadRequestError: If the purpose is not one this deployment offers,
                or wants a different shape of credential than was given.
        """
        if isinstance(value, (BrowserLoginSecret, BrowserProfileSecret)):
            expected_scope = "personal" if value.account_policy == "personal" else "organization"
            expected_visibility = (
                Visibility.PRIVATE if expected_scope == "personal" else Visibility.ORG
            )
            if usage_scope != expected_scope or visibility is not expected_visibility:
                raise refused_field(
                    "value.account_policy",
                    "Personal browser credentials must be private and owner-only; shared accounts must be organization-visible.",
                )
        if usage_scope == "personal" and not isinstance(
            value, (BrowserLoginSecret, BrowserProfileSecret)
        ):
            self._check_personal_connection(ctx, value, purpose=purpose, visibility=visibility)
        await self._validate_browser_agents(ctx.organization_id, value)
        self._check_purpose(purpose, value.kind)
        await self._refuse_duplicate_name(ctx, name)
        owner_user_id = ctx.user_id if visibility is Visibility.PRIVATE else None
        scope = (
            browser_credential_scope(
                ctx.organization_id,
                owner_user_id,
                personal=usage_scope == "personal",
            )
            if isinstance(value, (BrowserLoginSecret, BrowserProfileSecret))
            else VaultScope.organization(ctx.organization_id)
        )
        sealed = seal_secret(value, scope=scope)
        secret = await organization_secret_repo.create(
            self.db,
            organization_id=ctx.organization_id,
            name=name,
            description=description,
            kind=value.kind.value,
            purpose=purpose,
            visibility=visibility.value,
            # A private key needs an owner or nobody can reach it - the
            # database refuses that combination, and this is where it is made
            # impossible rather than caught.
            owner_user_id=owner_user_id,
            sealed_secret=sealed.ciphertext,
            hint=sealed.hint,
            key_version=sealed.key_version,
            created_by_user_id=ctx.user_id,
            usage_scope=usage_scope,
        )
        notifications = NotificationService(self.db)
        # Before the audit entry, never after it: `record_audit` holds the
        # chain lock to the end of the transaction, and the notification
        # below reaches for a `users` row that `admin_delete` takes first
        # and the chain second (#1763).
        audience = await notifications.hold_security_audience(ctx.organization_id)
        entry = await record_audit(
            self.db,
            actor_user_id=ctx.subject_id,
            organization_id=ctx.organization_id,
            action="secret.created",
            target_type="secret",
            target_id=str(secret.id),
            # The value never reaches the audit log; the hint is what identifies it.
            details={"name": name, "kind": value.kind.value, "hint": sealed.hint},
        )
        await notifications.security_event(entry, recipients=audience)
        return secret

    @staticmethod
    def _check_personal_connection(
        ctx: AuthContext,
        value: StorableSecret,
        *,
        purpose: str,
        visibility: Visibility,
    ) -> None:
        """Only documented API-key connections can power personal agent models."""
        if ctx.personal_user_id is None:
            raise AuthorizationError(message="Sign in as yourself to connect a personal model")
        if visibility is not Visibility.PRIVATE:
            raise refused_field("visibility", "A personal model connection must be private")
        if purpose not in {"anthropic", "openai"} or not isinstance(value, ApiKeySecret):
            raise refused_field(
                "usage_scope",
                "Personal connections currently support Anthropic and OpenAI API keys. "
                "ChatGPT subscription sign-in is not enabled for this app.",
            )
        if purpose == "anthropic" and value.api_key.get_secret_value().startswith("sk-ant-oat"):
            raise refused_field(
                "value.api_key",
                "Use a Claude Console API key for agents, not a Claude Code setup token",
            )

    async def update(
        self,
        ctx: AuthContext,
        secret_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        value: StorableSecret | None = None,
    ) -> OrganizationSecret:
        """Rename, re-describe or rotate a secret.

        A rotation may not change the kind: agents were published against a
        capability that requires one shape, and swapping an API key for an AWS
        key pair under a stable id would break them at run time with nothing
        pointing back at this edit. Changing shape means a new secret.

        Raises:
            NotFoundError: If no such secret exists in this organization.
            BadRequestError: If a rotation would change the secret's kind.
            AlreadyExistsError: If the new name is taken.
        """
        secret = await self._get(ctx, secret_id, perm=Perm.SECRETS_EDIT)
        update_data: dict[str, object] = {}

        if name is not None and name != secret.name:
            await self._refuse_duplicate_name(ctx, name)
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if value is not None:
            self._refuse_kind_change(secret, value)
            if secret.usage_scope == "personal" and not isinstance(
                value, (BrowserLoginSecret, BrowserProfileSecret)
            ):
                self._check_personal_connection(
                    ctx, value, purpose=secret.purpose, visibility=Visibility.PRIVATE
                )
            if isinstance(value, (BrowserLoginSecret, BrowserProfileSecret)):
                if secret.usage_scope == "personal" and (
                    value.account_policy != "personal" or secret.owner_user_id != ctx.user_id
                ):
                    raise refused_field(
                        "value.account_policy",
                        "A browser credential cannot change its owner or sharing policy.",
                    )
                if secret.usage_scope != "personal" and value.account_policy != "named_agents":
                    raise refused_field(
                        "value.account_policy",
                        "A shared browser account must keep its named-agent policy.",
                    )
                await self._validate_browser_agents(ctx.organization_id, value)
                scope = browser_credential_scope(
                    ctx.organization_id,
                    secret.owner_user_id,
                    personal=secret.usage_scope == "personal",
                )
            else:
                scope = VaultScope.organization(ctx.organization_id)
            sealed = seal_secret(value, scope=scope)
            update_data["authorization_generation"] = uuid4()
            update_data["sealed_secret"] = sealed.ciphertext
            update_data["hint"] = sealed.hint
            update_data["key_version"] = sealed.key_version

        if not update_data:
            return secret

        secret = await organization_secret_repo.update(
            self.db, secret=secret, update_data=update_data
        )
        notifications = NotificationService(self.db)
        # Before the audit entry, never after it: `record_audit` holds the
        # chain lock to the end of the transaction, and the notification
        # below reaches for a `users` row that `admin_delete` takes first
        # and the chain second (#1763).
        audience = await notifications.hold_security_audience(ctx.organization_id)
        entry = await record_audit(
            self.db,
            actor_user_id=ctx.subject_id,
            organization_id=ctx.organization_id,
            action="secret.rotated" if value is not None else "secret.updated",
            target_type="secret",
            target_id=str(secret.id),
            details={"name": secret.name, "kind": secret.kind, "hint": secret.hint},
        )
        await notifications.security_event(entry, recipients=audience)
        return secret

    async def delete(self, ctx: AuthContext, secret_id: UUID) -> None:
        """Delete a secret.

        Agents referencing it are not repointed and not blocked from being
        deleted out from under - they fail loudly at their next run, the same
        way a model profile whose key was deleted does. Silently substituting
        another secret is the alternative, and it is worse.
        """
        secret = await self._get(ctx, secret_id, perm=Perm.SECRETS_EDIT)
        await organization_secret_repo.delete(
            self.db, secret_id, organization_id=ctx.organization_id
        )
        notifications = NotificationService(self.db)
        # Before the audit entry, never after it: `record_audit` holds the
        # chain lock to the end of the transaction, and the notification
        # below reaches for a `users` row that `admin_delete` takes first
        # and the chain second (#1763).
        audience = await notifications.hold_security_audience(ctx.organization_id)
        entry = await record_audit(
            self.db,
            actor_user_id=ctx.subject_id,
            organization_id=ctx.organization_id,
            action="secret.deleted",
            target_type="secret",
            target_id=str(secret_id),
            details={"name": secret.name, "kind": secret.kind},
        )
        await notifications.security_event(entry, recipients=audience)

    async def resolve_for_bindings(
        self, ctx: AuthContext, secret_ids: list[UUID]
    ) -> dict[UUID, StorableSecret]:
        """Unseal the secrets a run's bindings reference.

        One of the two readers of a plaintext in this service, and the only one
        that hands over a whole credential. Ids that no longer resolve are simply
        absent from the result; the capability registry turns a missing *required*
        secret into a refusal, and it is the one place that knows which bindings
        actually needed one.
        """
        rows = await organization_secret_repo.get_many(
            self.db, secret_ids, organization_id=ctx.organization_id
        )
        scope = VaultScope.organization(ctx.organization_id)
        return {
            secret_id: unseal_secret(
                row.sealed_secret,
                kind=SecretKind(row.kind),
                scope=scope,
                key_version=row.key_version,
            )
            for secret_id, row in rows.items()
            if row.usage_scope != "personal"
            and row.kind not in {kind.value for kind in BROWSER_SECRET_KINDS}
        }

    async def browser_credentials(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        agent_id: UUID,
        secret_ids: tuple[UUID, ...],
        origin: str,
    ) -> list[BrowserCredentialOption]:
        """Return safe metadata for credentials this member and current agent may use."""
        try:
            canonical_origin = canonical_browser_origin(origin)
        except ValueError:
            return []
        if not await member_repo.get_active(
            self.db, organization_id=organization_id, user_id=user_id
        ):
            return []
        bound_secret_ids = await self._agent_browser_secret_ids(
            organization_id, agent_id, set(secret_ids)
        )
        if not bound_secret_ids:
            return []
        rows = await organization_secret_repo.get_many(
            self.db, list(bound_secret_ids), organization_id=organization_id
        )
        options: list[BrowserCredentialOption] = []
        for secret_id in secret_ids:
            if secret_id not in bound_secret_ids:
                continue
            row = rows.get(secret_id)
            if (
                row is None
                or row.organization_id != organization_id
                or row.kind not in {kind.value for kind in BROWSER_SECRET_KINDS}
            ):
                continue
            if not self._browser_row_scope_allowed(
                row, organization_id=organization_id, user_id=user_id
            ):
                continue
            value = self._unseal_browser_row(row, organization_id=organization_id)
            if not self._browser_use_allowed(row, value, user_id=user_id, agent_id=agent_id):
                continue
            if canonical_origin not in value.allowed_origins:
                continue
            options.append(
                BrowserCredentialOption(
                    id=row.id,
                    name=row.name,
                    kind=cast(Literal["browser_login", "browser_profile"], row.kind),
                    origin=canonical_origin,
                    authorization_generation=row.authorization_generation,
                    has_totp=isinstance(value, BrowserLoginSecret) and value.totp is not None,
                    fields=(
                        ()
                        if isinstance(value, BrowserLoginSecret)
                        else tuple(
                            field
                            for field in _BROWSER_PROFILE_FIELDS
                            if getattr(value, field) is not None
                        )
                    ),
                )
            )
        return options

    async def fill_browser_credential(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        agent_id: UUID,
        run_id: UUID,
        secret_id: UUID,
        expected_generation: UUID,
        origin: str,
        fields: Mapping[str, int],
    ) -> BrowserCredentialValues:
        """Open values for one admitted fill; its caller must pass them straight to CDP."""
        try:
            canonical_origin = canonical_browser_origin(origin)
        except ValueError:
            raise AuthorizationError(
                message="Browser credential is not available for this run"
            ) from None
        if not await member_repo.get_active(
            self.db, organization_id=organization_id, user_id=user_id
        ) or secret_id not in await self._agent_browser_secret_ids(
            organization_id, agent_id, {secret_id}
        ):
            raise AuthorizationError(message="Browser credential is not available for this run")
        row = await organization_secret_repo.get(
            self.db, secret_id, organization_id=organization_id
        )
        if (
            row is None
            or row.organization_id != organization_id
            or row.kind not in {kind.value for kind in BROWSER_SECRET_KINDS}
            or row.authorization_generation != expected_generation
            or not self._browser_row_scope_allowed(
                row, organization_id=organization_id, user_id=user_id
            )
        ):
            raise AuthorizationError(message="Browser credential is not available for this run")
        value = self._unseal_browser_row(row, organization_id=organization_id)
        if not self._browser_use_allowed(row, value, user_id=user_id, agent_id=agent_id):
            raise AuthorizationError(message="Browser credential is not available for this run")
        if canonical_origin not in value.allowed_origins:
            raise AuthorizationError(message="Browser credential is not available for this run")
        if "totp" in fields:
            if not isinstance(value, BrowserLoginSecret) or value.totp is None:
                raise BadRequestError(message="This browser login has no stored TOTP seed")
            await _wait_for_totp_window(value.totp)
        values = _browser_fill_values(value, fields)
        await record_audit(
            self.db,
            actor_user_id=user_id,
            organization_id=organization_id,
            action="secret.browser_fill_admitted",
            target_type="secret",
            target_id=str(secret_id),
            details={
                "agent_id": str(agent_id),
                "run_id": str(run_id),
                "origin": canonical_origin,
                "fields": sorted(fields),
                "authorization_generation": str(expected_generation),
                "result": "admitted",
            },
        )
        return BrowserCredentialValues(fields=values)

    def _unseal_browser_row(
        self, row: OrganizationSecret, *, organization_id: UUID
    ) -> BrowserLoginSecret | BrowserProfileSecret:
        """Open a browser kind only through its dedicated owner-bound scope."""
        scope = browser_credential_scope(
            organization_id,
            row.owner_user_id,
            personal=row.usage_scope == "personal",
        )
        model = (
            BrowserLoginSecret
            if row.kind == SecretKind.BROWSER_LOGIN.value
            else BrowserProfileSecret
        )
        return unseal_kind(
            row.sealed_secret,
            model=model,
            scope=scope,
            key_version=row.key_version,
        )

    @staticmethod
    def _browser_row_scope_allowed(
        row: OrganizationSecret, *, organization_id: UUID, user_id: UUID
    ) -> bool:
        if row.organization_id != organization_id:
            return False
        if row.usage_scope == "personal":
            return row.visibility == Visibility.PRIVATE.value and row.owner_user_id == user_id
        return (
            row.usage_scope == "organization"
            and row.visibility == Visibility.ORG.value
            and row.owner_user_id is None
        )

    @staticmethod
    def _browser_use_allowed(
        row: OrganizationSecret,
        value: BrowserLoginSecret | BrowserProfileSecret,
        *,
        user_id: UUID,
        agent_id: UUID,
    ) -> bool:
        if row.usage_scope == "personal":
            return (
                row.visibility == Visibility.PRIVATE.value
                and row.owner_user_id == user_id
                and value.account_policy == "personal"
                and not value.authorized_agent_ids
            )
        return (
            row.usage_scope == "organization"
            and row.visibility == Visibility.ORG.value
            and row.owner_user_id is None
            and value.account_policy == "named_agents"
            and agent_id in value.authorized_agent_ids
        )

    async def _agent_browser_secret_ids(
        self, organization_id: UUID, agent_id: UUID, secret_ids: set[UUID]
    ) -> set[UUID]:
        """Return only requested ids in the latest published browser binding."""
        if not secret_ids:
            return set()
        agent = await agent_repo.get(self.db, agent_id, organization_id=organization_id)
        if agent is None or agent.current_version_id is None:
            return set()
        version = await agent_repo.get_version(
            self.db, agent.current_version_id, organization_id=organization_id
        )
        if version is None:
            return set()
        from app.agents.spec import AgentSpec

        try:
            spec = AgentSpec.model_validate(version.spec)
        except ValueError:
            return set()
        bound: set[UUID] = set()
        for binding in spec.capabilities:
            if binding.id != "browser_use" or binding.config.get("connection") != "built_in":
                continue
            bound.update(
                secret_ids.intersection(
                    UUID(str(secret_id))
                    for secret_id in binding.config.get("browser_credential_ids", [])
                )
            )
        return bound

    async def _validate_browser_agents(
        self,
        organization_id: UUID,
        value: StorableSecret,
    ) -> None:
        """Keep every named shared-account agent inside the secret's organization."""
        if not isinstance(value, (BrowserLoginSecret, BrowserProfileSecret)):
            return
        if value.account_policy != "named_agents":
            return
        found = await agent_repo.get_many(
            self.db,
            list(value.authorized_agent_ids),
            organization_id=organization_id,
        )
        if set(found) != set(value.authorized_agent_ids):
            raise refused_field(
                "value.authorized_agent_ids",
                "Each authorized browser agent must belong to this organization.",
            )

    async def require_view(self, ctx: AuthContext, secret_id: UUID) -> OrganizationSecret:
        """The secret, if this caller may view it — otherwise refused.

        The gate for *naming* a secret id in a write: attaching it to a
        sandbox connection, or probing a service with it. A missing row and
        an unviewable row are answered alike, so the refusal says nothing
        about which it was.

        This is deliberately not enforced inside `resolve_for_bindings`:
        at run time the runner resolves the bindings of a published spec for
        whoever may run the agent, and the spec's secrets were checked
        against the publisher at publish. Checking here, at attach time, is
        the gate — see `docs/permissions.md`.
        """
        secret = await organization_secret_repo.get(
            self.db, secret_id, organization_id=ctx.organization_id
        )
        if secret is None or not await resolve_access(
            self.db, ctx, secret, Perm.SECRETS_VIEW, resource_type=SECRET
        ):
            raise AuthorizationError(
                message="You do not have access to that secret",
                details={"secret_id": str(secret_id)},
            )
        return secret

    async def listing_key(
        self, ctx: AuthContext, provider: str, *, secret_id: UUID | None = None
    ) -> str | None:
        """Open a visible selected key, or an organization key for a fixed provider URL."""
        if secret_id is not None:
            secret = await organization_secret_repo.get(
                self.db, secret_id, organization_id=ctx.organization_id
            )
            if secret is None or not await resolve_access(
                self.db, ctx, secret, Perm.SECRETS_VIEW, resource_type=SECRET
            ):
                raise BadRequestError(message="That key is not in this organization's vault")
            if secret.purpose != provider or secret.kind != SecretKind.API_KEY.value:
                raise BadRequestError(message="Choose an API key for this provider")
            value = unseal_kind(
                secret.sealed_secret,
                model=ApiKeySecret,
                scope=VaultScope.organization(ctx.organization_id),
                key_version=secret.key_version,
            )
            return value.api_key.get_secret_value()
        secrets = await organization_secret_repo.list_secrets(
            self.db, organization_id=ctx.organization_id, purposes=[provider]
        )
        for secret in secrets:
            if secret.usage_scope == "personal":
                continue
            value = unseal_secret(
                secret.sealed_secret,
                kind=SecretKind(secret.kind),
                scope=VaultScope.organization(ctx.organization_id),
                key_version=secret.key_version,
            )
            if isinstance(value, ApiKeySecret):
                return value.api_key.get_secret_value()
        return None

    async def usable_transcription_key(
        self, organization_id: UUID, secret_id: UUID, *, provider: str, shared: bool
    ) -> OrganizationSecret | None:
        """The named key, if a recording on `provider` may be sent with it - never opened.

        Usable means it is still in this organization's vault, it is an API key,
        and its purpose is the provider - a key is never sent to a service it
        was not issued by. A `shared` key, one the platform spends for everybody
        (an organization's default, a bot's), must also still be
        organization-visible: a key made private after it was chosen stops
        paying for other people's recordings rather than going on doing so.

        Existence only, so a readiness check can ask without a plaintext
        leaving the vault.
        """
        row = await organization_secret_repo.get(
            self.db, secret_id, organization_id=organization_id
        )
        if (
            row is None
            or row.usage_scope == "personal"
            or row.kind != SecretKind.API_KEY.value
            or row.purpose != provider
        ):
            return None
        if shared and row.visibility != Visibility.ORG.value:
            return None
        return row

    async def transcription_key(
        self, organization_id: UUID, secret_id: UUID, *, provider: str, shared: bool
    ) -> str | None:
        """The bearer token a transcription request is sent with, for the key it names.

        The key is the one the speech-to-text settings named next to the pair
        (`speech_to_text_settings`); nothing here chooses one. The plaintext
        goes to the provider with the recording and nowhere else.

        Takes an organization id rather than an `AuthContext`, because a channel
        bot transcribes before there is a sender to build one for.

        Returns:
            The key, or `None` when it is not usable
            (:meth:`usable_transcription_key`) - gone, of another kind or
            purpose, or no longer organization-visible where it has to be.
        """
        row = await self.usable_transcription_key(
            organization_id, secret_id, provider=provider, shared=shared
        )
        if row is None:
            return None
        value = unseal_secret(
            row.sealed_secret,
            kind=SecretKind.API_KEY,
            scope=VaultScope.organization(organization_id),
            key_version=row.key_version,
        )
        # `usable_transcription_key` only answers with API keys, and
        # `unseal_secret` refuses an envelope whose kind is not the one named,
        # so this is one - the cast says so.
        return cast(ApiKeySecret, value).api_key.get_secret_value()

    async def usable_smtp_account(
        self, ctx: AuthContext, secret_id: UUID
    ) -> OrganizationSecret | None:
        """An SMTP account this principal can use, within the current organization."""
        row = await organization_secret_repo.get(
            self.db, secret_id, organization_id=ctx.organization_id
        )
        if row is None or row.kind != SecretKind.SMTP_ACCOUNT.value:
            return None
        if not await resolve_access(self.db, ctx, row, Perm.SECRETS_VIEW, resource_type=SECRET):
            return None
        return row

    async def smtp_email_service(self, ctx: AuthContext, secret_id: UUID) -> EmailService | None:
        """Hand a permitted account directly to its SMTP transport; no plaintext leaves it.

        Returns None after deletion, a kind mismatch or revoked access. The host is
        sealed with the password, so a workflow editor cannot redirect credentials.
        """
        from app.services.email.providers.smtp import SMTPProvider
        from app.services.email.service import EmailService

        row = await self.usable_smtp_account(ctx, secret_id)
        if row is None:
            return None
        account = unseal_kind(
            row.sealed_secret,
            model=SmtpAccountSecret,
            scope=VaultScope.organization(ctx.organization_id),
            key_version=row.key_version,
        )
        return EmailService(
            SMTPProvider(
                host=account.host,
                port=account.port,
                username=account.username,
                password=account.password.get_secret_value(),
                use_tls=True,
                tls_mode=account.tls_mode,
            ),
            from_email=str(account.from_email),
            from_name=account.from_name or None,
        )

    async def voice_call_key(self, organization_id: UUID, secret_id: UUID) -> StorableSecret | None:
        """The key a voice call is held with, to hang it up (#54).

        A narrow reader: the plaintext goes to the provider's hang-up endpoint
        and nowhere else. Takes an organization id because the reaper has no
        caller to build a context for. `None` when the key is gone.
        """
        row = await organization_secret_repo.get(
            self.db, secret_id, organization_id=organization_id
        )
        if row is None or row.usage_scope == "personal":
            return None
        return unseal_secret(
            row.sealed_secret,
            kind=SecretKind(row.kind),
            scope=VaultScope.organization(organization_id),
            key_version=row.key_version,
        )

    async def oauth_app(self, ctx: AuthContext, *, kind: SecretKind) -> OAuthAppSecret:
        value, _, _ = await self.oauth_app_identity(ctx, kind=kind)
        return value

    async def oauth_app_identity(
        self, ctx: AuthContext, *, kind: SecretKind
    ) -> tuple[OAuthAppSecret, UUID, UUID]:
        """`oauth_app_identity_for_org` for the caller's own organization."""
        return await self.oauth_app_identity_for_org(ctx.organization_id, kind=kind)

    async def oauth_app_identity_for_org(
        self, organization_id: UUID, *, kind: SecretKind
    ) -> tuple[OAuthAppSecret, UUID, UUID]:
        """Unseal the organization's OAuth client credentials for a token exchange.

        The third plaintext reader here, and deliberately the narrowest: it opens one
        kind of secret and the only field that leaves is the public `client_id`. The
        `client_secret` is a `SecretStr` that escapes only when the connect flow
        passes it straight to the provider's token endpoint - it is never returned to
        a client, logged, or written to the audit trail.

        One method over every such kind, because the rule is the same for each and
        it is the rule that matters: `github_oauth_app` connects a repository
        account, `google_oauth_app` and `microsoft_oauth_app` a mailbox, and each
        organization registers its own client for any of them. A deployment-wide
        client read from the environment would be a second mechanism for a credential
        at rest, which this repository does not have.

        Who may reach it is decided by the caller, not here: the org connect route
        requires `mcp:manage`, and a member's personal connect requires only an
        active membership of the organization. This method is not gated on
        `secrets:view`, for the same reason `resolve_for_bindings` is not: it is the
        runtime spending a credential the organization stored, not a member reading
        the vault.

        Only an *org-visible* credential qualifies, and only an unambiguous one: a
        member's private client must never be spent for the whole organization's
        connection behind their back, and with two org-visible ones stored, silently
        taking whichever name sorts first would key the connection to a credential
        nobody chose.

        Raises:
            NotFoundError: If the organization has stored no org-visible secret of
                this kind. A 4xx the connect UI shows as "add the credentials
                first", never a 500 - a missing prerequisite is the operator's to
                fix, not a bug.
            BadRequestError: If more than one org-visible one is stored - the fix
                (keep exactly one) is named, with the candidate names so the
                operator knows which rows collide.
        """
        rows = await organization_secret_repo.list_org_visible_by_kind(
            self.db, organization_id=organization_id, kind=kind.value
        )
        label = describe_kind(kind)
        if not rows:
            raise NotFoundError(
                message=f"Add an org-visible {label} secret in Vault, then connect.",
                details={"kind": kind.value},
            )
        if len(rows) > 1:
            raise BadRequestError(
                message=(
                    f"More than one org-visible {label} secret is stored; keep "
                    "exactly one so the connection is keyed to a credential "
                    "somebody chose."
                ),
                details={"kind": kind.value, "names": [row.name for row in rows]},
            )
        row = rows[0]
        value = unseal_secret(
            row.sealed_secret,
            kind=kind,
            scope=VaultScope.organization(organization_id),
            key_version=row.key_version,
        )
        # `unseal_secret` refuses anything whose sealed kind is not the one asked
        # for, so this is the concrete type - the cast narrows it without a branch
        # that could never run.
        return cast(OAuthAppSecret, value), row.id, row.authorization_generation

    async def app_secret(self, organization_id: UUID, *, kind: SecretKind) -> SecretValue:
        """The organization's single org-visible secret of one kind, by id alone.

        The same read as :meth:`oauth_app` with no caller, for the one path that
        has none: a GitHub App delivery arrives at a shared URL with no session,
        and the organization is already established - it is the one holding the
        grant the installation id selected (#1072). Taking an `AuthContext` here
        would mean fabricating one for a request nobody made, which is worse than
        naming the organization.

        Raises:
            NotFoundError: No org-visible secret of this kind. On the delivery
                path the caller turns that into "nothing to fire", because a
                deployment that has not stored an App is not one this delivery
                belongs to.
            BadRequestError: More than one, with the names that collide.
        """
        rows = await organization_secret_repo.list_org_visible_by_kind(
            self.db, organization_id=organization_id, kind=kind.value
        )
        label = describe_kind(kind)
        if not rows:
            raise NotFoundError(
                message=f"No org-visible {label} secret is stored.",
                details={"kind": kind.value},
            )
        if len(rows) > 1:
            raise BadRequestError(
                message=(
                    f"More than one org-visible {label} secret is stored; keep "
                    "exactly one so it is unambiguous which one is in use."
                ),
                details={"kind": kind.value, "names": [row.name for row in rows]},
            )
        row = rows[0]
        return unseal_secret(
            row.sealed_secret,
            kind=kind,
            scope=VaultScope.organization(organization_id),
            key_version=row.key_version,
        )

    async def get_usable(
        self, ctx: AuthContext, secret_id: UUID, *, kind: SecretKind
    ) -> OrganizationSecret:
        """A secret of `kind` the caller may see, for naming on something that sends it.

        Raises:
            NotFoundError: If the caller may not see it, or it is another kind - the
                two read alike, as everywhere secrets are referenced.
        """
        secret = await self._get(ctx, secret_id)
        if secret.usage_scope == "personal":
            raise BadRequestError(
                message="Personal model connections can only be selected as your agent's model",
                details={"secret_id": str(secret_id)},
            )
        if secret.kind != kind.value:
            raise NotFoundError(message="Secret not found", details={"secret_id": str(secret_id)})
        return secret

    async def _get(
        self, ctx: AuthContext, secret_id: UUID, *, perm: Perm = Perm.SECRETS_VIEW
    ) -> OrganizationSecret:
        """One secret the caller may reach, or reported as missing.

        A refusal reads as "not found" so a member cannot probe which keys exist
        by watching 403s turn into 404s - the same rule agents and skills follow.
        """
        secret = await organization_secret_repo.get(
            self.db, secret_id, organization_id=ctx.organization_id
        )
        if secret is None:
            raise NotFoundError(message="Secret not found", details={"secret_id": str(secret_id)})
        allowed = await resolve_access(self.db, ctx, secret, perm, resource_type=SECRET)
        if not allowed:
            raise NotFoundError(message="Secret not found", details={"secret_id": str(secret_id)})
        return secret

    async def _refuse_duplicate_name(self, ctx: AuthContext, name: str) -> None:
        existing = await organization_secret_repo.get_by_name(
            self.db, name, organization_id=ctx.organization_id
        )
        if existing is not None:
            raise AlreadyExistsError(
                message=(
                    f"A secret named '{name}' already exists. The name and the last four "
                    "characters are all anyone can see of a secret, so give this one a name "
                    "that tells them apart - or rotate the existing one if you are replacing it."
                ),
                details={"name": name},
            )

    @staticmethod
    def _refuse_kind_change(secret: OrganizationSecret, value: StorableSecret) -> None:
        if secret.kind != value.kind.value:
            raise BadRequestError(
                message=(
                    f"Secret '{secret.name}' holds a {secret.kind}, and a rotation cannot "
                    "change that - agents were published against the shape it has. Create a "
                    "new secret instead."
                ),
                details={"secret_id": str(secret.id), "kind": secret.kind},
            )


_BROWSER_PROFILE_FIELDS = (
    "full_name",
    "email",
    "phone",
    "address_line1",
    "address_line2",
    "city",
    "region",
    "postal_code",
    "country",
)


def _browser_fill_values(
    value: BrowserLoginSecret | BrowserProfileSecret, fields: Mapping[str, int]
) -> dict[str, SecretStr]:
    """Select values for requested field roles; never return an unselected profile field."""
    if not fields or any(type(index) is not int or index < 0 for index in fields.values()):
        raise BadRequestError(message="Choose observed browser fields for this fill")
    if isinstance(value, BrowserLoginSecret):
        roles = set(fields)
        if roles == {"password"}:
            return {"password": value.password}
        if roles == {"username", "password"}:
            return {
                "username": value.username,
                "password": value.password,
            }
        if roles == {"totp"}:
            if value.totp is None:
                raise BadRequestError(message="This browser login has no stored TOTP seed")
            return {"totp": SecretStr(_totp_code(value))}
        raise BadRequestError(
            message="Choose password and optional username fields, or one TOTP field"
        )
    if not set(fields).issubset(_BROWSER_PROFILE_FIELDS):
        raise BadRequestError(message="Choose available profile fields only")
    selected = {field: getattr(value, field) for field in fields}
    if any(stored is None for stored in selected.values()):
        raise BadRequestError(message="One or more selected profile fields are unavailable")
    return {field: stored for field, stored in selected.items() if stored is not None}


async def _wait_for_totp_window(value: BrowserTotpSecret) -> None:
    """Wait briefly when a TOTP code would expire while the page receives it."""
    remaining = value.period_seconds - time.time() % value.period_seconds
    if remaining < 2:
        await asyncio.sleep(remaining + 0.01)


def _totp_code(value: BrowserLoginSecret) -> str:
    """Generate a fresh TOTP code for one explicit fill request."""
    assert value.totp is not None
    totp = value.totp
    raw_seed = totp.seed.get_secret_value()
    key = base64.b32decode(raw_seed.upper() + "=" * (-len(raw_seed) % 8))
    counter = int(time.time() // totp.period_seconds)
    digest = hmac.new(
        key,
        struct.pack(">Q", counter),
        digestmod=getattr(hashlib, totp.algorithm.lower()),
    ).digest()
    offset = digest[-1] & 0x0F
    truncated = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(truncated % (10**totp.digits)).zfill(totp.digits)


class SecretSharingGuard:
    """Keep personal connections private and browser credentials on their named-agent path."""

    async def before_visibility(
        self, db: AsyncSession, ctx: AuthContext, resource: OwnedResource, visibility: Visibility
    ) -> None:
        assert isinstance(resource, OrganizationSecret)
        if (
            resource.kind in {kind.value for kind in BROWSER_SECRET_KINDS}
            and visibility.value != resource.visibility
        ):
            raise BadRequestError(
                message="Change browser credential policy through its typed replacement, not visibility."
            )
        if resource.usage_scope == "personal" and visibility is not Visibility.PRIVATE:
            raise BadRequestError(message="Personal model connections cannot be shared")

    async def before_share(
        self, db: AsyncSession, ctx: AuthContext, resource: OwnedResource, change: ShareChange
    ) -> Deferred | None:
        assert isinstance(resource, OrganizationSecret)
        if resource.kind in {kind.value for kind in BROWSER_SECRET_KINDS}:
            raise BadRequestError(
                message="Browser credentials use their named-agent policy and cannot use resource grants."
            )
        if resource.usage_scope == "personal" and (
            change.visibility is not Visibility.PRIVATE
            or change.subject_user_id is not None
            or change.subject_group_id is not None
        ):
            raise BadRequestError(message="Personal model connections cannot be shared")
        return None

    async def adopt(self, db: AsyncSession, ctx: AuthContext, resource: OwnedResource) -> None:
        """Preserve the existing ownership claim for ordinary private vault entries."""
        assert isinstance(resource, OrganizationSecret)
        if resource.kind in {kind.value for kind in BROWSER_SECRET_KINDS}:
            raise BadRequestError(message="Browser credential ownership cannot be transferred.")
        resource.owner_user_id = ctx.subject_id
        await record_audit(
            db,
            actor_user_id=ctx.subject_id,
            organization_id=ctx.organization_id,
            action="resource.owner_claimed",
            target_type=SECRET.key,
            target_id=str(resource.id),
            details={"owner_user_id": str(resource.owner_user_id)},
        )


register_sharing_guard(SECRET, SecretSharingGuard())
