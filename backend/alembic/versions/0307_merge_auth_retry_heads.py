"""Join the OIDC MFA and retry-link migration branches without changing schema."""

revision: str = "0307_merge_auth_retry_heads"
down_revision: tuple[str, str] = (
    "0305b_merge_auth_heads",
    "0306_run_retry_link",
)
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
