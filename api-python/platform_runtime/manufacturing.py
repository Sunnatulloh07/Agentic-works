"""What a product is made of, and what the line actually produced (PRD v0.5, P12 / T4).

The asset model (P11) gave a factory a *place* for every machine, and the vision
feed (P11b) gave it an *event* for everything that happened there. Neither
answers the question a production manager actually asks on a Monday: **how much
did we make, from what, and how much of it was good.**

This module is that answer, and it is deliberately the *smallest* answer the
customer's own registers can support.

The line this module does not cross
-----------------------------------

**It reports what the registers say. It does not compute a manufacturing KPI that
needs data the customer has not declared.**

The PRD lists what Jidoka's cameras feed into an ERP: "OEE availability and
performance metrics per line and shift, SOP deviation events with timestamp and
station ID, cycle time per unit, quality alert flags". Only *some* of that can be
turned into a number from a register:

* **Cycle time per unit** — yes, if the standard time is declared. A measured
  cycle with no declared standard is reported as measured and *uncompared*, not
  silently judged fast or slow.
* **Yield** — yes, if both the output count and the defect count are readable.
  One without the other is reported as **not computable**, never as 100% or 0%.
* **OEE** — **no.** Availability and performance need *planned run time* and
  *ideal cycle time*, which are not in these registers. `vision.summary` already
  refuses this and says so ("bu P13 ishi"); this module keeps that refusal rather
  than inventing an availability figure from whatever happens to be present.
  P13 owns OEE.
* **SOP deviation and idle spans** — these are *events*, and P11b already reads
  them by class. This module counts a declared class; it does not re-derive one.

Every quantity ships with the inputs that produced it, for the same reason
`workforce` ships the names behind a count: a manager must be able to check the
arithmetic, not trust an index. A percentage with no visible denominator is a
number that will eventually be wrong and never questioned.

Boundaries that make this safe to read:

* Every read goes through the asset model, which goes through the Business Graph,
  which goes through the declared sources — so the register declaration, A1
  allowlist, agent tool permission and connection allowlist all apply unchanged.
  This module adds no new transport and no new authority.
* A register that cannot be read is **never** rendered as zero output. It is
  reported as unread, with the exception class, and the figures that depend on it
  are marked not computable.
* Quantities are parsed from declared columns. A cell that is not a number is
  counted as unreadable and reported, rather than coerced to zero — a blank cell
  and a confident zero are different facts.
* Nothing here evaluates a person. There is no per-operator, per-shift or
  per-team figure, because a yield number attributed to a person is a sanction
  waiting to happen, and the platform does not know why a batch failed.
* There is no write path in this file at all. A BOM is the customer's master
  data; an entity that could edit it would be the author of the recipe it is
  reporting on.
"""
from __future__ import annotations

import re

from . import assets
from .engine import Forbidden

MANUFACTURING_TOOLS = ('manufacturing.bom', 'manufacturing.cycle',
                       'manufacturing.yield')

# Mirrors the register ceiling the whole family respects: a read can never scan
# more than the source tool will return.
MAX_ROWS = 200
MAX_BOM_LINES = 200
MAX_NAMES = 50

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')

# A number a spreadsheet can hold. Parsed, never guessed: an ambiguous cell is
# reported unreadable rather than treated as zero.
NUMBER_RE = re.compile(r'^-?[0-9]{1,15}(?:[.,][0-9]{1,9})?$')
# ``[0-9]`` and not ``\d``: Python's ``re`` is Unicode-aware, so ``\d`` also
# matches Devanagari, Arabic-Indic and fullwidth digits, which ``float()`` then
# normalises. A cell written that way is reported unreadable rather than read as
# a plausible wrong number. See ``platform_runtime.cells``.


class ManufacturingError(RuntimeError):
    pass


def _bounded(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def _ladder(engine, tenant, agent):
    return str(engine.policy(tenant, agent).get('ladder', ''))


def _authority(engine, tenant, agent):
    """The ladder the figures were read under, carried into the output.

    A production figure that does not say which authority produced it cannot be
    audited later; every read in this family ships this block for that reason.
    """
    return {'agent': agent, 'ladder': _ladder(engine, tenant, agent)}


def _text(value, maximum=128):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)[:maximum]


