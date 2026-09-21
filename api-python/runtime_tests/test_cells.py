"""The ASCII-cell invariant, and the four modules that depend on it.

A single missing rule produced *silent* defects in several modules at once.
Python's ``re`` is Unicode-aware, so ``\\d`` matched Devanagari, Arabic-Indic and
fullwidth digits, and ``int()``/``float()`` normalised them. Nothing raised.
Nothing was counted. The platform simply answered with plausible wrong numbers:

    ``erp``        turned ``'१३.01.2026'`` into the string ``'2026-01-13'``
    ``documents``  filed a document under a date, and posted an amount, that
                   were never in the source
    ``escalation`` aged out an overdue item whose date it could not really read
    ``workforce``  read hours from a cell it could not read, accepted ``'1e5'``
                   as 100000, and let ``inf``/``nan`` through as a real shift;
                   it also echoed a Unicode date back as the register key
    ``telephony``  read a call duration out of a cell it could not read
    ``whatsapp``   read a Meta epoch out of digits that were not ASCII, so the
                   24-hour window was computed from 1970
    ``inventory``/``oee``/``manufacturing``
                   read a quantity or a price that was never written

The sweep ran three times, each broader than the last, and each found modules the
previous one missed:

1. **by module** -- the four that had just been touched, found by reading code;
2. **by regex pattern** -- found ``workforce``, ``telephony``, ``vision``, ``tools``;
3. **by numeric conversion** -- found ``whatsapp_inbound``.

That progression is the argument for ``cells``. The rule was never a module's
business, so every module that re-implemented parsing re-introduced the hole, and
looking module by module could only ever find the ones someone already suspected.
The structural test at the bottom of this file watches the whole list instead.

Each of those is asserted here as a refusal, from the *outside* of the module
that owns the rule, so that reverting any one of them fails a test rather than
silently returning a number again.
"""

import unittest

from platform_runtime import cells


# A representative of each Unicode decimal-digit family that ``\d`` accepts.
DEVANAGARI = '\u0967\u0968'          # १२
ARABIC_INDIC = '\u0661\u0662'        # ١٢
FULLWIDTH = '\uff11\uff12'           # １２
NON_ASCII_DIGITS = (DEVANAGARI, ARABIC_INDIC, FULLWIDTH)


class AsciiNumberTests(unittest.TestCase):
    def test_ascii_numbers_are_accepted(self):
        for text in ('0', '12', '-5', '+5', '1,5', '-1.5', '  12  '):
            with self.subTest(text=text):
                self.assertTrue(cells.is_ascii_number(text))

    def test_every_unicode_digit_family_is_refused(self):
        # The whole reason the module exists. If this test is ever "simplified"
        # back to ``\d``, it fails.
        for text in NON_ASCII_DIGITS:
            with self.subTest(text=text):
                self.assertFalse(cells.is_ascii_number(text))

    def test_a_mixed_cell_is_refused(self):
        # Half-ASCII is not more readable than none; it is more dangerous.
        self.assertFalse(cells.is_ascii_number(DEVANAGARI[0] + '2'))
        self.assertFalse(cells.is_ascii_number('1' + ARABIC_INDIC[0]))

    def test_exponent_notation_is_refused(self):
        # A spreadsheet rendering of a large number is not necessarily the number
        # the operator typed, and a quantity that gains three orders of magnitude
        # is the same defect class.
        for text in ('1e5', '1E5', '1e-5'):
            with self.subTest(text=text):
                self.assertFalse(cells.is_ascii_number(text))

    def test_non_numbers_are_refused(self):
        for text in ('', '   ', 'abc', '--1', '+-1', '.5', '5.', '1_000', '1 500'):
            with self.subTest(text=text):
                self.assertFalse(cells.is_ascii_number(text))

    def test_non_strings_are_refused(self):
        for value in (None, 12, 12.5, True, [], {}):
            with self.subTest(value=value):
                self.assertFalse(cells.is_ascii_number(value))

    def test_a_bounded_cell_is_refused_rather_than_truncated(self):
        # ``'1' * 16`` is over the 15-digit bound. Refusing is the point: a value
        # quietly shortened would be a different number than the one supplied.
        self.assertFalse(cells.is_ascii_number('1' * 16))
        self.assertTrue(cells.is_ascii_number('1' * 15))


