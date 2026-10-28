import pytest
from pydantic import SecretStr, ValidationError

from polymath.config import Settings


def test_settings_require_postgresql_and_redact_credentials() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://user:database-secret@localhost:5432/polymath",
        model_api_key="model-secret",
        meta_model="verified-meta-model",
        _env_file=None,
    )
    assert settings.meta_configured
    assert isinstance(settings.database_url, SecretStr)
    assert "database-secret" not in repr(settings)
    assert "model-secret" not in repr(settings)


@pytest.mark.parametrize("url", ["sqlite:///test.db", "not-a-url", "postgresql://user@host/db"])
def test_settings_reject_unsupported_database_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=url, _env_file=None)


def test_blank_key_and_model_do_not_count_as_configured() -> None:
    settings = Settings(model_api_key="  ", meta_model=" ", _env_file=None)
    assert settings.model_api_key is None
    assert settings.meta_model is None
    assert not settings.meta_configured


def test_key_alone_is_not_a_verified_model_configuration() -> None:
    settings = Settings(model_api_key="placeholder", meta_model=None, _env_file=None)
    assert not settings.meta_configured


def test_invalid_connection_url_is_not_echoed_in_validation_error() -> None:
    with pytest.raises(ValidationError) as captured:
        Settings(database_url="postgresql://user:must-stay-private@host/db", _env_file=None)
    assert "must-stay-private" not in str(captured.value)
    assert "input_value" not in str(captured.value)


def test_publication_approval_defaults_disabled_and_operator_token_is_private() -> None:
    token = "Synthetic_Operator_0123456789abcdef-ABCDEF"
    settings = Settings(operator_bootstrap_token=token, _env_file=None)
    assert not settings.publication_approval_enabled
    assert settings.publication_destinations == ()
    assert settings.operator_bootstrap_token.get_secret_value() == token
    assert token not in repr(settings)
    assert not settings.meta_configured


@pytest.mark.parametrize(
    "changes",
    [
        {"operator_bootstrap_token": "private-weak-token"},
        {"operator_bootstrap_token": "a" * 64},
        {"operator_bootstrap_token": "Synthetic_Operator_0123456789abcdef?unsafe"},
        {"publication_destinations": ("sandbox", "sandbox")},
        {"publication_destinations": ("unverified-destination",)},
        {"publication_approval_enabled": True},
    ],
)
def test_publication_configuration_rejects_implicit_or_invalid_authority(changes) -> None:
    with pytest.raises(ValidationError) as captured:
        Settings(**changes, _env_file=None)
    assert "private-weak-token" not in str(captured.value)
    assert "input_value" not in str(captured.value)


def test_publication_approval_needs_all_explicit_server_settings() -> None:
    settings = Settings(
        operator_bootstrap_token="Synthetic_Operator_0123456789abcdef-ABCDEF",
        publication_approval_enabled=True,
        publication_publisher_id="synthetic-publisher",
        publication_publisher_name="Synthetic Publisher",
        publication_destinations=("sandbox",),
        _env_file=None,
    )
    assert settings.publication_approval_enabled
    assert settings.publication_destinations == ("sandbox",)
    assert not settings.meta_configured
