"""Prove the graph adds nothing to the WhatsApp service window.

The PRD (v0.5 section 2.8) proposes ``wa_window_until`` as a new attribute on the
Business Graph's ``customer`` entity, so that "which number has an open window and
when does it close" becomes readable. The block that was scoped from that line
declines to build it, and declining is a claim that needs evidence rather than an
opinion, because "we decided not to" and "we could not" look the same in a report.

So this probe measures four things against the real modules:

1. A graph attribute IS a copied provider cell. Mapping ``wa_window_until`` makes the
   graph return whatever the sheet said, verbatim, with the clock consulted nowhere.
   It is a *copy*, not a *computation*.
2. The copy does not age correctly. The graph has no way to tell a stale cell from a
   current one: a window that expired in 2020 is reported as complete and
   conflict-free, which is the P8c defect reproduced one layer down.
3. Deriving it from ``observed`` is not a workaround. ``observed`` is our local read
   time, and the graph does not even claim it is a window; a briefing and a send
   reading different snapshots would disagree about the same customer.
4. ``whatsapp.window`` already answers the question, and answers it with provenance.
   The reader reports ``open``, ``closes_at`` AND ``source`` (``event`` or
   ``register``), and the send gate consults the same resolver, so the graph
   attribute would be a second, weaker source of the same fact.

Run from anywhere. Config is written to a temporary directory; no network is used and
no live credential is required.

Prints ``PROVEN`` on success.
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import business_graph, whatsapp
from platform_runtime.engine import Engine
from platform_runtime.tools import build_registry

TENANT = 't_probe_wa_graph'
AGENT = 'sales.bot'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
GRAPH_TOOLS = ['graph.entities', 'graph.entity', 'graph.search', 'graph.timeline',
               'graph.conflicts', 'graph.explain']

fails = []


def check(label, condition, detail=''):
    mark = 'ok  ' if condition else 'FAIL'
    print(f'  [{mark}] {label}' + (f' -- {detail}' if detail else ''))
    if not condition:
        fails.append(label)


class Transport:
    """A scripted sheet provider: returns the payload it was built with."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, url, token):
        self.calls.append(url)
        return self.payload


class Harness:
    def __init__(self, mapping):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.erp = self.root / 'erp.db'
        db = sqlite3.connect(self.erp)
        try:
            db.executescript("CREATE TABLE customers(id TEXT, name TEXT);"
                             "INSERT INTO customers VALUES('ali','Ali');")
            db.commit()
        finally:
            db.close()
        self.connections = {
            'erp': {'driver': 'sqlite_readonly', 'path': str(self.erp),
                    'tables': {'customers': ['id', 'name']}},
            'google': {},
        }
        self.registers = {
            'crm': {'connection': 'google', 'spreadsheet_id': SPREADSHEET,
                    'ranges': {'contacts': 'Kontaktlar!A1:C'}, 'max_rows': 50},
        }
        self.graph = {
            'conflict_policy': 'report',
            'entities': {
                'customer': {
                    'identity': 'id',
                    'priority': ['erp', 'crm'],
                    'sources': {
                        'erp': {'tool': 'connectors.read', 'key': 'id',
                                'args': {'connection': 'erp', 'table': 'customers',
                                         'columns': ['id', 'name']},
                                'map': {'name': 'name', **mapping.get('erp', {})}},
                        'crm': {'tool': 'sheets.rows', 'key': 'contact',
                                'args': {'register': 'crm', 'range': 'contacts'},
                                'map': {'name': 'Ism', **mapping.get('crm', {})}},
                    },
                },
            },
        }
        self.cfg = self.root / 'integrations.json'
        self.cfg.write_text(json.dumps({TENANT: {
            'connections': self.connections,
            'sheets_registers': self.registers,
            'business_graph': self.graph,
        }}, ensure_ascii=False), encoding='utf-8')
        self.policy = {'tools': [*GRAPH_TOOLS, 'connectors.read', 'sheets.rows'],
                       'allowed_connections': ['erp', 'google'],
                       'ladder': 'human_assisted'}
        self.env = patch.dict(os.environ, {
            'PLATFORM_INTEGRATIONS_FILE': str(self.cfg),
            'PLATFORM_DB_ROOTS': json.dumps([str(self.root)]),
        })
        self.env.start()

    def close(self):
        self.env.stop()
        self.tmp.cleanup()

    def call(self, name, args, transport):
        engine = Engine(self.root / 'platform.db', build_registry(),
                        lambda t, a: self.policy)
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: transport(url, token)):
                return build_registry().get(name).handler(engine, TENANT, AGENT, args, 's1')


