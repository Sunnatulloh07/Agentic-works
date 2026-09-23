"""Security regression tests (audit 2026-09-12).

Moved here from the retired ``tests/`` suite on 2026-09-22: these two gates test
live code (``app.packs`` traversal refusal and the ``/auth/token`` admin gate),
but they need pydantic/FastAPI, so they cannot live in ``runtime_tests/``.
The ``_isolated_db`` fixture below replaces the retired ``tests/conftest.py``
``_isolated_stores`` autouse fixture: without it the ``/auth/token`` gate would
open the developer's real ``data/app.db``.
"""
import pytest

from app.packs import PackError, load_pack


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB", str(tmp_path / "app.db"))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)


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
