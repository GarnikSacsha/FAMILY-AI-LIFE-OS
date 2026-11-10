"""Add one-shot confirmation state for dangerous chat operations.

Revision ID: 011_pending_confirmations
Revises: 010_google_sheets_reliability
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011_pending_confirmations"
down_revision: str | None = "010_google_sheets_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_confirmations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("initiated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("confirmation_code", sa.String(length=32), nullable=False),
        sa.Column("request_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action_type IN ('finance_log', 'calendar_delete', 'memory_dismiss')",
            name="ck_pending_confirmations_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'executing', 'completed', 'cancelled', 'expired', 'failed')",
            name="ck_pending_confirmations_status",
        ),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initiated_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("confirmation_code", name="uq_pending_confirmations_code"),
        sa.UniqueConstraint(
            "household_id",
            "telegram_chat_id",
            "initiated_by_user_id",
            "request_key",
            name="uq_pending_confirmations_request",
        ),
    )
    op.create_index("ix_pending_confirmations_household_id", "pending_confirmations", ["household_id"])
    op.create_index("ix_pending_confirmations_telegram_chat_id", "pending_confirmations", ["telegram_chat_id"])
    op.create_index(
        "ix_pending_confirmations_lookup",
        "pending_confirmations",
        ["household_id", "telegram_chat_id", "initiated_by_user_id", "status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pending_confirmations_lookup", table_name="pending_confirmations")
    op.drop_index("ix_pending_confirmations_telegram_chat_id", table_name="pending_confirmations")
    op.drop_index("ix_pending_confirmations_household_id", table_name="pending_confirmations")
    op.drop_table("pending_confirmations")
