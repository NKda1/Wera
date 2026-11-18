"""run log

What a run has done so far, step by step, and how far through its current step it is (UX-01,
17 Nov).

A run's bar had no number on it and its one line of detail said "the job APIs" or "tier 1 · 15
searches" for a minute at a time, so a person watching a search couldn't tell what it was doing or
whether it was stuck. `log` keeps the last steps a run finished, each with what it found and how
long it took; `stage_progress` is how far through the current step it is, 0–1, beside `progress`,
which is the whole run.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-17 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        # `server_default` as in 0008: NOT NULL on a table with rows in it. An earlier run kept no
        # log, which is what an empty list says.
        batch_op.add_column(sa.Column("stage_progress", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(
            sa.Column(
                "log",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        batch_op.drop_column("log")
        batch_op.drop_column("stage_progress")
