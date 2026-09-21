"""Stage 0: tenant JWT auth + isolation (brain-in-cloud, docs tamoyil 8)."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _token(tenant_id: str) -> str:
    resp = client.post("/auth/token", json={"tenant_id": tenant_id})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def test_me_requires_token():
    resp = client.get("/me")
    assert resp.status_code == 401


def test_me_returns_own_tenant():
    token = _token("demo-retail")
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == "demo-retail"


def test_tenants_are_isolated():
    token_a = _token("tenant-a")
    token_b = _token("tenant-b")
    me_a = client.get("/me", headers={"Authorization": f"Bearer {token_a}"}).json()
    me_b = client.get("/me", headers={"Authorization": f"Bearer {token_b}"}).json()
    assert me_a["tenant_id"] == "tenant-a"
    assert me_b["tenant_id"] == "tenant-b"
    assert me_a["tenant_id"] != me_b["tenant_id"]


def test_tampered_token_rejected():
    token = _token("demo-retail") + "tampered"
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
