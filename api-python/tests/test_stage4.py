"""Stage 4: Instagram + marketing pack + hot-lead/stat runtime."""
import json
import os

from fastapi.testclient import TestClient

os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "dev-webhook-secret")

from app.main import app  # noqa: E402
from app.packs import load_pack  # noqa: E402

client = TestClient(app)


def _ig(mid: str, text: str, sender: str = "999") -> dict:
    return {"entry": [{"messaging": [{"sender": {"id": sender},
            "message": {"mid": mid, "text": text}}]}]}


def test_instagram_verify_ok():
    r = client.get("/webhooks/instagram", params={"hub.mode": "subscribe",
                   "hub.verify_token": "dev-meta-verify", "hub.challenge": "CHAL123"})
    assert r.status_code == 200
    assert r.text == "CHAL123"


def test_instagram_verify_forbidden():
    r = client.get("/webhooks/instagram", params={"hub.mode": "subscribe",
                   "hub.verify_token": "wrong", "hub.challenge": "x"})
    assert r.status_code == 403


def test_instagram_message_answers_price():
    r = client.post("/webhooks/instagram", params={"tenant": "demo-retail"}, json=_ig("m1", "KB001 narxi?"))
    assert r.status_code == 200
    assert "350000" in r.json()["reply"]


def test_instagram_duplicate_mid_returns_same_approval():
    r1 = client.post("/webhooks/instagram", params={"tenant": "demo-retail"},
                     json=_ig("m2", "/buy KB001 1 chilonzor +998901234567"))
    r2 = client.post("/webhooks/instagram", params={"tenant": "demo-retail"},
                     json=_ig("m2", "/buy KB001 1 chilonzor +998901234567"))
    assert r2.json().get("duplicate") is True
    assert r2.json()["approval_id"] == r1.json()["approval_id"]
    pending = client.get("/approvals/pending", params={"tenant": "demo-retail"}).json()["pending"]
    assert len(pending) == 1


def test_instagram_signed_request_ok(monkeypatch):
    import hashlib
    import hmac
    import json as _json

    monkeypatch.setenv("META_APP_SECRET", "s3cr3t")
    body = _json.dumps(_ig("m9", "KB001 narxi?")).encode()
    sig = "sha256=" + hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
    r = client.post("/webhooks/instagram", params={"tenant": "demo-retail"}, content=body,
                    headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
    assert r.status_code == 200
    assert "350000" in r.json()["reply"]
    bad = client.post("/webhooks/instagram", params={"tenant": "demo-retail"}, content=body,
                      headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=wrong"})
    assert bad.status_code == 401


def test_instagram_buy_goes_to_approval():
    r = client.post("/webhooks/instagram", params={"tenant": "demo-retail"},
                    json=_ig("m3", "/buy KB002 1 yunusobod +998907654321"))
    assert r.status_code == 200
    assert r.json()["approval_id"]


def test_marketing_pack_loads_4_agents():
    pack = load_pack("marketing")
    assert len(pack.agents) == 4
    ids = {a.id for a in pack.agents}
    assert ids == {"marketing.content_editor", "marketing.ad_creative",
                   "marketing.researcher", "marketing.script_doctor"}


def test_fallback_logs_hot_lead():
    from app import hotlead
    from fastapi.testclient import TestClient as TC

    c = TC(app)
    c.post("/webhooks/telegram",
           json={"update_id": 601, "message": {"message_id": 601, "chat": {"id": 1}, "text": "tushunarsiz gap xyz"}},
           headers={"X-Telegram-Bot-Api-Secret-Token": "dev-webhook-secret"})
    leads = hotlead.pending_leads("demo-retail")
    assert len(leads) == 1
    assert leads[0]["channel"] == "telegram"
    masked = client.get("/leads/pending", params={"tenant": "demo-retail"}).json()["pending"]
    assert len(masked) == 1


def test_stats_counts():
    r = client.post("/webhooks/telegram",
                    json={"update_id": 602, "message": {"message_id": 602, "chat": {"id": 1},
                           "text": "/buy KB001 1 chilonzor +998901234567"}},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "dev-webhook-secret"})
    aid = r.json()["approval_id"]
    client.post(f"/approvals/{aid}/decide", json={"decision": "approved"})
    s = client.get("/stats/daily", params={"tenant": "demo-retail"})
    assert s.status_code == 200
    body = s.json()
    assert body["orders_total"] == 1
    assert body["orders_new"] == 1
    assert body["pending_approvals"] == 0
