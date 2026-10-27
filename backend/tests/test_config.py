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
