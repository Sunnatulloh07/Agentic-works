"""Manufacturing contract tests. Real Engine, real SQLite, scripted provider.

The PRD lists four things Jidoka's cameras feed into an ERP: OEE availability and
performance per line and shift, SOP deviation events with timestamp and station,
cycle time per unit, and quality alert flags. This module turns *three* of them
into numbers — and the tests are mostly about the fourth, which it refuses.

Every assertion here is about a number the platform must **not** invent:

* **OEE is not computed.** Availability and performance need planned run time and
  ideal cycle time, which no register in this module declares. `vision.summary`
  already refuses it and says "bu P13 ishi"; this module keeps that refusal, so
  the test checks the returned *keys* rather than the note (the note deliberately
  contains the word OEE in order to deny it).
* **A yield is not computed from one number.** Output readable, defects not: the
  station reports ``yield: None, yield_computable: false``. Assuming zero defects
  would print 100% for a line that might reject half of its work.
* **A blank cell is not a zero.** A blank and a confident 0 are different facts;
  each is reported through its own unreadable counter.
* **A defect count above output is reported, not clamped.** Turning it into 0%
  yield would hide a data error a manager needs to see.
* **No per-person figure exists.** There is no operator column, and the module's
  own callables contain no evaluation vocabulary.
* **A station binds by segment, never by string prefix.** ``zavod-1`` must not
  claim ``zavod-10``'s output — the P11 rule, reused rather than re-implemented.
* **An unreadable register is not zero output.** An outage and an idle line must
  not look the same.

Only the Sheets HTTP hop is scripted. The register declaration, A1 allowlist,
agent tool permission, connection allowlist, asset hierarchy and SQLite engine are
all the real ones.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime.engine import Engine, Forbidden
from platform_runtime.manufacturing import (
    MAX_BOM_LINES,
    MAX_NAMES,
    MANUFACTURING_TOOLS,
    _number,
    bom,
    cycle,
    manufacturing_config,
    register_manufacturing_tools,
    yield_report,
)
from platform_runtime.tools import build_registry

TENANT = 't_plant'
AGENT = 'ops.production'
CONNECTION = 'plant'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

LEVELS = ['zavod', 'sex', 'liniya', 'stanok']

POLICY = {
    'tools': ['sheets.rows', 'manufacturing.bom', 'manufacturing.cycle',
              'manufacturing.yield'],
    'allowed_connections': [CONNECTION, 'google'],
    'ladder': 'human_assisted',
}

ASSETS = {
    'entity': 'asset',
    'levels': LEVELS,
    'measurements': ['cycle_time', 'idle_time', 'output_count'],
}

MANUFACTURING = {
    'bom': {
        'structure': {
            'register': 'plant', 'range': 'bom',
            'parent_column': 'mahsulot', 'component_column': 'qism',
            'quantity_column': 'miqdor', 'unit_column': 'birlik',
            'scrap_column': 'brak',
        },
    },
    'cycle': {
        'line': {
            'register': 'plant', 'range': 'cycles',
            'station_column': 'stansiya', 'duration_column': 'davomiylik',
            'standard_column': 'standart', 'unit_column': 'birlik',
        },
    },
    'yield': {
        'shift': {
            'register': 'plant', 'range': 'output',
            'station_column': 'stansiya', 'output_column': 'chiqim',
            'defect_column': 'nuqson', 'unit_column': 'birlik',
        },
    },
}

REGISTERS = {
    'plant': {'connection': 'google', 'spreadsheet_id': SPREADSHEET,
              'ranges': {'bom': 'Tarkib!A1:F', 'cycles': 'Sikl!A1:D',
                         'output': 'Chiqim!A1:D'},
              'max_rows': 200},
}

# Two plants whose names collide under a string prefix. zavod-10's output must
# never be attributed to zavod-1, which startswith() would do.
BOM_ROWS = [
    ['mahsulot', 'qism', 'miqdor', 'birlik', 'brak'],
    ['stol-1', 'taxta', '4', 'dona', '0.5'],
    ['stol-1', 'mix', '12', 'dona', ''],
    ['stol-1', 'bolt', 'not-a-number', 'dona', '0'],
    ['stul-1', 'taxta', '2', 'dona', '0'],
    ['', 'orphan', '1', 'dona', '0'],
]

CYCLE_ROWS = [
    ['stansiya', 'davomiylik', 'standart', 'birlik'],
    ['zavod-1/sex-1/liniya-1/stanok-1', '12', '10', 'sekund'],
    ['zavod-1/sex-1/liniya-1/stanok-1', '14', '10', 'sekund'],
    ['zavod-1/sex-1/liniya-1/stanok-2', '20', '', 'sekund'],
    ['zavod-10/sex-1/liniya-1/stanok-1', '99', '10', 'sekund'],
    ['not-a-path', '11', '10', 'sekund'],
]

YIELD_ROWS = [
    ['stansiya', 'chiqim', 'nuqson', 'birlik'],
    ['zavod-1/sex-1/liniya-1/stanok-1', '100', '3', 'dona'],
    ['zavod-1/sex-1/liniya-1/stanok-2', '200', '5', 'dona'],
    ['zavod-10/sex-1/liniya-1/stanok-1', '50', '1', 'dona'],
    ['not-a-path', '40', '2', 'dona'],
]


class _Sentinel:
    def __repr__(self):
        return 'MISSING'


_DEFAULT = _Sentinel()
_MISSING = _Sentinel()


class Clock:
    def __init__(self, start=1_770_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class RecordingTransport:
    """Stands in for the Sheets HTTP GET. Records every call."""

    def __init__(self):
        self.payload = {'values': []}
        self.calls = []

    def __call__(self, url, token):
        self.calls.append(url)
        return self.payload


def matrix(header, rows):
    return {'values': [header] + [list(row) for row in rows]}


class ManufacturingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.transport = RecordingTransport()
        self.registers = json.loads(json.dumps(REGISTERS))
        self.policy = dict(POLICY)
        self.write_config()
        self.env = patch.dict(os.environ, {
            'PLATFORM_INTEGRATIONS_FILE': str(self.cfg),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.engine = Engine(self.root / 'production.db', build_registry(),
                             lambda t, a: self.policy, clock=self.clock)

    def write_config(self, manufacturing=_DEFAULT, assets=_DEFAULT):
        """Write the tenant config. ``_MISSING`` omits the key entirely.

        ``None`` is a JSON value and an absent key is not, and the module treats
        the two differently: a missing ``manufacturing`` block means "no register
        is declared" (a refusal), while an empty block is a typo to be refused.
        """
        self.cfg = self.root / 'integrations.json'
        payload = {
            'connections': {'google': {}},
            'sheets_registers': self.registers,
        }
        if assets is not _MISSING:
            payload['assets'] = ASSETS if assets is _DEFAULT else assets
        if manufacturing is not _MISSING:
            payload['manufacturing'] = (MANUFACTURING if manufacturing is _DEFAULT
                                        else manufacturing)
        self.cfg.write_text(json.dumps({TENANT: payload}, ensure_ascii=False),
                            encoding='utf-8')

    def call(self, fn, *args, payload=None, step='s1', **kwargs):
        """Invoke a manufacturing function with only the Sheets hop replaced."""
        self.transport.calls = []
        self.transport.payload = payload if payload is not None else {'values': []}
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: self.transport(url, token)):
                return fn(self.engine, TENANT, AGENT, *args, step, **kwargs)

    # ---------------------------------------------------------- registration

    def test_every_manufacturing_tool_is_read_only(self):
        registry = build_registry()
        for name in MANUFACTURING_TOOLS:
            self.assertIn(name, registry.items)
            self.assertEqual('read', registry.items[name].risk, name)

    def test_the_module_exposes_no_write_path(self):
        """A BOM is master data: nothing here may edit the recipe it reports on."""
        import inspect
        import platform_runtime.manufacturing as module
        callables = {name for name, value in vars(module).items()
                     if not name.startswith('_')
                     and (inspect.isfunction(value) or inspect.isclass(value))
                     and getattr(value, '__module__', '') == module.__name__}
        for banned in ('create', 'update', 'set', 'delete', 'write', 'append',
                       'edit', 'save', 'store', 'record'):
            self.assertFalse([name for name in callables if banned in name.lower()],
                             f'unexpected mutating surface matching {banned}')

    def test_no_tool_accepts_a_register_range_or_column(self):
        """Register, range and column names are operator configuration."""
        for name in MANUFACTURING_TOOLS:
            props = set(self.engine.registry.items[name].schema['properties'])
            for needle in ('register', 'range', 'spreadsheet_id', 'connection',
                           'parent_column', 'station_column', 'output_column'):
                self.assertNotIn(needle, props, f'{name} accepts {needle!r}')

    def test_the_schema_forbids_extra_keys(self):
        with self.assertRaises(ValueError):
            self.engine.registry.items['manufacturing.cycle'].validate(
                {'station': 'x', 'register': 'plant'})

    # ------------------------------------------------------------------ OEE

    def test_no_view_returns_an_oee_availability_or_performance_figure(self):
        """OEE needs planned run time and ideal cycle time. P13 owns it.

        Checked against the returned *keys*, not the note, because the note
        deliberately names OEE in order to deny it — searching the whole payload
        would fail on the caveat that makes the refusal honest.
        """
        banned = ('oee', 'availability', 'performance', 'efficiency', 'score',
                  'rank', 'rating', 'index', 'percent', 'target')
        for call, payload in ((bom, matrix(BOM_ROWS[0], BOM_ROWS[1:])),
                              (cycle, matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:])),
                              (yield_report, matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))):
            with self.subTest(view=call.__name__):
                result = self.call(call, payload=payload)
                for key in banned:
                    self.assertNotIn(key, {name.lower() for name in result})
                for record in result.get('stations', []) + result.get('lines', []):
                    for key in banned:
                        self.assertNotIn(key, {name.lower() for name in record})

    def test_the_cycle_note_points_at_p13_for_oee(self):
        result = self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]))
        self.assertIn('P13', result['note'])

    def test_no_view_evaluates_a_person(self):
        """No per-operator, per-shift or per-team figure exists anywhere."""
        banned = ('operator', 'xodim', 'worker', 'employee', 'person', 'shift',
                  'brigade', 'team', 'user', 'login', 'author')
        for call, payload in ((bom, matrix(BOM_ROWS[0], BOM_ROWS[1:])),
                              (cycle, matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:])),
                              (yield_report, matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))):
            with self.subTest(view=call.__name__):
                result = self.call(call, payload=payload)
                names = set(result)
                for record in result.get('stations', []) + result.get('lines', []):
                    names |= set(record)
                for needle in banned:
                    self.assertNotIn(needle, {name.lower() for name in names})

    def test_no_column_is_declared_for_a_person(self):
        """The refusal is structural: no config key could name an operator."""
        declared = set(manufacturing_config(TENANT)['yield']['shift'])
        for needle in ('operator_column', 'person_column', 'worker_column',
                       'shift_column', 'team_column'):
            self.assertNotIn(needle, declared)

    # ---------------------------------------------------------------- cycle

    def test_cycle_averages_the_readable_durations(self):
        result = self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]))
        by_station = {s['station']: s for s in result['stations']}
        first = by_station['zavod-1/sex-1/liniya-1/stanok-1']
        self.assertEqual(2, first['samples'])
        self.assertEqual(13.0, first['mean_duration'])

    def test_a_measured_cycle_without_a_standard_is_uncompared_not_judged(self):
        """Inventing the standard is the failure mode this rule exists to stop."""
        result = self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]))
        by_station = {s['station']: s for s in result['stations']}
        second = by_station['zavod-1/sex-1/liniya-1/stanok-2']
        self.assertFalse(second['compared'])
        self.assertIsNone(second['standard'])
        self.assertNotIn('delta_seconds', second)
        self.assertNotIn('ratio', second)

    def test_a_compared_cycle_reports_a_delta_and_a_ratio_never_a_verdict(self):
        result = self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]))
        first = {s['station']: s for s in result['stations']}[
            'zavod-1/sex-1/liniya-1/stanok-1']
        self.assertTrue(first['compared'])
        self.assertEqual(3.0, first['delta_seconds'])
        self.assertEqual(1.3, first['ratio'])
        # A ratio is a fact about two numbers, not an opinion about the line.
        for verdict in ('status', 'fast', 'slow', 'good', 'bad', 'warning',
                        'alert', 'ok'):
            self.assertNotIn(verdict, {name.lower() for name in first})

    def test_the_sample_and_unreadable_counts_sit_beside_the_mean(self):
        """A mean over two units must not be mistakable for one over two hundred."""
        rows = CYCLE_ROWS + [['zavod-1/sex-1/liniya-1/stanok-1', 'o‘qilmadi', '', '']]
        result = self.call(cycle, payload=matrix(rows[0], rows[1:]))
        first = {s['station']: s for s in result['stations']}[
            'zavod-1/sex-1/liniya-1/stanok-1']
        self.assertEqual(2, first['samples'])
        self.assertEqual(1, result['unreadable_duration'])

    def test_a_negative_duration_is_unreadable_not_a_fast_cycle(self):
        rows = CYCLE_ROWS + [['zavod-1/sex-1/liniya-1/stanok-1', '-5', '10', 'sekund']]
        result = self.call(cycle, payload=matrix(rows[0], rows[1:]))
        first = {s['station']: s for s in result['stations']}[
            'zavod-1/sex-1/liniya-1/stanok-1']
        self.assertEqual(2, first['samples'])
        self.assertEqual(1, result['unreadable_duration'])

    def test_a_negative_standard_does_not_make_a_cycle_comparable(self):
        rows = CYCLE_ROWS + [['zavod-1/sex-1/liniya-1/stanok-3', '9', '-4', 'sekund']]
        result = self.call(cycle, payload=matrix(rows[0], rows[1:]))
        record = {s['station']: s for s in result['stations']}[
            'zavod-1/sex-1/liniya-1/stanok-3']
        self.assertFalse(record['compared'])

    def test_the_cycle_narrowing_argument_selects_one_station(self):
        result = self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]),
                           station='zavod-1/sex-1/liniya-1/stanok-2')
        self.assertEqual(['zavod-1/sex-1/liniya-1/stanok-2'],
                         [s['station'] for s in result['stations']])

    def test_the_cycle_limit_is_bounded(self):
        for bad in (0, -1, MAX_NAMES + 1, 10_000):
            with self.subTest(limit=bad), self.assertRaises(ValueError):
                self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]),
                          limit=bad)

    def test_the_cycle_limit_is_applied_and_reported(self):
        result = self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]),
                           limit=1)
        self.assertEqual(1, len(result['stations']))
        self.assertTrue(result['truncated'])

    # ---------------------------------------------------------------- yield

    def test_yield_is_computed_when_both_counts_are_readable(self):
        result = self.call(yield_report,
                           payload=matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))
        first = {s['station']: s for s in result['stations']}[
            'zavod-1/sex-1/liniya-1/stanok-1']
        self.assertTrue(first['yield_computable'])
        self.assertEqual(0.97, first['yield'])
        self.assertEqual(97.0, first['good'])

    def test_yield_is_none_when_no_defect_column_is_declared(self):
        """Assuming zero defects would print 100% for a possibly terrible line."""
        no_defects = json.loads(json.dumps(MANUFACTURING))
        del no_defects['yield']['shift']['defect_column']
        self.write_config(manufacturing=no_defects)
        result = self.call(yield_report,
                           payload=matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))
        self.assertFalse(result['defect_column_declared'])
        for station in result['stations']:
            self.assertIsNone(station['yield'])
            self.assertFalse(station['yield_computable'])
            self.assertIsNone(station['defect'])

    def test_yield_is_none_when_every_defect_cell_is_blank(self):
        """A blank column is not a column of zeroes."""
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '100', '', 'dona'],
                ['zavod-1/sex-1/liniya-1/stanok-2', '50', '', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        for station in result['stations']:
            self.assertIsNone(station['yield'])
            self.assertFalse(station['yield_computable'])

    def test_a_blank_defect_cell_is_not_counted_as_an_unreadable_value(self):
        """Blank is "not recorded"; unreadable is "the cell held something odd".

        Conflating the two would report a quiet shift as a data problem.
        """
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '100', '', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(0, result['unreadable_defect'])

    def test_an_unreadable_defect_cell_is_reported_not_treated_as_zero(self):
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '100', 'besh', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(1, result['unreadable_defect'])

    def test_yield_is_none_when_output_is_unreadable(self):
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1/liniya-1/stanok-1', 'yuz', '3', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(1, result['unreadable_output'])
        self.assertEqual([], result['stations'])

    def test_yield_is_none_when_output_is_zero(self):
        """Division by zero has no meaning: 0% and 100% are both fabrications."""
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '0', '0', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        station = result['stations'][0]
        self.assertIsNone(station['yield'])
        self.assertFalse(station['yield_computable'])
        self.assertEqual(0.0, station['output'])

    def test_defects_above_output_are_reported_not_clamped(self):
        """It is a data error, and quietly making it 0% yield would hide it."""
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '10', '40', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(1, result['defect_exceeds_output'])
        station = result['stations'][0]
        self.assertIsNone(station['yield'])
        self.assertFalse(station['yield_computable'])
        self.assertEqual(40.0, station['defect'])

    def test_a_row_with_no_readable_output_creates_no_station(self):
        """The probe-caught defect: a blank output row became a 0.0 station.

        ``setdefault`` ran before the readable check, so a row whose output cell
        was blank (or unreadable) produced a station reporting ``output: 0.0``.
        A manager would read that as "the line made nothing" rather than "we did
        not record what it made" — the exact fabrication this module refuses.
        """
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '', '', 'dona'],
                ['zavod-1/sex-1/liniya-1/stanok-2', '', '', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        self.assertEqual([], result['stations'])
        for station in result['stations']:
            self.assertNotEqual(0.0, station['output'])

    def test_a_declared_zero_output_still_creates_a_station(self):
        """The complement: a confident 0 is a fact and must survive as one."""
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '0', '0', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(1, len(result['stations']))
        self.assertEqual(0.0, result['stations'][0]['output'])

    def test_an_unreadable_defect_cell_makes_the_station_uncomputable(self):
        """Output must not be discarded, and yield must not be reported.

        The probe-caught defect: the unreadable-defect branch took ``continue``
        *after* the record was created, so the row's readable output was dropped
        from the sum entirely — the station silently lost its production.
        """
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '100', 'besh', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(1, result['unreadable_defect'])
        station = result['stations'][0]
        self.assertEqual(100.0, station['output'], 'output must survive the defect cell')
        self.assertIsNone(station['yield'])
        self.assertFalse(station['yield_computable'])

    def test_one_unreadable_defect_cell_makes_the_whole_station_uncomputable(self):
        """Summing the readable cells would understate defects and inflate yield."""
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '100', '3', 'dona'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '100', 'besh', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        station = result['stations'][0]
        self.assertEqual(200.0, station['output'])
        self.assertIsNone(station['yield'])
        self.assertFalse(station['yield_computable'])

    def test_a_decimal_comma_is_read_as_a_number(self):
        """Real Uzbek spreadsheets hold ``12,5``; refusing it would be a bug."""
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '100', '2,5', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        station = result['stations'][0]
        self.assertEqual(2.5, station['defect'])
        self.assertTrue(station['yield_computable'])

    # ------------------------------------------------------------------ BOM

    def test_bom_reports_one_line_per_component(self):
        """Four edges: an unreadable quantity is still a line, just an unread one."""
        result = self.call(bom, payload=matrix(BOM_ROWS[0], BOM_ROWS[1:]))
        self.assertEqual('bom', result['view'])
        self.assertEqual(4, result['line_count'])

    def test_bom_narrows_to_one_product(self):
        result = self.call(bom, payload=matrix(BOM_ROWS[0], BOM_ROWS[1:]),
                           product='stul-1')
        self.assertEqual(['stul-1'], [line['product'] for line in result['lines']])

    def test_bom_lists_every_product_it_saw(self):
        result = self.call(bom, payload=matrix(BOM_ROWS[0], BOM_ROWS[1:]))
        products = {p['product'] for p in result['products']}
        self.assertEqual({'stol-1', 'stul-1'}, products)

    def test_bom_counts_a_blank_parent_rather_than_dropping_it_silently(self):
        """A row with no parent or no component is no edge, and it is counted."""
        result = self.call(bom, payload=matrix(BOM_ROWS[0], BOM_ROWS[1:]))
        self.assertEqual(1, result['skipped'])

    def test_bom_reports_an_unreadable_quantity_rather_than_zeroing_it(self):
        result = self.call(bom, payload=matrix(BOM_ROWS[0], BOM_ROWS[1:]))
        self.assertEqual(1, result['unreadable_quantity'])

    def test_bom_never_rolls_a_cost_up(self):
        """A cost needs prices with no declared source."""
        result = self.call(bom, payload=matrix(BOM_ROWS[0], BOM_ROWS[1:]))
        blob = json.dumps(result, ensure_ascii=False)
        for needle in ('cost', 'price', 'sum', 'total', 'n costing'):
            self.assertNotIn(needle, blob.lower())

    def test_a_blank_scrap_cell_does_not_become_zero(self):
        result = self.call(bom, payload=matrix(BOM_ROWS[0], BOM_ROWS[1:]))
        lines = [line for line in result['lines'] if line['component'] == 'mix']
        self.assertIsNone(lines[0]['scrap'])

    def test_bom_lines_are_bounded(self):
        rows = [BOM_ROWS[0]] + [['stol-1', f'qism-{n}', '1', 'dona', '0']
                                for n in range(MAX_BOM_LINES + 50)]
        result = self.call(bom, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(MAX_BOM_LINES, result['line_count'])
        self.assertTrue(result['truncated'])

    def test_a_bom_of_exactly_the_ceiling_does_not_call_itself_truncated(self):
        """``>=`` would report ``True`` at exactly the cap, having dropped nothing.

        A manager told the list is truncated would go looking for the rest of a
        list that is already complete. The flag is set only when a real line is
        actually left out.

        This is a *unit* test of the flag, not an integration one: the sheets
        layer itself caps ``max_rows`` at ``MAX_ROWS`` (200), so no payload can
        ever push the BOM past its own ceiling. That is exactly why the ``>=``
        form was latent rather than visible — and exactly why it must be measured
        here, by lowering the ceiling instead of trying to exceed it.
        """
        rows = [BOM_ROWS[0]] + [['stol-1', f'qism-{n}', '1', 'dona', '0']
                                for n in range(4)]
        with patch('platform_runtime.manufacturing.MAX_BOM_LINES', 4):
            result = self.call(bom, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(4, result['line_count'])
        self.assertFalse(result['truncated'])

    def test_a_bom_one_line_past_the_ceiling_is_truncated(self):
        rows = [BOM_ROWS[0]] + [['stol-1', f'qism-{n}', '1', 'dona', '0']
                                for n in range(5)]
        with patch('platform_runtime.manufacturing.MAX_BOM_LINES', 4):
            result = self.call(bom, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(4, result['line_count'])
        self.assertTrue(result['truncated'])

    def test_a_non_finite_quantity_is_unreadable_not_a_huge_number(self):
        """``inf >= 0`` is true, so an infinite quantity would sail through.

        A non-finite value is a real Python float and would otherwise read as
        "readable". The same hole was closed in P13's ``oee._number``.
        """
        rows = [BOM_ROWS[0], ['stol-1', 'taxta', 'inf', 'dona', '0']]
        result = self.call(bom, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(1, result['unreadable_quantity'])
        self.assertIsNone(result['lines'][0]['quantity'])

    def test_a_nan_quantity_is_unreadable_not_a_zero(self):
        rows = [BOM_ROWS[0], ['stol-1', 'taxta', 'nan', 'dona', '0']]
        result = self.call(bom, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(1, result['unreadable_quantity'])
        self.assertIsNone(result['lines'][0]['quantity'])

    def test_a_non_finite_output_count_is_unreadable(self):
        """``inf >= 0`` is true, so an infinite count would sail through as readable.

        The unreadable cell must not be summed. A station whose only output cell is
        non-finite has no output to attribute and is not fabricated as a zero row;
        the second, readable row keeps the station present so the value can be
        inspected.
        """
        rows = [YIELD_ROWS[0],
                ['zavod-1/sex-1/liniya-1/stanok-1', 'inf', '0', 'dona'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '100', '5', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(1, result['unreadable_output'])
        station = result['stations'][0]
        # Only the readable 100 was summed; the inf never entered the total.
        self.assertEqual(100.0, station['output'])

    def test_a_non_finite_cycle_duration_is_not_a_slow_cycle(self):
        rows = [CYCLE_ROWS[0],
                ['zavod-1/sex-1/liniya-1/stanok-1', 'inf', '10', 'sekund'],
                ['zavod-1/sex-1/liniya-1/stanok-1', '12', '10', 'sekund']]
        result = self.call(cycle, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(1, result['unreadable_duration'])
        station = result['stations'][0]
        # The mean is over the one readable sample, not over an infinite one.
        self.assertEqual(1, station['samples'])
        self.assertEqual(12.0, station['mean_duration'])

    # ------------------------------------------------------- asset binding

    def test_stations_bind_to_the_asset_hierarchy_by_segment(self):
        """The P11 rule, applied to the figures that depend on it.

        'zavod-10'.startswith('zavod-1') is True, so a prefix match would attribute
        one plant's output to the other and never say so.
        """
        result = self.call(yield_report,
                           payload=matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))
        paired = {s['station']: s.get('path') for s in result['stations']}
        self.assertEqual('zavod-1/sex-1/liniya-1/stanok-1',
                         paired['zavod-1/sex-1/liniya-1/stanok-1'])
        self.assertEqual('zavod-10/sex-1/liniya-1/stanok-1',
                         paired['zavod-10/sex-1/liniya-1/stanok-1'])

    def test_an_unbound_station_is_reported_without_a_path_not_guessed(self):
        """An empty string must never be readable as "this station has no place"."""
        result = self.call(yield_report,
                           payload=matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))
        unbound = [s for s in result['stations'] if s['station'] == 'not-a-path']
        self.assertEqual(1, len(unbound))
        self.assertNotIn('path', unbound[0])
        self.assertEqual(1, result['unbound'])

    def test_a_station_of_the_wrong_depth_is_unbound(self):
        rows = [['stansiya', 'chiqim', 'nuqson', 'birlik'],
                ['zavod-1/sex-1', '10', '1', 'dona']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        self.assertEqual(1, result['unbound'])

    def test_every_bound_station_maps_to_its_own_path(self):
        result = self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]))
        for station in result['stations']:
            if 'path' in station:
                self.assertEqual(station['station'], station['path'])

    def test_a_read_without_an_asset_hierarchy_is_forbidden(self):
        """P11 must exist first: a figure with nothing to bind to has no owner."""
        self.write_config(assets=_MISSING)
        with self.assertRaises(Forbidden):
            self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]))

    # -------------------------------------------------------- unreadability

    def test_an_unreadable_register_is_not_reported_as_zero_output(self):
        """An outage and an idle line must not look the same."""
        def explode(url, token):
            raise RuntimeError('provider outage')

        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get', side_effect=explode):
                with self.assertRaises(RuntimeError):
                    yield_report(self.engine, TENANT, AGENT, 's1')

    def test_a_register_that_returns_a_wrong_shape_is_refused(self):
        self.transport.payload = {'values': 'not-a-list'}
        with self.assertRaises(RuntimeError):
            self.call(yield_report, payload={'values': 'not-a-list'})

    def test_a_register_is_read_once_per_call(self):
        """No per-station re-read: the scan is one bounded pass."""
        result = self.call(yield_report,
                           payload=matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))
        self.assertGreater(result['station_count'], 1)
        self.assertEqual(1, len(self.transport.calls))

    def test_a_malformed_row_does_not_break_the_read(self):
        rows = YIELD_ROWS + [['', '', '', '']]
        result = self.call(yield_report, payload=matrix(rows[0], rows[1:]))
        self.assertGreater(result['station_count'], 0)
        self.assertEqual(1, result['skipped'])

    def test_no_credential_or_url_reaches_the_output(self):
        result = self.call(yield_report,
                           payload=matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))
        blob = json.dumps(result, ensure_ascii=False)
        for needle in ('token', 'secret', 'http://', 'https://', 'Bearer',
                       SPREADSHEET):
            self.assertNotIn(needle, blob)

    def test_the_output_carries_the_agent_authority(self):
        result = self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]))
        self.assertEqual(AGENT, result['authority']['agent'])
        self.assertEqual('human_assisted', result['authority']['ladder'])

    # ------------------------------------------------------------- authority

    def test_the_source_tool_must_be_held_by_the_agent(self):
        """A register read is not free: the agent must hold ``sheets.rows``."""
        self.policy = dict(POLICY, tools=['manufacturing.yield'])
        with self.assertRaises(Forbidden):
            self.call(yield_report, payload=matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))

    def test_a_connection_the_agent_does_not_hold_is_refused(self):
        self.policy = dict(POLICY, allowed_connections=['other'])
        with self.assertRaises(Forbidden):
            self.call(yield_report, payload=matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))

    def test_an_undeclared_manufacturing_block_is_forbidden(self):
        """A declared absence is a refusal, not an empty factory."""
        self.write_config(manufacturing=_MISSING)
        with self.assertRaises(Forbidden):
            self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]))

    def test_a_view_with_no_declared_register_is_forbidden(self):
        """A tenant with a yield register cannot satisfy a cycle read."""
        only_yield = {'yield': MANUFACTURING['yield']}
        self.write_config(manufacturing=only_yield)
        with self.assertRaises(Forbidden):
            self.call(cycle, payload=matrix(CYCLE_ROWS[0], CYCLE_ROWS[1:]))

    def test_two_registers_for_one_view_are_ambiguous_and_refused(self):
        two = json.loads(json.dumps(MANUFACTURING))
        two['yield']['second'] = dict(two['yield']['shift'], register='plant')
        self.write_config(manufacturing=two)
        with self.assertRaises(Forbidden):
            self.call(yield_report, payload=matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))

    # --------------------------------------------------------------- config

    def test_an_unknown_manufacturing_key_is_refused(self):
        broken = json.loads(json.dumps(MANUFACTURING))
        broken['oee'] = {}
        self.write_config(manufacturing=broken)
        with self.assertRaises(ValueError):
            manufacturing_config(TENANT)

    def test_an_unknown_column_key_is_refused(self):
        """A typo in a column name must not silently read the wrong column."""
        broken = json.loads(json.dumps(MANUFACTURING))
        broken['yield']['shift']['perf_column'] = 'x'
        self.write_config(manufacturing=broken)
        with self.assertRaises(ValueError):
            manufacturing_config(TENANT)

    def test_a_bom_without_a_component_column_is_refused(self):
        broken = json.loads(json.dumps(MANUFACTURING))
        del broken['bom']['structure']['component_column']
        self.write_config(manufacturing=broken)
        with self.assertRaises(ValueError):
            manufacturing_config(TENANT)

    def test_a_cycle_without_a_duration_column_is_refused(self):
        broken = json.loads(json.dumps(MANUFACTURING))
        del broken['cycle']['line']['duration_column']
        self.write_config(manufacturing=broken)
        with self.assertRaises(ValueError):
            manufacturing_config(TENANT)

    def test_an_invalid_register_name_is_refused(self):
        broken = json.loads(json.dumps(MANUFACTURING))
        broken['yield']['shift']['register'] = 'Not A Register'
        self.write_config(manufacturing=broken)
        with self.assertRaises(ValueError):
            manufacturing_config(TENANT)

    def test_an_undeclared_register_is_refused_at_read_time(self):
        """Configuration can name a register that does not exist; the read refuses."""
        broken = json.loads(json.dumps(MANUFACTURING))
        broken['yield']['shift']['register'] = 'phantom'
        broken['yield']['shift']['range'] = 'output'
        self.write_config(manufacturing=broken)
        with self.assertRaises(Forbidden):
            self.call(yield_report, payload=matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))

    def test_an_undeclared_range_is_refused_at_read_time(self):
        broken = json.loads(json.dumps(MANUFACTURING))
        broken['yield']['shift']['range'] = 'phantom_range'
        self.write_config(manufacturing=broken)
        with self.assertRaises(Forbidden):
            self.call(yield_report, payload=matrix(YIELD_ROWS[0], YIELD_ROWS[1:]))

    def test_an_empty_manufacturing_block_reports_all_three_views(self):
        """An empty block is a declaration of nothing, read as three empty maps."""
        self.write_config(manufacturing={})
        declared = manufacturing_config(TENANT)
        self.assertEqual({'bom': {}, 'cycle': {}, 'yield': {}}, declared)

    def test_a_missing_manufacturing_block_is_three_empty_maps(self):
        self.write_config(manufacturing=_MISSING)
        declared = manufacturing_config(TENANT)
        self.assertEqual({'bom': {}, 'cycle': {}, 'yield': {}}, declared)

    def test_an_integer_past_the_float_range_is_unreadable_not_fatal(self):
        """The numeric path, where the finite guard is the only defence.

        ``float(10 ** 400)`` raises ``OverflowError`` rather than returning
        infinity, so a provider cell holding an absurd integer crashed the read.
        The existing non-finite test feeds the STRING ``'inf'``, which the numeric
        regex rejects before the guard is reached -- it never exercises this path.
        """
        self.assertIsNone(_number(10 ** 400))
        self.assertIsNone(_number(float('inf')))
        self.assertIsNone(_number(float('nan')))
        self.assertEqual(1e308, _number(10 ** 308))


if __name__ == '__main__':
    unittest.main()
