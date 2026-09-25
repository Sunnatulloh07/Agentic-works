"""Operator read views over conversation turns (app/shop_api.py, R5).

/handoffs lists what the bot passed to a human, /conversations lists the latest
state of every conversation, and /conversations/{channel}/{id} is one thread:
customer and agent lines, operator replies and every turn with its status.
All three carry customer text, so they follow /inbox: owner and operator only,
tenant-scoped and bounded.
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
from platform_runtime.conversation import HANDOFF_KIND, record_turn

T = 'demo-retail'
AGENT = 'sales.responder'


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'false')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'app.db'))
    monkeypatch.setenv('PIPELINE_MODE', 'platform')


def auth(tenant=T, role='owner'):
    return {'Authorization': 'Bearer ' + issue_token(tenant, subject='test-user', role=role)}


def customer(key, text, chat='-123', tenant=T, clock=None):
    e = engine()
    if clock is not None:
        e.clock = lambda: clock
    e.accept_event(tenant, 'telegram', key, {'text': text, 'sender': '10', 'conversation_id': chat})
    if tenant == T:
        record_turn(e, tenant, 'telegram', key, AGENT, {'text': text, 'sender': '10', 'conversation_id': chat})
    else:  # no pack for this tenant: write the rows record_turn would write
        with e.tx() as c:
            c.execute("INSERT INTO p_conversation_turns(tenant,channel,event_key,conversation_id,sender,agent,"
                      "seq,status,created,updated) VALUES(?,?,?,?,?,?,1,'queued',1,1)",
                      (tenant, 'telegram', key, chat, '10', AGENT))
            c.execute('INSERT INTO p_conversation_history VALUES(?,?,?,?,?,?,?)',
                      (tenant, 'telegram', chat, 1, 'customer', text, 1.0))
    return e


def agent_line(text, chat='-123', created=50.0):
    with engine().tx() as c:
        seq = c.execute('SELECT COALESCE(MAX(seq),0)+1 n FROM p_conversation_history WHERE tenant=? '
                        'AND channel=? AND conversation_id=?', (T, 'telegram', chat)).fetchone()['n']
        c.execute('INSERT INTO p_conversation_history VALUES(?,?,?,?,?,?,?)',
                  (T, 'telegram', chat, seq, 'agent', text, created))


def handoff(key, reason, text, chat='-123', created=10.0, tenant=T):
    body = {'channel': 'telegram', 'event_key': key, 'conversation_id': chat, 'sender': '10',
            'agent': AGENT, 'reason': reason, 'text': text}
    with engine().tx() as c:
        c.execute('INSERT INTO p_records VALUES(?,?,?,?,?)',
                  (tenant, HANDOFF_KIND, 'telegram:' + key, json.dumps(body), created))


# ---- /handoffs ----------------------------------------------------------------

def test_handoffs_newest_first_with_customer_text_and_reason():
    handoff('m1', 'ungrounded_number', 'Narxi qancha?', created=10.0)
    handoff('m2', 'send_uncertain', 'Yetkazish bormi? O‘zbekcha', chat='-9', created=20.0)
    with TestClient(app) as c:
        r = c.get(f'/platform/{T}/handoffs', headers=auth(role='operator'))
    assert r.status_code == 200
    rows = r.json()['handoffs']
    assert [x['event_key'] for x in rows] == ['m2', 'm1']
    assert rows[0] == {'id': 'telegram:m2', 'channel': 'telegram', 'event_key': 'm2', 'conversation_id': '-9',
                       'reason': 'send_uncertain', 'text': 'Yetkazish bormi? O‘zbekcha', 'created': 20.0,
                       'agent': AGENT}


def test_handoffs_are_bounded_and_tenant_isolated():
    for i in range(105):
        handoff(f'k{i}', 'empty_reply', 'x', created=float(i))
    handoff('leak', 'empty_reply', 'boshqa tenant', tenant='other')
    with TestClient(app) as c:
        rows = c.get(f'/platform/{T}/handoffs', headers=auth()).json()['handoffs']
        other = c.get('/platform/other/handoffs', headers=auth('other')).json()['handoffs']
    assert len(rows) == 100 and rows[0]['event_key'] == 'k104'
    assert [x['text'] for x in other] == ['boshqa tenant']


def test_handoffs_owner_operator_only():
    with TestClient(app) as c:
        assert c.get(f'/platform/{T}/handoffs').status_code == 401
        assert c.get(f'/platform/{T}/handoffs', headers=auth('other')).status_code == 403
        for role in ('viewer', 'integrator'):
            assert c.get(f'/platform/{T}/handoffs', headers=auth(role=role)).status_code == 403


# ---- /conversations -------------------------------------------------------------

def test_conversations_show_the_latest_line_and_turn_per_conversation():
    customer('m1', 'Salom', clock=10.0)
    agent_line('Assalomu alaykum! Nima kerak?', created=11.0)
    customer('m2', 'Futbolka 92 bormi?', clock=12.0)
    customer('x1', 'Boshqa suhbat', chat='-9', clock=13.0)
    with TestClient(app) as c:
        r = c.get(f'/platform/{T}/conversations', headers=auth(role='operator'))
    assert r.status_code == 200
    rows = r.json()['conversations']
    assert [(x['channel'], x['conversation_id']) for x in rows] == [('telegram', '-9'), ('telegram', '-123')]
    first = rows[1]
    assert (first['last_role'], first['last_text'], first['last_at']) == ('customer', 'Futbolka 92 bormi?', 12.0)
    assert (first['turn_status'], first['turn_error']) == ('queued', '')


def test_conversations_are_tenant_isolated_and_role_gated():
    customer('m1', 'Salom')
    customer('o1', 'Boshqa', tenant='other')
    with TestClient(app) as c:
        mine = c.get(f'/platform/{T}/conversations', headers=auth()).json()['conversations']
        assert [x['last_text'] for x in mine] == ['Salom']
        assert c.get(f'/platform/{T}/conversations', headers=auth('other')).status_code == 403
        for role in ('viewer', 'integrator'):
            assert c.get(f'/platform/{T}/conversations', headers=auth(role=role)).status_code == 403


# ---- /conversations/{channel}/{conversation_id} ------------------------------------

def test_thread_has_ordered_lines_turns_and_operator_replies():
    customer('m1', 'Salom', clock=10.0)
    agent_line('Nima kerak?', created=11.0)
    customer('m2', 'Futbolka', clock=12.0)
    task = engine().operator_reply(T, 'telegram', '-123', 'Operator: bor, 92', actor='test-user',
                                   role='owner', key='r1', agent=AGENT)
    with engine().tx() as c:
        c.execute("UPDATE p_conversation_turns SET status='failed',error='send_failed',task='t9' "
                  "WHERE event_key='m1'")
    with TestClient(app) as c:
        r = c.get(f'/platform/{T}/conversations/telegram/-123', headers=auth(role='operator'))
    assert r.status_code == 200
    body = r.json()
    assert (body['channel'], body['conversation_id']) == ('telegram', '-123')
    assert [(x['role'], x['text']) for x in body['history']] == [
        ('customer', 'Salom'), ('agent', 'Nima kerak?'), ('customer', 'Futbolka')]
    turns = {x['event_key']: x for x in body['turns']}
    assert (turns['m1']['status'], turns['m1']['error'], turns['m1']['task']) == ('failed', 'send_failed', 't9')
    assert turns['m2']['status'] == 'queued'
    assert [(x['task_id'], x['text'], x['actor']) for x in body['operator_replies']] == [
        (task, 'Operator: bor, 92', 'test-user')]


def test_thread_is_tenant_isolated_and_role_gated():
    customer('m1', 'Salom')
    with TestClient(app) as c:
        assert c.get(f'/platform/{T}/conversations/telegram/-123', headers=auth()).status_code == 200
        assert c.get('/platform/other/conversations/telegram/-123', headers=auth('other')).status_code == 404
        assert c.get(f'/platform/{T}/conversations/telegram/-123', headers=auth('other')).status_code == 403
        assert c.get(f'/platform/{T}/conversations/telegram/-123',
                     headers=auth(role='viewer')).status_code == 403
        assert c.get(f'/platform/{T}/conversations/telegram/nope', headers=auth()).status_code == 404
        assert c.get(f'/platform/{T}/conversations/web/-123', headers=auth()).status_code == 404


def test_existing_routes_are_unchanged():
    with TestClient(app) as c:
        assert set(c.get(f'/platform/{T}/inbox', headers=auth()).json()) == {'events'}
        assert 'agents' in c.get(f'/platform/{T}/catalog', headers=auth()).json()


# ---- operator takeover ----------------------------------------------------------

def reply(c, text='Men javob beraman', key='k-reply-1', tenant=T, role='operator'):
    headers = {**auth(tenant, role), 'Idempotency-Key': key}
    return c.post(f'/platform/{tenant}/conversations/telegram/-123/reply', json={'text': text}, headers=headers)


def release(c, tenant=T, role='operator', key='k-release-1'):
    headers = auth(tenant, role)
    if key:
        headers['Idempotency-Key'] = key
    return c.post(f'/platform/{tenant}/conversations/telegram/-123/release', headers=headers)


def test_an_operator_reply_puts_the_chat_in_operator_mode():
    customer('m1', 'Salom')
    with TestClient(app) as c:
        assert reply(c).status_code == 200
        thread = c.get(f'/platform/{T}/conversations/telegram/-123', headers=auth()).json()
        listed = c.get(f'/platform/{T}/conversations', headers=auth()).json()['conversations'][0]
    assert thread['takeover']['actor'] == 'test-user' and thread['takeover']['until'] > 0
    assert listed['takeover'] == thread['takeover']


def test_release_hands_the_chat_back_to_the_bot():
    customer('m1', 'Salom')
    with TestClient(app) as c:
        reply(c)
        r = release(c)
        assert r.status_code == 200 and r.json() == {'released': True, 'takeover': None}
        assert release(c, key='k-release-2').json()['released'] is False
        thread = c.get(f'/platform/{T}/conversations/telegram/-123', headers=auth()).json()
    assert thread['takeover'] is None


def test_release_requires_a_key_an_operator_and_the_tenant():
    customer('m1', 'Salom')
    with TestClient(app) as c:
        reply(c)
        assert release(c, key=None).status_code == 422
        for role in ('viewer', 'integrator'):
            assert release(c, role=role).status_code == 403
        assert c.post(f'/platform/{T}/conversations/telegram/-123/release',
                      headers={**auth('other'), 'Idempotency-Key': 'k-x-12345'}).status_code == 403
        assert c.get(f'/platform/{T}/conversations/telegram/-123', headers=auth()).json()['takeover']


def test_takeover_is_tenant_isolated():
    customer('m1', 'Salom')
    customer('o1', 'Boshqa', tenant='other')
    with TestClient(app) as c:
        reply(c)
        other = c.get('/platform/other/conversations/telegram/-123', headers=auth('other')).json()
    assert other['takeover'] is None


def test_a_delivered_operator_reply_is_shown_once_as_a_history_line():
    from platform_runtime.operator_reply import settle_operator_replies
    customer('m1', 'Salom')
    with TestClient(app) as c:
        task = reply(c, text='Operator javobi').json()['task_id']
        with engine().tx() as db:
            db.execute("UPDATE p_tasks SET status='succeeded' WHERE id=?", (task,))
        assert settle_operator_replies(engine(), T)
        thread = c.get(f'/platform/{T}/conversations/telegram/-123', headers=auth()).json()
    assert [(x['role'], x['text']) for x in thread['history']][-1] == ('operator', 'Operator javobi')
    assert thread['operator_replies'] == []
