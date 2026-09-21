"""Adversarial probe: measure the manufacturing boundary, do not describe it.

A docstring is not evidence. Every claim the module makes about what it *refuses*
is measured here against a running engine, in six sections:

1. **No OEE surface exists.** The PRD lists OEE availability and performance; the
   probe greps every key of every view and every record for an OEE or evaluative
   name and reports the count. Zero is the only passing number.
2. **No person is evaluated.** Not just "no operator column": the probe tries to
   *make* the module emit a per-person figure by planting an operator column in
   the register and confirming the module never reads it.
3. **Yield honesty is real, not aspirational.** Three adversarial registers are
   fed in — defects blank, output zero, defects above output — and each must
   produce ``yield: None``, never a number.
4. **A blank is not a zero.** The probe feeds a register of all-blank cells and
   counts how many became 0. It must be zero.
5. **Segment binding holds under the prefix attack.** zavod-1 and zavod-10 are
   read in the same call and the probe measures whether any figure crossed.
6. **An outage is not an empty factory.** The provider is made to fail and the
   probe measures whether the module returned ``complete: True`` with zero
   output (the dangerous answer) or raised (the safe one).

Exit code is non-zero when any measured property is violated, so this can be run
in CI beside the suite.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'api-python'))

from platform_runtime.engine import Engine, Forbidden  # noqa: E402
from platform_runtime.manufacturing import (  # noqa: E402
    bom,
    cycle,
    manufacturing_config,
    yield_report,
)
from platform_runtime.tools import build_registry  # noqa: E402

TENANT = 't_probe'
AGENT = 'ops.production'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

LEVELS = ['zavod', 'sex', 'liniya', 'stanok']

POLICY = {
    'tools': ['sheets.rows', 'manufacturing.bom', 'manufacturing.cycle',
              'manufacturing.yield'],
    'allowed_connections': ['plant'],
    'ladder': 'human_assisted',
}

ASSETS = {'entity': 'asset', 'levels': LEVELS,
          'measurements': ['cycle_time', 'idle_time', 'output_count']}

MANUFACTURING = {
    'bom': {'structure': {'register': 'plant', 'range': 'bom',
                          'parent_column': 'mahsulot', 'component_column': 'qism',
                          'quantity_column': 'miqdor', 'unit_column': 'birlik',
                          'scrap_column': 'brak'}},
    'cycle': {'line': {'register': 'plant', 'range': 'cycles',
                       'station_column': 'stansiya', 'duration_column': 'davomiylik',
                       'standard_column': 'standart', 'unit_column': 'birlik'}},
    'yield': {'shift': {'register': 'plant', 'range': 'output',
                        'station_column': 'stansiya', 'output_column': 'chiqim',
                        'defect_column': 'nuqson', 'unit_column': 'birlik'}},
}

REGISTERS = {
    'plant': {'connection': 'plant', 'spreadsheet_id': SPREADSHEET,
              'ranges': {'bom': 'Tarkib!A1:F', 'cycles': 'Sikl!A1:D',
                         'output': 'Chiqim!A1:D'},
              'max_rows': 200},
}

# A register that carries an operator column the module must never read.
YIELD_ROWS = [
    ['stansiya', 'chiqim', 'nuqson', 'birlik', 'operator', 'brigada'],
    ['zavod-1/sex-1/liniya-1/stanok-1', '100', '3', 'dona', 'Ali Valiyev', 'A'],
    ['zavod-1/sex-1/liniya-1/stanok-2', '200', '5', 'dona', 'Husan Rahimov', 'B'],
    ['zavod-10/sex-1/liniya-1/stanok-1', '50', '1', 'dona', 'Zilola Karimova', 'A'],
]

CYCLE_ROWS = [
    ['stansiya', 'davomiylik', 'standart', 'birlik'],
    ['zavod-1/sex-1/liniya-1/stanok-1', '12', '10', 'sekund'],
    ['zavod-1/sex-1/liniya-1/stanok-1', '14', '10', 'sekund'],
    ['zavod-10/sex-1/liniya-1/stanok-1', '99', '10', 'sekund'],
]

BOM_ROWS = [
    ['mahsulot', 'qism', 'miqdor', 'birlik', 'brak'],
    ['stol-1', 'taxta', '4', 'dona', '0.5'],
]

OEE_KEYS = ('oee', 'availability', 'performance', 'efficiency', 'score', 'rank',
            'rating', 'index', 'percent', 'target', 'utilization')
PERSON_KEYS = ('operator', 'xodim', 'worker', 'employee', 'person', 'shift',
               'brigade', 'team', 'user', 'login', 'author', 'name')

_failures = []


def check(section, condition, message):
    if condition:
        print(f'  PASS  {message}')
    else:
        print(f'  FAIL  {message}')
        _failures.append(f'{section}: {message}')


class Harness:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = self.root / 'integrations.json'
        self.rows = {'values': []}
        self.calls = []
        self.write_config()
        self.env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(self.cfg)})
        self.env.start()
        self.engine = Engine(self.root / 'probe.db', build_registry(),
                             lambda t, a: POLICY, clock=lambda: 1_770_000_000.0)

    def write_config(self, manufacturing=MANUFACTURING, assets=ASSETS):
        payload = {'connections': {'plant': {}}, 'sheets_registers': REGISTERS,
                   'business_graph': {'conflict_policy': 'report', 'entities': {}}}
        if assets is not None:
            payload['assets'] = assets
        if manufacturing is not None:
            payload['manufacturing'] = manufacturing
        self.cfg.write_text(json.dumps({TENANT: payload}, ensure_ascii=False),
                            encoding='utf-8')

    def call(self, fn, rows, **kwargs):
        self.calls = []
        self.rows = {'values': rows}
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: self._fetch(url, token)):
                return fn(self.engine, TENANT, AGENT, 's1', **kwargs)

    def _fetch(self, url, token):
        self.calls.append(url)
        return self.rows

    def close(self):
        self.env.stop()
        self.tmp.cleanup()


def keys_of(result):
    names = set(result)
    for record in result.get('stations', []) + result.get('lines', []):
        names |= set(record)
    return {name.lower() for name in names}


def blob_of(result):
    return json.dumps(result, ensure_ascii=False).lower()


def section_one(h):
    print('\n[1] No OEE or performance surface exists')
    for label, fn, rows in (
            ('bom', bom, BOM_ROWS), ('cycle', cycle, CYCLE_ROWS),
            ('yield', yield_report, YIELD_ROWS)):
        result = h.call(fn, rows)
        keys = keys_of(result)
        hits = sorted(k for k in OEE_KEYS if any(k == key for key in keys))
        check('1', not hits, f'{label}: no OEE/evaluative key in output (found {hits})')
    result = h.call(cycle, CYCLE_ROWS)
    check('1', 'P13' in result['note'],
          'cycle: the note hands OEE to P13 explicitly')


def section_two(h):
    print('\n[2] No person is evaluated, even when the register names one')
    result = h.call(yield_report, YIELD_ROWS)
    keys = keys_of(result)
    hits = sorted(k for k in PERSON_KEYS if k in keys)
    check('2', not hits, f'no person key surfaced from a register holding one (found {hits})')
    text = blob_of(result)
    leaks = [n for n in ('Ali Valiyev', 'Husan Rahimov', 'Zilola Karimova', 'brigada')
             if n.lower() in text]
    check('2', not leaks, f'no operator value leaked into output (found {leaks})')
    declared = set(manufacturing_config(TENANT)['yield']['shift'])
    person_columns = [k for k in declared if 'operator' in k or 'brigade' in k]
    check('2', not person_columns,
          f'no config key could name an operator (found {person_columns})')


def section_three(h):
    print('\n[3] Yield is refused whenever it cannot be honestly computed')
    cases = {
        'defect column absent': (
            [['stansiya', 'chiqim', 'birlik'],
             ['zavod-1/sex-1/liniya-1/stanok-1', '100', 'dona']],
            {'yield': {'shift': {'register': 'plant', 'range': 'output',
                                 'station_column': 'stansiya',
                                 'output_column': 'chiqim',
                                 'unit_column': 'birlik'}}},
        ),
        'defect cells blank': (
            [['stansiya', 'chiqim', 'nuqson', 'birlik'],
             ['zavod-1/sex-1/liniya-1/stanok-1', '100', '', 'dona']],
            MANUFACTURING,
        ),
        'output zero': (
            [['stansiya', 'chiqim', 'nuqson', 'birlik'],
             ['zavod-1/sex-1/liniya-1/stanok-1', '0', '0', 'dona']],
            MANUFACTURING,
        ),
        'defects above output': (
            [['stansiya', 'chiqim', 'nuqson', 'birlik'],
             ['zavod-1/sex-1/liniya-1/stanok-1', '10', '40', 'dona']],
            MANUFACTURING,
        ),
    }
    for label, (rows, config) in cases.items():
        h.write_config(manufacturing=config)
        result = h.call(yield_report, rows)
        figures = [s['yield'] for s in result['stations']]
        check('3', all(f is None for f in figures),
              f'{label}: yield is None, never a number ({figures})')
        check('3', all(not s['yield_computable'] for s in result['stations']),
              f'{label}: yield_computable is false')
    h.write_config()


def section_four(h):
    print('\n[4] A blank cell is not a zero')
    rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
            ['zavod-1/sex-1/liniya-1/stanok-1', '', '', 'dona'],
            ['zavod-1/sex-1/liniya-1/stanok-2', '', '', 'dona']]
    result = h.call(yield_report, rows)
    zeros = [s for s in result['stations'] if s['output'] == 0.0]
    check('4', not zeros,
          f'a register of blanks produced no zero-output station ({len(zeros)} fabricated)')
    check('4', result['unreadable_output'] == 0,
          'a blank is not counted as an unreadable value either')
    # A confident zero IS still readable, and must survive as a real zero.
    rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
            ['zavod-1/sex-1/liniya-1/stanok-1', '0', '0', 'dona']]
    result = h.call(yield_report, rows)
    check('4', len(result['stations']) == 1 and result['stations'][0]['output'] == 0.0,
          'a declared 0 still reads as a real zero')


def section_five(h):
    print('\n[5] Binding is by segment; the prefix attack fails')
    result = h.call(yield_report, YIELD_ROWS)
    paired = {s['station']: s.get('path') for s in result['stations']}
    crossed = [s for s, p in paired.items() if p and p != s]
    check('5', not crossed, f'no station was bound to another station (crossed: {crossed})')
    check('5', paired.get('zavod-1/sex-1/liniya-1/stanok-1')
          == 'zavod-1/sex-1/liniya-1/stanok-1',
          'zavod-1 keeps its own output')
    check('5', paired.get('zavod-10/sex-1/liniya-1/stanok-1')
          == 'zavod-10/sex-1/liniya-1/stanok-1',
          "zavod-10 keeps its own output (a prefix match would give it to zavod-1)")
    # Outputs must not have been merged under one identity, and zavod-10's 50 must
    # not appear under zavod-1. The fixture has two zavod-1 stations (100 and 200);
    # neither figure may equal the sibling plant's 50.
    own = sorted(s['output'] for s in result['stations']
                 if s['station'].startswith('zavod-1/'))
    check('5', own == [100.0, 200.0],
          f'zavod-1 keeps exactly its own two outputs ({own})')
    check('5', 50.0 not in own and 150.0 not in own,
          "zavod-10's 50 is absent from zavod-1's totals and nothing was merged")


def section_six(h):
    print('\n[6] An outage is not an empty factory')
    def explode(url, token):
        raise RuntimeError('provider outage')

    with patch('platform_runtime.sheets.configured_manager') as manager:
        manager.return_value.access.return_value.access_token = 'fake-token'
        with patch('platform_runtime.sheets._http_get', side_effect=explode):
            try:
                result = yield_report(h.engine, TENANT, AGENT, 's1')
                check('6', False,
                      'an outage returned a normal payload '
                      f'(complete={result.get("complete")}, '
                      f'stations={len(result.get("stations", []))}) — a manager '
                      'would read this as an idle line')
            except Exception as error:
                check('6', True,
                      f'an outage surfaced as {type(error).__name__}, not as zero output')
    # A tenant with no declaration must refuse, not report an empty factory.
    h.write_config(manufacturing=None)
    try:
        h.call(yield_report, YIELD_ROWS)
        check('6', False, 'an undeclared manufacturing block returned a payload')
    except Forbidden:
        check('6', True, 'an undeclared manufacturing block is refused by name')
    h.write_config()


def section_seven(h):
    """The properties the post-implementation audit found and fixed."""
    print('\n[7] The audit fixes hold under measurement')

    # A non-finite cell must be unreadable, not a number. inf >= 0 is true, so
    # before the fix an infinite output count accumulated into the station total.
    result = h.call(yield_report,
                    [YIELD_ROWS[0],
                     ['zavod-1/sex-1/liniya-1/stanok-1', 'inf', '0', 'dona'],
                     ['zavod-1/sex-1/liniya-1/stanok-1', '100', '5', 'dona']])
    check('7', result['unreadable_output'] == 1,
          'a non-finite output cell is counted as unreadable')
    check('7', result['stations'][0]['output'] == 100.0,
          'an infinite output never enters the station total (100, not inf)')

    result = h.call(yield_report,
                    [YIELD_ROWS[0],
                     ['zavod-1/sex-1/liniya-1/stanok-1', 'nan', '0', 'dona'],
                     ['zavod-1/sex-1/liniya-1/stanok-1', '100', '5', 'dona']])
    check('7', result['unreadable_output'] == 1,
          'a nan output cell is unreadable too, not a zero')

    result = h.call(bom, [BOM_ROWS[0], ['stol-1', 'taxta', 'inf', 'dona', '0']])
    check('7', result['unreadable_quantity'] == 1,
          'a non-finite BOM quantity is unreadable')
    check('7', result['lines'][0]['quantity'] is None,
          'and it never becomes a quantity')

    # The BOM truncation flag must be set only when a real line is dropped, not at
    # exactly the ceiling. The ceiling is lowered so the property is exercised: the
    # sheets layer caps max_rows at MAX_ROWS so no payload can exceed it.
    with patch('platform_runtime.manufacturing.MAX_BOM_LINES', 2):
        result = h.call(bom, [BOM_ROWS[0],
                              ['stol-1', 'taxta', '1', 'dona', '0'],
                              ['stol-1', 'mix', '1', 'dona', '0']])
    check('7', result['line_count'] == 2 and result['truncated'] is False,
          'exactly the ceiling, nothing dropped, is not called truncated')
    with patch('platform_runtime.manufacturing.MAX_BOM_LINES', 2):
        result = h.call(bom, [BOM_ROWS[0],
                              ['stol-1', 'taxta', '1', 'dona', '0'],
                              ['stol-1', 'mix', '1', 'dona', '0'],
                              ['stol-1', 'bolt', '1', 'dona', '0']])
    check('7', result['line_count'] == 2 and result['truncated'] is True,
          'one line past the ceiling is truncated')


def main():
    print('Manufacturing boundary probe — measuring, not asserting')
    print('=' * 62)
    h = Harness()
    try:
        section_one(h)
        section_two(h)
        section_three(h)
        section_four(h)
        section_five(h)
        section_six(h)
        section_seven(h)
    finally:
        h.close()
    print('\n' + '=' * 62)
    if _failures:
        print(f'MEASURED FAILURES: {len(_failures)}')
        for failure in _failures:
            print(f'  - {failure}')
        return 1
    print('All measured properties hold. The boundary is real, not documented.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
