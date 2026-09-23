"""Measure the 131047 guarantee rather than claiming it (PRD v0.5, P8 / T1).

The module's docstring makes a strong claim:

    Error 131047 is impossible to reach **by design**.

131047 is Meta's refusal of free-form text sent outside the 24-hour service
window. The claim is not "we catch it" and not "we retry with a template" — it is
that a send that would produce it is refused **before any provider I/O**, so the
code path that reaches the provider cannot be the one that names a closed window.

Claims in a docstring are not evidence, so this probe drives the real ``send``
against a counting transport across every window state, and reports how many
provider POSTs each one produced. The interesting number is the zero.

It also measures the counterpart: a template outside the window still sends, so
the block did not simply disable WhatsApp outside the window — it uses the exact
path Meta permits.

Run from anywhere; paths are resolved relative to this file.
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry
from platform_runtime.whatsapp import ERROR_OUTSIDE_WINDOW, WINDOW_SECONDS, send

SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
NOW = 1_789_794_000.0

POLICY = {
    'tools': ['sheets.rows', 'whatsapp.send', 'whatsapp.window'],
    'allowed_connections': ['google'],
    'allowed_recipients': ['ali', 'dilnoza'],
    'ladder': 'human_assisted',
}

WINDOW_ROWS = [
    ['contact', 'oxirgi_xabar'],
    ['ali', '2026-09-19T10:00:00+05:00'],    # open: 10:00 Tashkent, same instant as NOW
    ['dilnoza', '2020-01-01T00:00:00+05:00'],  # closed by years
]


class CountingPost:
    def __init__(self):
        self.calls = []
        self.answer = {'messages': [{'id': 'wamid.PROBE'}]}

    def __call__(self, url, token, *, method='GET', body=None, timeout=20):
        self.calls.append(body)
        return self.answer


def build(tmp):
    root = Path(tmp)
    cfg = root / 'integrations.json'
    payload = {
        'connections': {'google': {}},
        'sheets_registers': {'sales': {
            'connection': 'google', 'spreadsheet_id': SPREADSHEET,
            'ranges': {'windows': 'Oyna!A1:B'}, 'max_rows': 200}},
        'whatsapp': {'registers': {'support': {
            'connection': 'sales', 'phone_number_id': '123456789012345',
            'contacts': {'ali': '998901234567', 'dilnoza': '998907654321'},
            'templates': {'order_update': {'name': 'order_update_uz',
                                           'language': 'uz', 'category': 'utility'}},
            'window_register': 'sales', 'window_range': 'windows',
            'last_inbound_column': 'oxirgi_xabar'}}},
        'whatsapp_tokens': {'support': {
            'messaging': 'WHATSAPP_SUPPORT_MESSAGING_TOKEN'}},
    }
    cfg.write_text(json.dumps({'demo-sales': payload}), encoding='utf-8')
    os.environ['PLATFORM_INTEGRATIONS_FILE'] = str(cfg)
    os.environ['WHATSAPP_SUPPORT_MESSAGING_TOKEN'] = 'probe-token'
    engine = Engine(root / 'probe.db', build_registry(),
                    lambda t, a: dict(POLICY), clock=lambda: NOW)
    return engine


def attempt(engine, post, args, *, full=False):
    """Run one send with only the Sheets GET and the Graph POST replaced."""
    post.calls = []
    with patch('platform_runtime.sheets.configured_manager') as manager:
        manager.return_value.access.return_value.access_token = 'fake-token'
        with patch('platform_runtime.sheets._http_get',
                   side_effect=lambda url, token: {'values': WINDOW_ROWS}):
            with patch('platform_runtime.whatsapp._bounded_json',
                       side_effect=post):
                try:
                    send(engine, 'demo-sales', 'sales.outreach', args, 'probe')
                    return 'sent', len(post.calls)
                except Forbidden as error:
                    text = str(error)
                    return ('refused: ' + text) if full else ('refused: ' + text[:60]), len(post.calls)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        engine = build(tmp)
        post = CountingPost()
        rows = [
            ('open window + text',
             {'register': 'support', 'contact': 'ali', 'text': 'Salom'}),
            ('CLOSED window + text',
             {'register': 'support', 'contact': 'dilnoza', 'text': 'Salom'}),
            ('CLOSED window + declared template',
             {'register': 'support', 'contact': 'dilnoza', 'template': 'order_update'}),
            ('open window + undeclared template',
             {'register': 'support', 'contact': 'ali', 'template': 'welcome'}),
        ]
        print(f'{"case":38} {"outcome":62} POSTs')
        refused_zero = 0
        for label, args in rows:
            outcome, calls = attempt(engine, post, args)
            print(f'{label:38} {outcome:62} {calls}')
            if label.startswith('CLOSED') and outcome.startswith('refused'):
                refused_zero += calls == 0

        closed_text = rows[1]
        outcome, calls = attempt(engine, post, closed_text[1], full=True)
        assert calls == 0, 'a closed-window text send reached the provider'
        assert str(ERROR_OUTSIDE_WINDOW) in outcome, outcome
        outcome, calls = attempt(engine, post, rows[2][1])
        assert calls == 1 and outcome == 'sent', (outcome, calls)

        print()
        print(f'PROVEN: free-form text outside the window is refused with 0 provider')
        print(f'        POSTs and the refusal names error {ERROR_OUTSIDE_WINDOW}, so the')
        print(f'        provider call that would produce 131047 is unreachable; a')
        print(f'        declared template is still delivered outside the window via')
        print(f'        the only path Meta permits. Window = {WINDOW_SECONDS}s.')


if __name__ == '__main__':
    main()
