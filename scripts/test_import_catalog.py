"""scripts/import_catalog.py: shop spreadsheet (CSV) -> packs/<tenant>/products.yaml.

Wired like test_run_local.py: `python -m unittest discover -s scripts -p "test_import_catalog.py"`.
Stdlib only. The round-trip checks (PyYAML parse + app.packs.Product validation)
skip when the API's own dependencies are not installed.
"""
import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

import import_catalog as ic

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
HAS_API_DEPS = all(importlib.util.find_spec(m) for m in ('yaml', 'pydantic'))
HEADER = 'id,name,price_uzs,sizes,stock,category,gender,age,colors,description,photo_url\n'
GOOD = (HEADER
        + 'TB002,Futbolka,99 000,86;92;98,86:2;92:3;98:0,futbolka,qiz,1-3 yosh,qizil;oq,"Paxta, yumshoq",'
          'https://cdn.example.uz/tb2.jpg\n'
        + 'TB001,"Bodi ""Kichkintoy""",89000,62;68,,bodi,o‘g‘il,0-6 oy,ko‘k,,\n')


def parse(text):
    return ic.parse_catalog(text)


class ParseTests(unittest.TestCase):
    def test_valid_rows_are_typed_and_sorted_by_id(self):
        products, errors = parse('﻿' + GOOD)
        self.assertEqual([], errors)
        self.assertEqual(['TB001', 'TB002'], [p['id'] for p in products])
        second = products[1]
        self.assertEqual(99000, second['price_uzs'])
        self.assertEqual(['86', '92', '98'], second['sizes'])
        self.assertEqual({'86': 2, '92': 3, '98': 0}, second['stock'])
        self.assertEqual(['qizil', 'oq'], second['colors'])
        self.assertEqual('Paxta, yumshoq', second['description'])
        self.assertEqual('Bodi "Kichkintoy"', products[0]['name'])
        self.assertEqual('o‘g‘il', products[0]['gender'])
        self.assertNotIn('stock', products[0])
        self.assertNotIn('photo_url', products[0])

    def test_invalid_rows_are_named_by_row_number(self):
        text = (HEADER
                + 'A1,,1000,86,,,,,,,\n'                       # row 2: name
                + 'A2,Ok,12.5,86,,,,,,,\n'                     # row 3: price
                + 'A3,Ok,1000,86,92:1,,,,,,\n'                 # row 4: stock size
                + 'A4,Ok,1000,86,86:-1,,,,,,\n'                # row 5: stock qty
                + 'A5,Ok,1000,86,,,,,,,http://x.uz/a.jpg\n'    # row 6: https
                + 'A 6,Ok,1000,86,,,,,,,\n')                   # row 7: id
        products, errors = parse(text)
        self.assertEqual([], products)
        for row in range(2, 8):
            self.assertTrue(any(e.startswith(f'qator {row}:') for e in errors), (row, errors))

    def test_duplicates_are_refused_with_both_rows(self):
        _, errors = parse(HEADER + 'A1,X,1,,,,,,,,\n' + 'B1,Y,1,,,,,,,,\n' + 'a1,Z,1,,,,,,,,\n')
        self.assertEqual(1, len(errors))
        self.assertIn('qator 4', errors[0])
        self.assertIn('qator 2', errors[0])

    def test_missing_or_unknown_columns_are_refused(self):
        self.assertTrue(parse('id,name,sizes\nA,B,1\n')[1])
        _, errors = parse('id,name,price_uzs,colour\nA,B,1,red\n')
        self.assertTrue(any('colour' in e for e in errors))

    def test_description_and_colors_are_bounded(self):
        _, errors = parse(HEADER + 'A1,X,1,,,,,,' + ';'.join(['r'] * 21) + ',,\n'
                          + 'A2,X,1,,,,,,,' + 'd' * 1001 + ',\n')
        self.assertTrue(any(e.startswith('qator 2:') and 'colors' in e for e in errors), errors)
        self.assertTrue(any(e.startswith('qator 3:') and 'description' in e for e in errors), errors)

    def test_excel_semicolon_files_are_detected(self):
        text = ('id;name;price_uzs;sizes;stock\n'
                'A1;Futbolka;99000;"86;92";"86:1;92:0"\n')
        products, errors = parse(text)
        self.assertEqual([], errors)
        self.assertEqual({'86': 1, '92': 0}, products[0]['stock'])

    def test_an_empty_file_is_an_error(self):
        self.assertTrue(parse('')[1])
        self.assertTrue(parse(HEADER)[1])


