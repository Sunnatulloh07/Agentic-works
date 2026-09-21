"""Stage 6: runner WS + navbat + kill switch (docs §3.5)."""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "dev-webhook-secret")

from app.main import app  # noqa: E402

client = TestClient(app)


def _token(tenant="demo-retail"):
    return client.post("/auth/token", json={"tenant_id": tenant}).json()["access_token"]


@pytest.fixture(autouse=True)
def _clean_runner():
    # Izolyatsiya conftest'da (har testga alohida APP_DB) — bu fixture tarixiy.
    yield


def test_ws_rejects_bad_token():
    with pytest.raises(Exception):
        with client.websocket_connect("/runner/ws") as ws:
            ws.send_json({"token": "bad"})
            ws.receive_json()


def test_heartbeat_registers_and_status_online():
    with client.websocket_connect("/runner/ws") as ws:
        ws.send_json({"token": _token()})
        ws.send_json({"type": "heartbeat"})
        ack = ws.receive_json()
        assert ack["type"] == "heartbeat_ack"
        assert ack["stopped"] is False
    s = client.get("/runner/status", params={"tenant": "demo-retail"})
    assert s.json()["online"] is True


def test_exec_roundtrip_via_heartbeat():
    ex = client.post("/runner/exec", json={"tenant": "demo-retail", "tool": "fs.list",
                                           "params": {"dir": "C:/Hisobotlar"}})
    tid = ex.json()["task_id"]
    with client.websocket_connect("/runner/ws") as ws:
        ws.send_json({"token": _token()})
        ws.send_json({"type": "heartbeat"})
        ack = ws.receive_json()
        assert len(ack["tasks"]) == 1
        assert ack["tasks"][0]["id"] == tid
        ws.send_json({"type": "result", "id": tid, "ok": True, "entries": ["a.xlsx"]})
        assert ws.receive_json()["type"] == "result_ack"
    r = client.get(f"/runner/result/{tid}")
    assert r.json()["result"]["entries"] == ["a.xlsx"]


def test_result_missing_404():
    assert client.get("/runner/result/nope", params={"tenant": "demo-retail"}).status_code == 404


def test_cross_tenant_result_rejected():
    ex = client.post("/runner/exec", json={"tenant": "tenant-a", "tool": "fs.list", "params": {}})
    tid = ex.json()["task_id"]
    with client.websocket_connect("/runner/ws") as ws:
        ws.send_json({"token": _token("tenant-b")})
        ws.send_json({"type": "heartbeat"})
        ws.receive_json()
        ws.send_json({"type": "result", "id": tid, "ok": True})
        assert ws.receive_json()["type"] == "error"
    r = client.get(f"/runner/result/{tid}", params={"tenant": "tenant-b"})
    assert r.status_code == 404


def test_stop_via_chat_explains_operator_only():
    """Ruling: masofadan /stop O'CHIQ (har kim DoS qilardi) — faqat admin panel/API."""
    r = client.post("/webhooks/telegram",
                    json={"update_id": 701, "message": {"message_id": 701, "chat": {"id": 1}, "text": "/stop"}},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "dev-webhook-secret"})
    assert "operator panelidan" in r.json()["reply"]
    s = client.get("/runner/status", params={"tenant": "demo-retail"})
    assert s.json()["stopped"] is False
    client.post("/runner/stop", params={"tenant": "demo-retail"})
    client.post("/runner/resume", params={"tenant": "demo-retail"})
    s2 = client.get("/runner/status", params={"tenant": "demo-retail"})
    assert s2.json()["stopped"] is False


def test_expired_heartbeat_token_closes():
    import jwt as _jwt

    from app.auth import SECRET

    bad = _jwt.encode({"tenant_id": "demo-retail", "exp": 1}, SECRET, algorithm="HS256")
    with client.websocket_connect("/runner/ws") as ws:
        ws.send_json({"token": _token()})
        ws.send_json({"type": "heartbeat"})
        ws.receive_json()
        ws.send_json({"type": "heartbeat", "token": bad})
        ack = ws.receive_json()
        assert ack.get("auth") is False


def test_kill_switch_stops_runner():
    client.post("/runner/stop", params={"tenant": "demo-retail"})
    with client.websocket_connect("/runner/ws") as ws:
        ws.send_json({"token": _token()})
        ws.send_json({"type": "heartbeat"})
        ack = ws.receive_json()
        assert ack["stopped"] is True
    s = client.get("/runner/status", params={"tenant": "demo-retail"})
    assert s.json()["stopped"] is True


def test_exec_requires_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "adm")
    r = client.post("/runner/exec", json={"tenant": "demo-retail", "tool": "x", "params": {}})
    assert r.status_code == 403

