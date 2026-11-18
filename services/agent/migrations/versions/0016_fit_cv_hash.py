"""fit cv hash

What the scorer read of the CV, stored with each score (MATCH-04, CV-08; 18 Nov).

On 18 Nov a skill-level edit on the master CV made every stored score stale, because staleness
was keyed on the CV *version* and any save bumps it — although the scorer never read skill levels
at all. A score is now stale when `fit.scoring_fingerprint` changes: a hash of exactly what the
fit prompt is given. Scores written before this have no hash and keep the version rule.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-18 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("fit_reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cv_hash", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("fit_reports", schema=None) as batch_op:
        batch_op.drop_column("cv_hash")
