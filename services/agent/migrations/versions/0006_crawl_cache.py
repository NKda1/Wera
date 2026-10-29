"""crawl cache

Adds the table behind SRCH-10 — a short-term record of what the last crawl paid Firecrawl for —
and the `credits_saved` column that lets a run say how much of it was answered for free.

Nothing in `crawl_cache` is a source of truth: every row has an expiry, and dropping the table
costs credits rather than data. It is a table rather than a `kv` key because the payloads are
whole job descriptions and the lookups are per URL.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-26 23:18:24.436245

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "crawl_cache",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("stored_at", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    with op.batch_alter_table("crawl_cache", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_crawl_cache_expires_at"), ["expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_crawl_cache_kind"), ["kind"], unique=False)

    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        # `server_default` so the column can be added NOT NULL to a table that already has rows.
        # It stays out of the model deliberately: `compare_metadata` runs with
        # `compare_server_default=False`, which is what 0005 relies on for the same trick. Every
        # historical run saved nothing, which is true rather than merely convenient.
        batch_op.add_column(sa.Column("credits_saved", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("intake_runs", schema=None) as batch_op:
        batch_op.drop_column("credits_saved")

    with op.batch_alter_table("crawl_cache", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_crawl_cache_kind"))
        batch_op.drop_index(batch_op.f("ix_crawl_cache_expires_at"))

    op.drop_table("crawl_cache")
