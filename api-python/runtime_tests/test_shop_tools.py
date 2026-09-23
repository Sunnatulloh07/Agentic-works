"""shop.info and orders.draft: grounded shop facts and a validated, priced order draft.

Dependency-free: the shop is a plain dict from an injected reader, the way
products.search reads an injected catalogue, so no pack, YAML or pydantic is
involved and the runtime never learns a pack's content.
"""
import json
import tempfile
import unittest
from pathlib import Path

from platform_runtime.agent_loop import MAX_OBSERVATION_BYTES
from platform_runtime.engine import Engine, encode
from platform_runtime.shop_tools import (MAX_SHOP_INFO_BYTES, SHOP_TOOLS, normalize_uz_phone,
                                         order_draft, shop_info)
from platform_runtime.tools import build_registry, known_tool_names

SHOP = {
    'shop_name': 'Bolajon',
    'faq': {'delivery': 'Toshkent bo‘ylab 30 000 so‘m, 1 kun.', 'payment': 'Naqd, Click, Payme.'},
    'branches': [{'id': 'chilonzor', 'name': 'Chilonzor filiali', 'address': 'Chilonzor 9',
                  'phone': '+998901110000', 'hours': '9:00-21:00'}],
    'products': [
        {'id': 'TB1', 'name': 'Futbolka', 'price_uzs': 99000, 'sizes': [86, 92, 98],
         'stock': {'86': 2, '92': 3, '98': 0}},
        {'id': 'TB2', 'name': 'Shapka', 'price_uzs': 45000, 'sizes': ['S', 'M'], 'stock': {}},
        {'id': 'TB3', 'name': 'Sumka', 'price_uzs': 120000, 'sizes': [], 'stock': {}},
    ],
}


def order(**overrides):
    args = {'product_id': 'TB1', 'size': '92', 'qty': 2, 'customer_name': 'Dilnoza',
            'phone': '+998 90 123-45-67', 'delivery': 'chilonzor'}
    args.update(overrides)
    return args


def codes(result):
    return [p['code'] for p in result['problems']]


class ShopInfoTests(unittest.TestCase):
    def test_returns_name_faq_and_branches(self):
        info = shop_info(SHOP)
        self.assertEqual('Bolajon', info['shop_name'])
        self.assertEqual(SHOP['faq'], info['faq'])
        self.assertEqual('chilonzor', info['branches'][0]['id'])
        self.assertEqual('+998901110000', info['branches'][0]['phone'])
        self.assertNotIn('products', info)

    def test_is_bounded_below_one_observation(self):
        huge = {'shop_name': 'x' * 5000,
                'faq': {'k%d' % i: 'ж' * 5000 for i in range(50)},
                'branches': [{'id': 'b%d' % i, 'name': 'ф' * 500, 'address': 'ф' * 500}
                             for i in range(50)]}
        info = shop_info(huge)
        self.assertLessEqual(len(encode(info).encode('utf-8')), MAX_SHOP_INFO_BYTES)
        self.assertLess(MAX_SHOP_INFO_BYTES, MAX_OBSERVATION_BYTES)
        self.assertTrue(info['truncated'])

    def test_tolerates_missing_or_odd_values(self):
        info = shop_info({'faq': {'a': ['nested'], 'b': 7}, 'branches': 'x'})
        self.assertEqual({'shop_name': '', 'faq': {'b': '7'}, 'branches': [], 'truncated': False}, info)


class PhoneTests(unittest.TestCase):
    def test_accepted_forms(self):
        for raw in ('+998901234567', '998901234567', '901234567', '+998 (90) 123-45-67', '90 123 45 67'):
            with self.subTest(raw=raw):
                self.assertEqual('+998901234567', normalize_uz_phone(raw))

    def test_refused_forms(self):
        for raw in ('', '12345', '+7 900 123 45 67', '8 90 123 45 67', 'tel: 901234567', '90123456a'):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_uz_phone(raw))


