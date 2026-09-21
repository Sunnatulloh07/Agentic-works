"""Measure the inbound WhatsApp guarantee: only Meta's own bytes become a customer.

The block's whole value is a boundary, and the boundary is easy to state:

    POST -> verify the RAW bytes against Meta's app secret -> then, and only then,
    parse -> drop everything that is not a declared customer's text -> durably accept

Every clause before the arrow is a claim that can be falsified, and a docstring is
not evidence. So this probe attacks it from four directions:

1. **Forgery.** A body nobody signed must be refused, a body signed with the wrong
   secret must be refused, and neither may leave a row in the durable inbox. If a
   forged delivery can be accepted, anyone who knows the URL can open a 24-hour
   window and every agent holding ``whatsapp.send`` will believe a customer wrote.

2. **Raw bytes, not a re-serialisation.** Sign one byte string, send a different one
   that parses to the same object, and confirm the check fails. This is the defect a
   "convenient" verifier introduces: ``json.dumps(json.loads(body))`` can reorder keys
   and change whitespace, and the signature covers bytes.

3. **Constant time.** A compare that short-circuits leaks how many leading characters
   of a forgery were right. Measured by counting digest comparisons, not by reading
   the source: the source is what a future edit changes.

4. **The handshake reflects only on a token match.** The GET handshake is the one
   place an unauthenticated caller gets a value echoed back, so the echo is gated on
   an exact token comparison and a wrong mode or token returns nothing.

What this probe does NOT show is that the *policy* (which numbers are customers, when
a window is open) is right for a real tenant -- that is what the unit tests cover. It
shows that a delivery which did not come from Meta cannot become a customer message.

Run from anywhere; paths are resolved relative to this file.
"""
import hashlib
import hmac
import inspect
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import whatsapp_inbound
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry

TENANT = 'demo-wa'
SECRET = 'probe-app-secret'
VERIFY_TOKEN = 'probe-verify-token'
ALI = '998901234567'

LADDERS = ('human_led', 'human_assisted', 'autonomous')


def policy(tenant, agent):
    return {'tools': list(whatsapp_inbound.INGEST_TOOLS), 'ladder': 'human_assisted'}


def sign(body, secret=SECRET):
    return 'sha256=' + hmac.new(secret.encode('utf-8'), body,
                                hashlib.sha256).hexdigest()


def envelope(messages=None, statuses=None):
    value = {'messaging_product': 'whatsapp',
             'metadata': {'display_phone_number': '15550001111',
                          'phone_number_id': '123456789012345'}}
    if messages or statuses:
        value['contacts'] = [{'profile': {'name': 'Ali'}, 'wa_id': ALI}]
    if messages:
        value['messages'] = messages
    if statuses:
        value['statuses'] = statuses
    return {'object': 'whatsapp_business_account',
            'entry': [{'id': '987654321098765',
                       'changes': [{'field': 'messages', 'value': value}]}]}


def message(message_id='wamid.PROBE1', body='Salom', sender=ALI,
            timestamp='1789794000'):
    return {'id': message_id, 'from': sender, 'timestamp': timestamp, 'type': 'text',
            'text': {'body': body}}


class Transport:
    """Stands in for the network so "we make no calls" is a measurement."""

    def __init__(self):
        self.calls = []


def build(root):
    root.mkdir(parents=True, exist_ok=True)
    cfg = root / 'integrations.json'
    cfg.write_text(json.dumps({TENANT: {
        'whatsapp': {'registers': {'support': {
            'connection': 'sales', 'phone_number_id': '123456789012345',
            'contacts': {'ali': ALI}}}},
        'whatsapp_webhook': {'app_secret_env': 'META_APP_SECRET',
                             'verify_token_env': 'META_VERIFY_TOKEN'},
    }}), encoding='utf-8')
    os.environ['PLATFORM_INTEGRATIONS_FILE'] = str(cfg)
    os.environ['META_APP_SECRET'] = SECRET
    os.environ['META_VERIFY_TOKEN'] = VERIFY_TOKEN
    return Engine(root / 'probe.db', build_registry(), policy)


