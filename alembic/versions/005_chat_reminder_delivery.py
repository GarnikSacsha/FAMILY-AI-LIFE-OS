"""Add reliable in-chat reminder delivery state.

Revision ID: 005_chat_reminder_delivery
Revises: 004_google_workspace_sync
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005_chat_reminder_delivery"
down_revision: str | None = "004_google_workspace_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reminders", sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "reminders",
        sa.Column("delivery_status", sa.String(length=20), server_default="pending", nullable=False),
    )
    op.add_column(
        "reminders",
        sa.Column("delivery_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("reminders", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reminders", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reminders", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reminders", sa.Column("last_error", sa.String(length=100), nullable=True))
    op.execute(
        sa.text(
            "UPDATE reminders SET delivery_status = 'delivered', delivered_at = updated_at WHERE is_triggered = true"
        )
    )
    op.create_check_constraint(
        "ck_reminders_delivery_status",
        "reminders",
        "delivery_status IN ('pending', 'sending', 'failed', 'delivered')",
    )
    op.create_index("ix_reminders_telegram_chat_id", "reminders", ["telegram_chat_id"])
    op.create_index(
        "ix_reminders_delivery_queue",
        "reminders",
        ["delivery_status", "trigger_at", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reminders_delivery_queue", table_name="reminders")
    op.drop_index("ix_reminders_telegram_chat_id", table_name="reminders")
    op.drop_constraint("ck_reminders_delivery_status", "reminders", type_="check")
    op.drop_column("reminders", "last_error")
    op.drop_column("reminders", "delivered_at")
    op.drop_column("reminders", "claimed_at")
    op.drop_column("reminders", "next_attempt_at")
    op.drop_column("reminders", "delivery_attempts")
    op.drop_column("reminders", "delivery_status")
    op.drop_column("reminders", "telegram_chat_id")
