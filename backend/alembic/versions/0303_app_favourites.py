"""Store app favourites per user so one person's sidebar preference stays private.

Revision ID: 0303_app_favourites
Revises: 0302_merge_upstream
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0303_app_favourites"
down_revision: str | None = "0302_merge_upstream"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_favourites",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "artifact_id"),
    )
    op.create_index(
        op.f("artifact_favourites_artifact_id_idx"),
        "artifact_favourites",
        ["artifact_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("artifact_favourites_artifact_id_idx"), table_name="artifact_favourites")
    op.drop_table("artifact_favourites")
