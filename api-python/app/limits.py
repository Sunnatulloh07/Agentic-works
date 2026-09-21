"""Tenant kunlik limiti (docs §11.5: cheksiz suhbat = $150+ riski).

Redis bo'lsa — atomik (multi-worker safe), bo'lmasa SQLite fallback.
"""
import math
import os
from datetime import datetime, timezone


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _limit() -> int:
    try:
        v = int(os.getenv("DAILY_LIMIT_PER_TENANT", "1000"))
        return v if v > 0 else 1000
    except ValueError:
        return 1000


def check_and_hit(tenant: str) -> bool:
    from . import cache
    from . import storage

    today = _today()
    r = cache.get()
    if r is not None:  # Redis: atomik (multi-worker safe)
        try:
            key = f"limit:{tenant}:{today}"
            r.set(key, 0, ex=86400, nx=True)  # TTL avval — crash'da kalit qolmaydi
            n = r.incr(key)
            return n <= _limit()
        except Exception:
            pass  # pastga tushadi — SQLite fallback
    with storage.tx() as c:
        c.execute("DELETE FROM limits WHERE day != ?", (today,))
        c.execute("DELETE FROM warned WHERE day != ?", (today,))
        row = c.execute("SELECT day, count FROM limits WHERE tenant=?", (tenant,)).fetchone()
        n = row["count"] if row and row["day"] == today else 0
        if n >= _limit():
            return False
        c.execute("INSERT OR REPLACE INTO limits(tenant, day, count) VALUES(?,?,?)",
                  (tenant, today, n + 1))
        return True


def warned(tenant: str) -> bool:
    """80% ogohlantirish — kuniga 1 marta True (spam yo'q)."""
    from . import cache
    from . import storage

    today = _today()
    r = cache.get()
    if r is not None:
        try:
            n = int(r.get(f"limit:{tenant}:{today}") or 0)
            if n >= math.ceil(0.8 * _limit()):
                flag = f"warned:{tenant}:{today}"
                if r.setnx(flag, 1):
                    r.expire(flag, 86400)
                    return True
            return False
        except Exception:
            pass
    with storage.tx() as c:
        row = c.execute("SELECT day, count FROM limits WHERE tenant=?", (tenant,)).fetchone()
        n = row["count"] if row and row["day"] == today else 0
        if n >= math.ceil(0.8 * _limit()):
            if c.execute("SELECT 1 FROM warned WHERE tenant=? AND day=?",
                         (tenant, today)).fetchone():
                return False
            c.execute("INSERT INTO warned(tenant, day) VALUES(?,?)", (tenant, today))
            return True
        return False
