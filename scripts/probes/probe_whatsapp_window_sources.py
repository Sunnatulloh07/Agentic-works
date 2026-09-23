"""Measure the window's precedence rule: which fact decides, and can it be forged?

The window gate is now sourced from two places, and the whole safety argument is that
a message Meta signed wins over a timestamp an operator typed:

    verified event  (whatsapp_inbound recorded)   <- wins
    operator sheet  (a cell someone maintains)    <- fallback

Three things can go wrong, and a docstring cannot rule any of them out:

1. **A stale register re-opens a closed window.** If the rule were "take the newer of
   the two", a sheet cell dated slightly in the future would extend the window
   indefinitely -- the platform granting itself permission from a value it never
   verified. Measured by putting a *newer* register value against an *older* verified
   event and asserting the event still wins.

2. **A missing register blinds a working tenant.** The fallback must still answer for a
   contact with no verified message, or migrating a tenant would break every window.

3. **An unverified message opens a window.** The only thing that may open a window is a
   message that arrived through signature verification. Measured by driving the real
   `ingest` with a forged signature and confirming no window appears, then with a real
   one and confirming it does.

What this probe does NOT show is that the register is honest or well-maintained -- the
point of the precedence is precisely that it does not have to be.

Run from anywhere; paths are resolved relative to this file.
"""
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import whatsapp
from platform_runtime import whatsapp_inbound
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry
from platform_runtime import tools as tools_module

TENANT = 'demo-ws'
SECRET = 'probe-app-secret'
VERIFY_TOKEN = 'probe-verify-token'
ALI = '998901234567'
DILNOZA = '998907654321'
WINDOW = whatsapp.WINDOW_SECONDS

REGISTER = {
    'connection': 'sales', 'phone_number_id': '123456789012345',
    'contacts': {'ali': ALI, 'dilnoza': DILNOZA},
    'window_register': 'windows', 'window_range': 'Oyna!A1:D',
    'last_inbound_column': 'oxirgi_xabar',
}
POLICY = {'tools': ['sheets.rows', 'whatsapp.window', 'whatsapp.send'],
          'allowed_connections': ['sales', 'google'],
          'allowed_recipients': ['ali', 'dilnoza'],
          'ladder': 'human_assisted'}


def sign(body, secret=SECRET):
    return 'sha256=' + hmac.new(secret.encode('utf-8'), body,
                                hashlib.sha256).hexdigest()


def envelope(message_id, sender, timestamp, text='Salom'):
    return {'object': 'whatsapp_business_account', 'entry': [{'id': '1', 'changes': [
        {'field': 'messages', 'value': {
            'messaging_product': 'whatsapp',
            'metadata': {'phone_number_id': '123456789012345'},
            'contacts': [{'profile': {'name': 'Ali'}, 'wa_id': sender}],
            'messages': [{'id': message_id, 'from': sender,
                          'timestamp': str(timestamp), 'type': 'text',
                          'text': {'body': text}}]}}]}]}


