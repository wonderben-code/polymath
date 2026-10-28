"""Validated server-side configuration; credentials never enter public responses."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
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
    operator_bootstrap_token: SecretStr | None = None
    operator_actor_id: str = Field(
        default="publisher", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$"
    )
    operator_display_name: str = Field(default="Publisher", min_length=1, max_length=120)
    publication_approval_enabled: bool = False
    publication_publisher_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$"
    )
    publication_publisher_name: str | None = Field(default=None, min_length=1, max_length=200)
    publication_destinations: tuple[Literal["sandbox", "production"], ...] = ()
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

    @field_validator("model_api_key", "operator_bootstrap_token", mode="before")
    @classmethod
    def empty_key_is_absent(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("operator_bootstrap_token")
    @classmethod
    def require_operator_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            token = value.get_secret_value()
            if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) is None or len(set(token)) < 12:
                raise ValueError("Operator access requires a strong URL-safe bootstrap token")
        return value

    @field_validator("operator_display_name", "publication_publisher_name")
    @classmethod
    def require_public_identity(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("Publisher identity must be valid public text") from None
            if not value or any(ord(character) < 32 for character in value):
                raise ValueError("Publisher identity must be valid public text")
        return value

    @model_validator(mode="after")
    def require_explicit_approval_configuration(self) -> "Settings":
        if len(set(self.publication_destinations)) != len(self.publication_destinations):
            raise ValueError("Publication destinations must be distinct")
        if self.publication_approval_enabled and not (
            self.operator_bootstrap_token
            and self.publication_publisher_id
            and self.publication_publisher_name
            and self.publication_destinations
        ):
            raise ValueError(
                "Publication approval requires explicit operator, publisher and destination"
            )
        return self

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
