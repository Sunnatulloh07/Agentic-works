"""Source-only HTTP regression cases. Real FastAPI required; NOT_RUN by agent."""
import os

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

import pytest
from fastapi.testclient import TestClient

from app import identity_store as identity
from app import platform_api
from app.auth import issue_token
from app.main import app
from app.storage import reset, tx


@pytest.fixture
def control_client(tmp_path, monkeypatch):
    monkeypatch.setenv('ENV', 'test')
    monkeypatch.setenv('ALLOW_INSECURE_DEV', 'true')
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'true')
    monkeypatch.setenv('PIPELINE_MODE', 'platform')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'http-control.db'))
    reset()
    try:
        me = identity.register_user('owner@example.com', 'fixture password 123', 'Owner')
        identity.create_workspace(me['id'], 'demo-retail', 'Retail')
        _, session = identity.create_session(me['id'], 'demo-retail')
        token = issue_token('demo-retail', subject=me['id'], role='owner',
                            session_id=session['session_id'], ttl_seconds=900)
        with TestClient(app) as client:
            yield client, {'Authorization': 'Bearer ' + token}, me['id']
    finally:
        reset()


@pytest.mark.parametrize('payload', [
    {'stopped': 'false'}, {'stopped': 'true'}, {'stopped': 0},
    {'stopped': 1}, {'stopped': None}, {'stopped': True, 'role': 'owner'},
])
def test_freeze_rejects_coercion_and_unknown_fields(control_client, payload):
    client, headers, _ = control_client
    response = client.post('/platform/demo-retail/freeze', headers=headers, json=payload)
    assert response.status_code == 422
    with platform_api.engine().read() as c:
        assert c.execute('SELECT count(*) FROM p_freeze').fetchone()[0] == 0


@pytest.mark.parametrize('payload', [
    {'device_id': 'pc', 'revoked': 'false'},
    {'device_id': 'pc', 'revoked': 0},
    {'device_id': 'pc', 'revoked': 1},
    {'device_id': 'pc', 'generation': 99},
    {'device_id': '../pc'},
])
def test_device_rejects_coercion_and_client_generation(control_client, payload):
    client, headers, _ = control_client
    response = client.post('/platform/demo-retail/devices', headers=headers, json=payload)
    assert response.status_code == 422
    with platform_api.engine().read() as c:
        assert c.execute('SELECT count(*) FROM p_devices').fetchone()[0] == 0


@pytest.mark.parametrize('interval', ['60', 60.0, True])
def test_schedule_rejects_non_integer_interval(control_client, interval):
    client, headers, _ = control_client
    response = client.post('/platform/demo-retail/schedules', headers=headers, json={
        'agent': 'ops.assistant', 'key': 'schedule',
        'steps': [{'tool': 'reports.summary', 'args': {}}],
        'interval_seconds': interval,
    })
    assert response.status_code == 422


def test_freeze_rechecks_owner_after_initial_http_authentication(control_client, monkeypatch):
    client, headers, owner = control_client
    original_identity = platform_api.identity

    def demote_after_authentication(*args, **kwargs):
        claims = original_identity(*args, **kwargs)
        with tx() as c:
            c.execute("UPDATE p_memberships SET role='viewer' WHERE user_id=?", (owner,))
        return claims

    monkeypatch.setattr(platform_api, 'identity', demote_after_authentication)
    response = client.post('/platform/demo-retail/freeze', headers=headers, json={'stopped': True})
    assert response.status_code == 403
    with platform_api.engine().read() as c:
        assert c.execute('SELECT count(*) FROM p_freeze').fetchone()[0] == 0


def test_device_rechecks_owner_after_initial_http_authentication(control_client, monkeypatch):
    client, headers, owner = control_client
    original_identity = platform_api.identity

    def disable_after_authentication(*args, **kwargs):
        claims = original_identity(*args, **kwargs)
        with tx() as c:
            c.execute("UPDATE p_users SET status='disabled' WHERE id=?", (owner,))
        return claims

    monkeypatch.setattr(platform_api, 'identity', disable_after_authentication)
    response = client.post('/platform/demo-retail/devices', headers=headers, json={'device_id': 'pc'})
    assert response.status_code == 403
    with platform_api.engine().read() as c:
        assert c.execute('SELECT count(*) FROM p_devices').fetchone()[0] == 0


