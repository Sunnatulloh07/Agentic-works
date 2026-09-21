"""Hot-lead: fallback endi yolg'on va'da emas (review #21).

Operatorga uzatilgan har bir lead shu yerga tushadi (SQLite).
Telefon yozishda maskalanadi.
"""
import json
import re
import time

from fastapi import APIRouter, Header, HTTPException, Request, status

router = APIRouter()
PHONE_MASK = re.compile(r"\+998[\d\s\-()]{9,16}")


def log_lead(tenant: str, channel: str, sender: str, text: str) -> None:
    from . import storage

    clean = re.sub(r"[<>]", "", text or "")[:500]
    clean = PHONE_MASK.sub("+998***", clean)
    storage.db().execute(
        "INSERT INTO leads(ts,tenant,channel,sender,text) VALUES(?,?,?,?,?)",
        (int(time.time()), tenant, channel, str(sender)[:64], clean),
    )
    storage.db().commit()


def pending_leads(tenant: str, limit: int = 100) -> list[dict]:
    from . import storage

    out = []
    for r in storage.db().execute(
            "SELECT ts,tenant,channel,sender,text FROM leads WHERE tenant=? ORDER BY ts LIMIT ?",
            (tenant, limit)).fetchall():
        out.append({"ts": r["ts"], "tenant": r["tenant"], "channel": r["channel"],
                    "sender": r["sender"], "text": r["text"]})
    return out


leads_router = APIRouter()


@leads_router.get("/leads/pending")
def leads_pending(
    request: Request,
    tenant: str = "demo-retail",
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    from .security import scope_ok

    if not scope_ok(tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    masked = []
    for r in pending_leads(tenant):
        row = dict(r)
        row["text"] = PHONE_MASK.sub("+998***", row.get("text", ""))
        masked.append(row)
    return {"pending": masked}
