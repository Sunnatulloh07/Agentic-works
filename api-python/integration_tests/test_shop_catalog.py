"""Retail catalogue model and search (app/packs.Product, app/tools.products_search).

A children's-clothing shop needs more than id/name/price/sizes to answer "92
razmer bormi?": per-size stock, category, colours and a short description. Every
field is optional so existing packs stay valid, and every field is bounded and
typed so a spreadsheet mistake is refused at load, not discovered by a customer.
"""
import pytest

from app.packs import PackError, load_pack
from app.tools import product_view, products_search

AGENT = "name: probe\nagents: []\nbranches: []\n"


def pack_with(tmp_path, monkeypatch, products_yaml):
    folder = tmp_path / "probe"
    folder.mkdir()
    (folder / "pack.yaml").write_text(AGENT, encoding="utf-8")
    (folder / "products.yaml").write_text(products_yaml, encoding="utf-8")
    monkeypatch.setattr("app.packs.PACKS_DIR", tmp_path)
    return load_pack("probe")


CATALOG = """products:
  - id: TB1
    name: "Bodi (paxta)"
    price_uzs: 89000
    sizes: [62, 68, 74]
    stock: {62: 0, 68: 4, 74: 1}
    category: bodi
    gender: "o‘g‘il"
    age: "0-12 oy"
    colors: [oq, ko‘k]
    description: "Yumshoq paxta, tugmali."
    photo_url: "https://cdn.example.uz/tb1.jpg"
  - id: TB2
    name: "Futbolka"
    price_uzs: 99000
    sizes: [86, 92, 98]
    stock: {86: 2, 92: 3, 98: 0}
    category: futbolka
    colors: [qizil]
  - id: TB3
    name: "Kombinezon (qishki)"
    price_uzs: 459000
    sizes: [80, 86, 92]
    category: kombinezon
    colors: [pushti, kulrang]
    description: "Issiq, suv o‘tkazmaydigan mato"
"""


# --- model ----------------------------------------------------------------------

def test_new_fields_are_optional_and_existing_packs_stay_valid():
    product = load_pack("demo-retail").products[0]
    assert (product.description, product.category, product.colors, product.stock,
            product.photo_url) == ("", "", [], {}, "")


def test_declared_fields_are_kept_and_stock_keys_become_strings(tmp_path, monkeypatch):
    first = pack_with(tmp_path, monkeypatch, CATALOG).products[0]
    assert first.category == "bodi" and first.gender == "o‘g‘il" and first.age == "0-12 oy"
    assert first.colors == ["oq", "ko‘k"]
    assert first.stock == {"62": 0, "68": 4, "74": 1}
    assert first.photo_url == "https://cdn.example.uz/tb1.jpg"


@pytest.mark.parametrize("extra, message", [
    ("    stock: {86: -1}\n", "stock"),
    ("    stock: {104: 1}\n", "104"),
    ('    photo_url: "http://cdn.example.uz/a.jpg"\n', "https"),
    ('    photo_url: "javascript:alert(1)"\n', "https"),
    ('    description: "' + "x" * 1001 + '"\n', "description"),
    ("    colors: [" + ", ".join(["r"] * 21) + "]\n", "colors"),
    ("    colour: [red]\n", "colour"),
])
def test_bad_catalogue_values_are_refused_at_load(tmp_path, monkeypatch, extra, message):
    text = "products:\n  - id: P1\n    name: A\n    price_uzs: 1000\n    sizes: [86, 92]\n" + extra
    with pytest.raises(PackError, match=message):
        pack_with(tmp_path, monkeypatch, text)


def test_stock_without_sizes_is_refused(tmp_path, monkeypatch):
    with pytest.raises(PackError, match="stock"):
        pack_with(tmp_path, monkeypatch,
                  "products:\n  - id: P1\n    name: A\n    price_uzs: 1\n    stock: {S: 1}\n")


# --- search -----------------------------------------------------------------------

def ids(pack, query):
    return [p.id for p in products_search(pack, query)]


def test_category_colour_and_description_words_match(tmp_path, monkeypatch):
    pack = pack_with(tmp_path, monkeypatch, CATALOG)
    assert ids(pack, "kombinezon")[0] == "TB3"
    assert ids(pack, "pushti rang bormi")[0] == "TB3"
    assert ids(pack, "suv o‘tkazmaydigan")[0] == "TB3"
    assert ids(pack, "qizil")[0] == "TB2"


