"""Validate the shipped asset-model example against the module's own validator.

Run from anywhere; paths are resolved relative to this file. This is the check
that keeps the example honest: an example the validator rejects would teach
operators a shape that fails at runtime, which this repository treats as a
defect rather than as documentation drift.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import assets
from platform_runtime import tools as tools_module

PATH = os.path.join(ROOT, 'config', 'assets.example.json')

with io.open(PATH, encoding='utf-8') as handle:
    raw = json.load(handle)

tenant = 'demo-plant'
block = raw[tenant]['assets']

# The module reads config through tools.config(tenant); point it at the example
# for the whole run, since every call below goes through it.
orig = tools_module.config
tools_module.config = lambda name: {tenant: raw.get(tenant, {})}.get(name, {})

declared = assets._declared_levels(tenant)
entity = assets.asset_entity(tenant)
measures = assets.measurements(tenant)
view = assets.levels(tenant)

print('levels :', view['levels'])
print('depth  :', view['depth'])
print('entity :', entity)
print('measure:', measures)

# Every path the example implies must round-trip through the identity rule.
sample = '/'.join('%s-%d' % (name, n + 1) for n, name in enumerate(declared))
parsed = assets.parse_path(tenant, sample)
assert parsed == sample.split('/'), parsed
print('sample :', sample, '-> ok')

# An intern prefix must be navigable but must not pass as an identity.
prefix = declared[0] + '-1'
assert assets.prefix_path(tenant, prefix) == [prefix]
try:
    assets.parse_path(tenant, prefix)
    raise AssertionError('a short path must be refused by the identity rule')
except ValueError:
    pass
print('prefix : intern node navigable, short path refused as identity')

# The prefix rule itself: zavod-1 must not be an ancestor of zavod-10.
assert assets.is_descendant(['zavod-1'],
                            ['zavod-10', 'sex-1', 'liniya-1', 'stanok-1']) is False
assert assets.is_descendant(['zavod-1'],
                            ['zavod-1', 'sex-1', 'liniya-1', 'stanok-1']) is True
print('prefix : zavod-1 is not an ancestor of zavod-10')

# A tenant with no assets block must report an empty hierarchy, not raise.
tools_module.config = lambda name: {}
try:
    absent = assets.levels('nobody')
finally:
    tools_module.config = orig
assert absent == {'entity': '', 'levels': [], 'depth': 0, 'declared': False}, absent
print('absent :', absent)

print('OK: example passes the module validator')
