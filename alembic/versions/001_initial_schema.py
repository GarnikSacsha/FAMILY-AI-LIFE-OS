"""001_initial_schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-07-26 02:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Households
    op.create_table(
        "households",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # Users
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("household_id", sa.UUID(), nullable=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("timezone", sa.String(length=50), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)

    # OAuth Authorization States
    op.create_table(
        "oauth_authorization_states",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("code_verifier_encrypted", sa.String(length=500), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )
    op.create_index(
        op.f("ix_oauth_authorization_states_state_hash"), "oauth_authorization_states", ["state_hash"], unique=True
    )

    # OAuth Tokens
    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("access_token_encrypted", sa.String(length=500), nullable=False),
        sa.Column("refresh_token_encrypted", sa.String(length=500), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Meals
    op.create_table(
        "meals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dish_name", sa.String(length=200), nullable=False),
        sa.Column("ingredients_json", sa.JSON(), nullable=True),
        sa.Column("calories_est", sa.Integer(), nullable=False),
        sa.Column("proteins_g", sa.Float(), nullable=False),
        sa.Column("fats_g", sa.Float(), nullable=False),
        sa.Column("carbs_g", sa.Float(), nullable=False),
        sa.Column("image_storage_key", sa.String(length=255), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("coaching_tip", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Financial Transactions
    op.create_table(
        "financial_transactions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_type", sa.String(length=20), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("merchant", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("subcategory", sa.String(length=100), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("receipt_storage_key", sa.String(length=255), nullable=True),
        sa.Column("raw_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("financial_transactions")
    op.drop_table("meals")
    op.drop_table("oauth_tokens")
    op.drop_table("oauth_authorization_states")
    op.drop_table("users")
    op.drop_table("households")
