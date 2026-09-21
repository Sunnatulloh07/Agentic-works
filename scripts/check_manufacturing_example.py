"""Validate the shipped manufacturing example against the module's own validator.

Run from ``api-python``. This is the check that keeps the example honest: an
example whose shape the validator would reject teaches operators a configuration
that fails at runtime, which is the documentation drift this repository treats as
a defect.

The example is JSON (it mirrors ``vision.example.json`` and ``assets.example.json``),
so the check parses it directly and then runs the **module's own** config
validator over the block. That is the point: the example is not checked against a
copy of the rules, it is checked against the rules.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'api-python'))

import platform_runtime.manufacturing as manufacturing  # noqa: E402
from platform_runtime import tools  # noqa: E402

PATH = os.path.join(HERE, '..', 'config', 'manufacturing.example.json')

with open(PATH, encoding='utf-8') as handle:
    example = json.load(handle)

# Comment keys are documentation, not configuration.
blocks = {tenant: value for tenant, value in example.items()
          if not tenant.startswith('_')}
assert blocks, 'the example declares no tenant'

# The validator reads the tenant config through ``tools.config``, which reads the
# environment-declared integrations file. Rather than write a file, the module's
# own accessor is pointed at the example block for the duration of the check.
original = tools.config


def fake_config(tenant):
    return blocks.get(tenant, {})


tools.config = fake_config
manufacturing.config = fake_config
try:
    for tenant, payload in sorted(blocks.items()):
        assert 'manufacturing' in payload, f'{tenant} has no manufacturing block'
        declared = manufacturing.manufacturing_config(tenant)
        assert set(declared) == {'bom', 'cycle', 'yield'}, 'three views expected'

        for view, entries in sorted(declared.items()):
            for name, entry in sorted(entries.items()):
                keys = set(entry)
                assert entry['register'], f'{tenant}.{view}.{name} has no register'
                assert entry['range'], f'{tenant}.{view}.{name} has no range'
                # The refusal has to be structural, so the check proves no person
                # column could be declared even by a future edit to the example.
                for banned in ('operator_column', 'person_column', 'worker_column',
                               'shift_column', 'team_column', 'brigade_column'):
                    assert banned not in keys, \
                        f'{tenant}.{view}.{name} declares a person column {banned!r}'
                for banned in ('oee', 'availability_column', 'performance_column'):
                    assert banned not in keys, \
                        f'{tenant}.{view}.{name} declares an OEE key {banned!r}'
                print(f'{tenant}.{view}.{name}: register={entry["register"]!r} '
                      f'range={entry["range"]!r} keys={sorted(keys)}')

    # A knob the validator must refuse, to prove the validator is running here and
    # the example is not merely passing by not being checked.
    broken = json.loads(json.dumps(blocks))
    tenant = sorted(broken)[0]
    broken[tenant]['manufacturing']['yield']['shift']['operator_column'] = 'x'
    tools.config = lambda t: broken.get(t, {})
    refused = False
    try:
        manufacturing.manufacturing_config(tenant)
    except ValueError:
        refused = True
    assert refused, 'an operator column must be refused by the validator'

    # And an OEE sub-block, which the module does not accept at all.
    broken = json.loads(json.dumps(blocks))
    broken[tenant]['manufacturing']['oee'] = {}
    tools.config = lambda t: broken.get(t, {})
    refused = False
    try:
        manufacturing.manufacturing_config(tenant)
    except ValueError:
        refused = True
    assert refused, 'an oee block must be refused by the validator'
finally:
    tools.config = original
    manufacturing.config = original

print('out-of-shape blocks -> refused')
print('OK: example passes the module validator')
