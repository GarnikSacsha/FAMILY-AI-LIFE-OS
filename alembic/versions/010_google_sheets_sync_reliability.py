"""Add retry lease and safe notification target for Google Sheets projections.

Revision ID: 010_google_sheets_reliability
Revises: 009_calendar_event_pending
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010_google_sheets_reliability"
down_revision: str | None = "009_calendar_event_pending"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("financial_transactions", sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "financial_transactions", sa.Column("sheets_next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "financial_transactions", sa.Column("sheets_sync_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "financial_transactions", sa.Column("sheets_failure_notified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_financial_transactions_sheets_due",
        "financial_transactions",
        ["sheets_sync_status", "sheets_next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_financial_transactions_sheets_due", table_name="financial_transactions")
    op.drop_column("financial_transactions", "sheets_failure_notified_at")
    op.drop_column("financial_transactions", "sheets_sync_started_at")
    op.drop_column("financial_transactions", "sheets_next_attempt_at")
    op.drop_column("financial_transactions", "telegram_chat_id")
