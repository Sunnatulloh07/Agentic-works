"""POST /platform/{t}/handoffs/{id}/resolve and GET /handoffs?status=.

A handoff record never changes; "a person handled it" is a separate row keyed by it.
Owner/operator only, Idempotency-Key required, tenant-scoped, audited, idempotent, and
a NEW handoff on the same conversation is open again.
"""
import json
import os
os.environ["ALLOW_INSECURE_DEV"] = "true"
os.environ.setdefault('ENV', 'test')
os.environ['PIPELINE_MODE'] = 'platform'
import pytest
from fastapi.testclient import TestClient

from app.auth import issue_token
from app.main import app
from app.platform_api import engine
from platform_runtime.conversation import HANDOFF_KIND

T = 'demo-retail'
AGENT = 'sales.responder'


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'false')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'app.db'))
    monkeypatch.setenv('PIPELINE_MODE', 'platform')


def auth(tenant=T, role='owner', sub='test-user', key='k-resolve-1'):
    headers = {'Authorization': 'Bearer ' + issue_token(tenant, subject=sub, role=role)}
    if key:
        headers['Idempotency-Key'] = key
    return headers


def handoff(key, chat='-123', created=10.0, tenant=T, reason='empty_reply'):
    body = {'channel': 'telegram', 'event_key': key, 'conversation_id': chat, 'sender': '10',
            'agent': AGENT, 'reason': reason, 'text': 'Narxi qancha?'}
    with engine().tx() as c:
        c.execute('INSERT INTO p_records VALUES(?,?,?,?,?)',
                  (tenant, HANDOFF_KIND, 'telegram:' + key, json.dumps(body), created))


def resolve(c, hid, tenant=T, **kw):
    note = kw.pop('note', None)
    return c.post(f'/platform/{tenant}/handoffs/{hid}/resolve', headers=auth(tenant, **kw),
                  json={'note': note} if note is not None else None)


def listed(c, status=None, tenant=T):
    url = f'/platform/{tenant}/handoffs' + (f'?status={status}' if status else '')
    r = c.get(url, headers=auth(tenant, role='operator', key=None))
    assert r.status_code == 200, r.text
    return {x['event_key']: x for x in r.json()['handoffs']}


def test_default_lists_open_only_and_resolve_moves_it():
    handoff('a', created=10.0, chat='1')
    handoff('b', created=20.0, chat='2')
    with TestClient(app) as c:
        assert set(listed(c)) == {'a', 'b'}
        r = resolve(c, 'telegram:a', role='operator', sub='oper-1', note='Telefonda hal qilindi')
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['resolved'] is True and body['resolved_by'] == 'oper-1' and body['changed'] is True
        assert set(listed(c)) == {'b'} and set(listed(c, 'open')) == {'b'}
        assert set(listed(c, 'resolved')) == {'a'} and set(listed(c, 'all')) == {'a', 'b'}
        row = listed(c, 'all')['a']
        assert (row['resolved'], row['resolved_by'], row['resolved_at']) == (True, 'oper-1', body['resolved_at'])
        assert listed(c, 'all')['b']['resolved'] is False and listed(c, 'all')['b']['resolved_at'] is None
        assert c.get(f'/platform/{T}/handoffs?status=bogus', headers=auth(key=None)).status_code == 422


def test_resolve_is_idempotent_and_keeps_the_first_resolver():
    handoff('a')
    with TestClient(app) as c:
        first = resolve(c, 'telegram:a', sub='first').json()
        second = resolve(c, 'telegram:a', sub='second')
        assert second.status_code == 200
        assert second.json()['resolved_by'] == 'first' and second.json()['changed'] is False
        assert second.json()['resolved_at'] == first['resolved_at']
    with engine().read() as c2:
        assert c2.execute("SELECT count(*) n FROM p_handoff_resolutions").fetchone()['n'] == 1
        assert c2.execute("SELECT count(*) n FROM p_audit WHERE action='conversation.handoff_resolved'").fetchone()['n'] == 1


def test_resolve_is_audited_with_the_actor():
    handoff('a')
    with TestClient(app) as c:
        resolve(c, 'telegram:a', sub='oper-7')
    with engine().read() as c2:
        row = c2.execute("SELECT actor,data FROM p_audit WHERE action='conversation.handoff_resolved'").fetchone()
    assert row['actor'] == 'oper-7' and json.loads(row['data'])['handoff_id'] == 'telegram:a'


def test_new_handoff_on_the_same_conversation_is_open_again():
    handoff('a', created=10.0, chat='1')
    with TestClient(app) as c:
        resolve(c, 'telegram:a')
        assert listed(c) == {}
        handoff('b', created=50.0, chat='1')          # the customer wrote again, bot stuck again
        assert set(listed(c)) == {'b'}
        assert listed(c, 'all')['a']['resolved'] is True


def test_resolving_closes_older_open_handoffs_of_that_chat_only():
    handoff('old', created=10.0, chat='1')
    handoff('mid', created=20.0, chat='1')
    handoff('new', created=30.0, chat='1')
    handoff('other', created=5.0, chat='2')
    with TestClient(app) as c:
        r = resolve(c, 'telegram:mid')
        assert sorted(r.json()['resolved_ids']) == ['telegram:mid', 'telegram:old']
        assert set(listed(c)) == {'new', 'other'}


def test_roles_key_and_unknown_handoff():
    handoff('a')
    with TestClient(app) as c:
        assert c.post(f'/platform/{T}/handoffs/telegram:a/resolve').status_code == 401
        for role in ('viewer', 'integrator'):
            assert resolve(c, 'telegram:a', role=role).status_code == 403
        assert resolve(c, 'telegram:a', key=None).status_code == 422
        assert resolve(c, 'telegram:nope').status_code == 404
        assert resolve(c, 'telegram:a', role='operator').status_code == 200
        assert c.post(f'/platform/{T}/handoffs/telegram:a/resolve',
                      headers=auth(), json={'note': 'x' * 501}).status_code == 422


def test_tenant_isolation():
    handoff('a', tenant=T)
    with TestClient(app) as c:
        # A token for another tenant is refused; the same id in a tenant that has none is a 404.
        assert c.post(f'/platform/{T}/handoffs/telegram:a/resolve', headers=auth('other')).status_code == 403
        assert resolve(c, 'telegram:a', tenant='other').status_code == 404
        assert set(listed(c)) == {'a'}


def test_resolve_in_one_tenant_leaves_another_tenants_handoff_open():
    handoff('a', tenant=T)
    handoff('a', tenant='other')
    with TestClient(app) as c:
        assert resolve(c, 'telegram:a', tenant=T).status_code == 200
        assert set(listed(c, tenant='other')) == {'a'}
