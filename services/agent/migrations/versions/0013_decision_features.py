"""decision features

What WERA needs to learn from a Gate 1 decision (MATCH-11, 15 Nov). A rejection was saved as a
job id and a free-text note, and nothing read it back: the reason was whichever chip or sentence
the dialog sent, and what the job *was* — its title, its scores, where it was — lived only on a
job row that the next CV upload deletes. Each decision now carries its reason as a code, the CV
version it was made against, the job's fit total, and a snapshot of the job's features, so it can
be learned from after the job itself is gone. The two reasons recorded before this are coded from
their notes.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-15 02:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRESETS = {
    "Wrong level": "wrong_level",
    "Wrong location": "wrong_location",
    "Not my field": "not_my_field",
    "Pay too low": "pay_too_low",
    "Already applied elsewhere": "applied_elsewhere",
}


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("decisions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reason_code", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("cv_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("fit_total", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("features", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True))
    decisions = sa.table("decisions", sa.column("note", sa.Text), sa.column("reason_code", sa.String))
    for note, code in PRESETS.items():
        op.execute(decisions.update().where(decisions.c.note == note).values(reason_code=code, note=None))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("decisions", schema=None) as batch_op:
        batch_op.drop_column("features")
        batch_op.drop_column("fit_total")
        batch_op.drop_column("cv_version")
        batch_op.drop_column("reason_code")
