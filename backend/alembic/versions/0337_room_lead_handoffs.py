"""Room lead policy and reviewed canonical task handoff provenance.

Revision 0337_room_lead_handoffs follows t3 head 0336_merge_core_v1761.
Downgrade refuses accepted links: preserve canonical task responsibility and
restore a matching backup or retain this schema until handoffs are reconciled.
"""

import sqlalchemy as sa

from alembic import op

revision = "0337_room_lead_handoffs"
down_revision = "0336_merge_core_v1761"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_room_submission_scope_id",
        "room_submissions",
        ["organization_id", "conversation_id", "id"],
    )
    op.create_unique_constraint(
        "uq_virtual_table_record_org_id", "virtual_table_records", ["organization_id", "id"]
    )
    op.add_column(
        "room_submissions",
        sa.Column("ask_lead", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "room_submissions", sa.Column("lead_policy_revision", sa.Integer(), nullable=True)
    )
    op.execute(
        "CREATE TABLE room_lead_policies (\n\tconversation_id UUID NOT NULL, \n\torganization_id UUID NOT NULL, \n\trevision INTEGER NOT NULL, \n\tconfiguration JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT room_lead_policies_pkey PRIMARY KEY (conversation_id), \n\tCONSTRAINT fk_room_policy_conversation FOREIGN KEY(organization_id, conversation_id) REFERENCES conversations (organization_id, id) ON DELETE CASCADE, \n\tCONSTRAINT room_lead_policies_organization_id_fkey FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE TABLE room_routing_proposals (\n\tid UUID NOT NULL, \n\torganization_id UUID NOT NULL, \n\tconversation_id UUID NOT NULL, \n\tsource_submission_id UUID NOT NULL, \n\tthread_root_message_id UUID, \n\trequester_id UUID NOT NULL, \n\tpolicy_revision INTEGER NOT NULL, \n\trevision INTEGER NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\titems JSONB NOT NULL, \n\tdecision_key VARCHAR(128), \n\tselected_items JSONB, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT room_routing_proposals_pkey PRIMARY KEY (id), \n\tCONSTRAINT fk_room_proposal_conversation FOREIGN KEY(organization_id, conversation_id) REFERENCES conversations (organization_id, id) ON DELETE CASCADE, \n\tCONSTRAINT room_routing_proposals_organization_id_key UNIQUE (organization_id, source_submission_id), \n\tCONSTRAINT uq_room_proposal_scope_id UNIQUE (organization_id, conversation_id, id), \n\tCONSTRAINT fk_room_proposal_source_scope FOREIGN KEY(organization_id, conversation_id, source_submission_id) REFERENCES room_submissions (organization_id, conversation_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_room_proposal_thread_scope FOREIGN KEY(conversation_id, thread_root_message_id) REFERENCES messages (conversation_id, id) ON DELETE CASCADE, \n\tCONSTRAINT room_routing_proposals_decision_check CHECK (status IN ('pending', 'accepted', 'discarded')), \n\tCONSTRAINT room_routing_proposals_organization_id_fkey FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, \n\tCONSTRAINT room_routing_proposals_source_submission_id_fkey FOREIGN KEY(source_submission_id) REFERENCES room_submissions (id) ON DELETE CASCADE, \n\tCONSTRAINT room_routing_proposals_thread_root_message_id_fkey FOREIGN KEY(thread_root_message_id) REFERENCES messages (id) ON DELETE CASCADE, \n\tCONSTRAINT room_routing_proposals_requester_id_fkey FOREIGN KEY(requester_id) REFERENCES users (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE INDEX room_routing_proposals_conversation_id_idx ON room_routing_proposals (conversation_id)"
    )
    op.execute(
        "CREATE TABLE room_task_links (\n\ttask_id UUID NOT NULL, \n\torganization_id UUID NOT NULL, \n\tconversation_id UUID NOT NULL, \n\tproposal_id UUID NOT NULL, \n\titem_id UUID NOT NULL, \n\tthread_root_message_id UUID, \n\trequester_id UUID NOT NULL, \n\treviewer_user_id UUID NOT NULL, \n\tagent_id UUID NOT NULL, \n\tversion_id UUID NOT NULL, \n\taccepted_revision INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT room_task_links_pkey PRIMARY KEY (task_id), \n\tCONSTRAINT room_task_links_organization_id_key UNIQUE (organization_id, proposal_id, item_id), \n\tCONSTRAINT fk_room_task_proposal_scope FOREIGN KEY(organization_id, conversation_id, proposal_id) REFERENCES room_routing_proposals (organization_id, conversation_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_room_task_tenant FOREIGN KEY(organization_id, task_id) REFERENCES virtual_table_records (organization_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_room_task_thread_scope FOREIGN KEY(conversation_id, thread_root_message_id) REFERENCES messages (conversation_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_room_task_conversation FOREIGN KEY(organization_id, conversation_id) REFERENCES conversations (organization_id, id) ON DELETE CASCADE, \n\tCONSTRAINT room_task_links_task_id_fkey FOREIGN KEY(task_id) REFERENCES task_links (record_id) ON DELETE CASCADE, \n\tCONSTRAINT room_task_links_organization_id_fkey FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, \n\tCONSTRAINT room_task_links_proposal_id_fkey FOREIGN KEY(proposal_id) REFERENCES room_routing_proposals (id) ON DELETE CASCADE, \n\tCONSTRAINT room_task_links_thread_root_message_id_fkey FOREIGN KEY(thread_root_message_id) REFERENCES messages (id) ON DELETE CASCADE, \n\tCONSTRAINT room_task_links_requester_id_fkey FOREIGN KEY(requester_id) REFERENCES users (id) ON DELETE CASCADE, \n\tCONSTRAINT room_task_links_reviewer_user_id_fkey FOREIGN KEY(reviewer_user_id) REFERENCES users (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE INDEX room_task_links_conversation_id_idx ON room_task_links (conversation_id)"
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM room_task_links)")):
        raise RuntimeError(
            "Reconcile accepted room handoffs before downgrading revision 0337_room_lead_handoffs; retain canonical tasks and their dispatch provenance"
        )
    op.drop_table("room_task_links")
    op.drop_table("room_routing_proposals")
    op.drop_table("room_lead_policies")
    op.drop_constraint("uq_virtual_table_record_org_id", "virtual_table_records", type_="unique")
    op.drop_constraint("uq_room_submission_scope_id", "room_submissions", type_="unique")
    op.drop_column("room_submissions", "lead_policy_revision")
    op.drop_column("room_submissions", "ask_lead")
