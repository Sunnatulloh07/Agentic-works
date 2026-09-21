"""Dependency-backed connector HTTP regressions. NOT RUN in the offline Computer."""
import json
import sqlite3
import pytest
from app import platform_api
from app.storage import tx
from test_control_plane_hardening_http import control_client


@pytest.fixture
def connector(control_client, tmp_path, monkeypatch):
    root = tmp_path / 'exports'
    root.mkdir()
    path = root / 'customer.db'
    db = sqlite3.connect(path)
    try:
        db.executescript("CREATE TABLE contacts(id INTEGER, name TEXT); INSERT INTO contacts VALUES(1,'private-customer');")
        db.commit()
    finally:
        db.close()
    raw = {'driver': 'sqlite_readonly', 'path': str(path), 'tables': {'contacts': ['id', 'name']},
           'agent_ids': ['ops.assistant']}
    monkeypatch.setenv('PLATFORM_DB_ROOTS', json.dumps([str(root)]))
    monkeypatch.setattr('platform_runtime.connectors.config', lambda tenant: {'connections': {'crm': raw}})
    monkeypatch.setattr(platform_api, 'policy', lambda tenant, agent: {
        'tools': ['connectors.read'], 'allowed_connections': ['crm'], 'ladder': 'autonomous'})
    return raw


def test_probe_with_scoped_agent_returns_only_receipt(control_client, connector):
    client, headers, _ = control_client
    response = client.post('/platform/demo-retail/connections/crm/verify', headers=headers,
                           json={'agent': 'ops.assistant'})
    assert response.status_code == 200
    assert response.json()['connection']['status'] == 'probe_succeeded'
    assert response.json()['persisted'] is False
    assert 'private-customer' not in response.text and connector['path'] not in response.text


@pytest.mark.parametrize('body', [{}, {'agent': 'other'}])
def test_probe_scoped_connection_requires_matching_agent(control_client, connector, body):
    client, headers, _ = control_client
    response = client.post('/platform/demo-retail/connections/crm/verify', headers=headers, json=body)
    assert response.status_code == 403


@pytest.mark.parametrize('body', [{'agent': True}, {'agent': 1}, {'agent': ''},
                                  {'agent': 'ops.assistant', 'role': 'owner'}])
def test_probe_strict_body(control_client, connector, body):
    client, headers, _ = control_client
    assert client.post('/platform/demo-retail/connections/crm/verify', headers=headers, json=body).status_code == 422


def test_revoked_connector_is_not_probed(control_client, connector):
    connector['lifecycle'] = 'revoked'
    client, headers, _ = control_client
    assert client.post('/platform/demo-retail/connections/crm/verify', headers=headers,
                       json={'agent': 'ops.assistant'}).status_code == 403


def test_probe_rechecks_actor_after_http_auth(control_client, connector, monkeypatch):
    client, headers, owner = control_client
    original = platform_api.identity
    def demote(*args, **kwargs):
        claims = original(*args, **kwargs)
        with tx() as c:
            c.execute("UPDATE p_memberships SET role='viewer' WHERE user_id=?", (owner,))
        return claims
    monkeypatch.setattr(platform_api, 'identity', demote)
    assert client.post('/platform/demo-retail/connections/crm/verify', headers=headers,
                       json={'agent': 'ops.assistant'}).status_code == 403
