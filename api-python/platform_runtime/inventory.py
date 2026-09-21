"""What a product costs, what it is worth, and whether we can sell it (PRD v0.4, P4).

The customer named this problem twice, in the words the v0.4 implementation note
recorded: *"Sotuvchi narxni olib yurmasin"* -- the seller must not carry the price
around. Today a salesperson asks finance for a price over Telegram, gets a number
typed from memory, and quotes it. The platform already reads MoySklad stock, 1C
accounting and a finance sheet through the Business Graph, but nothing answers the
seller's actual question in the seller's own words.

This module is that answer, and it is deliberately the *narrowest* answer the
customer's declared registers can support.

The line this module does not cross
-----------------------------------

**It reports the price and the stock. It does not decide the price.**

A price is a commercial decision. The platform reads what the operator declared,
says which system produced each number, and when two systems disagree it *shows
the disagreement* rather than picking one. The PRD is explicit that both numbers
are legitimate -- "buxgalter 1C raqamini bilishi kerak, sotuvchi MoySklad
raqamini" -- and silent selection is what produces an argument between a
salesperson and an accountant. So:

* Every figure carries its **source** and the moment we read it. A price with no
  provenance is a number nobody can defend.
* A conflict between systems is **reported**, and the operator's declared
  ``source_priority`` decides the default under ``primary_wins``; under ``report``
  nothing is selected at all. This module reuses the Business Graph's resolution
  rather than re-implementing a second opinion about the same question.
* **No margin is computed** unless the customer declared both a price and a cost.
  A "margin" derived from a price and a *guessed* cost is a number a manager
  would make a decision with.
* **No availability claim.** Stock being non-zero is not the same as "available":
  the platform does not know about reservations, quarantine, backorder or a
  warehouse the register does not track. It reports the quantity it read, and the
  unit, and refuses to translate that into a yes.

Boundaries that make this safe to read:

* Every read goes through the Business Graph, which goes through the declared
  sources -- so the register declaration, A1 allowlist, agent tool permission and
  connection allowlist all apply unchanged. This module adds no new transport and
  no new authority.
* A source that cannot be read is **never** rendered as zero. It is reported as
  unread, with the exception class, and any figure that depended on it is marked
  not computable. A blank price and a zero price are different facts, and a sheet
  with no price column must not look like a free product.
* A cell that is not a finite number is reported as unreadable rather than
  coerced. ``nan`` and ``inf`` are real floats and would otherwise sail through.
* Nothing here evaluates a person. There is no per-seller price or per-seller
  discount figure: the same number attributed to the salesperson who quoted it is
  a sanction waiting to happen, and the platform did not make the quote.
* There is no write path in this file at all. A price or a stock level is
  *inventory master data*, and an entity able to edit the price it is reporting on
  would be the author of the number a salesperson quotes.
"""
from __future__ import annotations

import re

from . import business_graph
from .engine import Forbidden

INVENTORY_TOOLS = ('inventory.product', 'inventory.stock', 'inventory.price',
                   'inventory.margin')

# Mirrors the register ceiling the whole family respects: a read can never scan
# more than the source tool will return.
MAX_ROWS = 200
MAX_ITEMS = 100
# There is deliberately no MAX_NAMES here. This module returns products, not
# people: `MAX_NAMES` in the sibling modules bounds a *names* list (who was
# absent, who is late). A constant that bounds nothing is worse than an absent
# one, because it advertises a limit nobody enforces. Removed during the audit
# that found the same "declared then read by nobody" shape in `source_priority`.
#
# `MAX_QUANTITY = 10 ** 15` used to sit on the next line, and nothing read it:
# `_number` parses with a bounded regex, the figures travel as floats, and no
# comparison in this module consults a ceiling. It was the exact shape the
# paragraph above describes, one line below the paragraph that describes it, so
# the fazza-26 audit removed it. A ceiling nothing enforces is a ceiling an
# operator will believe in.

