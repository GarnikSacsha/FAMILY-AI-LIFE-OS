"""Dismiss legacy unverified finance and malformed generated memory.

Revision ID: 007_clean_legacy_shared_memory
Revises: 006_shared_family_memory
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007_clean_legacy_shared_memory"
down_revision: str | None = "006_shared_family_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # These rows were generated from chat text before money sections were
    # grounded in financial_transactions. Raw chat and real transactions are
    # intentionally preserved.
    op.execute(
        sa.text(
            """
            UPDATE shared_memory_items
            SET status = 'dismissed'
            WHERE status = 'active'
              AND (
                kind IN ('money', 'suggestion')
                OR lower(content) LIKE '%[family]%'
                OR lower(content) LIKE '% said:%'
                OR lower(content) LIKE '%пользователь сообщил%'
                OR lower(content) LIKE '%текущий вывод%'
                OR lower(content) LIKE '%высшая математика%'
                OR lower(content) LIKE '%мусорн%'
                OR lower(content) LIKE '%выжимк%'
                OR (
                    kind IN ('fact', 'decision')
                    AND lower(content) LIKE '%грн%'
                )
              )
            """
        )
    )


def downgrade() -> None:
    # Dismissal is intentionally not reversed: provenance does not distinguish
    # legacy rows from later user-dismissed rows.
    pass
