"""Add durable retries for delivered shared-chat assistant replies.

Revision ID: 012_shared_context_retry
Revises: 011_pending_confirmations
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "012_shared_context_retry"
down_revision: str | None = "011_pending_confirmations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shared_conversation_message_retries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("household_id", sa.UUID(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("author_name", sa.String(length=100), nullable=False),
        sa.Column("message_type", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(length=100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'delivered')",
            name="ck_shared_conversation_message_retries_status",
        ),
        sa.UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_shared_conversation_message_retries_telegram_identity",
        ),
    )
    op.create_index(
        "ix_shared_conversation_message_retries_household_id",
        "shared_conversation_message_retries",
        ["household_id"],
    )
    op.create_index(
        "ix_shared_conversation_message_retries_telegram_chat_id",
        "shared_conversation_message_retries",
        ["telegram_chat_id"],
    )
    op.create_index(
        "ix_shared_conversation_message_retries_due",
        "shared_conversation_message_retries",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shared_conversation_message_retries_due",
        table_name="shared_conversation_message_retries",
    )
    op.drop_index(
        "ix_shared_conversation_message_retries_telegram_chat_id",
        table_name="shared_conversation_message_retries",
    )
    op.drop_index(
        "ix_shared_conversation_message_retries_household_id",
        table_name="shared_conversation_message_retries",
    )
    op.drop_table("shared_conversation_message_retries")