# A product identity a customer's own system can hold. Deliberately permissive
# about punctuation, because SKUs in the wild are 'SKU-1042', 'A.1/B' and worse.
IDENTITY_RE = re.compile(r'^[^\s\x00-\x1f]{1,64}$')

# A number a spreadsheet or a database can hold. Parsed, never guessed: an
# ambiguous cell is reported unreadable rather than treated as zero. The ASCII
# check is part of "not guessed" -- see ``cells`` for why ``\d`` is not enough.
NUMBER_RE = re.compile(r'^-?[0-9]{1,15}(?:[.,][0-9]{1,9})?$')

# The reasons that mean "the platform would not choose", as opposed to "the data
# does not hold this". The distinction decides which of the two output lists an
# operator is sent to, so it lives here once rather than being spelled out at each
# call site -- the earlier per-call-site version compared against ``'conflict'``
# only, which silently reclassified every later policy reason as missing data.
POLICY_REASONS = ('conflict', 'priority_unreadable')


class InventoryError(RuntimeError):
    """A deterministic refusal about an inventory read, not a transport failure."""


def _bounded(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def _identity(value, name='id', required=True):
    if value is None or value == '':
        if required:
            raise ValueError(f'{name} is required')
        return ''
    if not isinstance(value, str):
        value = str(value)
    text = value.strip()
    if not IDENTITY_RE.match(text):
        raise ValueError(f'{name} is not a valid product identity')
    return text


def _number(value):
    """Parse a declared numeric cell, or ``None`` when it is not a number.

    ``None`` and ``0`` are different facts, and this is the whole reason the
    function exists. A blank price that becomes zero understates revenue and
    tells a salesperson the product is free; a blank stock count that becomes
    zero tells them it is out of stock and loses a sale. Both are reported as
    unread through the caller's counter instead.

    A **non-finite** value is not a number either. ``nan`` and ``inf`` are real
    Python floats and would otherwise sail through as "readable", and an infinite
    quantity would accumulate into a stock total no downstream guard catches. A
    **large integer** is refused for the same reason and by the same rule: it is
    not a value this platform can carry, so it is unreadable rather than fatal.
    The same hole was closed in P13's ``oee._number`` and P12's
    ``manufacturing._number``; all three places that interpret a cell now refuse
    a cell that is not a finite number.
    """
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # An integer can be too large for a float, and `float()` RAISES rather than
        # returning infinity when it is. A cell the platform cannot represent is
        # unreadable, exactly like a blank one: this function's contract is to
        # report that, never to raise out of a read. Measured: `10 ** 400`.
        try:
            number = float(value)
        except OverflowError:
            return None
        return number if -float('inf') < number < float('inf') else None
    text = str(value).strip()
    if not NUMBER_RE.match(text):
        return None
    return float(text.replace(',', '.'))


def _text(value, maximum=128):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)[:maximum]


def _authority(engine, tenant, agent):
    """The ladder the figures were read under, carried into the output.

    A price that does not say which authority produced it cannot be audited
    later, and a stock level read under ``autonomous`` is a different fact from
    one read under ``human_led``. Every read in this family ships this block.
    """
    return {'agent': agent,
            'ladder': str(engine.policy(tenant, agent).get('ladder', ''))}


# ------------------------------------------------------------------ config


