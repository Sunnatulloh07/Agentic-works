"""Tenant authz (audit #2): JWT-bound tenant + per-tenant webhook secretlar.

/orqali: ?tenant= endi ishonchli emas — secret yoki JWT dan keladi.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
SECRET = {"X-Telegram-Bot-Api-Secret-Token": "dev-webhook-secret"}


def _jwt(tenant, admin=None):
    headers = {"X-Admin-Token": admin} if admin else {}
    return client.post("/auth/token", json={"tenant_id": tenant}, headers=headers).json()["access_token"]


def _buy(uid, tenant="demo-retail"):
    return client.post("/webhooks/telegram", params={"tenant": tenant},
                       json={"update_id": uid, "message": {"message_id": uid, "chat": {"id": 1},
                              "text": "/buy KB001 1 chilonzor +998901234567"}},
                       headers=SECRET).json()["approval_id"]


def test_jwt_reads_own_pending():
    _buy(1101)
    tok = _jwt("demo-retail")
    r = client.get("/approvals/pending", params={"tenant": "demo-retail"},
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert len(r.json()["pending"]) == 1


def test_jwt_cannot_read_other_tenant():
    _buy(1102)
    tok = _jwt("tenant-x")
    r = client.get("/approvals/pending", params={"tenant": "demo-retail"},
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_no_token_no_admin_denied(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "adm")
    assert client.get("/approvals/pending", params={"tenant": "demo-retail"}).status_code == 403
    assert client.get("/stats/daily", params={"tenant": "demo-retail"}).status_code == 403


def test_decide_scoped_to_tenant(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "adm")
    aid = _buy(1103)
    tok = _jwt("tenant-x", admin="adm")
    r = client.post(f"/approvals/{aid}/decide", json={"decision": "approved"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404  # begona tenant approve'ni ko'rmaydi ham
    r2 = client.post(f"/approvals/{aid}/decide", json={"decision": "approved"},
                     headers={"X-Admin-Token": "adm"})
    assert r2.status_code == 200


def test_expired_jwt_denied_on_scoped():
    import jwt as _jwt

    from app.auth import SECRET as _SECRET

    bad = _jwt.encode({"tenant_id": "demo-retail", "exp": 1}, _SECRET, algorithm="HS256")
    r = client.get("/approvals/pending", params={"tenant": "demo-retail"},
                   headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 403
    r2 = client.get("/stats/daily", params={"tenant": "demo-retail"},
                    headers={"Authorization": "Basic eA=="})
    assert r2.status_code == 403


def test_super_reads_other_tenant_stats():
    tok = _jwt("demo-retail")
    r = client.get("/stats/daily", params={"tenant": "other-tenant"},
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403  # tenant JWT faqat o'ziniki


def test_voice_tenant_scoped():
    tok = _jwt("tenant-x")
    r = client.post("/voice/speak", json={"text": "x"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_bearer_beats_admin_header(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "adm")
    tok = _jwt("tenant-x", admin="adm")
    r = client.get("/approvals/pending", params={"tenant": "demo-retail"},
                   headers={"Authorization": f"Bearer {tok}", "X-Admin-Token": "adm"})
    assert r.status_code == 403  # Bearer gapiradi (eskirgan/chet bo'lsa ham)


def test_per_tenant_webhook_secret(monkeypatch):
    monkeypatch.setenv("TENANT_SECRETS", "demo-retail:s3cr3t-tg")
    r = client.post("/webhooks/telegram", params={"tenant": "demo-retail"},
                    json={"update_id": 1104, "message": {"message_id": 1104, "chat": {"id": 1}, "text": "/start"}},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "dev-webhook-secret"})
    assert r.status_code == 401
    r2 = client.post("/webhooks/telegram", params={"tenant": "demo-retail"},
                     json={"update_id": 1104, "message": {"message_id": 1104, "chat": {"id": 1}, "text": "/start"}},
                     headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t-tg"})
    assert r2.status_code == 200
    r3 = client.post("/webhooks/telegram", params={"tenant": "other"},
                     json={"update_id": 1105, "message": {"message_id": 1105, "chat": {"id": 1}, "text": "/start"}},
                     headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t-tg"})
    assert r3.status_code == 403


def test_production_global_webhook_secret_cannot_choose_tenant(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "global-secret-1234")
    monkeypatch.delenv("TENANT_SECRETS", raising=False)
    monkeypatch.delenv("TELEGRAM_DEFAULT_TENANT", raising=False)

    r = client.post(
        "/webhooks/telegram",
        params={"tenant": "other-tenant"},
        json={"update_id": 1110, "message": {"chat": {"id": 1}, "text": "/start"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "global-secret-1234"},
    )

    assert r.status_code == 401
