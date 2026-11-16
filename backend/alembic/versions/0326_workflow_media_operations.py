"""Retain workflow media receipts and ambiguous provider outcomes.

An accepted provider request may outlive a worker or its lease. Its phase and
safe receipt must survive that worker so a retry can poll the same job or
refuse an ambiguous resubmission.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0326_workflow_media_operations"
down_revision: str | Sequence[str] | None = "0326_provider_account_pools"
branch_labels: None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_media_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("secret_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("secret_key_version", sa.Integer(), nullable=True),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("provider_receipt", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("output", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("reported_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("cost_is_partial", sa.Boolean(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "phase IN ('prepared', 'submitting', 'submitted', 'collecting', 'stored', 'unknown', 'failed', 'cancelled')",
            name=op.f("workflow_media_operations_ck_workflow_media_operation_phase_check"),
        ),
        sa.CheckConstraint(
            "estimated_cost_usd IS NULL OR estimated_cost_usd >= 0",
            name=op.f(
                "workflow_media_operations_ck_workflow_media_operation_estimated_cost_check"
            ),
        ),
        sa.CheckConstraint(
            "reported_cost_usd IS NULL OR reported_cost_usd >= 0",
            name=op.f(
                "workflow_media_operations_ck_workflow_media_operation_reported_cost_check"
            ),
        ),
        sa.ForeignKeyConstraint(["node_attempt_id"], ["node_attempts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["secret_id"], ["organization_secrets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("workflow_media_operations_pkey")),
        sa.UniqueConstraint("node_run_id", name=op.f("uq_workflow_media_operation_node_run")),
    )
    op.create_index(
        "ix_workflow_media_operation_run",
        "workflow_media_operations",
        ["organization_id", "workflow_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_media_operation_run", table_name="workflow_media_operations")
    op.drop_table("workflow_media_operations")
