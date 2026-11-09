"""llm call detail

Records *which fields* a model got wrong when its reply is rejected.

`llm_calls` could already say that a call failed and what code it failed with; it could not say
why. So a task that fails its first attempt every single time — `letter.write` was doing exactly
that, turning every cover letter into two calls and twice the wall clock — looked identical in
the data to one that failed occasionally and at random. The reason went to Logfire and nowhere
else, which means it is gone by the time anyone asks.

PRIV-03 still holds, and this is why the column stores `gateway._problem_fields` rather than the
validation error itself: field paths and rule names only, never a value. "paragraphs.0.source_ids
(not_in_cv)" says everything needed to fix the schema and nothing at all about the person.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-01 00:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.add_column(sa.Column("detail", sa.String(length=300), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.drop_column("detail")
