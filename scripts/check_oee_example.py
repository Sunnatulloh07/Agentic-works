"""Validate the shipped OEE example against the module's own validator.

Run from ``api-python``. This is the check that keeps the example honest: an
example whose shape the validator would reject teaches operators a configuration
that fails at runtime, which is the documentation drift this repository treats as
a defect.

The example is JSON (it mirrors ``manufacturing.example.json`` and
``assets.example.json``), so the check parses it directly and then runs the
**module's own** config validator over the block. That is the point: the example
is not checked against a copy of the rules, it is checked against the rules.

Two properties are asserted here that the P12 checker could not assert, because
OEE is the first block that *does* compute an index:

* the constant-plan path (``planned_run_seconds``) is reachable, so an operator
  who has no per-row shift-plan column is not locked out;
* the andon thresholds parse to bounded percentages, and a nonsense pair is
  refused at configure time rather than silently never firing.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'api-python'))

import platform_runtime.oee as oee  # noqa: E402
from platform_runtime import tools  # noqa: E402

PATH = os.path.join(HERE, '..', 'config', 'oee.example.json')

with open(PATH, encoding='utf-8') as handle:
    example = json.load(handle)

# Comment keys are documentation, not configuration.
blocks = {tenant: value for tenant, value in example.items()
          if not tenant.startswith('_')}
assert blocks, 'the example declares no tenant'

# The validator reads the tenant config through ``tools.config``, which reads the
# environment-declared integrations file. Rather than write a file, the module's
# own accessor is pointed at the example block for the duration of the check.
# ``oee.oee_config`` imports ``config`` lazily from ``tools`` at call time, so
# patching ``tools.config`` alone is what the module actually consults.
original = tools.config


def use(payload):
    tools.config = lambda t: payload.get(t, {})


use(blocks)
try:
    for tenant, payload in sorted(blocks.items()):
        assert 'oee' in payload, f'{tenant} has no oee block'
        declared = oee.oee_config(tenant)
        assert set(declared) == {'registers', 'thresholds'}, \
            'registers and thresholds expected'
        assert declared['registers'], f'{tenant} declares no register'

        constants = 0
        for name, entry in sorted(declared['registers'].items()):
            keys = set(entry)
            assert entry['register'], f'{tenant}.registers.{name} has no register'
            assert entry['range'], f'{tenant}.registers.{name} has no range'
            assert entry['station_column'], \
                f'{tenant}.registers.{name} has no station_column'

            # Two sources for one denominator is an ambiguity, not a precedence
            # rule. The validator refuses it; the check proves the refusal works
            # so a future edit to the example cannot smuggle both in.
            if entry.get('planned_run_column') and entry.get('planned_run_seconds'):
                raise AssertionError(
                    f'{tenant}.registers.{name} declares two plan sources')
            if entry.get('planned_run_seconds'):
                constants += 1
                assert float(entry['planned_run_seconds']) > 0, \
                    f'{tenant}.registers.{name} plan constant is not positive'

            # The refusal has to be structural, so the check proves no person
            # column could be declared even by a future edit to the example.
            for banned in ('operator_column', 'person_column', 'worker_column',
                           'shift_column', 'team_column', 'brigade_column'):
                assert banned not in keys, \
                    f'{tenant}.registers.{name} declares a person column {banned!r}'
            print(f'{tenant}.registers.{name}: register={entry["register"]!r} '
                  f'range={entry["range"]!r} keys={sorted(keys)}')

        assert constants >= 1, \
            'the example must exercise the constant-plan path, or operators ' \
            'without a shift-plan column cannot use it'

        assert declared['thresholds'], f'{tenant} declares no threshold'
        for metric, bound in sorted(declared['thresholds'].items()):
            assert metric in oee.THRESHOLD_KEYS, f'unknown metric {metric!r}'
            assert set(bound) <= {'below', 'above'}, f'{metric} has a stray key'
            for side, value in sorted(bound.items()):
                assert 0.0 <= float(value) <= 100.0, \
                    f'{metric}.{side} is outside 0..100 ({value})'
            low, high = bound.get('below'), bound.get('above')
            if low is not None and high is not None:
                assert float(low) < float(high), \
                    f'{metric} declares an unsatisfiable below >= above pair'
            print(f'{tenant}.thresholds.{metric}: {bound}')

    # A knob the validator must refuse, to prove the validator is running here and
    # the example is not merely passing by not being checked.
    tenant = sorted(blocks)[0]
    first = sorted(blocks[tenant]['oee']['registers'])[0]

    broken = json.loads(json.dumps(blocks))
    broken[tenant]['oee']['registers'][first]['operator_column'] = 'x'
    use(broken)
    refused = False
    try:
        oee.oee_config(tenant)
    except ValueError:
        refused = True
    assert refused, 'an operator column must be refused by the validator'

    # Declaring both a plan column and a plan constant is deliberately ACCEPTED.
    # The header states the precedence rule (column wins, constant is the blank-cell
    # fallback), so the check asserts acceptance here -- asserting a refusal would
    # be the documentation drift this repository treats as a defect, in reverse.
    broken = json.loads(json.dumps(blocks))
    entry = broken[tenant]['oee']['registers'][first]
    entry['planned_run_column'] = 'reja'
    entry['planned_run_seconds'] = 28800
    use(broken)
    accepted = None
    try:
        accepted = oee.oee_config(tenant)['registers'][first]
    except ValueError as exc:
        raise AssertionError(f'both plan sources must be accepted, not refused: {exc}')
    assert accepted['planned_run_column'] == 'reja' and \
        accepted['planned_run_seconds'] == 28800.0, \
        'both plan sources must survive validation, with the column winning at read time'

    # A threshold pair that can never fire.
    broken = json.loads(json.dumps(blocks))
    broken[tenant]['oee']['thresholds'] = {'oee': {'below': 90, 'above': 50}}
    use(broken)
    refused = False
    try:
        oee.oee_config(tenant)
    except ValueError:
        refused = True
    assert refused, 'a below >= above pair must be refused by the validator'

    # A threshold pair that can never fire.
    broken = json.loads(json.dumps(blocks))
    broken[tenant]['oee']['thresholds'] = {'oee': {'below': 90, 'above': 50}}
    use(broken)
    refused = False
    try:
        oee.oee_config(tenant)
    except ValueError:
        refused = True
    assert refused, 'a below >= above pair must be refused by the validator'

    # A threshold above 100 percent, which no OEE ratio could ever cross.
    broken = json.loads(json.dumps(blocks))
    broken[tenant]['oee']['thresholds'] = {'oee': {'below': 150}}
    use(broken)
    refused = False
    try:
        oee.oee_config(tenant)
    except ValueError:
        refused = True
    assert refused, 'a threshold above 100 must be refused by the validator'
finally:
    tools.config = original

print('out-of-shape blocks -> refused')
print('OK: example passes the module validator')
