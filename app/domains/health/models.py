import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin


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
