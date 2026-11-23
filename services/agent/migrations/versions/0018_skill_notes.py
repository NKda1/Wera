"""skill notes

What the Upskills analyst said about each gap term, per CV version (UPSK-08, 22 Nov).

Upskills is arithmetic over the posts' keywords, so it can count a gap but not explain it, and a
spelling the vocabulary misses ("financial models" beside "financial modelling") is two gaps. One
fast-tier call a search labels the terms not yet labelled for the CV version: a kind, a merge,
one line of what would close the gap. Kept here so no term is sent twice; the counts and weights
stay in code.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-22 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "skill_notes",
        sa.Column("term", sa.String(length=200), nullable=False),
        sa.Column("cv_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("same_as", sa.String(length=200), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("family_note", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("term", "cv_version"),
    )
    with op.batch_alter_table("skill_notes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_skill_notes_created_at"), ["created_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("skill_notes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_skill_notes_created_at"))
    op.drop_table("skill_notes")
