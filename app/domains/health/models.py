import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.documents.models import Document
from app.infrastructure.database.base import Base, TimestampMixin

# Health tables have composite owner FKs to documents. Import the target model
# here so fresh application/worker processes register its table before a flush.
_DOCUMENT_TABLE = Document.__table__


class OuraDailyMetric(Base, TimestampMixin):
    __tablename__ = "oura_daily_metrics"
    __table_args__ = (
        UniqueConstraint("user_id", "metric_date", name="uq_oura_daily_metrics_user_date"),
        CheckConstraint(
            "(readiness_score IS NULL OR readiness_score BETWEEN 0 AND 100) AND "
            "(sleep_score IS NULL OR sleep_score BETWEEN 0 AND 100) AND "
            "(activity_score IS NULL OR activity_score BETWEEN 0 AND 100)",
            name="ck_oura_daily_metrics_scores",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    readiness_score: Mapped[int | None] = mapped_column(Integer)
    sleep_score: Mapped[int | None] = mapped_column(Integer)
    activity_score: Mapped[int | None] = mapped_column(Integer)

    hrv_balance: Mapped[float | None] = mapped_column(Float)
    resting_heart_rate: Mapped[float | None] = mapped_column(Float)
    temperature_deviation: Mapped[float | None] = mapped_column(Float)
    spo2_average: Mapped[float | None] = mapped_column(Float)

    total_calories_burned: Mapped[int | None] = mapped_column(Integer)
    active_calories: Mapped[int | None] = mapped_column(Integer)
    steps: Mapped[int | None] = mapped_column(Integer)

    raw_json: Mapped[dict | None] = mapped_column(JSON)


class HealthProviderConnection(Base, TimestampMixin):
    """A user's identity and durable synchronization state at a health provider.

    ``provider_account_id`` is either null or a one-way, provider-scoped SHA-256
    digest. Raw provider account identifiers must never be persisted here.
    """

    __tablename__ = "health_provider_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_health_provider_connections_user_provider"),
        UniqueConstraint(
            "provider",
            "provider_account_id",
            name="uq_health_provider_connections_provider_account",
        ),
        UniqueConstraint("user_id", "id", name="uq_health_provider_connections_user_id_id"),
        CheckConstraint(
            "status IN ('connected', 'syncing', 'error', 'revoked')",
            name="ck_health_provider_connections_status",
        ),
        CheckConstraint(
            "provider_account_id IS NULL OR length(provider_account_id) = 64",
            name="ck_health_provider_connections_account_hash",
        ),
        Index("ix_health_provider_connections_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_account_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="connected")
    granted_scopes: Mapped[str | None] = mapped_column(String(500))
    sync_cursor: Mapped[str | None] = mapped_column(String(500))
    last_sync_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))