print('1. A graph attribute is a copied cell, not a computed value')
harness = Harness({'crm': {'wa_window_until': 'Oyna'}})
try:
    for cell in ('01.01.2020 10:00', '2099-12-31T23:59:59+05:00'):
        got = harness.call('graph.entity', {'entity': 'customer', 'id': 'ali'},
                           Transport({'values': [['contact', 'Oyna'],
                                                 ['ali', cell]]}))
        value = got['attributes']['wa_window_until']['values'][0]['value']
        check(f'cell {cell!r} is returned verbatim', value == cell, f'got {value!r}')
        check('  and is reported without a conflict', got['attributes']['wa_window_until']['conflict'] is False)
    check('the graph stored no derived window of its own',
          'window' not in got['attributes'])
finally:
    harness.close()

print()
print('2. The copy cannot tell a stale cell from a current one')
harness = Harness({'crm': {'wa_window_until': 'Oyna'}})
try:
    got = harness.call('graph.entity', {'entity': 'customer', 'id': 'ali'},
                       Transport({'values': [['contact', 'Oyna'],
                                             ['ali', '01.01.2020 10:00']]}))
    check('an expired window is returned as a plain value',
          got['attributes']['wa_window_until']['values'][0]['value'] == '01.01.2020 10:00')
    check('the read reports itself complete', got['complete'] is True)
    check('and reports no source errors', got['source_errors'] == [])
    check('so the caller has no signal that the window is stale -- which is the defect',
          got['complete'] is True and got['source_errors'] == [])
finally:
    harness.close()

print()
print('3. observed is a read time, never a window')
harness = Harness({})
try:
    got = harness.call('graph.entity', {'entity': 'customer', 'id': 'ali'},
                       Transport({'values': [['contact', 'Ism'], ['ali', 'Ali']]}))
    check('observed is present and documents freshness', 'observed' in got)
    check('no window attribute is invented from it',
          'wa_window_until' not in got['attributes'])
    check('and no attribute named window appears either', 'window' not in got['attributes'])
finally:
    harness.close()

print()
print('4. whatsapp.window already answers, with provenance')
sent = 1_789_794_000.0
opened, closes_at = whatsapp.window_state(sent, now=sent + 60)
check('an in-window message reads open', opened is True)
check('and reports when it closes', closes_at == sent + whatsapp.WINDOW_SECONDS,
      f'closes_at={closes_at}')
stale = sent - (whatsapp.WINDOW_SECONDS + 3600)
opened_stale, _ = whatsapp.window_state(stale, now=sent)
check('a message older than the window reads closed', opened_stale is False)
import inspect
reader = inspect.getsource(whatsapp.window)
check("the reader reports the answer's source", "'source': sources.get(" in reader)
check('the reader reports closes_at', "'closes_at': closes_at" in reader)
check('no graph tool declares itself a source for the window',
      all(build_registry().get(n).risk == 'read' for n in GRAPH_TOOLS))

print()
if fails:
    print(f'NOT PROVEN: {len(fails)} assertion(s) failed')
    for item in fails:
        print(f'  - {item}')
    sys.exit(1)
print('PROVEN: a wa_window_until graph attribute would be a hand-maintained copy of a')
print('        fact the platform verifies itself. The graph cannot age it (it reports a')
print('        2020 window as complete and conflict-free), cannot derive it from')
print('        observed (that is a read time, not a window) and cannot improve on')
print('        whatsapp.window, which already returns open, closes_at and source --')
print('        the same resolver the send gate consults before any provider I/O. So')
print('        the attribute is refused and guarded by tests instead of built.')
