"""Validate the shipped document-intake example against the module's validator.

Run from anywhere; paths are resolved relative to this file. An example the
validator rejects would teach operators a shape that fails at runtime, which this
repository treats as a defect rather than as documentation drift.

It also measures the claims the block is built on:

* the example declares no key that could pay, and an unknown key is refused;
* a malformed threshold is refused rather than silently replaced by a default;
* money is integer minor units, and a float is refused;
* a document that disagrees with itself is refused.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import documents
from platform_runtime import tools as tools_module

PATH = os.path.join(ROOT, 'config', 'documents.example.json')

with io.open(PATH, encoding='utf-8') as handle:
    raw = json.load(handle)

tenant = 'demo-ap'
orig = tools_module.config
tools_module.config = lambda name: {tenant: raw.get(tenant, {})}.get(name, {})

try:
    settings = documents.documents_config(tenant)
finally:
    tools_module.config = orig

print('settings resolved:')
for key in sorted(settings):
    print(f'  {key}: {settings[key]!r}')

# The block names no key that could pay.
for banned in ('pay', 'payment', 'transfer', 'settle', 'iban', 'beneficiary'):
    offenders = [k for k in raw[tenant]['documents'] if banned in k.lower()]
    assert not offenders, offenders
print('no payment-shaped key in the example')

# An unknown key is refused.
broken = json.loads(json.dumps(raw))
broken[tenant]['documents']['auto_pay'] = True
tools_module.config = lambda name: {tenant: broken.get(tenant, {})}.get(name, {})
try:
    try:
        documents.documents_config(tenant)
        raise AssertionError('an unknown documents key must be refused')
    except ValueError:
        pass
finally:
    tools_module.config = orig
print('unknown key -> refused')

# A malformed threshold is refused, not defaulted.
broken = json.loads(json.dumps(raw))
broken[tenant]['documents']['tolerance_minor'] = -5
tools_module.config = lambda name: {tenant: broken.get(tenant, {})}.get(name, {})
try:
    try:
        documents.documents_config(tenant)
        raise AssertionError('a negative tolerance must be refused')
    except ValueError:
        pass
finally:
    tools_module.config = orig
print('negative tolerance -> refused')

# Money arithmetic.
assert documents.normalize({'kind': 'invoice', 'supplier': 'acme', 'number': 'X-1',
                            'currency': 'USD', 'total': '1234.56'})['total_minor'] == 123456
try:
    documents.normalize({'kind': 'invoice', 'supplier': 'acme', 'number': 'X-1',
                         'currency': 'USD', 'total': 1234.56})
    raise AssertionError('a float amount must be refused')
except ValueError:
    pass
print('decimal string -> 123456 minor; float -> refused')

# A document that disagrees with itself is refused.
try:
    documents.normalize({'kind': 'invoice', 'supplier': 'acme', 'number': 'X-1',
                         'currency': 'UZS', 'total': 1000,
                         'line_items': [{'description': 'a', 'amount': 400},
                                        {'description': 'b', 'amount': 400}]})
    raise AssertionError('line items that do not sum to the total must be refused')
except ValueError:
    pass
print('line items != stated total -> refused')

# The tool surface cannot pay.
names = ' '.join(tools_module.build_registry().items)
for banned in ('pay', 'transfer', 'settle', 'remit', 'refund'):
    assert banned not in names, banned
print('no registered tool can pay')

print('OK: example passes the module validator')
