import pytest

from app.config import ConfigError, validate_runtime_config


def test_development_config_can_use_local_defaults():
    config = validate_runtime_config({"ENV": "dev", "ALLOW_INSECURE_DEV": "true"})

    assert config.environment == "dev"
    assert config.production is False


def test_missing_environment_is_rejected():
    with pytest.raises(ConfigError, match="ENV"):
        validate_runtime_config({})


def test_production_requires_core_secrets():
    with pytest.raises(ConfigError, match="JWT_SECRET"):
        validate_runtime_config({"ENV": "production"})


def test_production_rejects_weak_secrets():
    env = {
        "ENV": "production",
        "JWT_SECRET": "short",
        "ADMIN_TOKEN": "short",
        "TELEGRAM_WEBHOOK_SECRET": "short",
    }

    with pytest.raises(ConfigError, match="kuchsiz"):
        validate_runtime_config(env)


def test_production_accepts_complete_core_configuration():
    env = {
        "ENV": "production",
        "JWT_SECRET": "j" * 32,
        "ADMIN_TOKEN": "a" * 16,
        "TELEGRAM_WEBHOOK_SECRET": "t" * 16,
    }

    config = validate_runtime_config(env)

    assert config.production is True
    assert config.jwt_secret == "j" * 32

def test_development_without_explicit_opt_in_requires_secrets():
    with pytest.raises(ConfigError, match="JWT_SECRET"):
        validate_runtime_config({"ENV": "dev"})
