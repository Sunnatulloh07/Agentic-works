"""Requires FastAPI/Pydantic/HTTPX. Owner-only scheduling surface for outreach.

The point of these tests is that scheduling automated customer contact is an owner
action, that the input contract is strict, and that a non-owner cannot reach it.
"""
import os
os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import platform_api as api, identity_store as identity
from app.auth import issue_token
from app.storage import reset
from platform_runtime.agent_loop import AgentLoop
from platform_runtime.engine import Engine
from platform_runtime.tools import build_registry

TENANT = 'demo-retail'
AGENT = 'sales.reengager'
CONNECTION = 'crm_onec'
POLICY = {'tools': ['crm.lead.stalled', 'crm.timeline.attach_message'],
          'allowed_connections': [CONNECTION], 'ladder': 'human_assisted'}
URL = f'/platform/{TENANT}/reengagement'


def _engine():
    from app.storage import _path
    engine = Engine(_path(), build_registry(), lambda t, a: POLICY)
    return engine


@pytest.fixture
def reengagement_http(tmp_path, monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'true')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'db'))
    reset()
    config = {TENANT: {'connections': {CONNECTION: {
        'driver': 'onec', 'host': '1c.example.uz', 'allowed_hosts': ['1c.example.uz'],
        'auth': 'basic', 'basic_auth_env': 'ONEC_BASIC', 'base_path': '/hs/leads',
        'capabilities': ['read', 'execute_write'], 'agent_ids': [AGENT],
        'response_map': {'items': 'rows', 'id': 'Ref_Key'}}}}}
    cfg = tmp_path / 'integrations.json'
    cfg.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setenv('PLATFORM_INTEGRATIONS_FILE', str(cfg))
    monkeypatch.setenv('ONEC_BASIC', 'robot:secret')

    owner = identity.register_user('owner@example.invalid', 'fixture password 123', 'Owner')
    identity.create_workspace(owner['id'], TENANT, 'Demo')
    _, session = identity.create_session(owner['id'], TENANT)
    owner_headers = {'Authorization': 'Bearer ' + issue_token(
        TENANT, subject=owner['id'], role='owner', session_id=session['session_id'])}

    operator = identity.register_user('operator@example.invalid', 'fixture password 123', 'Op')
    _, raw_token = identity.create_invitation(owner['id'], TENANT, 'operator2@example.invalid',
                                              'operator')
    identity.register_with_invitation('operator2@example.invalid', 'fixture password 123',
                                      'Op2', raw_token)
    operator = identity.authenticate('operator2@example.invalid', 'fixture password 123')
    _, op_session = identity.create_session(operator['id'], TENANT)
    operator_headers = {'Authorization': 'Bearer ' + issue_token(
        TENANT, subject=operator['id'], role='operator', session_id=op_session['session_id'])}

    engine = _engine()
    monkeypatch.setattr(api, 'engine', lambda: engine)
    with TestClient(app) as client:
        yield client, owner_headers, operator_headers, engine
    reset()


def test_owner_can_configure_and_list(reengagement_http):
    client, owner_headers, _, _ = reengagement_http
    body = {'agent': AGENT, 'connection': CONNECTION, 'interval_seconds': 600}
    response = client.put(URL + '/main', headers=owner_headers, json=body)
    assert response.status_code == 200
    assert response.json()['id'] == 'main'
    listing = client.get(URL, headers=owner_headers)
    assert listing.status_code == 200
    assert [row['id'] for row in listing.json()['policies']] == ['main']
    assert listing.headers['cache-control'] == 'no-store'


def test_operator_cannot_schedule_outreach(reengagement_http):
    client, _, operator_headers, _ = reengagement_http
    response = client.put(URL + '/main', headers=operator_headers,
                          json={'agent': AGENT, 'connection': CONNECTION})
    assert response.status_code == 403


def test_operator_can_read_but_not_sync(reengagement_http):
    client, owner_headers, operator_headers, _ = reengagement_http
    client.put(URL + '/main', headers=owner_headers,
               json={'agent': AGENT, 'connection': CONNECTION})
    assert client.get(URL, headers=operator_headers).status_code == 200
    assert client.get(URL + '/main/ledger', headers=operator_headers).status_code == 200
    assert client.post(URL + '/main/sync', headers=operator_headers).status_code == 403


def test_anonymous_is_denied(reengagement_http):
    client, *_ = reengagement_http
    assert client.get(URL).status_code == 401
    assert client.put(URL + '/main', json={'agent': AGENT, 'connection': CONNECTION}).status_code == 401


def test_input_contract_is_strict(reengagement_http):
    client, owner_headers, _, _ = reengagement_http
    base = {'agent': AGENT, 'connection': CONNECTION}
    # Unknown field, out-of-range cooldown, wrong type, extra tenant control.
    for bad in [{**base, 'extra': 1}, {**base, 'cooldown_seconds': 60},
                {**base, 'max_attempts': True}, {**base, 'interval_seconds': 'hour'},
                {**base, 'max_seconds': 10}]:
        response = client.put(URL + '/main', headers=owner_headers, json=bad)
        assert response.status_code == 422, response.text


def test_ledger_is_empty_until_a_cycle_runs(reengagement_http):
    client, owner_headers, _, _ = reengagement_http
    client.put(URL + '/main', headers=owner_headers,
               json={'agent': AGENT, 'connection': CONNECTION})
    ledger = client.get(URL + '/main/ledger', headers=owner_headers).json()
    assert ledger['policy'] == 'main'
    assert ledger['entries'] == []


def test_tenant_mismatch_is_rejected(reengagement_http):
    client, owner_headers, _, _ = reengagement_http
    other = dict(owner_headers)
    response = client.get('/platform/other-tenant/reengagement', headers=other)
    assert response.status_code == 403