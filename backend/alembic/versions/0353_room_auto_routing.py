"""Keep automatic routing optional and give each paid decision a durable receipt.

The policy is per conversation because private/public rooms and standalone chats
do not have a project lead policy. Each accepted target-less post gets one frozen
decision row before the worker may call a model. That row keeps the original post,
the router and model choices, the bounded candidate set and the one-shot claim so
an uncertain provider outcome can be closed as unknown instead of charged again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0353_room_auto_routing"
down_revision: str | None = "0352_room_thread_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "room_auto_routing_policies",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("router_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("router_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("quiet_window_ms", sa.Integer(), server_default="2500", nullable=False),
        sa.Column("max_responders", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            name="fk_room_auto_routing_policy_conversation",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("room_auto_routing_policies_revision_check")),
        sa.CheckConstraint(
            "quiet_window_ms BETWEEN 0 AND 10000",
            name=op.f("room_auto_routing_policies_quiet_window_ms_check"),
        ),
        sa.CheckConstraint(
            "max_responders BETWEEN 1 AND 4",
            name=op.f("room_auto_routing_policies_max_responders_check"),
        ),
        sa.CheckConstraint(
            "(router_agent_id IS NULL) = (router_version_id IS NULL) AND "
            "(NOT enabled OR (router_agent_id IS NOT NULL AND model_profile_id IS NOT NULL))",
            name=op.f("room_auto_routing_policies_router_check"),
        ),
        sa.PrimaryKeyConstraint("organization_id", "conversation_id"),
    )

    op.create_table(
        "room_auto_routing_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("accepted_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_root_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("router_agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("router_name", sa.String(length=120), nullable=False),
        sa.Column("router_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_profile_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("quiet_window_ms", sa.Integer(), nullable=False),
        sa.Column("max_responders", sa.Integer(), nullable=False),
        sa.Column("candidate_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("grouped_message_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("router_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("continued_into_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            name="fk_room_auto_routing_decision_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id", "submission_id"],
            [
                "room_submissions.organization_id",
                "room_submissions.conversation_id",
                "room_submissions.id",
            ],
            name="fk_room_auto_routing_decision_submission",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "accepted_message_id"],
            ["messages.conversation_id", "messages.id"],
            name="fk_room_auto_routing_decision_message",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "thread_root_message_id"],
            ["messages.conversation_id", "messages.id"],
            name="fk_room_auto_routing_decision_thread",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requester_id"],
            ["users.id"],
            name=op.f("room_auto_routing_decisions_requester_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["router_run_id"],
            ["agent_runs.id"],
            name=op.f("room_auto_routing_decisions_router_run_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id", "continued_into_id"],
            [
                "room_auto_routing_decisions.organization_id",
                "room_auto_routing_decisions.conversation_id",
                "room_auto_routing_decisions.id",
            ],
            name="fk_room_auto_routing_decision_continued_into",
            ondelete="SET NULL (continued_into_id)",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "conversation_id",
            "id",
            name="uq_room_auto_routing_decision_scope",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "conversation_id",
            "submission_id",
            name="uq_room_auto_routing_decision_submission",
        ),
        sa.UniqueConstraint("router_run_id", name="room_auto_routing_decisions_router_run_id_key"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'selected', 'abstained', 'continued', 'failed', 'unknown')",
            name=op.f("room_auto_routing_decisions_status_check"),
        ),
        sa.CheckConstraint(
            "policy_revision >= 1",
            name=op.f("room_auto_routing_decisions_policy_revision_check"),
        ),
        sa.CheckConstraint(
            "model_profile_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("room_auto_routing_decisions_model_profile_fingerprint_check"),
        ),
        sa.CheckConstraint(
            "quiet_window_ms BETWEEN 0 AND 10000",
            name=op.f("room_auto_routing_decisions_quiet_window_ms_check"),
        ),
        sa.CheckConstraint(
            "max_responders BETWEEN 1 AND 4",
            name=op.f("room_auto_routing_decisions_max_responders_check"),
        ),
        sa.CheckConstraint(
            "claim_generation >= 0",
            name=op.f("room_auto_routing_decisions_claim_generation_check"),
        ),
        sa.CheckConstraint(
            "(claim_token IS NULL AND claim_generation = 0) OR "
            "(claim_token IS NOT NULL AND claim_generation >= 1)",
            name=op.f("room_auto_routing_decisions_claim_check"),
        ),
        sa.CheckConstraint(
            "status <> 'running' OR claim_token IS NOT NULL",
            name=op.f("room_auto_routing_decisions_running_claim_check"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(candidate_snapshot) = 'array' "
            "AND jsonb_array_length(candidate_snapshot) <= 100 "
            "AND octet_length(candidate_snapshot::text) <= 262144",
            name=op.f("room_auto_routing_decisions_candidate_snapshot_check"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(grouped_message_ids) = 'array' "
            "AND jsonb_array_length(grouped_message_ids) BETWEEN 1 AND 8",
            name=op.f("room_auto_routing_decisions_grouped_message_ids_check"),
        ),
        sa.CheckConstraint(
            "decision_output IS NULL OR (jsonb_typeof(decision_output) = 'object' "
            "AND octet_length(decision_output::text) <= 8192)",
            name=op.f("room_auto_routing_decisions_decision_output_check"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("room_auto_routing_decisions_pkey")),
    )
    op.create_index(
        "room_auto_routing_ready_idx",
        "room_auto_routing_decisions",
        ["organization_id", "conversation_id", "ready_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "room_auto_routing_activity_idx",
        "room_auto_routing_decisions",
        ["organization_id", "conversation_id", "status"],
    )
    op.create_index(
        "room_auto_routing_group_idx",
        "room_auto_routing_decisions",
        [
            "organization_id",
            "conversation_id",
            "requester_id",
            "thread_root_message_id",
            "accepted_at",
        ],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "LOCK TABLE room_auto_routing_policies, room_auto_routing_decisions "
        "IN ACCESS EXCLUSIVE MODE"
    )
    has_data = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM room_auto_routing_policies) "
            "OR EXISTS (SELECT 1 FROM room_auto_routing_decisions)"
        )
    )
    if has_data:
        raise RuntimeError("Cannot discard automatic routing policies or decision receipts")

    op.drop_index("room_auto_routing_group_idx", table_name="room_auto_routing_decisions")
    op.drop_index("room_auto_routing_activity_idx", table_name="room_auto_routing_decisions")
    op.drop_index("room_auto_routing_ready_idx", table_name="room_auto_routing_decisions")
    op.drop_table("room_auto_routing_decisions")
    op.drop_table("room_auto_routing_policies")
