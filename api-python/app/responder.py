"""LLM porti (docs §11.3 Trek B): bugun qoida, ertaga Haiku.

Responder protokoli o'zgarmaydi — RuleResponder o'rniga
ClaudeResponder keladi, route'lar tegilmaydi.
"""
from typing import Protocol
import re

from .lang import normalize
from .packs import Pack
from .tools import products_search

GREETING = {"salom", "salomlar", "assalom", "assalomu", "bormisan", "bor", "qaleysiz", "qale", "qalay", "yaxshimisan"}
PRICE = {"narx", "necha", "nechta", "nechim", "chand", "qancha", "sena", "skolko", "stoit", "skidka", "pul"}
BRANCH = {"filial", "manzil", "qayerda", "adres", "gde", "xayda", "qonda", "magazin"}


def _tokens(low: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", low)


def _has(tokens: list[str], keys: set[str]) -> bool:
    """So'z BOSHidan moslik (review #7): 'yaxshigdek'→gde xatosi yo'q."""
    return any(t == k or (t.startswith(k) and len(t) <= len(k) + 3) for t in tokens for k in keys)


class Responder(Protocol):
    def reply(self, text: str, pack: Pack) -> str: ...


FALLBACK = "Tushunmadim — operatorga uzatdim, tez orada javob beradi."


class RuleResponder:
    """Kalitsiz MVP: pack'dan narx/filial/FAQ. LLM kelganda almashadi."""

    def reply(self, text: str, pack: Pack) -> str:
        t = normalize(text)
        shop = pack.shop_name or "Do'konimiz"
        faq = pack.faq or {}
        if t.startswith("/start"):
            branches = ", ".join(b.name for b in pack.branches)
            return (
                f"Assalomu alaykum! {shop}. "
                f"Filiallarimiz: {branches}. "
                "Tovar narxini bilish uchun kodini yozing (masalan: KB001 narxi?). "
                "Buyurtma: /buy KOD SON FILIAL TELEFON ISM"
            )
        low = t.lower()
        tokens = _tokens(low)
        if tokens and tokens[0] in GREETING and len(tokens) <= 3 and not products_search(pack, t, limit=1):
            return ("Salom! Xush kelibsiz. Tovar narxini bilish uchun kodini yozing "
                    "(masalan: KB001 narxi?). Filiallar: " + ", ".join(b.name for b in pack.branches) + ".")
        hits = products_search(pack, t, limit=3)
        price_hit = _has(tokens, PRICE) and hits
        branch_hit = _has(tokens, BRANCH)
        if price_hit:
            p = hits[0]
            out = f"{p.name} ({p.id}) — {p.price_uzs} so'm. O'lchamlar: {', '.join(map(str, p.sizes))}."
            if branch_hit:  # ikki intent bitta javobda
                out += " Filiallar: " + ", ".join(b.name for b in pack.branches) + "."
            return out
        if branch_hit:
            lines = [f"{b.name}: {b.address}, {b.phone} ({b.hours})" for b in pack.branches]
            return "Filiallarimiz:\n" + "\n".join(lines)
        if any(w in low for w in ["yetkaz", "yetkar", "dostavka"]):
            return faq.get("delivery", "Yetkazib berish haqida operatordan so'rang.")
        if any(w in low for w in ["qaytar", "almashtir", "vozvrat"]):
            return faq.get("returns", "Qaytarish shartlari haqida operatordan so'rang.")
        if any(w in low for w in ["to'lov", "tolov", "click", "payme", "naqd", "oplata"]):
            return faq.get("payment", "To'lov haqida operatordan so'rang.")
        if hits:
            p = hits[0]
            return f"{p.name} ({p.id}) — {p.price_uzs} so'm. Buyurtma uchun: /buy {p.id} SON FILIAL TELEFON"
        return FALLBACK
