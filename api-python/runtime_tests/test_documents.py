"""Document intake contract tests. Real Engine, real SQLite.

The property that matters most here is a **negative** one, and it is asserted
structurally rather than promised in prose:

    The platform does not move money.

PRD v0.5 §8 puts it in the product's own scope: the document flow prepares a
control plan and puts a payment up for approval; it does not execute one. So the
suite checks the module's own surface for any function, and the registry for any
tool, that could pay, transfer or settle — and it checks that a prepared plan says
``executes_payment: False`` in its output rather than merely failing to include a
payment field.

The rest of the suite covers the control chain the block exists for:

* **money is compared in integer minor units, never floats** — a float amount is
  refused, because an accounts-payable check off by one tiyin is not a check;
* **a document that disagrees with itself is refused** — line items must sum to the
  stated total, or the total we later compare against a purchase order is a
  fiction;
* **absent evidence and contradicting evidence are different** — a missing purchase
  order is ``incomplete``, a disagreeing one is ``mismatch``, and an approver must
  be able to tell them apart;
* **the dedup key is the supplier plus the supplier's own number, not the amount** —
  the same number with a different amount is *more* suspicious, not less;
* **fraud controls report signals with evidence and never a verdict** — and a weak
  signal is reported without gating, because most real invoices are round numbers;
* **the plan is recomputed at write time, not trusted from the arguments** — an
  approved step replays its arguments, so a caller must not be able to approve a
  clean plan and submit a dirty one under the same fingerprint.
"""
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import inspect

from platform_runtime import documents as D
from platform_runtime.documents import (
    DEFAULT_TOLERANCE_MINOR,
    DOCUMENT_KINDS,
    DOCUMENT_TOOLS,
    MATERIAL_SIGNALS,
    WEAK_SIGNALS,
    DocumentError,
    document_key,
    documents_config,
    duplicates,
    format_amount,
    fraud_signals,
    load,
    match,
    normalize,
    plans,
    posting_plan,
    register_document_tools,
    remember,
    supplier_history,
)
from platform_runtime.engine import Conflict, Engine, Forbidden, NotFound
from platform_runtime.tools import build_registry

TENANT = 't_ap'
AGENT = 'finance.ap'

POLICY = {'tools': list(DOCUMENT_TOOLS), 'allowed_connections': [],
          'ladder': 'human_assisted'}


def fields(total=1250000, number='INV-1', supplier='acme', **extra):
    base = {'kind': 'invoice', 'supplier': supplier, 'number': number,
            'currency': 'UZS', 'total': total}
    base.update(extra)
    return base


