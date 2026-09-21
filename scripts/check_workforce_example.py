"""Validate the shipped workforce example against the module's own validator.

Run from ``api-python``. This is the check that keeps the example honest: an
example that the validator rejects would teach operators a shape that fails at
runtime, which is exactly the kind of documentation drift this repository has
been treating as a defect.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'api-python'))

from platform_runtime import tools as tools_module
from platform_runtime import workforce

PATH = os.path.join(HERE, '..', 'config', 'workforce.example.json')

with io.open(PATH, encoding='utf-8') as handle:
    raw = json.load(handle)

tenant = 'demo-retail'
block = raw[tenant]['workforce']

# The module reads config through tools.config(tenant); point it at the example.
orig = tools_module.config
tools_module.config = lambda name: {tenant: raw.get(tenant, {})}.get(name, {})
try:
    cleaned = workforce.workforce_config(tenant)
finally:
    tools_module.config = orig

print('registers declared:', sorted(cleaned['registers']))
for name, entry in sorted(cleaned['registers'].items()):
    print(f"  {name}: register={entry['register']!r} range={entry['range']!r} "
          f"status_column={entry['status_column']!r} "
          f"absent={entry['absent_statuses']} done={entry['done_statuses']} "
          f"open={entry['open_statuses']}")

# A register with no workforce block at all must be an empty config, not an error.
tools_module.config = lambda name: {}
try:
    empty = workforce.workforce_config('nobody')
finally:
    tools_module.config = orig
assert empty == {'registers': {}}, empty
print('absent block ->', empty)

# Every view the example declares must resolve without raising.
for view, entry in sorted(cleaned['registers'].items()):
    assert entry['register'] and entry['range'], view
print('OK: example passes the module validator')
