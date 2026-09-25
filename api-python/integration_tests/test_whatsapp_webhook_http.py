"""Requires FastAPI/HTTPX. The WhatsApp Cloud API webhook over HTTP.

GET  /webhooks/whatsapp  -- Meta's one-time subscribe handshake (META_VERIFY_TOKEN)
POST /webhooks/whatsapp  -- X-Hub-Signature-256 over the RAW body (META_APP_SECRET);
                            the business phone_number_id names the tenant

The parsing, window and reply rules are runtime_tests/test_whatsapp_conversation.py;
these pin the HTTP contract Meta is configured against. Nothing touches a network.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

import pytest
from fastapi.testclient import TestClient

from app import platform_api as api
from app.main import app
from app.storage import reset
from platform_runtime.engine import Engine
from platform_runtime.tools import build_registry

TENANT = 'demo-retail'
NUMBER = '106540352242922'
WA_ID = '998901112233'
SECRET = 'integration-meta-app-secret'
VERIFY = 'integration-verify-token'
URL = '/webhooks/whatsapp'


def sign(raw, secret=SECRET):
    return 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def delivery(messages=(), statuses=(), number=NUMBER):
    value = {'messaging_product': 'whatsapp',
             'metadata': {'display_phone_number': '998712000000', 'phone_number_id': number},
             'contacts': [{'profile': {'name': 'Ali'}, 'wa_id': WA_ID}]}
    if messages:
        value['messages'] = list(messages)
    if statuses:
        value['statuses'] = list(statuses)
    return {'object': 'whatsapp_business_account',
            'entry': [{'id': 'WABA1', 'changes': [{'field': 'messages', 'value': value}]}]}


def text(mid='wamid.H1', body='Salom, o‘g‘il bolaga kurtka bormi?'):
    return {'id': mid, 'from': WA_ID, 'timestamp': '1790000000', 'type': 'text', 'text': {'body': body}}


@pytest.fixture
def http(tmp_path, monkeypatch):
    cfg = tmp_path / 'integrations.json'
    cfg.write_text(json.dumps({TENANT: {
        'whatsapp': {'registers': {'shop': {'connection': 'whatsapp', 'phone_number_id': NUMBER}}}}}),
        encoding='utf-8')
    (tmp_path / 'packs').mkdir()
    monkeypatch.setenv('PLATFORM_INTEGRATIONS_FILE', str(cfg))
    monkeypatch.setenv('PACKS_DIR', str(tmp_path / 'packs'))  # no pack-local integrations.yaml
    monkeypatch.setenv('META_APP_SECRET', SECRET)
    monkeypatch.setenv('META_VERIFY_TOKEN', VERIFY)
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'false')
    monkeypatch.setenv('PIPELINE_MODE', 'platform')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'app.db'))
    reset()
    engine = Engine(tmp_path / 'engine.db', build_registry(), api.policy)
    monkeypatch.setattr(api, 'engine', lambda: engine)
    with TestClient(app) as client:
        yield client, engine
    reset()


def post(client, payload, signature=None, raw=None):
    raw = raw if raw is not None else json.dumps(payload).encode()
    headers = {'Content-Type': 'application/json'}
    # ``signature=None`` means "sign properly"; ``False`` means "no header at
    # all"; ``''`` means "an empty header value".  The three are distinct
    # states and ``signature or sign(raw)`` collapsed the last two into a
    # VALID signature, so an empty header was never actually exercised.
    if signature is False:
        pass
    elif signature is None:
        headers['X-Hub-Signature-256'] = sign(raw)
    else:
        headers['X-Hub-Signature-256'] = signature
    return client.post(URL, content=raw, headers=headers)


def events(engine):
    with engine.read() as c:
        return [dict(r, payload=json.loads(r['payload'])) for r in c.execute(
            "SELECT event_key,payload FROM p_events WHERE tenant=? AND channel='whatsapp' ORDER BY rowid",
            (TENANT,))]


def test_subscribe_handshake_echoes_the_challenge_as_plain_text(http):
    client, _ = http
    ok = client.get(URL, params={'hub.mode': 'subscribe', 'hub.verify_token': VERIFY,
                                 'hub.challenge': '1158201444'})
    assert ok.status_code == 200 and ok.text == '1158201444'
    assert ok.headers['content-type'].startswith('text/plain')
    for params in ({'hub.mode': 'subscribe', 'hub.verify_token': 'guess', 'hub.challenge': '1'},
                   {'hub.mode': 'unsubscribe', 'hub.verify_token': VERIFY, 'hub.challenge': '1'},
                   {}):
        assert client.get(URL, params=params).status_code == 403


def test_a_signed_customer_message_becomes_one_whatsapp_event(http):
    client, engine = http
    response = post(client, delivery([text()]))
    assert response.status_code == 200
    assert response.json()['accepted'] == ['wamid.H1']
    [event] = events(engine)
    assert event['event_key'] == 'wamid.H1'
    payload = event['payload']
    assert (payload['sender'], payload['conversation_id']) == (WA_ID, WA_ID)
    assert payload['text'] == 'Salom, o‘g‘il bolaga kurtka bormi?'
    assert payload['phone_number_id'] == NUMBER


def test_a_redelivery_is_idempotent_by_message_id(http):
    client, engine = http
    assert post(client, delivery([text()])).json()['accepted'] == ['wamid.H1']
    again = post(client, delivery([text()]))
    assert again.status_code == 200
    assert again.json()['accepted'] == [] and again.json()['duplicates'] == ['wamid.H1']
    assert len(events(engine)) == 1


@pytest.mark.parametrize('signature', [False, '', 'sha256=' + '0' * 64, 'deadbeef',
                                       sign(b'another body'), sign(b'x', 'wrong-secret')])
def test_a_missing_or_invalid_signature_is_401_and_stores_nothing(http, signature):
    client, engine = http
    response = post(client, delivery([text()]), signature=signature)
    assert response.status_code == 401
    assert SECRET not in response.text
    assert events(engine) == []


def test_the_signature_covers_the_exact_raw_bytes(http):
    client, engine = http
    original = json.dumps(delivery([text()]), indent=2).encode()
    reserialised = json.dumps(json.loads(original)).encode()
    assert post(client, None, signature=sign(original), raw=reserialised).status_code == 401
    assert post(client, None, signature=sign(original), raw=original).status_code == 200
    assert len(events(engine)) == 1


def test_an_unknown_business_number_is_acknowledged_and_ignored(http):
    client, engine = http
    response = post(client, delivery([text()], number='999999999999999'))
    assert response.status_code == 200 and response.json()['ignored'] is True
    assert events(engine) == []
    # Unauthenticated deliveries for an unknown number are still refused, not acknowledged.
    assert post(client, delivery([text()], number='999999999999999'),
                signature=sign(b'x')).status_code == 401


def test_status_updates_create_no_event(http):
    client, engine = http
    response = post(client, delivery(statuses=[
        {'id': 'wamid.OUT', 'status': 'failed', 'timestamp': '1790000001', 'recipient_id': WA_ID,
         'errors': [{'code': 131047, 'title': 'Re-engagement message'}]}]))
    assert response.status_code == 200 and response.json()['accepted'] == []
    assert events(engine) == []


def test_media_arrives_as_a_placeholder(http):
    client, engine = http
    photo = {'id': 'wamid.P1', 'from': WA_ID, 'timestamp': '1790000000', 'type': 'image',
             'image': {'id': 'media-1', 'mime_type': 'image/jpeg'}}
    assert post(client, delivery([photo])).status_code == 200
    assert events(engine)[0]['payload']['text'] == '[rasm]'


def test_an_oversized_body_is_413(http):
    client, engine = http
    raw = b'{"object":"whatsapp_business_account","entry":[]}' + b' ' * 1_000_001
    assert post(client, None, raw=raw).status_code == 413
    assert events(engine) == []


def test_an_unset_app_secret_fails_closed(http, monkeypatch):
    client, engine = http
    monkeypatch.delenv('META_APP_SECRET')
    response = post(client, delivery([text()]))
    assert response.status_code == 500
    assert events(engine) == []


def test_a_signed_body_that_is_not_json_is_rejected_without_an_event(http):
    client, engine = http
    raw = b'not json at all'
    assert post(client, None, raw=raw).status_code == 422
    assert post(client, None, raw=raw, signature=sign(b'other')).status_code == 401
    assert events(engine) == []


def test_a_signed_json_body_that_is_not_a_whatsapp_delivery_is_422(http):
    client, engine = http
    for raw in (b'["not", "an", "object"]', b'{"object":"page","entry":[]}'):
        assert post(client, None, raw=raw).status_code == 422
    assert events(engine) == []


def test_two_tenants_declaring_the_same_number_are_refused_not_guessed(http):
    client, engine = http
    config = Path(os.environ['PLATFORM_INTEGRATIONS_FILE'])
    data = json.loads(config.read_text(encoding='utf-8'))
    data['second-shop'] = data[TENANT]
    config.write_text(json.dumps(data), encoding='utf-8')
    response = post(client, delivery([text()]))
    assert response.status_code == 409
    assert events(engine) == []


def test_the_handshake_fails_closed_without_a_verify_token(http, monkeypatch):
    client, _ = http
    monkeypatch.delenv('META_VERIFY_TOKEN')
    response = client.get(URL, params={'hub.mode': 'subscribe', 'hub.verify_token': VERIFY,
                                       'hub.challenge': '1'})
    assert response.status_code == 500
