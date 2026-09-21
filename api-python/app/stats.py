"""Kunlik statistika (docs §8.0: intel.daily_stats agenti manbai).

Manba: SQLite orders (haqiqat). CSV mirror faqat Sheets/operator uchun.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request, status

from . import approvals as appr
from .hotlead import pending_leads
from .security import scope_ok

router = APIRouter()


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _day_bounds() -> tuple:
    """Operator kuni (Toshkent) → unix chegara."""
    import os as _os
    from datetime import time as _time
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(_os.getenv("OPERATOR_TZ", "Asia/Tashkent"))
    now_local = datetime.now(tz)
    start = int(datetime.combine(now_local.date(), _time.min, tzinfo=tz).timestamp())
    return start, start + 86400


@router.get("/stats/daily")
def daily(
    request: Request,
    tenant: str = "demo-retail",
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    if not scope_ok(tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    from . import storage

    orders, new = 0, 0
    start, end = _day_bounds()
    for row in storage.db().execute(
            "SELECT status, created_at FROM orders WHERE tenant=?", (tenant,)).fetchall():
        try:
            ts = int(row["created_at"])
        except (ValueError, TypeError):
            continue  # sanasi noma'lum qatorlar hisobga kirmaydi
        if ts < start or ts >= end:
            continue
        orders += 1
        if row["status"] == "new":
            new += 1
    return {
        "tenant": tenant,
        "orders_total": orders,
        "orders_new": new,
        "pending_approvals": len(appr._store_get().pending(tenant)),
        "hot_leads": len(pending_leads(tenant)),
    }