class AsciiDigitRunTests(unittest.TestCase):
    def test_a_pure_run_is_accepted(self):
        self.assertTrue(cells.is_ascii_digit_run('20260102'))
        self.assertTrue(cells.is_ascii_digit_run('0'))

    def test_separators_and_unicode_digits_are_refused(self):
        for text in ('2026-01-02', '2026.01.02', '2026' + DEVANAGARI[0] + '0102',
                     '20' + FULLWIDTH[0] + '6'):
            with self.subTest(text=text):
                self.assertFalse(cells.is_ascii_digit_run(text))

    def test_empty_is_refused(self):
        # An empty string is *not* a run of digits. Returning True here would let
        # a caller skip its own emptiness check.
        self.assertFalse(cells.is_ascii_digit_run(''))
        self.assertFalse(cells.is_ascii_digit_run(None))


class ErpDateTests(unittest.TestCase):
    """``erp._date_text`` must never return a value it was not given."""

    def setUp(self):
        from platform_runtime import erp
        self.erp = erp

    def test_a_devanagari_date_is_refused_not_rewritten(self):
        # Before the fix this returned the ASCII string '2026-01-13': a date the
        # platform invented and then keyed a posting off.
        with self.assertRaises(self.erp.ErpError):
            self.erp._date_text('\u0967\u0969.01.2026')

    def test_a_devanagari_iso_date_is_refused(self):
        with self.assertRaises(self.erp.ErpError):
            self.erp._date_text('\u0968\u0966\u0968\u096c-01-02')

    def test_the_refusal_names_the_reason(self):
        with self.assertRaises(self.erp.ErpError) as caught:
            self.erp._date_text('\u0967\u0969.01.2026')
        self.assertIn('ASCII', str(caught.exception))

    def test_ascii_dates_still_work(self):
        # The guard must not break the supported forms.
        self.assertEqual(self.erp._date_text('2026-01-13'), '2026-01-13')
        self.assertEqual(self.erp._date_text('13.01.2026'), '2026-01-13')
        self.assertEqual(self.erp._date_text('2026-01-02'), '2026-01-02')

    def test_genuine_refusals_are_unchanged(self):
        # The pre-existing rules must survive the new one.
        with self.assertRaises(self.erp.ErpError):
            self.erp._date_text('2026-13-01')      # not a real date
        with self.assertRaises(self.erp.ErpError):
            self.erp._date_text('12.01.2026')      # genuinely ambiguous
        with self.assertRaises(self.erp.ErpError):
            self.erp._date_text('31/12/26')        # unsupported form


class DocumentsCellTests(unittest.TestCase):
    """The document block files and posts money, so it must read ASCII only."""

    def setUp(self):
        from platform_runtime import documents
        self.documents = documents

    def test_a_unicode_amount_is_refused(self):
        with self.assertRaises(ValueError):
            self.documents._amount_minor('\u0967\u0968\u0969.\u0966\u0967', 'USD')

    def test_an_ascii_amount_is_still_parsed_exactly(self):
        self.assertEqual(self.documents._amount_minor('123.01', 'USD'), 12301)
        self.assertEqual(self.documents._amount_minor('123.01', 'UZS'), 123)

    def test_the_amount_refusal_names_ascii(self):
        with self.assertRaises(ValueError) as caught:
            self.documents._amount_minor('\u0967\u0968\u0969.\u0966\u0967', 'USD')
        self.assertIn('ASCII', str(caught.exception))

    def test_a_unicode_doc_date_is_refused(self):
        with self.assertRaises(ValueError):
            self.documents._date('\u0968\u0966\u0968\u096c-01-02')

    def test_an_ascii_doc_date_is_still_accepted(self):
        self.assertEqual(self.documents._date('2026-01-02'), '2026-01-02')


