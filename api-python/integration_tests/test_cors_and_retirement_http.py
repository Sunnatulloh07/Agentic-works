"""Requires real FastAPI/HTTPX. Static tests do not count as these passing."""
import os
os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')
from fastapi.testclient import TestClient
from app.main import app


def test_browser_put_preflight():
    with TestClient(app) as client:
        response = client.options('/platform/demo-retail/usage-budget', headers={
            'Origin': 'http://localhost:3000', 'Access-Control-Request-Method': 'PUT',
            'Access-Control-Request-Headers': 'authorization,content-type'})
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == 'http://localhost:3000'
        assert 'PUT' in response.headers['access-control-allow-methods']


def test_unknown_origin_rejected():
    with TestClient(app) as client:
        r = client.options('/platform/demo-retail/usage-budget', headers={
            'Origin': 'https://attacker.invalid', 'Access-Control-Request-Method': 'PUT'})
        assert r.status_code == 400
        assert 'access-control-allow-origin' not in r.headers


def _registered_paths(routes):
    """Every route path an ASGI app exposes, across FastAPI versions.

    FastAPI >= 0.141 keeps an included router as a wrapper that exposes
    ``original_router`` instead of ``path``, so scanning ``app.routes`` for
    ``route.path`` raises AttributeError instead of asserting on real routes.
    """
    paths = set()
    for route in routes:
        nested = getattr(route, 'original_router', None)
        if nested is not None:
            paths |= _registered_paths(nested.routes)
            continue
        path = getattr(route, 'path', None)
        if path:
            paths.add(path)
    return paths


def test_only_device_bound_runner_is_registered():
    paths = _registered_paths(app.routes)
    assert '/runner/ws' not in paths
    assert '/platform/runner/ws' in paths


def test_supervisor_route_preflight_allows_idempotency_key():
    # POST /platform/{tenant}/supervisor/route refuses a request without an
    # Idempotency-Key, so a browser preflight that names it must pass CORS.
    with TestClient(app) as client:
        r = client.options('/platform/demo-retail/supervisor/route', headers={
            'Origin': 'http://localhost:3000', 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'authorization,content-type,idempotency-key'})
        assert r.status_code == 200
        assert r.headers['access-control-allow-origin'] == 'http://localhost:3000'
        assert 'idempotency-key' in r.headers['access-control-allow-headers'].lower()
