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


@pytest.mark.parametrize(
    "changes",
    [
        {"budget_readiness_enabled": True},
        {"budget_readiness_enabled": True, "budget_admin_actor_ids": ("publisher",)},
        {
            "budget_readiness_enabled": True,
            "operator_bootstrap_token": "Synthetic_Operator_0123456789abcdef-ABCDEF",
            "budget_admin_actor_ids": ("another-actor",),
        },
        {"budget_admin_actor_ids": ("publisher", "publisher")},
        {"budget_admin_actor_ids": ("invalid actor",)},
        {"budget_max_hard_cap_microdollars": 150_000_001},
        {"budget_max_hard_cap_microdollars": 0},
    ],
)
def test_budget_readiness_requires_explicit_bounded_role_configuration(changes):
    with pytest.raises(ValidationError):
        Settings(**changes, _env_file=None)


def test_budget_readiness_configuration_grants_no_publication_or_model_authority():
    settings = Settings(
        operator_bootstrap_token="Synthetic_Operator_0123456789abcdef-ABCDEF",
        budget_admin_actor_ids=("publisher",),
        budget_readiness_enabled=True,
        _env_file=None,
    )
    assert settings.budget_readiness_enabled
    assert settings.budget_max_hard_cap_microdollars == 150_000_000
    assert not settings.publication_approval_enabled
    assert not settings.publication_execution_enabled
    assert not settings.meta_configured


def test_native_credentials_and_ledger_do_not_enable_paid_probe_by_default():
    settings = Settings(
        model_api_key="synthetic-placeholder",
        meta_model="synthetic-meta-model",
        native_budget_account_id="native-ledger",
        _env_file=None,
    )
    assert not settings.paid_meta_probe_enabled
    assert not settings.budget_readiness_enabled


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"budget_readiness_enabled": True},
        {"budget_readiness_enabled": True, "native_budget_account_id": "native"},
        {
            "budget_readiness_enabled": True,
            "native_budget_account_id": "native",
            "model_api_key": "synthetic",
        },
        {
            "budget_readiness_enabled": True,
            "native_budget_account_id": "native",
            "model_api_key": "synthetic",
            "meta_model": "synthetic-model",
            "meta_api_base_url": "https://other-provider.invalid/v1",
        },
    ],
)
def test_paid_probe_requires_all_explicit_server_configuration(changes):
    with pytest.raises(ValidationError):
        Settings(
            paid_meta_probe_enabled=True,
            operator_bootstrap_token="Synthetic_Operator_0123456789abcdef-ABCDEF",
            budget_admin_actor_ids=("publisher",),
            _env_file=None,
            **changes,
        )


def test_paid_probe_configuration_does_not_enable_any_other_remote_action():
    settings = Settings(
        paid_meta_probe_enabled=True,
        budget_readiness_enabled=True,
        operator_bootstrap_token="Synthetic_Operator_0123456789abcdef-ABCDEF",
        budget_admin_actor_ids=("publisher",),
        native_budget_account_id="same-native-ledger",
        model_api_key="synthetic-placeholder",
        meta_model="synthetic-meta-model",
        _env_file=None,
    )
    assert settings.paid_meta_probe_enabled
    assert not settings.publication_execution_enabled
    assert not settings.literature_ingestion_enabled


@pytest.mark.parametrize("model_id", ["model with spaces", "model\nheader", ".model", "a" * 121])
def test_meta_model_configuration_matches_the_native_identifier_contract(model_id):
    with pytest.raises(ValidationError):
        Settings(meta_model=model_id, _env_file=None)


@pytest.mark.parametrize("account_id", ["legacy/account", "-ledger", "ledger name"])
def test_new_native_ledger_configuration_is_strict_even_when_paid_execution_is_off(account_id):
    with pytest.raises(ValidationError):
        Settings(native_budget_account_id=account_id, _env_file=None)
