"""Complete schema and add security/data-integrity constraints.

Revision ID: 002_security_hardening
Revises: 001_initial_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002_security_hardening"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    op.add_column(
        "households",
        sa.Column("timezone", sa.String(length=50), server_default="Europe/Kyiv", nullable=False),
    )

    op.create_unique_constraint("uq_oauth_tokens_user_provider", "oauth_tokens", ["user_id", "provider"])
    op.create_index("ix_oauth_tokens_user_id", "oauth_tokens", ["user_id"])
    op.alter_column(
        "oauth_tokens",
        "access_token_encrypted",
        existing_type=sa.String(length=500),
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.alter_column(
        "oauth_tokens",
        "refresh_token_encrypted",
        existing_type=sa.String(length=500),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.create_index(
        "ix_oauth_authorization_states_expiry",
        "oauth_authorization_states",
        ["expires_at", "consumed_at"],
    )

    op.create_index("ix_meals_user_id", "meals", ["user_id"])
    op.create_index("ix_meals_consumed_at", "meals", ["consumed_at"])
    op.create_check_constraint("ck_meals_calories", "meals", "calories_est >= 0")
    op.create_check_constraint(
        "ck_meals_macros",
        "meals",
        "proteins_g >= 0 AND fats_g >= 0 AND carbs_g >= 0",
    )
    op.create_check_constraint(
        "ck_meals_confidence",
        "meals",
        "confidence_score >= 0 AND confidence_score <= 1",
    )

    op.add_column(
        "financial_transactions",
        sa.Column("account_id", sa.String(length=255), server_default="default", nullable=False),
    )
    op.alter_column(
        "financial_transactions",
        "currency",
        existing_type=sa.String(length=10),
        type_=sa.String(length=3),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_financial_transactions_owner_type",
        "financial_transactions",
        "owner_type IN ('user', 'household')",
    )
    op.create_check_constraint(
        "ck_financial_transactions_amount_positive",
        "financial_transactions",
        "amount > 0",
    )
    op.create_check_constraint(
        "ck_financial_transactions_direction",
        "financial_transactions",
        "direction IN ('expense', 'income')",
    )
    op.create_check_constraint(
        "ck_financial_transactions_currency",
        "financial_transactions",
        "length(currency) = 3 AND currency = upper(currency)",
    )
    op.create_check_constraint(
        "ck_financial_transactions_confidence",
        "financial_transactions",
        "confidence >= 0 AND confidence <= 1",
    )
    op.create_index(
        "ix_financial_transactions_owner_occurred",
        "financial_transactions",
        ["owner_id", "occurred_at"],
    )
    op.create_index(
        "uq_financial_transactions_import_identity",
        "financial_transactions",
        ["source", "account_id", "owner_type", "owner_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    op.create_table(
        "oura_daily_metrics",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("readiness_score", sa.Integer()),
        sa.Column("sleep_score", sa.Integer()),
        sa.Column("activity_score", sa.Integer()),
        sa.Column("hrv_balance", sa.Float()),
        sa.Column("resting_heart_rate", sa.Float()),
        sa.Column("temperature_deviation", sa.Float()),
        sa.Column("spo2_average", sa.Float()),
        sa.Column("total_calories_burned", sa.Integer()),
        sa.Column("active_calories", sa.Integer()),
        sa.Column("steps", sa.Integer()),
        sa.Column("raw_json", sa.JSON()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "metric_date", name="uq_oura_daily_metrics_user_date"),
        sa.CheckConstraint(
            "(readiness_score IS NULL OR readiness_score BETWEEN 0 AND 100) AND "
            "(sleep_score IS NULL OR sleep_score BETWEEN 0 AND 100) AND "
            "(activity_score IS NULL OR activity_score BETWEEN 0 AND 100)",
            name="ck_oura_daily_metrics_scores",
        ),
    )
    op.create_index("ix_oura_daily_metrics_user_id", "oura_daily_metrics", ["user_id"])
    op.create_index("ix_oura_daily_metrics_metric_date", "oura_daily_metrics", ["metric_date"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_type", sa.String(length=20), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("creator_id", sa.UUID(), nullable=False),
        sa.Column("assignee_id", sa.UUID()),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1000)),
        sa.Column("is_completed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("due_date", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("owner_type IN ('user', 'household')", name="ck_tasks_owner_type"),
    )
    op.create_index("ix_tasks_owner_id", "tasks", ["owner_id"])

    op.create_table(
        "shopping_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("household_id", sa.UUID(), nullable=False),
        sa.Column("added_by_id", sa.UUID(), nullable=False),
        sa.Column("item_name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.String(length=50)),
        sa.Column("is_purchased", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"]),
        sa.ForeignKeyConstraint(["added_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shopping_items_household_id", "shopping_items", ["household_id"])

    op.create_table(
        "reminders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("recipient_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("trigger_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_triggered", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("quiet_hours_override", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["recipient_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reminders_recipient_id", "reminders", ["recipient_id"])
    op.create_index("ix_reminders_trigger_at", "reminders", ["trigger_at"])

    op.create_table(
        "memory_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("fact_text", sa.Text(), nullable=False),
        sa.Column("source_message", sa.Text()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_items_user_id", "memory_items", ["user_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("doc_type", sa.String(length=50), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("checksum", sa.String(length=64)),
        sa.Column("extracted_facts", sa.JSON()),
        sa.Column("processing_status", sa.String(length=30), server_default="PROCESSED", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID()),
        sa.Column("chat_type", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_domain", sa.String(length=50), nullable=False),
        sa.Column("target_entity_id", sa.UUID()),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "chat_type IN ('private', 'group', 'supergroup', 'system')",
            name="ck_audit_logs_chat_type",
        ),
        sa.CheckConstraint("result IN ('success', 'denied', 'error')", name="ck_audit_logs_result"),
    )
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_logs_actor_created", "audit_logs", ["actor_user_id", "created_at"])
    op.create_index("ix_audit_logs_domain_created", "audit_logs", ["target_domain", "created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("documents")
    op.drop_table("memory_items")
    op.drop_table("reminders")
    op.drop_table("shopping_items")
    op.drop_table("tasks")
    op.drop_table("oura_daily_metrics")

    op.drop_index("uq_financial_transactions_import_identity", table_name="financial_transactions")
    op.drop_index("ix_financial_transactions_owner_occurred", table_name="financial_transactions")
    op.drop_constraint("ck_financial_transactions_confidence", "financial_transactions", type_="check")
    op.drop_constraint("ck_financial_transactions_currency", "financial_transactions", type_="check")
    op.drop_constraint("ck_financial_transactions_direction", "financial_transactions", type_="check")
    op.drop_constraint("ck_financial_transactions_amount_positive", "financial_transactions", type_="check")
    op.drop_constraint("ck_financial_transactions_owner_type", "financial_transactions", type_="check")
    op.alter_column(
        "financial_transactions",
        "currency",
        existing_type=sa.String(length=3),
        type_=sa.String(length=10),
        existing_nullable=False,
    )
    op.drop_column("financial_transactions", "account_id")

    op.drop_constraint("ck_meals_confidence", "meals", type_="check")
    op.drop_constraint("ck_meals_macros", "meals", type_="check")
    op.drop_constraint("ck_meals_calories", "meals", type_="check")
    op.drop_index("ix_meals_consumed_at", table_name="meals")
    op.drop_index("ix_meals_user_id", table_name="meals")
    op.drop_index("ix_oauth_authorization_states_expiry", table_name="oauth_authorization_states")
    op.alter_column(
        "oauth_tokens",
        "refresh_token_encrypted",
        existing_type=sa.Text(),
        type_=sa.String(length=500),
        existing_nullable=True,
    )
    op.alter_column(
        "oauth_tokens",
        "access_token_encrypted",
        existing_type=sa.Text(),
        type_=sa.String(length=500),
        existing_nullable=False,
    )
    op.drop_index("ix_oauth_tokens_user_id", table_name="oauth_tokens")
    op.drop_constraint("uq_oauth_tokens_user_provider", "oauth_tokens", type_="unique")
    op.drop_column("households", "timezone")