def inventory_config(tenant) -> dict:
    """Validate the tenant's declared inventory mapping, or an empty one.

    Configuration errors raise instead of being skipped: a typo in an attribute
    name must not silently produce a missing figure, because "the system has no
    price for this product" and "we looked for the wrong attribute" would look
    identical to a salesperson.

    This block declares no register, no column and no connection of its own. It
    names an **entity and attributes** in the Business Graph, because the graph is
    already the module that resolves which systems disagree; a second reader with
    its own idea of a source list would be a second answer to the same question.
    """
    from .tools import config
    raw = config(tenant).get('inventory')
    if raw is None:
        return {'entity': '', 'identity': 'sku', 'attributes': {}}
    if not isinstance(raw, dict):
        raise ValueError('inventory must be an object')
    unknown = set(raw) - {'entity', 'identity', 'attributes'}
    if unknown:
        raise ValueError(f'inventory has unsupported keys: {sorted(unknown)}')
    entity = raw.get('entity', '')
    if not isinstance(entity, str):
        raise ValueError('inventory.entity must be a string')
    if entity and not business_graph.ENTITY_RE.match(entity):
        raise ValueError(f'inventory.entity must match '
                         f'{business_graph.ENTITY_RE.pattern}')
    identity = raw.get('identity', 'sku')
    if not isinstance(identity, str) or not business_graph.ATTRIBUTE_RE.match(identity):
        raise ValueError('inventory.identity must be a valid attribute name')
    attributes = raw.get('attributes', {})
    if not isinstance(attributes, dict):
        raise ValueError('inventory.attributes must be an object')
    if len(attributes) > business_graph.MAX_PRIORITY_ATTRIBUTES:
        raise ValueError(f'inventory.attributes is limited to '
                         f'{business_graph.MAX_PRIORITY_ATTRIBUTES} entries')
    clean = {}
    for role, attribute in attributes.items():
        if role not in ATTRIBUTE_ROLES:
            raise ValueError(f'inventory.attributes has an unsupported role '
                             f'{role!r}; expected one of {sorted(ATTRIBUTE_ROLES)}')
        if not isinstance(attribute, str) or not business_graph.ATTRIBUTE_RE.match(attribute):
            raise ValueError(f'inventory.attributes.{role} must be a valid attribute '
                             f'name')
        clean[role] = attribute
    if not clean:
        raise ValueError('inventory.attributes declares no attribute at all')
    return {'entity': entity, 'identity': identity, 'attributes': clean}


# The roles this block understands. A role is a *meaning* ("the unit price"),
# mapped by the operator to whatever attribute their graph declared. Naming the
# graph attribute directly in the tool argument instead would let a model choose
# which number it calls a price.
ATTRIBUTE_ROLES = frozenset({
    'price', 'cost', 'currency', 'stock', 'unit', 'name', 'category',
})


def _settings(tenant):
    settings = inventory_config(tenant)
    if not settings['entity']:
        raise Forbidden('Inventory is not configured for this tenant: '
                        'inventory.entity is not declared')
    return settings


def _attribute(settings, role):
    """The graph attribute backing a role, or ``''`` when the operator omitted it.

    A missing role is not an error: a customer whose register has no cost column
    has not misconfigured anything, and the figures that need cost are reported
    not computable rather than the whole read failing.
    """
    configured = settings['attributes'].get(role, '')
    if configured:
        return configured
    return ''


# ------------------------------------------------------------------- reading


def _view(engine, tenant, agent, product_id, step):
    """One product's graph view. The graph resolves sources; this module reads it.

    Delegating to ``business_graph.resolve`` is what keeps this block honest: the
    authority preflight, the source-priority rule, the conflict report and the
    "a failed source is not a missing value" rule are all the graph's, already
    tested, and this module cannot drift from them by having its own copy.
    """
    settings = _settings(tenant)
    return business_graph.resolve(engine, tenant, agent, settings['entity'],
                                  product_id, step)


def _observations(view, attribute):
    """Every observation behind one attribute, in the graph's own order."""
    if not attribute:
        return []
    entry = view.get('attributes', {}).get(attribute)
    if not isinstance(entry, dict):
        return []
    values = entry.get('values')
    return values if isinstance(values, list) else []


