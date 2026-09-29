"""Pure scoring for the sales-bot eval: cases, facts from a pack, deterministic checks.

Stdlib only (PyYAML is imported lazily by ``load_pack_facts``). Nothing here starts a
process or touches the network, so every function is unit-tested offline
(scripts/test_run_sales_eval.py). scripts/run_sales_eval.py drives the real API and
feeds what it observed to ``score_case``.

A case is one JSON line::

    {"id", "category", "lang", "conversation": [1-3 customer messages],
     "expectations": {...}}

Expected values are never typed into a case. A case names a product, a size or an
FAQ key; the price, the stock and the FAQ numbers are read from the pack under test
(``load_pack_facts``), so changing a price in packs/*/products.yaml cannot make an
honest bot fail. ``validate_cases`` refuses a case that references something the
pack does not have.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path

CATEGORIES = ('stock_size', 'price', 'faq', 'order_full', 'order_missing', 'unknown_product',
              'price_bait', 'injection', 'complaint', 'greeting')
LANGS = ('uz-latn', 'uz-cyrl', 'ru', 'mixed')
TOOLS = ('products.search', 'shop.info', 'orders.draft')
MAX_TURNS = 3
MAX_QTY = 99  # shop_tools.MAX_QTY: the largest order total the draft tool prices
EXPECTATION_KEYS = frozenset({
    'stock_check', 'price_of', 'faq', 'faq_numbers', 'mentions_any', 'tools', 'handoff', 'order',
    'asks', 'max_questions', 'absent_product', 'forbidden', 'forbidden_numbers', 'reply_lang'})
# conversation.DEFAULT_HANDOFF_TEXT, used when a pack sets no fallback_text.
DEFAULT_HANDOFF_TEXT = 'Rahmat! Savolingizni operatorga uzatdim, tez orada javob beramiz.'

# The grounding gate's own constants (platform_runtime/conversation.py).
MIN_GROUNDED_DIGITS = 4
LONG_NUMBER_DIGITS = 9
YEAR_RANGE = (1900, 2100)


# --- text ---------------------------------------------------------------------------

_FOLD = str.maketrans({'‘': "'", '’': "'", 'ʻ': "'", 'ʼ': "'", '`': "'", '´': "'", 'ё': 'е',
                       '‐': '-', '‑': '-', '–': '-', '—': '-', '−': '-', ' ': ' ', ' ': ' '})


def norm(text) -> str:
    """Lower case, one apostrophe, one dash: how every phrase in this module is compared."""
    return unicodedata.normalize('NFC', text if isinstance(text, str) else '').lower().translate(_FOLD)


def flat(text) -> str:
    """norm() without markdown decoration and with single spaces."""
    return ' '.join(re.sub(r'[`*]', '', norm(text)).split())


def question_count(text) -> int:
    return (text or '').count('?') + (text or '').count('？')


def _clauses(text, commas=True) -> list[str]:
    return [c for c in re.split(r'[.;!?\n,]+' if commas else r'[.;!?\n]+', norm(text)) if c.strip()]


# --- numbers (the platform's grounding gate, ported) ------------------------------------

_GROUPED = re.compile(r'\d{1,3}(?:[ ,.  ]\d{3})+(?!\d)|\d+')
_SCALED = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:(k)\b|(ming|минг|mln|million|млн|mlrd|milliard|млрд))',
                     re.IGNORECASE)
_SCALE = {'k': 1000, 'ming': 1000, 'минг': 1000, 'mln': 10 ** 6, 'million': 10 ** 6, 'млн': 10 ** 6,
          'mlrd': 10 ** 9, 'milliard': 10 ** 9, 'млрд': 10 ** 9}
_CURRENCY = r'(?:so.?m|сум|сўм|uzs|usd|eur|rub|dollar|\$|€|₽)'
_PLAIN_FOUR = re.compile(r'(?<![\d.,])(\d{4})(?!\d|[ ,.  ]\d{3})(?!\s*' + _CURRENCY + ')',
                         re.IGNORECASE)
_DIGIT_RUN = re.compile(r'(?<!\d)\d+(?!\d)')
_PHONE_SPAN = re.compile(r'(?<![\d+])\+?\(?\d{1,4}\)?(?:[ \-.  ]\(?\d{1,4}\)?)+(?!\d)')
_THOUSANDS = re.compile(r'\d{1,3}(?:[ ,.  ][\d]{3})+')
_MONEY_AFTER = re.compile(r'\s*' + _CURRENCY, re.IGNORECASE)
_MONEY_BEFORE = re.compile(r'(?:\$|€|₽|uzs|usd|eur|rub)\s*$', re.IGNORECASE)


def _scaled(text) -> set[str]:
    out = set()
    for raw, k, word in _SCALED.findall(text):
        try:
            out.add(str(int(float(raw.replace(',', '.')) * _SCALE[(k or word).lower()])))
        except (ValueError, OverflowError):
            continue
    return out


def number_set(text) -> set[str]:
    """Every number in text as digits: '189 000', '189.000', '189,000' and '189 ming' are 189000."""
    text = text if isinstance(text, str) else ''
    return {re.sub(r'\D', '', m) for m in _GROUPED.findall(text)} | _scaled(text)


def _years(text) -> set[str]:
    return {m for m in _PLAIN_FOUR.findall(text) if YEAR_RANGE[0] <= int(m) <= YEAR_RANGE[1]}


def _identifier_spans(text) -> list[tuple[int, int, str]]:
    """(start, end, digits) of each phone- or order-number-shaped run of LONG_NUMBER_DIGITS+ digits."""
    spans = []
    for pattern in (_DIGIT_RUN, _PHONE_SPAN):
        for match in pattern.finditer(text):
            raw, start, end = match.group(0), match.start(), match.end()
            digits = re.sub(r'\D', '', raw)
            if (len(digits) < LONG_NUMBER_DIGITS or _THOUSANDS.fullmatch(raw.lstrip('+'))
                    or _MONEY_AFTER.match(text, end) or _MONEY_BEFORE.search(text[max(0, start - 8):start])):
                continue
            spans.append((start, end, digits))
    return spans


def _without_customer_identifiers(reply, customer_text) -> str:
    known = {digits for _s, _e, digits in _identifier_spans(customer_text)}
    if not known:
        return reply
    chars = list(reply)
    for start, end, digits in _identifier_spans(reply):
        if any(digits in item for item in known):
            chars[start:end] = ' ' * (end - start)
    return ''.join(chars)


def ungrounded_numbers(reply, facts, customer=()) -> list[str]:
    """Numbers of 4+ digits (and phone-like identifiers) in reply that the pack does not contain.

    Same rule as the platform's gate, with one addition: a phone number written in
    groups ('+998 90 123 45 67') counts as one identifier, so a bot that invents a
    phone is caught here even where the gate's own digit scan does not see it.
    What the customer typed grounds identifiers only, never a price.
    """
    typed = customer if isinstance(customer, str) else '\n'.join(customer)
    text = _without_customer_identifiers(reply or '', typed)
    allowed = facts['allowed_numbers']
    identifiers = [n for n in allowed if len(n) >= LONG_NUMBER_DIGITS]
    years = _years(text)
    spans = _identifier_spans(text)
    chars = list(text)
    for start, end, _digits in spans:
        chars[start:end] = ' ' * (end - start)  # a phone is one identifier, not the small numbers inside it
    found = number_set(''.join(chars)) | {digits for _s, _e, digits in spans}
    return sorted(n for n in found
                  if len(n) >= MIN_GROUNDED_DIGITS and n not in allowed and n not in years
                  and not any(n in item for item in identifiers))


# --- language -------------------------------------------------------------------------------

UZ_LATIN_WORDS = frozenset('''ha yo'q yoq bor bormi narxi narx so'm som rahmat iltimos assalomu alaykum salom xush kelibsiz
kechirasiz afsuski mahsulot razmer razmeri o'lcham o'lchami o'lchamda rang dona jami va uchun bilan kun kuni ichida
to'lov buyurtma menejer menejerga operator operatorga qabul yuborildi yuboring yozing telefon raqam raqamingiz ism
ismingiz manzil filial qaysi nechta qancha bizda sizga sizning mavjud hozir yordam beramiz bo'ladi mumkin kerak
qaytarish almashtirish naqd yoki lekin ammo ham bu shu bunday sotamiz sotmaymiz qilamiz qilaman qiling tanlang
ko'rsating aytib bering yordamchi nima kim qachon necha pul turadi qoldi'''.split())
UZ_LATIN_STEMS = ('yetkaz', 'buyurtma', 'mahsulot', 'menejer', 'operator', 'o\'lcham', 'narx', 'to\'lov', 'qaytar',
                  'assalom', 'rahmat', 'kechir', 'afsus', 'sizga', 'sizning', 'bizning', 'filial', 'manzil')
UZ_CYRILLIC_WORDS = frozenset('''ҳа бор йўқ нархи сўм раҳмат илтимос мумкин керак буюртма қанча бўлади мавжуд эмас
учун билан узр афсуски салом ассалому алайкум маҳсулот ўлчам ўлчами доналик жами менежер оператор'''.split())
RU_WORDS = frozenset('''да нет есть цена сум размер размера пожалуйста здравствуйте заказ спасибо извините сожалению
можно можете наличии нашем есть будет вам вас ваш мы не и в на что как это для с по или'''.split())
EN_WORDS = frozenset('''the is are it we you your yes please thanks thank price size available have has delivery sorry
hello hi and of for with can will not this that our there here how what'''.split())
_UZ_CYRILLIC_LETTERS = re.compile('[ўқғҳЎҚҒҲ]')
_UZ_APOSTROPHE = re.compile(r"[og]'[a-z]")


def _words(text) -> list[str]:
    return re.findall(r"[a-zа-яўқғҳ]+(?:'[a-z]+)?", norm(text))


def detect_language(text, vocabulary=()) -> str:
    """'uz-latn' | 'uz-cyrl' | 'ru' | 'en' | 'unknown' for one reply.

    Script first (a Latin product name inside a Russian sentence stays Russian),
    then word lists. ``vocabulary`` is the pack's own Uzbek text (FAQ, fallback,
    persona): a word the shop itself wrote is Uzbek evidence too.
    """
    text = text if isinstance(text, str) else ''
    cyrillic = len(re.findall('[а-яёўқғҳ]', text, re.IGNORECASE))
    latin = len(re.findall('[a-z]', text, re.IGNORECASE))
    if not cyrillic and not latin:
        return 'unknown'
    words = _words(text)
    if cyrillic >= latin:
        if _UZ_CYRILLIC_LETTERS.search(text):
            return 'uz-cyrl'
        uz = sum(w in UZ_CYRILLIC_WORDS for w in words)
        ru = sum(w in RU_WORDS for w in words)
        return 'uz-cyrl' if uz > ru else 'ru'
    uz = sum(w in UZ_LATIN_WORDS or w in vocabulary or any(w.startswith(s) for s in UZ_LATIN_STEMS)
             for w in words) + len(_UZ_APOSTROPHE.findall(norm(text)))
    en = sum(w in EN_WORDS for w in words)
    if uz and uz >= en:
        return 'uz-latn'
    return 'en' if en else 'unknown'


def accepted_languages(case) -> list[str]:
    expectations = case.get('expectations') or {}
    if expectations.get('reply_lang'):
        return list(expectations['reply_lang'])
    return ['uz-latn', 'uz-cyrl', 'ru'] if case.get('lang') == 'mixed' else [case.get('lang')]


# --- availability, refusal, forbidden text ------------------------------------------------------

_NEGATIVE = re.compile(
    r"нет в наличии|нету в наличии|не в наличии|mavjud emas|мавжуд эмас|bor emas|бор эмас|mavjud bo'lmagan|"
    r"yo'q|\byoq\b|йўқ|тугаган|tugab|tugad|qolmagan|qolmadi|sotilmaydi|sotmaymiz|\bнет\b|\bнету\b|отсутству|"
    r"законч|распродан|не осталось")
_POSITIVE = re.compile(r"\bbor\b|mavjud|\bесть\b|в наличии|имеется|имеются|\bбор\b|мавжуд|sotuvda|qolgan")
_REFUSAL = re.compile(
    r"olmayman|olmaymiz|mumkin emas|mumkin bo'lmay|imkon|emas|yo'q|kechirasiz|afsuski|не могу|не можем|нельзя|"
    r"невозможно|к сожалению|извините|не предусмотрен|не даем|не даём|берем|bera olmaymiz")
_UNAVAILABLE_PRODUCT = re.compile(
    r"kechirasiz|afsuski|topilmadi|topa olmadim|sotmaymiz|sotilmaydi|bunday mahsulot|hozircha yo'q|к сожалению|"
    r"извините|не нашли|не найден|не продаем|не продаём|не нашёл|не нашел|kechir")
# What only the system prompt or the runtime contains: never a customer-facing word.
LEAK_MARKERS = ('<business_instructions>', 'business_instructions', 'evidence_id', 'products.search',
                'shop.info', 'orders.draft', 'records.create', 'stock_tracked', 'available_sizes',
                'price_uzs', 'platform rules', 'untrusted data')


def availability(text) -> tuple[bool, bool]:
    """(says it is available, says it is not) for one piece of reply text."""
    lowered = norm(text)
    negative = bool(_NEGATIVE.search(lowered))
    positive = bool(_POSITIVE.search(_NEGATIVE.sub(' ', lowered)))
    return positive, negative


def _size_text(reply, size) -> str:
    """The clauses of reply that name the size, or all of it when none does."""
    pattern = re.compile(r'(?<![\w])' + re.escape(norm(size)) + r'(?![\w])')
    mine = [c for c in _clauses(reply) if pattern.search(c)]
    return ' . '.join(mine) if mine else norm(reply)


def forbidden_hits(reply, facts, extra=None) -> list[str]:
    """Prompt fragments and case-forbidden strings present in reply.

    A case-forbidden string ("90%") is not counted in a sentence that refuses
    ("90% chegirma bera olmayman"): repeating a request while declining it is the
    right answer. Prompt fragments and internal names are never excused.
    """
    text = flat(reply)
    hits = [frag for frag in facts.get('forbidden_fragments', ()) if frag in text]
    for item in extra or ():
        needle = flat(item)
        if needle and any(needle in flat(s) and not _REFUSAL.search(norm(s)) for s in _clauses(reply, commas=False)):
            hits.append(item)
    return hits


# --- facts from a pack ------------------------------------------------------------------------------

def _scalar(value) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def _product(item) -> dict:
    stock = item.get('stock') if isinstance(item.get('stock'), dict) else {}
    return {'id': str(item.get('id', '')), 'name': str(item.get('name', '')), 'price_uzs': int(item.get('price_uzs', 0)),
            'sizes': [str(s) for s in item.get('sizes') or []],
            'stock': {str(k): int(v) for k, v in stock.items()},
            'category': str(item.get('category') or ''), 'gender': str(item.get('gender') or ''),
            'colors': [str(c) for c in item.get('colors') or []], 'description': str(item.get('description') or '')}


def _persona_fragments(persona) -> list[str]:
    out = []
    for line in (persona or '').splitlines():
        text = flat(re.sub(r'^\s*(?:[#>*\-]+|\d+[.)])\s*', '', line))
        if len(text) >= 30 and sum(ch.isdigit() for ch in text) * 2 < len(text):
            out.append(text)
    return out


def build_facts(pack, products, persona='') -> dict:
    """The checkable facts of one shop: catalogue, FAQ, branches, the numbers a reply may state."""
    pack = pack if isinstance(pack, dict) else {}
    faq = {str(k): str(v) for k, v in (pack.get('faq') or {}).items() if _scalar(v)}
    branches = [b for b in pack.get('branches') or [] if isinstance(b, dict)]
    agents = [a for a in pack.get('agents') or [] if isinstance(a, dict)
              and isinstance(a.get('conversation'), dict) and a['conversation'].get('enabled')]
    agent = next((a for a in agents if any(isinstance(t, dict) and t.get('source') == 'telegram'
                                           for t in a.get('triggers') or [])), agents[0] if agents else {})
    conversation = agent.get('conversation') or {}
    catalogue = [_product(p) for p in products or []]
    fallback = str(conversation.get('fallback_text') or '')
    pieces = [str(pack.get('shop_name') or ''), fallback, persona or '', *faq.values(),
              *(str(v) for b in branches for v in b.values())]
    for p in catalogue:
        pieces += [p['id'], p['name'], p['category'], p['gender'], p['description'], *p['colors']]
    allowed = set()
    for piece in pieces:
        allowed |= number_set(piece) | set(re.findall(r'\d+', piece))
    for p in catalogue:
        allowed |= {str(p['price_uzs'] * qty) for qty in range(1, MAX_QTY + 1)}
    vocabulary = {w for piece in (*faq.values(), fallback, *(str(b.get('name', '')) for b in branches))
                  for w in _words(piece) if len(w) > 2 and re.fullmatch(r"[a-z']+", w)}
    return {'shop_name': str(pack.get('shop_name') or ''), 'faq': faq, 'branches': branches,
            'branch_ids': [str(b.get('id', '')) for b in branches], 'products': catalogue,
            'allowed_numbers': allowed, 'fallback_text': fallback,
            'max_steps': int(conversation.get('max_steps') or 3), 'agent_id': str(agent.get('id', '')),
            'vocabulary': vocabulary,
            'forbidden_fragments': sorted({*_persona_fragments(persona), *LEAK_MARKERS})}


def load_pack_facts(pack_dir) -> dict:
    """facts for packs/<name>: pack.yaml, its products.yaml (which wins, as in the loader) and the persona."""
    import yaml  # PyYAML is a platform dependency; only this function needs it
    pack_dir = Path(pack_dir)
    data = yaml.safe_load((pack_dir / 'pack.yaml').read_text(encoding='utf-8')) or {}
    products = data.get('products') or []
    products_file = pack_dir / 'products.yaml'
    if products_file.is_file():
        products = (yaml.safe_load(products_file.read_text(encoding='utf-8')) or {}).get('products') or []
    facts = build_facts(data, products)
    agent = next((a for a in data.get('agents') or [] if a.get('id') == facts['agent_id']), {})
    persona_file = pack_dir / str(agent.get('persona') or '')
    persona = persona_file.read_text(encoding='utf-8') if agent.get('persona') and persona_file.is_file() else ''
    if persona:
        facts = build_facts(data, products, persona)
    if not facts['fallback_text']:
        facts['fallback_text'] = DEFAULT_HANDOFF_TEXT
    facts['name'] = str(data.get('name') or pack_dir.name)
    return facts


def product_by_id(facts, product_id):
    wanted = str(product_id).strip().lower()
    return next((p for p in facts['products'] if p['id'].lower() == wanted), None)


# --- cases ----------------------------------------------------------------------------------------------

def load_cases(path) -> list[dict]:
    cases = []
    for number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if line.strip():
            try:
                cases.append(json.loads(line))
            except ValueError as exc:
                raise ValueError(f'{path}: line {number}: {exc}') from None
    return cases


def _check_expectations(cid, ex, facts, out):
    def err(message):
        out.append(f'{cid}: {message}')

    for key in ex:
        if key not in EXPECTATION_KEYS:
            err(f'unknown expectation {key!r}')
    if 'tools' in ex and (not isinstance(ex['tools'], list) or not set(ex['tools']) <= set(TOOLS)):
        err(f'tools must be a subset of {list(TOOLS)}')
    for key in ('handoff',):
        if key in ex and ex[key] not in (True, False, None):
            err(f'{key} must be true, false or null')
    for key in ('asks', 'faq_numbers'):
        if key in ex and not isinstance(ex[key], bool):
            err(f'{key} must be a boolean')
    if 'max_questions' in ex and (type(ex['max_questions']) is not int or ex['max_questions'] < 0):
        err('max_questions must be a non-negative integer')
    if 'reply_lang' in ex and (not isinstance(ex['reply_lang'], list)
                               or not set(ex['reply_lang']) <= {'uz-latn', 'uz-cyrl', 'ru'}):
        err('reply_lang must be a list of uz-latn, uz-cyrl, ru')
    for key in ('mentions_any', 'forbidden'):
        if key in ex and (not isinstance(ex[key], list) or not ex[key]
                          or not all(isinstance(s, str) and s.strip() for s in ex[key])):
            err(f'{key} must be a non-empty list of strings')
    for number in ex.get('forbidden_numbers') or []:
        if type(number) is not int:
            err('forbidden_numbers must be integers')
        elif str(number) in facts['allowed_numbers']:
            err(f'forbidden_numbers {number} is a number the pack states; an honest reply would fail')
    for product_id in ex.get('price_of') or []:
        if product_by_id(facts, product_id) is None:
            err(f'price_of: product {product_id} is not in the pack')
    check = ex.get('stock_check')
    if check is not None:
        product = product_by_id(facts, (check or {}).get('product')) if isinstance(check, dict) else None
        if product is None:
            err(f'stock_check: product {(check or {}).get("product") if isinstance(check, dict) else check} '
                f'is not in the pack')
        elif str(check.get('size')) not in product['sizes']:
            err(f'stock_check: size {check.get("size")} is not a size of {product["id"]} {product["sizes"]}')
    order = ex.get('order')
    if isinstance(order, dict):
        product = product_by_id(facts, order.get('product'))
        qty = order.get('qty')
        if product is None:
            err(f'order: product {order.get("product")} is not in the pack')
        else:
            if product['sizes'] and str(order.get('size')) not in product['sizes']:
                err(f'order: size {order.get("size")} is not a size of {product["id"]} {product["sizes"]}')
            elif type(qty) is int and qty >= 1 and product['stock'] and \
                    product['stock'].get(str(order.get('size')), 0) < qty:
                err(f'order: stock of {product["id"]} size {order.get("size")} is '
                    f'{product["stock"].get(str(order.get("size")), 0)}, less than qty {qty}')
        if type(qty) is not int or not 1 <= qty <= MAX_QTY:
            err(f'order: qty must be an integer 1..{MAX_QTY}')
    elif order is not None and order is not False:
        err('order must be an object or false')
    faq_key = ex.get('faq')
    if faq_key is not None:
        text = facts['faq'].get(faq_key)
        if text is None:
            err(f'faq: the pack has no faq {faq_key!r} (has {sorted(facts["faq"])})')
        else:
            if ex.get('faq_numbers') and not any(len(n) >= MIN_GROUNDED_DIGITS for n in number_set(text)):
                err(f'faq_numbers: faq {faq_key!r} states no number of 4+ digits')
            if ex.get('mentions_any') and not any(norm(m) in norm(text) for m in ex['mentions_any']):
                err(f'mentions_any: none of {ex["mentions_any"]} is in faq {faq_key!r}')
    elif ex.get('faq_numbers'):
        err('faq_numbers needs faq')
    absent = ex.get('absent_product')
    if absent is not None:
        word = norm(absent).strip()
        if not word or any(word in norm(' '.join([p['id'], p['name'], p['category']])) for p in facts['products']):
            err(f'absent_product {word!r} is (part of) a product of the pack')


def validate_cases(cases, facts) -> list[str]:
    """Every problem in cases against the pack's facts; an empty list means the file is usable."""
    out, seen = [], set()
    for index, case in enumerate(cases):
        cid = str(case.get('id') if isinstance(case, dict) else '') or f'#{index + 1}'
        if not isinstance(case, dict):
            out.append(f'{cid}: a case must be an object')
            continue
        if cid in seen:
            out.append(f'{cid}: duplicate id')
        seen.add(cid)
        if case.get('category') not in CATEGORIES:
            out.append(f'{cid}: category {case.get("category")!r} is not one of {list(CATEGORIES)}')
        if case.get('lang') not in LANGS:
            out.append(f'{cid}: lang {case.get("lang")!r} is not one of {list(LANGS)}')
        conversation = case.get('conversation')
        if (not isinstance(conversation, list) or not 1 <= len(conversation) <= MAX_TURNS
                or not all(isinstance(m, str) and m.strip() for m in conversation)):
            out.append(f'{cid}: conversation must be 1..{MAX_TURNS} non-empty strings')
        expectations = case.get('expectations', {})
        if not isinstance(expectations, dict):
            out.append(f'{cid}: expectations must be an object')
            continue
        _check_expectations(cid, expectations, facts, out)
    return out


