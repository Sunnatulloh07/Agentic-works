"""Final review'dan qolgan kritik yo'llar (2026-09-12)."""
import csv
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "dev-webhook-secret")

from app.main import app  # noqa: E402

client = TestClient(app)
SECRET = {"X-Telegram-Bot-Api-Secret-Token": "dev-webhook-secret"}


def _tg(uid, text, tenant="demo-retail"):
    return client.post("/webhooks/telegram", params={"tenant": tenant},
                       json={"update_id": uid, "message": {"message_id": uid,
                              "chat": {"id": 1}, "text": text}}, headers=SECRET)


@pytest.mark.parametrize("text", [
    "/buy KB001 0 chilonzor +998901234567",
    "/buy KB001 100 chilonzor +998901234567",
    "/buy KB001 abc chilonzor +998901234567",
    "/buy KB001 1 chilonzor 12345",
    "/buy KB001 1 chilonzor +99890123456789",
    "/buy KB001 1 nowhere +998901234567",
    "/buy KB001 1",
])
def test_buy_negatives_422(text):
    assert _tg(900, text).status_code == 422


def test_cross_tenant_pending_isolation():
    from app import approvals as appr

    appr._store_get().submit("tenant-a", "order", "t:x", "s",
                             {"tenant": "tenant-a", "update_id": 1, "phone": "+998900000001",
                              "product_id": "KB001", "qty": 1, "branch_id": "chilonzor"})
    rows = client.get("/approvals/pending", params={"tenant": "tenant-b"}).json()["pending"]
    assert rows == []
    assert all(a["tenant"] == "tenant-b" for a in rows)
    rows_a = client.get("/approvals/pending", params={"tenant": "tenant-a"}).json()["pending"]
    assert len(rows_a) == 1


def test_me_expired_token_401():
    import jwt as _jwt

    from app.auth import SECRET as _SECRET

    bad = _jwt.encode({"tenant_id": "x", "exp": 1}, _SECRET, algorithm="HS256")
    r = client.get("/me", headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401


def test_csv_formula_injection_escaped():
    from app import approvals as appr
    from app.orders import Order

    store = appr._outbox
    oid = store.append(Order(tenant="demo-retail", update_id=5, customer="=CMD(1)",
                             phone="+998900000001", product_id="KB001", qty=1, branch_id="chilonzor"))
    assert oid
    with store.path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["customer"] == "'=CMD(1)"


def test_trace_scrubs_nested():
    from app import trace

    trace.log({"a": {"phone": "+998901112233"}, "list": ["+998902223344"]})
    import os as _os

    content = open(_os.environ["TRACE_PATH"], encoding="utf-8").read()
    assert "+998901112233" not in content
    assert content.count("+998***") >= 2


def test_runner_queue_full_429(monkeypatch):
    import app.runner_ws as rws

    monkeypatch.setattr(rws, "MAX_QUEUE", 1)
    client.post("/runner/exec", json={"tenant": "demo-retail", "tool": "x", "params": {}})
    r = client.post("/runner/exec", json={"tenant": "demo-retail", "tool": "x", "params": {}})
    assert r.status_code == 429


def test_prod_without_admin_token_forbidden(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    r = client.post("/auth/token", json={"tenant_id": "x"})
    assert r.status_code == 403
    assert r.json()["detail"] == "auth sozlanmagan"
