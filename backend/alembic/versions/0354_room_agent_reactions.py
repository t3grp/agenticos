"""Add immutable agent-attributed room reaction actions."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0354_room_agent_reactions"
down_revision: str | None = "0353_room_auto_routing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "room_agent_message_reactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(length=120), nullable=False),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("reaction_only", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("emoji", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_room_agent_message_reaction_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            name="fk_room_agent_message_reaction_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "message_id"],
            ["messages.conversation_id", "messages.id"],
            name="fk_room_agent_message_reaction_message",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id", "submission_id"],
            [
                "room_submissions.organization_id",
                "room_submissions.conversation_id",
                "room_submissions.id",
            ],
            name="fk_room_agent_message_reaction_submission",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id", "decision_id"],
            [
                "room_auto_routing_decisions.organization_id",
                "room_auto_routing_decisions.conversation_id",
                "room_auto_routing_decisions.id",
            ],
            name="fk_room_agent_message_reaction_decision",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "char_length(emoji) BETWEEN 1 AND 32",
            name="ck_room_agent_message_reaction_emoji_length",
        ),
        sa.CheckConstraint(
            "(source = 'tool' AND submission_id IS NOT NULL AND decision_id IS NULL "
            "AND tool_call_id IS NOT NULL) OR "
            "(source = 'automatic' AND submission_id IS NULL AND decision_id IS NOT NULL "
            "AND tool_call_id IS NULL AND reaction_only = false)",
            name="ck_room_agent_message_reaction_provenance",
        ),
        sa.CheckConstraint(
            "NOT reaction_only OR source = 'tool'",
            name="ck_room_agent_message_reaction_only_tool",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("room_agent_message_reactions_pkey")),
    )
    op.create_index(
        "ix_room_agent_message_reactions_message",
        "room_agent_message_reactions",
        ["organization_id", "conversation_id", "message_id"],
    )
    op.create_index(
        "ix_room_agent_message_reactions_run_audit",
        "room_agent_message_reactions",
        ["organization_id", "run_id", "created_at", "id"],
    )
    op.create_index(
        "uq_room_agent_message_reactions_tool_call",
        "room_agent_message_reactions",
        ["organization_id", "conversation_id", "run_id", "tool_call_id"],
        unique=True,
        postgresql_where=sa.text("source = 'tool'"),
    )
    op.create_index(
        "uq_room_agent_message_reactions_decision",
        "room_agent_message_reactions",
        ["organization_id", "conversation_id", "decision_id"],
        unique=True,
        postgresql_where=sa.text("decision_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE room_agent_message_reactions IN ACCESS EXCLUSIVE MODE")
    has_data = op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM room_agent_message_reactions)")
    )
    if has_data:
        raise RuntimeError("Cannot discard agent room reaction action provenance")
    op.drop_index(
        "uq_room_agent_message_reactions_decision",
        table_name="room_agent_message_reactions",
    )
    op.drop_index(
        "uq_room_agent_message_reactions_tool_call",
        table_name="room_agent_message_reactions",
    )
    op.drop_index(
        "ix_room_agent_message_reactions_message",
        table_name="room_agent_message_reactions",
    )
    op.drop_index(
        "ix_room_agent_message_reactions_run_audit",
        table_name="room_agent_message_reactions",
    )
    op.drop_table("room_agent_message_reactions")