class Clock:
    def __init__(self, start=1_789_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.policy = dict(POLICY)
        self.engine = Engine(self.root / 'ap.db', build_registry(),
                             lambda t, a: self.policy, clock=Clock())

    # -------------------------------------------------------- money arithmetic

    def test_amounts_are_integer_minor_units(self):
        self.assertEqual(1250000, normalize(fields(1250000))['total_minor'])
        self.assertEqual(123456, normalize(fields('1234.56', currency='USD'))['total_minor'])

    def test_a_float_amount_is_refused(self):
        """Binary floating point cannot hold every decimal amount exactly."""
        with self.assertRaises(ValueError) as caught:
            normalize(fields(1250000.5))
        self.assertIn('float', str(caught.exception))

    def test_a_decimal_string_is_truncated_to_the_currency_precision(self):
        """Not rounded: rounding a submitted amount up inflates what a PO is compared to."""
        self.assertEqual(1250000, normalize(fields('1250000.99', currency='UZS'))['total_minor'])
        self.assertEqual(123456, normalize(fields('1234.569', currency='USD'))['total_minor'])

    def test_formatting_round_trips(self):
        for currency, text in (('UZS', '1250000'), ('USD', '1234.56')):
            document = normalize(fields(text, currency=currency))
            self.assertEqual(
                text if not text.endswith('.00') else text,
                format_amount(document['total_minor'], currency))

    def test_a_malformed_amount_is_refused(self):
        for bad in ('12,50', 'abc', '-5', '', '1e6'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    normalize(fields(bad))

    def test_a_negative_amount_cannot_be_expressed(self):
        with self.assertRaises(ValueError):
            normalize(fields(-1))

    def test_an_unknown_currency_is_refused(self):
        with self.assertRaises(ValueError):
            normalize(fields(currency='XYZ'))

    # ---------------------------------------------------- document integrity

    def test_line_items_must_sum_to_the_stated_total(self):
        with self.assertRaises(ValueError) as caught:
            normalize(fields(1000, line_items=[
                {'description': 'a', 'amount': 400},
                {'description': 'b', 'amount': 400}]))
        self.assertIn('disagrees with itself', str(caught.exception))

    def test_matching_line_items_are_accepted(self):
        document = normalize(fields(1000, line_items=[
            {'description': 'a', 'amount': 400},
            {'description': 'b', 'amount': 600}]))
        self.assertEqual(2, len(document['line_items']))

    def test_a_line_item_sum_mismatch_is_refused_across_currencies(self):
        with self.assertRaises(ValueError):
            normalize(fields('100.00', currency='USD', line_items=[
                {'description': 'a', 'amount': '99.99'}]))

    def test_a_quantity_line_totals_quantity_times_unit_price(self):
        """The defect the guard used to guarantee.

        ``quantity`` was validated and stored but the sum added ``amount`` alone,
        so 2 x 5000 with a stated total of 10000 was REFUSED as "disagrees with
        itself" while the same line with a stated total of 5000 was ACCEPTED --
        an arithmetically correct invoice rejected and an understated one passed,
        by the check whose whole job is catching the understatement. Both
        directions are asserted, because only the pair shows which way round the
        guard is.
        """
        document = normalize(fields(10000, line_items=[
            {'description': 'widget', 'quantity': 2, 'amount': 5000}]))
        self.assertEqual(10000, document['total_minor'])
        # The per-line total is reported, so an approver checking the arithmetic
        # does not have to perform the multiplication themselves.
        self.assertEqual(10000, document['line_items'][0]['line_total'])
        self.assertEqual(5000, document['line_items'][0]['amount'])
        with self.assertRaises(ValueError) as caught:
            normalize(fields(5000, line_items=[
                {'description': 'widget', 'quantity': 2, 'amount': 5000}]))
        self.assertIn('disagrees with itself', str(caught.exception))

    def test_quantities_accumulate_across_lines(self):
        document = normalize(fields(5000, line_items=[
            {'description': 'a', 'quantity': 2, 'amount': 1000},
            {'description': 'b', 'quantity': 3, 'amount': 1000}]))
        self.assertEqual(5000, document['total_minor'])

    def test_a_zero_quantity_contributes_nothing(self):
        document = normalize(fields(0, line_items=[
            {'description': 'a', 'quantity': 0, 'amount': 5000}]))
        self.assertEqual(0, document['total_minor'])
        self.assertEqual(0, document['line_items'][0]['line_total'])

    def test_an_absent_quantity_behaves_as_one(self):
        """Every existing caller omits quantity, so this pins the default down."""
        document = normalize(fields(1000, line_items=[
            {'description': 'a', 'amount': 400},
            {'description': 'b', 'amount': 600}]))
        self.assertEqual([1, 1], [i['quantity'] for i in document['line_items']])
        self.assertEqual(1000, document['total_minor'])

    def test_an_unknown_document_key_is_refused(self):
        broken = fields()
        broken['iban'] = 'x'
        with self.assertRaises(ValueError):
            normalize(broken)

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            normalize(fields(kind='receipt'))

    def test_an_ambiguous_date_is_refused(self):
        """10.01.2026 shifts a duplicate window by months if guessed."""
        with self.assertRaises(ValueError):
            normalize(fields(doc_date='10.01.2026'))

    def test_an_iso_date_is_accepted(self):
        self.assertEqual('2026-09-19', normalize(fields(doc_date='2026-09-19'))['doc_date'])

    def test_a_bank_account_is_normalized(self):
        document = normalize(fields(bank_account='1234 5678 9012'))
        self.assertEqual('123456789012', document['bank_account'])

    # ------------------------------------------------------------- dedup key

    def test_the_key_excludes_the_amount_on_purpose(self):
        """A same-number-different-amount document is MORE suspicious, not less."""
        first = normalize(fields(1000, number='INV-7'))
        second = normalize(fields(9999, number='INV-7'))
        self.assertEqual(document_key(first), document_key(second))

    def test_the_key_includes_the_supplier(self):
        left = normalize(fields(number='INV-7', supplier='acme'))
        right = normalize(fields(number='INV-7', supplier='other'))
        self.assertNotEqual(document_key(left), document_key(right))

    def test_the_key_includes_the_kind(self):
        left = normalize(fields(number='X-7', kind='invoice'))
        right = normalize(fields(number='X-7', kind='purchase_order'))
        self.assertNotEqual(document_key(left), document_key(right))

    def test_a_second_submission_is_a_duplicate_and_does_not_overwrite(self):
        first = remember(self.engine, TENANT, normalize(fields(1000, number='INV-1')))
        second = remember(self.engine, TENANT, normalize(fields(9999, number='INV-1')))
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        self.assertFalse(second['same_amount'])
        stored = load(self.engine, TENANT, first['id'])
        self.assertEqual(1000, stored['total_minor'])

    def test_an_exact_repeat_is_reported_as_same_amount(self):
        remember(self.engine, TENANT, normalize(fields(1000, number='INV-1')))
        again = remember(self.engine, TENANT, normalize(fields(1000, number='INV-1')))
        self.assertTrue(again['duplicate'])
        self.assertTrue(again['same_amount'])

    def test_duplicates_reports_the_altered_amount(self):
        remember(self.engine, TENANT, normalize(fields(1000, number='INV-1')))
        report = duplicates(self.engine, TENANT, AGENT, fields(1500, number='INV-1'))
        self.assertTrue(report['duplicate'])
        self.assertTrue(report['altered'])
        self.assertEqual({'from': '1000', 'to': '1500'}, report['amount_changed'])

    def test_a_currency_change_is_not_reported_as_an_amount_change(self):
        """The defect: the same number in two currencies read as a changed amount.

        Comparing ``total_minor`` alongside the currency means 1000 UZS and 1000
        USD are correctly *not* the same amount, but the old report then rendered
        them into one ``from``/``to`` pair formatted in two different symbols --
        ``from: '1000', to: '1000.00'`` -- which tells an approver to look for a
        price difference that does not exist. The amount is unchanged; the
        currency is what moved, and that is now the key that says so.
        """
        remember(self.engine, TENANT, normalize(fields(1000, number='INV-1')))
        report = duplicates(self.engine, TENANT, AGENT,
                            fields(1000, number='INV-1', currency='USD'))
        self.assertTrue(report['duplicate'])
        self.assertTrue(report['altered'])
        # Not the same amount -- 1000 UZS is not 1000 USD -- but the number held.
        self.assertFalse(report['same_amount'])
        self.assertFalse(report['same_currency'])
        self.assertEqual({'from': 'UZS', 'to': 'USD'}, report['currency_changed'])
        self.assertEqual('1000', report['amount_changed']['from'])
        self.assertEqual('1000', report['amount_changed']['to'])
        # A delta across currencies would be an invented number, so there is none.
        self.assertIsNone(report['amount_delta_minor'])

    def test_an_amount_change_carries_its_delta(self):
        remember(self.engine, TENANT, normalize(fields(1000, number='INV-1')))
        report = duplicates(self.engine, TENANT, AGENT, fields(1500, number='INV-1'))
        self.assertIsNone(report['currency_changed'])
        self.assertEqual(500, report['amount_delta_minor'])
        self.assertTrue(report['same_currency'])

    def test_an_exact_repeat_moves_nothing(self):
        remember(self.engine, TENANT, normalize(fields(1000, number='INV-1')))
        report = duplicates(self.engine, TENANT, AGENT, fields(1000, number='INV-1'))
        self.assertFalse(report['altered'])
        self.assertTrue(report['same_amount'])
        self.assertIsNone(report['amount_changed'])
        self.assertIsNone(report['currency_changed'])
        self.assertEqual(0, report['amount_delta_minor'])

    def test_duplicates_reports_nothing_for_an_unknown_document(self):
        report = duplicates(self.engine, TENANT, AGENT, fields(number='INV-404'))
        self.assertFalse(report['duplicate'])
        self.assertIsNone(report['original'])
        self.assertFalse(report['altered'])

    def test_history_is_scoped_to_the_supplier(self):
        remember(self.engine, TENANT, normalize(fields(number='INV-1', supplier='acme')))
        remember(self.engine, TENANT, normalize(fields(number='INV-1', supplier='other')))
        self.assertEqual(1, len(supplier_history(self.engine, TENANT, 'acme')))

    # ------------------------------------------------------- three-way match

    def test_three_identical_documents_match(self):
        report = match(self.engine, TENANT, AGENT, fields(1000), fields(1000), fields(1000))
        self.assertEqual('matched', report['status'])
        self.assertTrue(report['complete'])

    def test_a_missing_counterpart_is_incomplete_not_a_mismatch(self):
        """Absent evidence and contradicting evidence are different facts."""
        report = match(self.engine, TENANT, AGENT, fields(1000), None, None)
        self.assertEqual('incomplete', report['status'])
        self.assertFalse(report['complete'])
        self.assertEqual(['purchase_order', 'delivery_note'], report['missing'])

    def test_one_missing_counterpart_with_the_other_agreeing_is_partial(self):
        report = match(self.engine, TENANT, AGENT, fields(1000), None, fields(1000))
        self.assertEqual('partial', report['status'])
        self.assertEqual(['purchase_order'], report['missing'])

    def test_one_missing_counterpart_with_the_other_disagreeing_is_a_mismatch(self):
        report = match(self.engine, TENANT, AGENT, fields(1000), None, fields(900))
        self.assertEqual('mismatch', report['status'])

    def test_a_disagreement_is_a_mismatch(self):
        report = match(self.engine, TENANT, AGENT, fields(1000), fields(900), fields(1000))
        self.assertEqual('mismatch', report['status'])

    def test_no_tolerance_means_exact_agreement(self):
        self.assertEqual(DEFAULT_TOLERANCE_MINOR, 0)
        report = match(self.engine, TENANT, AGENT, fields(1000), fields(1001), fields(1000))
        self.assertEqual('mismatch', report['status'])

    def test_a_declared_tolerance_absorbs_a_small_difference(self):
        report = match(self.engine, TENANT, AGENT, fields(1000), fields(1001),
                       fields(1000), tolerance_minor=5)
        self.assertEqual('matched', report['status'])

    def test_the_tolerance_is_symmetric(self):
        """An invoice below the PO is as notable as one above it."""
        note = dict(fields(1000, kind='delivery_note'), number='DN-1')
        below = match(self.engine, TENANT, AGENT, fields(1000),
                      fields(1001, kind='purchase_order'), note, tolerance_minor=5)
        above = match(self.engine, TENANT, AGENT, fields(1000),
                      fields(999, kind='purchase_order'), note, tolerance_minor=5)
        self.assertEqual('matched', below['status'])
        self.assertEqual('matched', above['status'])

    def test_the_direction_of_the_difference_is_named(self):
        report = match(self.engine, TENANT, AGENT, fields(1000), fields(900), None)
        self.assertEqual('invoice above', report['checks'][0]['reason'])

    def test_currencies_are_never_compared_without_a_rate(self):
        report = match(self.engine, TENANT, AGENT, fields(1000),
                       fields(1000, currency='USD'), None)
        self.assertEqual('currency', report['checks'][0]['reason'])
        self.assertEqual('mismatch', report['status'])

    def test_a_negative_tolerance_is_refused(self):
        with self.assertRaises(ValueError):
            match(self.engine, TENANT, AGENT, fields(1000), fields(1000), None,
                  tolerance_minor=-1)

    def test_the_match_reports_money_as_strings_not_floats(self):
        report = match(self.engine, TENANT, AGENT, fields('1234.56', currency='USD'),
                       None, None)
        self.assertEqual('1234.56', report['invoice_total'])
        self.assertIsInstance(report['invoice_total'], str)

    # -------------------------------------------------------- fraud controls

    def test_no_history_is_a_signal_not_an_error(self):
        report = fraud_signals(self.engine, TENANT, AGENT, fields(number='NEW-1'))
        self.assertIn('no_history', [s['signal'] for s in report['signals']])
        self.assertIn('no_history', report['weak'])

    def test_history_removes_the_no_history_signal(self):
        remember(self.engine, TENANT, normalize(fields(1000, number='INV-1')))
        report = fraud_signals(self.engine, TENANT, AGENT, fields(1000, number='INV-2'))
        self.assertNotIn('no_history', [s['signal'] for s in report['signals']])

    def test_an_outlier_amount_is_a_material_signal(self):
        for amount in (1000000, 1100000, 1200000):
            remember(self.engine, TENANT,
                     normalize(fields(amount, number=f'INV-{amount}')))
        report = fraud_signals(self.engine, TENANT, AGENT, fields(9000000, number='INV-X'))
        self.assertIn('amount_outlier', report['material'])
        self.assertNotIn('amount_outlier', report['weak'])

    def test_an_outlier_signal_carries_its_evidence(self):
        for amount in (1000000, 1100000, 1200000):
            remember(self.engine, TENANT,
                     normalize(fields(amount, number=f'INV-{amount}')))
        report = fraud_signals(self.engine, TENANT, AGENT, fields(9000000, number='INV-X'))
        signal = next(s for s in report['signals'] if s['signal'] == 'amount_outlier')
        # Evidence is rendered for a human to check, so the median ships as text.
        self.assertEqual('1100000', signal['evidence']['median'])
        self.assertEqual(3, signal['evidence']['count'])

    def test_the_evidence_median_is_the_median_not_the_upper_middle(self):
        # An EVEN count is where `amounts[len // 2]` and a median diverge. Three
        # documents are not enough to see the difference, which is why the fixture
        # above stayed green while the value it asserted was reached by accident.
        for amount in (1000000, 1100000, 1200000, 1300000):
            remember(self.engine, TENANT,
                     normalize(fields(amount, number=f'INV-{amount}')))
        report = fraud_signals(self.engine, TENANT, AGENT, fields(9000000, number='INV-X'))
        signal = next(s for s in report['signals'] if s['signal'] == 'amount_outlier')
        # True median of [1000000, 1100000, 1200000, 1300000] is 1150000.
        # The upper-middle rule printed 1200000 and said it was the median.
        self.assertEqual('1150000', signal['evidence']['median'])
        self.assertEqual(4, signal['evidence']['count'])

    def test_the_document_under_test_is_not_in_its_own_comparison_population(self):
        # Documents flow receive -> store -> check, so by the time the outlier test
        # runs the document is normally already stored. It must not be counted as
        # its own prior document: an outlier that raises its own threshold weakens
        # the signal exactly where it matters most.
        for amount in (1000000, 1100000, 1200000):
            remember(self.engine, TENANT,
                     normalize(fields(amount, number=f'INV-{amount}')))
        remember(self.engine, TENANT,
                 normalize(fields(9000000, number='INV-SELF')))
        report = fraud_signals(self.engine, TENANT, AGENT,
                               fields(9000000, number='INV-SELF'))
        signal = next(s for s in report['signals'] if s['signal'] == 'amount_outlier')
        self.assertEqual(3, signal['evidence']['count'])
        self.assertEqual('1100000', signal['evidence']['median'])
        # The store did return four rows; one of them was this document.
        self.assertEqual(4, report['history_rows'])
        self.assertEqual(3, report['history_considered'])

    def test_a_supplier_history_of_only_the_document_itself_is_no_history(self):
        # The boundary the self-exclusion creates: if the only row the store holds
        # is the document under test, there is nothing to compare against and the
        # honest report says so rather than comparing the document with itself.
        remember(self.engine, TENANT,
                 normalize(fields(1250000, number='INV-ONLY')))
        report = fraud_signals(self.engine, TENANT, AGENT,
                               fields(1250000, number='INV-ONLY'))
        self.assertIn('no_history', [s['signal'] for s in report['signals']])
        self.assertEqual(0, report['history_considered'])
        self.assertNotIn('amount_outlier', [s['signal'] for s in report['signals']])

    def test_the_outlier_boundary_is_measured_from_both_sides(self):
        # True median 1100000 at outlier_ratio 3 puts the boundary at 3300000.
        # Both sides of it are asserted, because a test that only ever checks the
        # flagged side cannot tell a working threshold from a threshold set low.
        for amount in (1000000, 1100000, 1200000):
            remember(self.engine, TENANT,
                     normalize(fields(amount, number=f'INV-{amount}')))
        at_boundary = fraud_signals(self.engine, TENANT, AGENT,
                                    fields(3300000, number='INV-AT'))
        self.assertIn('amount_outlier',
                      [s['signal'] for s in at_boundary['signals']])
        below = fraud_signals(self.engine, TENANT, AGENT,
                              fields(3299999, number='INV-BELOW'))
        self.assertNotIn('amount_outlier',
                         [s['signal'] for s in below['signals']])

    def test_an_upper_middle_median_would_miss_an_invoice_this_test_catches(self):
        # The regression guard for the defect itself. With a two-document history
        # of 100000 and 300000 the true median is 200000, so at 3x anything from
        # 600000 is an outlier. The upper-middle rule read 300000 and set the bar
        # at 900000, so an 800000 invoice went unflagged. It must not.
        remember(self.engine, TENANT, normalize(fields(100000, number='INV-A')))
        remember(self.engine, TENANT, normalize(fields(300000, number='INV-B')))
        report = fraud_signals(self.engine, TENANT, AGENT,
                               fields(800000, number='INV-C'))
        signal = next(s for s in report['signals'] if s['signal'] == 'amount_outlier')
        self.assertEqual('200000', signal['evidence']['median'])
        self.assertEqual(2, signal['evidence']['count'])

    def test_an_outlier_is_not_raised_for_an_ordinary_amount(self):
        for amount in (1000000, 1100000, 1200000):
            remember(self.engine, TENANT,
                     normalize(fields(amount, number=f'INV-{amount}')))
        report = fraud_signals(self.engine, TENANT, AGENT, fields(1250000, number='INV-Y'))
        self.assertNotIn('amount_outlier', [s['signal'] for s in report['signals']])

    def test_a_round_number_is_a_weak_signal_not_a_material_one(self):
        report = fraud_signals(self.engine, TENANT, AGENT, fields(1000000, number='INV-R'))
        self.assertIn('round_number', report['weak'])
        self.assertNotIn('round_number', report['material'])

    def _roundness(self, total, currency='UZS', number='INV-RND'):
        report = fraud_signals(self.engine, TENANT, AGENT,
                               fields(total, currency=currency, number=number))
        signal = next((s for s in report['signals']
                       if s['signal'] == 'round_number'), None)
        return signal['evidence']['trailing_zeros'] if signal else None

    def test_roundness_is_measured_in_the_major_unit_for_every_currency(self):
        """The signal used to be dead for every currency but UZS.

        The old test asked ``total_minor % 10 ** MINOR_UNITS[currency] == 0``,
        which for USD (factor 100) only asked "are the cents zero" -- true of
        almost every invoice -- so a perfectly round USD 1000.00 went unflagged
        while a non-round UZS 100050 was flagged. Both directions are asserted
        for both currencies, because a one-sided check cannot tell a working
        predicate from one that never fires.
        """
        # Round, in the major unit: USD 1000.00 is round to three zeros, and the
        # same numeric amount in UZS is one thousand so'm, also three.
        self.assertEqual(3, self._roundness('1000.00', 'USD', number='INV-U1'))
        self.assertEqual(3, self._roundness('1000.00', 'UZS', number='INV-U2'))
        # Not round, in the major unit: the cents are not zero, and 1234 is not
        # a multiple of a thousand.
        self.assertIsNone(self._roundness('1000.50', 'USD', number='INV-U3'))
        self.assertIsNone(self._roundness(1234, 'UZS', number='INV-U4'))

    def test_a_barely_round_amount_is_not_flagged(self):
        """One trailing zero is ordinary, and flagging it would train the click-through.

        In UZS the smallest unit is one so'm, so 123450 and 123460 are ordinary
        prices. A signal that fires on them is a signal the approver learns to
        dismiss, which costs more than the signal is worth. Three zeros is the
        declared threshold; both sides of it are asserted.
        """
        self.assertIsNone(self._roundness(100050, 'UZS', number='INV-B1'))
        self.assertIsNone(self._roundness(123450, 'UZS', number='INV-B2'))
        self.assertEqual(3, self._roundness(123000, 'UZS', number='INV-B3'))

    def test_the_roundness_figure_is_trailing_zeros_not_significant_digits(self):
        """The number the approver reads has to match the sentence describing it.

        The old figure was ``len(str(major).rstrip('0'))``, the count of leading
        significant digits, rendered as a roundness claim. It gave UZS 100050 a
        value of 5 -- "very round" -- when the amount is barely round at all, and
        gave UZS 1000000 a value of 1, the same as the least round amount there
        is. The roundest amount must now have the largest figure.
        """
        self.assertEqual(6, self._roundness(1000000, number='INV-D1'))
        self.assertEqual(5, self._roundness(100000, number='INV-D2'))
        self.assertEqual(3, self._roundness(123000, number='INV-D3'))

    def test_a_changed_bank_account_is_a_material_signal(self):
        remember(self.engine, TENANT,
                 normalize(fields(1000, number='INV-1', bank_account='1111111111')))
        report = fraud_signals(self.engine, TENANT, AGENT,
                               fields(1000, number='INV-2', bank_account='2222222222'))
        self.assertIn('bank_account_changed', report['material'])

    def test_an_unchanged_bank_account_raises_nothing(self):
        remember(self.engine, TENANT,
                 normalize(fields(1000, number='INV-1', bank_account='1111111111')))
        report = fraud_signals(self.engine, TENANT, AGENT,
                               fields(1000, number='INV-2', bank_account='1111111111'))
        self.assertNotIn('bank_account_changed', [s['signal'] for s in report['signals']])

    def test_an_altered_duplicate_is_a_material_signal(self):
        remember(self.engine, TENANT, normalize(fields(1000, number='INV-1')))
        report = fraud_signals(self.engine, TENANT, AGENT, fields(1500, number='INV-1'))
        self.assertIn('duplicate_altered', report['material'])

    def test_a_weekend_date_only_signals_against_the_suppliers_own_pattern(self):
        """For a supplier that always issues on a Sunday it is ordinary."""
        remember(self.engine, TENANT,
                 normalize(fields(1000, number='INV-1', doc_date='2026-09-13')))
        report = fraud_signals(self.engine, TENANT, AGENT,
                               fields(1000, number='INV-2', doc_date='2026-09-19'))
        self.assertNotIn('weekend_date', [s['signal'] for s in report['signals']])

    def test_a_weekend_date_deviating_from_history_signals(self):
        remember(self.engine, TENANT,
                 normalize(fields(1000, number='INV-1', doc_date='2026-09-18')))
        report = fraud_signals(self.engine, TENANT, AGENT,
                               fields(1000, number='INV-2', doc_date='2026-09-19'))
        self.assertIn('weekend_date', report['weak'])

    def test_no_signal_is_ever_a_verdict(self):
        """The vocabulary is observations, never an accusation."""
        for signal in MATERIAL_SIGNALS | WEAK_SIGNALS:
            for banned in ('fraud', 'blocked', 'reject', 'guilty', 'deny'):
                self.assertNotIn(banned, signal, signal)

    def test_an_invalid_outlier_ratio_is_refused(self):
        with self.assertRaises(ValueError):
            fraud_signals(self.engine, TENANT, AGENT, fields(), outlier_ratio=0)

    # --------------------------------------------------------- posting plan

    def test_a_clean_matched_document_is_ready_for_approval(self):
        plan = posting_plan(self.engine, TENANT, AGENT, fields(1000, number='INV-C'),
                            purchase_order=dict(fields(1000, kind='purchase_order'), number='PO-1'),
                            delivery_note=dict(fields(1000, kind='delivery_note'), number='DN-1'))
        self.assertEqual('ready_for_approval', plan['recommendation'])
        self.assertFalse(plan['needs_attention'])

    def test_the_plan_always_says_it_does_not_pay(self):
        plan = posting_plan(self.engine, TENANT, AGENT, fields(number='INV-P'))
        self.assertIs(False, plan['executes_payment'])
        self.assertIn('does not move money', plan['note'])

    def test_a_duplicate_holds_the_plan(self):
        remember(self.engine, TENANT, normalize(fields(1000, number='INV-D')))
        plan = posting_plan(self.engine, TENANT, AGENT, fields(1000, number='INV-D'))
        self.assertEqual('hold_for_duplicate_review', plan['recommendation'])
        self.assertTrue(plan['needs_attention'])

    def test_a_mismatch_holds_the_plan(self):
        plan = posting_plan(self.engine, TENANT, AGENT, fields(1000, number='INV-M'),
                            purchase_order=dict(fields(900, kind='purchase_order'), number='PO-1'),
                            delivery_note=dict(fields(1000, kind='delivery_note'), number='DN-1'))
        self.assertEqual('hold_for_mismatch_review', plan['recommendation'])

    def test_a_missing_counterpart_asks_for_the_document(self):
        plan = posting_plan(self.engine, TENANT, AGENT, fields(1000, number='INV-A'))
        self.assertEqual('attach_missing_document', plan['recommendation'])

    def test_a_round_number_does_not_hold_a_clean_plan(self):
        """Most real invoices are round; gating on that would train reflexive approval."""
        plan = posting_plan(self.engine, TENANT, AGENT, fields(1000000, number='INV-R'),
                            purchase_order=dict(fields(1000000, kind='purchase_order'), number='PO-1'),
                            delivery_note=dict(fields(1000000, kind='delivery_note'), number='DN-1'))
        self.assertEqual('ready_for_approval', plan['recommendation'])
        self.assertIn('round_number', plan['fraud']['weak'])

    def test_the_outlier_threshold_decides_the_recommendation_at_the_right_place(self):
        """The median defect did not stop at printing a wrong number.

        A material signal replaces `ready_for_approval` with
        `review_fraud_signals`, so an inflated median silently sent genuinely
        outlying invoices straight to approval. With a two-document history of
        100000 and 300000 the true median is 200000 and the 3x boundary is
        600000; the upper-middle rule read 300000 and put the boundary at
        900000, so everything from 600000 to 899999 skipped the review.

        Both sides are asserted, because only the pair distinguishes a working
        threshold from one that never fires.
        """
        for amount in (100000, 300000):
            remember(self.engine, TENANT,
                     normalize(fields(amount, number=f'INV-{amount}')))
        po = dict(fields(600000, kind='purchase_order'), number='PO-1')
        dn = dict(fields(600000, kind='delivery_note'), number='DN-1')
        at_boundary = posting_plan(self.engine, TENANT, AGENT,
                                   fields(600000, number='INV-AT'),
                                   purchase_order=po, delivery_note=dn)
        self.assertEqual('review_fraud_signals', at_boundary['recommendation'])
        self.assertTrue(at_boundary['needs_attention'])
        below = posting_plan(self.engine, TENANT, AGENT,
                             fields(599999, number='INV-BELOW'),
                             purchase_order=dict(fields(599999,
                                                        kind='purchase_order'),
                                                 number='PO-2'),
                             delivery_note=dict(fields(599999,
                                                       kind='delivery_note'),
                                                number='DN-2'))
        self.assertEqual('ready_for_approval', below['recommendation'])

    def test_recording_a_plan_needs_an_active_tenant(self):
        self.engine.freeze(TENANT, True, 'test')
        registry = build_registry()
        with self.assertRaises((Forbidden, Conflict)):
            registry.get('document.posting_plan').handler(
                self.engine, TENANT, AGENT, {'fields': fields(number='INV-F')}, 's1')

    def test_a_plan_is_recorded_and_listed(self):
        registry = build_registry()
        out = registry.get('document.posting_plan').handler(
            self.engine, TENANT, AGENT, {'fields': fields(number='INV-REC')}, 'step-1')
        self.assertIs(False, out['executes_payment'])
        listed = plans(self.engine, TENANT, out['document_id'])
        self.assertEqual(1, len(listed))
        self.assertEqual('prepared', listed[0]['status'])

    def test_an_unknown_document_id_is_not_found(self):
        with self.assertRaises(NotFound):
            load(self.engine, TENANT, 'nope')

    # --------------------------------------------------- no payment path

    def test_the_module_has_no_function_that_moves_money(self):
        """Structurally asserted on the module's own callables, not on prose."""
        import inspect
        import platform_runtime.documents as module
        callables = {name for name, value in vars(module).items()
                     if not name.startswith('_')
                     and (inspect.isfunction(value) or inspect.isclass(value))
                     and getattr(value, '__module__', '') == module.__name__}
        for banned in ('pay', 'payment', 'transfer', 'settle', 'remit', 'refund',
                       'disburse', 'charge', 'execute'):
            offenders = [name for name in callables if banned in name.lower()]
            self.assertFalse(offenders, f'unexpected surface matching {banned}: {offenders}')

    def test_no_tool_can_pay(self):
        names = ' '.join(build_registry().items)
        for banned in ('pay', 'transfer', 'settle', 'remit', 'refund'):
            self.assertNotIn(banned, names, banned)

    def test_no_tool_accepts_a_raw_destination_account(self):
        """An IBAN argument is how a payment gets redirected."""
        for name in DOCUMENT_TOOLS:
            props = set(build_registry().get(name).schema['properties'])
            for banned in ('iban', 'account', 'beneficiary', 'swift', 'card',
                           'destination'):
                self.assertNotIn(banned, props, f'{name} accepts {banned!r}')


class RegistrationTests(unittest.TestCase):
    def test_every_document_tool_is_registered(self):
        registry = build_registry()
        for name in DOCUMENT_TOOLS:
            self.assertIn(name, registry.items, name)

    def test_the_reads_are_read_and_the_writes_are_write(self):
        registry = build_registry()
        expected = {'document.match': 'read', 'document.duplicates': 'read',
                    'document.fraud_signals': 'read', 'document.parse': 'write',
                    'document.posting_plan': 'write'}
        for name, risk in expected.items():
            self.assertEqual(risk, registry.get(name).risk, name)

    def test_every_write_requires_an_approval(self):
        """The engine gates every non-read, so a write here is an approval."""
        registry = build_registry()
        for name in DOCUMENT_TOOLS:
            spec = registry.get(name)
            if spec.risk != 'read':
                self.assertEqual('write', spec.risk, name)

    def test_module_exposes_exactly_five_tools(self):
        self.assertEqual(5, len(DOCUMENT_TOOLS))

    # ------------------------------------------------------- reachable schema

    def test_every_tool_accepts_a_well_formed_call(self):
        """The defect this guards against: tools that validate nothing.

        An earlier revision declared ``fields`` as a bare ``{'type': 'object'}``
        property. The registry validator refuses such a property outright — with no
        ``properties`` to check against, every dict fails with "Schema fields
        mismatch" — so all five tools rejected every call at the engine boundary.
        The tools existed, the capability packs referenced them, and none of them
        could be submitted. A registration test cannot catch that: the tool is
        registered, it simply cannot be used. So the assertion here is that a
        realistic call *passes validation*.
        """
        registry = build_registry()
        invoice = json.dumps(fields(number='INV-V'))
        calls = {
            'document.parse': {'fields': invoice},
            'document.match': {'invoice': invoice},
            'document.duplicates': {'fields': invoice},
            'document.fraud_signals': {'fields': invoice},
            'document.posting_plan': {'fields': invoice},
        }
        for name, args in calls.items():
            with self.subTest(tool=name):
                registry.get(name).validate(args)

    def test_a_nested_object_schema_would_reject_every_document(self):
        """The exact shape that broke, asserted against the validator itself.

        Stated as a test rather than as a comment because the module must not drift
        back to it, and because the failure is invisible in a registration check.
        """
        from platform_runtime.tools import obj, validate_schema
        broken = obj({'fields': {'type': 'object'}})
        with self.assertRaises(ValueError) as caught:
            validate_schema({'fields': fields()}, broken)
        self.assertEqual('Schema fields mismatch', str(caught.exception))

    def test_structured_arguments_travel_as_json_text(self):
        registry = build_registry()
        for name in DOCUMENT_TOOLS:
            schema = registry.get(name).schema
            for prop in ('fields', 'invoice', 'purchase_order', 'delivery_note'):
                if prop in schema['properties']:
                    self.assertEqual('string', schema['properties'][prop]['type'],
                                     f'{name}.{prop}')

    def test_a_malformed_json_payload_is_refused_cleanly(self):
        """A refusal naming the argument, not a TypeError from deep inside."""
        registry = build_registry()
        handler = registry.get('document.duplicates').handler
        with self.assertRaises(ValueError) as caught:
            handler(None, TENANT, AGENT, {'fields': '{not json'}, 's1')
        self.assertIn('fields', str(caught.exception))
        self.assertIn('JSON', str(caught.exception))

    def test_a_payload_that_is_not_an_object_is_refused(self):
        registry = build_registry()
        handler = registry.get('document.duplicates').handler
        with self.assertRaises(ValueError):
            handler(None, TENANT, AGENT, {'fields': '["not", "an", "object"]'}, 's1')


class PayloadTests(unittest.TestCase):
    """The decoder between the tool schema and the control chain."""

    def test_a_json_string_is_decoded(self):
        from platform_runtime.documents import _payload
        self.assertEqual({'kind': 'invoice'},
                         _payload({'fields': '{"kind": "invoice"}'}, 'fields'))

    def test_an_object_is_accepted_unchanged(self):
        """Tolerated so a caller holding the dict can pass it straight through."""
        from platform_runtime.documents import _payload
        value = {'kind': 'invoice'}
        self.assertIs(value, _payload({'fields': value}, 'fields'))

    def test_an_absent_required_payload_is_refused(self):
        from platform_runtime.documents import _payload
        with self.assertRaises(ValueError):
            _payload({}, 'fields', required=True)

    def test_an_absent_optional_payload_is_none(self):
        from platform_runtime.documents import _payload
        self.assertIsNone(_payload({}, 'purchase_order'))

    def test_a_non_string_non_object_is_refused(self):
        from platform_runtime.documents import _payload
        for value in (7, 1.5, True, ['a']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _payload({'fields': value}, 'fields')

    def test_the_chain_runs_end_to_end_through_the_real_engine(self):
        """The proof the schema works: submit, and the engine accepts it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = Engine(root / 'ap.db', build_registry(),
                            lambda t, a: dict(POLICY), clock=Clock())
            steps = [{'tool': 'document.posting_plan',
                      'args': {'fields': json.dumps(fields(number='INV-E2E'))}}]
            tid = engine.submit(TENANT, 'web', 'e2e-1', AGENT, steps, 'owner')
            task = engine.get(TENANT, tid)
            self.assertEqual(1, len(task['steps']))
            step = task['steps'][0]
            # The point of this test is that the step was ACCEPTED. Before the fix
            # the engine boundary rejected the call outright with "Schema fields
            # mismatch" and no task row survived, so reaching this line at all is
            # the regression check.
            self.assertEqual('document.posting_plan', step['tool'])
            # And the write is gated: the engine recorded that an approval is
            # needed, which is what makes a plan something a human agreed to.
            self.assertEqual(1, step['approval_needed'])
            self.assertEqual('pending', step['approval_status'])


class ConfigTests(unittest.TestCase):
    """Operator settings. Defaults are safe; a present-but-wrong block is refused."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.cfg = self.root / 'integrations.json'
        import os
        self.env = unittest.mock.patch.dict(
            os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(self.cfg)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.write({})

    def write(self, block, tenant=TENANT):
        payload = {tenant: {'documents': block}} if block is not None else {tenant: {}}
        self.cfg.write_text(json.dumps(payload), encoding='utf-8')

    def test_defaults_are_exact_and_conservative(self):
        settings = documents_config(TENANT)
        self.assertEqual(0, settings['tolerance_minor'])
        self.assertEqual('owner', settings['approver_role'])
        self.assertEqual('uz', settings['residency'])

    def test_an_absent_file_falls_back_to_defaults(self):
        import os
        self.cfg.unlink()
        self.assertEqual(0, documents_config(TENANT)['tolerance_minor'])

    def test_a_declared_tolerance_is_used(self):
        self.write({'tolerance_minor': 50000})
        self.assertEqual(50000, documents_config(TENANT)['tolerance_minor'])

    def test_a_malformed_block_is_refused_not_defaulted(self):
        """A typo in a threshold must not silently become the default."""
        self.write({'tolerance_minor': -1})
        with self.assertRaises(ValueError):
            documents_config(TENANT)

    def test_an_unknown_key_is_refused(self):
        self.write({'auto_pay': True})
        with self.assertRaises(ValueError):
            documents_config(TENANT)

    def test_an_unsupported_currency_is_refused(self):
        self.write({'currencies': ['UZS', 'XYZ']})
        with self.assertRaises(ValueError):
            documents_config(TENANT)

    def test_an_unknown_residency_is_refused(self):
        self.write({'residency': 'eu'})
        with self.assertRaises(ValueError):
            documents_config(TENANT)

    def test_an_unknown_approver_role_is_refused(self):
        self.write({'approver_role': 'agent'})
        with self.assertRaises(ValueError):
            documents_config(TENANT)


class BoundaryTests(unittest.TestCase):
    """The exact caps, pinned by VALUE.

    The rest of the suite asserts behaviour; a bound that is read back out of the
    module moves with the mutation that widens it, so these pin the literal and walk
    both sides of the boundary wherever the accepting side is reachable.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.cfg = self.root / 'integrations.json'
        import os
        self.env = unittest.mock.patch.dict(
            os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(self.cfg)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cfg.write_text(json.dumps({TENANT: {}}), encoding='utf-8')

    def write(self, block):
        self.cfg.write_text(json.dumps({TENANT: {'documents': block}}),
                            encoding='utf-8')

    # ------------------------------------------------------------------ the caps

    def test_the_amount_ceiling_is_ten_to_the_fifteenth(self):
        self.assertEqual(10 ** 15, D.MAX_AMOUNT)
        self.assertEqual(10 ** 15, D._amount_minor(10 ** 15, 'UZS'))
        with self.assertRaises(ValueError):
            D._amount_minor(10 ** 15 + 1, 'UZS')
        # The ceiling scales with the currency: an INT is MAJOR units.
        self.assertEqual(10 ** 17, D._amount_minor(10 ** 15, 'USD'))

    def test_the_line_item_cap_is_two_hundred(self):
        self.assertEqual(200, D.MAX_LINE_ITEMS)
        self.assertEqual(200, len(D._line_items([{'amount': 0}] * 200, 'UZS')))
        with self.assertRaises(ValueError):
            D._line_items([{'amount': 0}] * 201, 'UZS')

    def test_the_text_cap_is_two_hundred(self):
        self.assertEqual(200, D.MAX_TEXT_CHARS)
        item = D._line_items([{'description': 'd' * 200}], 'UZS')[0]
        self.assertEqual(200, len(item['description']))
        with self.assertRaises(ValueError):
            D._line_items([{'description': 'd' * 201}], 'UZS')

    def test_the_identifier_caps_are_sixty_four(self):
        self.assertEqual(64, len(D._number('A' * 64)))
        self.assertEqual(64, len(D._party('a' * 64)))
        for fn, value in ((D._number, 'A' * 65), (D._party, 'a' * 65)):
            with self.subTest(fn=fn.__name__), self.assertRaises(ValueError):
                fn(value)

    def test_the_amount_string_bounds(self):
        # The SHAPE bound is one digit STRICTER than the RANGE bound: fifteen whole
        # digits excludes 10**15 itself, which the range check would have allowed.
        # So the discriminating input is exactly 10**15 -- a 16-nines overflow is
        # refused by the range check either way and proves nothing about the shape.
        with self.assertRaises(ValueError):
            D._amount_minor('1000000000000000', 'UZS')
        # ...while the INT path does accept 10**15. That asymmetry is deliberate:
        # an operator's decimal string cannot express the ceiling, an integer can.
        self.assertEqual(10 ** 15, D._amount_minor(10 ** 15, 'UZS'))
        self.assertEqual(10 ** 15 - 1, D._amount_minor('9' * 15, 'UZS'))
        with self.assertRaises(ValueError):
            D._amount_minor('9' * 16, 'UZS')
        # Six fraction digits are allowed and TRUNCATED to the currency's precision.
        self.assertEqual(150, D._amount_minor('1.509999', 'USD'))
        with self.assertRaises(ValueError):
            D._amount_minor('1.1234567', 'USD')

    def test_a_negative_amount_is_refused(self):
        for value in (-1, -10 ** 6):
            with self.subTest(value=value), self.assertRaises(ValueError):
                D._amount_minor(value, 'UZS')

    # ---------------------------------------------------- the bank-account guard

    def test_the_bank_account_bound_is_on_the_digits_not_the_spaces(self):
        # The message promises "4 to 34 digits". The regex is applied to the SPACED
        # string, so bounding only that let `'    '` through, which stripped to `''`
        # and made `if doc['bank_account']` falsy -- silently SKIPPING the
        # `bank_account_changed` MATERIAL signal, whose whole purpose is catching a
        # supplier-account substitution. Measured before the fix.
        self.assertEqual('1234',
                         normalize(fields(bank_account='1234'))['bank_account'])
        self.assertEqual('123456789012',
                         normalize(fields(bank_account='1234 5678 9012'))['bank_account'])
        for value in ('    ', '1   ', '12  ', '123', '1 2 3'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize(fields(bank_account=value))
        self.assertEqual(34, len(normalize(fields(bank_account='1' * 34))['bank_account']))
        with self.assertRaises(ValueError):
            normalize(fields(bank_account='1' * 35))

    def test_an_emptied_bank_account_cannot_disable_the_signal(self):
        # The signal is gated on `if doc['bank_account']`, so a value that NORMALISED
        # to empty removed the control instead of failing it.
        with self.assertRaises(ValueError):
            normalize(fields(bank_account='     '))
        # A real account still raises the signal, so the control is intact.
        document = normalize(fields(bank_account='1111111111'))
        self.assertTrue(document['bank_account'])

    # ------------------------------------------------------------------ the date

    def test_the_date_guard_checks_the_calendar_not_only_the_shape(self):
        # The shape check is not a date check. An impossible date was stored, and
        # `date()` then raised on it INSIDE `fraud_signals`, where the exception is
        # caught -- so the document silently carried no weekday signal and nothing
        # said why.
        for value in ('2026-13-45', '2026-02-30', '2026-00-00', '2026-02-29',
                      '2026-04-31', '2026-06-31', '0000-01-01'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                D._date(value)
        for value in ('2026-09-18', '2024-02-29', '2026-12-31', '2026-01-01'):
            with self.subTest(value=value):
                self.assertEqual(value, D._date(value))
        self.assertEqual('', D._date(''))

    # ---------------------------------------------------------------- the median

    def test_the_median_is_exact_above_two_to_the_fifty_third(self):
        # `statistics.median` divides for an even count, returning a float, and floats
        # hold consecutive integers exactly only up to 2**53 -- while MAX_AMOUNT
        # allows a USD total of 10**17 minor units. The value is PRINTED into the
        # approver's evidence as "the median", and the module's own docstring says a
        # mislabelled median is a lie.
        self.assertEqual(2 ** 53 + 1, D._median([2 ** 53, 2 ** 53 + 2]))
        self.assertEqual(10 ** 16 + 1, D._median([10 ** 16, 10 ** 16 + 2]))
        self.assertEqual(10 ** 17 + 1, D._median([10 ** 17, 10 ** 17 + 2]))
        # Odd counts were always exact; keep them so.
        self.assertEqual(10 ** 17 + 1,
                         D._median([10 ** 17, 10 ** 17 + 1, 10 ** 17 + 5]))
        # Unsorted input, because the function promises the median of a list.
        self.assertEqual(1500, D._median([2000, 1000]))

    def test_the_median_does_not_route_money_through_a_float(self):
        source = inspect.getsource(D._median)
        # NOT `'statistics' not in source`: the docstring names it to explain why it
        # is unusable, so the word survives the removal. Assert the exact code.
        self.assertNotIn('import statistics', source)
        self.assertNotIn('statistics.median(values)', source)
        self.assertIn('(ordered[middle - 1] + ordered[middle]) // 2', source)

    # ----------------------------------------------------------- the tool schema

    def test_the_tool_bounds_are_pinned(self):
        registry = build_registry()
        for name in ('document.match', 'document.posting_plan'):
            tolerance = registry.get(name).schema['properties']['tolerance_minor']
            with self.subTest(name=name):
                self.assertEqual(0, tolerance['minimum'])
                self.assertEqual(100_000_000_000, tolerance['maximum'])
        ratio = registry.get('document.fraud_signals').schema['properties'][
            'outlier_ratio']
        self.assertEqual(1, ratio['minimum'])
        self.assertEqual(100, ratio['maximum'])

    def test_the_payload_bound_is_sixteen_thousand(self):
        tool = build_registry().get('document.parse')
        tool.validate({'fields': 'x' * 16000})
        with self.assertRaises(ValueError):
            tool.validate({'fields': 'x' * 16001})

    # --------------------------------------------------------------- the config

    def test_the_config_floors(self):
        self.write({'tolerance_minor': 0})
        self.assertEqual(0, documents_config(TENANT)['tolerance_minor'])
        self.write({'tolerance_minor': -1})
        with self.assertRaises(ValueError):
            documents_config(TENANT)
        self.write({'outlier_ratio': 1})
        self.assertEqual(1, documents_config(TENANT)['outlier_ratio'])
        self.write({'outlier_ratio': 0})
        with self.assertRaises(ValueError):
            documents_config(TENANT)

    def test_the_history_clamp_is_pinned(self):
        # DEFENSIVE: the accepting side needs 501 stored rows to walk, so the
        # expression is pinned by its exact line and the clamp is exercised by the
        # suite's engine-backed history tests.
        self.assertIn('min(max(1, int(limit)), 500)',
                      inspect.getsource(supplier_history))

    def test_the_round_number_threshold_is_three(self):
        # A bare literal would be a threshold nobody could argue with, so it is named;
        # pinning the value keeps it from drifting silently.
        self.assertEqual(3, D.ROUND_TRAILING_ZEROS)

    def test_the_default_tolerance_is_zero(self):
        # A non-zero default would excuse mismatches the operator never agreed to.
        self.assertEqual(0, DEFAULT_TOLERANCE_MINOR)
        self.assertEqual(0, documents_config(TENANT)['tolerance_minor'])


if __name__ == '__main__':
    unittest.main()
