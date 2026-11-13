"""Keep the existing branch merge after the secret-request migration.

The secret-request migration now follows ``0309_trigger_conversation_mode``,
which already includes the TOTP history. This no-DDL merge preserves the
existing revision id referenced by ``0309_trigger_conversation_mode`` while
joining its TOTP parent with ``0310_secret_requests``.
"""

revision: str = "0308_merge_0305a_and_0307"
down_revision: tuple[str, str] = ("0305a_totp_last_counter", "0310_secret_requests")
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