def main():
    print('whatsapp window: which fact decides, and can it be forged?')
    print()
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    cfg = root / 'integrations.json'
    cfg.write_text(json.dumps({TENANT: {
        'whatsapp': {'registers': {'support': REGISTER}},
        'whatsapp_tokens': {'support': {'messaging': 'WHATSAPP_PROBE_MESSAGING_TOKEN',
                                        'management': 'WHATSAPP_PROBE_MANAGEMENT_TOKEN'}},
        'whatsapp_webhook': {'app_secret_env': 'META_APP_SECRET',
                             'verify_token_env': 'META_VERIFY_TOKEN'},
        'sheets_registers': {'windows': {'connection': 'google',
                                         'spreadsheet_id': 'X',
                                         'ranges': {'windows': 'Oyna!A1:D'}}},
    }}), encoding='utf-8')
    os.environ['PLATFORM_INTEGRATIONS_FILE'] = str(cfg)
    os.environ['META_APP_SECRET'] = SECRET
    os.environ['META_VERIFY_TOKEN'] = VERIFY_TOKEN
    os.environ['WHATSAPP_PROBE_MESSAGING_TOKEN'] = 'probe-messaging-token'
    os.environ['WHATSAPP_PROBE_MANAGEMENT_TOKEN'] = 'probe-management-token'

    engine = Engine(root / 'ws.db', build_registry(), lambda t, a: POLICY)

    # The register answers with whatever this dict says; the sheet transport is not the
    # subject of the probe.
    register_rows = {}

    def fake_sheets(engine_, tenant, agent, args, step):
        return {'rows': [{'contact': k, 'oxirgi_xabar': v}
                         for k, v in register_rows.items()]}

    tool = engine.registry.get('sheets.rows')
    engine.registry.items['sheets.rows'] = tool.__class__(
        tool.name, tool.risk, tool.schema, fake_sheets, tool.external, tool.runner)

    def window_rows():
        out = whatsapp.window(engine, TENANT, 'sales.whatsapp', 'probe')
        return {r['contact']: r for r in out['registers'][0]['contacts']}

    def deliver(message_id, sender=ALI, timestamp=None, *, secret=SECRET):
        body = json.dumps(envelope(message_id, sender,
                                   int(time.time()) if timestamp is None
                                   else timestamp)).encode('utf-8')
        return whatsapp_inbound.ingest(engine, TENANT, body,
                                       {'x-hub-signature-256': sign(body, secret)},
                                       actor='probe')

    def iso(epoch):
        import datetime
        return datetime.datetime.fromtimestamp(
            epoch, datetime.timezone(datetime.timedelta(hours=5))).isoformat()

    try:
        # ------------------------------------------------ 3. forgery first (no window)
        print('1. only a verified message may open a window')
        register_rows = {'ali': '', 'dilnoza': ''}
        before = window_rows()
        print(f"   before any delivery: ali open={before['ali']['open']} "
              f"source={before['ali']['source']}")
        assert before['ali']['open'] is False
        try:
            deliver('wamid.FORGED', secret='not-the-secret')
            raise AssertionError('a forged delivery was accepted')
        except Forbidden:
            pass
        after = window_rows()
        print(f"   after a forged delivery: ali open={after['ali']['open']} "
              f"source={after['ali']['source']}")
        assert after['ali']['open'] is False, after['ali']
        assert after['ali']['source'] == 'register', after['ali']

        deliver('wamid.REAL1')
        real = window_rows()
        print(f"   after a signed delivery: ali open={real['ali']['open']} "
              f"source={real['ali']['source']}")
        assert real['ali']['open'] is True, real['ali']
        assert real['ali']['source'] == 'event', real['ali']
        print()

        # --------------------------------------------- 1. stale register cannot win
        print('2. a stale register cannot re-open (or extend) a closed window')
        # The verified event is now OLD (closed); the sheet claims ali wrote a minute
        # ago. If the rule were "newer wins", the sheet would win and the window open.
        engine2 = Engine(root / 'ws2.db', build_registry(), lambda t, a: POLICY)
        t2 = engine2.registry.get('sheets.rows')
        engine2.registry.items['sheets.rows'] = t2.__class__(
            t2.name, t2.risk, t2.schema, fake_sheets, t2.external, t2.runner)
        old = int(time.time()) - (WINDOW + 3600)
        engine2_submit = None
        body = json.dumps(envelope('wamid.OLD', ALI, old)).encode('utf-8')
        whatsapp_inbound.ingest(engine2, TENANT, body,
                                {'x-hub-signature-256': sign(body)}, actor='probe')
        register_rows = {'ali': iso(time.time() - 60), 'dilnoza': ''}
        out = whatsapp.window(engine2, TENANT, 'sales.whatsapp', 'probe')
        row = {r['contact']: r for r in out['registers'][0]['contacts']}['ali']
        print(f"   event says closed (1h past), register says open (1min ago)")
        print(f"   -> open={row['open']} source={row['source']}")
        assert row['source'] == 'event', row
        assert row['open'] is False, row
        print()

        # -------------------------------------- 2. fallback still answers
        print('3. the register still answers when there is no verified message')
        register_rows = {'ali': iso(time.time() - 60), 'dilnoza': ''}
        out = whatsapp.window(engine, TENANT, 'sales.whatsapp', 'probe')
        row = {r['contact']: r for r in out['registers'][0]['contacts']}['dilnoza']
        print(f"   dilnoza (never wrote): open={row['open']} source={row['source']} "
              f"reason={row['reason']}")
        assert row['source'] == 'register', row
        assert row['reason'] in ('no inbound message', 'window closed', 'window open'), row
        # And a register that DOES have a recent time is still honoured for them.
        register_rows = {'ali': '', 'dilnoza': iso(time.time() - 60)}
        out = whatsapp.window(engine, TENANT, 'sales.whatsapp', 'probe')
        row = {r['contact']: r for r in out['registers'][0]['contacts']}['dilnoza']
        print(f"   dilnoza with a fresh register row: open={row['open']} "
              f"source={row['source']}")
        assert row['open'] is True and row['source'] == 'register', row
        print()

        # --------------------------------- 4. the send gate agrees with the window
        print('4. the send gate acts on the same fact the window reports')
        register_rows = {'ali': '', 'dilnoza': ''}
        # ali has a live verified window (from section 1), so a free-form send passes
        # the window check. The provider call is stubbed; only the gate is measured.
        posted = []
        def fake_post(url, token, *, method='GET', body=None, timeout=20):
            posted.append(body)
            return {'messages': [{'id': 'wamid.OUT'}]}
        with mock.patch('platform_runtime.whatsapp._bounded_json',
                        side_effect=fake_post):
            result = whatsapp.send(engine, TENANT, 'sales.whatsapp',
                                   {'register': 'support', 'contact': 'ali',
                                    'text': 'Salom, buyurtmangiz tayyor'}, 'probe')
        print(f"   free-form to ali (verified window open): sent={len(posted)} "
              f"kind={result['kind']}")

        # dilnoza has no verified window and an empty register: refused, no provider I/O
        with mock.patch('platform_runtime.whatsapp._bounded_json',
                        side_effect=fake_post):
            try:
                whatsapp.send(engine, TENANT, 'sales.whatsapp',
                              {'register': 'support', 'contact': 'dilnoza',
                               'text': 'Salom'}, 'probe')
                refused = 'SENT (BUG)'
            except Forbidden:
                refused = 'refused'
        print(f"   free-form to dilnoza (no source open): {refused}")
        assert len(posted) == 1, posted
        print()

        print('PROVEN: a message that did not pass signature verification cannot open a')
        print('        window, and the verified event wins outright over the operator')
        print('        register -- tested by putting a NEWER register value against an')
        print('        OLDER event and confirming the event still decides. The register')
        print('        remains the fallback for contacts with no verified message, so a')
        print('        tenant mid-migration keeps working.')
        print()
        print('NOT PROVEN: that the register is accurate or maintained. That is the')
        print('        point of the precedence -- it no longer has to be, for the window')
        print('        to be correct.')
    finally:
        tmp.cleanup()


if __name__ == '__main__':
    main()
