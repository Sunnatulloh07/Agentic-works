"""Stage 1: Telegram sotuv-MVP (kalitsiz, offline test)."""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "dev-webhook-secret")

from app.main import app  # noqa: E402

client = TestClient(app)
SECRET = {"X-Telegram-Bot-Api-Secret-Token": "dev-webhook-secret"}


def _update(uid: int, text: str, chat: int = 777) -> dict:
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": chat}, "text": text}}


def test_webhook_rejects_no_secret():
    r = client.post("/webhooks/telegram", json=_update(1, "/start"))
    assert r.status_code == 401


def test_webhook_rejects_wrong_secret():
    r = client.post(
        "/webhooks/telegram",
        json=_update(1, "/start"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert r.status_code == 401


def test_start_greets_with_branches():
    r = client.post("/webhooks/telegram", json=_update(101, "/start"), headers=SECRET)
    assert r.status_code == 200
    assert "Chilonzor" in r.json()["reply"]


def test_price_question_answers_from_pack():
    r = client.post(
        "/webhooks/telegram", json=_update(102, "KB001 narxi qancha?"), headers=SECRET
    )
    assert r.status_code == 200
    assert "350000" in r.json()["reply"]


def test_buy_goes_to_approval_not_outbox():
    from app import approvals as appr

    r = client.post(
        "/webhooks/telegram",
        json=_update(103, "/buy KB001 2 chilonzor +998901234567 Anvar"),
        headers=SECRET,
    )
    assert r.status_code == 200
    assert "tasdiq" in r.json()["reply"].lower()
    assert r.json()["approval_id"]
    pending = client.get("/approvals/pending", params={"tenant": "demo-retail"}).json()["pending"]
    assert len(pending) == 1
    assert not appr._outbox.path.is_file()  # outbox'ga hali hech narsa yozilmadi


def test_duplicate_update_id_single_approval():
    body = _update(104, "/buy KB002 1 yunusobod +998907654321")
    r1 = client.post("/webhooks/telegram", json=body, headers=SECRET)
    r2 = client.post("/webhooks/telegram", json=body, headers=SECRET)
    assert r2.json().get("duplicate") is True
    pending = client.get("/approvals/pending", params={"tenant": "demo-retail"}).json()["pending"]
    assert len(pending) == 1
    assert r1.json()["approval_id"] == pending[0]["id"]


def test_buy_bad_product_rejected():
    r = client.post(
        "/webhooks/telegram", json=_update(105, "/buy XXX 1 chilonzor +99890"), headers=SECRET
    )
    assert r.status_code == 422


def test_unknown_text_fallback_to_operator():
    r = client.post(
        "/webhooks/telegram", json=_update(106, "salom aka ukamga sovg'a kerak"), headers=SECRET
    )
    assert r.status_code == 200
    assert "operator" in r.json()["reply"].lower()
