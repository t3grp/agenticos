"""Let a person pin an app to their sidebar.

A table of pins, mirroring `artifact_followers` (0106): one row per person per
page, a unique constraint so pinning twice is pinning once, and CASCADE deletes
so a deleted page or person takes its pins with it.

Revision ID: 0296_artifact_pins
Revises: 0295_tool_approval_consent
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0296_artifact_pins"
down_revision: str | None = "0295_tool_approval_consent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_pins",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id", "user_id", name="uq_artifact_pin"),
    )
    op.create_index("artifact_pins_artifact_id_idx", "artifact_pins", ["artifact_id"], unique=False)
    op.create_index("artifact_pins_user_id_idx", "artifact_pins", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("artifact_pins_user_id_idx", table_name="artifact_pins")
    op.drop_index("artifact_pins_artifact_id_idx", table_name="artifact_pins")
    op.drop_table("artifact_pins")
