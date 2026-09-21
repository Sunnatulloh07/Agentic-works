"""Validate the shipped vision example against the module's own validator.

Run from anywhere; paths are resolved relative to this file. This is the check
that keeps the example honest: an example the validator rejects would teach
operators a shape that fails at runtime, which this repository treats as a defect
rather than as documentation drift.

It also measures the two claims the example is built on: that a person-identifying
register must name its classes, and that the tenant-wide vocabulary is applied
across registers.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import tools as tools_module
from platform_runtime import vision

PATH = os.path.join(ROOT, 'config', 'vision.example.json')

with io.open(PATH, encoding='utf-8') as handle:
    raw = json.load(handle)

tenant = 'demo-plant'
block = raw[tenant]['vision']

# The module reads config through tools.config(tenant); point it at the example
# for the whole run, since every call below goes through it.
orig = tools_module.config
tools_module.config = lambda name: {tenant: raw.get(tenant, {})}.get(name, {})

cleaned = vision.vision_config(tenant)
print('registers declared:', sorted(cleaned['registers']))
for name, entry in sorted(cleaned['registers'].items()):
    print(f"  {name}: register={entry['register']!r} range={entry['range']!r} "
          f"sensitivity={entry['sensitivity']!r} "
          f"person_classes={entry['person_classes']} "
          f"biometric_ack={entry['biometric_ack']} "
          f"confidence_column={entry['confidence_column']!r}")

# The tenant-wide vocabulary: both registers contribute to one set.
identifying = vision.person_classes(tenant)
print('tenant-wide identifying classes:', sorted(identifying))
assert identifying == frozenset({'yuz'}), identifying

# A person register with no classes must be refused: otherwise every row would be
# read on the impersonal path while the register claims to be sensitive.
broken = {tenant: {'vision': {'registers': {'access': dict(
    cleaned['registers']['access'], person_classes=[])}}}}
tools_module.config = lambda name: broken.get(name, {})
try:
    try:
        vision.vision_config(tenant)
        raise AssertionError('a person register with no person_classes must be refused')
    except ValueError:
        pass
finally:
    tools_module.config = lambda name: {tenant: raw.get(tenant, {})}.get(name, {})

print('person register without classes -> refused')

# An unknown key must be refused rather than ignored, so an rtsp URL typo cannot
# look like a working camera configuration.
broken = {tenant: {'vision': {'registers': {'shopfloor': dict(
    cleaned['registers']['shopfloor'], camera_url='rtsp://x')}}}}
tools_module.config = lambda name: broken.get(name, {})
try:
    try:
        vision.vision_config(tenant)
        raise AssertionError('an unsupported register key must be refused')
    except ValueError:
        pass
finally:
    tools_module.config = lambda name: {tenant: raw.get(tenant, {})}.get(name, {})

print('unsupported register key -> refused')

# A tenant with no vision block must report an empty mapping, not raise.
tools_module.config = lambda name: {}
try:
    absent = vision.vision_config('nobody')
finally:
    tools_module.config = orig
assert absent == {'registers': {}}, absent
print('absent block ->', absent)

print('OK: example passes the module validator')
