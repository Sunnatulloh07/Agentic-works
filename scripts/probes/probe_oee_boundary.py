"""Adversarial probe: measure the OEE boundary, do not describe it.

A docstring is not evidence. P12 refused OEE on the grounds that availability and
performance need declared inputs a register cannot supply, and handed the figure
here. This probe measures whether that handover was honest, in six sections:

1. **A factor is never defaulted.** For each of the three factors the probe
   removes exactly the input that factor needs and measures whether the factor
   becomes ``None`` and is *named* in ``not_computable``. A computed factor with
   its input missing would mean the module invented a denominator.
2. **A nonsense ratio cannot escape.** The probe feeds good-above-produced,
   produced-zero and run-above-plan registers and measures whether any factor or
   the product leaves the honest range. A quality factor above 1.0 is the failure
   mode that would produce an OEE of 347%.
3. **A partial OEE is never a number.** The probe removes inputs one at a time and
   measures how many reported an ``oee`` while a factor was uncomputable. It must
   be zero, always.
4. **An uncomputable station is not an alarm.** The probe configures a threshold
   with a register whose OEE cannot be computed and measures whether a breach was
   raised. A missing column name must not page a manager about a failing line.
   It then measures the reverse in isolation — one declared metric crossing once
   fires exactly one breach, and two declared metrics crossing fire exactly two —
   so the guard is not blanket silence and one metric cannot inflate another.
   Finally it measures the stated plan-precedence rule (column wins, constant is
   the blank-cell fallback, an unreadable cell is neither), because a documented
   rule that the code does not implement is the defect class this repository
   treats as a bug and does not accept on a docstring's word.
5. **The platform does not decide what a bad OEE is.** With thresholds removed the
   probe measures whether ``andon`` still produced a comparison. It must refuse.
6. **No person is evaluated.** The probe plants ``operator`` and ``brigada``
   columns in the register and measures whether any person key or value appears.

Exit code is non-zero when any measured property is violated, so this can run in
CI beside the suite.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'api-python'))

from platform_runtime.engine import Engine, Forbidden  # noqa: E402
from platform_runtime.oee import andon, oee_config, report  # noqa: E402
from platform_runtime.tools import build_registry  # noqa: E402

TENANT = 't_probe'
AGENT = 'ops.oee'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

POLICY = {'tools': ['sheets.rows', 'oee.report', 'oee.andon'],
          'allowed_connections': ['plant'], 'ladder': 'human_assisted'}
ASSETS = {'entity': 'asset', 'levels': ['zavod', 'sex', 'liniya', 'stanok']}

HEADER = ['stansiya', 'reja', 'ideal', 'ish', 'chiqim', 'yaxshi', 'rejasiz',
          'operator', 'brigada']
STATION = 'zavod-1/sex-1/liniya-1/stanok-1'

# planned 28800, run 26000, ideal 10, produced 2400, good 2304 -> OEE exactly 0.8
WORKED = [STATION, '28800', '10', '26000', '2400', '2304', '1800',
          'Ali Valiyev', 'A']

FULL = {'register': 'plant', 'range': 'oee', 'station_column': 'stansiya',
        'planned_run_column': 'reja', 'ideal_cycle_column': 'ideal',
        'run_column': 'ish', 'produced_column': 'chiqim',
        'good_column': 'yaxshi', 'unplanned_column': 'rejasiz'}

PERSON_KEYS = ('operator', 'xodim', 'worker', 'employee', 'person', 'shift',
               'brigade', 'team', 'user', 'login', 'author', 'name')
VERDICT_KEYS = ('status', 'bad', 'warning', 'alert', 'grade', 'rank', 'score')

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
        self.rows = [HEADER, WORKED]
        self.calls = []
        self.counter = 0
        self.env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(self.cfg)})
        self.env.start()

    def build(self, registers, thresholds=None):
        self.counter += 1
        oee = {'registers': registers}
        if thresholds is not None:
            oee['thresholds'] = thresholds
        payload = {'connections': {'plant': {}}, 'assets': ASSETS,
                   'sheets_registers': {'plant': {
                       'connection': 'plant', 'spreadsheet_id': SPREADSHEET,
                       'ranges': {'oee': 'OEE!A1:J'}, 'max_rows': 200}},
                   'oee': oee}
        self.cfg.write_text(json.dumps({TENANT: payload}, ensure_ascii=False),
                            encoding='utf-8')
        self.calls = []
        return Engine(self.root / f'probe{self.counter}.db', build_registry(),
                      lambda t, a: POLICY, clock=lambda: 1_770_000_000.0)

    def read(self, engine, fn, rows=None, limit=None):
        rows = self.rows if rows is None else rows
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: self._fetch(url, token, rows)):
                if limit is None:
                    return fn(engine, TENANT, AGENT, 's1')
                return fn(engine, TENANT, AGENT, 's1', limit=limit)

    def _fetch(self, url, token, rows):
        self.calls.append(url)
        return {'values': rows}

    def one(self, registers, thresholds=None, rows=None):
        return self.read(self.build(registers, thresholds), report, rows)

    def close(self):
        self.env.stop()
        self.tmp.cleanup()


def station_of(result, index=0):
    return result['stations'][index]


def section_one(h):
    print('\n[1] A factor is never defaulted; its missing input is named')
    # Each factor with exactly its own input removed.
    cases = {
        'availability': ('planned_run_column', 'planned_run_seconds'),
        'performance': ('ideal_cycle_column', 'ideal_cycle_seconds'),
    }
    for factor, (column, input_name) in cases.items():
        entry = dict(FULL)
        entry.pop(column)
        result = h.one({'line': entry})
        station = station_of(result)
        check('1', station[factor] is None,
              f'{factor}: refused when {column} is not declared ({station[factor]})')
        check('1', input_name in station['not_computable']['absent'],
              f'{factor}: names {input_name} as absent')
        check('1', station['oee'] is None,
              f'{factor}: the product is refused too, not partially reported')

    # Quality with its inputs removed (no produced column at all).
    entry = dict(FULL)
    entry.pop('produced_column')
    entry.pop('good_column')
    station = station_of(h.one({'line': entry}))
    check('1', station['quality'] is None, 'quality: refused with no produced column')
    check('1', 'produced' in station['not_computable']['absent'],
          'quality: names produced as absent')

    # The reverse direction: with every input present, nothing is refused.
    station = station_of(h.one({'line': dict(FULL)}))
    check('1', station['oee_computable'] and station['oee'] == 0.8,
          'with every input declared the worked example yields exactly 0.8')
    check('1', station['not_computable'] == {'absent': [], 'unusable': []},
          'nothing is reported missing when nothing is')


def section_two(h):
    print('\n[2] A nonsense ratio cannot escape')
    # good > produced
    rows = [HEADER, [STATION, '28800', '10', '26000', '2400', '9999', '0', 'A', 'A']]
    result = h.one({'line': dict(FULL)}, rows=rows)
    station = station_of(result)
    check('2', station['quality'] is None,
          f"good above produced gives no quality factor ({station['quality']})")
    check('2', station['oee'] is None,
          'good above produced gives no OEE (347% is not a number to report)')
    check('2', result['good_exceeds_produced'] == 1,
          'good above produced is counted as a data error')

    # produced == 0
    rows = [HEADER, [STATION, '28800', '10', '26000', '0', '0', '0', 'A', 'A']]
    station = station_of(h.one({'line': dict(FULL)}, rows=rows))
    check('2', station['quality'] is None,
          f'produced zero gives no quality factor ({station["quality"]})')
    check('2', station['not_computable']['unusable'] == ['produced'],
          'produced zero is reported unusable, not absent')

    # run > plan is not clamped to a perfect 1.0
    rows = [HEADER, [STATION, '1000', '10', '5000', '100', '100', '0', 'A', 'A']]
    station = station_of(h.one({'line': dict(FULL)}, rows=rows))
    check('2', station['availability'] is not None and station['availability'] > 1.0,
          f"run above plan is not clamped to 100% ({station['availability']})")

    # Quality is a share, so it can never leave 0..1, for any produced/good pairing.
    # The bound is stated in the *reported* precision (the module rounds to four
    # places), and read back against the register's own declared run time so the
    # check cannot drift if the fixture row is edited.
    run = 26000.0
    ideal = 10.0
    for produced, good in ((100, 100), (100, 99), (100, 1), (100, 0)):
        rows = [HEADER, [STATION, '28800', '10', str(int(run)), str(produced),
                         str(good), '0', 'A', 'A']]
        station = station_of(h.one({'line': dict(FULL)}, rows=rows))
        quality = station['quality']
        if quality is not None:
            check('2', quality <= 1.0,
                  f'quality stays <= 1.0 for produced={produced} good={good} ({quality})')
        # performance is a *rate*, not a share: producing 100 units in 26000s
        # against a 10s ideal cycle is 0.04 of the rate the standard implies.
        # It legitimately sits below 1.0, and it is reported as such rather than
        # compared against a bound it was never asked to satisfy.
        performance = station['performance']
        expected = round((ideal * produced) / run, 4)
        if performance is not None:
            check('2', performance == expected,
                  f'performance is exactly ideal*produced/run for {produced} '
                  f'({performance} vs {expected})')
            check('2', performance <= 1.0,
                  f'and that low rate is reported below 1.0, not floored to it '
                  f'({performance})')


def section_three(h):
    print('\n[3] A partial OEE is never reported as a number')
    optional = ('planned_run_column', 'ideal_cycle_column', 'run_column',
                'produced_column', 'good_column')
    partials = 0
    for column in optional:
        entry = dict(FULL)
        entry.pop(column)
        station = station_of(h.one({'line': entry}))
        if not all(station[f] is not None for f in
                   ('availability', 'performance', 'quality')):
            partials += 1
            check('3', station['oee'] is None,
                  f'without {column}: oee is None while a factor is uncomputable')
            check('3', station['oee_computable'] is False,
                  f'without {column}: oee_computable is false')
    check('3', partials >= 4,
          f'at least four removals produced a partial OEE to test ({partials})')

    # The product must equal its own reported factors, or it is not reproducible.
    station = station_of(h.one({'line': dict(FULL)}))
    product = 1.0
    for factor in ('availability', 'performance', 'quality'):
        product *= station[factor]
    check('3', abs(station['oee'] - round(product, 4)) < 1e-4,
          f'the OEE equals the product of its reported factors ({station["oee"]})')

    # Every reported figure must ship its inputs.
    check('3', set(station['inputs']) == {'planned_run_seconds', 'run_seconds',
                                          'ideal_cycle_seconds', 'produced',
                                          'good', 'unplanned_seconds'},
          'every figure ships all six inputs that produced it')


def section_four(h):
    print('\n[4] An uncomputable station is not an alarm')
    entry = dict(FULL)
    entry.pop('ideal_cycle_column')       # performance refuses -> OEE refuses
    entry.pop('planned_run_column')       # availability refuses too
    thresholds = {'oee': {'below': 85}, 'availability': {'below': 95}}
    result = h.one({'line': entry}, thresholds=thresholds)
    andon_result = h.read(h.build({'line': entry}, thresholds), andon)
    check('4', andon_result['breach_count'] == 0,
          f"an uncomputable OEE raises no breach ({andon_result['breach_count']})")
    check('4', andon_result['not_evaluated'] > 0,
          'the uncomputable metric is counted as not evaluated')
    check('4', result['computable_count'] == 0,
          'the report agrees that nothing was computable')

    # And a genuine breach still fires, so the guard is not blanket silence.
    # Only the OEE threshold is declared here. The worked example is availability
    # 0.9028, so declaring availability below 95 as well would fire a second
    # breach from the same fixture and the two properties would be entangled --
    # the probe would be measuring the fixture, not the thresholding.
    only_oee = {'oee': {'below': 85}}
    good = h.read(h.build({'line': dict(FULL)}, only_oee), andon)
    check('4', good['breach_count'] == 1,
          f'a real breach (OEE 80 < 85) still fires ({good["breach_count"]})')
    breach = good['breaches'][0]
    check('4', breach['value'] == 80.0 and breach['threshold'] == 85.0,
          'the breach ships its measured value and the threshold it crossed')
    check('4', breach['metric'] == 'oee',
          f"the breach names the metric it crossed ({breach['metric']})")

    # One declaration, one breach: adding a second metric declaration is what
    # produces a second breach, and nothing else does.
    both = h.read(h.build({'line': dict(FULL)}, thresholds), andon)
    check('4', both['breach_count'] == 2,
          f'each declared metric that is crossed fires once, not more '
          f'({both["breach_count"]})')
    metrics = sorted(b['metric'] for b in both['breaches'])
    check('4', metrics == ['availability', 'oee'],
          f'and the two breaches are the two crossed metrics ({metrics})')

    # ---------------------------------------------------- plan precedence
    # The header states a precedence rule for two declared plan sources: the
    # column wins, the constant is the fallback for a blank cell, and a non-blank
    # unreadable cell uses neither. A documented rule that is wrong is the exact
    # defect class this repository treats as a bug, so it is measured here rather
    # than trusted. Note the column fixture is deliberately a *column* register,
    # so these cases do not collide with the constant-only path.
    const_and_col = dict(FULL)
    const_and_col['planned_run_seconds'] = 28800

    row = [STATION, '10000', '10', '26000', '2400', '2304', '0', 'A', 'A']
    station = station_of(h.one({'line': const_and_col}, rows=[HEADER, row]))
    check('4', station['inputs']['planned_run_seconds'] == 10000.0,
          f"the column wins when it holds a value "
          f"({station['inputs']['planned_run_seconds']})")
    check('4', station['availability'] == 2.6,
          f'and the OEE is computed against the column, not the constant '
          f'({station["availability"]})')

    row = [STATION, '', '10', '26000', '2400', '2304', '0', 'A', 'A']
    station = station_of(h.one({'line': const_and_col}, rows=[HEADER, row]))
    check('4', station['inputs']['planned_run_seconds'] == 28800.0,
          f"the constant fills in for a blank cell "
          f"({station['inputs']['planned_run_seconds']})")
    check('4', station['availability'] == 0.9028,
          f'and a blank cell is not a refusal when a default exists '
          f'({station["availability"]})')

    row = [STATION, 'yoq', '10', '26000', '2400', '2304', '0', 'A', 'A']
    report_result = h.one({'line': const_and_col}, rows=[HEADER, row])
    station = station_of(report_result)
    check('4', station['inputs']['planned_run_seconds'] is None,
          'a non-blank unreadable cell is neither the row value nor the default '
          f"({station['inputs']['planned_run_seconds']})")
    check('4', station['availability'] is None,
          'so availability is refused rather than filled with the constant')
    check('4', report_result['unreadable'] > 0,
          'and the unreadable cell is counted, not silently dropped')

    # Both declared must be *accepted* configuration, not a refusal: the header
    # reserves the refusal for two registers, and asserting a refusal here would
    # itself be documentation drift.
    h.build({'line': const_and_col}, None)
    entry = oee_config(TENANT)['registers']['line']
    check('4', entry.get('planned_run_column') and entry.get('planned_run_seconds'),
          'both plan sources survive validation together')


def section_five(h):
    print('\n[5] The platform does not decide what a bad OEE is')
    engine = h.build({'line': dict(FULL)}, None)
    try:
        h.read(engine, andon)
        check('5', False, 'andon compared against a threshold nobody declared')
    except Forbidden:
        check('5', True, 'with no threshold declared, andon refuses by name')

    # A threshold that can never fire is refused at configure time, not silently.
    engine = h.build({'line': dict(FULL)}, {'oee': {'below': 90, 'above': 50}})
    try:
        h.read(engine, report)
        check('5', False, 'an unsatisfiable threshold pair was accepted')
    except ValueError:
        check('5', True, 'an unsatisfiable below >= above pair is refused')


def section_six(h):
    print('\n[6] No person is evaluated, even when the register names one')
    result = h.one({'line': dict(FULL)})
    names = set(result)
    for record in result['stations']:
        names |= set(record)
    hits = sorted(k for k in PERSON_KEYS if k in {n.lower() for n in names})
    check('6', not hits, f'no person key surfaced from a register holding one (found {hits})')
    text = json.dumps(result, ensure_ascii=False).lower()
    leaks = [n for n in ('Ali Valiyev', 'brigada') if n.lower() in text]
    check('6', not leaks, f'no operator value leaked into the output (found {leaks})')
    # And no verdict vocabulary either.
    verdicts = sorted(k for k in VERDICT_KEYS if k in {n.lower() for n in names})
    check('6', not verdicts, f'no verdict key is present (found {verdicts})')

    andon_result = h.read(h.build({'line': dict(FULL)}, {'oee': {'below': 85}}), andon)
    names = set(andon_result)
    for record in andon_result['stations']:
        names |= set(record)
    for breach in andon_result['breaches']:
        names |= set(breach)
    verdicts = sorted(k for k in VERDICT_KEYS if k in {n.lower() for n in names})
    check('6', not verdicts, f'andon carries no verdict key either (found {verdicts})')


def section_seven(h):
    """The properties the post-implementation audit found and fixed.

    Each was a real defect, so each is measured here rather than trusted to the
    docstring: a non-finite cell read as a number, an alarm blind past its own
    display window, an opaque count standing in for a name, and a ``complete``
    flag asserted rather than derived.
    """
    print('\n[7] The audit fixes hold under measurement')

    # A non-finite cell must be unreadable, not a number. inf >= 0 is true, so
    # before the fix it accumulated into the run bucket as an infinite duration.
    rows = [HEADER, [STATION, '28800', '10', 'inf', '2400', '2304', '0']]
    result = h.one({'line': dict(FULL)}, None, rows=rows)
    station = station_of(result)
    check('7', result['unreadable'] == 1,
          'a non-finite run cell is counted as unreadable')
    check('7', station['inputs']['run_seconds'] is None,
          'a non-finite run cell never becomes a duration')
    check('7', station['availability'] is None,
          'an infinite run cannot produce an availability factor')
    check('7', station['oee'] is None,
          'an unreadable input cannot produce an OEE')

    rows = [HEADER, [STATION, '28800', '10', 'nan', '2400', '2304', '0']]
    result = h.one({'line': dict(FULL)}, None, rows=rows)
    check('7', station_of(result)['inputs']['run_seconds'] is None,
          'a nan run cell is unreadable too, not a zero')

    # The alarm must see every station the read saw. Build five stations where
    # only the last breaches, and ask for a display window of two: the breach is
    # behind the window and must still be found.
    healthy = ['28800', '10', '28800', '2880', '2880', '0']
    breach = ['28800', '10', '26000', '2400', '1200', '0']
    rows = [HEADER] + [
        [f'{STATION[:-1]}{n}'] + (healthy if n < 4 else breach) for n in range(5)]
    engine = h.build({'line': dict(FULL)}, {'oee': {'below': 85}})
    result = h.read(engine, andon, rows)
    check('7', result['breach_count'] == 1,
          'a breach is found among five stations')
    check('7', result['evaluated_count'] == 5,
          'every station is evaluated')
    check('7', len(result['stations']) == 5,
          'the default andon window is every station (no silent truncation)')

    # Now the same register with a narrowed display window: the breach sits behind
    # it and must still be found, because the alarm never inherits a display limit.
    result = h.read(engine, andon, rows, limit=2)
    check('7', result['breach_count'] == 1,
          'a breach behind the caller limit is still counted')
    check('7', result['evaluated_count'] == 5,
          'every station is evaluated even when the window is two')
    check('7', len(result['stations']) == 2,
          'the caller limit bounds only what is returned')

    # The named form of not_evaluated: a bare count would not say which metric.
    entry = {'register': 'plant', 'range': 'oee', 'station_column': 'stansiya',
             'run_column': 'ish', 'produced_column': 'chiqim',
             'good_column': 'yaxshi'}
    engine = h.build({'line': entry},
                     {'oee': {'below': 85}, 'availability': {'below': 95}})
    result = h.read(engine, andon)
    check('7', result['not_evaluated_metrics'] == ['availability', 'oee'],
          'the metrics that could not be compared are named, not counted only')
    check('7', result['not_evaluated_stations'] == [
        {'station': STATION, 'metrics': ['availability', 'oee']}],
        'the station each uncompared metric belongs to is named')

    # complete is derived from the read, not asserted.
    result = h.read(h.build({'line': dict(FULL)}, {'oee': {'below': 85}}), andon)
    check('7', result['complete'] is True and result['truncated'] is False,
          'a fully evaluated andon declares itself complete')
    note = result['note']
    check('7', 'TO‘LIQ EMAS' in note,
          'the note says a truncated list is not complete')


def main():
    print('OEE boundary probe — measuring, not asserting')
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
