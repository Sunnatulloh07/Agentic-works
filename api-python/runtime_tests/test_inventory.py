"""Inventory contract tests (PRD v0.4 P4). No live provider call.

The behaviours that matter, in the customer's own terms:

* a price arrives with the system that produced it, because a number with no
  provenance is a number a salesperson cannot defend;
* when MoySklad, 1C and the finance sheet disagree, the disagreement is
  reported -- this is the *"sotuvchi narxni olib yurmasin"* requirement, and
  silently picking one is the defect it exists to prevent;
* a blank price, a zero price and an unreadable price are three different facts;
* a margin is computed from two readable inputs or refused by name, never
  assembled from one real number and one default;
* stock is a quantity, never an availability claim;
* nothing here can write, and no figure is attributed to a person.

The sources are the same scripted transports the Business Graph tests use, so
these tests exercise the real dispatch path through ``connectors.read`` and
``sheets.rows`` rather than a rehearsal.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime import inventory
from platform_runtime.business_graph import graph_config
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry

TENANT = 't_inventory'
AGENT = 'sales.bot'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
INVENTORY_TOOLS = ['inventory.product', 'inventory.stock', 'inventory.price',
                   'inventory.margin']

POLICY = {
    'tools': [*INVENTORY_TOOLS, 'connectors.read', 'sheets.rows'],
    'allowed_connections': ['erp', 'moy', 'google'],
    'ladder': 'human_assisted',
}


class RecordingTransport:
    def __init__(self, payload=None):
        self.payload = {'values': []} if payload is None else payload
        self.calls = []

    def __call__(self, url, token):
        self.calls.append({'url': url, 'token': token})
        return self.payload


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # The ERP holds price, cost and stock. The MoySklad-shaped export holds a
        # DIFFERENT price, which is the whole point: a real customer runs both.
        self.erp = self.root / 'erp.db'
        db = sqlite3.connect(self.erp)
        try:
            db.executescript(
                "CREATE TABLE products(sku TEXT, name TEXT, price INTEGER, cost INTEGER,"
                " stock INTEGER, unit TEXT);"
                "INSERT INTO products VALUES('SKU-1042','Oyna',450000,310000,3,'dona');"
                "INSERT INTO products VALUES('SKU-77','Eshik',99000,60000,0,'dona');"
                "INSERT INTO products VALUES('SKU-BLANK','Blank narx',NULL,NULL,5,'dona');"
                "INSERT INTO products VALUES('SKU-JUNK','Buzuq',450000,'narx yoq',2,'dona');"
                "INSERT INTO products VALUES('', 'Identifikatorsiz',1,1,1,'dona');")
            db.commit()
        finally:
            db.close()

        self.moy = self.root / 'moy.db'
        db = sqlite3.connect(self.moy)
        try:
            db.executescript(
                "CREATE TABLE goods(sku TEXT, price INTEGER, stock INTEGER);"
                "INSERT INTO goods VALUES('SKU-1042',455000,3);"
                "INSERT INTO goods VALUES('SKU-77',99000,0);")
            db.commit()
        finally:
            db.close()

        self.connections = {
            'erp': {'driver': 'sqlite_readonly', 'path': str(self.erp),
                    'tables': {'products': ['sku', 'name', 'price', 'cost', 'stock',
                                            'unit']}},
            'moy': {'driver': 'sqlite_readonly', 'path': str(self.moy),
                    'tables': {'goods': ['sku', 'price', 'stock']}},
            'google': {},
        }
        self.registers = {
            'finance': {'connection': 'google', 'spreadsheet_id': SPREADSHEET,
                        'ranges': {'prices': 'Narx!A1:C'}, 'max_rows': 50},
        }
        self.graph = {
            'conflict_policy': 'report',
            'source_priority': {'price': {'primary': 'moy', 'fallback': ['erp']}},
            'entities': {
                'product': {
                    'identity': 'sku',
                    'sources': {
                        'erp': {'tool': 'connectors.read', 'key': 'sku',
                                'args': {'connection': 'erp', 'table': 'products',
                                         'columns': ['sku', 'name', 'price', 'cost',
                                                     'stock', 'unit']},
                                'map': {'name': 'name', 'price': 'price', 'cost': 'cost',
                                        'stock': 'stock', 'unit': 'unit'}},
                        'moy': {'tool': 'connectors.read', 'key': 'sku',
                                'args': {'connection': 'moy', 'table': 'goods',
                                         'columns': ['sku', 'price', 'stock']},
                                'map': {'price': 'price', 'stock': 'stock'}},
                        'finance': {'tool': 'sheets.rows', 'key': 'SKU',
                                    'args': {'register': 'finance', 'range': 'prices'},
                                    'map': {'price': 'Narx'}},
                    },
                },
            },
        }
        self.inventory = {
            'entity': 'product',
            'identity': 'sku',
            'attributes': {'price': 'price', 'cost': 'cost', 'stock': 'stock',
                           'name': 'name', 'unit': 'unit'},
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
            'inventory': self.inventory,
        }}, ensure_ascii=False), encoding='utf-8')

    def build(self):
        return Engine(self.root / 'platform.db', build_registry(),
                      lambda t, a: self.policy)

    def call(self, name, args, transport=None, policy=None, tenant=TENANT,
             agent=AGENT):
        transport = transport or RecordingTransport()
        engine = Engine(self.root / 'platform.db', build_registry(),
                        lambda t, a: policy or self.policy)
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: transport(url, token)):
                result = build_registry().get(name).handler(engine, tenant, agent,
                                                            args, 's1')
        self.transport = transport
        return result

    # ------------------------------------------------------------ registration

    def test_every_inventory_tool_is_read_only(self):
        registry = build_registry()
        for name in INVENTORY_TOOLS:
            with self.subTest(tool=name):
                self.assertEqual('read', registry.get(name).risk)

    def test_no_inventory_tool_is_external(self):
        """These tools add no transport, so nothing here reaches a provider itself."""
        registry = build_registry()
        for name in INVENTORY_TOOLS:
            with self.subTest(tool=name):
                self.assertFalse(getattr(registry.get(name), 'external', False))

    def test_config_is_empty_not_an_error_when_absent(self):
        """An absent block is an empty declaration, exactly as the graph treats one."""
        self.write_config({'connections': self.connections})
        settings = inventory.inventory_config(TENANT)
        self.assertEqual('', settings['entity'])
        self.assertEqual({}, settings['attributes'])
        self.assertEqual('sku', settings['identity'])

    def test_an_unknown_tenant_is_refused_by_the_platform_not_silently_empty(self):
        """A tenant with no integration config at all must not read as 'no inventory'.

        "This tenant is not configured" and "this tenant has no inventory" are
        different answers, and the platform already refuses the first rather than
        letting it degrade into the second.
        """
        with self.assertRaises(RuntimeError):
            inventory.inventory_config('a-tenant-with-no-config')

    def test_unsupported_config_keys_are_refused(self):
        for payload in ({'extra': 1}, {'entity': 'product', 'typo': 'x'}):
            self.write_config({'connections': self.connections,
                               'inventory': payload})
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    inventory.inventory_config(TENANT)

    def test_an_unsupported_role_is_refused(self):
        """A role is a meaning. An unknown one would silently configure nothing."""
        self.write_config({'connections': self.connections,
                           'inventory': {'entity': 'product',
                                         'attributes': {'pric': 'price'}}})
        with self.assertRaises(ValueError) as ctx:
            inventory.inventory_config(TENANT)
        self.assertIn('unsupported role', str(ctx.exception))

    def test_inventory_without_an_entity_is_refused_at_read_time(self):
        self.write_config({'connections': self.connections,
                           'inventory': {'attributes': {'price': 'price'}}})
        with self.assertRaises(Forbidden) as ctx:
            self.call('inventory.product', {'product_id': 'SKU-1042'})
        self.assertIn('not configured', str(ctx.exception))

    # -------------------------------------------------------------- the graph

    def test_inventory_grants_no_authority_of_its_own(self):
        """The graph tool must be held; inventory is not a way around it."""
        policy = {'tools': [*INVENTORY_TOOLS], 'allowed_connections': ['erp', 'moy'],
                  'ladder': 'human_led'}
        with self.assertRaises(Forbidden):
            self.call('inventory.product', {'product_id': 'SKU-1042'}, policy=policy)

    def test_a_connection_outside_the_allowlist_is_refused(self):
        policy = {'tools': [*INVENTORY_TOOLS, 'connectors.read', 'sheets.rows'],
                  'allowed_connections': ['nothing'], 'ladder': 'human_led'}
        with self.assertRaises(Forbidden):
            self.call('inventory.product', {'product_id': 'SKU-1042'}, policy=policy)

    def test_an_undeclared_entity_is_refused(self):
        self.write_config({'connections': self.connections,
                           'business_graph': self.graph,
                           'inventory': {'entity': 'widget',
                                         'attributes': {'price': 'price'}}})
        with self.assertRaises(Forbidden):
            self.call('inventory.product', {'product_id': 'SKU-1042'})

    # ------------------------------------------------------------ the price

    def test_price_carries_the_source_that_produced_it(self):
        result = self.call('inventory.price', {'product_id': 'SKU-1042'})
        price = result['items'][0]['price']
        self.assertTrue(price['declared'])
        for observation in price['values']:
            self.assertTrue(observation['source'],
                            'a price with no source cannot be defended')
        sources = {item['source'] for item in price['values']}
        self.assertEqual({'erp', 'moy'}, sources)

    def test_a_conflict_is_reported_not_silently_resolved(self):
        """The customer named this problem twice; both numbers must be visible."""
        result = self.call('inventory.price', {'product_id': 'SKU-1042'})
        price = result['items'][0]['price']
        self.assertTrue(price['conflict'],
                        'MoySklad 455000 vs ERP 450000 is a conflict')
        numbers = sorted(item['number'] for item in price['values'])
        self.assertEqual([450000.0, 455000.0], numbers)
        conflicts = result['items'][0]['conflicts']
        self.assertEqual('price', conflicts[0]['attribute'])
        self.assertEqual('report', conflicts[0]['resolution'])

    def test_report_policy_selects_nothing(self):
        result = self.call('inventory.price', {'product_id': 'SKU-1042'})
        self.assertIsNone(result['items'][0]['price']['selected'],
                          'report must pick no value at all')
        self.assertEqual('report', result['conflict_policy'])

    def test_primary_wins_honours_the_declared_priority(self):
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'inventory': self.inventory,
                           'business_graph': {**self.graph,
                                              'conflict_policy': 'primary_wins'}})
        result = self.call('inventory.price', {'product_id': 'SKU-1042'})
        selected = result['items'][0]['price']['selected']
        self.assertEqual('moy', selected['source'])
        self.assertEqual(455000.0, selected['number'])
        # The conflict is STILL reported under primary_wins, and names the winner.
        self.assertTrue(result['items'][0]['price']['conflict'])
        self.assertEqual('moy', result['items'][0]['conflicts'][0]['selected_source'])

    def test_a_declared_fallback_answers_when_the_primary_is_absent(self):
        """SKU-1042 is in both systems; SKU-BLANK is in neither."""
        result = self.call('inventory.price', {'product_id': 'SKU-BLANK'})
        price = result['items'][0]['price']
        self.assertTrue(price['missing'])
        self.assertEqual([], price['values'])

    def test_the_fallback_is_what_resolves_when_the_primary_has_no_row(self):
        """A product held only in the ERP still prices, via the declared fallback.

        This is the fallback half of the PRD's ``{primary, fallback}`` shape: the
        MoySklad export does not carry SKU-BLANK, so resolution must continue into
        the declared fallback rather than reporting no price.
        """
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'inventory': self.inventory,
                           'business_graph': {
                               **self.graph,
                               'conflict_policy': 'primary_wins',
                               'entities': {'product': {
                                   **self.graph['entities']['product'],
                                   'sources': {
                                       name: source for name, source
                                       in self.graph['entities']['product']
                                       ['sources'].items() if name != 'finance'}},
                               }}})
        result = self.call('inventory.price', {'product_id': 'SKU-77'})
        price = result['items'][0]['price']
        self.assertEqual(['erp', 'moy'], sorted(item['source']
                                               for item in price['values']))
        self.assertEqual('erp', price['selected']['source'])

    # ----------------------------------------------------- blank vs zero

    def test_a_blank_price_is_not_a_zero_price(self):
        """A free product and an unread product must not look the same."""
        result = self.call('inventory.price', {'product_id': 'SKU-BLANK'})
        price = result['items'][0]['price']
        self.assertTrue(price['missing'])
        self.assertIsNone(price['selected'])
        self.assertEqual(0, price['unreadable'])

    def test_an_unreadable_price_is_counted_not_coerced(self):
        """The junk cell is in the COST column of this fixture, by design.

        Putting it in a column the ERP does not even expose would test nothing:
        the interesting case is a value that *was read* and cannot be used.
        """
        result = self.call('inventory.product', {'product_id': 'SKU-JUNK'})
        cost = result['cost']
        self.assertFalse(cost['missing'])
        self.assertEqual(1, cost['unreadable'])
        self.assertIsNone(cost['selected']['number'],
                          'a non-numeric cell must not become zero')
        self.assertEqual(450000.0, result['price']['selected']['number'])

    def test_a_real_zero_price_is_reported_as_zero(self):
        """The other direction: a genuine zero must survive as a zero."""
        db = sqlite3.connect(self.erp)
        try:
            db.execute("INSERT INTO products VALUES('SKU-FREE','Bepul',0,0,1,'dona')")
            db.commit()
        finally:
            db.close()
        result = self.call('inventory.price', {'product_id': 'SKU-FREE'})
        price = result['items'][0]['price']
        self.assertEqual(0, price['unreadable'])
        self.assertEqual(0.0, price['values'][0]['number'])

    def test_decimal_comma_is_parsed(self):
        self.assertEqual(450000.0, inventory._number('450000'))
        self.assertEqual(450000.5, inventory._number('450000,5'))

    def test_a_non_finite_cell_is_not_a_number(self):
        """inf and nan are real floats; an infinite stock would poison a total."""
        for value in ('inf', '-inf', 'nan', float('inf'), float('-inf'), float('nan')):
            with self.subTest(value=value):
                self.assertIsNone(inventory._number(value))
        self.assertIsNone(inventory._number(True), 'a boolean is not a quantity')

    # ------------------------------------------------------------- the stock

    def test_stock_is_a_quantity_never_an_availability_claim(self):
        result = self.call('inventory.stock', {'product_id': 'SKU-77'})
        item = result['items'][0]
        self.assertEqual(0, item['stock']['selected']['number'])
        for key in ('available', 'in_stock', 'can_sell', 'reservable'):
            self.assertNotIn(key, item)
        self.assertIn('sotuvga tayyor', result['note'])

    def test_a_catalogue_read_is_bounded_and_reports_truncation(self):
        result = self.call('inventory.stock', {'limit': 2})
        self.assertEqual(2, result['item_count'])
        self.assertTrue(result['truncated'])
        self.assertGreater(result['scanned'], 2)

    def test_an_untruncated_read_does_not_claim_truncation(self):
        result = self.call('inventory.stock', {'limit': 50})
        self.assertFalse(result['truncated'])
        self.assertEqual([], result['sources_truncated'])

    def _seed_padding(self, count, late=''):
        db = sqlite3.connect(self.erp)
        try:
            db.executemany('INSERT INTO products VALUES(?,?,?,?,?,?)',
                           [(f'PAD-{i:03d}', 'p', 1, 1, 1, 'dona') for i in range(count)])
            if late:
                db.execute('INSERT INTO products VALUES(?,?,?,?,?,?)',
                           (late, 'Kech', 500, 400, 9, 'dona'))
            db.commit()
        finally:
            db.close()

    def test_a_product_past_the_source_ceiling_is_not_reported_as_absent(self):
        """The ERP table is read without a where-filter and connectors.read stops at
        50 rows, so a product past it had no price -- `blank`, `complete: true`."""
        self._seed_padding(60, late='SKU-LATE')
        result = self.call('inventory.product', {'product_id': 'SKU-LATE'})
        self.assertTrue(result['truncated'])
        self.assertEqual(['erp'], result['sources_truncated'])

    def test_a_catalogue_read_past_the_source_ceiling_says_so(self):
        self._seed_padding(60)
        for name in ('inventory.stock', 'inventory.price', 'inventory.margin'):
            with self.subTest(tool=name):
                result = self.call(name, {'limit': 100})
                self.assertEqual(['erp'], result['sources_truncated'])
                self.assertTrue(result['truncated'])
        # One product: a source at its ceiling matters only when the id was NOT
        # found in it. SKU-77 is in the first rows, so its answer is whole.
        for name in ('inventory.stock', 'inventory.price', 'inventory.margin'):
            with self.subTest(tool=name, one='SKU-77'):
                result = self.call(name, {'product_id': 'SKU-77'})
                self.assertEqual([], result['sources_truncated'])
                self.assertFalse(result['truncated'])
            with self.subTest(tool=name, one='SKU-NOWHERE'):
                result = self.call(name, {'product_id': 'SKU-NOWHERE'})
                self.assertEqual(['erp'], result['sources_truncated'])
                self.assertTrue(result['truncated'])

    # ------------------------------------------------------------ the margin

    def test_margin_is_computed_from_two_readable_inputs(self):
        # The fixture's default policy is `report`, and the price conflicts, so
        # the margin is refused -- for a reason that is NOT missing data. This is
        # the distinction the block was corrected to make.
        result = self.call('inventory.margin', {'product_id': 'SKU-1042'})
        item = result['items'][0]
        self.assertFalse(item['margin_computable'])
        self.assertEqual([], item['not_computable'])
        self.assertEqual(['conflict'], item['margin_blocked_by_conflict'])
        self.assertIsNone(item['margin'])

    def test_a_margin_with_no_cost_is_refused_by_name(self):
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': self.graph,
                           'inventory': {'entity': 'product',
                                         'attributes': {'price': 'price'}}})
        result = self.call('inventory.margin', {'product_id': 'SKU-1042'})
        item = result['items'][0]
        self.assertIsNone(item['margin'])
        self.assertFalse(item['margin_computable'])
        self.assertIn('not_declared', result['not_computable'])

    def test_a_conflict_is_not_reported_as_missing_data(self):
        """A refused selection and an absent column send an operator to two
        different places, so they must not arrive as one list."""
        result = self.call('inventory.margin', {'product_id': 'SKU-1042'})
        self.assertEqual([], result['not_computable'])
        self.assertIn('conflict', result['margin_blocked_by_conflict'])

    def test_declaring_a_priority_computes_the_margin_that_report_refused(self):
        """The fix is configuration, not more data -- and it works."""
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'inventory': self.inventory,
                           'business_graph': {**self.graph,
                                              'conflict_policy': 'primary_wins'}})
        result = self.call('inventory.margin', {'product_id': 'SKU-1042'})
        item = result['items'][0]
        self.assertTrue(item['margin_computable'])
        self.assertEqual(455000.0 - 310000.0, item['margin'])
        self.assertEqual([], item['not_computable'])
        self.assertEqual([], item['margin_blocked_by_conflict'])

    def test_an_undeclared_cost_column_produces_no_margin_anywhere(self):
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'business_graph': self.graph,
                           'inventory': {'entity': 'product',
                                         'attributes': {'price': 'price'}}})
        result = self.call('inventory.margin', {})
        for item in result['items']:
            self.assertIsNone(item['margin'])
            self.assertIn('not_declared', item['not_computable'])

    def test_a_product_read_leaves_margin_none_when_cost_is_unreadable(self):
        result = self.call('inventory.product', {'product_id': 'SKU-JUNK'})
        self.assertIsNone(result['margin'])
        self.assertFalse(result['margin_computable'])
        self.assertIn('unread', result['not_computable'])

    # ------------------------------------------------------------ naming

    def test_name_and_unit_survive_the_read(self):
        result = self.call('inventory.product', {'product_id': 'SKU-1042'})
        self.assertEqual('Oyna', result['name'])
        self.assertEqual('dona', result['unit'])

    def test_an_absent_product_returns_nothing_rather_than_failing(self):
        result = self.call('inventory.product', {'product_id': 'SKU-NOPE'})
        self.assertTrue(result['price']['missing'])
        self.assertEqual([], result['price']['values'])

    def test_an_invalid_identity_is_refused(self):
        for bad in ('', 'a b', 'x' * 200, None):
            with self.subTest(bad=bad):
                with self.assertRaises((ValueError, Forbidden)):
                    self.call('inventory.product', {'product_id': bad})

    # ------------------------------------------------------- source failure

    def test_a_failed_source_is_not_a_missing_value(self):
        """A provider outage must not look like "this product has no price"."""
        transport = RecordingTransport({'values': []})

        def exploding(url, token):
            if 'sheets.googleapis' in url:
                raise RuntimeError('provider down')
            return transport(url, token)

        engine = Engine(self.root / 'platform.db', build_registry(),
                        lambda t, a: self.policy)
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get', side_effect=exploding):
                result = build_registry().get('inventory.price').handler(
                    engine, TENANT, AGENT, {'product_id': 'SKU-1042'}, 's1')
        self.assertFalse(result['complete'])
        self.assertTrue(result['source_errors'],
                        'an unread source must be named, not rendered as no value')
        self.assertEqual('finance', result['source_errors'][0]['source'])

    # ------------------------------------------------------------ authority

    def test_every_read_carries_the_ladder_it_was_read_under(self):
        result = self.call('inventory.product', {'product_id': 'SKU-1042'})
        self.assertEqual('human_assisted', result['authority']['ladder'])
        self.assertEqual(AGENT, result['authority']['agent'])

    def test_no_figure_is_attributed_to_a_person(self):
        """A per-seller price is a sanction waiting to happen."""
        result = self.call('inventory.product', {'product_id': 'SKU-1042'})
        blob = json.dumps(result, ensure_ascii=False).lower()
        for key in ('seller', 'salesperson', 'operator', 'employee', 'manager_id',
                    'user_id'):
            self.assertNotIn(f'"{key}"', blob)

    def test_the_source_priority_declaration_is_honoured_end_to_end(self):
        """The fix this block shipped: a declared order must change the answer."""
        declared = graph_config(TENANT)
        self.assertEqual(['moy', 'erp'], declared['source_priority']['price'])
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'inventory': self.inventory,
                           'business_graph': {**self.graph,
                                              'conflict_policy': 'primary_wins'}})
        result = self.call('inventory.price', {'product_id': 'SKU-1042'})
        self.assertEqual('moy', result['items'][0]['price']['selected']['source'])


class PriceReasonTests(InventoryTests):
    """The three defects the second audit pass found in this module.

    All three are the same shape: a key or a bucket that was correct for the
    ``report`` policy and quietly wrong for ``primary_wins``, plus one path that
    returned a different set of keys than its sibling for the same question.
    """

    def _unreadable_primary(self):
        """A register whose DECLARED PRIMARY holds a junk price cell.

        The ERP holds a perfectly good price for the same SKU. Under
        ``primary_wins`` the graph honours the declared priority and selects the
        junk cell, so the platform is left holding a readable value it was told
        not to use.
        """
        db = sqlite3.connect(self.moy)
        try:
            db.executescript(
                "UPDATE goods SET price = 'narx yoq' WHERE sku = 'SKU-1042';")
            db.commit()
        finally:
            db.close()
        self.write_config({'connections': self.connections,
                           'sheets_registers': self.registers,
                           'inventory': self.inventory,
                           'business_graph': {**self.graph,
                                              'conflict_policy': 'primary_wins'}})

    def test_a_readable_value_exists_but_the_priority_chose_an_unreadable_one(self):
        """Telling the operator 'unread' sends them to the wrong place.

        'The cell is unreadable, full stop' and 'the priority you declared points
        at an unreadable cell while another declared source holds a good one' are
        different instructions: the first means repair MoySklad, the second means
        repoint the priority. They must not arrive as one word.
        """
        self._unreadable_primary()
        result = self.call('inventory.price', {'product_id': 'SKU-1042'})
        item = result['items'][0]
        self.assertEqual('priority_unreadable', item['price_reason'])
        # The readable value is still *shown*, so the operator can see it exists.
        readable = [v for v in item['price']['values'] if v['number'] is not None]
        self.assertEqual(1, len(readable))
        self.assertEqual(450000.0, readable[0]['number'])

    def test_a_priority_failure_is_not_reported_as_missing_data(self):
        """The fix must not re-enter through the not_computable door.

        Before ``POLICY_REASONS`` existed, the split compared against 'conflict'
        alone, so this reason was filed under ``not_computable`` -- which is the
        bucket that means 'your registers do not hold this'.
        """
        self._unreadable_primary()
        result = self.call('inventory.margin', {'product_id': 'SKU-1042'})
        item = result['items'][0]
        self.assertNotIn('priority_unreadable', item['not_computable'])
        self.assertIn('priority_unreadable', result['margin_blocked_by_conflict'])

    def test_an_unreadable_cell_with_no_readable_alternative_is_still_just_unread(self):
        """The distinction is earned, not assumed.

        When no source holds a readable value, the honest answer is 'unread' --
        there is no readable alternative to point the operator at, so naming a
        priority failure would invent a second, better value that does not exist.
        """
        self._unreadable_primary()
        # Remove the ERP's good price so nothing readable remains.
        db = sqlite3.connect(self.erp)
        try:
            db.executescript(
                "UPDATE products SET price = 'buzuq' WHERE sku = 'SKU-1042';")
            db.commit()
        finally:
            db.close()
        result = self.call('inventory.price', {'product_id': 'SKU-1042'})
        self.assertEqual('unread', result['items'][0]['price_reason'])

    def test_the_single_product_path_carries_the_reason_its_note_tells_you_to_read(self):
        """A note that names a field the answer does not contain is a dead end.

        The tool's own note says to consult ``price_reason``. The catalogue branch
        supplied it; the single-product branch -- the one a seller uses for one
        item, and the one the note is attached to -- did not.
        """
        result = self.call('inventory.price', {'product_id': 'SKU-1042'})
        item = result['items'][0]
        self.assertIn('price_reason', item)
        self.assertIn('unresolved', result)

    def test_both_price_paths_agree_on_the_reason_for_the_same_product(self):
        """One question must not have two shapes depending on how it was asked."""
        single = self.call('inventory.price', {'product_id': 'SKU-1042'})
        catalogue = self.call('inventory.price', {})
        by_id = {row['id']: row for row in catalogue['items']}
        self.assertEqual(single['items'][0]['price_reason'],
                         by_id['SKU-1042']['price_reason'])

    def test_both_margin_paths_use_the_same_bucket_for_every_reason(self):
        """The same defect the first audit pass fixed, guarded against returning.

        ``product()`` and ``margin()`` classify reasons independently; if one of
        them hardcodes a single policy reason again, they disagree about the same
        product and an operator is sent to two different places.
        """
        for policy_label, expected_bucket in (('report', 'conflict'),
                                              ('primary_wins', 'priority_unreadable')):
            with self.subTest(policy=policy_label):
                if policy_label == 'primary_wins':
                    self._unreadable_primary()
                else:
                    self.write_config({'connections': self.connections,
                                       'sheets_registers': self.registers,
                                       'inventory': self.inventory,
                                       'business_graph': self.graph})
                one = self.call('inventory.product', {'product_id': 'SKU-1042'})
                many = self.call('inventory.margin', {'product_id': 'SKU-1042'})
                self.assertIn(expected_bucket, one['margin_blocked_by_conflict'])
                self.assertIn(expected_bucket, many['margin_blocked_by_conflict'])
                self.assertEqual(one['not_computable'], many['not_computable'])


    def test_a_quantity_past_the_float_range_is_unreadable_not_fatal(self):
        """``float(10 ** 400)`` RAISES; a cell reader must not.

        The existing non-finite tests feed the STRING ``'inf'``, which the numeric
        regex rejects before the finite guard is ever consulted -- so they prove the
        regex works, not that the guard does. This walks the other path: a real
        Python integer past the float range, which is what a JSON provider hands
        back for an absurd quantity.
        """
        self.assertIsNone(inventory._number(10 ** 400))
        self.assertEqual(1e308, inventory._number(10 ** 308))
        self.assertIsNone(inventory._number(float('inf')))

    def test_the_module_declares_no_ceiling_it_does_not_enforce(self):
        """``MAX_QUANTITY`` bounded nothing, and the comment above it said so.

        It sat directly under the paragraph "a constant that bounds nothing is worse
        than an absent one, because it advertises a limit nobody enforces". The
        fazza-26 audit measured that nothing in the repository read it, and removed
        it. A ceiling an operator believes in but nothing enforces is a ceiling that
        will be trusted and never checked.
        """
        self.assertFalse(hasattr(inventory, 'MAX_QUANTITY'))
