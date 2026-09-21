"""Stage 3: o'zbek til qatlami (docs F6) — 50 test.

Lotin/kirill, apostrof variantlari, sheva, rus-aralash → standart lotin.
"""
import pytest

from app.lang import normalize
from app.responder import RuleResponder
from app.packs import load_pack

pack = load_pack("demo-retail")
resp = RuleResponder()


@pytest.mark.parametrize("raw,expected", [
    ("KB001 narxi?", "kb001 narxi?"),
    ("КБ001 нархи?", "kb001 narxi?"),
    ("o‘zbekcha o‘g‘il", "o'zbekcha o'g'il"),
    ("oʻzbek oʻgʻil", "o'zbek o'g'il"),
    ("o'zbek o'g'il", "o'zbek o'g'il"),
    ("hozi kelvotti", "hozir kelyapti"),
    ("bormisan aka?", "bor aka?"),
    ("Сколько стоит KB001?", "skolko stoit kb001?"),
    ("филиал qayerda?", "filial qayerda?"),
    ("KB001   NARXI  ", "kb001 narxi"),
    ("доставка bormi?", "dostavka bormi?"),
    ("to'lov click’dami?", "to'lov click'dami?"),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("text,want", [
    ("КБ001 нархи қанча?", "350000"),
    ("KB001 nechpul?", "350000"),
    ("Сколько стоит KB001?", "350000"),
    ("KB002 sena?", "330000"),
    ("филиал манзили?", "Chilonzor"),
    ("где филиал?", "Chilonzor"),
    ("hozi ochiqmisan?", "Tushunmadim — operatorga uzatdim"),
    ("kelvotti, KB003 bormi?", "120000"),
    ("доставка qancha?", "30000"),
    ("оплата qanday?", "Click"),
    ("qaytarish boarding?", "7 kun"),
    ("возврат bormi?", "7 kun"),
    ("bormisan, KB005 narxi?", "220000"),
    ("o‘g‘il kurtka necha pul?", "330000"),
    ("KB010 maktab formasi narxi qancha?", "400000"),
    ("qayerda joylashgansan?", "Chilonzor"),
    ("салом, KB020 bormi?", "KB020"),
    ("nimagap KB001 narxi?", "350000"),
    ("dukoniz xayda?", "Chilonzor"),
    ("magazin qonda?", "Chilonzor"),
    ("KB001 nechta turadi?", "350000"),
    ("kurtka nechim?", "350000"),
    ("skidka bormi KB003?", "120000"),
    ("yetkarib berasizmi?", "30000"),
    ("bormisan?", "Xush kelibsiz"),
    ("qaleysiz?", "Xush kelibsiz"),
    ("kurtka chand turadi?", "350000"),
    ("KB001 v nalichii?", "KB001"),
    ("magazin otkritmi?", "Chilonzor"),
    ("yo'mi KB002?", "KB002"),
    ("ketvotti, KB004 bormi?", "KB004"),
    ("qivossan KB001 narxi?", "350000"),
    ("qaleysiz, KB006 bormi?", "KB006"),
    ("KB007 skidka bormi?", "KB007"),
    ("qonda qaysi filial yaqin?", "Chilonzor"),
    ("xayda dukon?", "Chilonzor"),
    ("yetkarib berish qancha turadi?", "30000"),
    ("assalom KB008 narxi?", "90000"),
])
def test_reply_multilingual(text, want):
    assert want in resp.reply(text, pack)
