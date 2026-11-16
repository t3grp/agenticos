"""Join the media operation and provider account pool migration histories.

Both 0326 revisions are preserved. Their schema changes live in the parents;
this revision only gives Alembic one head after the branches merge.
"""

from collections.abc import Sequence

revision: str = "0332_merge_media_account_pools"
down_revision: tuple[str, str] = (
    "0326_workflow_media_operations",
    "0326_provider_account_pools",
)
branch_labels: None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
