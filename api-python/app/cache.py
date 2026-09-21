"""Redis — efemer holat (limitlar) uchun. Ishlamasa SQLite fallback.

Qoida: pul/audit (durable) har doim SQLite/Postgres'da; Redis faqat
tez hisoblagichlar. Redis o'lsa ham tizim ishlaydi (fail-open to SQLite).
"""
import os
import sys
import time

_client = None
_next_retry = 0.0


def _warn(msg: str) -> None:
    print(f"[cache] {msg}", file=sys.stderr, flush=True)


def get():
    """Redis client yoki None. Hech qachon exception otmaydi."""
    global _client, _next_retry
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    if _client is None:
        now = time.monotonic()
        if now < _next_retry:
            return None  # backoff — har so'rovda ulanishga urinilmaydi
        try:
            import redis

            _client = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
            _client.ping()
        except Exception as e:
            _next_retry = now + 60.0  # 60 sek keyin qayta urinish
            _warn(f"redis ulanmadi ({e.__class__.__name__}) — SQLite fallback")
            _client = None
    return _client


def reset() -> None:
    """Testlar uchun."""
    global _client, _next_retry
    try:
        if _client is not None:
            _client.close()
    except Exception:
        pass
    _client = None
    _next_retry = 0.0
