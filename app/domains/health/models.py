import uuid
from datetime import date, datetime
from typing import Optional
from sqlalchemy import String, Float, Integer, ForeignKey, JSON, Date, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin


class OuraDailyMetric(Base, TimestampMixin):
    __tablename__ = "oura_daily_metrics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    readiness_score: Mapped[Optional[int]] = mapped_column(Integer)
    sleep_score: Mapped[Optional[int]] = mapped_column(Integer)
    activity_score: Mapped[Optional[int]] = mapped_column(Integer)
    
    hrv_balance: Mapped[Optional[float]] = mapped_column(Float)
    resting_heart_rate: Mapped[Optional[float]] = mapped_column(Float)
    temperature_deviation: Mapped[Optional[float]] = mapped_column(Float)
    spo2_average: Mapped[Optional[float]] = mapped_column(Float)
    
    total_calories_burned: Mapped[Optional[int]] = mapped_column(Integer)
    active_calories: Mapped[Optional[int]] = mapped_column(Integer)
    steps: Mapped[Optional[int]] = mapped_column(Integer)

    raw_json: Mapped[Optional[dict]] = mapped_column(JSON)


class Meal(Base, TimestampMixin):
    __tablename__ = "meals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    dish_name: Mapped[str] = mapped_column(String(200), nullable=False)
    ingredients_json: Mapped[Optional[dict]] = mapped_column(JSON)
    
    calories_est: Mapped[int] = mapped_column(Integer, default=0)
    proteins_g: Mapped[float] = mapped_column(Float, default=0.0)
    fats_g: Mapped[float] = mapped_column(Float, default=0.0)
    carbs_g: Mapped[float] = mapped_column(Float, default=0.0)
    
    image_storage_key: Mapped[Optional[str]] = mapped_column(String(255))
    confidence_score: Mapped[float] = mapped_column(Float, default=0.8)
    coaching_tip: Mapped[Optional[str]] = mapped_column(String(500))
