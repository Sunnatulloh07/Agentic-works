"""Customer display names at ingest, and how the operator views show them.

Telegram carries message.from.first_name/last_name/username and WhatsApp Cloud
contacts[].profile.name. The name is customer-chosen text: it is stored bounded and
stripped of control/format characters as ``sender_name`` in the inbound event, shown
to operators, and never used to authenticate or fed to a model. Redelivery of the
SAME update must still dedup, and a nameless event keeps the payload it always had.
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
from platform_runtime import whatsapp_inbound as wa
from platform_runtime.conversation import record_turn

T = 'demo-retail'
AGENT = 'sales.responder'
TG_SECRET = {'X-Telegram-Bot-Api-Secret-Token': 'dev-webhook-secret'}


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'false')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'app.db'))
    monkeypatch.setenv('PIPELINE_MODE', 'platform')


def auth(tenant=T, role='owner'):
    return {'Authorization': 'Bearer ' + issue_token(tenant, subject='test-user', role=role)}


def update(uid, who, text='Salom', chat=-123):
    return {'update_id': uid, 'message': {'from': who, 'chat': {'id': chat}, 'text': text}}


def post(c, body):
    return c.post(f'/webhooks/telegram?tenant={T}', headers=TG_SECRET, json=body)


def stored(key, channel='telegram'):
    with engine().read() as c:
        row = c.execute('SELECT payload FROM p_events WHERE tenant=? AND channel=? AND event_key=?',
                        (T, channel, key)).fetchone()
    return json.loads(row['payload'])


def turn(key, chat, channel='telegram'):
    """Record the conversation turn the worker would, for an event already accepted."""
    record_turn(engine(), T, channel, key, AGENT, stored(key, channel))


def listing(c):
    rows = c.get(f'/platform/{T}/conversations', headers=auth(role='operator')).json()['conversations']
    return {x['conversation_id']: x for x in rows}


# ---- Telegram ingest -----------------------------------------------------------

def test_telegram_name_is_captured_bounded_and_clean():
    with TestClient(app) as c:
        assert post(c, update(1, {'id': 10, 'first_name': 'Dilnoza', 'last_name': 'Karimova', 'username': 'dk'})).status_code == 200
        assert post(c, update(2, {'id': 11, 'username': 'just_user'})).status_code == 200
        hostile = '‮evil\x00\n' + 'x' * 500
        assert post(c, update(3, {'id': 12, 'first_name': hostile, 'last_name': 5})).status_code == 200
        assert post(c, update(4, {'id': 13, 'first_name': '   '})).status_code == 200
    assert stored('1')['sender_name'] == 'Dilnoza Karimova'
    assert stored('2')['sender_name'] == 'just_user'
    name = stored('3')['sender_name']
    assert len(name) == 64 and name.startswith('evil x') and '‮' not in name and '\x00' not in name
    # No name at all: the payload keeps exactly the shape it always had.
    assert set(stored('4')) == {'sender', 'conversation_id', 'text'}


def test_the_name_never_becomes_the_sender():
    with TestClient(app) as c:
        post(c, update(5, {'id': 10, 'first_name': 'Admin'}))
    assert stored('5')['sender'] == '10'


def test_redelivery_of_the_same_update_still_dedups():
    body = update(6, {'id': 10, 'first_name': 'Ali', 'last_name': 'Valiyev'})
    with TestClient(app) as c:
        assert post(c, body).json()['status'] == 'accepted'
        again = post(c, body)
        assert again.status_code == 200 and again.json()['duplicate'] is True


# ---- WhatsApp ingest -----------------------------------------------------------

def wa_payload(name):
    return {'object': 'whatsapp_business_account', 'entry': [{'id': 'W', 'changes': [{'field': 'messages', 'value': {
        'metadata': {'phone_number_id': '106540352242922'},
        'contacts': [{'profile': {'name': name}, 'wa_id': '998901112233'}],
        'messages': [{'id': 'wamid.N1', 'from': '998901112233', 'timestamp': '1790000000', 'type': 'text',
                      'text': {'body': 'Salom'}}]}}]}]}


def test_whatsapp_profile_name_is_captured_clean_and_redelivery_dedups():
    raw = '  Ali​\n' + 'V' * 200
    events = wa.customer_messages(wa_payload(raw))['events']
    payload = events[0]['payload']
    assert payload['sender_name'] == 'Ali ' + 'V' * 60
    assert payload['sender'] == payload['conversation_id'] == '998901112233'
    e = engine()
    first = wa.accept_customer_messages(e, T, events)
    again = wa.accept_customer_messages(e, T, wa.customer_messages(wa_payload(raw))['events'])
    assert first['accepted'] == ['wamid.N1'] and again['duplicates'] == ['wamid.N1'] and not again['refused']
    assert 'sender_name' not in wa.customer_messages(wa_payload(''))['events'][0]['payload']


# ---- operator views ------------------------------------------------------------

def test_conversations_and_thread_show_the_latest_non_empty_name():
    with TestClient(app) as c:
        post(c, update(10, {'id': 10, 'first_name': 'Eski'}, chat=10))
        post(c, update(11, {'id': 10, 'first_name': 'Yangi', 'last_name': 'Ism'}, chat=10, text='Yana'))
        post(c, update(12, {'id': 10}, chat=10, text='Ismsiz'))
        post(c, update(13, {'id': 20}, chat=20))
        for uid, chat in ((10, '10'), (11, '10'), (12, '10'), (13, '20')):
            turn(str(uid), chat)
        rows = listing(c)
        assert rows['10']['sender_name'] == 'Yangi Ism' and rows['20']['sender_name'] == ''
        thread = c.get(f'/platform/{T}/conversations/telegram/10', headers=auth()).json()
        assert thread['sender_name'] == 'Yangi Ism'


def test_linked_customer_name_wins_and_both_are_present():
    with TestClient(app) as c:
        post(c, update(20, {'id': 10, 'first_name': 'Telegramdagi'}, chat=10))
        turn('20', '10')
        made = c.post(f'/platform/{T}/customers', json={'display_name': 'Dilnoza Karimova'}, headers=auth())
        cid = made.json()['customer']['id']
        c.post(f'/platform/{T}/customers/{cid}/channel-identities',
               json={'channel': 'telegram', 'external_id': '10', 'verified': True}, headers=auth())
        row = listing(c)['10']
    assert (row['customer_name'], row['sender_name']) == ('Dilnoza Karimova', 'Telegramdagi')


def test_name_is_channel_scoped_and_legacy_profile_name_is_cleaned():
    e = engine()
    # An older WhatsApp event: only the raw profile_name, with an invisible override in it.
    payload = {'sender': '998', 'conversation_id': '998', 'text': 'Salom', 'profile_name': 'Vali‮\x07'}
    e.accept_event(T, 'whatsapp', 'wamid.L1', payload)
    with TestClient(app) as c:
        post(c, update(30, {'id': 998, 'first_name': 'Telegram'}, chat=998))
        record_turn(e, T, 'whatsapp', 'wamid.L1', AGENT, payload)
        turn('30', '998')
        rows = c.get(f'/platform/{T}/conversations', headers=auth()).json()['conversations']
    assert {r['channel']: r['sender_name'] for r in rows} == {'whatsapp': 'Vali', 'telegram': 'Telegram'}