class EscalationStalenessTests(unittest.TestCase):
    """An unreadable due date must never age an item out of the escalation."""

    def _stale(self, due, now, max_age_days=365):
        # ``_is_stale`` needs no engine state, so it is exercised directly rather
        # than through a coordinator built for a tenant.
        from platform_runtime import escalation
        # The method is defined on the coordinator class; call it on a throwaway
        # instance without running __init__.
        instance = escalation.EscalationLoop.__new__(escalation.EscalationLoop)
        return instance._is_stale(due, now, max_age_days)

    def test_a_unicode_due_date_is_never_stale(self):
        # Before the fix this parsed as 2020-01-01 and the item was dropped.
        now = 1760000000.0
        self.assertFalse(self._stale('\u0968\u0966\u0968\u0966-01-01', now))

    def test_an_ascii_old_due_date_is_still_stale(self):
        now = 1760000000.0
        self.assertTrue(self._stale('2020-01-01', now))

    def test_an_ascii_recent_due_date_is_not_stale(self):
        now = 1760000000.0
        self.assertFalse(self._stale('2026-09-19', now))

    def test_an_unparsable_due_date_is_not_stale(self):
        now = 1760000000.0
        for due in ('not a date', '', None, '2026-13-45'):
            with self.subTest(due=due):
                self.assertFalse(self._stale(due, now))


class ValueReaderTests(unittest.TestCase):
    """The three ``_number`` readers must report unreadable, not read anyway."""

    def _readers(self):
        from platform_runtime import inventory, manufacturing, oee
        return (inventory, oee, manufacturing)

    def test_every_reader_refuses_unicode_digits(self):
        for module in self._readers():
            for text in NON_ASCII_DIGITS:
                with self.subTest(module=module.__name__, text=text):
                    self.assertIsNone(module._number(text))

    def test_every_reader_still_reads_ascii(self):
        for module in self._readers():
            with self.subTest(module=module.__name__):
                self.assertEqual(module._number('12'), 12.0)
                self.assertEqual(module._number('1,5'), 1.5)
                self.assertEqual(module._number('-5'), -5.0)

    def test_every_reader_keeps_blank_zero_and_non_finite_distinct(self):
        # The invariant the readers were originally written for: three different
        # facts, and a non-finite float is not a number either.
        for module in self._readers():
            with self.subTest(module=module.__name__):
                self.assertIsNone(module._number(''))
                self.assertIsNone(module._number(None))
                self.assertEqual(module._number('0'), 0.0)
                self.assertEqual(module._number(0), 0.0)
                self.assertIsNone(module._number(float('inf')))
                self.assertIsNone(module._number(float('nan')))
                self.assertIsNone(module._number('-inf'))


