"""Add user-isolated health history, baselines, and AI provenance.

Revision ID: 015_health_data_foundation
Revises: 014_monthly_payment_categories
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "015_health_data_foundation"
down_revision: str | None = "014_monthly_payment_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_pending_confirmations_type", "pending_confirmations", type_="check")
    op.create_check_constraint(
        "ck_pending_confirmations_type",
        "pending_confirmations",
        "action_type IN ('finance_log', 'calendar_delete', 'memory_dismiss', 'health_history_delete')",
    )
    op.create_unique_constraint("uq_documents_owner_id_id", "documents", ["owner_id", "id"])

    op.create_table(
        "health_provider_connections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_account_id", sa.String(length=64)),
        sa.Column("status", sa.String(length=20), server_default="connected", nullable=False),
        sa.Column("granted_scopes", sa.String(length=500)),
        sa.Column("sync_cursor", sa.String(length=500)),
        sa.Column("last_sync_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_succeeded_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('connected', 'syncing', 'error', 'revoked')",
            name="ck_health_provider_connections_status",
        ),
        sa.CheckConstraint(
            "provider_account_id IS NULL OR length(provider_account_id) = 64",
            name="ck_health_provider_connections_account_hash",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_health_provider_connections_user_provider"),
        sa.UniqueConstraint(
            "provider",
            "provider_account_id",
            name="uq_health_provider_connections_provider_account",
        ),
        sa.UniqueConstraint("user_id", "id", name="uq_health_provider_connections_user_id_id"),
    )
    op.create_index(
        "ix_health_provider_connections_user_status",
        "health_provider_connections",
        ["user_id", "status"],
    )

    op.create_table(
        "health_source_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID()),
        sa.Column("document_id", sa.UUID()),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("collection", sa.String(length=100), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("encryption_version", sa.String(length=20), server_default="v1", nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_health_source_records_version"),
        sa.CheckConstraint("schema_version >= 1", name="ck_health_source_records_schema_version"),
        sa.ForeignKeyConstraint(
            ["user_id", "connection_id"],
            ["health_provider_connections.user_id", "health_provider_connections.id"],
            name="fk_health_source_records_owned_connection",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "document_id"],
            ["documents.owner_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_health_source_records_owned_document",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "source",
            "collection",
            "source_record_id",
            "version",
            name="uq_health_source_records_identity_version",
        ),
        sa.UniqueConstraint(
            "user_id",
            "source",
            "collection",
            "source_record_id",
            "payload_sha256",
            name="uq_health_source_records_identity_hash",
        ),
        sa.UniqueConstraint("user_id", "id", name="uq_health_source_records_user_id_id"),
    )
    op.create_index(
        "ix_health_source_records_user_period",
        "health_source_records",
        ["user_id", "period_start", "period_end"],
    )
    op.create_index("ix_health_source_records_connection", "health_source_records", ["connection_id"])
    op.create_index("ix_health_source_records_document", "health_source_records", ["document_id"])

    op.create_table(
        "health_observations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("source_record_id", sa.UUID()),
        sa.Column("document_id", sa.UUID()),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("metric_code", sa.String(length=100), nullable=False),
        sa.Column("value_numeric", sa.Numeric(precision=18, scale=6)),
        sa.Column("value_text", sa.Text()),
        sa.Column("value_boolean", sa.Boolean()),
        sa.Column("unit", sa.String(length=50)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("quality", sa.String(length=20), server_default="measured", nullable=False),
        sa.Column("reference_low", sa.Numeric(precision=18, scale=6)),
        sa.Column("reference_high", sa.Numeric(precision=18, scale=6)),
        sa.Column("reference_flag", sa.String(length=30)),
        sa.Column("metadata_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "(value_numeric IS NOT NULL AND value_text IS NULL AND value_boolean IS NULL) OR "
            "(value_numeric IS NULL AND value_text IS NOT NULL AND value_boolean IS NULL) OR "
            "(value_numeric IS NULL AND value_text IS NULL AND value_boolean IS NOT NULL)",
            name="ck_health_observations_single_value",
        ),
        sa.CheckConstraint(
            "quality IN ('measured', 'estimated', 'derived', 'self_reported', 'unknown')",
            name="ck_health_observations_quality",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "document_id"],
            ["documents.owner_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_health_observations_owned_document",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "source_record_id"],
            ["health_source_records.user_id", "health_source_records.id"],
            ondelete="CASCADE",
            name="fk_health_observations_owned_source_record",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "source_record_id",
            "source",
            "metric_code",
            "observed_at",
            name="uq_health_observations_source_metric_time",
        ),
        sa.UniqueConstraint("user_id", "id", name="uq_health_observations_user_id_id"),
    )
    op.create_index(
        "ix_health_observations_user_current_metric_time",
        "health_observations",
        ["user_id", "is_current", "metric_code", "observed_at"],
    )
    op.create_index("ix_health_observations_document", "health_observations", ["document_id"])

    op.create_table(
        "health_metric_baselines",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("metric_code", sa.String(length=100), nullable=False),
        sa.Column("window_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("mean_value", sa.Numeric(precision=18, scale=6)),
        sa.Column("median_value", sa.Numeric(precision=18, scale=6)),
        sa.Column("minimum_value", sa.Numeric(precision=18, scale=6)),
        sa.Column("maximum_value", sa.Numeric(precision=18, scale=6)),
        sa.Column("standard_deviation", sa.Numeric(precision=18, scale=6)),
        sa.Column("unit", sa.String(length=50)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("window_days BETWEEN 1 AND 365", name="ck_health_metric_baselines_window"),
        sa.CheckConstraint("sample_count >= 0", name="ck_health_metric_baselines_samples"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "metric_code",
            "window_days",
            "as_of_date",
            name="uq_health_metric_baselines_user_metric_window_date",
        ),
    )
    op.create_index(
        "ix_health_metric_baselines_user_date",
        "health_metric_baselines",
        ["user_id", "as_of_date"],
    )

    op.create_table(
        "health_ai_insights",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("insight_type", sa.String(length=50), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("model_provider", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_days", sa.Integer(), server_default="90", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("limitations_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("retention_days BETWEEN 1 AND 365", name="ck_health_ai_insights_retention"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "id", name="uq_health_ai_insights_user_id_id"),
    )
    op.create_index(
        "ix_health_ai_insights_user_generated",
        "health_ai_insights",
        ["user_id", "generated_at"],
    )
    op.create_index("ix_health_ai_insights_expiry", "health_ai_insights", ["expires_at"])

    op.create_table(
        "health_ai_insight_evidence",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("insight_id", sa.UUID(), nullable=False),
        sa.Column("observation_id", sa.UUID()),
        sa.Column("source_record_id", sa.UUID()),
        sa.Column("evidence_role", sa.String(length=50), server_default="input", nullable=False),
        sa.CheckConstraint(
            "(observation_id IS NOT NULL AND source_record_id IS NULL) OR "
            "(observation_id IS NULL AND source_record_id IS NOT NULL)",
            name="ck_health_ai_insight_evidence_one_source",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "insight_id"],
            ["health_ai_insights.user_id", "health_ai_insights.id"],
            ondelete="CASCADE",
            name="fk_health_ai_insight_evidence_owned_insight",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["health_observations.user_id", "health_observations.id"],
            ondelete="CASCADE",
            name="fk_health_ai_insight_evidence_owned_observation",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "source_record_id"],
            ["health_source_records.user_id", "health_source_records.id"],
            ondelete="CASCADE",
            name="fk_health_ai_insight_evidence_owned_source_record",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "insight_id",
            "user_id",
            "observation_id",
            name="uq_health_ai_insight_evidence_observation",
        ),
        sa.UniqueConstraint(
            "insight_id",
            "user_id",
            "source_record_id",
            name="uq_health_ai_insight_evidence_source",
        ),
    )
    op.create_index(
        "ix_health_ai_insight_evidence_insight_id",
        "health_ai_insight_evidence",
        ["insight_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_health_ai_insight_evidence_insight_id",
        table_name="health_ai_insight_evidence",
    )
    op.drop_table("health_ai_insight_evidence")
    op.drop_index("ix_health_ai_insights_expiry", table_name="health_ai_insights")
    op.drop_index("ix_health_ai_insights_user_generated", table_name="health_ai_insights")
    op.drop_table("health_ai_insights")
    op.drop_index("ix_health_metric_baselines_user_date", table_name="health_metric_baselines")
    op.drop_table("health_metric_baselines")
    op.drop_index("ix_health_observations_document", table_name="health_observations")
    op.drop_index("ix_health_observations_user_current_metric_time", table_name="health_observations")
    op.drop_table("health_observations")
    op.drop_index("ix_health_source_records_document", table_name="health_source_records")
    op.drop_index("ix_health_source_records_connection", table_name="health_source_records")
    op.drop_index("ix_health_source_records_user_period", table_name="health_source_records")
    op.drop_table("health_source_records")
    op.drop_index("ix_health_provider_connections_user_status", table_name="health_provider_connections")
    op.drop_table("health_provider_connections")
    op.drop_constraint("uq_documents_owner_id_id", "documents", type_="unique")
    op.drop_constraint("ck_pending_confirmations_type", "pending_confirmations", type_="check")
    op.create_check_constraint(
        "ck_pending_confirmations_type",
        "pending_confirmations",
        "action_type IN ('finance_log', 'calendar_delete', 'memory_dismiss')",
    )
