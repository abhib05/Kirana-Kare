"""basket last activity + upi link

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("baskets", sa.Column("last_activity_at", sa.DateTime(timezone=True)))
    op.add_column("baskets", sa.Column("upi_link", sa.Text()))


def downgrade() -> None:
    op.drop_column("baskets", "upi_link")
    op.drop_column("baskets", "last_activity_at")
