"""The scope shared by secret storage, replacement, browser use and key rotation."""

from uuid import UUID

from app.core.exceptions import BadRequestError
from app.core.secret_kinds import BROWSER_SECRET_KINDS
from app.core.vault import VaultScope


def organization_secret_scope(
    *, organization_id: UUID, kind: str, usage_scope: str, owner_user_id: UUID | None
) -> VaultScope:
    """Preserve technical scopes; browser records require an immutable personal owner."""
    if kind not in BROWSER_SECRET_KINDS:
        return VaultScope.organization(organization_id)
    if usage_scope not in ("personal", "organization") or owner_user_id is None:
        raise BadRequestError(message="Browser credentials require a personal owner")
    if usage_scope == "organization":
        return VaultScope.organization(organization_id)
    return VaultScope.browser_member(organization_id, owner_user_id)
