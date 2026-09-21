"""Redis jonli testi — server bo'lmasa SKIP (unit testlar SQLite'da)."""
import os
import socket

import pytest

try:
    _s = socket.create_connection(("localhost", 6380), timeout=2)
    _s.close()
    REDIS_UP = True
except OSError:
    REDIS_UP = False

pytestmark = pytest.mark.skipif(not REDIS_UP, reason="redis:6380 o'chiq")


@pytest.fixture(autouse=True)
def _redis_env(monkeypatch):
    url = "redis://localhost:6380/15"
    assert url.startswith("redis://localhost:") and url.rsplit("/", 1)[-1] == "15"
    monkeypatch.setenv("REDIS_URL", url)
    from app import cache

    cache.reset()
    if cache.get() is not None:
        cache.get().flushdb()
    yield
    if cache.get() is not None:
        try:
            cache.get().flushdb()
        except Exception:
            pass
    cache.reset()


def test_redis_limit_counts():
    from app import limits

    assert limits.check_and_hit("t-redis") is True
    assert limits.check_and_hit("t-redis") is True


def test_redis_limit_blocks(monkeypatch):
    from app import limits

    monkeypatch.setenv("DAILY_LIMIT_PER_TENANT", "2")
    assert limits.check_and_hit("t-block") is True
    assert limits.check_and_hit("t-block") is True
    assert limits.check_and_hit("t-block") is False


def test_redis_warned_once(monkeypatch):
    from app import limits

    monkeypatch.setenv("DAILY_LIMIT_PER_TENANT", "10")
    for _ in range(8):
        limits.check_and_hit("t-warn")
    assert limits.warned("t-warn") is True
    assert limits.warned("t-warn") is False


def test_redis_down_falls_back_to_sqlite(monkeypatch):
    from app import cache
    from app import limits

    cache.reset()
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6399/0")  # yo'q port
    assert limits.check_and_hit("t-fb") is True
    assert os.getenv("REDIS_URL") == "redis://localhost:6399/0"
