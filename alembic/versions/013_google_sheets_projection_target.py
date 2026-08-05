"""Track the Google Sheets target that received each finance projection.

Revision ID: 013_google_sheets_target
Revises: 012_shared_context_retry
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "013_google_sheets_target"
down_revision: str | None = "012_shared_context_retry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "financial_transactions",
        sa.Column("sheets_projection_key", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("financial_transactions", "sheets_projection_key")
