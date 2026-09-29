"""load_pack is on every hot path, so an unchanged pack is parsed once.

policy(), agents(), catalog() and shop_data() all call load_pack, several times per
conversation turn and again for every step the engine re-validates. Parsing and
validating a 2 000-product catalogue took ~2.4 s per call (architecture review,
2026-09-27), so a turn spent tens of seconds of CPU before any model call.

The cache is keyed by the packs directory and pack name, and is valid only while
every file the pack was built from -- pack.yaml, products.yaml (present or absent)
and each persona file -- has the same mtime and size. Editing any of them is seen
on the next call; a pack that fails validation is never cached.
"""
import os

import pytest

from app import packs
from app.packs import PackError, load_pack

PACK = """name: probe
agents:
  - id: sales.bot
    name: Bot
    persona: prompts/bot.md
    tools: [products.search]
    ladder: autonomous
"""
PRODUCTS = """products:
  - { id: P1, name: "Shim", price_uzs: 150000, sizes: [3, 4] }
"""


_CLOCK = [1_900_000_000_000_000_000]


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    # Every write gets a strictly later mtime than any before it: two writes of the
    # same size inside one filesystem timestamp tick would otherwise look identical
    # (a flake seen under full-suite load). Real edits are seconds apart.
    _CLOCK[0] += 1_000_000_000
    os.utime(path, ns=(_CLOCK[0], _CLOCK[0]))


@pytest.fixture
def pack_dir(tmp_path, monkeypatch):
    monkeypatch.setattr('app.packs.PACKS_DIR', tmp_path)
    write(tmp_path / 'probe' / 'pack.yaml', PACK)
    write(tmp_path / 'probe' / 'products.yaml', PRODUCTS)
    write(tmp_path / 'probe' / 'prompts' / 'bot.md', 'Sen sotuvchisan.')
    return tmp_path / 'probe'


def count_parses(monkeypatch):
    calls = []
    real = packs.yaml.safe_load
    monkeypatch.setattr(packs.yaml, 'safe_load', lambda text: calls.append(1) or real(text))
    return calls


def test_unchanged_pack_is_parsed_once(pack_dir, monkeypatch):
    calls = count_parses(monkeypatch)
    first = load_pack('probe')
    parsed = len(calls)
    assert parsed >= 1
    assert load_pack('probe') is first
    assert len(calls) == parsed


def test_edited_pack_yaml_is_reloaded(pack_dir):
    load_pack('probe')
    write(pack_dir / 'pack.yaml', PACK.replace('name: Bot', 'name: Sotuvchi'))
    assert load_pack('probe').agents[0].name == 'Sotuvchi'


def test_edited_products_are_reloaded(pack_dir):
    assert load_pack('probe').products[0].price_uzs == 150000
    write(pack_dir / 'products.yaml', PRODUCTS.replace('150000', '160000'))
    assert load_pack('probe').products[0].price_uzs == 160000


def test_products_file_appearing_or_disappearing_is_seen(pack_dir):
    (pack_dir / 'products.yaml').unlink()
    assert load_pack('probe').products == []
    write(pack_dir / 'products.yaml', PRODUCTS)
    assert [p.id for p in load_pack('probe').products] == ['P1']


def test_edited_persona_is_reloaded(pack_dir):
    load_pack('probe')
    write(pack_dir / 'prompts' / 'bot.md', 'Sen iliq va qisqa javob beradigan sotuvchisan.')
    assert load_pack('probe').agents[0].prompt.startswith('Sen iliq')


def test_a_broken_edit_raises_and_is_not_cached(pack_dir):
    load_pack('probe')
    write(pack_dir / 'pack.yaml', 'agents: [')
    for _ in range(2):
        with pytest.raises(PackError):
            load_pack('probe')
    write(pack_dir / 'pack.yaml', PACK)
    assert load_pack('probe').agents[0].id == 'sales.bot'


def test_same_pack_name_in_another_packs_dir_is_a_different_entry(pack_dir, tmp_path_factory, monkeypatch):
    load_pack('probe')
    other = tmp_path_factory.mktemp('other')
    write(other / 'probe' / 'pack.yaml', PACK.replace('sales.bot', 'sales.other'))
    write(other / 'probe' / 'prompts' / 'bot.md', 'Boshqa.')
    monkeypatch.setattr('app.packs.PACKS_DIR', other)
    assert load_pack('probe').agents[0].id == 'sales.other'
