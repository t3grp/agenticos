"""Join the T3 network-connections head with the v1.86.1 upstream appearance head.

Both lines branch from 0342_criterion_verdicts and touch different tables. This
revision adds no schema changes; it keeps both upgrade paths reachable without
renumbering an applied revision.
"""

revision: str = "0348_merge_t3_v1861"
down_revision: tuple[str, str] = (
    "0347_network_connections",
    "0344_user_appearance",
)
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
