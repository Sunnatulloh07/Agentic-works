"""Import a shop catalogue spreadsheet (CSV) into packs/<tenant>/products.yaml.

    python scripts/import_catalog.py --tenant turkish-baby --csv katalog.csv --dry-run
    python scripts/import_catalog.py --tenant turkish-baby --csv katalog.csv

Columns (header row, any order; the first four are required):

    id, name, price_uzs, sizes, stock, category, gender, age, colors, description, photo_url

* ``sizes``  -- ``;``-separated: ``86;92;98``
* ``stock``  -- optional, ``size:qty;...``: ``86:2;92:3``; every size must be in ``sizes``
* ``colors`` -- ``;``-separated
* ``price_uzs`` -- whole so'm; spaces are allowed (``99 000``), decimals are not

The file must be UTF-8 (a BOM, as Excel writes it, is fine). A ``;``-delimited file
(Excel in the Uzbek/Russian locale) is detected from its header. Every invalid row
is reported with its row number (the header is row 1) and NOTHING is written until
the whole file is valid. Output is sorted by id and byte-for-byte deterministic,
written with a small YAML emitter of this script's own (no PyYAML needed); the
bounds are app/packs.py ``Product``'s, so the result loads as a pack catalogue.

Stdlib only. Exit codes: 0 ok, 1 invalid rows, 2 unusable input (tenant, file).
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TENANT_RE = re.compile(r'[A-Za-z0-9_-]{1,64}')
ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
REQUIRED = ('id', 'name', 'price_uzs', 'sizes')
COLUMNS = REQUIRED + ('stock', 'category', 'gender', 'age', 'colors', 'description', 'photo_url')
# app/packs.py Product bounds, restated: this script must run without the API's
# dependencies, and the round-trip test pins that the two agree.
MAX_NAME_CHARS = 200
MAX_LABEL_CHARS = 64
MAX_DESCRIPTION_CHARS = 1000
MAX_COLORS = 20
MAX_SIZES = 50
MAX_SIZE_CHARS = 16
MAX_URL_CHARS = 2000
MAX_PRICE = 10 ** 12
MAX_STOCK = 10 ** 9
MAX_ROWS = 5000
HEADER_COMMENT = ('# scripts/import_catalog.py yaratgan fayl. Qo‘lda tahrirlamang: CSV’ni o‘zgartirib\n'
                  '# qayta import qiling (--dry-run bilan avval tekshiring).\n')
# Characters YAML reads as a line break or refuses outright inside a scalar;
# everything else printable is written as itself so Uzbek text stays readable.
_ESCAPES = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\r': '\\r', '\t': '\\t', '\0': '\\0'}


def yaml_str(value: str) -> str:
    """A YAML double-quoted scalar that loads back as exactly ``value``."""
    out = []
    for ch in value:
        code = ord(ch)
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif (code < 0x20 or 0x7F <= code <= 0x9F or ch in '  ﻿'
              or 0xD800 <= code <= 0xDFFF or code in (0xFFFE, 0xFFFF)):
            out.append(f'\\u{code:04x}' if code <= 0xFFFF else f'\\U{code:08x}')
        else:
            out.append(ch)
    return '"' + ''.join(out) + '"'


def _split(cell: str) -> list[str]:
    return [part.strip() for part in cell.split(';') if part.strip()]


def _whole(text: str, what: str, maximum: int) -> int:
    digits = re.sub(r'[   _]', '', text.strip())
    if not re.fullmatch(r'\d{1,13}', digits) or int(digits) > maximum:
        raise ValueError(f'{what} butun, manfiy bo‘lmagan son bo‘lishi kerak: {text!r}')
    return int(digits)


def _product(row: dict) -> dict:
    """One CSV row as a products.yaml entry; raises ValueError naming the column."""
    pid = (row.get('id') or '').strip()
    if not ID_RE.fullmatch(pid):
        raise ValueError(f'id noto‘g‘ri (harf/raqam/-_., 1..64): {pid!r}')
    name = ' '.join((row.get('name') or '').split())
    if not 1 <= len(name) <= MAX_NAME_CHARS:
        raise ValueError(f'name 1..{MAX_NAME_CHARS} belgi bo‘lishi kerak')
    item = {'id': pid, 'name': name, 'price_uzs': _whole(row.get('price_uzs') or '', 'price_uzs', MAX_PRICE)}
    sizes = _split(row.get('sizes') or '')
    if len(sizes) > MAX_SIZES or any(len(s) > MAX_SIZE_CHARS for s in sizes):
        raise ValueError(f'sizes: ko‘pi bilan {MAX_SIZES} ta, har biri {MAX_SIZE_CHARS} belgigacha')
    if len({s.lower() for s in sizes}) != len(sizes):
        raise ValueError('sizes: takrorlangan o‘lcham')
    item['sizes'] = sizes
    stock = {}
    for part in _split(row.get('stock') or ''):
        size, sep, qty = part.rpartition(':')
        size = size.strip()
        if not sep or size not in sizes:
            raise ValueError(f'stock: {part!r} -- o‘lcham sizes ro‘yxatida bo‘lishi kerak (86:2 shaklida)')
        if size in stock:
            raise ValueError(f'stock: {size!r} ikki marta yozilgan')
        stock[size] = _whole(qty, f'stock {size}', MAX_STOCK)
    if stock:
        item['stock'] = stock
    for key in ('category', 'gender', 'age'):
        value = ' '.join((row.get(key) or '').split())
        if len(value) > MAX_LABEL_CHARS:
            raise ValueError(f'{key} {MAX_LABEL_CHARS} belgidan oshmasin')
        if value:
            item[key] = value
    colors = _split(row.get('colors') or '')
    if len(colors) > MAX_COLORS or any(len(c) > MAX_LABEL_CHARS for c in colors):
        raise ValueError(f'colors: ko‘pi bilan {MAX_COLORS} ta, har biri {MAX_LABEL_CHARS} belgigacha')
    if colors:
        item['colors'] = colors
    description = (row.get('description') or '').strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f'description {MAX_DESCRIPTION_CHARS} belgidan oshmasin')
    if description:
        item['description'] = description
    url = (row.get('photo_url') or '').strip()
    if url:
        if (not re.fullmatch(r'https://[^\s/@]+(/\S*)?', url) or len(url) > MAX_URL_CHARS):
            raise ValueError('photo_url faqat https:// manzil bo‘lishi kerak')
        item['photo_url'] = url
    return item


def parse_catalog(text: str) -> tuple[list[dict], list[str]]:
    """(products sorted by id, errors). Any error means nothing may be written."""
    text = text.lstrip('﻿')
    first = text.split('\n', 1)[0]
    delimiter = ';' if ';' in first and ',' not in first else ','
    reader = csv.DictReader(io.StringIO(text, newline=''), delimiter=delimiter)
    header = [(h or '').strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED if c not in header]
    unknown = [h for h in header if h not in COLUMNS]
    if missing or unknown or len(set(header)) != len(header):
        problems = []
        if missing:
            problems.append('yetishmayotgan ustun: ' + ', '.join(missing))
        if unknown:
            problems.append('noma’lum ustun: ' + ', '.join(unknown) + ' (ruxsat: ' + ', '.join(COLUMNS) + ')')
        if len(set(header)) != len(header):
            problems.append('takrorlangan ustun nomi')
        return [], ['qator 1: ' + '; '.join(problems)]
    reader.fieldnames = header
    products, errors, seen = [], [], {}
    for number, row in enumerate(reader, start=2):
        if number - 1 > MAX_ROWS:
            errors.append(f'qator {number}: {MAX_ROWS} dan ortiq mahsulot')
            break
        if None in row:
            errors.append(f'qator {number}: sarlavhadan ko‘p ustun')
            continue
        if not any((v or '').strip() for v in row.values()):
            continue
        try:
            item = _product(row)
        except ValueError as exc:
            errors.append(f'qator {number}: {exc}')
            continue
        key = item['id'].lower()
        if key in seen:
            errors.append(f"qator {number}: id {item['id']!r} qator {seen[key]} dagi bilan takrorlanadi")
            continue
        seen[key] = number
        products.append(item)
    if not products and not errors:
        errors.append('qator 2: mahsulot yo‘q')
    return (sorted(products, key=lambda p: p['id']) if not errors else []), errors


def render_yaml(products: list[dict]) -> str:
    lines = [HEADER_COMMENT.rstrip('\n'), 'products:']
    for item in sorted(products, key=lambda p: p['id']):
        lines.append(f"  - id: {yaml_str(item['id'])}")
        lines.append(f"    name: {yaml_str(item['name'])}")
        lines.append(f"    price_uzs: {item['price_uzs']}")
        lines.append('    sizes: [' + ', '.join(yaml_str(s) for s in item['sizes']) + ']')
        if item.get('stock'):
            lines.append('    stock: {' + ', '.join(f'{yaml_str(k)}: {v}' for k, v in item['stock'].items()) + '}')
        for key in ('category', 'gender', 'age'):
            if item.get(key):
                lines.append(f'    {key}: {yaml_str(item[key])}')
        if item.get('colors'):
            lines.append('    colors: [' + ', '.join(yaml_str(c) for c in item['colors']) + ']')
        for key in ('description', 'photo_url'):
            if item.get(key):
                lines.append(f'    {key}: {yaml_str(item[key])}')
    return '\n'.join(lines) + '\n'


def summary(products: list[dict]) -> str:
    categories: dict[str, int] = {}
    for item in products:
        label = item.get('category', '(kategoriyasiz)')
        categories[label] = categories.get(label, 0) + 1
    units = sum(sum(item.get('stock', {}).values()) for item in products)
    tracked = sum(1 for item in products if item.get('stock'))
    lines = [f'{len(products)} ta mahsulot; qoldiq kuzatiladigan: {tracked}, jami dona: {units}']
    lines += [f'  {name}: {count}' for name, count in sorted(categories.items())]
    return '\n'.join(lines)


def _write(target: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix='.products.', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(text.encode('utf-8'))
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='CSV katalog -> packs/<tenant>/products.yaml')
    parser.add_argument('--tenant', required=True, help='pack papkasi nomi, masalan turkish-baby')
    parser.add_argument('--csv', required=True, type=Path, help='UTF-8 CSV fayl')
    parser.add_argument('--packs-dir', type=Path, default=Path(os.environ.get('PACKS_DIR') or REPO / 'packs'))
    parser.add_argument('--dry-run', action='store_true', help='faqat tekshirish va xulosa, yozmaydi')
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Uzbek text on a legacy Windows console
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')
    if not TENANT_RE.fullmatch(args.tenant):
        print(f'tenant nomi noto‘g‘ri: {args.tenant!r}', file=sys.stderr)
        return 2
    folder = args.packs_dir / args.tenant
    if not (folder / 'pack.yaml').is_file():
        print(f'pack topilmadi: {folder / "pack.yaml"} -- avval pack papkasini yarating '
              '(packs/turkish-baby dan nusxa oling)', file=sys.stderr)
        return 2
    try:
        text = args.csv.read_bytes().decode('utf-8-sig')
    except OSError as exc:
        print(f'CSV o‘qilmadi: {args.csv} ({type(exc).__name__})', file=sys.stderr)
        return 2
    except UnicodeDecodeError:
        print('CSV UTF-8 bo‘lishi kerak (Excel: "CSV UTF-8" formatida saqlang)', file=sys.stderr)
        return 2
    products, errors = parse_catalog(text)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f'{len(errors)} ta xato: hech narsa yozilmadi', file=sys.stderr)
        return 1
    print(summary(products))
    target = folder / 'products.yaml'
    if args.dry_run:
        print(f'dry-run: {target} yozilmadi')
        return 0
    _write(target, render_yaml(products))
    print(f'{len(products)} ta mahsulot yozildi: {target}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
