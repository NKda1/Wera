"""run detail

Adds the one column a named stage needs: what the run is doing *right now*, in words.

0005 gave a run a `phase`, a `done` and a `total`, which was enough to stop the bar standing
still. It was not enough to say what was happening: `phase` is a short enum, so "searching" had
to cover twenty queries across four boards and "scoring" had to cover forty jobs. Every call site
already held the missing noun and threw it away — `on_query` has the board's domain in scope, the
scrape loop has the URL, the scoring loop has the job — so this is a column, not a mechanism.

Nullable and never read as fact: a heartbeat is best-effort (`intake.report` swallows its own
errors), so an absent detail means "nothing said", not "nothing happening".

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31 10:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("detail", sa.String(length=200), nullable=True))
    # `phase` keeps its String(16): the longest of the new stage names is "discovering" at 11, so
    # the column is already wide enough and the vocabulary lives in `schemas.runs.RunPhase`.
    # A finished run has nothing to be doing, so nothing to say about it.
    op.execute("update intake_runs set detail = null where status not in ('queued', 'running')")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        batch_op.drop_column("detail")
