"""Shop read tools: the facts a sales conversation may state, and a priced order draft.

Both tools read ONE tenant's shop through an injected reader, the way
``products.search`` reads an injected catalogue (``build_registry(catalog, shop)``):
this runtime never imports the API layer and never names a pack, a product or a
branch. The reader returns plain data::

    {'shop_name': str, 'faq': {str: str},
     'branches': [{'id', 'name', 'address', 'phone', 'hours'}],
     'products': [{'id', 'name', 'price_uzs', 'sizes', 'stock'}]}

Both are ``read`` risk and write nothing. ``orders.draft`` checks an order against
the catalogue and prices it; the model never supplies a price. Capturing a valid
draft is ``conversation.ConversationTurns``' job, through a pack-chosen agent whose
ladder decides whether a human approves it.
"""
import re

from .engine import encode
from .tools import Tool, obj, register_once, string

SHOP_TOOLS = ('shop.info', 'orders.draft')

# shop.info bounds. The result is one agent-loop observation, so the whole of it
# stays under agent_loop.MAX_OBSERVATION_BYTES (12000) with room for the wrapper.
MAX_SHOP_INFO_BYTES = 8000
MAX_SHOP_NAME_CHARS = 200
MAX_FAQ_ENTRIES = 12
MAX_FAQ_KEY_CHARS = 64
MAX_FAQ_TEXT_CHARS = 600
MAX_BRANCHES = 10
BRANCH_FIELDS = {'id': 64, 'name': 120, 'address': 200, 'phone': 32, 'hours': 64}

# orders.draft bounds.
MIN_QTY, MAX_QTY = 1, 99
MIN_NAME_CHARS, MAX_NAME_CHARS = 2, 100
MIN_ADDRESS_CHARS, MAX_ADDRESS_CHARS = 5, 300
MAX_PRODUCT_NAME_CHARS = 120
MAX_SIZE_CHARS = 16
# Wide on purpose: an out-of-range quantity is a problem the model can fix with
# the customer, not a schema refusal that ends the whole turn.
QTY_SCHEMA = {'type': 'integer', 'minimum': 0, 'maximum': 100000}
# Digits plus the punctuation people type in a phone number, nothing else.
PHONE_CHARS = re.compile(r'[\d\s+()\-.]+')
CURRENCY = 'UZS'


def _text(value, limit):
    return value[:limit] if isinstance(value, str) else ''


def shop_info(data):
    """Shop name, FAQ and branches, bounded. Products are products.search's job."""
    data = data if isinstance(data, dict) else {}
    raw_faq = data.get('faq') if isinstance(data.get('faq'), dict) else {}
    truncated = len(raw_faq) > MAX_FAQ_ENTRIES
    faq = {}
    for key, value in list(raw_faq.items())[:MAX_FAQ_ENTRIES]:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            continue
        text = str(value)
        truncated = truncated or len(text) > MAX_FAQ_TEXT_CHARS
        faq[str(key)[:MAX_FAQ_KEY_CHARS]] = text[:MAX_FAQ_TEXT_CHARS]
    raw_branches = data.get('branches') if isinstance(data.get('branches'), list) else []
    truncated = truncated or len(raw_branches) > MAX_BRANCHES
    branches = [{field: str(item.get(field) or '')[:limit] for field, limit in BRANCH_FIELDS.items()}
                for item in raw_branches[:MAX_BRANCHES] if isinstance(item, dict)]
    name = str(data.get('shop_name') or '')
    truncated = truncated or len(name) > MAX_SHOP_NAME_CHARS
    out = {'shop_name': name[:MAX_SHOP_NAME_CHARS], 'faq': faq, 'branches': branches,
           'truncated': truncated}
    # Byte bound last: drop the last FAQ entry, then the last branch, until it fits.
    while len(encode(out).encode('utf-8')) > MAX_SHOP_INFO_BYTES and (out['faq'] or out['branches']):
        if out['faq']:
            out['faq'].pop(next(reversed(out['faq'])))
        else:
            out['branches'].pop()
        out['truncated'] = True
    return out


def normalize_uz_phone(value):
    """'+998XXXXXXXXX' from what a customer types, or None.

    Accepts the full international form, 998 without the plus, and the nine-digit
    local form. Letters, or any other digit count, are refused rather than guessed.
    """
    if not isinstance(value, str) or not PHONE_CHARS.fullmatch(value.strip() or 'x'):
        return None
    digits = re.sub(r'\D', '', value)
    if len(digits) == 9:
        digits = '998' + digits
    if len(digits) != 12 or not digits.startswith('998'):
        return None
    return '+' + digits