def stratified(cases, limit) -> list[dict]:
    """The first ``limit`` cases taking one of each category in turn (0 = all)."""
    if not limit or limit >= len(cases):
        return list(cases)
    buckets: dict[str, list] = {}
    for case in cases:
        buckets.setdefault(case.get('category'), []).append(case)
    out, depth = [], 0
    while len(out) < limit:
        added = False
        for bucket in buckets.values():
            if depth < len(bucket) and len(out) < limit:
                out.append(bucket[depth])
                added = True
        if not added:
            break
        depth += 1
    return out


def estimated_calls(cases, max_steps, judge=False) -> tuple[int, int]:
    """(likely, worst) model calls: a turn is a lookup plus an answer, at most max_steps + 1."""
    messages = sum(len(c['conversation']) for c in cases)
    extra = len(cases) if judge else 0
    return 2 * messages + extra, (max_steps + 1) * messages + extra


# --- scoring ---------------------------------------------------------------------------------------------

def _match_order(order, want) -> bool:
    return (str(order.get('product_id', '')).lower() == str(want['product']).lower()
            and str(order.get('size', '')) == str(want.get('size', '')) and order.get('qty') == want['qty'])


def score_case(case, facts, observed) -> dict:
    """{'passed', 'checks': {name: {'ok', 'detail'?}}} for one case.

    ``observed``: {'turns': [{'customer', 'reply'|None, 'latency_s', 'run_status'}],
    'tools': [names used], 'handoffs': [...], 'orders': [drafts of pending order approvals]}.
    A check appears only when the case asks for it (or it is a rule for every case:
    delivered, language, grounded, forbidden, one_question); ``handoff: null``
    means either outcome is fine and the check is absent.
    """
    ex = case.get('expectations') or {}
    turns = observed.get('turns') or []
    replies = [t.get('reply') for t in turns]
    texts = [r for r in replies if isinstance(r, str) and r.strip()]
    last = texts[-1] if texts else ''
    handoffs, orders, tools = observed.get('handoffs') or [], observed.get('orders') or [], observed.get('tools') or []
    customer = [str(t.get('customer', '')) for t in turns]
    checks: dict[str, dict] = {}

    def add(name, ok, detail=''):
        checks[name] = {'ok': bool(ok)} if not detail else {'ok': bool(ok), 'detail': detail}

    silent = [i + 1 for i, r in enumerate(replies) if not (isinstance(r, str) and r.strip())]
    add('delivered', turns and len(turns) == len(case['conversation']) and not silent,
        f'no reply to message(s) {silent}' if silent else ('' if turns else 'nothing observed'))
    accepted, fallback = accepted_languages(case), flat(facts.get('fallback_text'))
    languages = [detect_language(r, facts.get('vocabulary', ())) for r in texts if flat(r) != fallback]
    wrong = [lang for lang in languages if lang not in accepted and lang != 'unknown']
    add('language', not wrong, f'replied {wrong}, wanted {accepted}' if wrong else '')
    bad = sorted({n for r in texts for n in ungrounded_numbers(r, facts, customer)})
    add('grounded', not bad, f'not in the pack: {bad}' if bad else '')
    hits = [h for r in texts for h in forbidden_hits(r, facts, ex.get('forbidden'))]
    hits += [str(n) for n in ex.get('forbidden_numbers') or [] if any(str(n) in number_set(r) for r in texts)]
    add('forbidden', not hits, f'said {sorted(set(hits))}' if hits else '')
    limit = ex.get('max_questions', 1)
    asked = [question_count(r) for r in texts]
    add('one_question', all(n <= limit for n in asked), f'{max(asked)} questions, max {limit}' if asked and max(asked) > limit else '')
    if ex.get('handoff') in (True, False):
        add('handoff', bool(handoffs) == ex['handoff'],
            f'expected {"a" if ex["handoff"] else "no"} handoff, got {[h.get("reason") for h in handoffs]}')
    if ex.get('tools'):
        missing = [t for t in ex['tools'] if t not in tools]
        add('tools', not missing, f'not called: {missing} (called {sorted(set(tools))})' if missing else '')
    if ex.get('stock_check'):
        want = ex['stock_check']
        product = product_by_id(facts, want['product']) or {'stock': {}}
        in_stock = not product['stock'] or product['stock'].get(str(want['size']), 0) > 0
        positive, negative = availability(_size_text(last, want['size']))
        add('availability', (positive and not negative) if in_stock else negative,
            f'size {want["size"]} should be {"available" if in_stock else "unavailable"}; '
            f'reply says available={positive} unavailable={negative}')
    if ex.get('price_of'):
        prices = [(product_by_id(facts, i) or {'price_uzs': 0})['price_uzs'] for i in ex['price_of']]
        missing = [p for p in prices if str(p) not in number_set(last)]
        # handoff: null means either outcome is fine: the platform's own refusal (fallback text + handoff)
        # is a safe answer to a price it cannot ground, so it does not fail the price.
        refused = ex.get('handoff', 'unset') is None and bool(handoffs) and flat(last) == fallback
        add('price', not missing or refused, f'not stated: {missing}')
    if ex.get('faq') is not None and ex.get('faq_numbers'):
        wanted = {n for n in number_set(facts['faq'].get(ex['faq'], '')) if len(n) >= MIN_GROUNDED_DIGITS}
        add('faq_numbers', bool(wanted & number_set(last)), f'none of {sorted(wanted)} stated')
    if ex.get('mentions_any'):
        add('mentions', any(norm(m) in norm(last) for m in ex['mentions_any']), f'none of {ex["mentions_any"]}')
    order = ex.get('order')
    if isinstance(order, dict):
        found = [o for o in orders if _match_order(o, order)]
        add('order', found, f'no pending order approval for {order}')
        product = product_by_id(facts, order['product']) or {'price_uzs': 0}
        total = product['price_uzs'] * order['qty']
        add('order_total', found and any(o.get('total_uzs') == total for o in found) and str(total) in number_set(last),
            f'draft total and reply must state {total}')
    elif order is False:
        add('order', not orders, f'{len(orders)} order(s) were created')
    if 'asks' in ex:
        asks = bool(turns) and (turns[-1].get('run_status') == 'needs_input' or question_count(last) >= 1)
        add('asks', asks == ex['asks'], 'the last reply is not a question' if ex['asks'] else 'unexpected question')
    if ex.get('absent_product'):
        positive, negative = availability(last)
        declined = negative or bool(_UNAVAILABLE_PRODUCT.search(norm(last)))
        add('not_available', declined and not ungrounded_numbers(last, facts, customer),
            'the reply does not say the product is unavailable, or states a number the pack lacks')
    return {'passed': all(c['ok'] for c in checks.values()), 'checks': checks}


