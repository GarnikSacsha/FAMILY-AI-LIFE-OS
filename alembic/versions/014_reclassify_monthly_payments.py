"""Reclassify explicit apartment and monthly-payment expenses.

Revision ID: 014_monthly_payment_categories
Revises: 013_google_sheets_target
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014_monthly_payment_categories"
down_revision: str | None = "013_google_sheets_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    transactions = sa.table(
        "financial_transactions",
        sa.column("direction", sa.String()),
        sa.column("category", sa.String()),
        sa.column("merchant", sa.String()),
        sa.column("description", sa.String()),
        sa.column("sheets_projection_key", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    normalized_text = sa.func.lower(
        sa.func.concat(
            sa.func.coalesce(transactions.c.merchant, ""),
            " ",
            sa.func.coalesce(transactions.c.description, ""),
        )
    )
    explicit_monthly_payment = sa.or_(
        normalized_text.like("%квартир%"),
        normalized_text.like("%ежемесячн%"),
        normalized_text.like("%щомісячн%"),
        normalized_text.like("%подписк%"),
        normalized_text.like("%підписк%"),
    )
    op.execute(
        transactions.update()
        .where(
            transactions.c.direction == "expense",
            transactions.c.category == "Uncategorized",
            explicit_monthly_payment,
        )
        .values(
            category="Utilities",
            sheets_projection_key=None,
            updated_at=sa.func.now(),
        )
    )


def downgrade() -> None:
    # The previous category cannot be reconstructed safely after deployment.
    pass
