"""Stage 6: runner — heartbeat ro'yxat, vazifa navbati, kill switch.

Runner'da miya yo'q: server vazifa beradi (navbat), runner bajarib
natija qaytaradi. Har heartbeat'da killed bayrog'i tekshiriladi.
"""
import asyncio
import json
import threading
import time
import uuid

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from .auth import verify_token
from .security import scope_ok

router = APIRouter()
MAX_QUEUE = 100
MAX_RESULTS = 1000
_lock = threading.Lock()


def _seen_set(tenant: str) -> None:
    from . import storage

    c = storage.db()
    c.execute("INSERT OR REPLACE INTO rseen(tenant, ts) VALUES(?,?)", (tenant, int(time.time())))
    c.commit()


def _is_killed(tenant: str) -> bool:
    from . import storage

    return storage.db().execute("SELECT 1 FROM rkilled WHERE tenant=?", (tenant,)).fetchone() is not None


def _set_killed(tenant: str, killed: bool) -> None:
    from . import storage

    c = storage.db()
    if killed:
        c.execute("INSERT OR REPLACE INTO rkilled(tenant) VALUES(?)", (tenant,))
    else:
        c.execute("DELETE FROM rkilled WHERE tenant=?", (tenant,))
    c.commit()


def _queued(tenant: str) -> list[dict]:
    from . import storage

    return [dict(r) for r in storage.db().execute(
        "SELECT id, tool, params FROM rtasks WHERE tenant=? AND status='queued' ORDER BY enqueued_at",
        (tenant,)).fetchall()]


class ExecRequest(BaseModel):
    tenant: str
    tool: str
    params: dict = {}


@router.websocket("/runner/ws")
async def runner_ws(ws: WebSocket):
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=10)
    except Exception:
        await ws.close(code=4400)
        return
    try:
        tenant = verify_token(str(hello.get("token", "")))
    except Exception:
        await ws.close(code=4401)
        return
    _seen_set(tenant)
    try:
        while True:
            msg = await ws.receive_json()
            _seen_set(tenant)
            if msg.get("type") == "heartbeat":
                tok = msg.get("token")
                if tok:
                    try:
                        if verify_token(str(tok)) != tenant:
                            raise ValueError("tenant mismatch")
                    except Exception:
                        await ws.send_json({"type": "heartbeat_ack", "auth": False})
                        await ws.close(code=4401)
                        return
                with _lock:
                    tasks = _queued(tenant)  # ack kelmaguncha qayta uzatiladi
                await ws.send_json({"type": "heartbeat_ack", "stopped": _is_killed(tenant), "tasks": tasks})
            elif msg.get("type") == "result":
                mid = str(msg.get("id", ""))
                from . import storage

                row = storage.db().execute("SELECT tenant FROM rtasks WHERE id=?", (mid,)).fetchone()
                if row is None or row["tenant"] != tenant:
                    await ws.send_json({"type": "error", "detail": "begona task id"})
                else:
                    with _lock:
                        c = storage.db()
                        c.execute("INSERT OR REPLACE INTO rresults(id, payload) VALUES(?,?)",
                                  (mid, json.dumps(msg, ensure_ascii=False)))
                        c.execute("UPDATE rtasks SET status='done' WHERE id=?", (mid,))
                        n = c.execute("SELECT COUNT(*) n FROM rresults").fetchone()["n"]
                        if n > MAX_RESULTS:
                            c.execute("DELETE FROM rresults WHERE id IN "
                                      "(SELECT id FROM rresults LIMIT ?)", (n - MAX_RESULTS,))
                        c.commit()
                    await ws.send_json({"type": "result_ack", "id": mid})
            else:
                await ws.send_json({"type": "error", "detail": "unknown message"})
    except WebSocketDisconnect:
        return


@router.post("/runner/exec")
def runner_exec(req: ExecRequest, request: Request,
                x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict:
    if not scope_ok(req.tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    with _lock:
        from . import storage

        with storage.tx() as c:
            q = c.execute("SELECT COUNT(*) n FROM rtasks WHERE tenant=? AND status='queued'",
                          (req.tenant,)).fetchone()["n"]
            if q >= MAX_QUEUE:
                full = True
            else:
                full = False
                tid = f"{req.tenant}-{uuid.uuid4().hex[:12]}"
                c.execute("INSERT INTO rtasks(id,tenant,tool,params,status,enqueued_at) VALUES(?,?,?,?,?,?)",
                          (tid, req.tenant, req.tool, json.dumps(req.params, ensure_ascii=False),
                           "queued", int(time.time())))
    if full:
        from . import storage as _st

        with _st.tx() as c2:
            c2.execute("INSERT INTO rmeta(key, value) VALUES(?,1) "
                       "ON CONFLICT(key) DO UPDATE SET value=value+1", (f"dropped:{req.tenant}",))
        raise HTTPException(status_code=429, detail="navbat to'la")
    return {"ok": True, "task_id": tid}


@router.get("/runner/result/{task_id}")
def runner_result(task_id: str, request: Request, tenant: str = "demo-retail",
                  x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict:
    if not scope_ok(tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    if _task_tenant_of(task_id) != tenant:
        raise HTTPException(status_code=404, detail="natija hali yo'q")
    from . import storage

    rec = storage.db().execute("SELECT payload FROM rresults WHERE id=?", (task_id,)).fetchone()
    if rec is None:
        raise HTTPException(status_code=404, detail="natija hali yo'q")
    return {"ok": True, "result": json.loads(rec["payload"])}


def _task_tenant_of(task_id: str) -> str | None:
    from . import storage

    row = storage.db().execute("SELECT tenant FROM rtasks WHERE id=?", (task_id,)).fetchone()
    return row["tenant"] if row else None


@router.post("/runner/stop")
def runner_stop(request: Request, tenant: str = "demo-retail",
                x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict:
    """Kill switch (Telegram /stop ham shu yerga keladi)."""
    if not scope_ok(tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    _set_killed(tenant, True)
    return {"ok": True, "stopped": True}


@router.post("/runner/resume")
def runner_resume(request: Request, tenant: str = "demo-retail",
                  x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict:
    if not scope_ok(tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    _set_killed(tenant, False)
    return {"ok": True, "stopped": False}


@router.get("/runner/status")
def runner_status(request: Request, tenant: str = "demo-retail",
                  x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict:
    if not scope_ok(tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    now = int(time.time())
    from . import storage

    row = storage.db().execute("SELECT ts FROM rseen WHERE tenant=?", (tenant,)).fetchone()
    last = row["ts"] if row else 0
    qrow = storage.db().execute("SELECT COUNT(*) n FROM rtasks WHERE tenant=? AND status='queued'",
                                (tenant,)).fetchone()
    drop = storage.db().execute("SELECT value FROM rmeta WHERE key=?",
                                (f"dropped:{tenant}",)).fetchone()
    return {"tenant": tenant, "online": now - last < 90, "last_seen": last,
            "stopped": _is_killed(tenant), "queued": qrow["n"] if qrow else 0,
            "dropped": drop["value"] if drop else 0}
