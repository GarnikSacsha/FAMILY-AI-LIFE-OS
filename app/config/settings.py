import os
from typing import Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App General Settings
    APP_NAME: str = "Family AI Life OS"
    ENVIRONMENT: str = "production"
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "change-me-in-production-super-secret-key-32bytes"

    # Database & Cache
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/family_life_os"
    REDIS_URL: str = "redis://localhost:6379/0"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: Optional[str]) -> str:
        if not v:
            return "postgresql+asyncpg://postgres:postgres@localhost:5432/family_life_os"
        # Convert postgres:// or postgresql:// to postgresql+asyncpg:// for async SQLAlchemy
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
    DENYS_TELEGRAM_ID: int = 123456789
    OLEKSANDRA_TELEGRAM_ID: int = 987654321
    FAMILY_GROUP_CHAT_ID: Optional[int] = None

    # AI & LLM Providers
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    
    # Model Assignments
    OPENAI_FAST_MODEL: str = "gpt-5.6-terra"
    OPENAI_REASONING_MODEL: str = "gpt-5.6-terra"
    TERRA_MODEL_NAME: str = "gpt-5.6-terra"
    
    GEMINI_VISION_MODEL: str = "gemini-2.5-flash"
    GEMINI_FINANCE_MODEL: str = "gemini-2.5-flash"

    # Oura Ring OAuth2
    OURA_CLIENT_ID: Optional[str] = None
    OURA_CLIENT_SECRET: Optional[str] = None
    OURA_REDIRECT_URI: str = "http://localhost:8080/oura/callback"

    # Google Sheets Integration
    GOOGLE_SHEETS_SPREADSHEET_ID: Optional[str] = None
    GOOGLE_CREDENTIALS_JSON_PATH: Optional[str] = None

    # S3 Object Storage
    S3_ENDPOINT_URL: Optional[str] = None
    S3_ACCESS_KEY: Optional[str] = None
    S3_SECRET_KEY: Optional[str] = None
    S3_BUCKET_NAME: str = "family-life-os-docs"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