class OrderDraftTests(unittest.TestCase):
    def test_a_valid_draft_is_normalised_and_priced_server_side(self):
        result = order_draft(SHOP, order())
        self.assertEqual((True, []), (result['valid'], result['problems']))
        draft = result['draft']
        self.assertEqual(('TB1', 'Futbolka', '92', 2), (draft['product_id'], draft['product_name'],
                                                         draft['size'], draft['qty']))
        self.assertEqual((99000, 198000, 'UZS'), (draft['unit_price_uzs'], draft['total_uzs'], draft['currency']))
        self.assertEqual('+998901234567', draft['phone'])
        self.assertEqual({'type': 'branch', 'branch_id': 'chilonzor', 'branch_name': 'Chilonzor filiali',
                          'address': 'Chilonzor 9'}, draft['delivery'])

    def test_address_delivery_and_case_insensitive_ids(self):
        result = order_draft(SHOP, order(product_id='tb2', size='s', qty=1,
                                         delivery='Yunusobod 4-kvartal, 12-uy'))
        self.assertTrue(result['valid'])
        self.assertEqual(('TB2', 'S', 45000), (result['draft']['product_id'], result['draft']['size'],
                                              result['draft']['total_uzs']))
        self.assertEqual({'type': 'address', 'address': 'Yunusobod 4-kvartal, 12-uy'}, result['draft']['delivery'])

    def test_a_product_without_sizes_needs_none(self):
        result = order_draft(SHOP, {k: v for k, v in order(product_id='TB3', qty=1).items() if k != 'size'})
        self.assertTrue(result['valid'])
        self.assertEqual('', result['draft']['size'])

    def test_each_problem_is_named(self):
        cases = ((order(product_id='NOPE'), 'unknown_product'),
                 ({k: v for k, v in order().items() if k != 'size'}, 'size_required'),
                 (order(size='104'), 'size_unavailable'),
                 (order(size='98'), 'out_of_stock'),
                 (order(qty=4), 'out_of_stock'),
                 (order(qty=0), 'qty_out_of_range'),
                 (order(qty=100), 'qty_out_of_range'),
                 (order(phone='12345'), 'invalid_phone'),
                 (order(customer_name=' A '), 'name_required'),
                 (order(delivery='abc'), 'delivery_invalid'))
        for args, code in cases:
            with self.subTest(code=code, args=args):
                result = order_draft(SHOP, args)
                self.assertFalse(result['valid'])
                self.assertIn(code, codes(result))

    def test_unavailable_size_lists_what_is_in_stock(self):
        problem = order_draft(SHOP, order(size='104'))['problems'][0]
        self.assertEqual(['86', '92'], problem['available'])

    def test_untracked_stock_accepts_any_declared_size(self):
        self.assertTrue(order_draft(SHOP, order(product_id='TB2', size='M', qty=50))['valid'])

    def test_several_problems_are_reported_together(self):
        result = order_draft(SHOP, order(size='104', phone='1', delivery='x'))
        self.assertEqual({'size_unavailable', 'invalid_phone', 'delivery_invalid'}, set(codes(result)))

    def test_an_empty_shop_refuses_every_order(self):
        self.assertEqual(['unknown_product'], codes(order_draft({}, order())))


class RegistrationTests(unittest.TestCase):
    def test_tools_register_only_with_a_shop_reader(self):
        for name in SHOP_TOOLS:
            self.assertNotIn(name, build_registry().items)
            self.assertNotIn(name, build_registry(lambda t, q: []).items)
            self.assertIn(name, build_registry(lambda t, q: [], lambda t: SHOP).items)
            self.assertIn(name, known_tool_names())

    def test_both_are_read_only(self):
        registry = build_registry(lambda t, q: [], lambda t: SHOP)
        self.assertEqual({'read'}, {registry.get(name).risk for name in SHOP_TOOLS})

    def test_schema_refuses_unknown_arguments(self):
        registry = build_registry(lambda t, q: [], lambda t: SHOP)
        with self.assertRaises(ValueError):
            registry.get('orders.draft').validate({**order(), 'price_uzs': 1})
        with self.assertRaises(ValueError):
            registry.get('shop.info').validate({'tenant': 'other'})

    def test_engine_runs_both_for_the_tenant_it_reads(self):
        seen = []

        def reader(tenant):
            seen.append(tenant)
            return SHOP
        with tempfile.TemporaryDirectory() as root:
            e = Engine(Path(root) / 'shop.db', build_registry(lambda t, q: [], reader),
                       lambda t, a: {'tools': list(SHOP_TOOLS), 'ladder': 'autonomous'})
            task = e.submit('t1', 'web', 'k1', 'bot', [{'tool': 'shop.info', 'args': {}},
                                                       {'tool': 'orders.draft', 'args': order()}])
            while e.tick('t1'):
                pass
            steps = e.get('t1', task)['steps']
        self.assertEqual(['succeeded', 'succeeded'], [s['status'] for s in steps])
        self.assertEqual(0, steps[0]['approval_needed'])
        self.assertEqual('Bolajon', steps[0]['result']['shop_name'])
        self.assertEqual(198000, steps[1]['result']['draft']['total_uzs'])
        self.assertEqual({'t1'}, set(seen))
        json.dumps(steps[1]['result'])


if __name__ == '__main__':
    unittest.main()
