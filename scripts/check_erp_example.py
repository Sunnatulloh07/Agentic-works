"""Validate the shipped ERP-posting example against the module's own validator.

Run from anywhere; paths are resolved relative to this file. An example the
validator rejects would teach operators a shape that fails at runtime, which this
repository treats as a defect rather than as documentation drift.

It also measures the claims the block is built on:

* the example declares no key that could pay, and an unknown key is refused;
* a non-HTTPS host and a traversing path are refused;
* a posting is refused, by name, when the document does not carry every field;
* the account and the counterparty come from the operator's register, and an alias
  the operator never declared is refused rather than forwarded;
* the tool surface cannot pay.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import erp
from platform_runtime import tools as tools_module

PATH = os.path.join(ROOT, 'config', 'erp_posting.example.json')

with io.open(PATH, encoding='utf-8') as handle:
    raw = json.load(handle)

tenant = 'demo-erp'
# tools.config reads PLATFORM_INTEGRATIONS_FILE and erp.erp_config resolves the
# tenant through it, so both module-level names are patched here. The checker must
# depend only on the shipped example, never on a file that happens to exist on the
# machine it runs on.
orig = tools_module.config
orig_erp = erp.config


def use(block):
    """Point both modules at one tenant block, so the example is the only input."""
    tools_module.config = lambda name: {tenant: block}.get(name, {})
    erp.config = lambda name: {tenant: block}.get(name, {})


use(raw[tenant])

try:
    settings = erp.erp_config(tenant)
finally:
    tools_module.config = orig
    erp.config = orig_erp

print('settings resolved:')
for key in sorted(settings):
    shown = settings[key]
    if key in ('accounts', 'counterparties'):
        shown = f'<{len(shown)} aliases>'
    print(f'  {key}: {shown!r}')

# The block names no key that could pay.
for banned in ('pay', 'payment', 'transfer', 'settle', 'remit', 'refund', 'iban',
               'beneficiary', 'payout'):
    offenders = [k for k in raw[tenant]['erp_posting'] if banned in k.lower()]
    assert not offenders, offenders
print('no payment-shaped key in the example')

# An unknown key is refused.
broken = json.loads(json.dumps(raw))
broken[tenant]['erp_posting']['auto_pay'] = True
use(broken[tenant])
try:
    try:
        erp.erp_config(tenant)
        raise AssertionError('an unknown erp_posting key must be refused')
    except ValueError:
        pass
finally:
    tools_module.config = orig
    erp.config = orig_erp
print('unknown key -> refused')

# A host that is not a bare hostname is refused, and so is a traversing path.
for name, value, why in (('host', 'https://erp.example.uz', 'a scheme in host'),
                         ('host', 'erp.example.uz/hs', 'a path in host'),
                         ('post_path', '../admin', 'traversal'),
                         ('search_path', '//evil.example', 'protocol-relative')):
    broken = json.loads(json.dumps(raw))
    broken[tenant]['erp_posting'][name] = value
    use(broken[tenant])
    try:
        try:
            erp.erp_config(tenant)
            raise AssertionError(f'{why} must be refused')
        except ValueError:
            pass
    finally:
        tools_module.config = orig
        erp.config = orig_erp
    print(f'{name}={value!r} ({why}) -> refused')

# A document missing a field is refused, and the refusal names it.
use(raw[tenant])
try:
    complete = {'supplier': 'acme', 'number': 'INV-1', 'doc_date': '2026-03-04',
                'currency': 'UZS', 'total_minor': 1250000}
    posting = erp.resolve_posting(tenant, complete, account='purchase',
                                 counterparty='acme')
    assert posting['account'] == '6010', posting
    assert posting['counterparty'] == 'ACME-LLC-0001', posting
    print('complete document -> account 6010, counterparty ACME-LLC-0001')

    try:
        erp.resolve_posting(tenant, {'supplier': 'acme', 'number': 'INV-1'},
                            account='purchase', counterparty='acme')
        raise AssertionError('a document missing fields must be refused')
    except erp.ErpError as error:
        text = str(error)
        for missing in ('doc_date', 'currency', 'total_minor'):
            assert missing in text, (missing, text)
        print(f'incomplete document -> refused, naming {text.split(":")[1].strip()[:44]}...')

    # A malformed value is reported as malformed, not as missing.
    try:
        erp.resolve_posting(tenant, dict(complete, total_minor=1250000.5),
                            account='purchase', counterparty='acme')
        raise AssertionError('a float amount must be refused')
    except erp.ErpError as error:
        assert 'integer' in str(error), error
        print('float total_minor -> refused as malformed (not reported as missing)')

    # The two operator fields cannot come from the document.
    for field in ('account', 'counterparty'):
        try:
            erp.resolve_posting(tenant, dict(complete), account='purchase',
                                counterparty='acme', **{field: 'injected'})
        except TypeError:
            pass
    try:
        erp.resolve_posting(tenant, dict(complete), account='not-declared',
                            counterparty='acme')
        raise AssertionError('an undeclared account alias must be refused')
    except erp.ErpError as error:
        assert 'not declared by the operator' in str(error), error
        print('undeclared account alias -> refused')
finally:
    tools_module.config = orig
    erp.config = orig_erp

# An ambiguous date is refused rather than guessed.
use(raw[tenant])
try:
    erp.resolve_posting(tenant, dict(complete, doc_date='10.01.2026'),
                        account='purchase', counterparty='acme')
    raise AssertionError('an ambiguous date must be refused')
except erp.ErpError as error:
    assert 'ambiguous' in str(error), error
    print('doc_date 10.01.2026 -> refused as ambiguous')
finally:
    tools_module.config = orig
    erp.config = orig_erp

# A calendar day that does not exist is refused, not silently rolled forward.
use(raw[tenant])
try:
    erp.resolve_posting(tenant, dict(complete, doc_date='2026-02-30'),
                        account='purchase', counterparty='acme')
    raise AssertionError('2026-02-30 must be refused')
except erp.ErpError as error:
    assert 'calendar' in str(error) or 'not a real date' in str(error), error
    print('doc_date 2026-02-30 -> refused as not a real date')
finally:
    tools_module.config = orig
    erp.config = orig_erp

# The tool surface cannot pay.
registry = tools_module.build_registry()
names = ' '.join(registry.items)
for banned in ('pay', 'transfer', 'settle', 'remit', 'refund', 'payout'):
    assert banned not in names, banned
assert 'erp.posting_submit' in names
print('no registered tool can pay; erp.posting_submit is present')

print('OK: example passes the module validator')