class WorkforceCellTests(unittest.TestCase):
    """The shift register. Found on the second, pattern-driven sweep.

    ``_hours`` also carried the ``inf``/``nan`` hole that P12 and P13 closed in
    their own copies -- the sweep that fixed those never reached this module,
    because it read the defect as belonging to the modules that had just been
    written rather than to the pattern of parsing a cell by hand.
    """

    def setUp(self):
        from platform_runtime import workforce
        self.workforce = workforce

    def test_hours_refuses_every_unicode_digit_family(self):
        for text in NON_ASCII_DIGITS:
            with self.subTest(text=text):
                self.assertIsNone(self.workforce._hours(text))

    def test_hours_refuses_exponent_notation(self):
        # Before the fix this was 100000.0 working hours.
        self.assertIsNone(self.workforce._hours('1e5'))

    def test_hours_refuses_a_non_finite_value(self):
        # The hole that survived the P12/P13 sweep.
        for value in (float('inf'), float('nan'), float('-inf')):
            with self.subTest(value=value):
                self.assertIsNone(self.workforce._hours(value))
        for text in ('inf', 'nan', '-inf'):
            with self.subTest(text=text):
                self.assertIsNone(self.workforce._hours(text))

    def test_hours_still_reads_a_real_cell(self):
        self.assertEqual(self.workforce._hours('12'), 12.0)
        self.assertEqual(self.workforce._hours('1,5'), 1.5)
        self.assertEqual(self.workforce._hours('0'), 0.0)
        self.assertEqual(self.workforce._hours(8), 8.0)

    def test_hours_keeps_blank_distinct_from_zero(self):
        self.assertIsNone(self.workforce._hours(''))
        self.assertIsNone(self.workforce._hours(None))
        self.assertEqual(self.workforce._hours('0'), 0.0)

    def test_a_unicode_date_is_not_a_register_key(self):
        # ``_iso_day_text`` echoes the match back, so this would have become the
        # row's key: a date no ASCII reader can find.
        self.assertIsNone(self.workforce._iso_day_text('\u0968\u0966\u0968\u096c-01-02'))

    def test_an_ascii_day_is_still_returned(self):
        self.assertEqual(self.workforce._iso_day_text('2026-01-02'), '2026-01-02')


class TelephonyCellTests(unittest.TestCase):
    """A call duration is a measured quantity, so it reads through the same rule."""

    def _duration_reader(self):
        # ``_duration(row, entry)`` needs a register entry, so this exercises it
        # through the same door the module uses rather than re-implementing it.
        from platform_runtime import telephony

        entry = {'duration_column': 'duration'}

        def read(cell):
            return telephony._duration({'duration': cell}, entry)

        return read

    def test_a_unicode_duration_is_not_a_call_length(self):
        read = self._duration_reader()
        for text in NON_ASCII_DIGITS:
            with self.subTest(text=text):
                self.assertIsNone(read(text))

    def test_an_ascii_duration_is_still_read(self):
        read = self._duration_reader()
        self.assertEqual(read('123'), 123.0)
        self.assertEqual(read('12.5'), 12.5)
        self.assertEqual(read('0'), 0.0)

    def test_the_duration_reader_is_unchanged_by_the_ascii_rule(self):
        # Measured, not assumed: the duration reader accepts a dotted decimal and
        # refuses a comma one, and that asymmetry predates the ASCII rule -- the
        # comma is normalised to a dot *before* the pattern is applied, so it then
        # fails the digit check. This test records the behaviour as it is, so the
        # ASCII fix is not credited with (or blamed for) a change it did not make.
        read = self._duration_reader()
        self.assertEqual(read('12.5'), 12.5)
        self.assertIsNone(read('12,5'))
        self.assertIsNone(read('1234567'))       # over MAX_DURATION digits

    def test_a_blank_duration_is_none(self):
        read = self._duration_reader()
        self.assertIsNone(read(''))
        self.assertIsNone(read(None))


class WhatsAppEpochTests(unittest.TestCase):
    """Found on the third sweep, over numeric *conversions* rather than patterns.

    ``str.isdigit()`` is Unicode-aware, so ``'१२३'`` passed it and ``int()``
    normalised it. The window arithmetic then ran off epoch 123 -- 1970 -- and the
    customer's 24-hour window would have read as long closed.
    """

    def setUp(self):
        from platform_runtime import whatsapp_inbound
        self.module = whatsapp_inbound

    def test_a_unicode_epoch_is_not_a_timestamp(self):
        for text in NON_ASCII_DIGITS:
            with self.subTest(text=text):
                self.assertIsNone(self.module._epoch(text))

    def test_an_ascii_epoch_is_still_an_int(self):
        self.assertEqual(self.module._epoch('1760000000'), 1760000000)
        self.assertEqual(self.module._epoch(1760000000), 1760000000)

    def test_a_non_numeric_epoch_is_none(self):
        for value in ('', 'abc', None, True, '12.5', '-5'):
            with self.subTest(value=value):
                self.assertIsNone(self.module._epoch(value))


