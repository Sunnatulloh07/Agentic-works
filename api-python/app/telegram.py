"""Telegram adapter: faqat parse + secret. Mantiq pipeline'da.

Tenant core'da yozilmaydi: ?tenant=<nom>, TELEGRAM_DEFAULT_TENANT env yoki
TENANT_SECRETS dagi per-tenant secret uni aniqlaydi. Hech biri bo'lmasa -> 400.
"""
import os

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from .pipeline import handle_text_message
from .security import resolve_webhook_tenant

router = APIRouter()
MAX_BODY = 1_000_000


class TelegramUpdate(BaseModel):
    update_id: int
    message: dict = {}


# Media without a caption still becomes a turn, so the agent can ask for a text
# description or hand off; the placeholder is all it sees (files are not fetched).
MEDIA_PLACEHOLDERS = (("photo", "[rasm]"), ("video", "[video]"), ("document", "[fayl]"),
                      ("voice", "[ovozli xabar]"), ("audio", "[audio]"), ("video_note", "[video xabar]"))


def message_text(message: dict) -> str:
    """text, else caption (media), else a media placeholder, else '' (ignored)."""
    for field in ("text", "caption"):
        value = message.get(field)
        if isinstance(value, str) and value.strip():
            return value
    for field, placeholder in MEDIA_PLACEHOLDERS:
        if message.get(field):
            return placeholder
    return ""


@router.post("/webhooks/telegram")
async def telegram_webhook(request: Request, tenant: str = "") -> dict:
    tenant = tenant or os.getenv("TELEGRAM_DEFAULT_TENANT", "").strip()
    status_, tenant = resolve_webhook_tenant(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), tenant)
    if status_ == "none":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad secret")
    if status_ == "mismatch" or tenant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant mismatch")
    try:
        size = int(request.headers.get("content-length") or 0)
    except ValueError:
        size = 0
    if size > MAX_BODY:
        raise HTTPException(status_code=413, detail="juda katta")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "juda katta")
    try:
        import json
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(422, "JSON noto‘g‘ri")
    if not isinstance(body, dict) or "message" not in body:
        return {"ok": True, "ignored": True}  # edited/callback/photo — jim
    try:
        update = TelegramUpdate(**body)
    except Exception:
        raise HTTPException(status_code=422, detail="update formati xato")
    text = message_text(update.message)
    if not text:
        return {"ok": True, "ignored": True}  # stiker, servis xabar, bo'sh matn — event yaratilmaydi
    if not tenant:
        raise HTTPException(status_code=400, detail="tenant kerak: ?tenant=<nom> yoki TELEGRAM_DEFAULT_TENANT")
    sender = str(update.message.get("from", {}).get("id", update.message.get("chat", {}).get("id", "?")))
    return handle_text_message(tenant, "telegram", str(update.update_id),
                               sender, text,
                               numeric_id=update.update_id,
                               conversation_id=str(update.message.get("chat", {}).get("id", sender)))
