"""O'zbek til qatlami (docs F6): kirill→lotin, apostrof, sheva, rus-aralash."""
import re

_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "s", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "'", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ғ": "g'", "қ": "q", "ҳ": "h", "ў": "o'",
}
_APOS = {"‘": "'", "’": "'", "ʼ": "'", "ʻ": "'", "´": "'", "`": "'",
        "‛": "'", "ʹ": "'", "ˊ": "'"}
# Eslatma: ц→s ataylab (o'zbek translit: aksiya; rus "цена"→"sena" intent uchun).
_STRIP = "?,.!,:;()\"'"

# Kirill-latun o'xshashlar (ID qidiruv uchun): КВ001 → KB001
_HOMO = str.maketrans({
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h",
    "о": "o", "р": "p", "с": "c", "т": "t", "х": "x",
})


def fold_ids(s: str) -> str:
    """ID taqqoslash uchun o'xshash harflarni latinlashtiradi."""
    return (s or "").lower().translate(_HOMO)

_SHEVA = {
    "kelvotti": "kelyapti",
    "bormisan": "bor",
    "bormisiz": "bor",
    "hozi": "hozir",
    "nimagap": "nima gap",
    "nechpul": "narx",
    "kanaqa": "qanday",
    "qale": "qalay",
}


def normalize(text: str) -> str:
    t = (text or "").lower()
    for k, v in _APOS.items():
        t = t.replace(k, v)
    t = "".join(_CYR.get(ch, ch) for ch in t)
    t = re.sub(r"\s+", " ", t).strip()
    words = [_SHEVA.get(w.strip(_STRIP), w) for w in t.split(" ")]
    return " ".join(words)
