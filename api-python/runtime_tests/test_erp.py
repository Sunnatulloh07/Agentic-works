"""ERP posting contract tests. Real Engine, real SQLite, scripted transport.

The properties that matter here are all **negative**, because this is the first
write in the repository that leaves the platform for a financial system:

    the platform posts a document; it does not move money.
    an incomplete document is refused; it is never completed by a guess.
    a document is never posted twice.

Each is asserted against the module's real surface rather than promised in prose.
The third is measured from two directions, because it is defended twice: the ERP is
asked whether it already holds the document, and the platform's own ledger collides
on a UNIQUE index. Either alone has a hole -- the ledger cannot see a posting made
elsewhere, and the ERP search cannot be atomic with the POST -- so both are tested,
including the case where one is unavailable.

The transport is scripted, so no test opens a socket. A test that needed a live ERP
would be a test that never runs, and an untested financial write is the one kind of
code that must not be shipped on the strength of review alone.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from platform_runtime.engine import Conflict, Engine, Forbidden
from platform_runtime.erp import (
    DRIVERS,
    ERP_TOOLS,
    ErpError,
    MAX_AMOUNT_MINOR,
    MAX_NOTE_CHARS,
    MAX_RESPONSE_BYTES,
    POSTING_TOOLS,
    REQUIRED_FIELDS,
    _dig,
    default_erp_transport,
    erp_config,
    find_posted,
    identity,
    ledger,
    posted,
    posting_body,
    register_erp_tools,
    resolve_posting,
    submit,
)
from platform_runtime.tools import build_registry

TENANT = 't_erp'
AGENT = 'finance.ap'

POLICY = {'tools': list(ERP_TOOLS), 'allowed_connections': [],
          'ladder': 'human_assisted', 'approver_role': 'owner'}

CONFIG = {
    'driver': 'onec_http',
    'enabled': True,
    'host': 'erp.example.uz',
    'base_path': '/hs/agent',
    'timeout_seconds': 15,
    'auth': 'bearer',
    'token_env': 'ERP_PROBE_TOKEN',
    'post_path': '/documents',
    'search_path': '/documents/search',
    'response_map': {'created_id': 'result.Ref_Key', 'existing_id': 'result.Ref_Key',
                     'items': 'result.items'},
    'accounts': {'purchase': '60.01'},
    'counterparties': {'acme': 'ACME-LLC-0001'},
}

DOCUMENT = {'kind': 'invoice', 'supplier': 'acme', 'number': 'INV-1042',
            'doc_date': '2026-09-12', 'currency': 'UZS', 'total_minor': 1_250_000}

# The module's own source, so a ceiling can be pinned to the literal it is
# documented as and not merely to whatever symbol the test happens to import.
# Without this, moving `MAX_RESPONSE_BYTES` from 80_000 to 80_001 moves the test
# with it and the suite stays green -- the blindness fazza 24 recorded.
SOURCE = (Path(__file__).resolve().parents[1] / 'platform_runtime'
          / 'erp.py').read_text(encoding='utf-8')


class Clock:
    def __init__(self, start=1_789_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class ScriptTransport:
    """A scripted ERP. Records every request; returns queued responses in order.

    ``post_response`` answers *every* POST with the same document, which is what a
    test wants when it is exercising the duplicate guard rather than the response
    reader: the queue can then describe only the searches, and a test does not have
    to know how many POSTs a scenario happens to issue.
    """

    def __init__(self, responses=None, post_response=None):
        self.responses = list(responses or [{}])
        self.post_response = post_response
        self.calls = []

    def __call__(self, url, body=None, headers=None, method='GET', timeout=15):
        self.calls.append({'url': url, 'body': body, 'headers': dict(headers or {}),
                           'method': method})
        if method == 'POST' and self.post_response is not None:
            return self.post_response
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    @property
    def posts(self):
        return [call for call in self.calls if call['method'] == 'POST']


class ErpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.policy = dict(POLICY)
        self.env = mock.patch.dict(os.environ, {'ERP_PROBE_TOKEN': 'secret-token'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.write_config()
        self.engine = Engine(self.root / 'erp.db', build_registry(),
                             lambda t, a: self.policy, clock=Clock())

    def write_config(self, erp=None, tenant=TENANT):
        payload = {'erp_posting': CONFIG if erp is None else erp}
        path = self.root / 'integrations.json'
        path.write_text(json.dumps({tenant: payload}, ensure_ascii=False),
                        encoding='utf-8')
        self.env_patch = mock.patch.dict(
            os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(path)})
        self.env_patch.start()

    def tearDown(self):
        patch = getattr(self, 'env_patch', None)
        if patch is not None:
            patch.stop()

    def resolve(self, document=None, **extra):
        """Resolve with the operator aliases defaulted to the declared ones."""
        extra.setdefault('account', 'purchase')
        extra.setdefault('counterparty', 'acme')
        return resolve_posting(TENANT, document or dict(DOCUMENT), **extra)

    def call(self, name, args, transport=None):
        transport = transport or ScriptTransport([{}])
        with mock.patch('platform_runtime.erp.default_erp_transport',
                        side_effect=transport):
            return build_registry().get(name).handler(
                self.engine, TENANT, AGENT, args, 's1')

    # ------------------------------------------------------ money boundary

    def test_the_module_exposes_no_payment_callable(self):
        """Structural: no public callable with a money-moving name."""
        import platform_runtime.erp as module
        vocabulary = ('pay', 'payment', 'transfer', 'settle', 'disburse', 'remit',
                      'payout', 'refund', 'charge')
        suspects = []
        for name in dir(module):
            if name.startswith('_'):
                continue
            value = getattr(module, name)
            if not callable(value):
                continue
            lowered = name.lower()
            if any(term in lowered for term in vocabulary):
                suspects.append(name)
        self.assertEqual([], suspects)

    def test_the_registry_contains_no_payment_tool_from_this_block(self):
        registry = build_registry()
        for name in ERP_TOOLS:
            tool = registry.get(name)
            self.assertNotIn('pay', name.split('.')[-1])
            self.assertIn(tool.risk, {'read', 'write'})
        self.assertEqual(frozenset({'erp.posting_submit'}), POSTING_TOOLS)

    def test_a_posting_body_can_never_carry_a_payment_instruction(self):
        """The body is built from resolved fields only; nothing is appended later."""
        posting = self.resolve()
        body = posting_body(erp_config(TENANT), posting, note='ok')
        self.assertEqual({'document_type', 'counterparty', 'account',
                          'document_number', 'document_date', 'amount', 'currency',
                          'counterparty_name', 'comment'}, set(body))
        self.assertEqual('invoice', body['document_type'])
        for forbidden in ('pay', 'payment', 'transfer', 'iban', 'account_number',
                          'bic', 'swift'):
            self.assertNotIn(forbidden, body)

    def test_a_submitted_posting_reports_that_it_moved_no_money(self):
        transport = ScriptTransport(
            [{}, {'result': {'Ref_Key': 'DOC-77'}}])
        result = self.call('erp.posting_submit',
                           {'document': json.dumps(DOCUMENT),
                            'account': 'purchase', 'counterparty': 'acme'}, transport)
        self.assertTrue(result['posted'])
        self.assertFalse(result['moves_money'])
        self.assertEqual('DOC-77', result['external_id'])

    def test_autonomous_ladder_does_not_acquire_a_payment_path(self):
        self.policy = {**POLICY, 'ladder': 'autonomous'}
        transport = ScriptTransport([{}, {'result': {'Ref_Key': 'DOC-1'}}])
        result = self.call('erp.posting_submit',
                           {'document': json.dumps(DOCUMENT),
                            'account': 'purchase', 'counterparty': 'acme'}, transport)
        self.assertFalse(result['moves_money'])
        for call in transport.calls:
            self.assertEqual('POST' if call is transport.posts[-1] else call['method'],
                             call['method'])
            self.assertNotIn('payment', json.dumps(call.get('body') or {}))

    # ---------------------------------------------------- exact-or-refuse

    def test_an_incomplete_document_is_refused_by_name(self):
        for missing in REQUIRED_FIELDS:
            document = dict(DOCUMENT)
            document.pop(missing, None)
            extra = {'account': 'purchase', 'counterparty': 'acme'}
            if missing in extra:
                extra.pop(missing)
            with self.subTest(missing=missing):
                with self.assertRaises(ErpError) as caught:
                    resolve_posting(TENANT, document, **extra)
                self.assertIn('incomplete', str(caught.exception))

    def test_no_field_is_inferred_when_the_document_is_silent(self):
        """An empty document produces a refusal, never a default."""
        with self.assertRaises(ErpError) as caught:
            resolve_posting(TENANT, {})
        message = str(caught.exception)
        for name in ('supplier', 'number', 'doc_date', 'currency', 'total_minor',
                     'account', 'counterparty'):
            self.assertIn(name, message)

    def test_the_account_is_an_operator_alias_never_a_raw_code(self):
        with self.assertRaises(ErpError) as caught:
            self.resolve(account='60.01')
        self.assertIn('not declared', str(caught.exception))
        # The declared alias resolves to the operator's own code.
        self.assertEqual('60.01', self.resolve(account='purchase')['account'])

    def test_an_undeclared_counterparty_is_refused(self):
        with self.assertRaises(ErpError) as caught:
            self.resolve(counterparty='acme-llc')
        self.assertIn('not declared', str(caught.exception))

    def test_an_ambiguous_date_is_refused_rather_than_guessed(self):
        """10.01.2026 shifts a posted document by months, which is a tax problem."""
        for bad in ('10.01.2026', '01.10.2026', 'next tuesday', '2026-13-01'):
            with self.subTest(bad=bad):
                document = {**DOCUMENT, 'doc_date': bad}
                with self.assertRaises(ErpError) as caught:
                    resolve_posting(TENANT, document, account='purchase',
                                    counterparty='acme')
                self.assertIn('doc_date', str(caught.exception))

    def test_a_disambiguated_numeric_date_is_accepted(self):
        """13.01.2026 has only one reading, so it is not ambiguous."""
        for text, expected in (('13.01.2026', '2026-01-13'),
                               ('2026-09-12', '2026-09-12'),
                               ('25.12.2026', '2026-12-25')):
            with self.subTest(text=text):
                document = {**DOCUMENT, 'doc_date': text}
                posting = resolve_posting(TENANT, document, account='purchase',
                                          counterparty='acme')
                self.assertEqual(expected, posting['doc_date'])

    def test_a_float_amount_is_refused_with_its_own_reason(self):
        """A malformed field and a missing one must not read the same.

        Folding 'the amount you sent is not a number' into 'incomplete' hides a typo
        behind a shrug; the operator is sent looking for a field that is present.
        """
        document = {**DOCUMENT, 'total_minor': 1250000.5}
        with self.assertRaises(ErpError) as caught:
            resolve_posting(TENANT, document, account='purchase', counterparty='acme')
        self.assertIn('integer', str(caught.exception))
        self.assertNotIn('incomplete', str(caught.exception))

    def test_a_malformed_currency_is_refused_with_its_own_reason(self):
        document = {**DOCUMENT, 'currency': 'SOMM'}
        with self.assertRaises(ErpError) as caught:
            resolve_posting(TENANT, document, account='purchase', counterparty='acme')
        self.assertIn('three-letter', str(caught.exception))
        self.assertNotIn('incomplete', str(caught.exception))

    def test_a_negative_amount_is_refused(self):
        document = {**DOCUMENT, 'total_minor': -1}
        with self.assertRaises(ErpError):
            resolve_posting(TENANT, document, account='purchase', counterparty='acme')

    def test_posting_is_refused_when_the_tenant_never_declared_it(self):
        self.write_config(erp=None)
        path = self.root / 'empty.json'
        path.write_text(json.dumps({TENANT: {}}), encoding='utf-8')
        with mock.patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(path)}):
            with self.assertRaises(Forbidden):
                resolve_posting(TENANT, dict(DOCUMENT), account='purchase',
                                counterparty='acme')

    def test_posting_is_refused_when_the_operator_disabled_it(self):
        self.write_config(erp={**CONFIG, 'enabled': False})
        with self.assertRaises(Forbidden) as caught:
            resolve_posting(TENANT, dict(DOCUMENT), account='purchase',
                            counterparty='acme')
        self.assertIn('disabled', str(caught.exception))

    # ----------------------------------------------------- duplicate guards

    def test_the_erp_is_asked_before_anything_is_posted(self):
        transport = ScriptTransport([{'result': {}}, {'result': {'Ref_Key': 'DOC-1'}}])
        posting = self.resolve(account='purchase', counterparty='acme')
        submit(self.engine, TENANT, AGENT, posting, transport=transport)
        self.assertEqual(['GET', 'POST'],
                         [call['method'] for call in transport.calls])

    def test_a_document_the_erp_already_holds_is_not_posted(self):
        transport = ScriptTransport([{'result': {'Ref_Key': 'EXISTING-9'}}])
        posting = self.resolve(account='purchase', counterparty='acme')
        result = submit(self.engine, TENANT, AGENT, posting, transport=transport)
        self.assertFalse(result['posted'])
        self.assertTrue(result['duplicate'])
        self.assertEqual('already_in_erp', result['reason'])
        self.assertEqual([], transport.posts)
        # The refusal is recorded, not just returned: an operator must be able to
        # see that the ERP held it and the platform declined to duplicate.
        rows = ledger(self.engine, TENANT)
        self.assertEqual('skipped_existing', rows[0]['status'])
        self.assertEqual('EXISTING-9', rows[0]['external_id'])

    def test_the_local_ledger_blocks_a_second_posting(self):
        first = ScriptTransport([{'result': {}}, {'result': {'Ref_Key': 'DOC-1'}}])
        posting = self.resolve(account='purchase', counterparty='acme')
        submit(self.engine, TENANT, AGENT, posting, transport=first)
        second = ScriptTransport([{'result': {}}, {'result': {'Ref_Key': 'DOC-2'}}])
        with self.assertRaises(Conflict) as caught:
            submit(self.engine, TENANT, AGENT, posting, transport=second)
        self.assertIn('already posted', str(caught.exception))
        # Nothing was sent on the second attempt, not even the search.
        self.assertEqual([], second.calls)

    def test_the_unique_index_is_the_second_guard_not_the_first(self):
        """A ledger hit is caught before I/O; the index catches a race."""
        posting = self.resolve(account='purchase', counterparty='acme')
        transport = ScriptTransport([{'result': {}}, {'result': {'Ref_Key': 'DOC-1'}}])
        submit(self.engine, TENANT, AGENT, posting, transport=transport)
        # Bypass the ledger read and try to write the same identity again, as a
        # concurrent submit would.
        from platform_runtime.erp import _record
        with self.assertRaises(Conflict) as caught:
            _record(self.engine, TENANT, posting, '', {}, 'posted', 'DOC-1b', {}, {})
        self.assertIn('already been posted', str(caught.exception))

    def test_an_unreachable_erp_blocks_the_posting_rather_than_assuming_new(self):
        """Not being able to ask is not the same as the answer being no."""

        def broken(url, body=None, headers=None, method='GET', timeout=15):
            # A transport-level failure, which is what an outage actually raises.
            raise TimeoutError('connection timed out')

        posting = self.resolve()
        with self.assertRaises(ErpError) as caught:
            submit(self.engine, TENANT, AGENT, posting, transport=broken)
        message = str(caught.exception)
        self.assertIn('already posted', message)
        self.assertIn('refused', message)
        # Nothing was recorded as a posting: the document is still open.
        self.assertIsNone(posted(self.engine, TENANT, posting))

    def test_a_credential_problem_is_not_disguised_as_an_outage(self):
        """An operator sent to investigate the ERP when the fix is an env var
        has been sent to the wrong place; the variable name must survive."""
        posting = self.resolve()
        env = {k: v for k, v in os.environ.items() if k != 'ERP_PROBE_TOKEN'}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as caught:
                submit(self.engine, TENANT, AGENT, posting,
                       transport=ScriptTransport([{'result': {}}]))
        self.assertIn('ERP_PROBE_TOKEN', str(caught.exception))
        self.assertNotIn('could not ask the ERP', str(caught.exception))

    def test_a_different_amount_does_not_escape_the_duplicate_key(self):
        """The amount is deliberately excluded, exactly as the document block does."""
        posting = self.resolve(account='purchase', counterparty='acme')
        transport = ScriptTransport([{'result': {}}, {'result': {'Ref_Key': 'DOC-1'}}])
        submit(self.engine, TENANT, AGENT, posting, transport=transport)
        altered = {**DOCUMENT, 'total_minor': 9_999_999}
        reposting = resolve_posting(TENANT, altered, account='purchase',
                                    counterparty='acme')
        self.assertNotEqual(posting['total_minor'], reposting['total_minor'])
        self.assertEqual(identity(posting), identity(reposting))
        with self.assertRaises(Conflict):
            submit(self.engine, TENANT, AGENT, reposting,
                   transport=ScriptTransport())

    def test_a_different_document_number_is_a_different_identity(self):
        first = self.resolve(account='purchase', counterparty='acme')
        second = resolve_posting(TENANT, {**DOCUMENT, 'number': 'INV-1043'},
                                 account='purchase', counterparty='acme')
        self.assertNotEqual(identity(first), identity(second))

    def test_a_second_driver_is_a_different_identity(self):
        """Two ERPs are two ledgers; the same invoice may legitimately live in both."""
        driver, kind, supplier, number = identity(self.resolve())
        self.assertEqual('onec_http', driver)
        self.assertEqual('invoice', kind)
        self.assertEqual('acme', supplier)
        self.assertEqual('INV-1042', number)

    # ------------------------------------------------------- failure records

    def test_a_transport_failure_is_recorded_before_it_propagates(self):
        def broken(url, body=None, headers=None, method='GET', timeout=15):
            if method == 'GET':
                return {'result': {}}
            raise ErpError('ERP transport failure')

        posting = self.resolve(account='purchase', counterparty='acme')
        with self.assertRaises(ErpError):
            submit(self.engine, TENANT, AGENT, posting, transport=broken)
        rows = ledger(self.engine, TENANT)
        self.assertEqual('failed', rows[0]['status'])
        self.assertEqual(1, len(rows))

    def test_a_posting_without_an_identifier_is_unconfirmed_not_settled(self):
        transport = ScriptTransport([{'result': {}}, {'result': {}}])
        posting = self.resolve(account='purchase', counterparty='acme')
        with self.assertRaises(Conflict) as caught:
            submit(self.engine, TENANT, AGENT, posting, transport=transport)
        self.assertIn('reconcile', str(caught.exception))
        rows = ledger(self.engine, TENANT)
        self.assertEqual('unconfirmed', rows[0]['status'])
        self.assertEqual('', rows[0]['external_id'])

    def test_a_failed_posting_can_be_retried(self):
        """A failure records the attempt but must not block the correction."""
        def flaky(url, body=None, headers=None, method='GET', timeout=15):
            if method == 'GET':
                return {'result': {}}
            raise ErpError('ERP transport failure')

        posting = self.resolve(account='purchase', counterparty='acme')
        with self.assertRaises(ErpError):
            submit(self.engine, TENANT, AGENT, posting, transport=flaky)
        # The failed row is recorded for audit but does NOT claim the identity, so
        # the document is still postable. A transport blip must not permanently
        # block a legitimate invoice.
        self.assertIsNone(posted(self.engine, TENANT, posting))
        self.assertEqual('failed', ledger(self.engine, TENANT)[0]['status'])
        good = ScriptTransport([{'result': {}}, {'result': {'Ref_Key': 'DOC-9'}}])
        result = submit(self.engine, TENANT, AGENT, posting, transport=good)
        self.assertTrue(result['posted'])

    def test_the_ledger_never_carries_a_credential(self):
        transport = ScriptTransport([{'result': {}}, {'result': {'Ref_Key': 'DOC-1'}}])
        posting = self.resolve(account='purchase', counterparty='acme')
        submit(self.engine, TENANT, AGENT, posting, transport=transport)
        serialized = json.dumps(ledger(self.engine, TENANT))
        self.assertNotIn('secret-token', serialized)
        self.assertNotIn('Authorization', serialized)

    # ------------------------------------------------------------- transport

    def test_the_transport_refuses_plain_http(self):
        from platform_runtime.erp import default_erp_transport
        with self.assertRaises(ValueError) as caught:
            default_erp_transport('http://erp.example.uz/documents')
        self.assertIn('HTTPS', str(caught.exception))

    def test_the_transport_refuses_an_embedded_credential(self):
        from platform_runtime.erp import default_erp_transport
        with self.assertRaises(ValueError):
            default_erp_transport('https://user:pw@erp.example.uz/documents')

    def test_an_error_body_is_never_echoed(self):
        """An ERP error page can carry a stack trace or an internal hostname."""
        from platform_runtime.erp import default_erp_transport
        import urllib.error
        error = urllib.error.HTTPError('https://erp.example.uz/x', 500, 'boom',
                                       {}, None)
        with mock.patch('urllib.request.OpenerDirector.open', side_effect=error):
            with self.assertRaises(ErpError) as caught:
                default_erp_transport('https://erp.example.uz/x')
        message = str(caught.exception)
        self.assertIn('500', message)
        self.assertNotIn('boom', message)

    # ----------------------------------------------------------- configuration

    def test_a_host_with_a_scheme_is_refused(self):
        with self.assertRaises(ValueError):
            erp_config_with({**CONFIG, 'host': 'https://erp.example.uz'})
        self.write_config()

    def test_a_traversal_path_is_refused(self):
        for bad in ('../admin', '/a/../b', '//evil.example', 'documents'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    erp_config_with({**CONFIG, 'post_path': bad})
        self.write_config()

    def test_an_unknown_driver_is_refused(self):
        with self.assertRaises(ValueError):
            erp_config_with({**CONFIG, 'driver': 'sap'})
        self.write_config()

    def test_an_unknown_config_key_is_refused(self):
        with self.assertRaises(ValueError):
            erp_config_with({**CONFIG, 'webhook': 'https://evil.example'})
        self.write_config()

    def test_a_malformed_credential_reference_is_refused(self):
        for bad in ('lowercase', 'with-dash', '', '9START'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    erp_config_with({**CONFIG, 'token_env': bad})
        self.write_config()

    # -------------------------------------------------------------- tool layer

    def test_tools_are_registered_with_the_declared_risks(self):
        registry = build_registry()
        self.assertEqual('read', registry.get('erp.posting_prepare').risk)
        self.assertEqual('read', registry.get('erp.posting_status').risk)
        self.assertEqual('write', registry.get('erp.posting_submit').risk)

    def test_prepare_reports_the_refusal_instead_of_raising(self):
        """A dry run whose failure mode is an exception cannot be used as a check."""
        result = self.call('erp.posting_prepare',
                           {'document': json.dumps({'number': 'INV-1'})})
        self.assertFalse(result['ready'])
        self.assertIn('incomplete', result['reason'])

    def test_prepare_shows_exactly_what_would_be_sent(self):
        result = self.call('erp.posting_prepare',
                           {'document': json.dumps(DOCUMENT), 'account': 'purchase',
                            'counterparty': 'acme'})
        self.assertTrue(result['ready'])
        self.assertEqual('ACME-LLC-0001', result['would_send']['counterparty'])
        self.assertEqual('60.01', result['would_send']['account'])
        self.assertEqual(1_250_000, result['would_send']['amount'])

    def test_prepare_makes_no_provider_call(self):
        transport = ScriptTransport([{'result': {}}])
        self.call('erp.posting_prepare',
                  {'document': json.dumps(DOCUMENT), 'account': 'purchase',
                   'counterparty': 'acme'}, transport)
        self.assertEqual([], transport.calls)

    def test_prepare_reports_a_document_the_ledger_already_holds(self):
        posting = self.resolve(account='purchase', counterparty='acme')
        good = ScriptTransport([{'result': {}}, {'result': {'Ref_Key': 'DOC-1'}}])
        submit(self.engine, TENANT, AGENT, posting, transport=good)
        result = self.call('erp.posting_prepare',
                           {'document': json.dumps(DOCUMENT), 'account': 'purchase',
                            'counterparty': 'acme'})
        self.assertFalse(result['ready'])
        self.assertIsNotNone(result['ledger'])

    def test_status_reads_the_ledger_and_reports_the_driver(self):
        result = self.call('erp.posting_status', {})
        self.assertEqual('onec_http', result['driver'])
        self.assertTrue(result['enabled'])
        self.assertEqual(0, result['count'])

    def test_status_limit_is_coerced_not_typed(self):
        result = self.call('erp.posting_status', {'limit': None})
        self.assertEqual(0, result['count'])
        with self.assertRaises(ValueError):
            self.call('erp.posting_status', {'limit': 'many'})

    def seed_posting(self, posting_id):
        """Write one ledger row directly.

        The count tests measure the *read* path, so they seed the table rather than
        driving a submit through a transport: going through `submit` would make the
        assertion depend on the write path's own rules, which are tested elsewhere.
        """
        with self.engine.tx() as c:
            c.execute(
                '''INSERT INTO p_erp_postings(tenant,id,document,driver,kind,supplier,
                   number,currency,total_minor,external_id,status,created,settled)
                   VALUES(?,?,'doc','onec_http','invoice','sup','N','UZS',100,?,
                   'posted',1400.0,0)''',
                (TENANT, posting_id, f'ext-{posting_id}'))

    def test_status_count_is_the_ledger_size_not_the_page_size(self):
        """`count` must answer "how many postings are there", not "how many fit".

        It used to be `len(rows)`, so a ledger of twenty and a ledger of four
        thousand both answered `count: 20` -- an inventory figure that is really a
        page size. The two are told apart by seeding more postings than the limit
        and reading both numbers off the same reply.
        """
        for index in range(25):
            self.seed_posting(f'p{index:03d}')
        result = self.call('erp.posting_status', {'limit': 10})
        self.assertEqual(25, result['count'])
        self.assertEqual(10, result['returned'])
        self.assertTrue(result['truncated'])

    def test_status_is_not_truncated_when_the_ledger_exactly_fills_the_page(self):
        """The boundary the old shape could not express at all."""
        for index in range(10):
            self.seed_posting(f'p{index:03d}')
        result = self.call('erp.posting_status', {'limit': 10})
        self.assertEqual(10, result['count'])
        self.assertEqual(10, result['returned'])
        self.assertFalse(result['truncated'])

    def test_an_agent_without_the_tool_is_refused(self):
        self.policy = {**POLICY, 'tools': ['erp.posting_prepare']}
        with self.assertRaises(Forbidden):
            self.call('erp.posting_submit',
                      {'document': json.dumps(DOCUMENT), 'account': 'purchase',
                       'counterparty': 'acme'})
        self.policy = dict(POLICY)

    def test_the_plan_is_recomputed_rather_than_trusted_from_the_arguments(self):
        """An approved step replays its arguments; a stored plan must not be honoured."""
        transport = ScriptTransport([{'result': {}}, {'result': {'Ref_Key': 'DOC-1'}}])
        result = self.call('erp.posting_submit',
                           {'document': json.dumps(DOCUMENT), 'account': 'purchase',
                            'counterparty': 'acme',
                            'plan': json.dumps({'total_minor': 1})}, transport)
        self.assertTrue(result['posted'])
        self.assertEqual(1_250_000, transport.posts[0]['body']['amount'])

    # ------------------------------------------------------- custom_http driver

    def test_both_drivers_declare_the_same_required_configuration(self):
        """The two drivers differ in how an answer is READ, never in what is required.

        A second driver that needed fewer fields would be a second, easier way to
        reach the ERP, which is the opposite of the point of declaring a target.
        """
        for driver in DRIVERS:
            settings = erp_config_with(dict(CONFIG, driver=driver))
            self.assertEqual(driver, settings['driver'])
            self.assertEqual(CONFIG['accounts'], settings['accounts'])
            self.assertEqual(CONFIG['counterparties'], settings['counterparties'])

    def test_a_third_driver_is_refused(self):
        with self.assertRaises(ValueError):
            erp_config_with(dict(CONFIG, driver='odoo_xmlrpc'))

    def test_custom_http_reads_a_flat_answer_while_onec_reads_a_nested_one(self):
        """The driver's only behavioural difference, measured on the same response.

        A custom JSON endpoint returns the document id at the top level; 1C's
        service wraps it. If the driver changed nothing, one of these could only
        work by accident, and a custom ERP would silently fail to see a document it
        already holds -- the double-posting condition.
        """
        flat = {'id': 'C-77'}
        nested = {'result': {'Ref_Key': 'C-77'}}
        custom = erp_config_with(dict(CONFIG, driver='custom_http', response_map={
            'created_id': 'id', 'existing_id': 'id', 'items': 'items'}))
        onec = erp_config_with(dict(CONFIG, driver='onec_http'))
        self.assertEqual('C-77', find_posted(custom, self.resolve(), ScriptTransport([flat])))
        self.assertIsNone(find_posted(onec, self.resolve(), ScriptTransport([flat])))
        self.assertEqual('C-77', find_posted(onec, self.resolve(), ScriptTransport([nested])))

    def test_basic_auth_is_sent_for_a_driver_that_declares_it(self):
        """basic vs bearer is a config choice, and the header must follow it.

        1C on-premise typically authenticates with user:password, so a block that
        only ever sent a bearer token would be undeployable at the customers this
        driver exists for.
        """
        # write_config, not erp_config_with: the former persists the block that
        # submit() will READ, the latter only validates a payload in isolation.
        self.write_config(dict(CONFIG, driver='custom_http', auth='basic',
                               basic_auth_env='ERP_BASIC', token_env=None))
        with mock.patch.dict(os.environ, {'ERP_BASIC': 'agent:s3cret'}):
            # The search answers "not held", so the POST is the request under test.
            transport = ScriptTransport([{'result': {}}],
                                        post_response={'result': {'Ref_Key': 'DOC-9'}})
            submit(self.engine, TENANT, AGENT, self.resolve(), transport=transport)
        header = transport.posts[0]['headers']['Authorization']
        self.assertTrue(header.startswith('Basic '), header)
        import base64
        self.assertEqual('agent:s3cret',
                         base64.b64decode(header.split(' ', 1)[1]).decode('utf-8'))

    def test_a_malformed_basic_credential_is_refused_without_a_request(self):
        """The failure must happen before I/O, naming the shape that is wrong."""
        self.write_config(dict(CONFIG, driver='custom_http', auth='basic',
                               basic_auth_env='ERP_BASIC', token_env=None))
        with mock.patch.dict(os.environ, {'ERP_BASIC': 'agent-with-no-password'}):
            transport = ScriptTransport([{'result': {'Ref_Key': 'DOC-9'}}])
            with self.assertRaises(ValueError) as caught:
                submit(self.engine, TENANT, AGENT, self.resolve(), transport=transport)
        self.assertIn('user:password', str(caught.exception))
        self.assertEqual([], transport.calls)

    def test_the_two_drivers_do_not_share_a_duplicate_identity(self):
        """The driver is part of the identity, so the same invoice number can be
        posted once per ERP. A migration between two systems needs exactly that,
        and conflating the two would refuse a legitimate second posting -- while
        dropping the driver from the key would let the SECOND system's copy be
        posted twice, which is the failure this key exists to prevent."""
        onec = self.resolve()
        custom = dict(onec, driver='custom_http')
        self.assertNotEqual(identity(onec), identity(custom))
        # A search always answers "not held" and a POST always answers with an id,
        # so both submissions are genuine first postings regardless of ordering.
        transport = ScriptTransport([{'result': {}}], post_response={'result': {'Ref_Key': 'ONE'}})
        first = submit(self.engine, TENANT, AGENT, onec, transport=transport)
        second = submit(self.engine, TENANT, AGENT, custom, transport=transport)
        self.assertTrue(first['posted'])
        self.assertTrue(second['posted'])
        self.assertEqual(2, len(transport.posts))

    def test_the_same_driver_still_refuses_the_same_document(self):
        """Adding the driver to the key must not weaken the guard within one ERP.

        Without this, the previous test would also pass if the identity had simply
        stopped being enforced at all.
        """
        posting = self.resolve()
        transport = ScriptTransport([{'result': {}}], post_response={'result': {'Ref_Key': 'ONE'}})
        first = submit(self.engine, TENANT, AGENT, posting, transport=transport)
        self.assertTrue(first['posted'])
        with self.assertRaises(Conflict):
            submit(self.engine, TENANT, AGENT, posting, transport=transport)
        # The refusal came from the local ledger, so no second POST was issued.
        self.assertEqual(1, len(transport.posts))

    # ------------------------------------------------ measured boundary ceilings
    #
    # Every test below was added by the fazza 25 ultra-audit, and every one of them
    # exists because a revert proved its absence: nine of the module's eleven
    # thresholds were correct AND unmeasured, so flipping the comparison or nudging
    # the literal left the whole suite green. Each test now sits ON the boundary and
    # one step past it, and the source-literal test pins the boundary's value.

    def test_the_boundary_constants_are_the_documented_literals(self):
        """A ceiling that can move with the test is not a pinned ceiling.

        `MAX_RESPONSE_BYTES = 80_001` would leave the behavioural test green,
        because that test derives its payload from the same symbol. The literal is
        asserted separately so a widened bound is visible as a failure.
        """
        expected = (
            'MAX_RESPONSE_BYTES = 80_000',
            'MAX_NOTE_CHARS = 500',
            'MAX_AMOUNT_MINOR = 10 ** 15',
            '    if type(timeout) is not int or not 1 <= timeout <= 60:',
            '    if not 1 <= month <= 12 or not 1 <= day <= 31 '
            'or not 1900 <= year <= 2999:',
            '        if depth > 3:',
            "        if not isinstance(alias, str) or not re.fullmatch("
            "r'[a-z0-9][a-z0-9_.-]{0,63}', alias):",
            '        if not isinstance(target, str) or not target '
            'or len(target) > 64:',
            '    limit = min(max(1, limit), 200)',
            '        elif first > 12:',
        )
        # Exact LINE membership, not substring: `'MAX_AMOUNT_MINOR = 10 ** 15'` is a
        # substring of `'MAX_AMOUNT_MINOR = 10 ** 15 + 1'`, so a substring assertion
        # is satisfied by the widened ceiling it exists to catch. Measured: the
        # first version of this test left M3 green.
        lines = set(SOURCE.splitlines())
        for literal in expected:
            with self.subTest(literal=literal):
                self.assertIn(literal, lines)

    def test_the_amount_ceiling_itself_is_accepted_and_one_past_it_is_not(self):
        """`0 <= total_minor <= 10**15` is inclusive at BOTH ends.

        The only amount test asserted that `-1` is refused, which pins the floor's
        existence and not its value: a ceiling of 10**15, of 10**9 or of nothing at
        all would each pass it. Both ends are walked, and one step past the top.
        """
        for value in (0, MAX_AMOUNT_MINOR):
            with self.subTest(value=value):
                posting = self.resolve({**DOCUMENT, 'total_minor': value})
                self.assertEqual(value, posting['total_minor'])
        for value in (MAX_AMOUNT_MINOR + 1, -1):
            with self.subTest(value=value):
                with self.assertRaises(ErpError):
                    self.resolve({**DOCUMENT, 'total_minor': value})

    def test_the_supplier_text_ceiling_is_two_hundred_characters(self):
        for length, accepted in ((200, True), (201, False)):
            with self.subTest(length=length):
                document = {**DOCUMENT, 'supplier': 'a' * length}
                if accepted:
                    self.assertEqual('a' * length, self.resolve(document)['supplier'])
                else:
                    with self.assertRaises(ErpError):
                        self.resolve(document)

    def test_the_note_ceiling_is_five_hundred_characters(self):
        settings = erp_config(TENANT)
        posting = self.resolve()
        body = posting_body(settings, posting, 'n' * MAX_NOTE_CHARS)
        self.assertEqual(MAX_NOTE_CHARS, len(body['comment']))
        with self.assertRaises(ErpError):
            posting_body(settings, posting, 'n' * (MAX_NOTE_CHARS + 1))

    def test_the_response_byte_ceiling_is_eighty_thousand(self):
        """One byte past the ceiling is refused; the ceiling itself is not.

        The transport reads `MAX + 1` bytes, so the predicate has to be `>` and not
        `>=`: under `>=` a response of exactly 80_000 bytes -- a legitimate answer --
        would be reported as oversized.
        """

        class Response:
            def __init__(self, raw):
                self.status = 200
                self._raw = raw

            def read(self, size=-1):
                return self._raw[:size] if size >= 0 else self._raw

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class Opener:
            def __init__(self, raw):
                self.raw = raw

            def open(self, request, timeout=None):
                return Response(self.raw)

        def sized(total):
            return b'{"x":"' + b'a' * (total - 8) + b'"}'

        at_ceiling = sized(MAX_RESPONSE_BYTES)
        past_ceiling = sized(MAX_RESPONSE_BYTES + 1)
        self.assertEqual(MAX_RESPONSE_BYTES, len(at_ceiling))
        with mock.patch('urllib.request.build_opener', lambda *a, **k: Opener(at_ceiling)):
            self.assertEqual({'x': 'a' * (MAX_RESPONSE_BYTES - 8)},
                             default_erp_transport('https://erp.example.uz/x'))
        with mock.patch('urllib.request.build_opener', lambda *a, **k: Opener(past_ceiling)):
            with self.assertRaises(ErpError):
                default_erp_transport('https://erp.example.uz/x')

    def test_the_timeout_window_is_one_to_sixty_seconds(self):
        for value in (1, 60):
            with self.subTest(value=value):
                settings = erp_config_with({**CONFIG, 'timeout_seconds': value})
                self.assertEqual(value, settings['timeout_seconds'])
        for value in (0, 61, True, 15.0):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    erp_config_with({**CONFIG, 'timeout_seconds': value})

    def test_the_calendar_year_window_is_1900_to_2999(self):
        for text in ('1900-01-01', '2999-12-31'):
            with self.subTest(text=text):
                self.assertEqual(text, self.resolve({**DOCUMENT, 'doc_date': text})['doc_date'])
        for text in ('1899-12-31', '3000-01-01'):
            with self.subTest(text=text):
                with self.assertRaises(ErpError):
                    self.resolve({**DOCUMENT, 'doc_date': text})

    def test_a_leap_day_is_a_real_date_and_a_common_year_twenty_ninth_is_not(self):
        self.assertEqual('2024-02-29',
                         self.resolve({**DOCUMENT, 'doc_date': '2024-02-29'})['doc_date'])
        with self.assertRaises(ErpError):
            self.resolve({**DOCUMENT, 'doc_date': '2023-02-29'})

    def test_a_numeric_date_is_unique_only_when_exactly_one_part_exceeds_twelve(self):
        """Two parts over twelve name no month; two parts at or under twelve are ambiguous.

        Both are refused, and they are refused with DIFFERENT reasons. The first
        used to say "ambiguous ... send a form where one part is greater than 12",
        which is a remedy the offending input already satisfies -- the operator is
        sent in a circle. Measured, then fixed.
        """
        for text, expected in (('13.01.2026', '2026-01-13'), ('01.13.2026', '2026-01-13')):
            with self.subTest(text=text):
                self.assertEqual(expected, self.resolve({**DOCUMENT, 'doc_date': text})['doc_date'])
        with self.assertRaises(ErpError) as ambiguous:
            self.resolve({**DOCUMENT, 'doc_date': '12.12.2026'})
        self.assertIn('ambiguous', str(ambiguous.exception))
        with self.assertRaises(ErpError) as no_month:
            self.resolve({**DOCUMENT, 'doc_date': '13.13.2026'})
        self.assertIn('no month', str(no_month.exception))
        self.assertNotIn('ambiguous', str(no_month.exception))

    def test_the_pointer_depth_ceiling_is_four_segments(self):
        document = {'a': {'b': {'c': {'d': 'four', 'e': {'f': 'five'}}}}}
        self.assertEqual('four', _dig(document, 'a.b.c.d'))
        self.assertIsNone(_dig(document, 'a.b.c.d.e'))
        self.assertEqual('D', _dig(document, 'a.b.c.d.e', 'D'))

    def test_the_operator_alias_and_target_ceilings_are_sixty_four_characters(self):
        self.assertEqual({'a' * 64: '60.01'},
                         erp_config_with({**CONFIG, 'accounts': {'a' * 64: '60.01'}})['accounts'])
        with self.assertRaises(ValueError):
            erp_config_with({**CONFIG, 'accounts': {'a' * 65: '60.01'}})
        self.assertEqual({'acct': 'x' * 64},
                         erp_config_with({**CONFIG, 'accounts': {'acct': 'x' * 64}})['accounts'])
        with self.assertRaises(ValueError):
            erp_config_with({**CONFIG, 'accounts': {'acct': 'x' * 65}})

    def test_the_status_limit_ceiling_is_two_hundred_rows(self):
        """The clamp is 1..200, measured from both sides and one step past each end.

        `test_status_limit_is_coerced_not_typed` only walked `None` and `'many'`, so
        the clamp's own numbers were never read: `min(max(1, limit), 201)` passed.
        """
        for index in range(205):
            self.seed_posting(f'q{index:03d}')
        for limit, expected in ((1, 1), (200, 200), (0, 1), (201, 200)):
            with self.subTest(limit=limit):
                result = self.call('erp.posting_status', {'limit': limit})
                self.assertEqual(expected, result['returned'])
        result = self.call('erp.posting_status', {'limit': 200})
        self.assertEqual(205, result['count'])
        self.assertTrue(result['truncated'])



def erp_config_with(erp):
    """Validate a config payload directly, without touching the shared file."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'integrations.json'
        path.write_text(json.dumps({TENANT: {'erp_posting': erp}}), encoding='utf-8')
        with mock.patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(path)}):
            return erp_config(TENANT)
