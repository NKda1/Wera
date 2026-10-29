"""run phase

Adds the fields the Jobs screen needs to show what a run is actually doing. `progress` existed
but only ever moved in demo mode, where `settle()` animates it against `duration_ms`; a real run
left it at 0 until the graph wrote 1 at the end. Scoring is ~98% of a real run's wall clock and
had no representation at all.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25 09:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("phase", sa.String(length=16), nullable=False, server_default="searching"))
        batch_op.add_column(sa.Column("done", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("total", sa.Integer(), nullable=False, server_default="0"))
    # A run that already finished is not "searching". The UI only reads `phase` while a run is
    # running, so this is tidiness rather than correctness — but a stale value in a column is
    # the kind of thing someone later reads as fact.
    op.execute("update intake_runs set phase = 'done' where status not in ('queued', 'running')")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        batch_op.drop_column("total")
        batch_op.drop_column("done")
        batch_op.drop_column("phase")
