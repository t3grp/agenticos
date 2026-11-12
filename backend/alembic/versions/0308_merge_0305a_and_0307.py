"""Join the #53 MFA branch and the #51 retry-link branch without rewriting history.

``0305_question_nudge`` has two children: #53's ``0304_resource_grant_expires_at``
(MFA/TOTP, via ``0305a_totp_last_counter``) and #51's ``0306_run_retry_link``
(via ``0307_secret_requests``). Both landed on ``t3`` before either could see
the other, so ``t3`` held two heads and ``alembic upgrade head`` refused with
``Multiple head revisions are present``. This merge joins them; it changes no
tables, credentials, or data.
"""

revision: str = "0308_merge_0305a_and_0307"
down_revision: tuple[str, str] = (
    "0305a_totp_last_counter",
    "0307_secret_requests",
)
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
