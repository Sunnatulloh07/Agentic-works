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

@pytest.mark.parametrize("value", ["*", " * ", "127.0.0.1,*", "10.0.0.1, *"])
def test_a_wildcard_forwarded_allow_ips_is_refused(value):
    """uvicorn then trusts X-Forwarded-For from anyone and takes the LEFT-most hop."""
    for env in ({"ENV": "dev", "ALLOW_INSECURE_DEV": "true"},
                {"ENV": "production", "JWT_SECRET": "j" * 32, "ADMIN_TOKEN": "a" * 16,
                 "TELEGRAM_WEBHOOK_SECRET": "t" * 16}):
        with pytest.raises(ConfigError, match="FORWARDED_ALLOW_IPS") as caught:
            validate_runtime_config({**env, "FORWARDED_ALLOW_IPS": value})
        assert "TRUSTED_PROXIES" in str(caught.value)


def test_an_explicit_forwarded_allow_ips_list_is_accepted():
    for value in ("", "127.0.0.1", "10.0.0.1,10.0.0.2"):
        assert validate_runtime_config({"ENV": "dev", "ALLOW_INSECURE_DEV": "true",
                                        "FORWARDED_ALLOW_IPS": value}).production is False


def test_development_without_explicit_opt_in_requires_secrets():
    with pytest.raises(ConfigError, match="JWT_SECRET"):
        validate_runtime_config({"ENV": "dev"})
