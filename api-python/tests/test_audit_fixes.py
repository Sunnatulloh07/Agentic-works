"""Audit fix'lar uchun testlar (2026-09-12 chuqur audit)."""
import csv
import os

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "dev-webhook-secret")

from app.main import app  # noqa: E402

client = TestClient(app)
SECRET = {"X-Telegram-Bot-Api-Secret-Token": "dev-webhook-secret"}


def _token(tenant="demo-retail"):
    return client.post("/auth/token", json={"tenant_id": tenant}).json()["access_token"]


def test_ws_close_code_4401_on_bad_token():
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/runner/ws") as ws:
            ws.send_json({"token": "bad"})
            ws.receive_json()
    assert e.value.code == 4401


def test_task_redelivered_until_result():
    ex = client.post("/runner/exec", json={"tenant": "demo-retail", "tool": "fs.list", "params": {}})
    tid = ex.json()["task_id"]
    with client.websocket_connect("/runner/ws") as ws:
        ws.send_json({"token": _token()})
        ws.send_json({"type": "heartbeat"})
        assert len(ws.receive_json()["tasks"]) == 1  # 1-uzatish
        ws.send_json({"type": "heartbeat"})
        assert len(ws.receive_json()["tasks"]) == 1  # natijasiz — qayta
        ws.send_json({"type": "result", "id": tid, "ok": True})
        ws.receive_json()
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["tasks"] == []  # natijadan keyin bo'sh


def test_queue_full_counts_dropped(monkeypatch):
    import app.runner_ws as rws

    monkeypatch.setattr(rws, "MAX_QUEUE", 1)
    client.post("/runner/exec", json={"tenant": "demo-retail", "tool": "x", "params": {}})
    assert client.post("/runner/exec", json={"tenant": "demo-retail", "tool": "x", "params": {}}).status_code == 429
    s = client.get("/runner/status", params={"tenant": "demo-retail"})
    assert s.json()["dropped"] == 1


def test_outbox_append_idempotent():
    from app import approvals as appr
    from app.orders import Order

    o = Order(tenant="demo-retail", update_id=77, phone="+998900000007",
              product_id="KB001", qty=1, branch_id="chilonzor")
    oid = appr._outbox.append(o)
    oid2 = appr._outbox.append(o)
    assert oid == oid2
    with appr._outbox.path.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["id"] == oid]
    assert len(rows) == 1


def test_decide_revalidates_tampered_payload():
    import json

    from app import approvals as appr
    from app import storage

    ap = appr._store_get().submit("demo-retail", "order", "t:x", "s",
                                  {"tenant": "demo-retail", "update_id": 78, "phone": "+998900000008",
                                   "product_id": "KB001", "qty": 1, "branch_id": "chilonzor"})
    c = storage.db()
    row = c.execute("SELECT payload FROM approvals WHERE id=?", (ap.id,)).fetchone()
    p = json.loads(row["payload"])
    p["qty"] = 999
    c.execute("UPDATE approvals SET payload=? WHERE id=?", (json.dumps(p), ap.id))
    c.commit()
    resp = client.post(f"/approvals/{ap.id}/decide", json={"decision": "approved"})
    assert resp.status_code == 500


def test_voice_requires_admin_when_configured(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "adm")
    assert client.post("/voice/speak", json={"text": "x"}).status_code == 403
    assert client.post("/voice/transcribe").status_code == 403


def test_resubmit_after_decide_creates_new_pending():
    """Ruling: qarordan keyin qayta yuborish — yangi pending (odam ko'radi)."""
    from app import approvals as appr

    payload = {"tenant": "demo-retail", "update_id": 1001, "phone": "+998900000009",
               "product_id": "KB001", "qty": 1, "branch_id": "chilonzor"}
    a1 = appr._store_get().submit("demo-retail", "order", "t:x", "s", payload, channel="telegram")
    appr._store_get().decide(a1.id, "rejected")
    a2 = appr._store_get().submit("demo-retail", "order", "t:x", "s", payload, channel="telegram")
    assert a2.id != a1.id
    assert len(appr._store_get().pending("demo-retail")) == 1


def test_tenant_name_validated():
    assert client.post("/auth/token", json={"tenant_id": "../x"}).status_code == 400
    assert client.post("/auth/token", json={"tenant_id": "   "}).status_code == 400
