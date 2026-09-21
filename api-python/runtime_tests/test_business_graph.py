"""Business Graph contract tests. No live provider call; the sheet transport is scripted.

The behaviours that matter: the graph grants no authority of its own, a conflict is
reported instead of silently resolved, a failing source is never rendered as "this
attribute does not exist", and a source's arguments can only come from operator
configuration.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime.business_graph import (
    BusinessGraphError,
    canonical,
    declaration,
    entity_names,
    graph_config,
    register_graph_tools,
)
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry

TENANT = 't_graph'
AGENT = 'sales.bot'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
GRAPH_TOOLS = ['graph.entities', 'graph.entity', 'graph.search', 'graph.timeline',
               'graph.conflicts', 'graph.explain']

POLICY = {
    'tools': [*GRAPH_TOOLS, 'connectors.read', 'sheets.rows'],
    'allowed_connections': ['erp', 'google'],
    'ladder': 'human_assisted',
}


class RecordingTransport:
    def __init__(self, payload=None):
        self.payload = {'values': []} if payload is None else payload
        self.calls = []

    def __call__(self, url, token):
        self.calls.append({'url': url, 'token': token})
        return self.payload


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.erp = self.root / 'erp.db'
        db = sqlite3.connect(self.erp)
        try:
            db.executescript(
                "CREATE TABLE products(sku TEXT, price INTEGER, stock INTEGER);"
                "INSERT INTO products VALUES('SKU-1042',450000,3);"
                "INSERT INTO products VALUES('SKU-77',99000,0);"
                "INSERT INTO products VALUES('',1,1);")
            db.commit()
        finally:
            db.close()
        self.connections = {
            'erp': {'driver': 'sqlite_readonly', 'path': str(self.erp),
                    'tables': {'products': ['sku', 'price', 'stock']}},
            'google': {},
        }
        self.registers = {
            'finance': {'connection': 'google', 'spreadsheet_id': SPREADSHEET,
                        'ranges': {'margins': 'Marja!A1:C'}, 'max_rows': 50},
        }
        self.graph = {
            'conflict_policy': 'report',
            'entities': {
                'product': {
                    'identity': 'sku',
                    'priority': ['erp', 'finance'],
                    'sources': {
                        'erp': {'tool': 'connectors.read', 'key': 'sku',
                                'args': {'connection': 'erp', 'table': 'products',
                                         'columns': ['sku', 'price', 'stock']},
                                'map': {'price': 'price', 'stock': 'stock'}},
                        'finance': {'tool': 'sheets.rows', 'key': 'SKU',
                                    'args': {'register': 'finance', 'range': 'margins'},
                                    'map': {'price': 'Narx', 'margin': 'Foyda'}},
                    },
                },
            },
        }
        self.policy = dict(POLICY)
        self.write_config()
        self.env = patch.dict(os.environ, {
            'PLATFORM_INTEGRATIONS_FILE': str(self.cfg),
            'PLATFORM_DB_ROOTS': json.dumps([str(self.root)]),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def write_config(self, payload=None):
        self.cfg = self.root / 'integrations.json'
        self.cfg.write_text(json.dumps({TENANT: payload if payload is not None else {
            'connections': self.connections,
            'sheets_registers': self.registers,
            'business_graph': self.graph,
        }}, ensure_ascii=False), encoding='utf-8')

    def build(self):
        # One file per test is unnecessary: every schema statement is IF NOT EXISTS.
        return Engine(self.root / 'platform.db', build_registry(), lambda t, a: self.policy)

    def call(self, name, args, transport=None):
        """Invoke a graph handler with the sheet provider replaced by a script."""
        transport = transport or RecordingTransport()
        engine = self.build()
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: transport(url, token)):
                result = build_registry().get(name).handler(engine, TENANT, AGENT, args, 's1')
        self.transport = transport
        return result

    # ------------------------------------------------------------ registration

    def test_every_graph_tool_is_read_only(self):
        registry = build_registry()
        for name in GRAPH_TOOLS:
            self.assertEqual('read', registry.get(name).risk)

    def test_undeclared_graph_is_empty_not_an_error(self):
        self.write_config({'connections': self.connections})
        self.assertEqual({}, graph_config(TENANT)['entities'])
        self.assertEqual([], entity_names(TENANT))

    def test_entity_names_are_sorted(self):
        self.assertEqual(['product'], entity_names(TENANT))
    # ------------------------------------------------------------ configuration

    def test_unsupported_keys_are_refused(self):
        for payload in ({'business_graph': {**self.graph, 'extra': 1}},
                        {'business_graph': {'entities': {'product': {
                            **self.graph['entities']['product'], 'extra': 1}}}},
                        {'business_graph': {'entities': {'product': {
                            'sources': {'erp': {**self.graph['entities']['product']
                                                ['sources']['erp'], 'extra': 1}}}}}}):
            with self.subTest(payload=payload):
                self.write_config(payload)
                with self.assertRaises(ValueError):
                    graph_config(TENANT)
        self.write_config()

    def test_newest_wins_is_refused_rather_than_faked(self):
        self.write_config({'business_graph': {**self.graph, 'conflict_policy': 'newest_wins'}})
        with self.assertRaises(ValueError) as caught:
            graph_config(TENANT)
        self.assertIn('not implemented', str(caught.exception))
        self.write_config()

    def test_unknown_conflict_policy_is_refused(self):
        self.write_config({'business_graph': {**self.graph, 'conflict_policy': 'guess'}})
        with self.assertRaises(ValueError):
            graph_config(TENANT)
        self.write_config()

    def test_write_tool_cannot_back_a_source(self):
        for tool in ('telegram.send', 'sheets.append', 'database.write'):
            self.write_config({'business_graph': {'entities': {'product': {
                'sources': {'erp': {'tool': tool, 'key': 'sku', 'args': {'connection': 'erp'},
                                    'map': {'price': 'price'}}}},
                'conflict_policy': 'report'}}})
            with self.subTest(tool=tool), self.assertRaises(ValueError):
                graph_config(TENANT)
        self.write_config()

    def test_priority_must_list_every_source_exactly_once(self):
        for priority in (['erp'], ['erp', 'erp'], ['erp', 'other'], 'erp'):
            entity = {**self.graph['entities']['product'], 'priority': priority}
            self.write_config({'business_graph': {'entities': {'product': entity},
                                                  'conflict_policy': 'report'}})
            with self.subTest(priority=priority), self.assertRaises(ValueError):
                graph_config(TENANT)
        self.write_config()

    def test_declared_field_outside_the_column_allowlist_is_refused(self):
        entity = json.loads(json.dumps(self.graph['entities']['product']))
        entity['sources']['erp']['map']['cost'] = 'cost'
        self.write_config({'business_graph': {'entities': {'product': entity},
                                              'conflict_policy': 'report'},
                           'connections': self.connections})
        with self.assertRaises(ValueError) as caught:
            graph_config(TENANT)
        self.assertIn('not declared', str(caught.exception))
        self.write_config()

    def test_attribute_name_must_be_an_identifier(self):
        entity = json.loads(json.dumps(self.graph['entities']['product']))
        entity['sources']['erp']['map']['Price!'] = 'price'
        self.write_config({'business_graph': {'entities': {'product': entity},
                                              'conflict_policy': 'report'}})
        with self.assertRaises(ValueError):
            graph_config(TENANT)
        self.write_config()

    def test_source_args_must_be_operator_declared(self):
        for args in ({}, 'connection=erp', None):
            entity = json.loads(json.dumps(self.graph['entities']['product']))
            entity['sources']['erp']['args'] = args
            self.write_config({'business_graph': {'entities': {'product': entity},
                                                  'conflict_policy': 'report'}})
            with self.subTest(args=args), self.assertRaises(ValueError):
                graph_config(TENANT)
        self.write_config()

    def test_undeclared_entity_is_forbidden_not_empty(self):
        self.write_config({'business_graph': {'entities': {
            'product': self.graph['entities']['product']}, 'conflict_policy': 'report'}})
        with self.assertRaises(Forbidden):
            self.call('graph.entity', {'entity': 'customer', 'id': 'c-1'})
        self.write_config()
# ---------------------------------------------------------------- authority

    def test_graph_tool_must_be_held_by_the_agent(self):
        self.policy = {**POLICY, 'tools': ['connectors.read', 'sheets.rows']}
        with self.assertRaises(Forbidden):
            self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'})

    def test_missing_source_tool_denies_before_any_provider_read(self):
        self.policy = {**POLICY, 'tools': [*GRAPH_TOOLS, 'connectors.read']}
        transport = RecordingTransport({'values': [['SKU', 'Narx']]})
        with self.assertRaises(Forbidden) as caught:
            self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'}, transport)
        self.assertIn('sheets.rows', str(caught.exception))
        # No authority for every source means no source is touched at all.
        self.assertEqual([], transport.calls)

    def test_disallowed_connection_denies_before_any_provider_read(self):
        self.policy = {**POLICY, 'allowed_connections': ['erp']}
        transport = RecordingTransport({'values': [['SKU', 'Narx']]})
        with self.assertRaises(Forbidden):
            self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'}, transport)
        self.assertEqual([], transport.calls)

    def test_a_sheets_source_connection_is_gated_too(self):
        """A sheets source carries its connection in its register, not in args.

        Regression guard: the preflight used to gate connections only for
        ``connectors.read``/``database.read``. Every sheets-backed source was
        therefore unchecked, so dropping the register's connection from the
        policy still read the sheet. The scope must be proven before any provider
        I/O for *every* source, whichever way it reaches a connection.
        """
        self.policy = {**POLICY, 'allowed_connections': ['erp']}
        transport = RecordingTransport({'values': [['SKU', 'Narx']]})
        with self.assertRaises(Forbidden) as caught:
            self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'}, transport)
        self.assertIn('finance', str(caught.exception))
        self.assertEqual([], transport.calls)

    def test_an_unresolvable_register_is_not_a_way_past_the_tool_check(self):
        """If no connection can be attributed, the tool check still stands.

        The register lookup is best-effort: an entry that cannot be read must
        leave the source gated by its tool, never silently authorised. The sheet
        is reached but refused for its tool, and no provider is touched.
        """
        self.registers = {}
        self.write_config()
        self.policy = {**POLICY, 'allowed_connections': ['erp', 'google'],
                       'tools': [*GRAPH_TOOLS, 'connectors.read']}
        transport = RecordingTransport({'values': [['SKU', 'Narx']]})
        with self.assertRaises(Forbidden) as caught:
            self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'}, transport)
        self.assertIn('sheets.rows', str(caught.exception))
        self.assertEqual([], transport.calls)

    def test_undeclared_attribute_is_forbidden(self):
        for tool, args in (('graph.search', {'entity': 'product', 'attribute': 'balance',
                                             'equals': '1'}),
                           ('graph.explain', {'entity': 'product', 'id': 'SKU-1042',
                                              'attribute': 'balance'})):
            with self.subTest(tool=tool), self.assertRaises(Forbidden):
                self.call(tool, args)

    # --------------------------------------------------------------- resolution

    def test_entity_view_carries_value_source_and_observed(self):
        result = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                           RecordingTransport({'values': [['SKU', 'Narx', 'Foyda'],
                                                          ['SKU-1042', 450000, 140000]]}))
        self.assertEqual('SKU-1042', result['id'])
        self.assertTrue(result['complete'])
        stock = result['attributes']['stock']
        self.assertEqual(3, stock['selected']['value'])
        self.assertEqual('erp', stock['selected']['source'])
        self.assertIsInstance(result['attributes']['stock']['values'][0]['observed'], float)
        self.assertFalse(stock['conflict'])

    def test_conflict_is_reported_and_nothing_is_selected_under_report(self):
        result = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                           RecordingTransport({'values': [['SKU', 'Narx'],
                                                          ['SKU-1042', 462000]]}))
        price = result['attributes']['price']
        self.assertTrue(price['conflict'])
        self.assertIsNone(price['selected'])
        self.assertEqual([450000, 462000], [item['value'] for item in price['values']])
        self.assertEqual([{'attribute': 'price', 'values': [450000, 462000],
                           'sources': ['erp', 'finance'], 'resolution': 'report',
                           'selected_source': None}], result['conflicts'])
        self.assertEqual('report', result['conflict_policy'])

    def test_primary_wins_selects_the_declared_order_and_still_reports(self):
        self.graph = {**self.graph, 'conflict_policy': 'primary_wins'}
        self.write_config()
        result = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                           RecordingTransport({'values': [['SKU', 'Narx'],
                                                          ['SKU-1042', 462000]]}))
        price = result['attributes']['price']
        self.assertEqual({'value': 450000, 'source': 'erp'}, price['selected'])
        self.assertTrue(price['conflict'], 'a selected value must not hide the conflict')
        self.assertEqual('erp', result['conflicts'][0]['selected_source'])

    def test_a_numeric_string_is_not_a_conflict_but_different_text_is(self):
        # A database returns 450000 and a spreadsheet returns '450000'; calling that
        # a price conflict would be noise. Genuinely different text still conflicts.
        same = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                         RecordingTransport({'values': [['SKU', 'Narx'],
                                                        ['SKU-1042', '450000']]}))
        self.assertFalse(same['attributes']['price']['conflict'])
        different = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                              RecordingTransport({'values': [['SKU', 'Narx'],
                                                             ['SKU-1042', 462000]]}))
        self.assertTrue(different['attributes']['price']['conflict'])

    def test_canonical_is_case_folded_for_text_only(self):
        self.assertEqual(canonical(450000), canonical('450000'))
        self.assertEqual(canonical('Ali Valiyev'), canonical('ali valiyev'))
        self.assertNotEqual(canonical(450000), canonical('450001'))

    def test_ids_are_compared_as_strings(self):
        # The connector returns an INTEGER id while the caller passes a string; the two
        # must match, otherwise every row looks absent.
        db = sqlite3.connect(self.erp)
        try:
            db.executescript("CREATE TABLE orders(id INTEGER, total INTEGER);"
                             "INSERT INTO orders VALUES(1042,900000);")
            db.commit()
        finally:
            db.close()
        self.connections['erp']['tables']['orders'] = ['id', 'total']
        entity = json.loads(json.dumps(self.graph['entities']['product']))
        entity['sources']['erp']['key'] = 'id'
        entity['sources']['erp']['args'] = {'connection': 'erp', 'table': 'orders',
                                           'columns': ['id', 'total']}
        entity['sources']['erp']['map'] = {'total': 'total'}
        entity['sources']['finance']['key'] = 'ID'
        self.write_config({'connections': self.connections, 'sheets_registers': self.registers,
                           'business_graph': {'conflict_policy': 'report',
                                              'entities': {'order': entity}}})
        result = self.call('graph.entity', {'entity': 'order', 'id': '1042'},
                           RecordingTransport({'values': [['ID'], ['1042']]}))
        self.assertEqual(900000, result['attributes']['total']['selected']['value'])
        empty = self.call('graph.entity', {'entity': 'order', 'id': '9999'},
                          RecordingTransport({'values': [['ID'], ['9999']]}))
        self.assertEqual({}, empty['attributes'])
        self.write_config()

    def test_ids_of_different_types_do_not_share_a_key(self):
        # 1042 and '1042' are one row, but a float 1042.5 must not silently collapse
        # into the integer key space either.
        self.assertNotEqual(canonical('1042'), canonical('1042.5'))

    def test_rows_without_a_key_are_skipped_not_matched(self):
        result = self.call('graph.entity', {'entity': 'product', 'id': ''},
                           RecordingTransport({'values': [['SKU'], ['']]}))
        self.assertEqual(0, result['sources'][1]['matched'])
        self.assertEqual(1, result['sources'][1]['rows'])

    def test_source_status_reports_rows_and_matched(self):
        result = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                           RecordingTransport({'values': [['SKU', 'Narx'],
                                                          ['SKU-1042', 450000]]}))
        self.assertEqual([{'source': 'erp', 'rows': 3, 'matched': 1, 'read': True},
                          {'source': 'finance', 'rows': 1, 'matched': 1, 'read': True}],
                         result['sources'])

    # ----------------------------------------------------------- failure honesty

    def test_a_failing_source_is_named_and_never_reads_as_absent_data(self):
        def broken(url, token):
            raise OSError('provider unreachable')

        engine = self.build()
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get', side_effect=broken):
                result = build_registry().get('graph.entity').handler(
                    engine, TENANT, AGENT, {'entity': 'product', 'id': 'SKU-1042'}, 's1')
        self.assertFalse(result['complete'])
        # Only the exception class name is recorded. The provider message can echo a
        # range, a spreadsheet id or an internal URL, so it never reaches the model.
        self.assertEqual([{'source': 'finance', 'error': 'OSError'}], result['source_errors'])
        # The ERP value is still reported, and the caller can see which source is missing.
        self.assertEqual(450000, result['attributes']['price']['selected']['value'])
        self.assertEqual([{'source': 'erp', 'rows': 3, 'matched': 1, 'read': True},
                          {'source': 'finance', 'rows': 0, 'matched': 0, 'read': False}],
                         result['sources'])

    def test_a_transport_error_is_recorded_and_not_swallowed_into_a_value(self):
        transport = RecordingTransport()
        transport.payload = {'values': 'not-a-matrix'}
        result = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                           transport)
        self.assertFalse(result['complete'])
        self.assertEqual('SheetsError', result['source_errors'][0]['error'])

    def test_authority_failure_is_raised_not_recorded(self):
        # A denied connection is a policy answer, not a provider outage: it must not be
        # softened into a partial read carrying the other sources' values.
        self.policy = {**POLICY, 'allowed_connections': ['google']}
        with self.assertRaises(Forbidden):
            self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'})

    def test_graph_error_is_a_runtime_error(self):
        self.assertTrue(issubclass(BusinessGraphError, RuntimeError))
# ------------------------------------------------- search / conflicts / explain

    MIXED = {'values': [['SKU', 'Narx', 'Foyda'],
                        ['SKU-1042', 462000, 140000],
                        ['SKU-77', 99000, 20000]]}

    def _seed_stock_zero_products(self, count):
        """Put `count` products with stock 0 into the ERP table that feeds search.

        `search` reads its identifiers from the configured connection, not from a
        scripted Sheets transport, so a fixture built out of a transport cannot
        change how many products match. This seeds the real table instead, which
        is the only way to make the population exceed a limit.
        """
        db = sqlite3.connect(self.erp)
        try:
            db.execute("DELETE FROM products WHERE sku LIKE 'CUT-%'")
            db.executemany('INSERT INTO products VALUES(?,?,?)',
                           [(f'CUT-{i}', 1000 + i, 0) for i in range(count)])
            db.commit()
        finally:
            db.close()

    def test_search_reports_a_cut_when_a_match_is_actually_left_out(self):
        """The cut side of the boundary, which MIXED cannot reach.

        `test_search_truncation_is_true_only_when_a_match_was_left_out` measures
        the exact-fit side: the seeded ERP table holds exactly one product with
        stock 0, so a limit of 1 returns everything and must not claim a cut. That
        is the side where the correct predicate and the original broken one
        disagree -- but it is only half the story, because a predicate stuck at
        False would pass it. This seeds two more matching products so the cut side
        is measured too.

        The population is MEASURED, not assumed: the base setUp fixture already
        contributes one stock-0 product, so asserting "2" here would be wrong by
        one and would blame the code for the fixture's arithmetic. A first draft of
        this test did exactly that and reported a defect where there was none.
        """
        self._seed_stock_zero_products(2)
        full = self.call('graph.search', {'entity': 'product', 'attribute': 'stock',
                                          'equals': 0, 'limit': 100},
                         RecordingTransport(self.MIXED))
        population = len(full['matches'])
        self.assertEqual(3, population, f'fixture yields {population} stock-0 products')
        self.assertFalse(full['truncated'],
                         'a limit of 100 cannot cut a population of 3')
        for limit in (1, 2, population, population + 1):
            result = self.call('graph.search', {'entity': 'product',
                                                'attribute': 'stock', 'equals': 0,
                                                'limit': limit},
                               RecordingTransport(self.MIXED))
            self.assertEqual(min(population, limit), len(result['matches']))
            self.assertEqual(population > limit, result['truncated'],
                             f'population={population} limit={limit} reported '
                             f'truncated={result["truncated"]}')

    def _seed_conflicting_products(self, count):
        """Seed `count` ERP products whose price will disagree with the sheet.

        A conflict needs one attribute observed from two sources with different
        values. The ERP side comes from the seeded table; the finance side comes
        from the scripted transport, so the transport has to carry the same ids
        with different numbers for the disagreement to be visible.
        """
        db = sqlite3.connect(self.erp)
        try:
            db.execute("DELETE FROM products WHERE sku LIKE 'DIS-%'")
            db.executemany('INSERT INTO products VALUES(?,?,?)',
                           [(f'DIS-{i}', 100000 + i, 5) for i in range(count)])
            db.commit()
        finally:
            db.close()
        return {'values': [['SKU', 'Narx', 'Foyda']] +
                          [[f'DIS-{i}', 200000 + i, 1] for i in range(count)]}

    def test_conflicts_reports_a_cut_when_a_conflict_is_actually_left_out(self):
        """The cut side for `graph.conflicts`, measured against a real population.

        MIXED yields exactly one conflict, so `test_conflicts_truncation_is_false_
        when_the_conflict_list_fits` can only ever exercise the exact fit and
        cannot tell a working predicate from one that is stuck at False.
        """
        transport = self._seed_conflicting_products(3)
        full = self.call('graph.conflicts', {'entity': 'product', 'limit': 50},
                         RecordingTransport(transport))
        population = len(full['conflicts'])
        self.assertEqual(3, population,
                         'the fixture must produce three conflicts to bound')
        for limit in (1, 2, 3, 4):
            result = self.call('graph.conflicts', {'entity': 'product',
                                                   'limit': limit},
                               RecordingTransport(transport))
            self.assertEqual(min(population, limit), len(result['conflicts']))
            self.assertEqual(population > limit, result['truncated'],
                             f'population={population} limit={limit} reported '
                             f'truncated={result["truncated"]}')


    def test_search_matches_a_declared_attribute_value(self):
        result = self.call('graph.search', {'entity': 'product', 'attribute': 'stock',
                                            'equals': 0},
                           RecordingTransport(self.MIXED))
        self.assertEqual(['SKU-77'], [match['id'] for match in result['matches']])
        self.assertEqual('erp', result['matches'][0]['source'])
        self.assertTrue(result['complete'])

    def test_search_is_bounded_and_says_so(self):
        result = self.call('graph.search', {'entity': 'product', 'attribute': 'stock',
                                            'equals': 0, 'limit': 1},
                           RecordingTransport(self.MIXED))
        self.assertLessEqual(len(result['matches']), 1)

    def test_search_truncation_is_true_only_when_a_match_was_left_out(self):
        """An exactly-full result is not a truncated one.

        `truncated` used to be read off `len(matches) >= limit` after the loop, an
        expression satisfied both by a genuinely cut list and by one that ended
        exactly on the bound. They mean opposite things: "ask again with a bigger
        limit" versus "this is everything", and the wrong one sends the operator to
        re-run an identical query. The flag is now set where a further match is
        refused rather than read back off the length, and this measures that from
        the side where the two disagree: only one product carries stock 0, so a
        limit of one answers the question completely and must not claim a cut.
        """
        for limit in (1, 2, 3):
            result = self.call('graph.search', {'entity': 'product',
                                                'attribute': 'stock', 'equals': 0,
                                                'limit': limit},
                               RecordingTransport(self.MIXED))
            self.assertEqual(1, len(result['matches']))
            self.assertFalse(result['truncated'],
                             f'limit={limit} returned every match and claimed a cut')

    def test_conflicts_truncation_is_false_when_the_conflict_list_fits(self):
        result = self.call('graph.conflicts', {'entity': 'product', 'limit': 1},
                           RecordingTransport(self.MIXED))
        self.assertEqual(1, len(result['conflicts']))
        self.assertFalse(result['truncated'])

    def test_timeline_truncation_measures_the_boundary_from_both_sides(self):
        """SKU-1042 has exactly two events in this fixture, which is the whole point.

        A limit of two returns both and is an exact fit, so the flag must be False.
        A limit of one leaves the second event out, so the flag must be True. The
        old predicate returned True for both, and only the second case warrants it.
        """
        for limit, expected in ((1, True), (2, False), (3, False)):
            result = self.call('graph.timeline', {'entity': 'product',
                                                  'id': 'SKU-1042', 'limit': limit},
                               RecordingTransport(self.MIXED))
            self.assertEqual(min(2, limit), len(result['events']))
            self.assertEqual(expected, result['truncated'],
                             f'limit={limit} returned {len(result["events"])} events')

    def test_search_names_a_source_that_could_not_be_read(self):
        engine = self.build()
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=OSError('unreachable')):
                result = build_registry().get('graph.search').handler(
                    engine, TENANT, AGENT,
                    {'entity': 'product', 'attribute': 'stock', 'equals': 0}, 's1')
        # A search whose source failed must not look like a search that found nothing.
        self.assertFalse(result['complete'])
        self.assertEqual([{'source': 'finance', 'error': 'OSError'}], result['source_errors'])

    def test_conflicts_lists_disagreements_across_systems(self):
        result = self.call('graph.conflicts', {'entity': 'product'},
                           RecordingTransport(self.MIXED))
        self.assertEqual(1, len(result['conflicts']))
        conflict = result['conflicts'][0]
        self.assertEqual('SKU-1042', conflict['id'])
        self.assertEqual('price', conflict['attribute'])
        self.assertEqual(['erp', 'finance'], conflict['sources'])
        self.assertIsNone(conflict['selected_source'])
        self.assertEqual('report', conflict['resolution'])
        self.assertGreaterEqual(result['scanned'], 2)

    def test_timeline_preserves_provider_order_and_does_not_claim_chronology(self):
        result = self.call('graph.timeline', {'entity': 'product', 'id': 'SKU-1042'},
                           RecordingTransport(self.MIXED))
        self.assertEqual('provider', result['order'])
        self.assertEqual(['erp', 'finance'], [event['source'] for event in result['events']])
        self.assertEqual([0, 0], [event['position'] for event in result['events']])
        self.assertEqual({'price': 450000, 'stock': 3}, result['events'][0]['values'])
        self.assertEqual({'price': 462000, 'margin': 140000}, result['events'][1]['values'])
        self.assertNotIn('observed', result['events'][0])

    def test_explain_names_the_declared_order_and_the_missing_sources(self):
        result = self.call('graph.explain', {'entity': 'product', 'id': 'SKU-1042',
                                             'attribute': 'price'},
                           RecordingTransport(self.MIXED))
        self.assertEqual(['erp', 'finance'], result['declared_by'])
        self.assertEqual(['erp', 'finance'], result['observed_by'])
        self.assertEqual([], result['missing_from'])
        self.assertEqual(['erp', 'finance'], result['priority'])
        self.assertEqual('report', result['resolution'])
        self.assertIsNone(result['selected'])

    def test_explain_reports_an_attribute_a_source_never_supplied(self):
        payload = {'values': [['SKU', 'Boshqa'], ['SKU-1042', 'x']]}
        result = self.call('graph.explain', {'entity': 'product', 'id': 'SKU-1042',
                                             'attribute': 'price'},
                           RecordingTransport(payload))
        # A blank cell is a declared source with no observation, not a missing source.
        self.assertEqual(['erp'], result['observed_by'])
        self.assertEqual(['finance'], result['missing_from'])
        self.assertEqual({'value': 450000, 'source': 'erp'}, result['selected'])

    # ------------------------------------------------------------------ shape only

    def test_entities_returns_shape_without_connection_range_or_column_details(self):
        result = self.call('graph.entities', {})
        entity = result['entities'][0]
        self.assertEqual('product', entity['entity'])
        self.assertEqual(['margin', 'price', 'stock'], entity['attributes'])
        self.assertEqual(['erp', 'finance'], entity['priority'])
        self.assertEqual({'source': 'erp', 'tool': 'connectors.read'}, entity['sources'][0])
        # The operator's arguments stay in operator configuration.
        self.assertNotIn('args', entity['sources'][0])
        self.assertNotIn(SPREADSHEET, json.dumps(result))
        self.assertNotIn('products', json.dumps(result))
        self.assertEqual('report', result['conflict_policy'])

    # --------------------------------------------------------- provider amortisation

    def test_scanning_tools_read_each_source_once_regardless_of_id_count(self):
        """A per-id re-read would multiply provider cost by the number of ids.

        ``search`` and ``conflicts`` build one view per id. If each view re-read the
        sources, a bounded scan would issue one provider GET per id per source for
        one tool call. The sources must be read once and grouped in memory instead.
        """
        db = sqlite3.connect(self.erp)
        try:
            for index in range(2, 12):
                db.execute("INSERT INTO products VALUES(?,?,?)",
                           (f'SKU-{index}', 1000 * index, index))
            db.commit()
        finally:
            db.close()

        transport = RecordingTransport(self.MIXED)
        result = self.call('graph.conflicts', {'entity': 'product'}, transport)
        self.assertGreaterEqual(result['scanned'], 2)
        # One read of the sheet register, not one per scanned entity.
        self.assertEqual(1, len(transport.calls))

        search_transport = RecordingTransport(self.MIXED)
        self.call('graph.search',
                  {'entity': 'product', 'attribute': 'price', 'equals': 462000},
                  search_transport)
        self.assertEqual(1, len(search_transport.calls))

    def test_a_single_entity_read_still_issues_one_provider_get(self):
        transport = RecordingTransport(self.MIXED)
        self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'}, transport)
        self.assertEqual(1, len(transport.calls))

    def test_search_and_conflicts_agree_with_a_per_id_read(self):
        """The batched path must return exactly what the per-id path returned."""
        expected = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                             RecordingTransport(self.MIXED))
        found = self.call('graph.search',
                          {'entity': 'product', 'attribute': 'price', 'equals': 462000},
                          RecordingTransport(self.MIXED))
        self.assertEqual(['SKU-1042'], [match['id'] for match in found['matches']])
        self.assertEqual(expected['attributes']['price']['conflict'],
                         found['matches'][0]['conflict'])
        self.assertEqual(expected['attributes']['price']['values'][0]['value'],
                         found['matches'][0]['value'])

    def test_conflicts_still_reports_the_same_disagreement_after_batching(self):
        result = self.call('graph.conflicts', {'entity': 'product'},
                           RecordingTransport(self.MIXED))
        price = next(item for item in result['conflicts'] if item['attribute'] == 'price')
        self.assertEqual([450000, 462000], price['values'])
        self.assertEqual(['erp', 'finance'], price['sources'])
        self.assertIsNone(price['selected_source'])

    def test_a_failing_source_is_reported_once_after_batching(self):
        transport = RecordingTransport(self.MIXED)

        def broken(url, token):
            transport.calls.append({'url': url, 'token': token})
            raise OSError('provider unreachable')

        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get', side_effect=broken):
                result = build_registry().get('graph.conflicts').handler(
                    self.build(), TENANT, AGENT, {'entity': 'product'}, 's1')
        # Named once, not once per scanned entity.
        self.assertEqual([{'source': 'finance', 'error': 'OSError'}],
                         result['source_errors'])
        self.assertFalse(result['complete'])

    def test_batched_ids_keep_the_declared_source_priority_order(self):
        """Grouping by id must not reorder ids against the per-source read order."""
        from platform_runtime.business_graph import _collect_all, declaration, identifiers
        payload = {'values': [['SKU', 'Narx'], ['SKU-9999', 777]]}
        transport = RecordingTransport(payload)

        def with_transport(fn):
            with patch('platform_runtime.sheets.configured_manager') as manager:
                manager.return_value.access.return_value.access_token = 'fake-token'
                with patch('platform_runtime.sheets._http_get',
                           side_effect=lambda url, token: transport(url, token)):
                    return fn()

        per_source = with_transport(
            lambda: identifiers(self.build(), TENANT, AGENT, 'product', 's1')[0])
        engine = self.build()
        entry = declaration(TENANT, 'product')
        collected = with_transport(
            lambda: _collect_all(engine, TENANT, AGENT, entry, 's1'))
        batched = with_transport(
            lambda: identifiers(engine, TENANT, AGENT, 'product', 's1',
                                collected=collected)[0])
        self.assertEqual(per_source, batched)
        # Source priority: every erp id before a sheet-only id.
        self.assertEqual(['SKU-1042', 'SKU-77', 'SKU-9999'], batched)


class WaLifecycleGuardTests(GraphTests):
    """PRD section 2.8 asks for ``wa_window_until`` as a graph attribute. It refuses.

    A graph attribute is mechanically a **column of a provider row** -- ``_collect``
    reads ``row.get(field)`` for every mapped attribute. So declaring
    ``wa_window_until`` on the ``customer`` entity would mean "this value lives in a
    cell someone typed", and a hand-typed cell does not update itself when the
    customer writes. That is exactly the defect P8c shipped and P8d closed, one layer
    down: the platform would be replacing a fact it verified (a Meta-signed message
    the inbound block recorded) with a fact a human maintained by hand, and the send
    it then permits is the one that returns error 131047.

    These tests exist so the mistake cannot come back silently. They do not assert
    that WhatsApp does not work -- ``whatsapp.window`` already reports the window and
    its ``source``. They assert that the *graph* adds nothing, and that adding it
    would be a regression rather than a feature.
    """

    def test_wa_window_until_is_not_a_declared_graph_attribute(self):
        """The guard itself: no entity, no source, anywhere, may map it."""
        for entity in entity_names(TENANT):
            entry = declaration(TENANT, entity)
            with self.subTest(entity=entity):
                self.assertNotIn('wa_window_until', entry['attributes'])
                for source_name, source in entry['sources'].items():
                    self.assertNotIn('wa_window_until', source['map'],
                                     f'{entity}.{source_name} maps the window attribute')

    def test_a_window_attribute_would_come_from_the_provider_row(self):
        """An attribute is a copied cell, not a computed value -- measured, not claimed.

        Declaring it and reading it back returns whatever the sheet said, verbatim,
        with no clock consulted anywhere. That is the mechanism by which a stale cell
        becomes a wrong window.
        """
        entity = json.loads(json.dumps(self.graph['entities']['product']))
        entity['sources']['finance']['map']['wa_window_until'] = 'Oyna'
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {'conflict_policy': 'report',
                                              'entities': {'product': entity}}})
        # A value months in the past and one in the future are both returned as-is.
        for cell in ('01.01.2020 10:00', '2099-12-31T23:59:59+05:00'):
            transport = RecordingTransport(
                {'values': [['SKU', 'Oyna'], ['SKU-1042', cell]]})
            view = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                             transport)
            reported = view['attributes'].get('wa_window_until')
            self.assertIsNotNone(reported)
            self.assertEqual(cell, reported['values'][0]['value'])
            self.assertFalse(reported['conflict'])

    def test_a_sheet_window_column_is_only_as_fresh_as_the_person_who_typed_it(self):
        """The P8c defect, reproduced literally through the graph.

        The customer writes; the sheet is not updated; the graph reports the old
        window. Nothing raises, nothing is missing, and the answer is wrong.
        """
        entity = json.loads(json.dumps(self.graph['entities']['product']))
        entity['sources']['finance']['map']['wa_window_until'] = 'Oyna'
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {'conflict_policy': 'report',
                                              'entities': {'product': entity}}})
        transport = RecordingTransport(
            {'values': [['SKU', 'Oyna'], ['SKU-1042', '01.01.2020 10:00']]})
        view = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                         transport)
        self.assertEqual('01.01.2020 10:00',
                         view['attributes']['wa_window_until']['values'][0]['value'])
        # The graph is complete and confident: it cannot tell a stale cell from a
        # current one, which is why the window must be derived where the clock is.
        self.assertTrue(view['complete'])
        self.assertEqual([], view['source_errors'])

    def test_the_graph_cannot_derive_a_window_from_its_own_read_time(self):
        """``observed`` is our local read time, so it must never become a window.

        A graph read is a snapshot; a service window is a function of *now* and the
        customer's last message. Substituting one for the other would let a briefing
        and a send disagree about the same customer.
        """
        transport = RecordingTransport({'values': [['SKU', 'Narx'], ['SKU-1042', 450000]]})
        first = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                          transport)
        self.assertIn('observed', first)
        # observed is present and documents freshness, but it is not a window.
        self.assertNotIn('wa_window_until', first['attributes'])
        self.assertNotIn('window', first['attributes'])

    def test_window_attribute_alone_cannot_open_a_window_in_the_graph(self):
        """A mapped attribute is never consulted by the send gate.

        The graph is read-only, so no graph value can authorize anything. This is the
        property that makes 'just put the window in the graph' sound safer than it is:
        it is not used at all, which means the operator would maintain a column that
        the platform ignores while believing it decides the send.
        """
        entity = json.loads(json.dumps(self.graph['entities']['product']))
        entity['sources']['finance']['map']['wa_window_until'] = 'Oyna'
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {'conflict_policy': 'report',
                                              'entities': {'product': entity}}})
        transport = RecordingTransport(
            {'values': [['SKU', 'Oyna'], ['SKU-1042', '2099-12-31T23:59:59+05:00']]})
        view = self.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'},
                         transport)
        # The graph reports "open in 2099" and the graph tool still holds no write
        # path: no send can be authorized from this value.
        self.assertEqual('2099-12-31T23:59:59+05:00',
                         view['attributes']['wa_window_until']['values'][0]['value'])
        for name in GRAPH_TOOLS:
            self.assertEqual('read', build_registry().get(name).risk)

    def test_whatsapp_window_reports_source_so_the_graph_has_nothing_to_add(self):
        """``whatsapp.window`` already answers with provenance; the graph adds no fact.

        This is the positive half of the guard: the PRD's *goal* -- an operator can see
        which number has an open window and when it closes -- is already met, and met
        from the verified source rather than from a typed cell.
        """
        from platform_runtime.whatsapp import window_state, WINDOW_SECONDS
        sent = 1_789_794_000.0
        opened, closes_at = window_state(sent, now=sent + 60)
        self.assertTrue(opened)
        self.assertEqual(sent + WINDOW_SECONDS, closes_at)
        # The reader exposes outcome and provenance together, which is the whole
        # requirement; a graph attribute could only restate it less reliably.
        import inspect
        from platform_runtime import whatsapp
        source = inspect.getsource(whatsapp.window)
        self.assertIn("'source': sources.get(", source)
        self.assertIn("'closes_at': closes_at", source)


class SourcePriorityTests(GraphTests):
    """PRD 6.3 asks for an operator-declared per-attribute source order.

    The key ``business_graph.source_priority`` was whitelisted in ``graph_config``
    from the beginning -- an unknown key raises, so the whitelist is what told an
    operator the key exists -- and then read by nobody. ``graph_config`` returned
    only ``conflict_policy`` and ``entities``, so every consumer (``resolve``,
    ``search``, ``conflicts``, ``explain``, ``timeline``) was blind to it and the
    declaration configured nothing while appearing to.

    That is the same defect class as the ``wa_window_until`` guards above: a
    documented capability whose implementation is absent. These tests pin the
    behaviour so the key cannot quietly become inert again.
    """

    def test_source_priority_survives_validation(self):
        """A whitelisted key must reach its consumers, not merely avoid an error."""
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {
                               **self.graph,
                               'source_priority': {'price': {'primary': 'erp',
                                                             'fallback': ['finance']}},
                           }})
        declared = graph_config(TENANT)
        self.assertIn('source_priority', declared)
        self.assertEqual(['erp', 'finance'], declared['source_priority']['price'])

    def test_per_attribute_priority_decides_the_winner(self):
        """``price`` from the ERP while ``margin`` comes from the sheet, one view."""
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {
                               **self.graph,
                               'source_priority': {'price': {'primary': 'finance'}},
                           }})
        entity = self.graph['entities']['product']
        # The entity order says erp first; the attribute order says finance first,
        # and the more specific declaration is the one the operator meant.
        from platform_runtime.business_graph import _primary
        winner = _primary(declaration(TENANT, 'product'), 'price',
                          [{'source': 'erp', 'value': 1}, {'source': 'finance', 'value': 2}])
        self.assertEqual('finance', winner)
        self.assertEqual(['erp', 'finance'], entity['priority'])

    def test_an_undeclared_attribute_keeps_the_entity_order(self):
        from platform_runtime.business_graph import _primary
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {
                               **self.graph,
                               'source_priority': {'price': {'primary': 'finance'}},
                           }})
        winner = _primary(declaration(TENANT, 'product'), 'stock',
                          [{'source': 'erp', 'value': 1}, {'source': 'finance', 'value': 2}])
        self.assertEqual('erp', winner)

    def test_explain_names_the_order_that_chose_the_value(self):
        """"Where did this number come from" must answer with the deciding rule."""
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {
                               **self.graph,
                               'source_priority': {'price': {'primary': 'finance',
                                                             'fallback': ['erp']}},
                           }})
        result = self.call('graph.explain', {'entity': 'product', 'id': 'SKU-1042',
                                            'attribute': 'price'})
        self.assertEqual(['finance', 'erp'], result['attribute_priority'])
        self.assertEqual('attribute', result['priority_source'])

    def test_a_declared_primary_without_fallback_still_resolves(self):
        """A primary absent from the observations falls through to the entity order.

        ``{primary: finance}`` names one preferred source, not the only source: if
        the preferred system has no value for this attribute, resolution continues
        into the entity order rather than reporting a missing value. Declaring a
        fallback is how an operator pins the *rest* of the order explicitly.
        """
        from platform_runtime.business_graph import _primary
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {
                               **self.graph,
                               'source_priority': {'price': {'primary': 'finance'}},
                           }})
        entry = declaration(TENANT, 'product')
        self.assertEqual(['finance'], entry['source_priority']['price'])
        self.assertEqual('finance',
                         _primary(entry, 'price',
                                  [{'source': 'erp', 'value': 1},
                                   {'source': 'finance', 'value': 2}]))
        self.assertEqual('erp',
                         _primary(entry, 'price', [{'source': 'erp', 'value': 1}]))

    def test_entity_priority_used_when_no_attribute_declaration_exists(self):
        result = self.call('graph.explain', {'entity': 'product', 'id': 'SKU-1042',
                                            'attribute': 'price'})
        self.assertEqual(['erp', 'finance'], result['attribute_priority'])
        self.assertEqual('entity', result['priority_source'])

    def test_priority_naming_an_undeclared_source_is_refused(self):
        """A typo here would make the whole declaration a silent no-op."""
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {
                               **self.graph,
                               'source_priority': {'price': {'primary': 'ghost'}},
                           }})
        with self.assertRaises(ValueError) as ctx:
            graph_config(TENANT)
        self.assertIn('does not declare', str(ctx.exception))

    def test_priority_for_an_unmapped_attribute_is_refused(self):
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {
                               **self.graph,
                               'source_priority': {'margin_typo': {'primary': 'erp'}},
                           }})
        with self.assertRaises(ValueError) as ctx:
            graph_config(TENANT)
        self.assertIn('no source maps', str(ctx.exception))

    def test_a_bare_string_is_shorthand_for_a_single_primary(self):
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {
                               **self.graph,
                               'source_priority': {'price': 'finance'},
                           }})
        self.assertEqual(['finance'], graph_config(TENANT)['source_priority']['price'])

    def test_entity_table_overrides_the_shared_table(self):
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {
                               **self.graph,
                               'source_priority': {'price': {'primary': 'finance'}},
                               'entities': {
                                   'product': {**self.graph['entities']['product'],
                                               'source_priority': {'price': 'erp'}},
                               },
                           }})
        product = graph_config(TENANT)['entities']['product']
        self.assertEqual(['erp'], product['source_priority']['price'])

    def test_no_declared_priority_is_deterministic_not_json_order(self):
        """JSON objects are unordered; the fallback must not depend on key order.

        The sources are declared non-alphabetically on purpose. If they were
        already alphabetical, this test could not distinguish a deterministic
        order from raw document order.
        """
        sources = {name: dict(self.graph['entities']['product']['sources']['erp'],
                              args={'connection': 'erp', 'table': 'products',
                                    'columns': ['sku', 'price', 'stock']})
                   for name in ('zz_erp', 'aa_finance')}
        self.write_config({'connections': self.connections,
                           'business_graph': {
                               'conflict_policy': 'report',
                               'entities': {'product': {
                                   'identity': 'sku',
                                   'sources': sources,
                               }},
                           }})
        order = graph_config(TENANT)['entities']['product']['order']
        self.assertEqual(['aa_finance', 'zz_erp'], order)
        self.assertNotEqual(list(sources), order)

    def test_explicit_priority_can_outrank_an_alphabetically_earlier_source(self):
        sources = {name: dict(self.graph['entities']['product']['sources']['erp'],
                              args={'connection': 'erp', 'table': 'products',
                                    'columns': ['sku', 'price', 'stock']})
                   for name in ('aa_onec', 'zz_moysklad')}
        self.write_config({'connections': self.connections,
                           'business_graph': {
                               'conflict_policy': 'report',
                               'entities': {'product': {
                                   'identity': 'sku',
                                   'priority': ['zz_moysklad', 'aa_onec'],
                                   'sources': sources,
                               }},
                           }})
        product = graph_config(TENANT)['entities']['product']
        self.assertEqual(['zz_moysklad', 'aa_onec'], product['order'])
        from platform_runtime.business_graph import _primary
        winner = _primary(product, 'price',
                          [{'source': 'aa_onec', 'value': 1},
                           {'source': 'zz_moysklad', 'value': 2}])
        self.assertEqual('zz_moysklad', winner)

    def test_conflict_report_still_names_every_source_under_report_policy(self):
        """A declared priority must not turn ``report`` into a silent selection."""
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {
                               **self.graph,
                               'source_priority': {'price': {'primary': 'erp'}},
                           }})
        result = self.call('graph.explain', {'entity': 'product', 'id': 'SKU-1042',
                                            'attribute': 'price'})
        self.assertEqual('report', result['resolution'])

    def test_entities_tool_shows_the_priority_it_actually_read(self):
        """The declared shape must display what the runtime accepted.

        `source_priority` stayed inert for several versions because nothing
        displayed it: an operator could declare a priority, see no error, and
        never learn that no code read it. A shape report that omits a live key
        cannot tell a working declaration from an ignored one.
        """
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': {
                               **self.graph,
                               'source_priority': {'price': {'primary': 'finance'}},
                           }})
        result = self.call('graph.entities', {})
        self.assertEqual(['finance'], result['source_priority']['price'])
        product = next(e for e in result['entities'] if e['entity'] == 'product')
        self.assertEqual(['finance'], product['source_priority']['price'])

    def test_entities_tool_is_empty_not_lying_when_nothing_is_declared(self):
        """An absent declaration must read as absent, not as a filled-in default."""
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': self.graph})
        result = self.call('graph.entities', {})
        self.assertEqual({}, result['source_priority'])
        product = next(e for e in result['entities'] if e['entity'] == 'product')
        self.assertEqual({}, product['source_priority'])


    def test_two_enormous_values_do_not_collapse_into_one(self):
        """``float()`` returns INFINITY for an out-of-range numeric string, silently.

        ``canonical`` decides whether two sources agree. Two DIFFERENT enormous
        values both canonicalised to ``('n', inf)`` and therefore compared EQUAL --
        a real conflict reported as agreement, which is the one failure this module
        exists to prevent. An out-of-range INTEGER was worse still: it raised
        ``OverflowError`` straight out of a read. Both fall back to the text form,
        and the two forms agree with each other.
        """
        huge_one, huge_two = '1' + '0' * 400, '2' + '0' * 400
        self.assertNotEqual(canonical(huge_one), canonical(huge_two))
        self.assertEqual(canonical(10 ** 400), canonical(huge_one))
        self.assertEqual(('n', 450000.0), canonical(450000))
        self.assertEqual(canonical(450000), canonical('450000'))
