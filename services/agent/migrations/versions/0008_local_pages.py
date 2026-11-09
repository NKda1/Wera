"""local pages

Two counters that say how a run read its pages: locally, or by paying Firecrawl for it.

SRCH-05 used to mean one thing — a 5-credit Firecrawl JSON scrape per new URL — and page scrapes
were 5 of every 7 credits a run spent. `sources.extract` reads most pages for nothing instead
(JSON-LD, the ATS feeds, then the fast tier), with Firecrawl kept as the last rung.

These exist because that claim needs a number against it. "Most pages" is an assumption until a
run says `18 local, 2 paid`, and a board changing shape shows up here as a drift upwards in
`fallback_pages` — which is worth seeing before the invoice says so.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31 11:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        # `server_default` so the columns can be added NOT NULL to a table with rows in it, and
        # left out of the model for the same reason 0005 and 0006 leave theirs out
        # (`compare_server_default=False`). Every historical run read zero pages locally, which
        # is true rather than merely convenient — the ladder didn't exist.
        batch_op.add_column(sa.Column("local_pages", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("fallback_pages", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        batch_op.drop_column("fallback_pages")
        batch_op.drop_column("local_pages")