def _number(value):
    """Parse a declared numeric cell, or ``None`` when it is not a number.

    ``None`` and ``0`` are different facts. A blank cell that becomes zero would
    understate output and inflate a yield; an unreadable cell that becomes zero
    would do the same silently. Both are reported through the caller's unreadable
    counter instead.

    A **non-finite** value is not a number either. ``nan`` and ``inf`` are real
    Python floats and would otherwise sail through as "readable": ``inf >= 0`` is
    true, so an infinite output count would accumulate into a station total. The
    same hole was closed in P13's ``oee._number``; the two are the only places a
    cell is interpreted, so both refuse a cell that is not a finite number. An
    **integer past the float range** is refused by the same rule: ``float()`` raises
    ``OverflowError`` there rather than returning infinity, so the conversion is
    guarded and an unrepresentable cell is counted as unreadable rather than fatal.
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


# ------------------------------------------------------------------ config


def manufacturing_config(tenant) -> dict:
    """Validate the tenant's declared manufacturing mapping, or an empty one.

    Configuration errors raise instead of being skipped: a typo in a column name
    must not silently produce zero output, because "the line made nothing" and "we
    read the wrong column" would look identical to a manager.
    """
    from .tools import config
    raw = config(tenant).get('manufacturing')
    if raw is None:
        return {'bom': {}, 'cycle': {}, 'yield': {}}
    if not isinstance(raw, dict):
        raise ValueError('manufacturing must be an object')
    unknown = set(raw) - {'bom', 'cycle', 'yield'}
    if unknown:
        raise ValueError(f'manufacturing has unsupported keys: {sorted(unknown)}')
    return {
        'bom': _block(raw.get('bom'), 'bom', _bom_entry),
        'cycle': _block(raw.get('cycle'), 'cycle', _cycle_entry),
        'yield': _block(raw.get('yield'), 'yield', _yield_entry),
    }


def _block(value, name, validate):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f'manufacturing.{name} must be an object')
    return {key: validate(key, entry) for key, entry in value.items()}


def _require_register(name, entry, kind):
    unknown = set(entry) - _KEYS[kind]
    if unknown:
        raise ValueError(f'manufacturing.{kind} {name} has unsupported keys: '
                         f'{sorted(unknown)}')
    out = {}
    for key in ('register', 'range'):
        value = entry.get(key)
        if not isinstance(value, str) or not NAME_RE.match(value):
            raise ValueError(f'manufacturing.{kind} {name} requires a valid {key}')
        out[key] = value
    for key in _COLUMNS[kind]:
        value = entry.get(key, '')
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError(f'manufacturing.{kind} {name} {key} must be a string')
        out[key] = value
    return out


_KEYS = {
    'bom': {'register', 'range', 'parent_column', 'component_column', 'quantity_column',
            'unit_column', 'scrap_column'},
    'cycle': {'register', 'range', 'station_column', 'duration_column',
              'standard_column', 'unit_column'},
    'yield': {'register', 'range', 'station_column', 'output_column',
              'defect_column', 'unit_column'},
}
_COLUMNS = {
    'bom': ('parent_column', 'component_column', 'quantity_column', 'unit_column',
            'scrap_column'),
    'cycle': ('station_column', 'duration_column', 'standard_column', 'unit_column'),
    'yield': ('station_column', 'output_column', 'defect_column', 'unit_column'),
}


def _bom_entry(name, entry):
    if not isinstance(entry, dict):
        raise ValueError(f'manufacturing.bom {name} must be an object')
    out = _require_register(name, entry, 'bom')
    if not out['parent_column'] or not out['component_column']:
        raise ValueError(f'manufacturing.bom {name} requires parent_column and '
                         f'component_column')
    return out


def _cycle_entry(name, entry):
    if not isinstance(entry, dict):
        raise ValueError(f'manufacturing.cycle {name} must be an object')
    out = _require_register(name, entry, 'cycle')
    if not out['station_column'] or not out['duration_column']:
        raise ValueError(f'manufacturing.cycle {name} requires station_column and '
                         f'duration_column')
    return out


def _yield_entry(name, entry):
    if not isinstance(entry, dict):
        raise ValueError(f'manufacturing.yield {name} must be an object')
    out = _require_register(name, entry, 'yield')
    if not out['station_column'] or not out['output_column']:
        raise ValueError(f'manufacturing.yield {name} requires station_column and '
                         f'output_column')
    return out


def _resolve(tenant, kind):
    """Pick the declared register for a view, or refuse by name."""
    declared = manufacturing_config(tenant)[kind]
    if not declared:
        raise Forbidden(f'No manufacturing {kind} register is declared for this tenant')
    if len(declared) == 1:
        return next(iter(declared.values()))
    raise Forbidden(f'Manufacturing {kind} has more than one register declared; '
                    f'a view is ambiguous')


def _read_rows(engine, tenant, agent, entry, kind):
    """Read one declared register through the ordinary sheets tool handler.

    Going through the registry means the register declaration, A1 allowlist, agent
    tool permission and connection allowlist all apply exactly as for a manual
    call. A provider failure surfaces as the handler's own error class.
    """
    tool = engine.registry.get('sheets.rows')
    args = {'register': entry['register'], 'range': entry['range']}
    tool.validate(args)
    result = tool.handler(engine, tenant, agent, args, 'manufacturing:' + kind)
    rows = result.get('rows')
    if not isinstance(rows, list):
        raise ManufacturingError('Manufacturing register returned an unexpected shape')
    return rows[:MAX_ROWS], result


def _bind(tenant, station):
    """Place a station cell in the asset hierarchy as a path, or ``None``.

    Matching is by segment, never by string prefix — the P11 rule, reused rather
    than re-implemented here, so ``zavod-1`` can never claim ``zavod-10``'s output.
    """
    try:
        segments = assets.parse_path(tenant, station)
    except (ValueError, Forbidden):
        return None
    return assets.format_path(segments)


def _declared_levels(tenant):
    levels = assets._declared_levels(tenant)
    if not levels:
        raise Forbidden('No asset hierarchy is declared for this tenant')
    return levels


# -------------------------------------------------------------------- BOM


def bom(engine, tenant, agent, step, *, product=''):
    """The declared product structure: what a product is assembled from.

    A BOM is master data, so this is a **read of the customer's register and
    nothing more**. It does not roll costs up, because a cost needs prices this
    module has no declared source for, and a rolled-up figure with an invisible
    basis is worse than none. It does not explode multi-level structures into
    totals, because that would hide which level a component came from.

    ``product`` narrows to one parent. Every line names its component, quantity,
    unit and declared scrap, so the shape of the product is inspectable rather
    than summarised.
    """
    _declared_levels(tenant)
    entry = _resolve(tenant, 'bom')
    rows, raw = _read_rows(engine, tenant, agent, entry, 'bom')
    product = _text(product, 120).strip()
    lines, skipped, unreadable_qty, unbound = [], 0, 0, 0
    # Set only when a real line is actually dropped. Testing ``len(lines) >=
    # MAX_BOM_LINES`` after the loop is wrong at exactly the ceiling: a BOM with
    # precisely 200 lines has dropped nothing, and reporting ``truncated: True``
    # would tell a manager to fetch the rest of a list that is already complete.
    truncated = False
    parents = []
    seen_parents = set()
    for row in rows:
        parent = _text(row.get(entry['parent_column']), 120).strip()
        component = _text(row.get(entry['component_column']), 120).strip()
        if not parent or not component:
            # Without both ends there is no edge to report.
            skipped += 1
            continue
        if parent not in seen_parents:
            seen_parents.add(parent)
            if len(parents) < MAX_NAMES:
                record = {'product': parent}
                # A product is often built at a station and so is also an asset, but
                # a BOM may name a product that is never a node in the hierarchy.
                # ``path`` is included only when it really binds, so an empty string
                # can never be read as "this product has no place".
                bound = _bind(tenant, parent)
                if bound:
                    record['path'] = bound
                parents.append(record)
        if product and parent != product:
            continue
        quantity = _number(row.get(entry['quantity_column'])) if entry['quantity_column'] else None
        if entry['quantity_column'] and quantity is None:
            unreadable_qty += 1
        if len(lines) >= MAX_BOM_LINES:
            truncated = True
            continue
        line = {'product': parent, 'component': component}
        if entry['quantity_column']:
            line['quantity'] = quantity
        if entry['unit_column']:
            line['unit'] = _text(row.get(entry['unit_column']), 32).strip()
        if entry['scrap_column']:
            scrap = _number(row.get(entry['scrap_column']))
            line['scrap'] = scrap
            if scrap is None and _text(row.get(entry['scrap_column']), 32).strip():
                unreadable_qty += 1
        lines.append(line)
    return {
        'view': 'bom',
        'register': entry['register'],
        'product': product,
        'products': parents,
        'product_count': len(seen_parents),
        'lines': lines,
        'line_count': len(lines),
        'unreadable_quantity': unreadable_qty,
        'skipped': skipped,
        'scanned': len(rows),
        'truncated': truncated or raw.get('truncated', False),
        'complete': True,
        'source_errors': [],
        'authority': _authority(engine, tenant, agent),
        'note': ('Bu mahsulot tarkibi — mijozning master ma’lumoti. Platforma uni '
                 'Tahrirlamaydi va tannarxni hisoblamaydi: narx manbasi '
                 'deklaratsiya qilinmagan.'),
    }


# ------------------------------------------------------------------ cycle


def cycle(engine, tenant, agent, step, *, station='', limit=MAX_NAMES):
    """Measured cycle time per station, compared to a declared standard.

    The comparison is the whole point and also the whole danger. A measured cycle
    with **no declared standard** is reported as measured and marked
    ``compared: false`` rather than judged: calling it fast or slow would be the
    platform inventing the standard, and the PRD's own warning about vendor
    accuracy applies with equal force to an invented baseline.

    Averages are reported per station as a mean of the readable durations, always
    with the sample count and the unreadable count beside them, so a mean over two
    units can never be mistaken for a mean over two hundred.
    """
    limit = _bounded(limit, 'limit', 1, MAX_NAMES)
    _declared_levels(tenant)
    entry = _resolve(tenant, 'cycle')
    rows, raw = _read_rows(engine, tenant, agent, entry, 'cycle')
    wanted = _text(station, 255).strip()
    by_station, unreadable, skipped = {}, 0, 0
    compared_any = False
    for row in rows:
        name = _text(row.get(entry['station_column']), 255).strip()
        if not name:
            skipped += 1
            continue
        if wanted and name != wanted:
            continue
        duration = _number(row.get(entry['duration_column']))
        if duration is None:
            unreadable += 1
            continue
        if duration < 0:
            unreadable += 1
            continue
        bucket = by_station.setdefault(name, {'station': name, 'samples': 0, 'total': 0.0,
                                              'standard': None, 'declared': False})
        bucket['samples'] += 1
        bucket['total'] += duration
        if entry['standard_column']:
            standard = _number(row.get(entry['standard_column']))
            if standard is not None and standard >= 0:
                bucket['standard'] = standard
                bucket['declared'] = True
    out = []
    for bucket in sorted(by_station.values(), key=lambda item: (-item['samples'], item['station'])):
        seconds = round(bucket['total'] / bucket['samples'], 3)
        record = {'station': bucket['station'],
                  'samples': bucket['samples'],
                  'mean_duration': seconds,
                  'unit': entry['unit_column'] and _text(
                      next((r.get(entry['unit_column']) for r in rows
                            if _text(r.get(entry['station_column']), 255).strip()
                            == bucket['station']), ''), 32).strip() or '',
                  'standard': bucket['standard'],
                  'compared': bucket['declared']}
        # A station that cannot be placed in the asset tree is reported without a
        # path rather than attached to a guessed node; the ``unbound`` count below
        # makes that visible instead of silent.
        bound = _bind(tenant, bucket['station'])
        if bound:
            record['path'] = bound
        if bucket['declared']:
            # Reported as a signed delta AND a ratio, never as a verdict. Whether a
            # slow cycle is a problem depends on the shift plan, which is not here.
            record['delta_seconds'] = round(seconds - bucket['standard'], 3)
            if bucket['standard'] > 0:
                record['ratio'] = round(seconds / bucket['standard'], 3)
            compared_any = True
        out.append(record)
        if len(out) >= limit:
            break
    # A station with no ``path`` key is exactly one that did not bind; ``path`` is
    # only ever added when it does, so a missing key is the signal, not a defect.
    unbound = sum(1 for record in out if not record.get('path'))
    return {
        'view': 'cycle',
        'register': entry['register'],
        'station': wanted,
        'stations': out,
        'station_count': len(by_station),
        'compared': compared_any,
        'unbound': unbound,
        'unreadable_duration': unreadable,
        'skipped': skipped,
        'scanned': len(rows),
        'truncated': len(by_station) > limit or raw.get('truncated', False),
        'complete': True,
        'source_errors': [],
        'authority': _authority(engine, tenant, agent),
        'note': ('O‘lchangan sikl vaqti. Standart deklaratsiya qilinmagan bo‘lsa, '
                 'taqqoslanmaydi: platforma standartni o‘zi o‘ylab topmaydi. '
                 'Bu OEE emas — OEE uchun rejalashtirilgan ish vaqti kerak (P13).'),
    }


# ----------------------------------------------------------- yield report


def _yield_figure(output, defect):
    """Yield from two counts, or ``None`` when it cannot honestly be computed.

    Returns ``(yield_ratio, good)``. ``None`` happens when no output was read at
    all: division by zero has no meaning here, and reporting 0% or 100% would be a
    fabricated number. Defects exceeding output is not clamped to zero either — it
    is a data problem the manager must see, so it is reported through the caller.
    """
    if output <= 0:
        return None, None
    good = output - defect
    return round(good / output, 4), good


def yield_report(engine, tenant, agent, step, *, station='', limit=MAX_NAMES):
    """Output, defects and yield per station, from the registers that hold them.

    Three refusals, each enforced rather than described:

    * **Yield is not computed from one number.** If output is readable but defects
      are not (no column declared, or every cell blank), the station is reported
      with ``yield: None`` and ``yield_computable: false``. Assuming zero defects
      would print 100% for a line that might be rejecting half its work.
    * **A defect count above output is reported, not clamped.** It is a data error,
      and quietly turning it into 0% yield would hide it.
    * **No per-person figure exists.** There is no operator column in this read,
      deliberately: a yield attributed to a person is a sanction waiting for a
      reason the platform does not have.
    """
    limit = _bounded(limit, 'limit', 1, MAX_NAMES)
    _declared_levels(tenant)
    entry = _resolve(tenant, 'yield')
    rows, raw = _read_rows(engine, tenant, agent, entry, 'yield')
    wanted = _text(station, 255).strip()
    by_station, unreadable_out, unreadable_defect, skipped, impossible = {}, 0, 0, 0, 0
    has_defect_column = bool(entry['defect_column'])
    for row in rows:
        name = _text(row.get(entry['station_column']), 255).strip()
        if not name:
            skipped += 1
            continue
        if wanted and name != wanted:
            continue
        output_raw = row.get(entry['output_column'])
        output = _number(output_raw)
        if output is None:
            # A cell holding text that is not a number is *unreadable*; a blank
            # cell is *not recorded*. They are counted separately because they
            # mean different things to a manager, but neither is a zero, and a
            # row with no readable output has no output to attribute. Creating a
            # record here would sum the row into ``0.0`` and report a fabricated
            # "the line made nothing".
            if _text(output_raw, 32).strip():
                unreadable_out += 1
            continue
        record = by_station.setdefault(name, {'station': name, 'output': 0.0,
                                              'defect': 0.0, 'defect_read': False})
        record['output'] += output
        if has_defect_column:
            defect_raw = row.get(entry['defect_column'])
            defect = _number(defect_raw)
            if defect is None:
                # An unreadable defect cell does not discard the output we just
                # read. It makes the *yield* uncomputable for this station, which
                # is the honest answer: we cannot say how much was good if we
                # cannot read how much was bad.
                if _text(defect_raw, 32).strip():
                    unreadable_defect += 1
                    record['defect_unreadable'] = True
            else:
                record['defect'] += defect
                record['defect_read'] = True
    out = []
    for record in sorted(by_station.values(), key=lambda item: (-item['output'], item['station'])):
        # A station is computable only when a defect count was actually read *and*
        # no defect cell in it was unreadable. Summing readable cells while
        # ignoring an unreadable one would understate defects and inflate yield.
        computable = has_defect_column and record['defect_read'] \
            and not record.get('defect_unreadable')
        ratio, good = _yield_figure(record['output'], record['defect']) if computable else (None, None)
        if computable and record['defect'] > record['output']:
            impossible += 1
            ratio, good = None, None
        station_record = {'station': record['station'],
                          'output': record['output'],
                          'defect': record['defect'] if computable else None,
                          'good': good,
                          'yield': ratio,
                          'yield_computable': bool(computable and ratio is not None)}
        # Same rule as ``cycle``: a station that binds to no asset node is
        # reported without a path, and the ``unbound`` count below names it,
        # rather than attaching it to a guessed node or an ambiguous ''.
        bound = _bind(tenant, record['station'])
        if bound:
            station_record['path'] = bound
        if entry['unit_column']:
            station_record['unit'] = _text(
                next((r.get(entry['unit_column']) for r in rows
                      if _text(r.get(entry['station_column']), 255).strip()
                      == record['station']), ''), 32).strip()
        out.append(station_record)
        if len(out) >= limit:
            break
    unbound = sum(1 for record in out if not record.get('path'))
    return {
        'view': 'yield',
        'register': entry['register'],
        'station': wanted,
        'stations': out,
        'station_count': len(by_station),
        'defect_column_declared': has_defect_column,
        'unbound': unbound,
        'unreadable_output': unreadable_out,
        'unreadable_defect': unreadable_defect,
        'defect_exceeds_output': impossible,
        'skipped': skipped,
        'scanned': len(rows),
        'truncated': len(by_station) > limit or raw.get('truncated', False),
        'complete': True,
        'source_errors': [],
        'authority': _authority(engine, tenant, agent),
        'note': ('Chiqim faqat ikkala son o‘qilganda hisoblanadi. Nuqson ustuni '
                 'yo‘q yoki bo‘sh bo‘lsa — chiqim None, 100% emas. Xodim bo‘yicha '
                 'ko‘rsatkich yo‘q.'),
    }


# --------------------------------------------------------------- tool handlers


def _bom_tool(engine, tenant, agent, args, step):
    return bom(engine, tenant, agent, step, product=args.get('product', ''))


def _cycle_tool(engine, tenant, agent, args, step):
    return cycle(engine, tenant, agent, step, station=args.get('station', ''),
                 limit=args.get('limit', MAX_NAMES))


def _yield_tool(engine, tenant, agent, args, step):
    return yield_report(engine, tenant, agent, step, station=args.get('station', ''),
                        limit=args.get('limit', MAX_NAMES))


def register_manufacturing_tools(registry):
    """Three read tools. No write path exists here at all.

    Read-only because a BOM is master data: an entity able to edit what a product
    is made of would be the author of the recipe it is reporting on, and every
    yield and cycle figure would then be computed against a model it could change.
    The customer owns their BOM; the platform reads it.
    """
    from .tools import Tool, obj, string
    limit = {'type': 'integer', 'minimum': 1, 'maximum': MAX_NAMES}
    tools = [
        ('manufacturing.bom',
         obj({'product': string(120)}, required=[]), _bom_tool),
        ('manufacturing.cycle',
         obj({'station': string(255), 'limit': limit}, required=[]), _cycle_tool),
        ('manufacturing.yield',
         obj({'station': string(255), 'limit': limit}, required=[]), _yield_tool),
    ]
    for name, schema, handler in tools:
        if name in registry.items:
            continue
        registry.add(Tool(name, 'read', schema, handler))
