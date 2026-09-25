"""Requires FastAPI/Pydantic/HTTPX. Operator takeover reply over HTTP.

POST /platform/{tenant}/conversations/{channel}/{conversation_id}/reply
  body {"text": 1..4000}, header Idempotency-Key, roles owner/operator
  -> {"task_id", "status"}

The engine path is runtime_tests/test_operator_reply.py; these tests pin the HTTP
contract the dashboard is built against and run the real pack (capability routing
picks the owning agent) and, for revocation, the real identity directory. The send
adapter is stubbed: nothing here touches a network.
"""
import os
os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')
import pytest
from fastapi.testclient import TestClient

from app import platform_api as api
from app.auth import issue_token
from app.main import app
from app.storage import reset
from platform_runtime.engine import Engine
from platform_runtime.operator_reply import settle_operator_replies
from platform_runtime.tools import Tool, build_registry

TENANT = 'demo-retail'
CHAT = '-555'
TG_SECRET = {'X-Telegram-Bot-Api-Secret-Token': 'dev-webhook-secret'}
TEXT = 'Salom, o‘g‘lim! Narxi 189 000 so‘m.'


def url(channel='telegram', chat=CHAT, tenant=TENANT):
    return f'/platform/{tenant}/conversations/{channel}/{chat}/reply'


def auth(role='operator', tenant=TENANT, subject='olga', key='k1'):
    headers = {'Authorization': 'Bearer ' + issue_token(tenant, subject=subject, role=role)}
    if key is not None:
        headers['Idempotency-Key'] = key
    return headers


def stub_engine(sent):
    from app.runtime_authority import runtime_authority
    from app.storage import _path, db
    db()
    registry = build_registry()
    for name in ('telegram.send', 'instagram.send'):
        real = registry.get(name)

        def send(engine, tenant, agent, args, key, name=name):
            sent.append((name, dict(args)))
            return {'provider': name.split('.')[0], 'external_id': str(len(sent))}
        registry.items[name] = Tool(real.name, real.risk, real.schema, send, external=True)
    return Engine(_path(), registry, api.policy, authority=runtime_authority)


@pytest.fixture
def http(tmp_path, monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'false')
    monkeypatch.setenv('PIPELINE_MODE', 'platform')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'app.db'))
    reset()
    sent = []
    engine = stub_engine(sent)
    monkeypatch.setattr(api, 'engine', lambda: engine)
    with TestClient(app) as client:
        yield client, engine, sent
    reset()


def inbound(client, chat=CHAT, update=1):
    payload = {'update_id': update, 'message': {'from': {'id': 555}, 'chat': {'id': int(chat)}, 'text': 'Narxi?'}}
    response = client.post(f'/webhooks/telegram?tenant={TENANT}', headers=TG_SECRET, json=payload)
    assert response.status_code == 200 and response.json()['status'] == 'accepted'


def test_reply_to_a_known_conversation_is_sent_once_and_joins_the_history(http):
    client, engine, sent = http
    inbound(client)
    response = client.post(url(), headers=auth(), json={'text': TEXT})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'task_id', 'status'} and body['status'] == 'queued'
    task = engine.get(TENANT, body['task_id'])
    # Capability routing: the first pack agent holding telegram.send, never a fixed id.
    assert task['agent'] == 'sales.responder'
    step = task['steps'][0]
    assert step['args'] == {'conversation_id': CHAT, 'text': TEXT}
    assert (step['approval_status'], step['approver']) == ('approved', 'olga')
    assert engine.tick(TENANT)
    assert sent == [('telegram.send', {'conversation_id': CHAT, 'text': TEXT})]
    assert settle_operator_replies(engine, TENANT)
    with engine.read() as c:
        history = [tuple(r) for r in c.execute(
            'SELECT role,text FROM p_conversation_history WHERE tenant=? AND conversation_id=?', (TENANT, CHAT))]
    assert history == [('operator', TEXT)]
    again = client.post(url(), headers=auth(), json={'text': TEXT})
    assert again.json() == {'task_id': body['task_id'], 'status': 'succeeded'}


def test_owner_may_reply(http):
    client, engine, _ = http
    inbound(client)
    response = client.post(url(), headers=auth('owner', subject='boss'), json={'text': TEXT})
    assert response.status_code == 200
    assert engine.get(TENANT, response.json()['task_id'])['steps'][0]['approver'] == 'boss'


