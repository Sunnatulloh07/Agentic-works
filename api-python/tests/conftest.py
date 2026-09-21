"""Test izolyatsiyasi: har testga alohida APP_DB (SQLite) + tmp fayllar."""
import os
os.environ["ALLOW_INSECURE_DEV"] = "true"

import pytest

os.environ.setdefault("ENV", "test")
os.environ.setdefault("PIPELINE_MODE", "legacy")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "dev-webhook-secret")


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    from app import approvals as appr
    from app import cache
    from app import storage
    from app.tools import CsvOrderStore

    cache.reset()

    monkeypatch.setenv("APP_DB", str(tmp_path / "app.db"))
    monkeypatch.setattr(appr, "_store", appr.FileApprovalStore())
    monkeypatch.setattr(appr, "_outbox", CsvOrderStore(tmp_path / "orders.csv"))
    from app.ladder import LadderStore

    monkeypatch.setattr(appr, "_ladder", LadderStore())
    from app import ladder as ldr

    monkeypatch.setattr(ldr, "_ladder_singleton", LadderStore())
    monkeypatch.setenv("TRACE_PATH", str(tmp_path / "trace.jsonl"))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)  # dev-ochiq holat qulflangan
    storage.reset()
    yield
    storage.reset()
