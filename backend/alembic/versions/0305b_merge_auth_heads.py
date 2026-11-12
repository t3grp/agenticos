"""Join the MFA and question-nudge migration branches without changing schema.

The MFA challenge and question-nudge revisions both descend from separate
additive branches after `0303_artifact_favourites`. This merge lets Alembic
apply both branches through one head without rewriting either migration.
"""

revision: str = "0305b_merge_auth_heads"
down_revision: tuple[str, str] = (
    "0305_question_nudge",
    "0305a_totp_last_counter",
)
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