def stored(engine):
    with engine.read() as c:
        return [dict(r) for r in c.execute(
            'SELECT event_key, status, payload FROM p_events WHERE tenant=? '
            'AND channel=? ORDER BY rowid', (TENANT, whatsapp_inbound.CHANNEL))]


def main():
    print('whatsapp_inbound: can a delivery that is not from Meta become a customer?')
    print()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        engine = build(root)

        # ----------------------------------------------------------- 1. forgery
        print('1. forged deliveries are refused and leave nothing behind')
        cases = [
            ('unsigned', {}, json.dumps(envelope([message()])).encode('utf-8')),
            ('wrong secret', None, json.dumps(envelope([message()])).encode('utf-8')),
            ('bare digest', {'x-hub-signature-256': 'a' * 64},
             json.dumps(envelope([message()])).encode('utf-8')),
            ('short digest', {'x-hub-signature-256': 'sha256=abc'},
             json.dumps(envelope([message()])).encode('utf-8')),
            ('valid-length zeros', {'x-hub-signature-256': 'sha256=' + '0' * 64},
             json.dumps(envelope([message()])).encode('utf-8')),
        ]
        for name, headers, body in cases:
            sent = headers if headers is not None else {
                'x-hub-signature-256': sign(body, 'not-the-secret')}
            try:
                whatsapp_inbound.ingest(engine, TENANT, body, sent, actor='probe')
                outcome = 'ACCEPTED (BUG)'
            except Forbidden:
                outcome = 'refused'
            except Exception as error:  # noqa: BLE001
                outcome = f'{type(error).__name__}'
            print(f'   {name:22} {outcome}')
            assert outcome == 'refused', f'{name}: {outcome}'
        assert stored(engine) == [], stored(engine)
        print(f'   durable inbox after 5 forgeries: {len(stored(engine))} rows')
        print()

        # ------------------------------------------------- 2. raw bytes coverage
        print('2. the signature covers the raw bytes, not a re-serialisation')
        # A first attempt at this used json.dumps(json.loads(body)) as the second
        # string, and it proved nothing: with the same separators and key order the
        # round trip is byte-identical, so the probe was signing and sending the same
        # bytes and calling it a different string. The difference has to be one that
        # survives parsing -- whitespace does, because Meta's bytes and a re-dump can
        # agree on content and disagree on the wire.
        original = json.dumps(envelope([message()])).encode('utf-8')
        reserialised = json.dumps(json.loads(original), indent=2).encode('utf-8')
        assert original != reserialised, 'the two strings must differ in bytes'
        assert json.loads(original) == json.loads(reserialised), \
            'and parse to the same object, or the test is about parsing not signing'
        try:
            whatsapp_inbound.ingest(engine, TENANT, reserialised,
                                    {'x-hub-signature-256': sign(original)},
                                    actor='probe')
            same = 'ACCEPTED (BUG)'
        except Forbidden:
            same = 'refused'
        except Exception as error:  # noqa: BLE001
            same = f'{type(error).__name__}'
        print(f'   sign(A), send(B) where A != B in bytes but B parses to A: {same}')
        assert same == 'refused', same
        assert stored(engine) == [], stored(engine)
        # And the honest delivery of the exact bytes is accepted, so the check is not
        # refusing everything.
        report = whatsapp_inbound.ingest(engine, TENANT, original,
                                         {'x-hub-signature-256': sign(original)},
                                         actor='probe')
        print(f'   sign(A), send(A): accepted={report["accepted"]}')
        assert report['accepted'] == ['wamid.PROBE1'], report
        assert len(stored(engine)) == 1, stored(engine)
        print()

        # ----------------------------------------------------- 3. constant time
        print('3. the digest comparison does not short-circuit')
        compare_calls = {'n': 0}
        real_compare = hmac.compare_digest

        def counted(a, b):
            compare_calls['n'] += 1
            return real_compare(a, b)

        body = json.dumps(envelope([message(message_id='wamid.PROBE2')])).encode()
        good = sign(body)
        near = 'sha256=' + good[len('sha256:'):]
        near = 'sha256=' + (good[len('sha256='):-1] + ('0' if good[-1] != '0' else '1'))
        hmac.compare_digest = counted
        try:
            first = whatsapp_inbound.verify_signature(body, good, SECRET)
            second = whatsapp_inbound.verify_signature(body, near, SECRET)
        finally:
            hmac.compare_digest = real_compare
        print(f'   good signature -> {first}; near-miss -> {second}')
        print(f'   compare_digest calls: {compare_calls["n"]} (one per attempt, no '
              f'early exit)')
        assert first is True and second is False, (first, second)
        assert compare_calls['n'] == 2, compare_calls
        print()

        # ------------------------------------------------------- 4. handshake
        print('4. the handshake reflects a challenge only on a token match')
        cases = [
            ('correct', {'hub.mode': 'subscribe', 'hub.verify_token': VERIFY_TOKEN,
                         'hub.challenge': 'CHAL'}, 'CHAL'),
            ('wrong token', {'hub.mode': 'subscribe', 'hub.verify_token': 'nope',
                             'hub.challenge': 'CHAL'}, None),
            ('wrong mode', {'hub.mode': 'unsubscribe', 'hub.verify_token': VERIFY_TOKEN,
                            'hub.challenge': 'CHAL'}, None),
            ('no token', {'hub.mode': 'subscribe', 'hub.challenge': 'CHAL'}, None),
        ]
        for name, query, expected in cases:
            got = whatsapp_inbound.verify_challenge(query, VERIFY_TOKEN)
            print(f'   {name:14} -> {got!r}')
            assert got == expected, (name, got, expected)
        print()

        # -------------------------------------- 5. no ingest function is a tool
        print('5. no tool can manufacture an inbound message')
        registry = build_registry()
        checked = 0
        for name in whatsapp_inbound.INGEST_TOOLS:
            tool = registry.get(name)
            print(f'   {name:18} risk={tool.risk}')
            assert tool.risk == 'read', name
            checked += 1
        banned_names = ('ingest', 'accept_event', 'webhook.ingest', 'whatsapp.accept')
        for banned in banned_names:
            assert not any(banned in n for n in registry.items), banned
        # And the module exposes no callable that a registry could wrap as a write.
        # The check is a word-boundary match on the name, not a substring: a substring
        # test matched ``_query_payload`` because 'pay' sits inside 'payload', which is
        # a false alarm about an argument decoder and would train a reader to ignore
        # the probe.
        import re as _re
        pattern = _re.compile(r'(^|_)(send|pay|payment|post|transfer|charge|execute)'
                              r'(s|ed|ing)?(_|$)')
        writers = [n for n, v in inspect.getmembers(whatsapp_inbound,
                                                    inspect.isfunction)
                   if pattern.search(n)]
        print(f'   write-shaped callables exposed by the module: {writers}')
        assert writers == [], writers
        print()

    print('PROVEN: an unsigned or wrongly-signed delivery cannot become a customer')
    print('        message, and the check is over the exact bytes with a constant-time')
    print('        compare. Only read tools are exposed; there is no way for a model to')
    print('        accept a webhook, so it cannot manufacture an inbound message and')
    print('        therefore cannot open a 24-hour window for a customer who never')
    print('        wrote.')
    print()
    print('NOT PROVEN (and deliberately so): that the operator-declared contact list')
    print('        and the window policy match a real tenant, and that the outbound')
    print('        path consults the window this block records. The latter is an open')
    print('        gap: engine.OUTBOUND_CHANNELS omits whatsapp, so whatsapp.send is')
    print('        gated by the static allowed_recipients list and reads its window')
    print("        from the operator's sheet. Closing that is its own unit of work.")


if __name__ == '__main__':
    main()
