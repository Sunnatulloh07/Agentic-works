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


SECOND = 'second-shop'
SECOND_NUMBER = '106540352242933'
UNKNOWN_NUMBER = '999999999999999'


def batch(*numbers):
    """One Meta app's signed POST carrying a message for each business number."""
    entries = []
    for index, number in enumerate(numbers):
        entries.extend(delivery([text(mid='wamid.B%d' % index, body='Salom %d' % index)],
                                number=number)['entry'])
    return {'object': 'whatsapp_business_account', 'entry': entries}


def add_tenant(name, number):
    config = Path(os.environ['PLATFORM_INTEGRATIONS_FILE'])
    data = json.loads(config.read_text(encoding='utf-8'))
    data[name] = {'whatsapp': {'registers': {'main': {'connection': 'whatsapp', 'phone_number_id': number}}}}
    config.write_text(json.dumps(data), encoding='utf-8')


def tenant_events(engine, tenant):
    with engine.read() as c:
        return [json.loads(r['payload'])['text'] for r in c.execute(
            "SELECT payload FROM p_events WHERE tenant=? AND channel='whatsapp' ORDER BY rowid", (tenant,))]


def test_one_post_for_two_shops_is_split_by_business_number(http):
    """One Meta app batches several WABAs' numbers: each message goes to its own tenant."""
    client, engine = http
    add_tenant(SECOND, SECOND_NUMBER)
    response = post(client, batch(NUMBER, SECOND_NUMBER))
    assert response.status_code == 200
    assert sorted(response.json()['accepted']) == ['wamid.B0', 'wamid.B1']
    assert tenant_events(engine, TENANT) == ['Salom 0']
    assert tenant_events(engine, SECOND) == ['Salom 1']


def test_a_message_for_an_undeclared_number_is_dropped_not_given_to_the_owner(http):
    client, engine = http
    response = post(client, batch(NUMBER, UNKNOWN_NUMBER))
    assert response.status_code == 200
    assert response.json()['accepted'] == ['wamid.B0']
    assert {'reason': 'unknown_business_number'} in response.json()['dropped']
    assert tenant_events(engine, TENANT) == ['Salom 0']


def test_an_unreadable_tenant_configuration_is_503_so_meta_retries(http, caplog):
    """A typo in one tenant's file used to make its number 'unknown': 200, never retried."""
    client, engine = http
    broken = Path(os.environ['PACKS_DIR']) / 'broken-shop'
    broken.mkdir()
    (broken / 'integrations.yaml').write_text(
        'whatsapp:\n  registers:\n    main:\n      connection: whatsapp\n'
        '      phone_number_id: "not-digits-SECRETVALUE"\n', encoding='utf-8')
    with caplog.at_level('WARNING'):
        response = post(client, delivery([text()]))
    assert response.status_code == 503
    assert 'broken-shop' not in response.text
    assert events(engine) == []
    logged = ' '.join(record.getMessage() for record in caplog.records)
    assert 'broken-shop' in logged and 'ValueError' in logged
    assert 'SECRETVALUE' not in logged


def test_an_unknown_number_without_any_app_secret_is_acknowledged_not_retried(http, monkeypatch):
    client, engine = http
    monkeypatch.delenv('META_APP_SECRET')
    response = post(client, delivery([text()], number=UNKNOWN_NUMBER))
    assert response.status_code == 200 and response.json()['ignored'] is True
    assert events(engine) == []


def many_numbers(count):
    """An unsigned body naming ``count`` distinct business numbers, within the byte cap."""
    changes = [{'field': 'messages', 'value': {'metadata': {'phone_number_id': str(10 ** 14 + n)}}}
               for n in range(count)]
    return {'object': 'whatsapp_business_account',
            'entry': [{'id': 'W%d' % i, 'changes': changes[i * 50:(i + 1) * 50]}
                      for i in range(-(-count // 50))]}


def test_a_delivery_naming_too_many_business_numbers_is_refused_before_any_lookup(http, monkeypatch):
    """Measured before the cap: 2,500 ids, each re-reading every tenant's config, 24.9 s."""
    import time
    from platform_runtime import tools, whatsapp_inbound as wa
    client, engine = http
    calls = []
    monkeypatch.setattr(tools, 'config', lambda tenant: calls.append(tenant) or {})
    raw = json.dumps(many_numbers(2500)).encode()
    assert len(raw) < wa.MAX_BODY_BYTES
    started = time.perf_counter()
    response = post(client, None, signature='sha256=' + '0' * 64, raw=raw)
    assert time.perf_counter() - started < 0.5
    assert response.status_code == 422
    assert calls == []
    assert events(engine) == []
    # At the cap the delivery is still routed normally (and, unsigned, refused as 401).
    allowed = many_numbers(wa.MAX_ROUTED_NUMBERS)
    assert post(client, allowed, signature='sha256=' + '0' * 64).status_code == 401


def test_the_tenant_index_is_built_once_per_delivery(http, monkeypatch):
    from platform_runtime import whatsapp_inbound as wa
    client, _ = http
    calls = []
    real = wa._configured_tenants
    monkeypatch.setattr(wa, '_configured_tenants', lambda: calls.append(1) or real())
    payload = many_numbers(wa.MAX_ROUTED_NUMBERS)
    payload['entry'][0]['changes'][0]['value']['metadata']['phone_number_id'] = NUMBER
    post(client, payload)
    assert calls == [1]


def test_a_declared_length_over_the_cap_is_413_before_the_body_is_read(http):
    client, engine = http
    raw = json.dumps(delivery([text()])).encode()
    response = client.post(URL, content=raw, headers={
        'Content-Type': 'application/json', 'X-Hub-Signature-256': sign(raw),
        'Content-Length': str(1_000_001)})
    assert response.status_code == 413
    assert events(engine) == []


def test_a_streamed_body_is_cut_at_the_cap_not_buffered_whole():
    """No Content-Length (chunked): the read stops at MAX_BODY_BYTES + one chunk."""
    import asyncio
    from fastapi import HTTPException
    from app import whatsapp_api
    from platform_runtime import whatsapp_inbound as wa

    pulled = []

    class Endless:
        headers = {}

        async def stream(self):
            while True:
                pulled.append(65536)
                yield b' ' * 65536

    with pytest.raises(HTTPException) as caught:
        asyncio.run(whatsapp_api._read_body(Endless()))
    assert caught.value.status_code == 413
    assert sum(pulled) <= wa.MAX_BODY_BYTES + 65536

    class Small:
        headers = {'content-length': '5'}

        async def stream(self):
            yield b'ab'
            yield b'cde'

    assert asyncio.run(whatsapp_api._read_body(Small())) == b'abcde'


def test_the_handshake_fails_closed_without_a_verify_token(http, monkeypatch):
    client, _ = http
    monkeypatch.delenv('META_VERIFY_TOKEN')
    response = client.get(URL, params={'hub.mode': 'subscribe', 'hub.verify_token': VERIFY,
                                       'hub.challenge': '1'})
    assert response.status_code == 500
