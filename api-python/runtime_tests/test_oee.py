"""OEE and andon contract tests. Real Engine, real SQLite, scripted provider.

P12 refused OEE by name and handed it here. The tests are about the *refusal
discipline* that makes accepting the handover safe, not about the formula, which
is arithmetic anyone can check:

* **The formula is right.** A worked example with hand-computed factors proves
  ``availability * performance * quality`` produces the expected number; if the
  arithmetic were wrong every other assertion would be meaningless.
* **A factor is never defaulted.** Each of the three factors needs declared
  inputs. When one is absent the factor is ``None``, the OEE is ``None``, and the
  missing input is *named* — rather than assuming a plan time or an ideal cycle.
  This is the whole reason P12 refused the figure.
* **An unusable input is not a missing one.** A produced count of zero and a good
  count above produced are data errors, not absences, and are reported separately
  because the fix is a different action.
* **A nonsense ratio cannot escape.** Good units above produced must not yield a
  quality factor above 1.0, which would produce an OEE of 347%.
* **No index is reported that is half-invented.** A partial product is not an
  OEE, so ``oee`` is ``None`` unless every factor is computable.
* **An uncomputable station is not a threshold breach.** Otherwise a missing
  column name would page a manager about a failing line.
* **The platform does not decide what a bad OEE is.** Thresholds are operator
  configuration; with none declared, ``andon`` refuses rather than inventing one.
* **No person is evaluated**, structurally: no operator column and no config key
  that could name one.
* **A register is read once**, an outage surfaces, and no credential or URL
  reaches the output.

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
from platform_runtime.oee import (
    FACTORS,
    MAX_STATIONS,
    OEE_TOOLS,
    _number,
    andon,
    oee_config,
    register_oee_tools,
    report,
)
from platform_runtime.tools import build_registry

TENANT = 't_plant'
AGENT = 'ops.oee'
CONNECTION = 'plant'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

LEVELS = ['zavod', 'sex', 'liniya', 'stanok']

POLICY = {
    'tools': ['sheets.rows', 'oee.report', 'oee.andon'],
    'allowed_connections': [CONNECTION, 'google'],
    'ladder': 'human_assisted',
}

ASSETS = {
    'entity': 'asset',
    'levels': LEVELS,
    'measurements': ['cycle_time', 'idle_time', 'output_count'],
}

# The full mapping: every input declared, so OEE is computable.
OEE = {
    'registers': {
        'line': {
            'register': 'plant', 'range': 'oee',
            'station_column': 'stansiya',
            'planned_run_column': 'reja',
            'ideal_cycle_column': 'ideal',
            'run_column': 'ish',
            'produced_column': 'chiqim',
            'good_column': 'yaxshi',
            'unplanned_column': 'rejasiz',
        },
    },
    'thresholds': {'oee': {'below': 85}},
}

REGISTERS = {
    'plant': {'connection': 'google', 'spreadsheet_id': SPREADSHEET,
              'ranges': {'oee': 'OEE!A1:H'}, 'max_rows': 200},
}

HEADER = ['stansiya', 'reja', 'ideal', 'ish', 'chiqim', 'yaxshi', 'rejasiz']

# A worked example with hand-computed factors:
#   planned 28800s (8h), run 26000s, ideal 10s, produced 2400, good 2304
#   availability = 26000 / 28800 = 0.902778
#   performance  = (10 * 2400) / 26000 = 24000 / 26000 = 0.923077
#   quality      = 2304 / 2400 = 0.96
#   OEE          = 0.902778 * 0.923077 * 0.96 = 0.8 exactly
STATION = 'zavod-1/sex-1/liniya-1/stanok-1'
FULL = [STATION, '28800', '10', '26000', '2400', '2304', '1800']


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


class OeeTests(unittest.TestCase):
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
        self.engine = Engine(self.root / 'oee.db', build_registry(),
                             lambda t, a: self.policy, clock=self.clock)

    def write_config(self, oee=_DEFAULT, assets=_DEFAULT):
        """Write the tenant config. ``_MISSING`` omits the key entirely."""
        self.cfg = self.root / 'integrations.json'
        payload = {
            'connections': {'google': {}},
            'sheets_registers': self.registers,
        }
        if assets is not _MISSING:
            payload['assets'] = ASSETS if assets is _DEFAULT else assets
        if oee is not _MISSING:
            payload['oee'] = OEE if oee is _DEFAULT else oee
        self.cfg.write_text(json.dumps({TENANT: payload}, ensure_ascii=False),
                            encoding='utf-8')

    def rebuild(self, key):
        self.engine = Engine(self.root / (key + '.db'), build_registry(),
                             lambda t, a: self.policy, clock=self.clock)

    def call(self, fn, *args, payload=None, step='s1', **kwargs):
        """Invoke an oee function with only the Sheets hop replaced."""
        self.transport.calls = []
        self.transport.payload = payload if payload is not None else {'values': []}
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: self.transport(url, token)):
                return fn(self.engine, TENANT, AGENT, *args, step, **kwargs)

    def configure(self, oee, payload=None, key='cfg'):
        """Rewrite the config, rebuild the engine, and read once."""
        self.write_config(oee=oee)
        self.rebuild(key)
        return self.call(report, payload=payload if payload is not None
                         else matrix(HEADER, [FULL]))

    def configure_andon(self, oee, payload=None, key='andon'):
        self.write_config(oee=oee)
        self.rebuild(key)
        return self.call(andon, payload=payload if payload is not None
                         else matrix(HEADER, [FULL]))

    # ---------------------------------------------------------- registration

    def test_every_oee_tool_is_read_only(self):
        registry = build_registry()
        for name in OEE_TOOLS:
            self.assertIn(name, registry.items)
            self.assertEqual('read', registry.items[name].risk, name)

    def test_the_module_exposes_no_write_path(self):
        """An entity able to write could declare its own plan and score itself."""
        import inspect
        import platform_runtime.oee as module
        callables = {name for name, value in vars(module).items()
                     if not name.startswith('_')
                     and (inspect.isfunction(value) or inspect.isclass(value))
                     and getattr(value, '__module__', '') == module.__name__}
        for banned in ('create', 'update', 'set', 'delete', 'write', 'append',
                       'edit', 'save', 'store', 'record'):
            self.assertFalse([name for name in callables if banned in name.lower()],
                             f'unexpected mutating surface matching {banned}')

    def test_no_tool_accepts_a_register_range_or_column(self):
        for name in OEE_TOOLS:
            props = set(self.engine.registry.items[name].schema['properties'])
            for needle in ('register', 'range', 'spreadsheet_id', 'connection',
                           'planned_run_column', 'ideal_cycle_column'):
                self.assertNotIn(needle, props, f'{name} accepts {needle!r}')

    def test_the_schema_forbids_extra_keys(self):
        with self.assertRaises(ValueError):
            self.engine.registry.items['oee.report'].validate(
                {'station': 'x', 'register': 'plant'})

    # ------------------------------------------------------------ the formula

    def test_the_three_factors_and_the_product_are_correct(self):
        """Hand-computed: 0.902778 * 0.923077 * 0.96 = 0.8 exactly."""
        result = self.call(report, payload=matrix(HEADER, [FULL]))
        station = result['stations'][0]
        self.assertEqual(0.9028, station['availability'])
        self.assertEqual(0.9231, station['performance'])
        self.assertAlmostEqual(0.96, station['quality'], places=6)
        self.assertEqual(0.8, station['oee'])
        self.assertTrue(station['oee_computable'])

    def test_every_figure_ships_the_inputs_that_produced_it(self):
        """A percentage with no visible denominator cannot be checked."""
        station = self.call(report, payload=matrix(HEADER, [FULL]))['stations'][0]
        self.assertEqual({
            'planned_run_seconds': 28800.0,
            'run_seconds': 26000.0,
            'ideal_cycle_seconds': 10.0,
            'produced': 2400.0,
            'good': 2304.0,
            'unplanned_seconds': 1800.0,
        }, station['inputs'])

    def test_the_oee_equals_the_product_of_its_own_reported_factors(self):
        """The index must be reproducible from what the payload shows."""
        station = self.call(report, payload=matrix(HEADER, [FULL]))['stations'][0]
        product = 1.0
        for factor in FACTORS:
            product *= station[factor]
        self.assertAlmostEqual(station['oee'], product, places=4)

    # -------------------------------------------------- factor not defaulted

    def test_no_plan_declared_refuses_availability_but_not_performance(self):
        """P12's exact reason for refusing OEE, now measured.

        Availability is ``run / planned`` and cannot exist without the plan.
        Performance is ``ideal * produced / run`` — three measured values — so it
        genuinely does not need the plan, and refusing it too would throw away
        information the customer did declare. The *product* is still refused,
        which is the part that matters: no OEE is reported.
        """
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'ideal_cycle_column': 'ideal',
                 'run_column': 'ish', 'produced_column': 'chiqim',
                 'good_column': 'yaxshi'}
        result = self.configure({'registers': {'line': entry}}, key='noplan')
        station = result['stations'][0]
        self.assertIsNone(station['oee'])
        self.assertFalse(station['oee_computable'])
        self.assertIsNone(station['availability'])
        self.assertEqual(0.9231, station['performance'])
        self.assertEqual(0.96, station['quality'], 'quality needs no plan either')
        self.assertIn('planned_run_seconds',
                      station['not_computable']['absent'])

    def test_no_ideal_cycle_refuses_performance_only(self):
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'planned_run_column': 'reja',
                 'run_column': 'ish', 'produced_column': 'chiqim',
                 'good_column': 'yaxshi'}
        result = self.configure({'registers': {'line': entry}}, key='noideal')
        station = result['stations'][0]
        self.assertIsNone(station['oee'])
        self.assertIsNone(station['performance'])
        self.assertEqual(0.9028, station['availability'])
        self.assertEqual(0.96, station['quality'])
        self.assertIn('ideal_cycle_seconds', station['not_computable']['absent'])

    def test_a_plan_declared_as_a_constant_is_used(self):
        """Most small factories know the plan as 'each shift is 8 hours'."""
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'planned_run_seconds': 28800,
                 'ideal_cycle_column': 'ideal', 'run_column': 'ish',
                 'produced_column': 'chiqim', 'good_column': 'yaxshi'}
        result = self.configure({'registers': {'line': entry}}, key='constant')
        station = result['stations'][0]
        self.assertTrue(station['oee_computable'])
        self.assertEqual(0.8, station['oee'])

    def test_a_declared_column_wins_over_the_constant(self):
        """A per-row value is more specific than a default.

        The header states this precedence explicitly, because declaring both is
        accepted configuration rather than an ambiguity the platform refuses.
        """
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'planned_run_seconds': 100,
                 'planned_run_column': 'reja', 'ideal_cycle_column': 'ideal',
                 'run_column': 'ish', 'produced_column': 'chiqim',
                 'good_column': 'yaxshi'}
        result = self.configure({'registers': {'line': entry}}, key='precedence')
        station = result['stations'][0]
        self.assertEqual(28800.0, station['inputs']['planned_run_seconds'])
        self.assertEqual(0.9028, station['availability'])

    def test_the_constant_fills_in_for_a_blank_planned_cell(self):
        """'Column wins' must not mean 'blank cell refuses availability'.

        The constant is the station default; a row the customer did not fill in
        is exactly what a default is for. Without this the two declarations
        together would behave worse than the constant alone.
        """
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'planned_run_seconds': 28800,
                 'planned_run_column': 'reja', 'ideal_cycle_column': 'ideal',
                 'run_column': 'ish', 'produced_column': 'chiqim',
                 'good_column': 'yaxshi'}
        result = self.configure({'registers': {'line': entry}}, key='blankplan',
                                payload=matrix(
                                    HEADER,
                                    [[STATION, '', '10', '26000', '2400',
                                      '2304', '1800']]))
        station = result['stations'][0]
        self.assertEqual(28800.0, station['inputs']['planned_run_seconds'])
        self.assertEqual(0.9028, station['availability'])

    def test_an_unreadable_planned_cell_does_not_become_the_constant(self):
        """A non-blank unreadable cell is neither the row's value nor the default.

        The customer wrote *something* there, so silently substituting the
        station constant would report a plan the customer did not declare for
        that row. It is counted instead.
        """
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'planned_run_seconds': 28800,
                 'planned_run_column': 'reja', 'ideal_cycle_column': 'ideal',
                 'run_column': 'ish', 'produced_column': 'chiqim',
                 'good_column': 'yaxshi'}
        result = self.configure({'registers': {'line': entry}}, key='badplan',
                                payload=matrix(
                                    HEADER,
                                    [[STATION, 'yoq', '10', '26000', '2400',
                                      '2304', '1800']]))
        station = result['stations'][0]
        self.assertIsNone(station['inputs']['planned_run_seconds'])
        self.assertIsNone(station['availability'])
        self.assertIn('planned_run_seconds', station['not_computable']['absent'])
        self.assertGreater(result['unreadable'], 0)

    def test_both_plan_sources_together_are_accepted_configuration(self):
        """Two plan sources is not the ambiguity two *registers* is.

        The refusal boundary is stated in the header; this test pins it so a
        future edit cannot turn a working configuration into a hard failure.
        """
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'planned_run_seconds': 28800,
                 'planned_run_column': 'reja', 'ideal_cycle_column': 'ideal',
                 'run_column': 'ish', 'produced_column': 'chiqim',
                 'good_column': 'yaxshi'}
        self.write_config(oee={'registers': {'line': entry}})
        declared = oee_config(TENANT)
        self.assertEqual('reja', declared['registers']['line']['planned_run_column'])
        self.assertEqual(28800.0,
                         declared['registers']['line']['planned_run_seconds'])

    def test_no_input_at_all_still_reads_and_says_what_is_missing(self):
        """Nothing declared is a readable answer, not a crash."""
        entry = {'register': 'plant', 'range': 'oee', 'station_column': 'stansiya'}
        result = self.configure({'registers': {'line': entry}}, key='empty')
        station = result['stations'][0]
        self.assertIsNone(station['oee'])
        for name in ('planned_run_seconds', 'ideal_cycle_seconds', 'produced'):
            self.assertIn(name, station['not_computable']['absent'])

    # ------------------------------------------- unusable is not absent

    def test_produced_zero_is_reported_unusable_not_absent(self):
        """Division by zero is a data fact, not a missing column."""
        result = self.call(report, payload=matrix(
            HEADER, [[STATION, '28800', '10', '26000', '0', '0', '1800']]))
        station = result['stations'][0]
        self.assertIsNone(station['quality'])
        self.assertEqual(['produced'], station['not_computable']['unusable'])
        self.assertEqual([], station['not_computable']['absent'])

    def test_good_above_produced_cannot_produce_a_ratio_above_one(self):
        """Without this guard the OEE would read 347%, which destroys trust."""
        result = self.call(report, payload=matrix(
            HEADER, [[STATION, '28800', '10', '26000', '2400', '9999', '1800']]))
        station = result['stations'][0]
        self.assertIsNone(station['quality'])
        self.assertIsNone(station['oee'])
        self.assertFalse(station['oee_computable'])
        self.assertEqual(1, result['good_exceeds_produced'])
        self.assertEqual(['good'], station['not_computable']['unusable'])

    def test_a_quality_factor_can_never_exceed_one(self):
        for produced, good in ((100, 100), (100, 99), (100, 1), (100, 0)):
            with self.subTest(produced=produced, good=good):
                result = self.call(report, payload=matrix(
                    HEADER, [[STATION, '28800', '10', '26000',
                              str(produced), str(good), '1800']]))
                quality = result['stations'][0]['quality']
                if quality is not None:
                    self.assertLessEqual(quality, 1.0)

    def test_a_run_longer_than_the_plan_is_not_clamped(self):
        """It is a declaration error, and clamping hides it behind a perfect 1.0."""
        result = self.call(report, payload=matrix(
            HEADER, [[STATION, '28800', '10', '30000', '2400', '2304', '0']]))
        station = result['stations'][0]
        self.assertGreater(station['availability'], 1.0)

    def test_a_blank_planned_cell_falls_back_to_the_constant_not_to_zero(self):
        """A blank is not a zero: zero planned time would be a division by zero."""
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'planned_run_seconds': 28800,
                 'planned_run_column': 'reja', 'ideal_cycle_column': 'ideal',
                 'run_column': 'ish', 'produced_column': 'chiqim',
                 'good_column': 'yaxshi'}
        result = self.configure({'registers': {'line': entry}},
                                payload=matrix(HEADER, [[STATION, '', '10', '26000',
                                                         '2400', '2304', '0']]),
                                key='blankplan')
        station = result['stations'][0]
        self.assertEqual(28800.0, station['inputs']['planned_run_seconds'])

    def test_a_non_blank_unreadable_planned_cell_is_counted_not_defaulted(self):
        """A blank falls back to the constant; a text cell does not.

        The two are different facts. Papering over 'sakkiz soat' with the declared
        default would hide a data problem behind a number that looks planned.
        """
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'planned_run_seconds': 28800,
                 'planned_run_column': 'reja',
                 'ideal_cycle_column': 'ideal', 'run_column': 'ish',
                 'produced_column': 'chiqim', 'good_column': 'yaxshi'}
        result = self.configure({'registers': {'line': entry}},
                                payload=matrix(HEADER, [[STATION, 'sakkiz soat',
                                                         '10', '26000', '2400',
                                                         '2304', '0']]),
                                key='badplan')
        self.assertEqual(1, result['unreadable'])
        self.assertIsNone(result['stations'][0]['availability'])
        self.assertTrue(result['stations'][0]['oee_computable'] is False)

    def test_a_decimal_comma_is_read_as_a_number(self):
        result = self.call(report, payload=matrix(
            HEADER, [[STATION, '28800', '10,5', '26000', '2400', '2304', '0']]))
        station = result['stations'][0]
        self.assertEqual(10.5, station['inputs']['ideal_cycle_seconds'])

    # ----------------------------------------------------------- partial OEE

    def test_a_partial_oee_is_never_reported_as_a_number(self):
        """An index assembled from one measured factor and two defaults looks
        complete and is not, which is worse than a refusal."""
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'planned_run_column': 'reja',
                 'run_column': 'ish', 'produced_column': 'chiqim',
                 'good_column': 'yaxshi'}
        result = self.configure({'registers': {'line': entry}}, key='partial')
        for station in result['stations']:
            if not all(station[f] is not None for f in FACTORS):
                self.assertIsNone(station['oee'])
                self.assertFalse(station['oee_computable'])

    def test_computable_count_matches_the_records(self):
        result = self.call(report, payload=matrix(HEADER, [FULL]))
        expected = sum(1 for s in result['stations'] if s['oee_computable'])
        self.assertEqual(expected, result['computable_count'])

    # ----------------------------------------------------------------- andon

    def test_a_breach_is_reported_with_its_value_and_threshold(self):
        result = self.call(andon, payload=matrix(HEADER, [FULL]))
        self.assertEqual(1, result['breach_count'])
        breach = result['breaches'][0]
        self.assertEqual('oee', breach['metric'])
        self.assertEqual(80.0, breach['value'])
        self.assertEqual(85.0, breach['threshold'])
        self.assertEqual('below', breach['breach'])

    def test_a_value_inside_its_threshold_is_not_a_breach(self):
        result = self.configure_andon(
            {'registers': OEE['registers'], 'thresholds': {'oee': {'below': 50}}},
            key='inside')
        self.assertEqual([], result['breaches'])

    def test_an_above_threshold_is_honoured(self):
        result = self.configure_andon(
            {'registers': OEE['registers'], 'thresholds': {'oee': {'above': 50}}},
            key='above')
        self.assertEqual('above', result['breaches'][0]['breach'])

    def test_a_factor_threshold_is_checked_independently_of_the_product(self):
        """Availability falling is a maintenance signal even when OEE is fine."""
        result = self.configure_andon(
            {'registers': OEE['registers'],
             'thresholds': {'availability': {'below': 95}}}, key='factor')
        breach = result['breaches'][0]
        self.assertEqual('availability', breach['metric'])
        self.assertEqual(90.28, breach['value'])

    def test_an_uncomputable_station_is_not_a_breach(self):
        """Otherwise a missing column name would page a manager about a line."""
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'run_column': 'ish',
                 'produced_column': 'chiqim', 'good_column': 'yaxshi'}
        result = self.configure_andon(
            {'registers': {'line': entry}, 'thresholds': {'oee': {'below': 85}}},
            key='uncomputable')
        self.assertEqual(0, result['breach_count'])
        self.assertEqual(1, result['not_evaluated'])

    def test_no_declared_threshold_is_a_refusal_not_a_default(self):
        """The platform does not decide that 85% is a bad OEE.

        With no threshold declared there is nothing to compare against, and
        inventing one would page a manager about a bound they never chose. This is
        the same rule as the recipient in P6: alerting configuration belongs to
        the operator, and a missing one is a refusal rather than a default.
        """
        self.write_config(oee={'registers': OEE['registers']})
        self.rebuild('nothresh')
        with self.assertRaises(Forbidden) as caught:
            self.call(andon, payload=matrix(HEADER, [FULL]))
        self.assertIn('threshold', str(caught.exception).lower())

    def test_an_undeclared_threshold_block_is_forbidden(self):
        self.write_config(oee={'registers': OEE['registers']})
        self.rebuild('notr')
        with self.assertRaises(Forbidden):
            self.call(andon, payload=matrix(HEADER, [FULL]))

    def test_the_thresholds_ship_with_the_breaches(self):
        """A breach is only checkable if the bound is visible beside it."""
        result = self.call(andon, payload=matrix(HEADER, [FULL]))
        self.assertEqual({'oee': {'below': 85.0}}, result['thresholds'])

    def test_the_andon_note_states_it_is_a_fact_not_a_verdict(self):
        result = self.call(andon, payload=matrix(HEADER, [FULL]))
        self.assertIn('FAKTI', result['note'])
        self.assertIn('jazo emas', result['note'])

    def test_the_andon_note_warns_when_the_list_is_not_complete(self):
        """Truncation is a caveat on a report and a hole in an alarm.

        The note must say so, because a caller reading ``breach_count: 0`` on a
        truncated andon could otherwise conclude the plant is fine when the
        register simply held more stations than were listed.
        """
        result = self.call(andon, payload=matrix(HEADER, [FULL]))
        self.assertIn('TO‘LIQ EMAS', result['note'])
        self.assertIn('truncated=true', result['note'])

    def test_the_andon_sees_a_breach_beyond_the_callers_limit(self):
        """The defect this line exists to prevent: a silent stop at station 51.

        ``report`` truncates eagerly, so an andon that inherited the display
        limit would evaluate only the first ``limit`` stations and report
        ``breach_count: 0`` for a plant that is on fire. The breach here sits
        behind the window and must still be found.
        """
        rows = [HEADER]
        for index in range(5):
            # The first four run a full 8h with no unplanned stop, so their OEE is
            # 1.0 and healthy; the fifth (behind a limit of 2) is the breach.
            good = ['28800', '10', '28800', '2880', '2880', '0'] if index < 4 \
                else ['28800', '10', '26000', '2400', '1200', '0']
            rows.append([f'zavod-1/sex-1/liniya-1/stanok-{index}'] + good)
        result = self.call(andon, payload=matrix(rows[0], rows[1:]), limit=2)
        self.assertEqual(1, result['breach_count'])
        self.assertEqual('zavod-1/sex-1/liniya-1/stanok-4',
                         result['breaches'][0]['station'])
        # Every station was compared even though only two are returned.
        self.assertEqual(5, result['evaluated_count'])
        self.assertEqual(2, len(result['stations']))

    def test_the_andon_evaluates_every_station_not_just_the_returned_ones(self):
        rows = [HEADER]
        for index in range(4):
            rows.append([f'zavod-1/sex-1/liniya-1/stanok-{index}', '28800', '10',
                         '26000', '2400', '2304', '0'])
        result = self.call(andon, payload=matrix(rows[0], rows[1:]), limit=1)
        self.assertEqual(4, result['evaluated_count'])
        self.assertEqual(4, result['station_count'])
        self.assertEqual(1, len(result['stations']))

    def test_not_evaluated_names_the_metric_that_could_not_be_compared(self):
        """A bare count is not evidence: "3" does not say which metric is missing.

        The missing metric is named beside the count, exactly as
        ``not_computable`` names the factors it could not build.
        """
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'run_column': 'ish',
                 'produced_column': 'chiqim', 'good_column': 'yaxshi'}
        result = self.configure_andon(
            {'registers': {'line': entry},
             'thresholds': {'oee': {'below': 85}, 'availability': {'below': 95}}},
            key='named')
        self.assertEqual(2, result['not_evaluated'])
        self.assertEqual(['availability', 'oee'], result['not_evaluated_metrics'])

    def test_not_evaluated_names_the_station_the_metric_belongs_to(self):
        entry = {'register': 'plant', 'range': 'oee',
                 'station_column': 'stansiya', 'run_column': 'ish',
                 'produced_column': 'chiqim', 'good_column': 'yaxshi'}
        result = self.configure_andon(
            {'registers': {'line': entry}, 'thresholds': {'oee': {'below': 85}}},
            key='named_station')
        self.assertEqual(
            [{'station': STATION, 'metrics': ['oee']}],
            result['not_evaluated_stations'])

    def test_a_fully_evaluated_andon_names_no_metrics_and_no_stations(self):
        """The lists are evidence of absence, so an empty register empties them."""
        result = self.call(andon, payload=matrix(HEADER, [FULL]))
        self.assertEqual([], result['not_evaluated_metrics'])
        self.assertEqual([], result['not_evaluated_stations'])

    def test_a_complete_andon_declares_itself_complete(self):
        result = self.call(andon, payload=matrix(HEADER, [FULL]))
        self.assertFalse(result['truncated'])
        self.assertTrue(result['complete'])

    # ------------------------------------------------- non-finite is not a number

    def test_a_non_finite_cell_is_unreadable_not_a_huge_number(self):
        """``inf >= 0`` is true, so an infinite run time would accumulate.

        A non-finite value is a real Python float and would otherwise sail
        through as "readable". It must be counted as unreadable and never
        propagated into a factor.
        """
        row = [STATION, '28800', '10', 'inf', '2400', '2304', '0']
        result = self.call(report, payload=matrix(HEADER, [row]))
        station = result['stations'][0]
        self.assertEqual(1, result['unreadable'])
        self.assertIsNone(station['inputs']['run_seconds'])
        self.assertIsNone(station['availability'])
        self.assertIsNone(station['oee'])

    def test_a_nan_cell_is_unreadable_not_a_zero(self):
        row = [STATION, '28800', '10', 'nan', '2400', '2304', '0']
        result = self.call(report, payload=matrix(HEADER, [row]))
        self.assertEqual(1, result['unreadable'])
        self.assertIsNone(result['stations'][0]['inputs']['run_seconds'])
        self.assertIsNone(result['stations'][0]['availability'])

    def test_a_negative_infinity_cell_is_unreadable(self):
        row = [STATION, '28800', '10', '-inf', '2400', '2304', '0']
        result = self.call(report, payload=matrix(HEADER, [row]))
        self.assertEqual(1, result['unreadable'])
        self.assertIsNone(result['stations'][0]['inputs']['run_seconds'])

    # ------------------------------------------------- neither is a judgement

    def test_no_view_returns_a_verdict_word(self):
        """A ratio is a fact about two numbers, not an opinion about a line."""
        banned = ('status', 'good', 'bad', 'warning', 'alert', 'ok', 'fail',
                  'grade', 'level', 'rank', 'score')
        for fn in (report, andon):
            with self.subTest(view=fn.__name__):
                result = self.call(fn, payload=matrix(HEADER, [FULL]))
                names = set(result)
                for record in result['stations']:
                    names |= set(record)
                for breach in result.get('breaches', []):
                    names |= set(breach)
                for needle in banned:
                    if needle == 'good':
                        continue    # ``good`` is a unit count, not a verdict
                    self.assertNotIn(needle, {name.lower() for name in names})

    def test_no_view_evaluates_a_person(self):
        banned = ('operator', 'xodim', 'worker', 'employee', 'person', 'shift',
                  'brigade', 'team', 'user', 'login', 'author')
        for fn in (report, andon):
            with self.subTest(view=fn.__name__):
                result = self.call(fn, payload=matrix(HEADER, [FULL]))
                names = set(result)
                for record in result['stations']:
                    names |= set(record)
                for needle in banned:
                    self.assertNotIn(needle, {name.lower() for name in names})

    def test_no_config_key_could_name_a_person(self):
        declared = set(oee_config(TENANT)['registers']['line'])
        for needle in ('operator_column', 'person_column', 'worker_column',
                       'shift_column', 'team_column', 'brigade_column'):
            self.assertNotIn(needle, declared)

    # ------------------------------------------------------- asset binding

    def test_stations_bind_by_segment_not_by_string_prefix(self):
        rows = [HEADER,
                [STATION, '28800', '10', '26000', '2400', '2304', '0'],
                ['zavod-10/sex-1/liniya-1/stanok-1', '28800', '10', '26000',
                 '2400', '2304', '0']]
        result = self.call(report, payload=matrix(rows[0], rows[1:]))
        paired = {s['station']: s.get('path') for s in result['stations']}
        self.assertEqual('zavod-1/sex-1/liniya-1/stanok-1',
                         paired['zavod-1/sex-1/liniya-1/stanok-1'])
        self.assertEqual('zavod-10/sex-1/liniya-1/stanok-1',
                         paired['zavod-10/sex-1/liniya-1/stanok-1'])

    def test_an_unbound_station_is_reported_without_a_path(self):
        """An empty string must never read as 'this station has no place'."""
        rows = [HEADER, ['not-a-path', '28800', '10', '26000', '2400', '2304', '0']]
        result = self.call(report, payload=matrix(rows[0], rows[1:]))
        station = result['stations'][0]
        self.assertNotIn('path', station)
        self.assertEqual(1, result['unbound'])

    def test_a_read_without_an_asset_hierarchy_is_forbidden(self):
        self.write_config(assets=_MISSING)
        self.rebuild('noassets')
        with self.assertRaises(Forbidden):
            self.call(report, payload=matrix(HEADER, [FULL]))

    # -------------------------------------------------------- unreadability

    def test_an_unreadable_register_is_not_reported_as_a_zero_oee(self):
        """An outage and a stopped line must not look the same."""
        def explode(url, token):
            raise RuntimeError('provider outage')

        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get', side_effect=explode):
                with self.assertRaises(RuntimeError):
                    report(self.engine, TENANT, AGENT, 's1')

    def test_a_register_is_read_once_per_call(self):
        result = self.call(report, payload=matrix(HEADER, [FULL]))
        self.assertGreater(result['station_count'], 0)
        self.assertEqual(1, len(self.transport.calls))

    def test_a_malformed_row_does_not_break_the_read(self):
        rows = [HEADER, FULL, ['', '', '', '', '', '', '']]
        result = self.call(report, payload=matrix(rows[0], rows[1:]))
        self.assertGreater(result['station_count'], 0)
        self.assertEqual(1, result['skipped'])

    def test_no_credential_or_url_reaches_the_output(self):
        result = self.call(report, payload=matrix(HEADER, [FULL]))
        blob = json.dumps(result, ensure_ascii=False)
        for needle in ('token', 'secret', 'http://', 'https://', 'Bearer',
                       SPREADSHEET):
            self.assertNotIn(needle, blob)

    def test_the_output_carries_the_agent_authority(self):
        result = self.call(report, payload=matrix(HEADER, [FULL]))
        self.assertEqual(AGENT, result['authority']['agent'])
        self.assertEqual('human_assisted', result['authority']['ladder'])

    # --------------------------------------------------------------- bound

    def test_the_limit_is_applied_and_reported(self):
        rows = [HEADER]
        for index in range(5):
            rows.append([f'zavod-1/sex-1/liniya-1/stanok-{index}', '28800', '10',
                         '26000', '2400', '2304', '0'])
        result = self.call(report, payload=matrix(rows[0], rows[1:]), limit=2)
        self.assertEqual(2, len(result['stations']))
        self.assertTrue(result['truncated'])

    def test_the_limit_is_bounded(self):
        for bad in (0, -1, MAX_STATIONS + 1, 10_000):
            with self.subTest(limit=bad), self.assertRaises(ValueError):
                self.call(report, payload=matrix(HEADER, [FULL]), limit=bad)

    def test_the_station_argument_selects_one_station(self):
        rows = [HEADER, FULL,
                ['zavod-1/sex-1/liniya-1/stanok-2', '28800', '10', '26000',
                 '2400', '2304', '0']]
        result = self.call(report, payload=matrix(rows[0], rows[1:]),
                           station='zavod-1/sex-1/liniya-1/stanok-2')
        self.assertEqual(['zavod-1/sex-1/liniya-1/stanok-2'],
                         [s['station'] for s in result['stations']])

    # ------------------------------------------------------------ authority

    def test_the_source_tool_must_be_held_by_the_agent(self):
        self.policy = dict(POLICY, tools=['oee.report'])
        with self.assertRaises(Forbidden):
            self.call(report, payload=matrix(HEADER, [FULL]))

    def test_a_connection_the_agent_does_not_hold_is_refused(self):
        self.policy = dict(POLICY, allowed_connections=['other'])
        with self.assertRaises(Forbidden):
            self.call(report, payload=matrix(HEADER, [FULL]))

    def test_an_undeclared_oee_block_is_forbidden(self):
        self.write_config(oee=_MISSING)
        self.rebuild('noblock')
        with self.assertRaises(Forbidden):
            self.call(report, payload=matrix(HEADER, [FULL]))

    def test_two_registers_for_one_read_are_ambiguous_and_refused(self):
        two = json.loads(json.dumps(OEE))
        two['registers']['second'] = dict(two['registers']['line'])
        self.write_config(oee=two)
        self.rebuild('two')
        with self.assertRaises(Forbidden):
            self.call(report, payload=matrix(HEADER, [FULL]))

    def test_an_undeclared_register_is_refused_at_read_time(self):
        broken = json.loads(json.dumps(OEE))
        broken['registers']['line']['register'] = 'phantom'
        self.write_config(oee=broken)
        self.rebuild('phantom')
        with self.assertRaises(Forbidden):
            self.call(report, payload=matrix(HEADER, [FULL]))

    # --------------------------------------------------------------- config

    def test_an_unknown_oee_key_is_refused(self):
        broken = json.loads(json.dumps(OEE))
        broken['andon'] = {}
        self.write_config(oee=broken)
        with self.assertRaises(ValueError):
            oee_config(TENANT)

    def test_an_unknown_column_key_is_refused(self):
        broken = json.loads(json.dumps(OEE))
        broken['registers']['line']['perf_column'] = 'x'
        self.write_config(oee=broken)
        with self.assertRaises(ValueError):
            oee_config(TENANT)

    def test_a_register_without_a_station_column_is_refused(self):
        broken = json.loads(json.dumps(OEE))
        del broken['registers']['line']['station_column']
        self.write_config(oee=broken)
        with self.assertRaises(ValueError):
            oee_config(TENANT)

    def test_an_unknown_threshold_key_is_refused(self):
        broken = json.loads(json.dumps(OEE))
        broken['thresholds']['oee_percent'] = {'below': 85}
        self.write_config(oee=broken)
        with self.assertRaises(ValueError):
            oee_config(TENANT)

    def test_a_threshold_outside_percent_bounds_is_refused(self):
        for bad in (-1, 101, 1000):
            broken = json.loads(json.dumps(OEE))
            broken['thresholds']['oee'] = {'below': bad}
            self.write_config(oee=broken)
            with self.subTest(below=bad), self.assertRaises(ValueError):
                oee_config(TENANT)

    def test_an_unsatisfiable_threshold_pair_is_refused(self):
        """below >= above can never fire, so it is a configuration error."""
        broken = json.loads(json.dumps(OEE))
        broken['thresholds']['oee'] = {'below': 90, 'above': 50}
        self.write_config(oee=broken)
        with self.assertRaises(ValueError):
            oee_config(TENANT)

    def test_a_threshold_with_no_bound_is_refused(self):
        broken = json.loads(json.dumps(OEE))
        broken['thresholds']['oee'] = {}
        self.write_config(oee=broken)
        with self.assertRaises(ValueError):
            oee_config(TENANT)

    def test_a_negative_planned_constant_is_refused(self):
        broken = json.loads(json.dumps(OEE))
        broken['registers']['line']['planned_run_seconds'] = -1
        self.write_config(oee=broken)
        with self.assertRaises(ValueError):
            oee_config(TENANT)

    def test_an_empty_oee_block_is_two_empty_maps(self):
        self.write_config(oee={})
        self.assertEqual({'registers': {}, 'thresholds': {}}, oee_config(TENANT))

    def test_a_missing_oee_block_is_two_empty_maps(self):
        self.write_config(oee=_MISSING)
        self.assertEqual({'registers': {}, 'thresholds': {}}, oee_config(TENANT))

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
