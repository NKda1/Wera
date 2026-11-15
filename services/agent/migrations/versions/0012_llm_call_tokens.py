"""llm call tokens

What part of a call's tokens were a provider's prompt cache, and what part was thinking (13 Nov
plan, A.5). Both were invisible: `input_tokens` and `output_tokens` say how much, not what kind.
Thinking was most of `fit.score`'s 1,480 output tokens and bought no agreement on the golden
set; a cached prefix is billed at a fraction of the price. Neither can be tuned without being
counted, so each is now on the row. Null on rows from before, which is "not measured", not 0.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-14 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cached_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("reasoning_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.drop_column("reasoning_tokens")
        batch_op.drop_column("cached_tokens")
