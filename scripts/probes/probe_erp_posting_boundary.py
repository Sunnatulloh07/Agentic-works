"""Prove the ERP posting boundary: exact or refuse, post once, move no money.

``erp.py`` is the first code in this repository that writes to a financial system,
so the claims it makes are not documentation — they are the reason it is allowed to
exist. Three of them, and each is measured against the real module rather than
restated from its docstrings.

**Claim 1 -- the platform does not move money.** Checked structurally and
behaviourally: no public callable carries a money-moving verb, no request body this
module can construct contains a payment instruction, and a successful posting
reports ``moves_money: False``. Note the claim is *narrower* than the document
block's: a posting DOES create an accounting document. The line is that recording a
payable and paying it are different acts, and only the first is here.

**Claim 2 -- an incomplete document is refused, never completed.** Every required
field is removed in turn and the refusal is shown to name it. Then the sharper half:
an empty document must not produce a *default*, and the two fields that do not live
in the document at all (the ledger account and the counterparty) must come from the
operator's own register, so a supplier invoice cannot choose where its own debt
lands.

**Claim 3 -- a document is never posted twice.** Measured from three directions,
because the defence has three parts and testing one would leave the other two
unproven: the ERP is asked first, the local ledger blocks before any I/O, and the
UNIQUE claim index catches what a race slips past both. The index case is
reproduced by bypassing the ledger read deliberately, which is the only way to show
that the index -- and not the lookup -- is what makes it a guarantee.

A fourth property is measured because it is easy to get wrong and expensive to get
wrong: **a failed attempt stays retryable**. A transport blip that permanently
blocked an invoice would force an operator to edit the database, so the failed row
must be recorded without claiming the document's identity.

Run from anywhere. No network, no live credential, no ERP.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import erp
from platform_runtime.engine import Conflict, Engine, Forbidden
from platform_runtime.tools import build_registry

TENANT = 't_probe_erp'
AGENT = 'finance.ap'
POLICY = {'tools': list(erp.ERP_TOOLS), 'allowed_connections': [],
          'ladder': 'human_assisted', 'approver_role': 'owner'}

CONFIG = {
    'driver': 'onec_http', 'enabled': True, 'host': 'erp.example.uz',
    'base_path': '/hs/agent', 'timeout_seconds': 15, 'auth': 'bearer',
    'token_env': 'ERP_PROBE_TOKEN', 'post_path': '/documents',
    'search_path': '/documents/search',
    'response_map': {'created_id': 'result.Ref_Key', 'existing_id': 'result.Ref_Key',
                     'items': 'result.items'},
    'accounts': {'purchase': '60.01'},
    'counterparties': {'acme': 'ACME-LLC-0001'},
}

DOCUMENT = {'kind': 'invoice', 'supplier': 'acme', 'number': 'INV-1042',
            'doc_date': '2026-09-12', 'currency': 'UZS', 'total_minor': 1_250_000}

fails = []


def check(label, condition, detail=''):
    mark = 'ok  ' if condition else 'FAIL'
    print(f'  [{mark}] {label}' + (f' -- {detail}' if detail else ''))
    if not condition:
        fails.append(label)


class Clock:
    def __init__(self, start=1_789_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class Script:
    def __init__(self, responses=None):
        self.responses = list(responses or [{}])
        self.calls = []

    def __call__(self, url, body=None, headers=None, method='GET', timeout=15):
        self.calls.append({'url': url, 'body': body, 'method': method,
                           'headers': dict(headers or {})})
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    @property
    def posts(self):
        return [c for c in self.calls if c['method'] == 'POST']


class Harness:
    def __init__(self, erp_config=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'integrations.json'
        self.path.write_text(
            json.dumps({TENANT: {'erp_posting': CONFIG if erp_config is None
                                 else erp_config}}), encoding='utf-8')
        self.env = mock.patch.dict(os.environ, {
            'PLATFORM_INTEGRATIONS_FILE': str(self.path),
            'ERP_PROBE_TOKEN': 'secret-token'})
        self.env.start()
        self.engine = Engine(self.root / 'erp.db', build_registry(),
                             lambda t, a: POLICY, clock=Clock())

    def close(self):
        self.env.stop()
        self.tmp.cleanup()

    def resolve(self, document=None, **extra):
        extra.setdefault('account', 'purchase')
        extra.setdefault('counterparty', 'acme')
        return erp.resolve_posting(TENANT, document or dict(DOCUMENT), **extra)


print('1. The platform does not move money')
harness = Harness()
try:
    vocabulary = ('pay', 'payment', 'transfer', 'settle', 'disburse', 'remit',
                  'payout', 'refund', 'charge')
    suspects = [n for n in dir(erp)
                if not n.startswith('_') and callable(getattr(erp, n))
                and any(t in n.lower() for t in vocabulary)]
    check('no public callable carries a money-moving verb', suspects == [],
          f'found {suspects}' if suspects else '')

    posting = harness.resolve()
    body = erp.posting_body(erp.erp_config(TENANT), posting, note='ok')
    check('a posting body names an invoice, not a payment',
          body['document_type'] == 'invoice', f"document_type={body['document_type']!r}")
    for term in ('pay', 'payment', 'transfer', 'iban', 'bic', 'swift'):
        check(f'  the body carries no {term!r}', term not in body)

    transport = Script([{'result': {}}, {'result': {'Ref_Key': 'DOC-77'}}])
    result = erp.submit(harness.engine, TENANT, AGENT, posting, transport=transport)
    check('a successful posting reports that it moved no money',
          result['moves_money'] is False and result['posted'] is True)
    sent = json.dumps(transport.posts[0]['body'])
    check('and nothing sent contained a payment instruction',
          not any(t in sent for t in ('payment', 'transfer', 'iban')), sent[:80])
finally:
    harness.close()

print()
print('2. An incomplete document is refused, never completed')
harness = Harness()
try:
    for missing in erp.REQUIRED_FIELDS:
        document = dict(DOCUMENT)
        extra = {'account': 'purchase', 'counterparty': 'acme'}
        if missing in ('account', 'counterparty'):
            extra.pop(missing)
        else:
            document.pop(missing, None)
        try:
            erp.resolve_posting(TENANT, document, **extra)
            ok = False
            detail = 'accepted'
        except erp.ErpError as error:
            ok = missing in str(error)
            detail = str(error)[:70]
        check(f'missing {missing!r} is refused by name', ok, detail)

    try:
        erp.resolve_posting(TENANT, {})
        check('an empty document produces a refusal, never a default', False)
    except erp.ErpError as error:
        named = all(name in str(error) for name in
                    ('supplier', 'number', 'doc_date', 'currency', 'total_minor',
                     'account', 'counterparty'))
        check('an empty document names every field it lacks', named)

    try:
        harness.resolve(account='60.01')
        check('a raw account code is refused in favour of the operator alias', False)
    except erp.ErpError as error:
        check('a raw account code is refused in favour of the operator alias',
              'not declared' in str(error))

    for bad in ('10.01.2026', '2026-13-01', 'next tuesday'):
        try:
            harness.resolve({**DOCUMENT, 'doc_date': bad})
            check(f'the ambiguous date {bad!r} is refused, not guessed', False)
        except erp.ErpError:
            check(f'the ambiguous date {bad!r} is refused, not guessed', True)

    posting = harness.resolve({**DOCUMENT, 'doc_date': '13.01.2026'})
    check("an unambiguous date ('13.01.2026') is accepted", posting['doc_date'] == '2026-01-13',
          posting['doc_date'])

    empty = Harness(erp_config=None)
    try:
        empty.path.write_text(json.dumps({TENANT: {}}), encoding='utf-8')
        try:
            erp.resolve_posting(TENANT, dict(DOCUMENT), account='purchase',
                                counterparty='acme')
            check('an undeclared tenant cannot post at all', False)
        except Forbidden:
            check('an undeclared tenant cannot post at all', True)
    finally:
        empty.close()
finally:
    harness.close()

print()
print('3. A document is never posted twice')
harness = Harness()
try:
    posting = harness.resolve()

    # (a) the ERP is asked before anything is sent
    first = Script([{'result': {}}, {'result': {'Ref_Key': 'DOC-1'}}])
    erp.submit(harness.engine, TENANT, AGENT, posting, transport=first)
    check('the ERP is searched before the document is sent',
          [c['method'] for c in first.calls] == ['GET', 'POST'])

    # (b) an ERP that already holds it is not sent to
    harness2 = Harness()
    try:
        second = Script([{'result': {'Ref_Key': 'EXISTING-9'}}])
        result = erp.submit(harness2.engine, TENANT, AGENT,
                            harness2.resolve(), transport=second)
        check('a document the ERP already holds is not posted',
              result['posted'] is False and result['duplicate'] is True)
        check('  and no POST was issued at all', second.posts == [])
        check('  and the refusal is recorded for the operator',
              erp.ledger(harness2.engine, TENANT)[0]['status'] == 'skipped_existing')
    finally:
        harness2.close()

    # (c) the local ledger blocks before any I/O
    third = Script([{'result': {}}, {'result': {'Ref_Key': 'DOC-2'}}])
    try:
        erp.submit(harness.engine, TENANT, AGENT, posting, transport=third)
        check('a second posting is refused by the local ledger', False)
    except Conflict:
        check('a second posting is refused by the local ledger', True)
    check('  and nothing was sent on the second attempt', third.calls == [])

    # (d) the UNIQUE index catches what the lookup cannot, reproduced by bypassing it
    try:
        erp._record(harness.engine, TENANT, posting, '', {}, 'posted', 'DOC-1b', {}, {})
        check('the UNIQUE claim index is the guarantee, not the lookup', False)
    except Conflict as error:
        check('the UNIQUE claim index is the guarantee, not the lookup',
              'already been posted' in str(error))

    # (e) the amount is excluded from the identity, so a re-issued total cannot slip past
    altered = erp.resolve_posting(TENANT, {**DOCUMENT, 'total_minor': 9_999_999},
                                  account='purchase', counterparty='acme')
    check('a different amount does not escape the duplicate key',
          erp.identity(posting) == erp.identity(altered))
finally:
    harness.close()

print()
print('4. A failed attempt stays retryable')
harness = Harness()
try:
    def flaky(url, body=None, headers=None, method='GET', timeout=15):
        if method == 'GET':
            return {'result': {}}
        raise TimeoutError('connection timed out')

    posting = harness.resolve()
    try:
        erp.submit(harness.engine, TENANT, AGENT, posting, transport=flaky)
    except Exception:
        pass
    rows = erp.ledger(harness.engine, TENANT)
    check('the failed attempt is recorded', len(rows) == 1 and rows[0]['status'] == 'failed',
          rows[0]['status'] if rows else 'nothing recorded')
    check('and it does not claim the document identity',
          erp.posted(harness.engine, TENANT, posting) is None)

    good = Script([{'result': {}}, {'result': {'Ref_Key': 'DOC-9'}}])
    result = erp.submit(harness.engine, TENANT, AGENT, posting, transport=good)
    check('so the same document can still be posted afterwards',
          result['posted'] is True, f"external_id={result['external_id']}")

    # An unconfirmed posting is reported as unconfirmed, never as settled.
    harness3 = Harness()
    try:
        bare = Script([{'result': {}}, {'result': {}}])
        try:
            erp.submit(harness3.engine, TENANT, AGENT, harness3.resolve(),
                       transport=bare)
            check('a 2xx without an identifier is not reported as settled', False)
        except Conflict as error:
            check('a 2xx without an identifier is not reported as settled',
                  'reconcile' in str(error))
        third_rows = erp.ledger(harness3.engine, TENANT)
        check('  and it is recorded as unconfirmed',
              third_rows[0]['status'] == 'unconfirmed')
    finally:
        harness3.close()
finally:
    harness.close()

print()
print('5. The endpoint surface is narrow')
harness = Harness()
try:
    for bad, why in (('http://erp.example.uz/x', 'plain HTTP'),
                     ('https://user:pw@erp.example.uz/x', 'an embedded credential')):
        from platform_runtime.erp import default_erp_transport
        try:
            default_erp_transport(bad)
            check(f'the transport refuses {why}', False)
        except ValueError:
            check(f'the transport refuses {why}', True)

    for bad in ('../admin', '/a/../b', '//evil.example', 'documents'):
        try:
            erp._clean_path(bad, 'path')
            check(f'the config refuses the path {bad!r}', False)
        except ValueError:
            check(f'the config refuses the path {bad!r}', True)

    transport = Script([{'result': {}}, {'result': {'Ref_Key': 'DOC-1'}}])
    erp.submit(harness.engine, TENANT, AGENT, harness.resolve(), transport=transport)
    serialized = json.dumps(erp.ledger(harness.engine, TENANT))
    check('the ledger never stores the credential',
          'secret-token' not in serialized and 'Authorization' not in serialized)
    check('the request carried the credential in a header, not the URL',
          'secret-token' not in transport.calls[0]['url']
          and transport.calls[0]['headers'].get('Authorization') == 'Bearer secret-token')
finally:
    harness.close()

print()
if fails:
    print(f'NOT PROVEN: {len(fails)} assertion(s) failed')
    for item in fails:
        print(f'  - {item}')
    sys.exit(1)
print('PROVEN: a posting is refused unless the document carries every field it needs,')
print('        and the two fields it cannot carry -- the ledger account and the')
print('        counterparty -- come from the operator\'s own register, so a supplier')
print('        invoice cannot choose where its own debt lands. It is posted at most')
print('        once, enforced three ways (the ERP search, the local ledger before any')
print('        I/O, and a UNIQUE index that survives a race). A failed attempt is')
print('        recorded but left retryable, and an unanswered 2xx is unconfirmed')
print('        rather than settled. Money is never moved: a posting records a payable,')
print('        and paying it stays a human act in the ERP the customer already trusts.')
