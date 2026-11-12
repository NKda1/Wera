"""run queued

How many postings a board search found and left in the queue (SRCH-26).

A search stops reading once twelve jobs are scored, so most of what discovery finds is not read
on the run that found it — it waits in Redis for "Find more". Without this count a run that
queued thirty postings and one that found nothing would both read "12 new jobs", and the button
that serves the rest would be a promise nobody could check.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-11 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        # `server_default` for the same reason as 0008: NOT NULL on a table with rows in it. Every
        # earlier run queued nothing, which is true — there was no queue.
        batch_op.add_column(sa.Column("queued", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        batch_op.drop_column("queued")
