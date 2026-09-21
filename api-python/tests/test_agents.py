"""Agent xaritasi API (docs U1): pack agentlari + ladder darajasi + bugungi yuk."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_agents_lists_pack_agents():
    r = client.get("/agents", params={"tenant": "demo-retail"})
    assert r.status_code == 200
    agents = r.json()["agents"]
    assert len(agents) == 6
    ids = {a["id"] for a in agents}
    assert "sales.order_taker" in ids
    for a in agents:
        assert a["ladder"] in ("human_led", "human_assisted", "autonomous")
        assert "pending" in a and "done_today" in a


def test_agents_scoped():
    tok = client.post("/auth/token", json={"tenant_id": "tenant-x"}).json()["access_token"]
    r = client.get("/agents", params={"tenant": "demo-retail"},
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_agents_unknown_tenant_404():
    r = client.get("/agents", params={"tenant": "nope"})
    assert r.status_code == 404


def test_agents_pending_and_done_counts():
    from fastapi.testclient import TestClient as TC

    c = TC(app)
    r = c.post("/webhooks/telegram", params={"tenant": "demo-retail"},
               json={"update_id": 1301, "message": {"message_id": 1301, "chat": {"id": 1},
                      "text": "/buy KB001 1 chilonzor +998901234567"}},
               headers={"X-Telegram-Bot-Api-Secret-Token": "dev-webhook-secret"})
    aid = r.json()["approval_id"]
    g1 = client.get("/agents", params={"tenant": "demo-retail"}).json()["agents"]
    ot1 = next(a for a in g1 if a["id"] == "sales.order_taker")
    assert ot1["pending"] == 1 and ot1["done_today"] == 0
    c.post(f"/approvals/{aid}/decide", json={"decision": "approved"})
    g2 = client.get("/agents", params={"tenant": "demo-retail"}).json()["agents"]
    ot2 = next(a for a in g2 if a["id"] == "sales.order_taker")
    assert ot2["pending"] == 0 and ot2["done_today"] == 1
