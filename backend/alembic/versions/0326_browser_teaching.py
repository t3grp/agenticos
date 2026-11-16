"""Store redacted managed-browser demonstrations and typed skill provenance.

Teaching rows retain their minimal review state for 24 hours. Applying a proposal
keeps only the approved skill procedure; the transient steps are erased.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0326_browser_teaching"
down_revision: str | Sequence[str] | None = "0325_merge_t3_rooms_v173"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "skill_versions_ck_skill_version_source_check"
_SOURCES = (
    "'backfill', 'created', 'edit', 'resources', 'library', 'workspace', 'improve', "
    "'restore', 'catch_up', 'from_fork'"
)


def upgrade() -> None:
    op.create_table(
        "browser_teaching_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("lease_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("origin", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pause_reason", sa.String(length=24), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("events", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capture_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["proposal_id"], ["skill_proposals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="browser_teaching_sessions_org_key"
        ),
    )
    op.create_index(
        "ix_browser_teaching_owner_status",
        "browser_teaching_sessions",
        ["organization_id", "owner_user_id", "status"],
    )
    op.add_column(
        "skill_proposals",
        sa.Column("browser_teaching_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_skill_proposals_browser_teaching",
        "skill_proposals",
        ["browser_teaching_id"],
        unique=True,
    )
    op.drop_constraint(op.f(_CHECK), "skill_versions", type_="check")
    op.create_check_constraint(
        op.f(_CHECK), "skill_versions", f"source IN ({_SOURCES}, 'browser_teaching')"
    )


def downgrade() -> None:
    op.execute("UPDATE skill_versions SET source = 'edit' WHERE source = 'browser_teaching'")
    op.drop_constraint(op.f(_CHECK), "skill_versions", type_="check")
    op.create_check_constraint(op.f(_CHECK), "skill_versions", f"source IN ({_SOURCES})")
    op.drop_index("ix_skill_proposals_browser_teaching", table_name="skill_proposals")
    op.drop_column("skill_proposals", "browser_teaching_id")
    op.drop_index("ix_browser_teaching_owner_status", table_name="browser_teaching_sessions")
    op.drop_table("browser_teaching_sessions")