def test_a_size_token_boosts_products_that_carry_the_size(tmp_path, monkeypatch):
    pack = pack_with(tmp_path, monkeypatch, CATALOG)
    # "92 razmer bormi?" names no product: every product with size 92 is a hit.
    assert set(ids(pack, "92 razmer bormi?")) == {"TB2", "TB3"}
    # With a category word, the product that also has the size ranks first.
    assert ids(pack, "futbolka 92")[0] == "TB2"


def test_search_stays_bounded(tmp_path, monkeypatch):
    rows = "".join(f"  - {{id: P{i}, name: Futbolka {i}, price_uzs: 1000, sizes: [92]}}\n"
                   for i in range(30))
    pack = pack_with(tmp_path, monkeypatch, "products:\n" + rows)
    assert len(products_search(pack, "futbolka 92")) == 5


def test_existing_demo_search_is_unchanged():
    pack = load_pack("demo-retail")
    assert ids(pack, "KB001")[0] == "KB001"
    assert ids(pack, "kurtka")[:1] == ["KB001"]


# --- the view the model reads -----------------------------------------------------

def test_view_carries_stock_and_in_stock_sizes(tmp_path, monkeypatch):
    pack = pack_with(tmp_path, monkeypatch, CATALOG)
    view = product_view(pack.products[1])
    assert view["id"] == "TB2" and view["price_uzs"] == 99000
    assert view["stock"] == {"86": 2, "92": 3, "98": 0}
    assert view["available_sizes"] == ["86", "92"]
    assert view["in_stock"] is True and view["stock_tracked"] is True


def test_view_says_when_stock_is_not_tracked(tmp_path, monkeypatch):
    view = product_view(pack_with(tmp_path, monkeypatch, CATALOG).products[2])
    assert view["stock_tracked"] is False
    assert "in_stock" not in view and "stock" not in view
    assert view["colors"] == ["pushti", "kulrang"]


def test_view_bounds_the_description(tmp_path, monkeypatch):
    text = ("products:\n  - id: P1\n    name: A\n    price_uzs: 1\n"
            '    description: "' + "d" * 1000 + '"\n')
    view = product_view(pack_with(tmp_path, monkeypatch, text).products[0])
    assert len(view["description"]) <= 300


def test_catalog_results_given_to_the_model_answer_a_size_question(tmp_path, monkeypatch):
    from app.platform_api import catalog
    pack_with(tmp_path, monkeypatch, CATALOG)
    rows = catalog("probe", "futbolka 92 razmer bormi?")
    assert rows[0]["id"] == "TB2"
    assert rows[0]["stock"]["92"] == 3 and "92" in rows[0]["available_sizes"]


# --- the shop reader behind shop.info / orders.draft ---------------------------

SHOP_PACK = ('name: probe\nshop_name: "Bolajon"\n'
             'faq:\n  delivery: "Toshkent bo‘ylab 30 000 so‘m"\n'
             'agents: []\nbranches:\n  - {id: chilonzor, name: "Chilonzor", address: "Chilonzor 9"}\n')


def test_shop_reader_feeds_shop_info_and_orders_draft(tmp_path, monkeypatch):
    from app.platform_api import shop_data
    from platform_runtime.shop_tools import order_draft, shop_info
    pack_with(tmp_path, monkeypatch, CATALOG)
    (tmp_path / "probe" / "pack.yaml").write_text(SHOP_PACK, encoding="utf-8")
    info = shop_info(shop_data("probe"))
    assert info["shop_name"] == "Bolajon" and info["faq"]["delivery"].startswith("Toshkent")
    assert info["branches"][0]["id"] == "chilonzor"
    result = order_draft(shop_data("probe"), {"product_id": "TB2", "size": "92", "qty": 2,
                                              "customer_name": "Ali", "phone": "901234567",
                                              "delivery": "chilonzor"})
    assert result["valid"] is True and result["draft"]["total_uzs"] == 198000


def test_platform_engine_registers_the_shop_tools(tmp_path, monkeypatch):
    from app.platform_api import engine
    monkeypatch.setenv("APP_DB", str(tmp_path / "app.db"))
    names = engine().registry.items
    assert {"products.search", "shop.info", "orders.draft"} <= set(names)
