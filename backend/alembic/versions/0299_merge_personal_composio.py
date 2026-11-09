"""Join deployed personal models and app connections without rewriting either history.

The native Claude deployment already holds personal model ownership at 0296.
App connections descend from the parallel 0297 agent-message revision. Keeping
both parents lets either deployment apply only its missing additive changes;
this merge itself changes no tables, credentials, or ownership data.
"""

revision: str = "0299_merge_personal_composio"
down_revision: tuple[str, str] = (
    "0296_personal_model_connections",
    "0298_composio_connections",
)
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
