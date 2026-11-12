"""Grants can expire: `expires_at` on `resource_grants`.

A share with no expiry is permanent, and permanent shares are what an auditor
asks about first - who can still reach a resource because somebody shared it
months ago and nobody revoked it. With the column, a share can be given a
lifetime at creation, and the repository's access reads exclude expired rows,
so the access stops without anyone deleting the row. The row stays, so the
Sharing panel can still show it, and the owner can renew or revoke it.

Existing rows keep `NULL` - permanent, as every grant made before expiry
existed was. Nothing is silently shortened by the migration.

Numbering: this revision is `0303` on this branch. The row-level-access build
claims `0303` on its own branch and the failure-learning-loop build claims
`0304`/`0305`; merge order is row-level first, so this revision is renumbered
when this branch rebases onto the merged head.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0303_resource_grant_expires_at"
down_revision: str = "0302_merge_upstream"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column(
        "resource_grants",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("resource_grants", "expires_at")