class HealthSourceRecord(Base, TimestampMixin):
    """An encrypted, immutable version of one provider or document source record."""

    __tablename__ = "health_source_records"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "collection",
            "source_record_id",
            "version",
            name="uq_health_source_records_identity_version",
        ),
        UniqueConstraint(
            "user_id",
            "source",
            "collection",
            "source_record_id",
            "payload_sha256",
            name="uq_health_source_records_identity_hash",
        ),
        UniqueConstraint("user_id", "id", name="uq_health_source_records_user_id_id"),
        ForeignKeyConstraint(
            ["user_id", "connection_id"],
            ["health_provider_connections.user_id", "health_provider_connections.id"],
            name="fk_health_source_records_owned_connection",
        ),
        ForeignKeyConstraint(
            ["user_id", "document_id"],
            ["documents.owner_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_health_source_records_owned_document",
        ),
        CheckConstraint("version >= 1", name="ck_health_source_records_version"),
        CheckConstraint("schema_version >= 1", name="ck_health_source_records_schema_version"),
        Index("ix_health_source_records_user_period", "user_id", "period_start", "period_end"),
        Index("ix_health_source_records_connection", "connection_id"),
        Index("ix_health_source_records_document", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[uuid.UUID | None] = mapped_column()
    document_id: Mapped[uuid.UUID | None] = mapped_column()
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    collection: Mapped[str] = mapped_column(String(100), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1")
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class HealthObservation(Base, TimestampMixin):
    """A normalized, queryable health measurement owned by exactly one user."""

    __tablename__ = "health_observations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source_record_id",
            "source",
            "metric_code",
            "observed_at",
            name="uq_health_observations_source_metric_time",
        ),
        UniqueConstraint("user_id", "id", name="uq_health_observations_user_id_id"),
        ForeignKeyConstraint(
            ["user_id", "source_record_id"],
            ["health_source_records.user_id", "health_source_records.id"],
            ondelete="CASCADE",
            name="fk_health_observations_owned_source_record",
        ),
        ForeignKeyConstraint(
            ["user_id", "document_id"],
            ["documents.owner_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_health_observations_owned_document",
        ),
        CheckConstraint(
            "(value_numeric IS NOT NULL AND value_text IS NULL AND value_boolean IS NULL) OR "
            "(value_numeric IS NULL AND value_text IS NOT NULL AND value_boolean IS NULL) OR "
            "(value_numeric IS NULL AND value_text IS NULL AND value_boolean IS NOT NULL)",
            name="ck_health_observations_single_value",
        ),
        CheckConstraint(
            "quality IN ('measured', 'estimated', 'derived', 'self_reported', 'unknown')",
            name="ck_health_observations_quality",
        ),
        Index(
            "ix_health_observations_user_current_metric_time",
            "user_id",
            "is_current",
            "metric_code",
            "observed_at",
        ),
        Index("ix_health_observations_document", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_record_id: Mapped[uuid.UUID | None] = mapped_column()
    document_id: Mapped[uuid.UUID | None] = mapped_column()
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(100), nullable=False)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    value_text: Mapped[str | None] = mapped_column(Text)
    value_boolean: Mapped[bool | None] = mapped_column(Boolean)
    unit: Mapped[str | None] = mapped_column(String(50))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    quality: Mapped[str] = mapped_column(String(20), nullable=False, default="measured")
    reference_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    reference_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    reference_flag: Mapped[str | None] = mapped_column(String(30))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class HealthMetricBaseline(Base, TimestampMixin):
    """A dated personal metric baseline over a bounded rolling window."""

    __tablename__ = "health_metric_baselines"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "metric_code",
            "window_days",
            "as_of_date",
            name="uq_health_metric_baselines_user_metric_window_date",
        ),
        CheckConstraint("window_days BETWEEN 1 AND 365", name="ck_health_metric_baselines_window"),
        CheckConstraint("sample_count >= 0", name="ck_health_metric_baselines_samples"),
        Index("ix_health_metric_baselines_user_date", "user_id", "as_of_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(100), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mean_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    median_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    minimum_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    maximum_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    standard_deviation: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit: Mapped[str | None] = mapped_column(String(50))


class HealthAIInsight(Base, TimestampMixin):
    """A non-clinical AI interpretation with bounded retention and reproducible inputs."""

    __tablename__ = "health_ai_insights"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_health_ai_insights_user_id_id"),
        CheckConstraint("retention_days BETWEEN 1 AND 365", name="ck_health_ai_insights_retention"),
        Index("ix_health_ai_insights_user_generated", "user_id", "generated_at"),
        Index("ix_health_ai_insights_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    insight_type: Mapped[str] = mapped_column(String(50), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    model_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    limitations_json: Mapped[list | dict | None] = mapped_column(JSON)


class HealthAIInsightEvidence(Base):
    """Foreign-key provenance from an AI insight to the exact inputs it used."""

    __tablename__ = "health_ai_insight_evidence"
    __table_args__ = (
        UniqueConstraint(
            "insight_id",
            "user_id",
            "observation_id",
            name="uq_health_ai_insight_evidence_observation",
        ),
        UniqueConstraint(
            "insight_id",
            "user_id",
            "source_record_id",
            name="uq_health_ai_insight_evidence_source",
        ),
        CheckConstraint(
            "(observation_id IS NOT NULL AND source_record_id IS NULL) OR "
            "(observation_id IS NULL AND source_record_id IS NOT NULL)",
            name="ck_health_ai_insight_evidence_one_source",
        ),
        ForeignKeyConstraint(
            ["user_id", "insight_id"],
            ["health_ai_insights.user_id", "health_ai_insights.id"],
            ondelete="CASCADE",
            name="fk_health_ai_insight_evidence_owned_insight",
        ),
        ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["health_observations.user_id", "health_observations.id"],
            ondelete="CASCADE",
            name="fk_health_ai_insight_evidence_owned_observation",
        ),
        ForeignKeyConstraint(
            ["user_id", "source_record_id"],
            ["health_source_records.user_id", "health_source_records.id"],
            ondelete="CASCADE",
            name="fk_health_ai_insight_evidence_owned_source_record",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    insight_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    observation_id: Mapped[uuid.UUID | None] = mapped_column()
    source_record_id: Mapped[uuid.UUID | None] = mapped_column()
    evidence_role: Mapped[str] = mapped_column(String(50), nullable=False, default="input")


class Meal(Base, TimestampMixin):
    __tablename__ = "meals"
    __table_args__ = (
        CheckConstraint("calories_est >= 0", name="ck_meals_calories"),
        CheckConstraint(
            "proteins_g >= 0 AND fats_g >= 0 AND carbs_g >= 0",
            name="ck_meals_macros",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_meals_confidence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    dish_name: Mapped[str] = mapped_column(String(200), nullable=False)
    ingredients_json: Mapped[dict | None] = mapped_column(JSON)

    calories_est: Mapped[int] = mapped_column(Integer, default=0)
    proteins_g: Mapped[float] = mapped_column(Float, default=0.0)
    fats_g: Mapped[float] = mapped_column(Float, default=0.0)
    carbs_g: Mapped[float] = mapped_column(Float, default=0.0)

    image_storage_key: Mapped[str | None] = mapped_column(String(255))
    confidence_score: Mapped[float] = mapped_column(Float, default=0.8)
    coaching_tip: Mapped[str | None] = mapped_column(String(500))
