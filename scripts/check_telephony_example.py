"""Validate the shipped telephony example against the module's own validator.

Run from ``api-python``. This is the check that keeps the example honest: an
example whose shape the validator would reject teaches operators a configuration
that fails at runtime, which is the documentation drift this repository treats as
a defect.

The example is JSON, so the check parses it directly and then runs the
**module's own** config validator over the block. The example is not checked
against a copy of the rules, it is checked against the rules.

Three properties are asserted here that the earlier checkers could not, because
telephony is the first block whose central question is a **legal** one:

* the consent vocabulary is closed (a purpose outside the declared set is
  refused);
* the number normalisation the module uses is the one the example's
  ``consented_numbers`` is compared with, so an example written in spaced form
  still matches a register cell written in digits;
* a locator-shaped outcome cell is withheld rather than republished, so the
  example cannot smuggle a media path through an ``outcome_column``.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'api-python'))

import platform_runtime.telephony as telephony  # noqa: E402
from platform_runtime import tools  # noqa: E402

PATH = os.path.join(HERE, '..', 'config', 'telephony.example.json')

with open(PATH, encoding='utf-8') as handle:
    example = json.load(handle)

# Comment keys are documentation, not configuration.
blocks = {tenant: value for tenant, value in example.items()
          if not tenant.startswith('_')}
assert blocks, 'the example declares no tenant'

original = tools.config

failures = []


def use(payload):
    tools.config = lambda t: payload.get(t, {})


use(blocks)


def check(condition, message):
    if condition:
        print(f'  PASS  {message}')
    else:
        print(f'  FAIL  {message}')
        failures.append(message)


print('Telephony example check')
print('=' * 62)

for tenant, payload in sorted(blocks.items()):
    assert 'telephony' in payload, f'{tenant} has no telephony block'
    use(blocks)
    try:
        declared = telephony.telephony_config(tenant)

        # The declared purposes are all inside the closed vocabulary.
        purposes = telephony.purposes(tenant)
        check(purposes <= set(telephony.PURPOSES),
              f'{tenant}: every declared purpose is in the closed set '
              f'(found {sorted(purposes)})')
        check(len(declared['registers']) == 2,
              f'{tenant}: both registers parse (the example exercises more than '
              f'one purpose)')
        check(len(declared['consent_registers']) == 1,
              f'{tenant}: the consent register parses')

        # The consent register's columns are the ones the module reads.
        consent_register = next(iter(declared['consent_registers'].values()))
        check(consent_register['status_column'] == 'holat',
              f'{tenant}: the consent status column parses')

        # The allowlist normalises to digits, so a spaced example matches a
        # register cell written in digit form.
        allowlist = declared['consented_numbers']
        check(allowlist == frozenset({'998901234567', '998911111111'}),
              f'{tenant}: the allowlist normalises to digits (found {sorted(allowlist)})')
        check(telephony.normalise('+998 90 123 45 67') in allowlist,
              f'{tenant}: a spaced number matches the digit form in the allowlist')

        # A locator-shaped cell is withheld, not republished.
        check(telephony._outcome('https://media.example/rec-1.wav') == '',
              f'{tenant}: a scheme-prefixed URL is withheld as an outcome')
        check(telephony._outcome('rec-1.mp3') == '',
              f'{tenant}: a bare media filename is withheld as an outcome')
        check(telephony._outcome('answered') == 'answered',
              f'{tenant}: an ordinary outcome word survives the filter')

        # Stage B: the pace and the retention window are declared and parse.
        check(declared['throughput'] == {'per_window': 20, 'window_seconds': 3600},
              f'{tenant}: the declared pace parses (found {declared["throughput"]})')
        check(declared['retention_days'] == 90,
              f'{tenant}: the retention window parses (found {declared["retention_days"]})')
        check(telephony._retention(90) is not None,
              f'{tenant}: the retention validator accepts the example value')
    finally:
        tools.config = original

    # A mutation the validator must refuse: an unknown purpose.
    mutated = json.loads(json.dumps(payload))
    mutated['telephony']['registers']['service_line']['purpose'] = 'survey'
    use({tenant: mutated})
    try:
        telephony.telephony_config(tenant)
        check(False, f'{tenant}: an unknown purpose is refused at configure time')
    except ValueError:
        check(True, f'{tenant}: an unknown purpose is refused at configure time')
    finally:
        tools.config = original

    # A mutation the validator must refuse: an unusable allowlist number.
    mutated = json.loads(json.dumps(payload))
    mutated['telephony']['consented_numbers'] = ['Ali Valiyev']
    use({tenant: mutated})
    try:
        telephony.telephony_config(tenant)
        check(False, f'{tenant}: an unusable allowlist number is refused at '
                     f'configure time')
    except ValueError:
        check(True, f'{tenant}: an unusable allowlist number is refused at '
                    f'configure time')
    finally:
        tools.config = original

    # A mutation the validator must refuse: a person column on a register.
    mutated = json.loads(json.dumps(payload))
    mutated['telephony']['registers']['service_line']['agent_column'] = 'xodim'
    use({tenant: mutated})
    try:
        telephony.telephony_config(tenant)
        check(False, f'{tenant}: an unknown person-shaped key is refused')
    except ValueError:
        check(True, f'{tenant}: an unknown person-shaped key is refused')
    finally:
        tools.config = original

    # A mutation the validator must refuse: a consent register with no retention
    # window. VO-07 makes retention an operator obligation, so omitting it is not
    # a default, it is an incomplete declaration.
    mutated = json.loads(json.dumps(payload))
    mutated['telephony'].pop('retention_days', None)
    use({tenant: mutated})
    try:
        telephony.telephony_config(tenant)
        check(False, f'{tenant}: a consent register with no retention window is refused')
    except ValueError:
        check(True, f'{tenant}: a consent register with no retention window is refused')
    finally:
        tools.config = original

    # A mutation the validator must refuse: a retention window that is not finite.
    for bad, label in ((0, 'a zero-day'),
                       (9999, 'an unbounded'),
                       ('forever', 'a non-numeric')):
        mutated = json.loads(json.dumps(payload))
        mutated['telephony']['retention_days'] = bad
        use({tenant: mutated})
        try:
            telephony.telephony_config(tenant)
            check(False, f'{tenant}: {label} retention window is refused')
        except ValueError:
            check(True, f'{tenant}: {label} retention window is refused')
        finally:
            tools.config = original

    # A mutation the validator must refuse: a pace that no human could supervise.
    for bad, label in (({'per_window': 0, 'window_seconds': 3600},
                        'a zero-per-window pace'),
                       ({'per_window': 20, 'window_seconds': 1},
                        'a sub-minute window'),
                       ({'per_window': 20, 'window_seconds': 3600, 'burst': 5},
                        'an unknown pace key')):
        mutated = json.loads(json.dumps(payload))
        mutated['telephony']['throughput'] = bad
        use({tenant: mutated})
        try:
            telephony.telephony_config(tenant)
            check(False, f'{tenant}: {label} is refused')
        except ValueError:
            check(True, f'{tenant}: {label} is refused')
        finally:
            tools.config = original

print('=' * 62)
if failures:
    print(f'CHECK FAILURES: {len(failures)}')
    for failure in failures:
        print(f'  - {failure}')
    sys.exit(1)
print('The example is valid against the module\'s own rules.')
