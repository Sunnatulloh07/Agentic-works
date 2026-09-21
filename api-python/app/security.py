"""Umumiy xavfsizlik yordamchilari (audit 2026-09-12, #25/#26).

Dev'da ochiq (kalitsiz demo), prod'da fail-closed.
"""
import hmac
import os


def _env() -> str:
    return os.getenv("ENV", "").lower()


def is_prod() -> bool:
    return _env() in ("prod", "production")


def dev_open() -> bool:
    """Faqat aniq dev/test — 'staging', 'Prod' kabi noaniq qiymatlar yopiq."""
    return (_env().strip() in ("dev", "development", "test")
            and os.getenv("ALLOW_INSECURE_DEV", "").lower() == "true")


def admin_ok(header_value: str | None) -> bool:
    required = os.getenv("ADMIN_TOKEN")
    if required:
        return bool(header_value) and hmac.compare_digest(header_value, required)
    return dev_open()


def telegram_secret_ok(provided: str | None) -> bool:
    expected = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if not expected:
        if not dev_open():
            return False
        expected = "dev-webhook-secret"
    return bool(provided) and hmac.compare_digest(provided, expected)


def tenant_secrets() -> dict:
    """TENANT_SECRETS='t1:s1,t2:s2' — har tenant o'z webhook secreti."""
    out = {}
    for pair in os.getenv("TENANT_SECRETS", "").split(","):
        if ":" in pair:
            t, s = pair.split(":", 1)
            if t.strip() and s.strip():
                out[t.strip()] = s.strip()
    return out


def resolve_webhook_tenant(provided: str | None, query_tenant: str) -> tuple[str, str | None]:
    """-> ("ok", tenant) | ("mismatch", None) | ("none", None)."""
    mapping = tenant_secrets()
    if not mapping:
        if is_prod():
            configured = os.getenv("TELEGRAM_DEFAULT_TENANT", "").strip()
            if not configured or query_tenant != configured:
                return ("none", None)
        return ("ok", query_tenant) if telegram_secret_ok(provided) else ("none", None)
    for t, s in mapping.items():
        if provided and hmac.compare_digest(provided, s):
            if not query_tenant or query_tenant == t:
                return ("ok", t)
            return ("mismatch", None)
    return ("none", None)


def auth_role(x_admin_token: str | None, request=None) -> str | None:
    """'super' (ADMIN_TOKEN) | tenant_id (JWT unga tegishli) | None.

    Bearer bo'lsa — faqat JWT gapiradi (dev-open uni bekor qilmaydi).
    Bearer bo'lmasa — admin mantig'i (dev'da ochiq, prod'da yopiq).
    """
    if request is not None:
        auth = request.headers.get("Authorization", "")
        if auth:
            if not auth.lower().startswith("bearer "):
                return None
            try:
                from .auth import verify_token

                return verify_token(auth[7:].strip())
            except Exception:
                return None
    if admin_ok(x_admin_token):
        return "super"
    return None


def scope_ok(tenant: str, x_admin_token: str | None, request=None) -> bool:
    role = auth_role(x_admin_token, request)
    return role == "super" or role == tenant