def test_same_key_same_body_is_one_task_and_a_different_body_is_409(http):
    client, engine, sent = http
    inbound(client)
    first = client.post(url(), headers=auth(), json={'text': TEXT}).json()['task_id']
    assert client.post(url(), headers=auth(), json={'text': TEXT}).json()['task_id'] == first
    assert client.post(url(), headers=auth(), json={'text': 'boshqa'}).status_code == 409
    inbound(client, chat='-556', update=2)
    assert client.post(url(chat='-556'), headers=auth(), json={'text': TEXT}).status_code == 409
    assert len(engine.list_tasks(TENANT)) == 1
    engine.tick(TENANT)
    assert not engine.tick(TENANT)
    assert len(sent) == 1


def test_idempotency_key_is_required(http):
    client, engine, _ = http
    inbound(client)
    assert client.post(url(), headers=auth(key=None), json={'text': TEXT}).status_code == 422
    assert client.post(url(), headers=auth(key='k' * 300), json={'text': TEXT}).status_code == 422
    assert engine.list_tasks(TENANT) == []


@pytest.mark.parametrize('role', ['viewer', 'integrator'])
def test_viewer_and_integrator_are_refused(http, role):
    client, engine, _ = http
    inbound(client)
    assert client.post(url(), headers=auth(role), json={'text': TEXT}).status_code == 403
    assert engine.list_tasks(TENANT) == []


def test_anonymous_device_and_cross_tenant_tokens_are_refused(http):
    client, engine, _ = http
    inbound(client)
    assert client.post(url(), headers={'Idempotency-Key': 'k1'}, json={'text': TEXT}).status_code == 401
    device = issue_token(TENANT, subject='d', role='device', token_type='device', device_id='d', generation=1)
    assert client.post(url(), headers={'Authorization': 'Bearer ' + device, 'Idempotency-Key': 'k1'},
                       json={'text': TEXT}).status_code == 403
    assert client.post(url(), headers=auth(tenant='marketing'), json={'text': TEXT}).status_code == 403
    assert engine.list_tasks(TENANT) == []


def test_unknown_conversation_is_404(http):
    client, engine, sent = http
    assert client.post(url(), headers=auth(), json={'text': TEXT}).status_code == 404
    inbound(client)
    assert client.post(url(chat='-999'), headers=auth(), json={'text': TEXT}).status_code == 404
    # The same chat on a different channel is a different conversation.
    assert client.post(url('instagram'), headers=auth(), json={'text': TEXT}).status_code == 404
    assert engine.list_tasks(TENANT) == [] and sent == []


def test_another_tenants_conversation_is_404(http):
    client, engine, _ = http
    engine.accept_event('marketing', 'telegram', 'm1', {'text': 'x', 'sender': '1', 'conversation_id': CHAT})
    assert client.post(url(), headers=auth(), json={'text': TEXT}).status_code == 404
    assert engine.list_tasks(TENANT) == [] and engine.list_tasks('marketing') == []


def test_frozen_tenant_is_refused(http):
    client, engine, _ = http
    inbound(client)
    assert client.post(f'/platform/{TENANT}/freeze', headers=auth('owner'), json={'stopped': True}).status_code == 200
    assert client.post(url(), headers=auth(), json={'text': TEXT}).status_code == 403
    assert engine.list_tasks(TENANT) == []


@pytest.mark.parametrize('body', [{'text': ''}, {'text': '   '}, {'text': 'x' * 4001}, {'text': 5},
                                  {}, {'text': 'ok', 'agent': 'ops.assistant'}])
def test_text_bounds_and_strict_body(http, body):
    client, engine, _ = http
    inbound(client)
    assert client.post(url(), headers=auth(), json=body).status_code == 422
    assert engine.list_tasks(TENANT) == []


def test_text_of_exactly_4000_characters_is_accepted(http):
    client, _, _ = http
    inbound(client)
    assert client.post(url(), headers=auth(), json={'text': 'x' * 4000}).status_code == 200


@pytest.mark.parametrize('channel', ['web', 'operator', 'cron'])
def test_a_channel_without_a_verified_inbound_stream_is_refused(http, channel):
    client, engine, _ = http
    inbound(client)
    assert client.post(url(channel), headers=auth(), json={'text': TEXT}).status_code == 422
    assert engine.list_tasks(TENANT) == []


