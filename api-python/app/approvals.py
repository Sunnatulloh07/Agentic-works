"""Tasdiq navbati (docs F5): write-harakatlar operator roziligisiz o'tmaydi.

Saqlash: SQLite (storage.py). Interfeys o'zgarmaydi — Postgres kelganda
faqat storage ichi almashadi.
"""
import json
import re
import threading
import time
import uuid

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from . import storage

router = APIRouter()
_lock = threading.Lock()

PHONE_MASK = re.compile(r"\+998[\d\s\-()]{9,16}")


def _mask_phone(s: str) -> str:
    return PHONE_MASK.sub("+998***", s)


class Approval(BaseModel):
    id: str
    tenant: str
    kind: str
    agent_id: str
    summary: str
    payload: dict
    status: str = "pending"
    decided_at: int = 0
    decided_by: str = ""
    reason: str = ""


class CorruptError(Exception):
    """Payload buzilgan — 500, pending qoladi (operator ko'radi)."""


class FileApprovalStore:
    """Nom tarixiy (test/conftest mosligi uchun). Ichida SQLite."""

    def __init__(self, path=None) -> None:
        pass

    def submit(self, tenant: str, kind: str, agent_id: str, summary: str,
               payload: dict, channel: str = "") -> Approval:
        from . import storage

        uid = payload.get("update_id")
        with storage.tx() as c:
            if uid is not None:  # faqat pending ichida dedup (qarorlidan keyin — yangi)
                for r in c.execute("SELECT * FROM approvals WHERE tenant=? AND status='pending'",
                                   (tenant,)).fetchall():
                    try:
                        p = json.loads(r["payload"])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if p.get("update_id") == uid and p.get("channel", "") == channel:
                        return Approval(id=r["id"], tenant=r["tenant"], kind=r["kind"],
                                        agent_id=r["agent"], summary=r["summary"],
                                        payload=p, status=r["status"],
                                        decided_at=r["decided_at"] or 0,
                                        decided_by=r["decided_by"] or "",
                                        reason=r["reason"] or "")
            ap = Approval(
                id=f"{tenant}-{uuid.uuid4().hex[:12]}",
                tenant=tenant, kind=kind, agent_id=agent_id, summary=summary, payload=payload,
            )
            full_payload = {**payload, "channel": channel}
            ap.payload = full_payload
            c.execute(
                "INSERT INTO approvals(id,tenant,kind,agent,summary,payload,status,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (ap.id, tenant, kind, agent_id, summary, json.dumps(full_payload, ensure_ascii=False),
                 "pending", int(time.time())),
            )
            return ap

    def pending(self, tenant: str, limit: int = 100) -> list[dict]:
        c = storage.db()
        out = []
        for r in c.execute(
                "SELECT id,tenant,kind,agent,summary,status,decided_at,decided_by,reason"
                " FROM approvals WHERE tenant=? AND status='pending' ORDER BY created_at LIMIT ?",
                (tenant, limit)).fetchall():
            out.append({"id": r["id"], "tenant": r["tenant"], "kind": r["kind"],
                        "agent_id": r["agent"], "summary": _mask_phone(r["summary"] or ""),
                        "status": r["status"], "decided_at": r["decided_at"] or 0,
                        "decided_by": r["decided_by"] or "", "reason": r["reason"] or ""})
        return out

    def get(self, approval_id: str) -> dict | None:
        from . import storage

        r = storage.db().execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"])
        except (json.JSONDecodeError, TypeError):
            d["payload"] = {}
        return d

    def find_pending_by_update(self, tenant: str, channel: str, update_id: int) -> dict | None:
        c = storage.db()
        for r in c.execute("SELECT * FROM approvals WHERE tenant=? AND status='pending'",
                           (tenant,)).fetchall():
            try:
                p = json.loads(r["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            if p.get("channel", "") == channel and p.get("update_id") == update_id:
                d = dict(r)
                d["payload"] = p
                return d
        return None

    def decide(self, approval_id: str, decision: str, reason: str = "", actor: str = "legacy-admin") -> dict:
        """Avval ish (outbox), keyin belgi. Xato bo'lsa pending qoladi."""
        if decision not in ("approved", "rejected"):
            raise ValueError("decision: approved|rejected")
        from .orders import Order, validate_order_payload
        from .packs import load_pack

        with _lock:
            c = storage.db()
            r = c.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if r is None:
                raise LookupError("not found")
            if r["status"] != "pending":
                raise ValueError("already decided")
            try:
                payload = json.loads(r["payload"])
            except (json.JSONDecodeError, TypeError):
                raise CorruptError("payload buzilgan")
            order_id = ""
            if r["kind"] == "order":
                try:
                    validate_order_payload(payload, load_pack(r["tenant"]))
                except (ValueError, ValidationError) as e:
                    raise CorruptError(f"pack'ga mos emas: {e}")
                from .ladder import LadderStore as _LS

                before = _LS().level(r["agent"])
                if decision == "approved":
                    try:
                        order = Order(**{k: v for k, v in payload.items() if k != "channel"})
                        order.id = "approval-" + approval_id
                        order_id = _outbox_get().append(order)
                    except ValidationError as e:
                        raise CorruptError(f"buyurtma yaroqsiz: {e}")
                    after = _LS().record(r["agent"], True)
                else:
                    after = _LS().record(r["agent"], False)
                if after != before:
                    from . import trace as _trace

                    _trace.log({"tenant": r["tenant"], "agent": r["agent"],
                                "action": f"ladder_{after}"})
            c.execute("UPDATE approvals SET status=?, decided_at=?, decided_by=?, reason=? WHERE id=?",
                      ("approved" if decision == "approved" else "rejected",
                       int(time.time()), actor, re.sub(r"[<>]", "", reason or "")[:200],
                       approval_id))
            c.commit()
            return {"id": r["id"], "tenant": r["tenant"], "kind": r["kind"], "agent_id": r["agent"],
                    "summary": r["summary"], "status": "approved" if decision == "approved" else "rejected"}, order_id


_store: FileApprovalStore | None = None


def _store_get() -> FileApprovalStore:
    global _store
    if _store is None:
        _store = FileApprovalStore()
    return _store


_outbox = None


def _outbox_get():
    global _outbox
    if _outbox is None:
        from .tools import CsvOrderStore

        _outbox = CsvOrderStore()
    return _outbox


_ladder = None


def _ladder_get():
    global _ladder
    if _ladder is None:
        from .ladder import LadderStore

        _ladder = LadderStore()
    return _ladder


class DecideRequest(BaseModel):
    decision: str
    reason: str = ""


@router.get("/approvals/pending")
def pending_list(
    request: Request,
    tenant: str = "demo-retail",
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    from .security import scope_ok

    if not scope_ok(tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return {"pending": _store_get().pending(tenant)}


@router.post("/approvals/{approval_id}/decide")
def decide(
    approval_id: str,
    req: DecideRequest,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    from .security import auth_role, scope_ok

    rec = _store_get().get(approval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="topilmadi")
    role = auth_role(x_admin_token, request)
    if role != "super" and rec["tenant"] != role:
        raise HTTPException(status_code=404, detail="topilmadi")  # mavjudligini bildirmaymiz
    if not scope_ok(rec["tenant"], x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    if req.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="decision: approved|rejected")
    try:
        from .auth import verify_claims
        authorization = request.headers.get("Authorization", "")
        actor = verify_claims(authorization[7:])["sub"] if authorization.startswith("Bearer ") else "bootstrap-admin"
        rec, order_id = _store_get().decide(approval_id, req.decision, req.reason, actor)
    except LookupError:
        raise HTTPException(status_code=404, detail="topilmadi")
    except ValueError:
        raise HTTPException(status_code=409, detail="allaqachon hal qilingan")
    except CorruptError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "status": rec["status"], "order_id": order_id}
