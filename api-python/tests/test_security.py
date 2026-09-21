"""Security regression tests (audit 2026-09-12)."""
import pytest

from app.packs import PackError, load_pack


@pytest.mark.parametrize("evil", ["../../etc", "..", "a/b", "/abs", "a..b/../c", ""])
def test_load_pack_rejects_traversal(evil):
    with pytest.raises(PackError):
        load_pack(evil)


def test_auth_token_admin_gate(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    monkeypatch.setenv("ADMIN_TOKEN", "s3cr3t")
    r1 = client.post("/auth/token", json={"tenant_id": "x"})
    assert r1.status_code == 403
    r2 = client.post(
        "/auth/token", json={"tenant_id": "x"}, headers={"X-Admin-Token": "s3cr3t"}
    )
    assert r2.status_code == 200