def test_a_whatsapp_reply_to_a_verified_conversation_is_queued(http, monkeypatch):
    """whatsapp owns a verified inbound stream since app/whatsapp_api.py, so a chat
    that wrote can be answered from the dashboard exactly as a Telegram chat is."""
    client, engine, sent = http
    monkeypatch.setattr(api, 'agents', lambda tenant: [
        {'id': 'wa.bot', 'tools': ['whatsapp.send'], 'triggers': [], 'conversation': {}}])
    monkeypatch.setattr(engine, 'policy', lambda tenant, agent: {
        'tools': ['whatsapp.send'], 'ladder': 'autonomous', 'approval': [],
        'allowed_recipients': [], 'allowed_connections': []})
    engine.accept_event(TENANT, 'whatsapp', 'wamid.W1',
                        {'text': 'Salom', 'sender': '998901112233',
                         'conversation_id': '998901112233'})
    response = client.post(url('whatsapp', '998901112233'), headers=auth(), json={'text': TEXT})
    assert response.status_code == 200
    task = engine.get(TENANT, response.json()['task_id'])
    assert task['agent'] == 'wa.bot' and task['status'] == 'queued'
    assert task['steps'][0]['tool'] == 'whatsapp.send'
    assert task['steps'][0]['approval_status'] == 'approved'
    assert sent == []  # queued, not dispatched: nothing here touches Meta


def test_a_whatsapp_reply_to_a_chat_that_never_wrote_is_404(http, monkeypatch):
    client, engine, _ = http
    monkeypatch.setattr(api, 'agents', lambda tenant: [
        {'id': 'wa.bot', 'tools': ['whatsapp.send'], 'triggers': [], 'conversation': {}}])
    monkeypatch.setattr(engine, 'policy', lambda tenant, agent: {
        'tools': ['whatsapp.send'], 'ladder': 'autonomous', 'approval': [],
        'allowed_recipients': [], 'allowed_connections': []})
    inbound(client)  # a telegram chat only
    assert client.post(url('whatsapp', '998901112233'), headers=auth(),
                       json={'text': TEXT}).status_code == 404
    assert engine.list_tasks(TENANT) == []


def test_no_pack_agent_holding_the_send_tool_is_refused(http, monkeypatch):
    client, engine, _ = http
    inbound(client)
    monkeypatch.setattr(api, 'agents', lambda tenant: [{'id': 'ops.assistant', 'tools': ['reports.summary']}])
    assert client.post(url(), headers=auth(), json={'text': TEXT}).status_code == 422
    assert engine.list_tasks(TENANT) == []


# --- the real identity directory: revocation between submit and claim -------------------

@pytest.fixture
def directory(tmp_path, monkeypatch):
    from app import identity_store as identity
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'true')
    monkeypatch.setenv('PIPELINE_MODE', 'platform')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'app.db'))
    reset()
    owner = identity.register_user('owner@example.invalid', 'fixture password 123', 'Owner')
    identity.create_workspace(owner['id'], TENANT, 'Demo')
    _, session = identity.create_session(owner['id'], TENANT)
    _, invite = identity.create_invitation(owner['id'], TENANT, 'op@example.invalid', 'operator')
    identity.register_with_invitation('op@example.invalid', 'fixture password 123', 'Op', invite)
    operator = identity.authenticate('op@example.invalid', 'fixture password 123')
    _, op_session = identity.create_session(operator['id'], TENANT)
    headers = {'Authorization': 'Bearer ' + issue_token(TENANT, subject=operator['id'], role='operator',
                                                        session_id=op_session['session_id']),
               'Idempotency-Key': 'k1'}
    sent = []
    engine = stub_engine(sent)
    monkeypatch.setattr(api, 'engine', lambda: engine)
    with TestClient(app) as client:
        yield client, engine, sent, headers, owner, operator, identity
    reset()


def test_operator_revoked_between_submit_and_claim_is_not_sent(directory):
    client, engine, sent, headers, owner, operator, identity = directory
    engine.accept_event(TENANT, 'telegram', 'u1', {'text': 'Narxi?', 'sender': '555', 'conversation_id': CHAT})
    response = client.post(url(), headers=headers, json={'text': TEXT})
    assert response.status_code == 200
    identity.revoke_membership(owner['id'], TENANT, operator['id'])
    assert not engine.tick(TENANT)
    task = engine.get(TENANT, response.json()['task_id'])
    assert task['status'] == 'failed' and task['steps'][0]['error'] == 'authority_revoked'
    assert sent == []
    assert not settle_operator_replies(engine, TENANT)
    # The revoked session cannot submit again either.
    assert client.post(url(), headers={**headers, 'Idempotency-Key': 'k2'},
                       json={'text': TEXT}).status_code in (401, 403)
