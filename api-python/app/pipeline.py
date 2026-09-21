"""Umumiy xabar pipeline: Telegram/Instagram/Runner bir oqimga kiradi.

Kanallar faqat parse qiladi — mantiq shu yerda (DIP: kanal almashadi, oqim o'zgarmaydi).
Tekshiruv tartibi: secret(kanalda) → pack → parse → dedup → limit → ish.
Ruling: masofadan /stop O'CHIQ (har kim DoS qilardi) — faqat admin panel/API.
"""
import hashlib
import re
import time

from fastapi import HTTPException

from . import approvals as appr
from . import runner_ws as rws  # noqa: F401 (kelajak: operator xabarnomasi)
from . import trace
from .hotlead import log_lead
from .limits import check_and_hit, warned
from .orders import Order, validate_order_payload
from .packs import PackError, load_pack
from .responder import FALLBACK, RuleResponder

_responder = RuleResponder()
ORDER_AGENT = "sales.order_taker"
PHONE_CLEAN = re.compile(r"[\s\-()]")


def remember(tenant: str, channel: str, key: str) -> bool:
    """Idempotent kalit (SQLite, restart-safe)."""
    import sqlite3

    from . import storage

    c = storage.db()
    try:
        c.execute("INSERT INTO idem(tenant, channel, key) VALUES(?,?,?)", (tenant, channel, key))
        c.commit()
        return True
    except sqlite3.IntegrityError:
        c.rollback()
        return False


def _stable_id(channel: str, update_key: str, numeric_id: int | None) -> int:
    """Deterministik int: Telegram — asl update_id, IG — sha256(mid)."""
    if numeric_id is not None:
        return numeric_id
    return int(hashlib.sha256(f"{channel}:{update_key}".encode()).hexdigest()[:8], 16) % 10**9


def _norm_phone(raw: str) -> str:
    p = PHONE_CLEAN.sub("", raw)
    if re.fullmatch(r"998\d{9}", p):
        p = "+" + p
    return p


def _parse_buy(text: str, pack, tenant: str, order_uid: int, channel: str) -> Order:
    parts = text.split()
    head = parts[0].split("@")[0].lower() if parts else ""
    if len(parts) < 5 or head != "/buy":
        raise HTTPException(status_code=422, detail="Format: /buy KOD SON FILIAL TELEFON [ISM]")
    _, pid, qty_s, branch_raw, phone_raw, *rest = parts
    pid = pid.strip(",.!;:")
    product = next((p for p in pack.products if p.id.upper() == pid.upper()), None)
    if product is None:
        raise HTTPException(status_code=422, detail=f"Tovar topilmadi: {pid}")
    branch_id = branch_raw.strip(",.!;:").lower()
    branch = next((b for b in pack.branches if b.id.lower() == branch_id), None)
    if branch is None:
        raise HTTPException(status_code=422, detail=f"Filial topilmadi: {branch_raw}")
    try:
        qty = int(qty_s)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Son noto'g'ri: {qty_s}")
    if qty < 1 or qty > 99:
        raise HTTPException(status_code=422, detail="Son 1-99 oralig'ida bo'lsin")
    phone = _norm_phone(phone_raw)
    order = Order(
        tenant=tenant,
        update_id=order_uid,
        customer=" ".join(rest)[:200] or "mijoz",
        phone=phone,
        product_id=product.id,
        qty=qty,
        branch_id=branch.id,
    )
    try:
        validate_order_payload(order.model_dump(), pack)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return order


def _legacy_handle_text_message(tenant: str, channel: str, update_key: str, sender: str,
                        text: str, numeric_id: int | None = None,
                        count_quota: bool = True) -> dict:
    """Bitta kirish — barcha kanallar uchun. 422/404/429 HTTPException otadi."""
    t0 = time.perf_counter()
    try:
        pack = load_pack(tenant)
    except PackError:
        raise HTTPException(status_code=404, detail="tenant pack topilmadi")
    clean = (text or "").strip()[:4000]
    if clean == "/stop":
        return {"ok": True, "reply": "To'xtatish faqat operator panelidan (xavfsizlik)."}
    parts = clean.split()
    head = parts[0].split("@")[0].lower() if parts else ""
    is_buy = head == "/buy"
    order_uid = _stable_id(channel, update_key, numeric_id)
    if is_buy:  # 422 kvota/dedup yemasligi uchun limitdan oldin parse
        order = _parse_buy(clean, pack, tenant, order_uid, channel)
    else:
        order = None
    if not remember(tenant, channel, update_key):
        found = appr._store_get().find_pending_by_update(tenant, channel, order_uid)
        out = {"ok": True, "duplicate": True, "reply": "Qabul qilingan."}
        if found:
            out["approval_id"] = found["id"]
        return out
    if count_quota and not check_and_hit(tenant):
        trace.log({"tenant": tenant, "channel": channel, "action": "rate_limited_429"})
        raise HTTPException(status_code=429, detail="Kunlik limit tugadi")
    action, agent_id, extra = "reply", f"{tenant}:sales.responder", {}
    if is_buy:
        assert order is not None
        ap = appr._store_get().submit(
            tenant=tenant,
            kind="order",
            agent_id=f"{tenant}:{ORDER_AGENT}",
            summary=f"{order.product_id}x{order.qty} {order.branch_id} {order.phone}",
            payload={**order.model_dump(), "channel": channel},
            channel=channel,
        )
        reply = f"So'rovingiz qabul qilindi, operator tasdiqlaydi. ID: {ap.id}."
        action, agent_id, extra = "order_pending", f"{tenant}:{ORDER_AGENT}", {"approval_id": ap.id}
    else:
        reply = _responder.reply(clean or "/start", pack)
        if reply == FALLBACK:
            log_lead(tenant, channel, sender, clean[:500])
    ms = int((time.perf_counter() - t0) * 1000)
    trace.log({"tenant": tenant, "channel": channel, "update_key": str(update_key),
               "agent": agent_id, "action": action, "ms": ms})
    if warned(tenant):
        trace.log({"tenant": tenant, "action": "limit_warned_80pct"})
    return {"ok": True, "reply": reply, **extra}


def handle_text_message(tenant: str, channel: str, update_key: str, sender: str,
                        text: str, numeric_id: int | None = None,
                        count_quota: bool = True, conversation_id: str | None = None) -> dict:
    """Durable acceptance first. No early processed flag, LLM or provider call here."""
    import os
    if os.getenv("PIPELINE_MODE") == "legacy":
        from .security import is_prod
        if is_prod():
            raise HTTPException(503, "Legacy pipeline is disabled in production")
        return _legacy_handle_text_message(tenant,channel,update_key,sender,text,numeric_id,count_quota)
    from .platform_api import engine, call
    try:
        load_pack(tenant)
    except PackError:
        raise HTTPException(404,"tenant pack topilmadi")
    # Existing quota accounting is intentionally not used before durable acceptance:
    # a failed quota check must not poison the inbox idempotency key.
    if channel == "ui": channel = "web"
    return call(engine().accept_event,tenant,channel,str(update_key),{
        "sender":sender,"conversation_id":conversation_id or sender,"text":(text or "")[:4000]})