class EmitTests(unittest.TestCase):
    TRICKY = ['a "quoted" \\ back', 'colon: yes # not a comment', '- leading dash', 'line\nbreak',
              'sep line', 'tab\tend', "o‘g‘il ko‘ylak", 'yes', '123', '', 'null']

    def test_output_is_deterministic_whatever_the_row_order(self):
        lines = GOOD.splitlines(keepends=True)
        swapped = lines[0] + lines[2] + lines[1]
        self.assertEqual(ic.render_yaml(parse(GOOD)[0]), ic.render_yaml(parse(swapped)[0]))

    def test_output_starts_with_the_replace_me_comment_and_uses_lf(self):
        text = ic.render_yaml(parse(GOOD)[0])
        self.assertTrue(text.startswith('#'))
        self.assertIn('import_catalog.py', text.splitlines()[0])
        self.assertNotIn('\r', text)

    @unittest.skipUnless(HAS_API_DEPS, 'PyYAML/pydantic not installed')
    def test_tricky_strings_round_trip_exactly(self):
        import yaml
        for value in self.TRICKY:
            with self.subTest(value=value):
                self.assertEqual(value, yaml.safe_load('v: ' + ic.yaml_str(value))['v'])

    @unittest.skipUnless(HAS_API_DEPS, 'PyYAML/pydantic not installed')
    def test_output_round_trips_through_the_pack_product_model(self):
        import yaml
        sys.path.insert(0, str(REPO / 'api-python'))
        try:
            from app.packs import Product
        finally:
            sys.path.remove(str(REPO / 'api-python'))
        products = parse(GOOD)[0]
        loaded = yaml.safe_load(ic.render_yaml(products))['products']
        self.assertEqual(products, loaded)
        models = [Product.model_validate(item) for item in loaded]
        self.assertEqual({'86': 2, '92': 3, '98': 0}, models[1].stock)


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ic.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packs = Path(self.tmp.name) / 'packs'
        (self.packs / 'shop').mkdir(parents=True)
        (self.packs / 'shop' / 'pack.yaml').write_text('name: shop\n', encoding='utf-8')
        self.csv = Path(self.tmp.name) / 'katalog.csv'
        self.csv.write_bytes(('﻿' + GOOD).replace('\n', '\r\n').encode('utf-8'))
        self.target = self.packs / 'shop' / 'products.yaml'

    def test_writes_the_tenant_products_file(self):
        code, out, _ = self.run_cli('--tenant', 'shop', '--csv', str(self.csv), '--packs-dir', str(self.packs))
        self.assertEqual(0, code)
        data = self.target.read_bytes()
        self.assertNotIn(b'\r', data)
        self.assertIn('TB002', data.decode('utf-8'))
        self.assertIn('2', out)

    def test_dry_run_writes_nothing_and_summarises(self):
        code, out, _ = self.run_cli('--tenant', 'shop', '--csv', str(self.csv), '--packs-dir', str(self.packs),
                                    '--dry-run')
        self.assertEqual(0, code)
        self.assertFalse(self.target.exists())
        self.assertIn('dry-run', out)
        self.assertIn('futbolka', out)

    def test_an_invalid_file_writes_nothing_and_fails(self):
        self.target.write_text('products: []\n', encoding='utf-8')
        self.csv.write_text(HEADER + 'A1,,1,,,,,,,,\n', encoding='utf-8')
        code, _, err = self.run_cli('--tenant', 'shop', '--csv', str(self.csv), '--packs-dir', str(self.packs))
        self.assertEqual(1, code)
        self.assertIn('qator 2', err)
        self.assertEqual('products: []\n', self.target.read_text(encoding='utf-8'))

    def test_unknown_or_unsafe_tenant_is_refused(self):
        for tenant in ('nope', '../shop', 'a/b'):
            with self.subTest(tenant=tenant):
                code, _, err = self.run_cli('--tenant', tenant, '--csv', str(self.csv),
                                            '--packs-dir', str(self.packs))
                self.assertEqual(2, code)
                self.assertTrue(err)

    def test_non_utf8_file_is_refused(self):
        self.csv.write_bytes(HEADER.encode() + 'A1,Ko\xfcylak,1,,,,,,,,\n'.encode('latin-1'))
        code, _, err = self.run_cli('--tenant', 'shop', '--csv', str(self.csv), '--packs-dir', str(self.packs))
        self.assertEqual(2, code)
        self.assertIn('UTF-8', err)

    def test_the_shipped_example_csv_imports_cleanly(self):
        example = REPO / 'packs' / '_template' / 'products.example.csv'
        products, errors = ic.parse_catalog(example.read_bytes().decode('utf-8-sig'))
        self.assertEqual([], errors)
        self.assertGreaterEqual(len(products), 3)


if __name__ == '__main__':
    unittest.main()
