"""Runtime konfiguratsiyasi va production fail-closed qoidalari."""
from dataclasses import dataclass
import os
from collections.abc import Mapping


class ConfigError(RuntimeError):
    """Runtime xavfsiz ishga tushishi uchun konfiguratsiya yaroqsiz."""


@dataclass(frozen=True)
class RuntimeConfig:
    environment: str
    production: bool
    jwt_secret: str
    admin_token: str
    telegram_webhook_secret: str


def _secret(env: Mapping[str, str], key: str, minimum: int) -> str:
    value = env.get(key, "")
    if not value:
        raise ConfigError(f"{key} sozlanmagan")
    if value in {"dev-only-secret-change-in-prod-32chars", "dev-webhook-secret"}:
        raise ConfigError(f"{key} ma’lum demo qiymati bo‘lishi mumkin emas")
    if len(value) < minimum:
        raise ConfigError(f"{key} kuchsiz: kamida {minimum} belgi kerak")
    return value


def validate_runtime_config(env: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Environment'ni tekshiradi; Faqat explicit ALLOW_INSECURE_DEV bilan lokal defaultlarga ruxsat beradi."""
    values = env if env is not None else os.environ
    environment = values.get("ENV", "").lower().strip()
    if not environment:
        raise ConfigError("ENV sozlanmagan; production yoki explicit dev/test kerak")
    if environment not in {"dev", "development", "test", "prod", "production"}:
        raise ConfigError("ENV faqat dev, test yoki production bo'lishi kerak")
    production = environment in {"prod", "production"}
    # Login throttle proxy ro‘yxati: xato yozuv startup’da to‘xtatadi, birinchi
    # login’dagi 500 emas (app/client_ip.py).
    from .client_ip import ProxyConfigError, parse_trusted_proxies
    try:
        parse_trusted_proxies(values.get("TRUSTED_PROXIES", ""))
    except ProxyConfigError as exc:
        raise ConfigError(str(exc)) from None
    # uvicorn'ning o‘z proxy qatlami '*' bilan har kimdan X-Forwarded-For qabul qiladi
    # va CHAPdagi hop’ni oladi -- uni mijoz yozadi, ya’ni per-IP throttle kaliti
    # hujumchi tanlagan qiymat bo‘lib qoladi. Yagona XFF o‘quvchisi app/client_ip.py.
    if "*" in (part.strip() for part in values.get("FORWARDED_ALLOW_IPS", "").split(",")):
        raise ConfigError("FORWARDED_ALLOW_IPS='*' ruxsat etilmaydi: uvicorn’ni --no-proxy-headers "
                          "bilan ishga tushiring va proxy manzillarini TRUSTED_PROXIES ga yozing")
    # Pack ichidagi integrations.yaml o‘z TENANT_<NOM>__ nom maydonidan tashqaridagi env
    # nomini faqat shu ro‘yxat orqali o‘qiy oladi. Ro‘yxatdagi platforma siri uni har bir
    # pack’ka ochib bergan bo‘lardi, shuning uchun xato birinchi so‘rovda emas, startup’da.
    from platform_runtime.tools import CredentialScopeError, shared_secret_names
    try:
        shared_secret_names(values)
    except CredentialScopeError as exc:
        raise ConfigError(str(exc)) from None
    if not production and values.get("ALLOW_INSECURE_DEV", "").lower() == "true":
        return RuntimeConfig(
            environment=environment,
            production=False,
            jwt_secret=values.get("JWT_SECRET", "dev-only-secret-change-in-prod-32chars"),
            admin_token=values.get("ADMIN_TOKEN", ""),
            telegram_webhook_secret=values.get("TELEGRAM_WEBHOOK_SECRET", "dev-webhook-secret"),
        )
    return RuntimeConfig(
        environment=environment,
        production=production,
        jwt_secret=_secret(values, "JWT_SECRET", 32),
        admin_token=_secret(values, "ADMIN_TOKEN", 16),
        telegram_webhook_secret=_secret(values, "TELEGRAM_WEBHOOK_SECRET", 16),
    )