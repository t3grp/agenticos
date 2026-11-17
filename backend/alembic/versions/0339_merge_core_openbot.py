"""Join the core release checkpoint and OpenBot histories without replaying DDL.

Both parents retain their existing migration identity and ancestry. Upgrading
either completed branch applies only the missing branch before this pure join.
"""

revision: str = "0339_merge_core_openbot"
down_revision: tuple[str, str] = ("0338_message_reply_excerpt", "0335_browser_teaching")
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