def _find(items, wanted):
    wanted = wanted.strip().lower() if isinstance(wanted, str) else ''
    return next((item for item in items if isinstance(item, dict)
                 and str(item.get('id', '')).strip().lower() == wanted), None) if wanted else None


def order_draft(data, args):
    """{valid, draft, problems}: the order checked against the shop and priced here."""
    data = data if isinstance(data, dict) else {}
    products = data.get('products') if isinstance(data.get('products'), list) else []
    branches = data.get('branches') if isinstance(data.get('branches'), list) else []
    problems, draft = [], {'currency': CURRENCY}

    product = _find(products, args.get('product_id'))
    qty = args.get('qty')
    qty_ok = type(qty) is int and MIN_QTY <= qty <= MAX_QTY
    if not qty_ok:
        problems.append({'field': 'qty', 'code': 'qty_out_of_range', 'min': MIN_QTY, 'max': MAX_QTY})
    else:
        draft['qty'] = qty
    if product is None:
        problems.append({'field': 'product_id', 'code': 'unknown_product'})
    else:
        sizes = [str(size) for size in product.get('sizes') or []]
        stock = product.get('stock') if isinstance(product.get('stock'), dict) else {}
        in_stock = [size for size in sizes if not stock or stock.get(size, 0) > 0]
        draft.update(product_id=str(product.get('id')),
                     product_name=str(product.get('name', ''))[:MAX_PRODUCT_NAME_CHARS])
        wanted = args.get('size', '')
        size = next((s for s in sizes if s.lower() == str(wanted).strip().lower()), None)
        if sizes and not str(wanted).strip():
            problems.append({'field': 'size', 'code': 'size_required', 'available': in_stock})
        elif sizes and size is None:
            problems.append({'field': 'size', 'code': 'size_unavailable', 'available': in_stock})
        elif sizes and stock and qty_ok and stock.get(size, 0) < qty:
            problems.append({'field': 'size', 'code': 'out_of_stock',
                             'available_qty': max(stock.get(size, 0), 0), 'available': in_stock})
        draft['size'] = (size or '')[:MAX_SIZE_CHARS] if sizes else ''
        price = product.get('price_uzs')
        if type(price) is int:
            draft['unit_price_uzs'] = price
            if qty_ok:
                draft['total_uzs'] = price * qty

    name = ' '.join(str(args.get('customer_name', '')).split())
    if not MIN_NAME_CHARS <= len(name) <= MAX_NAME_CHARS:
        problems.append({'field': 'customer_name', 'code': 'name_required'})
    else:
        draft['customer_name'] = name
    phone = normalize_uz_phone(args.get('phone'))
    if phone is None:
        problems.append({'field': 'phone', 'code': 'invalid_phone', 'format': '+998XXXXXXXXX'})
    else:
        draft['phone'] = phone

    delivery = ' '.join(str(args.get('delivery', '')).split())
    branch = _find(branches, delivery)
    if branch is not None:
        draft['delivery'] = {'type': 'branch', 'branch_id': str(branch.get('id')),
                             'branch_name': str(branch.get('name', ''))[:BRANCH_FIELDS['name']],
                             'address': str(branch.get('address', ''))[:BRANCH_FIELDS['address']]}
    elif MIN_ADDRESS_CHARS <= len(delivery) <= MAX_ADDRESS_CHARS:
        draft['delivery'] = {'type': 'address', 'address': delivery}
    else:
        problems.append({'field': 'delivery', 'code': 'delivery_invalid',
                         'branches': [str(b.get('id')) for b in branches[:MAX_BRANCHES] if isinstance(b, dict)]})
    return {'valid': not problems, 'draft': draft, 'problems': problems}


def register_shop_tools(registry, shop):
    """Register both tools over ``shop(tenant) -> dict``. Idempotent (register_once)."""
    def info(engine, tenant, agent, args, key):
        return shop_info(shop(tenant))

    def draft(engine, tenant, agent, args, key):
        return order_draft(shop(tenant), args)

    register_once(registry, [
        Tool('shop.info', 'read', obj({}), info),
        Tool('orders.draft', 'read', obj({
            'product_id': string(64), 'size': string(MAX_SIZE_CHARS), 'qty': QTY_SCHEMA,
            'customer_name': string(MAX_NAME_CHARS), 'phone': string(32),
            'delivery': string(MAX_ADDRESS_CHARS)},
            ['product_id', 'qty', 'customer_name', 'phone', 'delivery']), draft),
    ])