def _figure(view, attribute, *, numeric=True):
    """One attribute reduced to a value plus the source that produced it.

    Returns a block that always names what happened, so "no value", "a value we
    cannot read" and "two systems disagree" are three distinguishable answers.
    Under ``report`` a conflicting attribute has ``selected: None`` and the
    caller must look at ``conflict``: this function does not quietly pick one, it
    reads the graph's ``selected`` -- which is the graph's decision to make.
    """
    values = _observations(view, attribute)
    if not attribute:
        return {'attribute': '', 'declared': False, 'values': [],
                'selected': None, 'conflict': False, 'unreadable': 0,
                'missing': True}
    if not values:
        return {'attribute': attribute, 'declared': True, 'values': [],
                'selected': None, 'conflict': False, 'unreadable': 0,
                'missing': True}
    entry = view['attributes'].get(attribute, {})
    selected = entry.get('selected')
    unreadable = 0
    observed = []
    for item in values:
        raw = item.get('value')
        number = _number(raw) if numeric else None
        if numeric and number is None:
            unreadable += 1
        observed.append({'source': item.get('source', ''),
                         'value': raw,
                         'number': number if numeric else None,
                         'observed': item.get('observed')})
    return {'attribute': attribute, 'declared': True, 'values': observed,
            'selected': ({'value': selected.get('value'),
                          'number': _number(selected.get('value')) if numeric else None,
                          'source': selected.get('source', '')} if selected else None),
            'conflict': bool(entry.get('conflict')),
            'unreadable': unreadable,
            'missing': False}


def _selected_number(figure):
    """The selected value as a number, or ``None`` when there is no usable one.

    Deliberately strict: a selection that exists but does not parse is ``None``,
    not zero, and the caller must consult ``unreadable`` to see why.
    """
    selected = figure.get('selected')
    if not isinstance(selected, dict):
        return None
    return selected.get('number')


def _reason(figure):
    """Why an attribute produced no usable number, as a name rather than a shrug.

    Five distinguishable answers, because an operator is sent to a different place
    by each one:

    ``''``                    a usable number was selected;
    ``'not_declared'``        the operator mapped no attribute to this role;
    ``'unread'``              a value was read and is not a finite number;
    ``'blank'``               the sources hold no value for this attribute at all;
    ``'conflict'``            two systems disagree and the policy selects nothing;
    ``'priority_unreadable'`` the policy *did* choose, and the value it chose is
                              not readable -- while another source holds one that is.

    The last two are the reasons this function exists.

    ``'conflict'`` is the ``report`` case: everything needed to compute a margin was
    read and the platform is declining to *choose* a price. Reporting that as "not
    computable" would be a lie -- it sends an operator to reconcile their registers
    when the actual fix is to declare a source priority.

    ``'priority_unreadable'`` is the ``primary_wins`` case, and it is subtler. The
    operator declared a priority, the graph honoured it, and the value the priority
    selected is a junk cell. Before this name existed the answer was ``'unread'``,
    which is true but useless: it says the cell is unreadable and stops there. An
    operator reading it goes to repair the *primary* register, when the cheaper and
    more likely correct fix is that their priority is pointing at a column that was
    never populated -- and a readable value for the same attribute is sitting in a
    source they already declared. Naming it sends them to the priority, not the cell.

    Deliberately *not* reported as ``'conflict'``: the policy chose, so there is
    nothing for the operator to reconcile. And deliberately not silently falling
    back to the readable value either: the platform does not overrule a declared
    priority, it reports that the priority produced nothing usable.
    """
    if not figure.get('declared'):
        return 'not_declared'
    if figure.get('missing'):
        return 'blank'
    selected = figure.get('selected')
    readable_other = any(item.get('number') is not None
                         for item in figure.get('values', ()))
    if figure.get('conflict') and not selected:
        return 'conflict'
    if selected is not None and selected.get('number') is None:
        # A selection exists but is not readable. If some *other* source holds a
        # number, the declared priority is what failed, not the data.
        return 'priority_unreadable' if readable_other else 'unread'
    if selected is None:
        if figure.get('unreadable'):
            return 'unread'
        return 'blank'
    return ''


