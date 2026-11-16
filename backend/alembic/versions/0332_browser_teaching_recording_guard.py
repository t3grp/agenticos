"""Allow one active teaching recording for each managed browser call.

Concurrent starts can both miss an existing recording. This index makes the
browser identity the database boundary that resolves that race.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0332_browser_teaching_recording_guard"
down_revision: str | Sequence[str] | None = "0326_browser_teaching"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_browser_teaching_recording_per_browser "
        "ON browser_teaching_sessions (organization_id, run_id, call_id) "
        "WHERE status = 'recording'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_browser_teaching_recording_per_browser")
