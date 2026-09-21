"""Sheets register contract tests. No live Google call; the transport is scripted.

The behaviours that matter: a spreadsheet id, a range and a credential can only come
from operator configuration, an undeclared name is refused rather than read as empty,
and provider rows are bounded and never reformatted before they reach a model.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime.engine import Engine, Forbidden
from platform_runtime.sheets import (
    MAX_CELL_CHARS,
    registers,
    resolve,
    shape_rows,
    validate_range,
)
from platform_runtime.tools import build_registry

TENANT = 't_sheets'
AGENT = 'finance.bot'
CONNECTION = 'google'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

POLICY = {
    'tools': ['sheets.registers', 'sheets.read', 'sheets.rows', 'sheets.append'],
    'allowed_connections': [CONNECTION],
    'ladder': 'human_assisted',
}


class RecordingTransport:
    def __init__(self, payload=None):
        self.payload = {'values': []} if payload is None else payload
        self.calls = []

    def __call__(self, url, token):
        self.calls.append({'url': url, 'token': token})
        return self.payload


class SheetsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name) / 'sheets.db', build_registry(),
                             lambda t, a: POLICY)
        self.registers = {
            'finance': {
                'connection': CONNECTION,
                'spreadsheet_id': SPREADSHEET,
                'ranges': {'revenue': 'Kunlik!A1:F', 'expenses': 'Xarajat!A1:D'},
                'max_rows': 50,
            },
            'hr': {
                'connection': CONNECTION,
                'spreadsheet_id': SPREADSHEET,
                'ranges': {'candidates': 'Nomzodlar!A1:E'},
                'header_row': False,
            },
        }
        self.write_config({'sheets_registers': self.registers})
        env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(self.cfg)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)

    def write_config(self, payload):
        self.cfg = Path(self.tmp.name) / 'integrations.json'
        self.cfg.write_text(json.dumps({TENANT: payload}, ensure_ascii=False), encoding='utf-8')

    def tool(self, name):
        return build_registry().get(name)

    def call(self, name, args, transport=None):
        """Invoke a handler with the provider read replaced by a scripted transport."""
        transport = transport or RecordingTransport()
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-access-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: transport(url, token)):
                result = self.tool(name).handler(self.engine, TENANT, AGENT, args, 's1')
        self.transport = transport
        return result

    # ------------------------------------------------------------ registration

    def test_tools_are_read_only_except_the_approved_append(self):
        registry = build_registry()
        for name in ('sheets.registers', 'sheets.read', 'sheets.rows'):
            self.assertEqual('read', registry.get(name).risk)
        self.assertEqual('write', registry.get('sheets.append').risk)

    def test_registers_are_validated_not_skipped(self):
        entry = registers(TENANT)
        self.assertEqual({'finance', 'hr'}, set(entry))
        self.assertEqual('Kunlik!A1:F', entry['finance']['ranges']['revenue'])
        self.assertTrue(entry['finance']['header_row'])
        self.assertFalse(entry['hr']['header_row'])

    def test_bad_register_configuration_is_refused(self):
        base = {'connection': CONNECTION, 'spreadsheet_id': SPREADSHEET,
                'ranges': {'x': 'S!A1'}}
        for label, override in [
            ('short id', {'spreadsheet_id': 'tooshort'}),
            ('bad id chars', {'spreadsheet_id': 'bad id with spaces!!!!!!!!!!!!!!!!'}),
            ('no ranges', {'ranges': {}}),
            ('bad a1', {'ranges': {'x': 'S!A1;DROP'}}),
            ('traversal-ish a1', {'ranges': {'x': '../../etc'}}),
            ('unknown key', {'extra': 1}),
            ('bad header_row', {'header_row': 'yes'}),
            ('bad max_rows', {'max_rows': 0}),
            ('no connection', {'connection': ''}),
        ]:
            with self.subTest(case=label):
                self.write_config({'sheets_registers': {'bad': {**base, **override}}})
                with self.assertRaises(ValueError):
                    registers(TENANT)
        self.write_config({'sheets_registers': self.registers})

    def test_register_name_must_be_an_identifier(self):
        self.write_config({'sheets_registers': {'Bad Name!': self.registers['finance']}})
        with self.assertRaises(ValueError):
            registers(TENANT)

    def test_a1_notation_accepts_cyrillic_sheet_names(self):
        for good in ['Лист!A1:F', 'Kunlik!A1', 'S!A1:B99', 'S!A:F', 'S!1:5', 'Kunlik!A1:F100']:
            with self.subTest(range=good):
                validate_range(good)

    def test_bare_sheet_name_is_not_a_range(self):
        # 'Kunlik' alone is ambiguous with a named range, so it is refused rather
        # than silently interpreted as a whole-sheet read.
        with self.assertRaises(ValueError):
            validate_range('Kunlik')

    def test_a1_notation_rejects_structure_breaking_input(self):
        for bad in ['S!A1;B2', 'S!A1,B2', '=SUM(A1:A9)', '../etc', "S'!A1", 'S!A0', '', '!A1',
                    'A1' * 80]:
            with self.subTest(range=bad), self.assertRaises(ValueError):
                validate_range(bad)

    def test_undeclared_register_and_range_are_refused(self):
        with self.assertRaises(Forbidden):
            resolve(TENANT, 'missing', 'revenue')
        with self.assertRaises(Forbidden):
            resolve(TENANT, 'finance', 'missing')

    def test_resolve_returns_only_operator_declared_destination(self):
        resolved = resolve(TENANT, 'finance', 'revenue')
        self.assertEqual(SPREADSHEET, resolved['spreadsheet_id'])
        self.assertEqual('Kunlik!A1:F', resolved['a1'])
        self.assertEqual(CONNECTION, resolved['connection'])

    # -------------------------------------------------------------- provider read

    def test_read_uses_only_the_declared_range_and_token(self):
        transport = RecordingTransport({'values': [['a', 'b']]})
        self.call('sheets.read', {'register': 'finance', 'range': 'revenue'}, transport)
        call = transport.calls[0]
        self.assertIn(f'/v4/spreadsheets/{SPREADSHEET}/values/', call['url'])
        # Both '!' and ':' are percent-encoded, so a declared range cannot break out
        # of the path segment it was placed in.
        self.assertIn('Kunlik%21A1%3AF', call['url'])
        self.assertIn('valueRenderOption=UNFORMATTED_VALUE', call['url'])
        self.assertEqual('fake-access-token', call['token'])

    def test_plan_cannot_supply_a_spreadsheet_or_a_range(self):
        # The schema rejects unknown keys, so extra arguments never reach the adapter.
        for args in [{'register': 'finance', 'range': 'revenue', 'spreadsheet_id': 'x'},
                     {'register': 'finance', 'range': 'revenue', 'a1': 'A1:Z900'},
                     {'register': 'finance', 'range': 'revenue', 'url': 'https://evil.invalid'}]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.tool('sheets.read').validate(args)

    def test_agent_without_the_tool_is_denied(self):
        engine = Engine(Path(self.tmp.name) / 'deny.db', build_registry(),
                        lambda t, a: {'tools': ['sheets.registers'],
                                      'allowed_connections': [CONNECTION],
                                      'ladder': 'human_assisted'})
        with self.assertRaises(Forbidden):
            self.tool('sheets.read').handler(engine, TENANT, AGENT,
                                             {'register': 'finance', 'range': 'revenue'}, 's1')

    def test_connection_outside_agent_allowlist_is_denied(self):
        engine = Engine(Path(self.tmp.name) / 'deny2.db', build_registry(),
                        lambda t, a: {'tools': POLICY['tools'], 'allowed_connections': ['other'],
                                      'ladder': 'human_assisted'})
        with self.assertRaises(Forbidden):
            self.tool('sheets.read').handler(engine, TENANT, AGENT,
                                             {'register': 'finance', 'range': 'revenue'}, 's1')

    def test_an_empty_allowlist_permits_nothing(self):
        """The rule every other module already uses, now enforced here too.

        ``sheets.py`` used to read an empty ``allowed_connections`` as "everything
        permitted" while ``engine.py``, ``connectors.py``, ``google_adapters.py``
        and ``business_graph.py`` read it as "nothing permitted". One policy value
        meant two opposite things, and this file was the odd one out. A handler
        invoked directly -- as the dispatch path can do, and as an internal caller
        certainly can -- was therefore more permissive here than anywhere else.
        """
        engine = Engine(Path(self.tmp.name) / 'deny3.db', build_registry(),
                        lambda t, a: {'tools': POLICY['tools'], 'allowed_connections': [],
                                      'ladder': 'human_assisted'})
        with self.assertRaises(Forbidden):
            self.tool('sheets.read').handler(engine, TENANT, AGENT,
                                             {'register': 'finance', 'range': 'revenue'}, 's1')
        with self.assertRaises(Forbidden):
            self.tool('sheets.rows').handler(engine, TENANT, AGENT,
                                             {'register': 'finance', 'range': 'revenue'}, 's1')

    def test_an_absent_allowlist_permits_nothing(self):
        """Absent is the same as empty, and must not be read as a wildcard."""
        engine = Engine(Path(self.tmp.name) / 'deny4.db', build_registry(),
                        lambda t, a: {'tools': POLICY['tools'], 'ladder': 'human_assisted'})
        with self.assertRaises(Forbidden):
            self.tool('sheets.read').handler(engine, TENANT, AGENT,
                                             {'register': 'finance', 'range': 'revenue'}, 's1')

    def test_registers_listing_honours_the_allowlist(self):
        """The register map is filtered by the same rule as a read.

        A listing is not data, but it is a map of what exists. Handing one out
        under a policy every other module treats as closed is the same defect at a
        smaller scale, so the two call sites were fixed together.
        """
        engine = Engine(Path(self.tmp.name) / 'deny5.db', build_registry(),
                        lambda t, a: {'tools': POLICY['tools'], 'allowed_connections': [],
                                      'ladder': 'human_assisted'})
        listing = self.tool('sheets.registers').handler(engine, TENANT, AGENT, {}, 's1')
        self.assertEqual([], listing['registers'])

    def test_registers_listing_shows_only_permitted_connections(self):
        """And it still shows what IS permitted, so the fix is not a blanket deny."""
        engine = Engine(Path(self.tmp.name) / 'deny6.db', build_registry(),
                        lambda t, a: {'tools': POLICY['tools'], 'allowed_connections': [CONNECTION],
                                      'ladder': 'human_assisted'})
        listing = self.tool('sheets.registers').handler(engine, TENANT, AGENT, {}, 's1')
        self.assertEqual(['finance', 'hr'], [r['register'] for r in listing['registers']])

    def test_registers_output_hides_the_spreadsheet_id(self):
        listing = self.call('sheets.registers', {})
        self.assertEqual(['finance', 'hr'], [r['register'] for r in listing['registers']])
        self.assertNotIn(SPREADSHEET, json.dumps(listing, ensure_ascii=False))
        self.assertEqual(['expenses', 'revenue'], listing['registers'][0]['ranges'])

    def test_rows_are_keyed_by_the_header(self):
        payload = {'values': [['Kun', 'Summa', 'Izoh'], ['2026-09-19', 450000, 'Naqd'],
                              ['2026-09-18', 120000, 'Click']]}
        result = self.call('sheets.rows', {'register': 'finance', 'range': 'revenue'},
                           RecordingTransport(payload))
        self.assertEqual(['Kun', 'Summa', 'Izoh'], result['header'])
        self.assertEqual(2, result['returned'])
        self.assertEqual({'Kun': '2026-09-19', 'Summa': 450000, 'Izoh': 'Naqd'}, result['rows'][0])
        self.assertEqual(450000, result['rows'][0]['Summa'])
        self.assertFalse(result['truncated'])

    def test_rows_without_a_header_returns_the_matrix(self):
        payload = {'values': [['Ali', 1], ['Vali', 2]]}
        result = self.call('sheets.rows', {'register': 'hr', 'range': 'candidates'},
                           RecordingTransport(payload))
        self.assertEqual([], result['header'])
        self.assertEqual([['Ali', 1], ['Vali', 2]], result['rows'])

    def test_limit_narrows_but_never_widens_the_declared_max(self):
        payload = {'values': [['h'], ['1'], ['2'], ['3']]}
        result = self.call('sheets.rows', {'register': 'finance', 'range': 'revenue', 'limit': 2},
                           RecordingTransport(payload))
        self.assertEqual(2, result['returned'])
        self.assertTrue(result['truncated'])
        # A caller cannot ask for more rows than the register declares.
        big = self.call('sheets.rows', {'register': 'finance', 'range': 'revenue', 'limit': 200},
                        RecordingTransport(payload))
        self.assertEqual(3, big['returned'])

    def test_provider_failure_is_sanitized(self):
        from platform_runtime.sheets import SheetsError, _http_get
        import io
        import urllib.error
        error = urllib.error.HTTPError('https://sheets.googleapis.com/v4/x', 403,
                                       'Forbidden', {}, io.BytesIO(b'quota detail'))
        self.addCleanup(error.close)
        with patch('urllib.request.OpenerDirector.open', side_effect=error):
            with self.assertRaises(SheetsError) as raised:
                _http_get('https://sheets.googleapis.com/v4/x', 'token')
        message = str(raised.exception)
        self.assertIn('403', message)
        self.assertNotIn('Forbidden', message)
        self.assertNotIn('quota detail', message)


class ShapingTests(unittest.TestCase):
    """Pure shaping rules: bounded, positional-safe, never reformatted."""

    def test_cells_are_bounded(self):
        header, rows = shape_rows([['h'], ['x' * 5000]], True, 10)
        self.assertEqual(MAX_CELL_CHARS, len(rows[0]['h']))

    def test_blank_header_becomes_positional(self):
        header, rows = shape_rows([['', 'Summa'], ['a', 1]], True, 10)
        self.assertEqual(['column_1', 'Summa'], header)
        self.assertEqual({'column_1': 'a', 'Summa': 1}, rows[0])

    def test_duplicate_header_is_disambiguated(self):
        header, _ = shape_rows([['Summa', 'Summa'], [1, 2]], True, 10)
        self.assertEqual(['Summa', 'Summa_2'], header)

    def test_short_and_long_rows_are_padded_not_dropped(self):
        header, rows = shape_rows([['a', 'b'], ['only-a'], ['a', 'b', 'extra']], True, 10)
        self.assertEqual({'a': 'only-a', 'b': ''}, rows[0])
        self.assertEqual({'a': 'a', 'b': 'b', 'column_3': 'extra'}, rows[1])

    def test_non_list_rows_are_ignored(self):
        header, rows = shape_rows([['h'], 'not-a-row', ['ok']], True, 10)
        self.assertEqual(['h'], header)
        self.assertEqual([{'h': 'ok'}], rows)

    def test_numbers_and_booleans_survive_unquoted(self):
        _, rows = shape_rows([['n', 'b'], [450000, True]], True, 5)
        self.assertEqual(450000, rows[0]['n'])
        self.assertIs(True, rows[0]['b'])

    def test_empty_sheet_is_not_an_error(self):
        header, rows = shape_rows([], True, 10)
        self.assertEqual([], header)
        self.assertEqual([], rows)


class SheetsBoundaryTests(SheetsTests):
    """The exact caps, pinned by VALUE.

    The existing tests read the constants back out of the module (``MAX_CELL_CHARS``,
    ``shape_rows``' own limit), so widening a constant moves both sides of the
    assertion and the test stays green. These pin the literal, and where the bound is
    reachable they walk it from both sides.
    """

    ENTRY = {'connection': CONNECTION, 'spreadsheet_id': SPREADSHEET,
             'ranges': {'revenue': 'A1:B'}, 'max_rows': 10}

    # ------------------------------------------------------------- the caps

    def test_the_read_ceiling_is_two_hundred(self):
        from platform_runtime.sheets import MAX_ROWS
        self.assertEqual(200, MAX_ROWS)
        limit = self.tool('sheets.read').schema['properties']['limit']
        self.assertEqual(200, limit['maximum'])
        self.assertEqual(1, limit['minimum'])

    def test_the_cell_ceiling_is_two_hundred(self):
        from platform_runtime.sheets import MAX_CELL_CHARS
        self.assertEqual(200, MAX_CELL_CHARS)
        _, rows = shape_rows([['h'], ['x' * 5000]], True, 5)
        self.assertEqual(200, len(rows[0]['h']))

    def test_the_response_byte_ceiling_is_two_hundred_thousand(self):
        import platform_runtime.sheets as S
        from platform_runtime.sheets import SheetsError

        class Response:
            def __init__(self, body):
                self.body = body

            def read(self, n):
                return self.body[:n]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Opener:
            def __init__(self, body):
                self.body = body

            def open(self, request, timeout=None):
                return Response(self.body)

        def body_of(size):
            # A VALID JSON object of exactly `size` bytes: b'{"v":"' + pad + b'"}'
            return b'{"v":"' + b'x' * (size - 8) + b'"}'

        def read(body):
            with patch('urllib.request.build_opener',
                       side_effect=lambda *a, **k: Opener(body)):
                return S._http_get('https://example.invalid/x', 'tok')

        self.assertEqual(200_000, len(body_of(200_000)))
        self.assertEqual({'v': 'x' * (200_000 - 8)}, read(body_of(200_000)))
        with self.assertRaises(SheetsError):
            read(body_of(200_001))

    def test_the_spreadsheet_id_ceiling_is_one_hundred_and_twenty(self):
        self.write_config({'sheets_registers': {
            'ok': dict(self.ENTRY, spreadsheet_id='A' * 120)}})
        self.assertEqual('A' * 120, registers(TENANT)['ok']['spreadsheet_id'])
        self.write_config({'sheets_registers': {
            'bad': dict(self.ENTRY, spreadsheet_id='A' * 121)}})
        with self.assertRaises(ValueError):
            registers(TENANT)

    def test_the_connection_name_ceiling_is_sixty_four(self):
        entry = {k: v for k, v in self.ENTRY.items() if k != 'connection'}
        self.write_config({'sheets_registers': {
            'ok': dict(entry, connection='c' * 64)}})
        self.assertEqual(64, len(registers(TENANT)['ok']['connection']))
        self.write_config({'sheets_registers': {
            'bad': dict(entry, connection='c' * 65)}})
        with self.assertRaises(ValueError):
            registers(TENANT)

    def test_register_and_range_names_are_capped_at_sixty_four(self):
        self.write_config({'sheets_registers': {
            'r' * 64: dict(self.ENTRY)}})
        self.assertIn('r' * 64, registers(TENANT))
        self.write_config({'sheets_registers': {
            'r' * 65: dict(self.ENTRY)}})
        with self.assertRaises(ValueError):
            registers(TENANT)
        self.write_config({'sheets_registers': {
            'ok': dict(self.ENTRY, ranges={'g' * 65: 'A1:B'})}})
        with self.assertRaises(ValueError):
            registers(TENANT)

    def test_the_range_length_ceiling_is_one_hundred_and_twenty_eight(self):
        # DEFENSIVE: the longest VALID A1 string is 86 characters (64-char sheet
        # name + `!` + a 3-column, 7-digit cell, twice), so no valid range reaches
        # 128 and the accepting side cannot be walked. Pinned by the exact line.
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'sheets.py').read_text(encoding='utf-8')
        lines = set(source.splitlines())
        self.assertIn('    if not isinstance(value, str) or not value '
                      'or len(value) > 128:', lines)

    def test_a_sheet_name_without_a_cell_is_refused(self):
        # A1_RE matches a bare `Sheet!` -- the cell part is OPTIONAL -- which is a
        # sheet reference, not a range. The explicit bang check is what refuses it.
        with self.assertRaises(ValueError):
            validate_range('Kunlik!')

    def test_the_register_cap_is_fifty(self):
        self.write_config({'sheets_registers': {
            'r%d' % i: dict(self.ENTRY) for i in range(50)}})
        self.assertEqual(50, len(registers(TENANT)))
        self.write_config({'sheets_registers': {
            'r%d' % i: dict(self.ENTRY) for i in range(51)}})
        with self.assertRaises(ValueError):
            registers(TENANT)

    def test_the_range_cap_is_forty(self):
        self.write_config({'sheets_registers': {
            'ok': dict(self.ENTRY,
                       ranges={'g%d' % i: 'A1:B' for i in range(40)})}})
        self.assertEqual(40, len(registers(TENANT)['ok']['ranges']))
        self.write_config({'sheets_registers': {
            'bad': dict(self.ENTRY,
                        ranges={'g%d' % i: 'A1:B' for i in range(41)})}})
        with self.assertRaises(ValueError):
            registers(TENANT)

    def test_the_declared_max_rows_ceiling_is_one_thousand(self):
        self.write_config({'sheets_registers': {
            'ok': dict(self.ENTRY, max_rows=1000)}})
        self.assertEqual(1000, registers(TENANT)['ok']['max_rows'])
        self.write_config({'sheets_registers': {
            'bad': dict(self.ENTRY, max_rows=1001)}})
        with self.assertRaises(ValueError):
            registers(TENANT)

    # ------------------------------------------------------- the two fixes

    def test_a_header_is_not_counted_as_a_data_row(self):
        # `header_row` defaults to True, so `values` carries the header. Counting it
        # as data announced a cut for a sheet holding exactly `limit` DATA rows, and
        # the caller re-runs a query that returns the same rows. Measured before the
        # fix: returned=2, truncated=True for [header, row1, row2] at limit=2.
        payload = {'values': [['h'], ['1'], ['2']]}
        result = self.call('sheets.rows',
                           {'register': 'finance', 'range': 'revenue', 'limit': 2},
                           RecordingTransport(payload))
        self.assertEqual(2, result['returned'])
        self.assertFalse(result['truncated'])
        # One more data row DOES announce a cut.
        payload = {'values': [['h'], ['1'], ['2'], ['3']]}
        cut = self.call('sheets.rows',
                        {'register': 'finance', 'range': 'revenue', 'limit': 2},
                        RecordingTransport(payload))
        self.assertTrue(cut['truncated'])

    def test_a_register_may_declare_max_rows_above_the_read_ceiling(self):
        # The declared cap is 1000, the read ceiling 200. Passing the declared value
        # straight into bounded_int(..., 1, MAX_ROWS) made EVERY ordinary read -- one
        # that passes no `limit` at all -- fail with "limit must be an integer 1..200",
        # a configuration `registers` had accepted. The effective limit is the smaller
        # of the two, clamped rather than raised.
        self.write_config({'sheets_registers': {
            'big': dict(self.ENTRY, max_rows=500)}})
        result = self.call('sheets.read', {'register': 'big', 'range': 'revenue'},
                           RecordingTransport({'values': [['h'], ['1']]}))
        self.assertEqual(1, result['returned'])
        # A declared limit above the ceiling is clamped to it, not refused.
        result = self.call('sheets.read',
                           {'register': 'big', 'range': 'revenue', 'limit': 100000},
                           RecordingTransport(
                               {'values': [['h']] + [['1']] * 300}))
        self.assertEqual(200, result['returned'])

    def test_the_keyed_row_slice_is_defensively_bounded(self):
        # `capped = values[:limit + 1]` already bounds `matrix`, so `matrix[1:]` and
        # `matrix[1:limit + 1]` are the same list: the slice is UNREACHABLE through
        # `_read` and a revert of it changes nothing observable. Pinned by its exact
        # line, with the reason, so a future change to `capped` cannot quietly widen
        # it while this test keeps passing for the wrong reason.
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'sheets.py').read_text(encoding='utf-8')
        lines = set(source.splitlines())
        self.assertIn('    for row in matrix[1:limit + 1]:', lines)

    def test_shape_rows_returns_at_most_the_limit(self):
        _, rows = shape_rows([['h'], ['1'], ['2'], ['3']], True, 2)
        self.assertEqual([{'h': '1'}, {'h': '2'}], rows)


if __name__ == '__main__':
    unittest.main()