def product(engine, tenant, agent, product_id, step):
    """Everything the platform knows about one product, from every declared source.

    The seller's question, answered without a round trip to finance: what does
    this cost, what do we sell it for, how much is there, and **which system said
    so**. Stock is reported as the quantity the register holds and is never
    translated into "available" -- see the module docstring.
    """
    settings = _settings(tenant)
    product_id = _identity(product_id, 'product_id')
    view = _view(engine, tenant, agent, product_id, step)

    price = _figure(view, _attribute(settings, 'price'))
    cost = _figure(view, _attribute(settings, 'cost'))
    stock = _figure(view, _attribute(settings, 'stock'))
    currency = _figure(view, _attribute(settings, 'currency'), numeric=False)
    unit = _figure(view, _attribute(settings, 'unit'), numeric=False)
    name = _figure(view, _attribute(settings, 'name'), numeric=False)

    price_value = _selected_number(price)
    cost_value = _selected_number(cost)

    # A margin is reported ONLY when both inputs are readable. A margin from a
    # guessed cost is a number a manager would price against.
    margin = None
    margin_computable = False
    if price_value is not None and cost_value is not None:
        margin = price_value - cost_value
        margin_computable = True
    price_reason = _reason(price)
    cost_reason = _reason(cost)
    # An unanswered *selection* is not the same as a missing input: one is a
    # policy decision the operator can change in a line of configuration, the
    # other is data their registers do not hold. Reporting them in one list would
    # send an operator to reconcile registers when the fix is a declared priority.
    #
    # Both policy reasons belong here, not only ``conflict``. ``priority_unreadable``
    # means a priority WAS declared and it selected an unreadable cell, so the fix is
    # still configuration -- repoint or correct the priority. Filing it under
    # ``not_computable`` would say "your data is missing", which is the one thing the
    # docstring above promises not to say.
    reasons = sorted({reason for reason in (price_reason, cost_reason) if reason})
    margin_reasons = [reason for reason in reasons
                      if reason not in POLICY_REASONS]
    margin_conflicts = [reason for reason in reasons
                        if reason in POLICY_REASONS]

    return {
        'entity': settings['entity'],
        'id': product_id,
        'name': (name.get('selected') or {}).get('value', '') if name['declared'] else '',
        'price': price,
        'cost': cost,
        'stock': stock,
        'currency': (currency.get('selected') or {}).get('value', '')
                    if currency['declared'] else '',
        'unit': (unit.get('selected') or {}).get('value', '') if unit['declared'] else '',
        'margin': margin,
        'margin_computable': margin_computable,
        'not_computable': margin_reasons,
        'margin_blocked_by_conflict': margin_conflicts,
        'conflicts': view.get('conflicts', []),
        'sources': view.get('sources', []),
        'source_errors': view.get('source_errors', []),
        'complete': bool(view.get('complete')),
        'conflict_policy': view.get('conflict_policy', ''),
        'observed': view.get('observed'),
        'authority': _authority(engine, tenant, agent),
        'note': ('Narx va tannarx faqat e’lon qilingan manbalardan o‘qiladi va har '
                 'biri o‘z manbasini ko‘rsatadi. Tizimlar kelishmasa — ziddiyat '
                 'ko‘rsatiladi, jim tanlanmaydi. Ombor soni "sotuvga tayyor" degani '
                 'emas: rezerv, karantin va hisobga olinmagan ombor platformaga '
                 'ma’lum emas.'),
    }