def test_frozen_device_enroll_denied_revoke_allowed_and_audited(control_client):
    client, headers, owner = control_client
    assert client.post('/platform/demo-retail/devices', headers=headers, json={'device_id': 'pc'}).status_code == 200
    assert client.post('/platform/demo-retail/freeze', headers=headers, json={'stopped': True}).status_code == 200
    assert client.post('/platform/demo-retail/devices', headers=headers, json={'device_id': 'pc'}).status_code == 403
    response = client.post('/platform/demo-retail/devices', headers=headers, json={'device_id': 'pc', 'revoked': True})
    assert response.status_code == 200
    assert 'device_token' not in response.json()
    with platform_api.engine().read() as c:
        rows = c.execute("SELECT action,actor FROM p_audit WHERE action LIKE 'device.%' ORDER BY id").fetchall()
        assert [(row['action'], row['actor']) for row in rows] == [
            ('device.enrolled', owner), ('device.revoked', owner),
        ]


@pytest.mark.parametrize('path,payload', [
    ('tasks', {'agent': 'ops.assistant', 'key': 'task', 'steps': [{'tool': 'reports.summary', 'args': {}}], 'tenant': 'other'}),
    ('events', {'key': 'event', 'text': 'hello', 'sender': 'another-user'}),
    ('steps/nonexistent/approval', {'decision': 'approve'}),
    ('steps/nonexistent/reconcile', {'outcome': 'cancelled', 'evidence': 'receipt'}),
    ('inbox/retry', {'channel': '', 'key': 'event'}),
])
def test_control_plane_rejects_unrecognized_request_fields(control_client, path, payload):
    client, headers, _ = control_client
    response = client.post('/platform/demo-retail/' + path, headers=headers, json=payload)
    assert response.status_code == 422


def test_agent_run_create_list_detail_cancel_without_provider_call(control_client):
    client, headers, _ = control_client
    body = {'agent': 'ops.assistant', 'key': 'loop', 'text': 'Hisobotni ko‘rsat', 'max_steps': 2}
    first = client.post('/platform/demo-retail/agent-runs', headers=headers, json=body)
    assert first.status_code == 200
    run_id = first.json()['run_id']
    duplicate = client.post('/platform/demo-retail/agent-runs', headers=headers, json=body)
    assert duplicate.json()['run_id'] == run_id
    detail = client.get('/platform/demo-retail/agent-runs/' + run_id, headers=headers)
    assert detail.status_code == 200 and detail.json()['status'] == 'pending'
    assert detail.json()['calls'] == 0 and 'claim' not in detail.json()
    listed = client.get('/platform/demo-retail/agent-runs', headers=headers)
    assert listed.status_code == 200 and len(listed.json()['runs']) == 1
    cancelled = client.post('/platform/demo-retail/agent-runs/' + run_id + '/cancel', headers=headers, json={})
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'


@pytest.mark.parametrize('override', [
    {'max_steps': True}, {'max_steps': '2'}, {'max_steps': 2.0},
    {'max_steps': 13}, {'max_seconds': 59}, {'max_seconds': '60'},
    {'actor': 'forged-owner'}, {'role': 'owner'}, {'approved': True},
])
def test_agent_run_strict_request_contract(control_client, override):
    client, headers, _ = control_client
    body = {'agent': 'ops.assistant', 'key': 'loop', 'text': 'Hisobot', **override}
    assert client.post('/platform/demo-retail/agent-runs', headers=headers, json=body).status_code == 422


def test_agent_run_creation_denied_to_current_viewer(control_client):
    client, headers, owner = control_client
    with tx() as c:
        c.execute("UPDATE p_memberships SET role='viewer' WHERE user_id=?", (owner,))
    response = client.post('/platform/demo-retail/agent-runs', headers=headers,
                           json={'agent': 'ops.assistant', 'key': 'loop', 'text': 'Hisobot'})
    assert response.status_code == 403
