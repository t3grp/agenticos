"""Secret requests an agent raised mid-run (#13).

A run that needs a credential it does not have parks on `request_secret`
and leaves one row per ask in `secret_requests`: which secret, what it is
for, and what happened to it. The value is never stored here - fulfil seals
it straight into `organization_secrets` and this table keeps only the
created secret's id and hint, so no read of a request can leak a value.

This revision follows #51's `0306_run_retry_link`. The chain has one head
here; #32's draft also claims 0304/0305 and the upstream sync claims
0304-0307, so the number must be re-checked against `main` at merge time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0307_secret_requests"
down_revision: str | None = "0306_run_retry_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "secret_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("secret_name", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="api_key"),
        sa.Column("what_for", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("fulfilled_secret_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fulfilled_hint", sa.String(length=8), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["fulfilled_secret_id"],
            ["organization_secrets.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("secret_requests_organization_id_idx"),
        "secret_requests",
        ["organization_id"],
        unique=False,
    )
    op.create_index(op.f("secret_requests_run_id_idx"), "secret_requests", ["run_id"], unique=False)
    op.create_index(op.f("secret_requests_status_idx"), "secret_requests", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("secret_requests_status_idx"), table_name="secret_requests")
    op.drop_index(op.f("secret_requests_run_id_idx"), table_name="secret_requests")
    op.drop_index(op.f("secret_requests_organization_id_idx"), table_name="secret_requests")
    op.drop_table("secret_requests")