def stock(engine, tenant, agent, step, *, product_id='', limit=MAX_ITEMS):
    """Stock levels, for one product or for the declared catalogue.

    Returns the quantity each declared source holds. Never a boolean: whether a
    unit may be sold is a warehouse's answer, not this module's.
    """
    settings = _settings(tenant)
    limit = _bounded(limit, 'limit', 1, MAX_ITEMS)
    if product_id:
        one = product(engine, tenant, agent, product_id, step)
        return {'entity': settings['entity'], 'view': 'stock', 'items': [{
            'id': one['id'], 'stock': one['stock'], 'unit': one['unit'],
        }], 'item_count': 1, 'truncated': False, 'complete': one['complete'],
        'source_errors': one['source_errors'], 'observed': one['observed'],
        'authority': one['authority'],
        'note': 'Ombor soni — o‘qilgan qiymat, "sotuvga tayyor" degani emas.'}

    attribute = _attribute(settings, 'stock')
    ids = business_graph.identifiers(engine, tenant, agent, settings['entity'],
                                     step, MAX_ITEMS)[0]
    out = []
    errors = []
    for identifier in ids:
        view = _view(engine, tenant, agent, identifier, step)
        errors.extend(view.get('source_errors', []))
        figure = _figure(view, attribute)
        unit_attribute = _attribute(settings, 'unit')
        out.append({
            'id': identifier,
            'stock': figure,
            'unit': ((_figure(view, unit_attribute, numeric=False).get('selected') or {})
                     .get('value', '') if unit_attribute else ''),
        })
        if len(out) >= limit:
            break
    return {
        'entity': settings['entity'], 'view': 'stock', 'items': out,
        'item_count': len(out), 'scanned': len(ids),
        'truncated': len(out) >= limit and len(ids) > len(out),
        'complete': not errors, 'source_errors': errors,
        'observed': engine.clock(), 'authority': _authority(engine, tenant, agent),
        'note': 'Ombor soni — o‘qilgan qiymat, "sotuvga tayyor" degani emas.',
    }


def price(engine, tenant, agent, step, *, product_id='', limit=MAX_ITEMS):
    """Prices, with the source that produced each one.

    This is the tool that answers *"sotuvchi narxni olib yurmasin"*: a salesperson
    asks for one product, gets the price, and gets the system that said it. When
    MoySklad, 1C and the finance sheet disagree, all three are in the output, and
    which one is ``selected`` depends on the operator's declared policy -- which
    is echoed back so nobody has to guess.
    """
    settings = _settings(tenant)
    limit = _bounded(limit, 'limit', 1, MAX_ITEMS)
    if product_id:
        one = product(engine, tenant, agent, product_id, step)
        # The single-product answer carries the SAME keys as a catalogue row, plus
        # the two fields only a single lookup can supply. This is the path a
        # salesperson uses for one item, and it is the path the note below addresses
        # -- so omitting ``price_reason`` here told the reader to consult a field
        # that was not in the answer. The catalogue branch and this one returned
        # different shapes for the same question; both are now the same shape.
        reason = _reason(one['price'])
        return {'entity': settings['entity'], 'view': 'price', 'items': [{
            'id': one['id'], 'name': one['name'], 'price': one['price'],
            'currency': one['currency'], 'price_reason': reason,
            'conflicts': one['conflicts'],
        }], 'item_count': 1, 'truncated': False, 'complete': one['complete'],
        'unresolved': [reason] if reason else [],
        'conflict_policy': one['conflict_policy'],
        'source_errors': one['source_errors'], 'observed': one['observed'],
        'authority': one['authority'],
        'note': ('Narx — o‘qilgan qiymat, manbasi bilan. Ziddiyat bo‘lsa hamma '
                 'qiymat ko‘rsatiladi; tanlov operator siyosatiga bog‘liq. '
                 'price_reason "conflict" bo‘lsa — manba ustuvorligini e’lon qilish '
                 'kerak; "priority_unreadable" bo‘lsa — ustuvorlik o‘qib '
                 'bo‘lmaydigan katakka ishora qilgan, boshqa manbada qiymat bor.')}

    attribute = _attribute(settings, 'price')
    ids = business_graph.identifiers(engine, tenant, agent, settings['entity'],
                                     step, MAX_ITEMS)[0]
    out = []
    errors = []
    policy = ''
    unresolved = []
    for identifier in ids:
        view = _view(engine, tenant, agent, identifier, step)
        errors.extend(view.get('source_errors', []))
        policy = policy or view.get('conflict_policy', '')
        figure = _figure(view, attribute)
        reason = _reason(figure)
        if reason:
            unresolved.append(reason)
        # A catalogue row carries only what a per-row graph read already produced.
        # ``name`` and ``currency`` are deliberately NOT here: supplying them would
        # mean a second ``product()`` call per identifier, turning one read into N
        # for a list a caller asked to be bounded. A caller who needs the
        # single-product shape asks for one product -- which is the path that now
        # carries the same ``price_reason`` key as this one.
        out.append({'id': identifier, 'price': figure,
                    'price_reason': reason,
                    'conflicts': view.get('conflicts', [])})
        if len(out) >= limit:
            break
    return {
        'entity': settings['entity'], 'view': 'price', 'items': out,
        'item_count': len(out), 'scanned': len(ids),
        'unresolved': sorted(set(unresolved)),
        'truncated': len(out) >= limit and len(ids) > len(out),
        'complete': not errors, 'conflict_policy': policy,
        'source_errors': errors, 'observed': engine.clock(),
        'authority': _authority(engine, tenant, agent),
        'note': ('Narx — o‘qilgan qiymat, manbasi bilan. Ziddiyat bo‘lsa hamma '
                 'qiymat ko‘rsatiladi; tanlov operator siyosatiga bog‘liq. '
                 'price_reason "conflict" bo‘lsa — manba ustuvorligini e’lon qilish '
                 'kerak, ma’lumot yetishmayotgani yo‘q.'),
    }


