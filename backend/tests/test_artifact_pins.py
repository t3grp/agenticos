"""Pinning an artifact: a person's own sidebar shortcut to a page they can open.

Pinning is a shortcut, not a grant - it reaches nobody who could not already
read the page - and a person's own choice, made in the console: an API key
changes none of them.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AuthorizationError
from app.core.permissions import AuthContext, OrgRoleName, Perm
from app.db.models.artifact import ArtifactPin
from app.services import artifact as artifacts
from app.services.artifact import ArtifactService

pytestmark = pytest.mark.anyio

PATH = "app.services.artifact"


def test_a_pin_row_names_the_page_and_the_person() -> None:
    artifact_id, user_id = uuid.uuid4(), uuid.uuid4()

    shown = repr(ArtifactPin(artifact_id=artifact_id, user_id=user_id))

    assert str(artifact_id) in shown and str(user_id) in shown


@pytest.mark.anyio
@pytest.mark.security
@pytest.mark.parametrize("change", ["pin", "unpin"])
async def test_a_key_changes_nobody_s_pins(change: str) -> None:
    """A read-only key could otherwise pin or unpin its issuer's sidebar."""
    key = AuthContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        role=OrgRoleName.MEMBER,
        api_key_id=uuid.uuid4(),
        key_scopes=frozenset({Perm.ARTIFACTS_VIEW}),
    )
    service = artifacts.ArtifactService(MagicMock())

    with (
        patch.object(artifacts.artifact_repo, "pin", new=AsyncMock()) as pin,
        patch.object(artifacts.artifact_repo, "unpin", new=AsyncMock()) as unpin,
        pytest.raises(AuthorizationError) as caught,
    ):
        await getattr(service, change)(key, uuid.uuid4())

    pin.assert_not_awaited()
    unpin.assert_not_awaited()
    assert "Pinning an app" in str(caught.value)


class TestListPinned:
    def _ctx(self) -> AuthContext:
        return AuthContext(
            user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role=OrgRoleName.MEMBER
        )

    async def test_nobody_pinned_nothing_lists_and_nothing_is_read(self) -> None:
        service = ArtifactService(MagicMock())
        with (
            patch.object(artifacts.artifact_repo, "pinned_ids", new=AsyncMock(return_value=[])),
            patch.object(artifacts.artifact_repo, "list_visible", new=AsyncMock()) as visible,
        ):
            listed = await service.list_pinned(self._ctx())

        assert listed.items == [] and listed.total == 0
        visible.assert_not_awaited()
