"""Stage 2: ladder + tasdiq navbati + trace (docs F5, §4 audit)."""
import json
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "dev-webhook-secret")

from app.main import app  # noqa: E402

client = TestClient(app)
SECRET = {"X-Telegram-Bot-Api-Secret-Token": "dev-webhook-secret"}


def _buy(uid: int, text: str = "/buy KB001 2 chilonzor +998901234567 Anvar") -> dict:
    r = client.post(
        "/webhooks/telegram",
        params={"tenant": "demo-retail"},
        json={"update_id": uid, "message": {"message_id": uid, "chat": {"id": 1}, "text": text}},
        headers=SECRET,
    )
    assert r.status_code == 200
    return r.json()


def test_buy_creates_pending_approval():
    body = _buy(201)
    assert "tasdiq" in body["reply"].lower()
    assert body["approval_id"]


def test_pending_list_shows_it():
    body = _buy(202)
    r = client.get("/approvals/pending", params={"tenant": "demo-retail"})
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()["pending"]]
    assert body["approval_id"] in ids


def test_approve_response_and_pending_gone():
    body = _buy(203)
    aid = body["approval_id"]
    r = client.post(f"/approvals/{aid}/decide", json={"decision": "approved"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["order_id"].startswith("demo-retail-")
    r2 = client.post(f"/approvals/{aid}/decide", json={"decision": "approved"})
    assert r2.status_code == 409
    r3 = client.get("/approvals/pending", params={"tenant": "demo-retail"})
    assert all(a["id"] != aid for a in r3.json()["pending"])


def test_approved_order_row_content():
    import csv

    from app import approvals as appr

    body = _buy(206)
    client.post(f"/approvals/{body['approval_id']}/decide", json={"decision": "approved"})
    with appr._outbox.path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["product_id"] == "KB001"
    assert rows[0]["qty"] == "2"
    assert rows[0]["branch_id"] == "chilonzor"
    assert rows[0]["status"] == "new"


def test_pending_masks_phone():
    body = _buy(207)
    r = client.get("/approvals/pending", params={"tenant": "demo-retail"})
    row = next(a for a in r.json()["pending"] if a["id"] == body["approval_id"])
    assert "+998***" in row["summary"]
    assert "+998901234567" not in row["summary"]


def test_submit_idempotent_same_update():
    from app import approvals as appr

    s = appr._store_get()
    payload = {"tenant": "demo-retail", "update_id": 999, "phone": "+998900000001",
               "product_id": "KB001", "qty": 1, "branch_id": "chilonzor"}
    a1 = s.submit("demo-retail", "order", "t:a", "s", payload)
    a2 = s.submit("demo-retail", "order", "t:a", "s", payload)
    assert a1.id == a2.id
    assert len(s.pending("demo-retail")) == 1


def test_reject_writes_nothing():
    from app import approvals as appr

    body = _buy(204)
    r = client.post(f"/approvals/{body['approval_id']}/decide", json={"decision": "rejected"})
    assert r.json()["status"] == "rejected"
    assert not appr._outbox.path.is_file()  # rad etilganda outbox bo'sh
    pending = client.get("/approvals/pending", params={"tenant": "demo-retail"}).json()["pending"]
    assert all(a["id"] != body["approval_id"] for a in pending)


def test_decide_unknown_id_404():
    r = client.post("/approvals/nope-123/decide", json={"decision": "approved"})
    assert r.status_code == 404


def test_ladder_promotes_on_clean_run(tmp_path):
    from app.ladder import LadderStore

    store = LadderStore(min_tasks=3)
    assert store.level("sales.order_taker") == "human_led"
    store.record("sales.order_taker", True)
    store.record("sales.order_taker", True)
    assert store.level("sales.order_taker") == "human_led"
    store.record("sales.order_taker", True)
    assert store.level("sales.order_taker") == "human_assisted"


def test_ladder_holds_on_errors(tmp_path):
    from app.ladder import LadderStore

    store = LadderStore(min_tasks=3)
    store.record("a", True)
    store.record("a", False)
    store.record("a", True)
    assert store.level("a") == "human_led"


def test_ladder_demotes_on_many_errors(tmp_path):
    from app.ladder import LadderStore

    store = LadderStore(min_tasks=3)
    for _ in range(3):
        store.record("a", True)
    assert store.level("a") == "human_assisted"
    for _ in range(3):
        store.record("a", False)
    assert store.level("a") == "human_led"


def test_owner_can_set_level():
    r = client.post("/ladder/demo-retail/sales.order_taker", json={"level": "autonomous"})
    assert r.status_code == 200
    assert r.json()["level"] == "autonomous"
    g = client.get("/ladder/demo-retail/sales.order_taker")
    assert g.json()["level"] == "autonomous"
    bad = client.post("/ladder/demo-retail/x", json={"level": "boss"})
    assert bad.status_code == 422


def test_429_when_daily_limit_exceeded(monkeypatch):
    monkeypatch.setenv("DAILY_LIMIT_PER_TENANT", "2")
    for uid in (301, 302):
        r = client.post(
            "/webhooks/telegram",
            params={"tenant": "demo-retail"},
            json={"update_id": uid, "message": {"message_id": uid, "chat": {"id": 1}, "text": "/start"}},
            headers=SECRET,
        )
        assert r.status_code == 200
    r = client.post(
        "/webhooks/telegram",
        params={"tenant": "demo-retail"},
        json={"update_id": 303, "message": {"message_id": 303, "chat": {"id": 1}, "text": "/start"}},
        headers=SECRET,
    )
    assert r.status_code == 429


def test_trace_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_PATH", str(tmp_path / "t.jsonl"))
    _buy(205)
    lines = (tmp_path / "t.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert lines
    evt = json.loads(lines[-1])
    assert evt["update_key"] == "205"
    assert evt["tenant"] == "demo-retail"
    assert evt["agent"] == "demo-retail:sales.order_taker"
    assert evt["action"] == "order_pending"
    assert "ms" in evt


def test_trace_scrubs_phone(tmp_path, monkeypatch):
    from app import trace

    monkeypatch.setenv("TRACE_PATH", str(tmp_path / "t.jsonl"))
    trace.log({"note": "call +998901112233 now"})
    content = (tmp_path / "t.jsonl").read_text(encoding="utf-8")
    assert "+998901112233" not in content
    assert "+998***" in content