def margin(engine, tenant, agent, step, *, product_id='', limit=MAX_ITEMS):
    """Price minus declared cost, and refused by name when either is absent.

    A margin is the one computed figure in this module, and it is computed ONLY
    from two readable inputs. If the cost column is not declared, or the cell is
    blank, or the value is not a finite number, the margin is ``None`` and
    ``not_computable`` names which input was missing -- because a margin
    assembled from one real number and one default looks complete and is not.
    """
    settings = _settings(tenant)
    limit = _bounded(limit, 'limit', 1, MAX_ITEMS)
    price_attribute = _attribute(settings, 'price')
    cost_attribute = _attribute(settings, 'cost')

    if product_id:
        one = product(engine, tenant, agent, product_id, step)
        return {'entity': settings['entity'], 'view': 'margin', 'items': [{
            'id': one['id'], 'name': one['name'],
            'price': one['price'], 'cost': one['cost'],
            'margin': one['margin'], 'margin_computable': one['margin_computable'],
            'not_computable': one['not_computable'],
            'margin_blocked_by_conflict': one['margin_blocked_by_conflict'],
            'currency': one['currency'],
        }], 'item_count': 1, 'not_computable': one['not_computable'],
        'margin_blocked_by_conflict': one['margin_blocked_by_conflict'],
        'truncated': False, 'complete': one['complete'],
        'conflict_policy': one['conflict_policy'],
        'source_errors': one['source_errors'], 'observed': one['observed'],
        'authority': one['authority'],
        'note': ('Foyda faqat narx va tannarx ikkalasi ham o‘qilganda hisoblanadi. '
                 'Aks holda — None va sabab nomi bilan. "conflict" sababi narx '
                 'manbalari kelishmaganini bildiradi: manba ustuvorligini e’lon '
                 'qilish tuzatadi, ombordagi raqam emas.')}

    ids = business_graph.identifiers(engine, tenant, agent, settings['entity'],
                                     step, MAX_ITEMS)[0]
    out = []
    errors = []
    not_computable = []
    blocked_by_conflict = []
    policy = ''
    for identifier in ids:
        view = _view(engine, tenant, agent, identifier, step)
        errors.extend(view.get('source_errors', []))
        policy = policy or view.get('conflict_policy', '')
        price_figure = _figure(view, price_attribute)
        cost_figure = _figure(view, cost_attribute)
        price_value = _selected_number(price_figure)
        cost_value = _selected_number(cost_figure)
        reasons = sorted({reason for reason in (_reason(price_figure),
                                                _reason(cost_figure)) if reason})
        # Same split as ``product()``: both policy reasons are kept out of
        # ``not_computable``, because in neither case is data missing.
        conflict_reasons = [reason for reason in reasons
                            if reason in POLICY_REASONS]
        data_reasons = [reason for reason in reasons
                        if reason not in POLICY_REASONS]
        not_computable.extend(data_reasons)
        blocked_by_conflict.extend(conflict_reasons)
        out.append({
            'id': identifier,
            'price': price_figure, 'cost': cost_figure,
            'margin': (price_value - cost_value) if not reasons else None,
            'margin_computable': not reasons,
            'not_computable': data_reasons,
            'margin_blocked_by_conflict': conflict_reasons,
        })
        if len(out) >= limit:
            break
    return {
        'entity': settings['entity'], 'view': 'margin', 'items': out,
        'item_count': len(out), 'scanned': len(ids),
        'not_computable': sorted(set(not_computable)),
        'margin_blocked_by_conflict': sorted(set(blocked_by_conflict)),
        'truncated': len(out) >= limit and len(ids) > len(out),
        'complete': not errors, 'conflict_policy': policy,
        'source_errors': errors,
        'observed': engine.clock(), 'authority': _authority(engine, tenant, agent),
        'note': ('Foyda faqat narx va tannarx ikkalasi ham o‘qilganda hisoblanadi. '
                 'Aks holda — None va sabab nomi bilan. "conflict" sababi narx '
                 'manbalari kelishmaganini bildiradi: manba ustuvorligini e’lon '
                 'qilish tuzatadi, ombordagi raqam emas.'),
    }


