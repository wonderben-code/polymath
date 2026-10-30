"""Validated server-side configuration; credentials never enter public responses."""

import base64
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
    # Optional free OpenAlex key, sent only as a Bearer header for source search.
    openalex_api_key: SecretStr | None = None
    operator_bootstrap_token: SecretStr | None = None
    operator_actor_id: str = Field(
        default="publisher", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$"
    )
    operator_display_name: str = Field(default="Publisher", min_length=1, max_length=120)
    publication_approval_enabled: bool = False
    publication_execution_enabled: bool = False
    literature_ingestion_enabled: bool = False
    public_sharing_enabled: bool = False
    # Experimental Frontier programmes record a tree of ordinary owned research
    # runs; they never spend directly, so the feature itself defaults on.
    frontier_enabled: bool = True
    converge_enabled: bool = True
    budget_readiness_enabled: bool = False
    budget_admin_actor_ids: tuple[str, ...] = ()
    budget_max_hard_cap_microdollars: int = Field(default=150_000_000, ge=1, le=150_000_000)
    paid_meta_probe_enabled: bool = False
    paid_meta_research_enabled: bool = False
    public_meta_research_enabled: bool = False
    # The longest single free-use delegation an operator may approve (1 h to 14 days).
    public_max_delegation_seconds: int = Field(default=86400, ge=3600, le=14 * 86400)
    public_resource_limits_enabled: bool = False
    native_budget_account_id: str | None = Field(
        default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$"
    )
    zenodo_sandbox_token: SecretStr | None = None
    zenodo_production_token: SecretStr | None = None
    publication_publisher_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$"
    )
    publication_publisher_name: str | None = Field(default=None, min_length=1, max_length=200)
    publication_destinations: tuple[Literal["sandbox", "production"], ...] = ()
    # Connect Zenodo for every visitor (docs/CONNECT_ZENODO.md). A destination is
    # offered only when its OAuth client ID and secret are both configured.
    visitor_zenodo_enabled: bool = False
    zenodo_connection_key: SecretStr | None = None
    public_site_origin: str | None = Field(
        default=None, pattern=r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$"
    )
    zenodo_sandbox_client_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,200}$")
    zenodo_sandbox_client_secret: SecretStr | None = None
    zenodo_production_client_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_.-]{1,200}$"
    )
    zenodo_production_client_secret: SecretStr | None = None
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

    @field_validator(
        "model_api_key",
        "operator_bootstrap_token",
        "zenodo_sandbox_token",
        "zenodo_production_token",
        "zenodo_connection_key",
        "zenodo_sandbox_client_secret",
        "zenodo_production_client_secret",
        mode="before",
    )
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

    @field_validator("budget_admin_actor_ids")
    @classmethod
    def require_budget_actor_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            len(value) > 10
            or len(set(value)) != len(value)
            or any(
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", item) is None for item in value
            )
        ):
            raise ValueError("Budget administration requires distinct explicit actor identifiers")
        return value

    @model_validator(mode="after")
    def require_budget_readiness_authority(self) -> "Settings":
        if self.budget_readiness_enabled and (
            not self.operator_bootstrap_token
            or self.operator_actor_id not in self.budget_admin_actor_ids
        ):
            raise ValueError("Budget readiness requires an explicitly authorized operator")
        return self

    @model_validator(mode="after")
    def require_explicit_native_probe_configuration(self) -> "Settings":
        if (self.paid_meta_probe_enabled or self.paid_meta_research_enabled) and not (
            self.budget_readiness_enabled
            and self.operator_bootstrap_token
            and self.operator_actor_id in self.budget_admin_actor_ids
            and self.native_budget_account_id
            and self.meta_configured
            and self.meta_api_base_url == "https://api.meta.ai/v1"
        ):
            raise ValueError("Paid Meta work requires explicit native and budget authority")
        return self

    @model_validator(mode="after")
    def require_explicit_public_configuration(self) -> "Settings":
        if self.public_resource_limits_enabled and not self.operator_bootstrap_token:
            raise ValueError("Public resource controls require a strong server credential")
        if self.public_meta_research_enabled and not (
            self.paid_meta_research_enabled and self.public_resource_limits_enabled
        ):
            raise ValueError("Public research requires native authority and resource controls")
        return self

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
        if self.publication_execution_enabled:
            if not self.publication_approval_enabled:
                raise ValueError("Publication execution requires explicit approval configuration")
            if any(
                (destination == "sandbox" and self.zenodo_sandbox_token is None)
                or (destination == "production" and self.zenodo_production_token is None)
                for destination in self.publication_destinations
            ):
                raise ValueError("Publication execution requires credentials for each destination")
        return self

    @field_validator("meta_model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        normalized = value.strip() or None if value is not None else None
        if (
            normalized is not None
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}", normalized) is None
        ):
            raise ValueError("META_MODEL must be an explicit bounded model identifier")
        return normalized

    @model_validator(mode="after")
    def require_visitor_zenodo_configuration(self) -> "Settings":
        if self.visitor_zenodo_enabled:
            if self.zenodo_connection_key is None or self.public_site_origin is None:
                raise ValueError("Visitor Zenodo requires a connection key and the site origin")
            try:
                key = base64.b64decode(self.zenodo_connection_key.get_secret_value(), validate=True)
            except ValueError:
                key = b""
            if len(key) != 32:
                raise ValueError("ZENODO_CONNECTION_KEY must be 32 random bytes in base64")
        return self

    @property
    def visitor_zenodo_destinations(self) -> tuple[Literal["sandbox", "production"], ...]:
        if not self.visitor_zenodo_enabled:
            return ()
        pairs = (
            ("sandbox", self.zenodo_sandbox_client_id, self.zenodo_sandbox_client_secret),
            ("production", self.zenodo_production_client_id, self.zenodo_production_client_secret),
        )
        return tuple(name for name, client, secret in pairs if client and secret)

    @property
    def meta_configured(self) -> bool:
        return bool(self.model_api_key and self.meta_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
