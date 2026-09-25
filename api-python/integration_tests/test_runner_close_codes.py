"""The runner WebSocket says WHY it closes, so the runner can act on it.

One close code (4403) used to cover every failure -- a bad token, a revoked device,
a timeout, a stale claim, a database error -- so apps/runner could only guess
whether to reconnect or stop. Each code now has one meaning:

    4401  device token invalid or expired        -> refresh the token
    4403  device revoked or rotated               -> stop, re-enroll
    4400  protocol violation (unknown message ...) -> reconnect with backoff
    1011  server error                            -> reconnect with backoff
"""
import os

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import identity_store as identity
from app import platform_api
from app.auth import issue_token
from app.main import app
from app.storage import reset

WS = '/platform/runner/ws'


@pytest.fixture
def runner_client(tmp_path, monkeypatch):
    monkeypatch.setenv('ENV', 'test')
    monkeypatch.setenv('ALLOW_INSECURE_DEV', 'true')
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'true')
    monkeypatch.setenv('PIPELINE_MODE', 'platform')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'runner-ws.db'))
    reset()
    try:
        me = identity.register_user('owner@example.com', 'fixture password 123', 'Owner')
        identity.create_workspace(me['id'], 'demo-retail', 'Retail')
        _, session = identity.create_session(me['id'], 'demo-retail')
        token = issue_token('demo-retail', subject=me['id'], role='owner',
                            session_id=session['session_id'], ttl_seconds=900)
        with TestClient(app) as client:
            yield client, {'Authorization': 'Bearer ' + token}
    finally:
        reset()


def enroll(client, headers, device='pc'):
    response = client.post('/platform/demo-retail/devices', headers=headers, json={'device_id': device})
    assert response.status_code == 200
    return response.json()['device_token']


def close_code(client, hello, *messages):
    with client.websocket_connect(WS) as ws:
        ws.send_json(hello)
        try:
            for message in messages:
                ws.send_json(message)
                ws.receive_json()
            ws.receive_json()
        except WebSocketDisconnect as closed:
            return closed.code
    raise AssertionError('socket was not closed')


def test_invalid_token_closes_4401(runner_client):
    client, _ = runner_client
    assert close_code(client, {'token': 'not-a-jwt'}, {'type': 'heartbeat'}) == platform_api.RUNNER_CLOSE_AUTH == 4401


def test_user_token_is_not_a_device_token_4401(runner_client):
    client, headers = runner_client
    user_token = headers['Authorization'].split(' ', 1)[1]
    assert close_code(client, {'token': user_token}, {'type': 'heartbeat'}) == 4401


def test_expired_device_token_closes_4401(runner_client, monkeypatch):
    client, headers = runner_client
    enroll(client, headers)
    import time
    from app import auth
    real = time.time
    with monkeypatch.context() as clock:
        clock.setattr(auth.time, 'time', lambda: real() - 7200)  # minted two hours ago
        expired = issue_token('demo-retail', subject='pc', role='device', token_type='device',
                              device_id='pc', generation=1, ttl_seconds=3600)
    assert close_code(client, {'token': expired}, {'type': 'heartbeat'}) == 4401


def test_revoked_device_closes_4403(runner_client):
    client, headers = runner_client
    token = enroll(client, headers)
    with client.websocket_connect(WS) as ws:
        ws.send_json({'token': token})
        ws.send_json({'type': 'heartbeat'})
        assert ws.receive_json()['type'] == 'heartbeat_ack'
        assert client.post('/platform/demo-retail/devices', headers=headers,
                           json={'device_id': 'pc', 'revoked': True}).status_code == 200
        ws.send_json({'type': 'heartbeat'})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == platform_api.RUNNER_CLOSE_REVOKED == 4403


def test_rotated_device_token_closes_4403(runner_client):
    client, headers = runner_client
    old = enroll(client, headers)
    enroll(client, headers)  # re-enrolling bumps the generation: the old token is rotated out
    assert close_code(client, {'token': old}, {'type': 'heartbeat'}) == 4403


def test_unknown_message_is_a_protocol_close_4400(runner_client):
    client, headers = runner_client
    token = enroll(client, headers)
    assert close_code(client, {'token': token}, {'type': 'bogus'}) == platform_api.RUNNER_CLOSE_PROTOCOL == 4400


def test_unowned_result_is_a_protocol_close_4400(runner_client):
    client, headers = runner_client
    token = enroll(client, headers)
    assert close_code(client, {'token': token}, {'type': 'result', 'id': 'nope', 'claim': 'x', 'ok': True}) == 4400


def test_server_error_closes_1011(runner_client, monkeypatch):
    client, headers = runner_client
    token = enroll(client, headers)

    def broken(*args, **kwargs):
        raise RuntimeError('database is locked')
    monkeypatch.setattr(platform_api.Engine, 'claim', broken)
    assert close_code(client, {'token': token}, {'type': 'heartbeat'}) == 1011
