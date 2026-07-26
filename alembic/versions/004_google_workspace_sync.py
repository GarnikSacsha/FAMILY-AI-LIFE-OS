"""Add reliable Google Sheets synchronization state.

Revision ID: 004_google_workspace_sync
Revises: 003_identity_unique_cleanup
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004_google_workspace_sync"
down_revision: str | None = "003_identity_unique_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "financial_transactions",
        sa.Column("sheets_sync_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "financial_transactions",
        sa.Column("sheets_sync_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "financial_transactions",
        sa.Column("sheets_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "financial_transactions",
        sa.Column("sheets_sync_error", sa.String(length=100), nullable=True),
    )
    # Existing rows may already be present in the manually maintained sheet.
    # Do not replay them automatically and risk duplicate historical entries.
    op.execute(
        sa.text("UPDATE financial_transactions SET sheets_sync_status = 'disabled' WHERE sheets_sync_status IS NULL")
    )
    op.alter_column(
        "financial_transactions",
        "sheets_sync_status",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default="pending",
    )
    op.create_check_constraint(
        "ck_financial_transactions_sheets_sync_status",
        "financial_transactions",
        "sheets_sync_status IN ('pending', 'syncing', 'synced', 'failed', 'disabled')",
    )
    op.create_index(
        "ix_financial_transactions_sheets_sync",
        "financial_transactions",
        ["sheets_sync_status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_financial_transactions_sheets_sync",
        table_name="financial_transactions",
    )
    op.drop_constraint(
        "ck_financial_transactions_sheets_sync_status",
        "financial_transactions",
        type_="check",
    )
    op.drop_column("financial_transactions", "sheets_sync_error")
    op.drop_column("financial_transactions", "sheets_synced_at")
    op.drop_column("financial_transactions", "sheets_sync_attempts")
    op.drop_column("financial_transactions", "sheets_sync_status")
