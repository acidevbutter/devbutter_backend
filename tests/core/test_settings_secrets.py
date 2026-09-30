import pytest
from pydantic import ValidationError

from app.settings.config import Settings

_DEFAULT_JWT_SECRET = Settings.model_fields["jwt_secret"].default
_DEFAULT_DATABASE_PASSWORD = Settings.model_fields["database_password"].default


@pytest.fixture(autouse=True)
def _clear_ambient_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear ambient secrets so tests control every value explicitly
    # regardless of what's in the developer's shell or .env.
    for var in ("JWT_SECRET", "DATABASE_PASSWORD", "PAYMENT_TOKEN_ENCRYPTION_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_development_allows_default_secrets() -> None:
    settings = Settings(_env_file=None, environment="development")
    assert settings.jwt_secret == _DEFAULT_JWT_SECRET


@pytest.mark.parametrize("environment", ["staging", "preprod", "production"])
def test_non_dev_rejects_default_jwt_secret(environment: str) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(
            _env_file=None,
            environment=environment,
            jwt_secret=_DEFAULT_JWT_SECRET,
            database_password="real-password",
            payment_token_encryption_key="key",
        )


def test_non_dev_rejects_default_database_password() -> None:
    with pytest.raises(ValidationError, match="DATABASE_PASSWORD"):
        Settings(
            _env_file=None,
            environment="staging",
            jwt_secret="real-secret",
            database_password=_DEFAULT_DATABASE_PASSWORD,
            payment_token_encryption_key="key",
        )


def test_non_dev_rejects_empty_payment_token_encryption_key() -> None:
    with pytest.raises(ValidationError, match="PAYMENT_TOKEN_ENCRYPTION_KEY"):
        Settings(
            _env_file=None,
            environment="staging",
            jwt_secret="real-secret",
            database_password="real-password",
            payment_token_encryption_key="",
        )


def test_non_dev_valid_config_passes() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret="real-secret",
        database_password="real-password",
        payment_token_encryption_key="key",
    )
    assert settings.jwt_secret == "real-secret"