# --------------------------------------------------------------- tool handlers


def _product_tool(engine, tenant, agent, args, step):
    return product(engine, tenant, agent, args.get('product_id', ''), step)


def _stock_tool(engine, tenant, agent, args, step):
    return stock(engine, tenant, agent, step,
                 product_id=args.get('product_id', ''),
                 limit=args.get('limit', MAX_ITEMS))


def _price_tool(engine, tenant, agent, args, step):
    return price(engine, tenant, agent, step,
                 product_id=args.get('product_id', ''),
                 limit=args.get('limit', MAX_ITEMS))


def _margin_tool(engine, tenant, agent, args, step):
    return margin(engine, tenant, agent, step,
                  product_id=args.get('product_id', ''),
                  limit=args.get('limit', MAX_ITEMS))


def register_inventory_tools(registry):
    """Four read tools. There is no write path in this module at all.

    Read-only because a price and a stock level are inventory master data: an
    entity able to edit the price it is reporting on would be the author of the
    number a salesperson quotes, and the whole point of this block is that the
    seller quotes a number the *system* holds rather than one they carry.

    Also note what is NOT here: no tool that reserves stock, no tool that quotes,
    no tool that places an order, and no tool that computes a discount. Each of
    those is a commercial act on the customer's behalf, and a wrong one is
    discovered when the goods ship.
    """
    from .tools import Tool, obj, string
    limit = {'type': 'integer', 'minimum': 1, 'maximum': MAX_ITEMS}
    tools = [
        ('inventory.product',
         obj({'product_id': string(64)}, required=['product_id']), _product_tool),
        ('inventory.stock',
         obj({'product_id': string(64), 'limit': limit}, required=[]), _stock_tool),
        ('inventory.price',
         obj({'product_id': string(64), 'limit': limit}, required=[]), _price_tool),
        ('inventory.margin',
         obj({'product_id': string(64), 'limit': limit}, required=[]), _margin_tool),
    ]
    for name, schema, handler in tools:
        if name in registry.items:
            continue
        registry.add(Tool(name, 'read', schema, handler))
