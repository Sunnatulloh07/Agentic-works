"""The conversation list names the customer when the shop has linked the sender.

A chat list of bare numeric ids is unreadable for an operator. When Customer 360
already links the sender's channel identity to a customer, /conversations returns
that customer's display name as ``customer_name``; otherwise it is ''. Additive
field only: the rest of the row is unchanged (test_conversation_views.py).
"""
import os
os.environ["ALLOW_INSECURE_DEV"] = "true"
os.environ.setdefault('ENV', 'test')
os.environ['PIPELINE_MODE'] = 'platform'
import pytest
from fastapi.testclient import TestClient

from app.auth import issue_token
from app.main import app
from app.platform_api import engine
from platform_runtime.conversation import record_turn

T = 'demo-retail'
AGENT = 'sales.responder'


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'false')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'app.db'))
    monkeypatch.setenv('PIPELINE_MODE', 'platform')


def auth(tenant=T, role='owner'):
    return {'Authorization': 'Bearer ' + issue_token(tenant, subject='test-user', role=role)}


def customer_line(key, text, sender, chat):
    e = engine()
    payload = {'text': text, 'sender': sender, 'conversation_id': chat}
    e.accept_event(T, 'telegram', key, payload)
    record_turn(e, T, 'telegram', key, AGENT, payload)


def link(c, name, external_id, tenant=T, channel='telegram'):
    created = c.post(f'/platform/{tenant}/customers', json={'display_name': name}, headers=auth(tenant))
    assert created.status_code == 200, created.text
    customer_id = created.json()['customer']['id']
    r = c.post(f'/platform/{tenant}/customers/{customer_id}/channel-identities',
               json={'channel': channel, 'external_id': external_id, 'verified': True}, headers=auth(tenant))
    assert r.status_code == 200, r.text


def names(c):
    rows = c.get(f'/platform/{T}/conversations', headers=auth(role='operator')).json()['conversations']
    return {x['conversation_id']: x['customer_name'] for x in rows}


def test_linked_sender_is_named_and_unlinked_is_blank():
    customer_line('m1', 'Salom', sender='10', chat='10')
    customer_line('m2', 'Narxi?', sender='20', chat='20')
    with TestClient(app) as c:
        link(c, 'Dilnoza Karimova', '10')
        assert names(c) == {'10': 'Dilnoza Karimova', '20': ''}


def test_name_comes_from_the_same_channel_and_tenant_only():
    customer_line('m1', 'Salom', sender='10', chat='10')
    with TestClient(app) as c:
        link(c, 'Instagramdagi boshqa odam', '10', channel='instagram')
        assert names(c) == {'10': ''}
        # Another tenant linking the same id cannot name this tenant's chat.
        other = c.post('/platform/other/customers', json={'display_name': 'Begona'}, headers=auth('other'))
        if other.status_code == 200:
            cid = other.json()['customer']['id']
            c.post(f'/platform/other/customers/{cid}/channel-identities',
                   json={'channel': 'telegram', 'external_id': '10', 'verified': True}, headers=auth('other'))
        assert names(c) == {'10': ''}