class GraphCanonicalTests(unittest.TestCase):
    """The conflict detector, checked because it is where the rule matters most.

    This one was already correct -- ``_NUMERIC`` always used ``[0-9]`` -- and is
    tested so it stays that way. The point of the assertion is subtler than the
    others: a Unicode digit must not be read as *either* a number *or* as a second
    spelling of the ASCII number. It must stay a distinct string, so the graph
    reports "these are two different values" rather than inventing agreement or a
    conflict between two values that are really the same.
    """

    def setUp(self):
        from platform_runtime import business_graph
        self.graph = business_graph

    def test_a_unicode_digit_is_not_a_number(self):
        self.assertEqual(self.graph.canonical('\u0967\u0968')[0], 's')

    def test_a_unicode_digit_does_not_equal_its_ascii_twin(self):
        # If this were equal, a mangled cell and a real value would be reported as
        # agreement -- the one outcome worse than a reported conflict.
        self.assertNotEqual(self.graph.canonical('\u0967\u0968'),
                            self.graph.canonical('12'))

    def test_ascii_numbers_still_compare_as_numbers(self):
        # A database returns 450000 and a spreadsheet returns '450000'; these must
        # stay equal, or every price would look like a conflict.
        self.assertEqual(self.graph.canonical('450000'),
                         self.graph.canonical(450000))


class NoUnicodeDigitPatternRemainsTests(unittest.TestCase):
    """A structural guard, so the rule cannot be lost module by module.

    The four defects were one missing rule in four places. A behavioural test can
    only cover the call sites someone thought to write; this reads the source of
    the value-and-date readers and fails if a bare ``\\d`` reappears in a pattern
    that interprets a cell.
    """

    # Files whose regexes interpret a value that came from someone else's system.
    # The first audit pass found only the four modules below; the second pass,
    # sweeping by pattern rather than by module, found ``workforce``, ``telephony``,
    # ``vision`` and ``tools``; the third, sweeping the numeric *conversions* rather
    # than the patterns, found ``whatsapp_inbound``. They are all listed here so the
    # next one to reappear fails a test instead of being found by hand a fourth time.
    #
    # ``business_graph.py`` is on the list although it was already correct: its
    # ``_NUMERIC`` always used ``[0-9]``, which is exactly the pattern the rest of
    # the family is being brought to. Watching it keeps it that way.
    WATCHED = ('cells.py', 'erp.py', 'documents.py', 'escalation.py',
               'inventory.py', 'oee.py', 'manufacturing.py',
               'workforce.py', 'telephony.py', 'vision.py', 'tools.py',
               'whatsapp_inbound.py', 'business_graph.py')

    def test_no_bare_digit_class_in_a_value_reader(self):
        import os
        import re as regex

        root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '..', 'platform_runtime')
        offenders = []
        for name in self.WATCHED:
            path = os.path.join(root, name)
            with open(path, encoding='utf-8') as handle:
                for number, line in enumerate(handle, 1):
                    stripped = line.strip()
                    if stripped.startswith('#'):
                        continue
                    # ``\d`` inside a pattern literal, but not ``\\d`` written in a
                    # docstring about it, and not the deliberately ASCII classes.
                    if regex.search(r'''re\.(?:compile|fullmatch|match)\(.*[^\\0-9]\\d''',
                                    line):
                        offenders.append(f'{name}:{number}: {stripped}')
        self.assertEqual(offenders, [], 'a Unicode-aware \\d is interpreting a cell: '
                                        + '; '.join(offenders))


if __name__ == '__main__':
    unittest.main()
