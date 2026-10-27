"""Validated server-side configuration; credentials never enter public responses."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False, extra="ignore", env_file=".env", hide_input_in_errors=True
    )

    environment: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="POLYMATH_ENVIRONMENT"
    )
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://polymath:polymath@localhost:5432/polymath"
    )
    model_api_key: SecretStr | None = None
    meta_model: str | None = None
    meta_api_base_url: str = "https://api.meta.ai/v1"
    frontend_dist: Path | None = Field(
        default=None, validation_alias=AliasChoices("FRONTEND_DIST", "STATIC_DIR")
    )

    @field_validator("database_url")
    @classmethod
    def require_postgresql(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except Exception:
            raise ValueError("DATABASE_URL must be a PostgreSQL connection URL") from None
        if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
            raise ValueError("DATABASE_URL must use postgresql+psycopg and specify host/database")
        return value

    @field_validator("model_api_key", mode="before")
    @classmethod
    def empty_key_is_absent(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("meta_model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @property
    def meta_configured(self) -> bool:
        return bool(self.model_api_key and self.meta_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
