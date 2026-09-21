"""Operator API: buyurtmalar ro'yxati + buyruq paneli (docs U4/U5)."""
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

router = APIRouter()


@router.get("/orders")
def orders_list(
    request: Request,
    tenant: str = "demo-retail",
    limit: int = 50,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    from . import storage
    from .security import scope_ok

    if not scope_ok(tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    limit = max(1, min(limit, 200))
    rows = storage.db().execute(
        "SELECT id,update_id,customer,phone,product_id,qty,branch_id,status,created_at"
        " FROM orders WHERE tenant=? ORDER BY created_at DESC LIMIT ?",
        (tenant, limit)).fetchall()
    from .security import auth_role

    full = auth_role(x_admin_token, request) == "super"
    out = []
    for r in rows:
        d = dict(r)
        if not full:
            import re as _re

            d["phone"] = _re.sub(r"\+998[\d\s\-()]{9,16}", "+998***", d.get("phone") or "")
        out.append(d)
    return {"orders": out}


class SimulateRequest(BaseModel):
    tenant: str = "demo-retail"
    text: str = Field(max_length=2000)
    sender: str = Field(default="operator", max_length=64)
    key: str = ""  # UI uuid — double-submit idempotentligi


@router.post("/simulate")
def simulate(
    req: SimulateRequest,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """Buyruq paneli: operator matni pipeline'dan o'tadi (xuddi mijoz kabi).

    Operator kvotasi mijoz limitiga tegmaydi (admin-gated).
    """
    import uuid as _uuid

    from .pipeline import handle_text_message
    from .security import scope_ok

    if not scope_ok(req.tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="text bo'sh")
    from .limits import check_and_hit as _hit

    if not _hit("ui:" + req.tenant):  # operator byudjeti alohida (mijoz kvotasiga tegmaydi)
        raise HTTPException(status_code=429, detail="Operator limiti tugadi")
    key = req.key.strip() or f"ui-{_uuid.uuid4().hex[:12]}"
    return handle_text_message(req.tenant, "ui", key, req.sender, req.text[:2000], count_quota=False)


@router.get("/agents")
def agents_map(
    request: Request,
    tenant: str = "demo-retail",
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """Agent xaritasi (docs U1): pack + ladder + bugungi yuk."""
    from datetime import datetime, time as _time
    from zoneinfo import ZoneInfo

    import os as _os

    from . import storage
    from .ladder import LadderStore, key as ladder_key
    from .packs import PackError, load_pack
    from .security import scope_ok

    if not scope_ok(tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    try:
        pack = load_pack(tenant)
    except PackError:
        raise HTTPException(status_code=404, detail="tenant pack topilmadi")
    store = LadderStore()
    tz = ZoneInfo(_os.getenv("OPERATOR_TZ", "Asia/Tashkent"))
    now_local = datetime.now(tz)
    day_start = int(datetime.combine(now_local.date(), _time.min, tzinfo=tz).timestamp())
    day_end = day_start + 86400
    c = storage.db()
    pend_rows = c.execute(
        "SELECT agent, COUNT(*) n FROM approvals WHERE tenant=? AND status='pending' GROUP BY agent",
        (tenant,)).fetchall()
    pend_map = {r["agent"]: r["n"] for r in pend_rows}
    done_rows = c.execute(
        "SELECT agent, COUNT(*) n FROM approvals WHERE tenant=? AND status!='pending'"
        " AND decided_at>=? AND decided_at<? GROUP BY agent",
        (tenant, day_start, day_end)).fetchall()
    done_map = {r["agent"]: r["n"] for r in done_rows}
    levels = store.all_levels()
    out = []
    for a in pack.agents:
        k = ladder_key(tenant, a.id)
        out.append({"id": a.id, "name": a.name, "tools": a.tools,
                    "ladder": levels.get(k, "human_led"),
                    "pending": pend_map.get(k, 0), "done_today": done_map.get(k, 0)})
    return {"tenant": tenant, "shop": pack.shop_name, "agents": out}
