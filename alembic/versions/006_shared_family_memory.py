"""Add shared family-chat memory, summaries, pending actions, and Sheets receipts.

Revision ID: 006_shared_family_memory
Revises: 005_chat_reminder_delivery
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006_shared_family_memory"
down_revision: str | None = "005_chat_reminder_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "shared_conversation_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("household_id", sa.UUID(), nullable=False),
        sa.Column("author_user_id", sa.UUID()),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("author_name", sa.String(length=100), nullable=False),
        sa.Column("message_type", sa.String(length=20), nullable=False),
        sa.Column(
            "included_in_conversation_summary",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_shared_conversation_messages_role"),
        sa.CheckConstraint(
            "message_type IN ('text', 'voice', 'photo', 'document', 'other')",
            name="ck_shared_conversation_messages_type",
        ),
        sa.UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            "role",
            name="uq_shared_conversation_messages_telegram_identity",
        ),
    )
    op.create_index(
        "ix_shared_conversation_messages_household_id",
        "shared_conversation_messages",
        ["household_id"],
    )
    op.create_index(
        "ix_shared_conversation_messages_author_user_id",
        "shared_conversation_messages",
        ["author_user_id"],
    )
    op.create_index(
        "ix_shared_conversation_messages_telegram_chat_id",
        "shared_conversation_messages",
        ["telegram_chat_id"],
    )
    op.create_index(
        "ix_shared_conversation_messages_summary_queue",
        "shared_conversation_messages",
        ["telegram_chat_id", "included_in_conversation_summary", "created_at"],
    )

    op.create_table(
        "shared_memory_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("household_id", sa.UUID(), nullable=False),
        sa.Column("source_message_id", sa.UUID()),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["shared_conversation_messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('decision', 'action', 'money', 'open_question', 'fact', 'preference', 'suggestion')",
            name="ck_shared_memory_items_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'resolved', 'dismissed')",
            name="ck_shared_memory_items_status",
        ),
        sa.UniqueConstraint(
            "household_id",
            "fingerprint",
            name="uq_shared_memory_items_household_fingerprint",
        ),
    )
    op.create_index("ix_shared_memory_items_household_id", "shared_memory_items", ["household_id"])
    op.create_index(
        "ix_shared_memory_items_active",
        "shared_memory_items",
        ["household_id", "status", "updated_at"],
    )

    op.create_table(
        "shared_conversation_summaries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("household_id", sa.UUID(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("summary_kind", sa.String(length=20), nullable=False),
        sa.Column("period_key", sa.String(length=100), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_last_message_id", sa.UUID()),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("structured_data", sa.JSON(), nullable=False),
        sa.Column("delivery_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(length=100)),
        *_timestamps(),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_last_message_id"],
            ["shared_conversation_messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "summary_kind IN ('conversation', 'daily')",
            name="ck_shared_conversation_summaries_kind",
        ),
        sa.CheckConstraint(
            "delivery_status IN ('pending', 'delivered', 'failed')",
            name="ck_shared_conversation_summaries_delivery_status",
        ),
        sa.UniqueConstraint(
            "household_id",
            "summary_kind",
            "period_key",
            name="uq_shared_conversation_summaries_period",
        ),
    )
    op.create_index(
        "ix_shared_conversation_summaries_household_id",
        "shared_conversation_summaries",
        ["household_id"],
    )
    op.create_index(
        "ix_shared_conversation_summaries_telegram_chat_id",
        "shared_conversation_summaries",
        ["telegram_chat_id"],
    )

    op.create_table(
        "pending_shared_actions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("household_id", sa.UUID(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("initiated_by_user_id", sa.UUID(), nullable=False),
        sa.Column("action_type", sa.String(length=30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initiated_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("action_type IN ('reminder')", name="ck_pending_shared_actions_type"),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'cancelled', 'expired')",
            name="ck_pending_shared_actions_status",
        ),
    )
    op.create_index("ix_pending_shared_actions_household_id", "pending_shared_actions", ["household_id"])
    op.create_index("ix_pending_shared_actions_telegram_chat_id", "pending_shared_actions", ["telegram_chat_id"])
    op.create_index(
        "ix_pending_shared_actions_lookup",
        "pending_shared_actions",
        ["household_id", "telegram_chat_id", "status", "expires_at"],
    )

    op.add_column(
        "financial_transactions",
        sa.Column("sheets_updated_range", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("financial_transactions", "sheets_updated_range")
    op.drop_index("ix_pending_shared_actions_lookup", table_name="pending_shared_actions")
    op.drop_index("ix_pending_shared_actions_telegram_chat_id", table_name="pending_shared_actions")
    op.drop_index("ix_pending_shared_actions_household_id", table_name="pending_shared_actions")
    op.drop_table("pending_shared_actions")
    op.drop_index(
        "ix_shared_conversation_summaries_telegram_chat_id",
        table_name="shared_conversation_summaries",
    )
    op.drop_index(
        "ix_shared_conversation_summaries_household_id",
        table_name="shared_conversation_summaries",
    )
    op.drop_table("shared_conversation_summaries")
    op.drop_index("ix_shared_memory_items_active", table_name="shared_memory_items")
    op.drop_index("ix_shared_memory_items_household_id", table_name="shared_memory_items")
    op.drop_table("shared_memory_items")
    op.drop_index(
        "ix_shared_conversation_messages_summary_queue",
        table_name="shared_conversation_messages",
    )
    op.drop_index(
        "ix_shared_conversation_messages_telegram_chat_id",
        table_name="shared_conversation_messages",
    )
    op.drop_index(
        "ix_shared_conversation_messages_author_user_id",
        table_name="shared_conversation_messages",
    )
    op.drop_index(
        "ix_shared_conversation_messages_household_id",
        table_name="shared_conversation_messages",
    )
    op.drop_table("shared_conversation_messages")
