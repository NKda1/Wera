"""job signals

What the jobs a CV nearly matched ask for (UPSK-06, 20 Nov).

Upskills read only scored jobs, so the jobs the title filters dropped — 274 of 606 leads in one
finance search, "Equity Analyst" and "Commercial Finance Analyst" among them — never said what
the person's market asks for. The best of them a search, by CV similarity, now have their keywords
extracted once and kept here, one row per posting, and Upskills counts them at half weight.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_signals",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column("keywords", JSON, nullable=False),
        sa.Column("education", JSON, nullable=True),
        sa.Column("job_hash", sa.String(length=32), nullable=True),
        sa.Column("seen_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    with op.batch_alter_table("job_signals", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_job_signals_job_id"), ["job_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_job_signals_seen_at"), ["seen_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("job_signals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_signals_seen_at"))
        batch_op.drop_index(batch_op.f("ix_job_signals_job_id"))
    op.drop_table("job_signals")
