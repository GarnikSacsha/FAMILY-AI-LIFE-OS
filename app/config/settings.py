import base64
import binascii
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    # App General Settings
    APP_NAME: str = "Family AI Life OS"
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "development-only-change-me"  # noqa: S105 - rejected in production
    # The container listener is not an authorization boundary.
    HTTP_HOST: str = "0.0.0.0"  # noqa: S104  # nosec B104
    HTTP_PORT: int = Field(
        default=8000,
        validation_alias=AliasChoices("HTTP_PORT", "PORT"),
    )

    # Database & Cache
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/family_life_os"
    REDIS_URL: str = "redis://localhost:6379/0"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: str | None) -> str:
        if not v:
            return "postgresql+asyncpg://postgres:postgres@localhost:5432/family_life_os"
        value = v.strip()
        # Convert postgres:// or postgresql:// to postgresql+asyncpg:// for async SQLAlchemy
        if value.startswith("postgres://"):
            value = value.replace("postgres://", "postgresql+asyncpg://", 1)
        elif value.startswith("postgresql://") and not value.startswith("postgresql+asyncpg://"):
            value = value.replace("postgresql://", "postgresql+asyncpg://", 1)

        try:
            parsed = make_url(value)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL must be a valid database URL.") from exc

        if value.count("://") != 1 or (parsed.database and "://" in parsed.database):
            raise ValueError("DATABASE_URL contains concatenated connection strings.")
        if parsed.drivername.startswith("postgresql") and (not parsed.host or not parsed.database):
            raise ValueError("DATABASE_URL must include a PostgreSQL host and database name.")
        return value

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"  # noqa: S105 - rejected in production
    DENYS_TELEGRAM_ID: int = 123456789
    OLEKSANDRA_TELEGRAM_ID: int = 987654321
    FAMILY_GROUP_CHAT_ID: int | None = None

    # AI & LLM Providers
    OPENAI_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None

    # Model Assignments
    OPENAI_FAST_MODEL: str = "gpt-5.6-terra"
    OPENAI_REASONING_MODEL: str = "gpt-5.6-terra"
    TERRA_MODEL_NAME: str = "gpt-5.6-terra"

    GEMINI_VISION_MODEL: str = "gemini-2.5-flash"
    GEMINI_FINANCE_MODEL: str = "gemini-2.5-flash"

    # Oura Ring OAuth2
    OURA_CLIENT_ID: str | None = None
    OURA_CLIENT_SECRET: SecretStr | None = None
    OURA_REDIRECT_URI: str = "http://localhost:8000/oauth/oura/callback"
    OURA_SCOPES: str = "daily heartrate spo2"
    TOKEN_ENCRYPTION_KEY: SecretStr | None = None

    # Google Sheets Integration
    GOOGLE_SHEETS_SPREADSHEET_ID: str | None = None
    GOOGLE_CREDENTIALS_JSON_PATH: str | None = None
    GOOGLE_CREDENTIALS_JSON: SecretStr | None = None

    # S3 Object Storage
    S3_ENDPOINT_URL: str | None = None
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_BUCKET_NAME: str = "family-life-os-docs"

    @field_validator("TOKEN_ENCRYPTION_KEY")
    @classmethod
    def validate_token_encryption_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None

        encoded_key = value.get_secret_value()
        try:
            raw_key = base64.b64decode(encoded_key, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("TOKEN_ENCRYPTION_KEY must be valid Base64.") from exc

        if len(raw_key) != 32:
            raise ValueError("TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes.")
        return value

    @field_validator("OURA_SCOPES")
    @classmethod
    def validate_oura_scopes(cls, value: str) -> str:
        allowed_scopes = {
            "email",
            "personal",
            "daily",
            "heartrate",
            "tag",
            "workout",
            "session",
            "spo2",
        }
        scopes = value.split()
        if not scopes or len(scopes) != len(set(scopes)):
            raise ValueError("OURA_SCOPES must contain unique supported scopes.")
        unsupported = set(scopes) - allowed_scopes
        if unsupported:
            raise ValueError("OURA_SCOPES contains unsupported scopes.")
        return " ".join(scopes)

    @model_validator(mode="after")
    def validate_production_configuration(self) -> "Settings":
        if self.ENVIRONMENT != "production":
            return self

        placeholder_values = {
            "",
            "change-me-in-production-super-secret-key-32bytes",
            "development-only-change-me",
            "generate-a-32-byte-secure-random-secret-key",
        }
        if self.SECRET_KEY.strip() in placeholder_values:
            raise ValueError("A non-placeholder SECRET_KEY is required in production.")
        if self.TOKEN_ENCRYPTION_KEY is None:
            raise ValueError("TOKEN_ENCRYPTION_KEY is required in production.")
        if not self.OURA_CLIENT_ID or not self.OURA_CLIENT_ID.strip():
            raise ValueError("OURA_CLIENT_ID is required in production.")
        if self.OURA_CLIENT_SECRET is None or not self.OURA_CLIENT_SECRET.get_secret_value().strip():
            raise ValueError("OURA_CLIENT_SECRET is required in production.")
        if not self.TELEGRAM_BOT_TOKEN or self.TELEGRAM_BOT_TOKEN.startswith("1234567890:"):
            raise ValueError("A non-placeholder TELEGRAM_BOT_TOKEN is required in production.")
        if self.DENYS_TELEGRAM_ID <= 0 or self.OLEKSANDRA_TELEGRAM_ID <= 0:
            raise ValueError("Authorized Telegram IDs must be positive.")
        if self.DENYS_TELEGRAM_ID == self.OLEKSANDRA_TELEGRAM_ID:
            raise ValueError("Authorized Telegram IDs must be distinct.")

        database_url = make_url(self.DATABASE_URL)
        if database_url.drivername.startswith("postgresql") and database_url.host in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("DATABASE_URL cannot target localhost in production.")

        redirect_uri = self.OURA_REDIRECT_URI.strip().lower()
        if not redirect_uri.startswith("https://"):
            raise ValueError("OURA_REDIRECT_URI must use HTTPS in production.")
        if not redirect_uri.endswith("/oauth/oura/callback"):
            raise ValueError("OURA_REDIRECT_URI must end with /oauth/oura/callback.")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