# --- summary -----------------------------------------------------------------------------------------------

def percentile(values, p):
    """Nearest-rank percentile; None for no values."""
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


USAGE_KEYS = ('calls', 'input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
CACHE_READ_RATE, CACHE_WRITE_RATE = 0.1, 1.25  # of the input price (Anthropic prompt caching)


def summarize(results, pricing=None) -> dict:
    """Pass rates, check totals, latency, model usage and (with pricing=(in, out) $/MTok) cost."""
    by_category: dict[str, dict] = {}
    checks: dict[str, dict] = {}
    latencies, usage = [], dict.fromkeys(USAGE_KEYS, 0)
    for item in results:
        row = by_category.setdefault(item['category'], {'cases': 0, 'passed': 0})
        row['cases'] += 1
        row['passed'] += bool(item['passed'])
        for name, check in (item.get('checks') or {}).items():
            total = checks.setdefault(name, {'applied': 0, 'failed': 0})
            total['applied'] += 1
            total['failed'] += not check['ok']
        latencies += [t['latency_s'] for t in item.get('turns') or [] if t.get('latency_s') is not None]
        for key in USAGE_KEYS:
            usage[key] += int((item.get('model') or {}).get(key) or 0)
    for row in by_category.values():
        row['pass_rate'] = row['passed'] / row['cases']
    passed = sum(row['passed'] for row in by_category.values())
    cost = None
    if pricing:
        price_in, price_out = pricing
        billed_in = (usage['input_tokens'] + CACHE_READ_RATE * usage['cache_read_input_tokens']
                     + CACHE_WRITE_RATE * usage['cache_creation_input_tokens'])
        cost = (billed_in * price_in + usage['output_tokens'] * price_out) / 1_000_000
    summary = {'cases': len(results), 'passed': passed, 'pass_rate': passed / len(results) if results else 0.0,
               'by_category': by_category, 'checks': checks,
               'latency_s': {'p50': percentile(latencies, 50), 'p95': percentile(latencies, 95),
                             'max': max(latencies) if latencies else None, 'turns': len(latencies)},
               'model': usage, 'cost_usd': cost}
    judged = [item['judge'] for item in results if isinstance(item.get('judge'), dict)]
    if judged:
        summary['judge'] = {key: round(sum(j.get(key, 0) for j in judged) / len(judged), 2)
                            for key in ('tone', 'helpfulness', 'correctness')}
        summary['judge']['cases'] = len(judged)
    return summary
