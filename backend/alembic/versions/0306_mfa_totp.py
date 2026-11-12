"""TOTP for console sign-in: `totp_*` columns on `users`.

A second factor for the password login, so a stolen password alone no longer
opens the console. The secret is sealed in the vault under the user's own
scope (see `app/core/vault.py`); the columns hold only the envelope and its
key version. `totp_enabled` is separate from the secret because enrolment
stores the secret first and flips the flag only once the authenticator has
proved it can produce a code. Recovery codes are stored as SHA-256 digests,
never the codes themselves.

Existing rows get no secret and the flag off - nobody's sign-in changes
until they enrol. Nothing here forces enrolment; whether the deployment
requires it is a policy decision for the operator, not this migration.

Numbering: this revision is `0306` on this branch, sitting directly on the
`0302` base for now. The row-level-access build claims `0303` and the
failure-learning-loop build claims `0304`/`0305`; merge order is row-level
first, then the loop, then this branch (`0306` and up) - so this revision is
re-linked onto the merged head when this branch rebases, and the grants
migration on this branch (`0303`) is renumbered with it.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0306_mfa_totp"
down_revision: str = "0302_merge_upstream"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret_encrypted", sa.String(1000), nullable=True))
    op.add_column("users", sa.Column("totp_key_version", sa.Integer(), nullable=True))
    op.add_column(
        "users",
        sa.Column("totp_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("users", sa.Column("totp_recovery_hashes", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "totp_recovery_hashes")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_key_version")
    op.drop_column("users", "totp_secret_encrypted")
