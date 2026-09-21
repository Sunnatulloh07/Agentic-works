"""Operator API testlari: /orders + /simulate (scope bilan)."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
SECRET = {"X-Telegram-Bot-Api-Secret-Token": "dev-webhook-secret"}


def test_orders_lists_approved():
    client.post("/webhooks/telegram",
                json={"update_id": 1201, "message": {"message_id": 1201, "chat": {"id": 1},
                       "text": "/buy KB001 1 chilonzor +998901234567"}},
                headers=SECRET)
    aid = client.get("/approvals/pending", params={"tenant": "demo-retail"}).json()["pending"][0]["id"]
    client.post(f"/approvals/{aid}/decide", json={"decision": "approved"})
    r = client.get("/orders", params={"tenant": "demo-retail"})
    assert r.status_code == 200
    assert len(r.json()["orders"]) == 1
    assert r.json()["orders"][0]["product_id"] == "KB001"


def test_orders_scoped():
    tok = client.post("/auth/token", json={"tenant_id": "tenant-x"}).json()["access_token"]
    r = client.get("/orders", params={"tenant": "demo-retail"},
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_simulate_answers_like_customer():
    r = client.post("/simulate", json={"tenant": "demo-retail", "text": "KB001 narxi?"})
    assert r.status_code == 200
    assert "350000" in r.json()["reply"]


def test_simulate_empty_422():
    r = client.post("/simulate", json={"tenant": "demo-retail", "text": "   "})
    assert r.status_code == 422
