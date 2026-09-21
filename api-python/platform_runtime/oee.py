"""OEE, downtime and SOP deviation — and the line where each one stops (PRD v0.5, P13 / T4).

P12 refused OEE by name and handed it here: *"Availability and performance need
planned run time and ideal cycle time, which are not in these registers. P13 owns
OEE."* This module is that handover honoured. It is the last block of T4.

What OEE actually requires
--------------------------

OEE is a product of three ratios, and each one needs inputs that a register
either has or does not:

    availability = run_time / planned_run_time
    performance  = (ideal_cycle_time * units_produced) / run_time
    quality      = good_units / units_produced
    OEE          = availability * performance * quality

This is why P12 could not compute it. ``units_produced`` and ``good_units`` it
can read (that is its yield). ``planned_run_time`` and ``ideal_cycle_time`` are
**not** measurements at all — they are the *standard* against which measurement
is judged, and a standard is a declaration the customer makes, not a number the
platform can derive from observations. A platform that infers its own denominator
is grading the factory against a benchmark it invented.

So the rule here is not "compute OEE". It is:

> **Each of the three factors is computed only from declared inputs, and each is
> refused by name when its inputs are absent — never defaulted, never assumed.**

That gives four honest outcomes rather than one fragile number:

* all three factors computable → ``oee`` is a number, with all three factors and
  all six inputs beside it;
* some factor not computable → ``oee: None``, the computable factors still
  reported, and ``not_computable`` naming exactly which inputs are absent or
  unusable;
* **availability** needs the plan and refuses without it; **performance** does
  *not*, because it is ``ideal_cycle * produced / run`` — three measured values.
  So a customer who declares an ideal cycle time but no shift plan gets a real
  performance and quality figure and a refused OEE, which is strictly more
  information than refusing the whole read;
* nothing declared → the module still reads, and names every missing input.

Planned run time: column and constant, and which one wins
---------------------------------------------------------

A station's planned run time may be declared **either** as a column
(``planned_run_column``) **or** as a constant (``planned_run_seconds``) — or as
both, in which case the rule is measured and stated here rather than left to
chance:

* **the column wins.** A readable, positive cell is the plan for that row. It is
  more specific than a station-wide constant, and a factory that writes its shift
  plan per row expects the row to be believed.
* **the constant is the fallback for a cell that is blank**, not a competing
  source. This is what a default is for: a row the customer simply did not fill
  in still gets an availability figure rather than a refusal.
* **a non-blank, unreadable cell is neither.** It is counted in ``unreadable``
  and sets no plan, because the customer wrote something there and the platform
  will not quietly replace it with the constant.

So declaring both is not an ambiguity to be refused — it is the ordinary
"per-row value with a station default" shape, and it resolves deterministically.
The refusal is reserved for what is genuinely ambiguous: *two registers* declared
for one read (see ``_resolve_register``).

A partial OEE is not reported as an OEE with blanks. An index whose factors are
half-invented is exactly the "number that will eventually be wrong and never
questioned" P12's header warns about, and it is worse than no index at all
because it looks complete.

The three things this module will not do
----------------------------------------

* **It does not choose the shift calendar.** Planned run time is declared per
  station as a duration in seconds, or read from a declared column. Deriving it
  from calendar arithmetic would mean the platform deciding how many shifts a
  factory runs, which the customer has not told it.
* **It does not treat idle time as unplanned downtime.** An idle span is an
  observation (P11b reads it as an event class); whether it was planned (a lunch
  break, a scheduled changeover) or unplanned (a breakdown) is a *declaration*
  the customer makes by class. The module counts the class the operator names as
  unplanned; it does not classify one itself.
* **It does not evaluate a person.** Same rule as ``workforce`` and P12: no
  per-operator, per-shift or per-team figure, and no config key could create one.
  An OEE attributed to a machine is a maintenance signal; the same number
  attributed to the operator on that shift is a sanction.

Andon — the alarm half
----------------------

"Poka-yoke" in the PRD's terminology is a *andon* cord: a threshold breach that
reaches a manager without being asked for. This module provides the **detection**
half and reuses the P6 escalation coordinator for the **delivery** half, rather
than growing a second delivery mechanism:

* ``oee.andon`` reads the declared thresholds and returns which stations breached
  them, with the measured value and the threshold that was breached — the fact,
  not the verdict. Alerting is a coordinator action, not a tool, so a model can
  neither manufacture an alarm nor suppress one.
* A threshold is operator configuration. The platform does not decide that 85% is
  a bad OEE; the customer decides what to be told about.

Boundaries that make this safe to read:

* Every read goes through ``sheets.rows``, so the register declaration, A1
  allowlist, agent tool permission and connection allowlist all apply unchanged.
  This module adds no transport and no new authority.
* A register that cannot be read is **never** rendered as zero output or zero
  downtime. The exception surfaces; a station is never silently reported as
  producing nothing.
* A blank cell and a confident zero are different facts, and are counted through
  separate unreadable counters. Neither becomes zero.
* Nothing here writes. There is no write path in this file at all.
"""
from __future__ import annotations

import re

from . import assets
from .engine import Forbidden

OEE_TOOLS = ('oee.report', 'oee.andon')

# Mirrors the register ceiling the whole family respects.
MAX_ROWS = 200
MAX_STATIONS = 50
MAX_THRESHOLDS = 16
# No MAX_NAMES: this module returns stations and factors, never a list of people.
# `MAX_STATIONS` is the bound that is actually enforced; a second, unused
# constant would advertise a limit nobody applies. Removed during the audit that
# found the same "declared then read by nobody" shape in `source_priority`.

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')
NUMBER_RE = re.compile(r'^-?[0-9]{1,15}(?:[.,][0-9]{1,9})?$')
# ``[0-9]`` and not ``\d``: Python's ``re`` is Unicode-aware, so ``\d`` also
# matches Devanagari, Arabic-Indic and fullwidth digits, which ``float()`` then
# normalises. A cell written that way is reported unreadable rather than read as
# a plausible wrong number. See ``platform_runtime.cells``.

# The three factors, in the order the formula multiplies them. Named here so a
# refusal can say exactly which factor it could not compute.
FACTORS = ('availability', 'performance', 'quality')

# The declared inputs each factor needs, stated once so ``not_computable`` is
# derived from this table rather than from three separate hand-written lists that
# could drift apart.
FACTOR_INPUTS = {
    'availability': ('planned_run_seconds', 'run_seconds'),
    'performance': ('ideal_cycle_seconds', 'run_seconds', 'produced'),
    'quality': ('produced', 'good'),
}

# A threshold key must be one of these. ``oee`` is included because the customer
# may care about the product; the factors are included because a manager often
# wants to be told about a specific one (availability falling is a maintenance
# signal even when OEE is fine).
THRESHOLD_KEYS = ('oee', 'availability', 'performance', 'quality')

# Percentage comparisons in this module are done on a 0..1 ratio, but thresholds
# are declared the way an operator writes them — as a percentage. A single bound
# for both would let ``90`` mean 9000% or ``0.9`` mean 0.9%, so the two are kept
# apart and the percentage is converted once, explicitly.
PERCENT = 100.0


class OeeError(RuntimeError):
    pass


def _bounded(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def _bounded_number(value, name, low, high):
    """A finite declared number in range. ``bool`` is not a number here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a number {low}..{high}')
    number = float(value)
    if number != number or number in (float('inf'), float('-inf')):
        raise ValueError(f'{name} must be a finite number')
    if not low <= number <= high:
        raise ValueError(f'{name} must be a number {low}..{high}')
    return number


def _text(value, maximum=128):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)[:maximum]


def _number(value):
    """Parse a declared numeric cell, or ``None`` when it is not a number.

    ``None`` and ``0`` are different facts, exactly as in P12: a blank cell that
    became zero would understate planned time or overstate availability, and an
    unreadable cell that became zero would do the same silently.

    A **non-finite** value is not a number either. ``nan`` and ``inf`` are real
    Python floats and would otherwise sail through as "readable": ``inf >= 0`` is
    true, so an infinite run time would accumulate into the bucket and produce an
    infinite availability that no guard downstream catches. Refusing them here is
    the single point where every cell in the module is interpreted, so a cell that
    is not a finite number becomes *unreadable* — counted, never propagated. The
    same rule covers an **integer past the float range**: ``float()`` raises
    ``OverflowError`` there rather than returning infinity, so the conversion is
    guarded and an unrepresentable cell is counted like any other unreadable one.
    """
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # `float()` raises rather than returning infinity when the integer is past
        # the float range. An unrepresentable cell is unreadable, not fatal.
        try:
            text = float(value)
        except OverflowError:
            return None
        return text if -float('inf') < text < float('inf') else None
    text = str(value).strip()
    if not NUMBER_RE.match(text):
        return None
    return float(text.replace(',', '.'))


def _ladder(engine, tenant, agent):
    return str(engine.policy(tenant, agent).get('ladder', ''))


def _authority(engine, tenant, agent):
    return {'agent': agent, 'ladder': _ladder(engine, tenant, agent)}


# ------------------------------------------------------------------ config


def oee_config(tenant) -> dict:
    """Validate the tenant's declared OEE mapping, or an empty one.

    Configuration errors raise instead of being skipped. A typo in a column name
    must not silently produce an uncomputable factor, because "the customer has
    not declared a plan" and "we read the wrong column" would look identical — and
    the second is a defect while the first is a fact.
    """
    from .tools import config
    raw = config(tenant).get('oee')
    if raw is None:
        return {'registers': {}, 'thresholds': {}}
    if not isinstance(raw, dict):
        raise ValueError('oee must be an object')
    unknown = set(raw) - {'registers', 'thresholds'}
    if unknown:
        raise ValueError(f'oee has unsupported keys: {sorted(unknown)}')

    registers = raw.get('registers')
    if registers is None:
        clean_registers = {}
    elif not isinstance(registers, dict):
        raise ValueError('oee.registers must be an object')
    else:
        clean_registers = {name: _register_entry(name, entry)
                           for name, entry in registers.items()}

    thresholds = raw.get('thresholds')
    if thresholds is None:
        clean_thresholds = {}
    elif not isinstance(thresholds, dict):
        raise ValueError('oee.thresholds must be an object')
    else:
        clean_thresholds = {key: _threshold_entry(key, entry)
                            for key, entry in thresholds.items()}
    return {'registers': clean_registers, 'thresholds': clean_thresholds}


_REGISTER_KEYS = {
    'register', 'range', 'station_column',
    'planned_run_column', 'ideal_cycle_column',
    'run_column', 'produced_column', 'good_column',
    'unplanned_column', 'planned_run_seconds',
}
# Only these may carry a column name; ``register`` and ``range`` are validated as
# names instead. Splitting them makes it impossible for a column key to be silent
# while ``register`` is required.
_REGISTER_COLUMNS = ('station_column', 'planned_run_column', 'ideal_cycle_column',
                     'run_column', 'produced_column', 'good_column',
                     'unplanned_column')


def _register_entry(name, entry):
    if not isinstance(entry, dict):
        raise ValueError(f'oee.registers {name} must be an object')
    unknown = set(entry) - _REGISTER_KEYS
    if unknown:
        raise ValueError(f'oee.registers {name} has unsupported keys: {sorted(unknown)}')
    out = {}
    for key in ('register', 'range'):
        value = entry.get(key)
        if not isinstance(value, str) or not NAME_RE.match(value):
            raise ValueError(f'oee.registers {name} requires a valid {key}')
        out[key] = value
    for key in _REGISTER_COLUMNS:
        value = entry.get(key, '')
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError(f'oee.registers {name} {key} must be a string')
        out[key] = value
    if not out['station_column']:
        raise ValueError(f'oee.registers {name} requires station_column')

    # A plan may be declared per row as a column, per station as a constant, or
    # both. The column wins when it holds a readable positive value; the constant
    # is the fallback for a blank cell. See the header: this is deterministic, and
    # is NOT the ambiguity it might look like -- the refusal is for two registers.
    plan = entry.get('planned_run_seconds')
    if plan is not None:
        out['planned_run_seconds'] = _bounded_number(
            plan, f'oee.registers {name} planned_run_seconds', 1, 31_536_000)
    return out


def _threshold_entry(key, entry):
    if key not in THRESHOLD_KEYS:
        raise ValueError(f'oee.thresholds has an unsupported key {key!r}; '
                         f'expected one of {sorted(THRESHOLD_KEYS)}')
    if not isinstance(entry, dict):
        raise ValueError(f'oee.thresholds {key} must be an object')
    unknown = set(entry) - {'below', 'above'}
    if unknown:
        raise ValueError(f'oee.thresholds {key} has unsupported keys: {sorted(unknown)}')
    if not entry:
        raise ValueError(f'oee.thresholds {key} declares no bound')
    out = {}
    for bound in ('below', 'above'):
        if bound in entry:
            # Percentages as the operator writes them, 0..100.
            out[bound] = _bounded_number(entry[bound], f'oee.thresholds {key}.{bound}',
                                         0, 100)
    if 'below' in out and 'above' in out and out['below'] >= out['above']:
        raise ValueError(f'oee.thresholds {key} has below >= above, which can never '
                         f'be satisfied')
    return out


def _resolve_register(tenant):
    """Pick the declared register, or refuse by name."""
    declared = oee_config(tenant)['registers']
    if not declared:
        raise Forbidden('No OEE register is declared for this tenant')
    if len(declared) == 1:
        return next(iter(declared.values()))
    raise Forbidden('OEE has more than one register declared; a read is ambiguous')


def _read_rows(engine, tenant, agent, entry):
    """Read the declared register through the ordinary sheets tool handler.

    Going through the registry means the register declaration, A1 allowlist, agent
    tool permission and connection allowlist all apply exactly as for a manual
    call. This module adds no data path of its own.
    """
    tool = engine.registry.get('sheets.rows')
    args = {'register': entry['register'], 'range': entry['range']}
    tool.validate(args)
    result = tool.handler(engine, tenant, agent, args, 'oee')
    rows = result.get('rows')
    if not isinstance(rows, list):
        raise OeeError('OEE register returned an unexpected shape')
    return rows[:MAX_ROWS], result


def _bind(tenant, station):
    """Place a station cell in the asset hierarchy as a path, or ``None``.

    Matching is by segment, never by string prefix — the P11 rule, reused rather
    than re-implemented, so ``zavod-1`` can never claim ``zavod-10``'s figures.
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


# -------------------------------------------------------------------- OEE


def _factor_availability(planned, run):
    """``run / planned``, or ``None`` when either input is missing.

    A run longer than the plan is not clamped: it is a declaration error (the plan
    says 8 hours, the register says 9 were worked) and clamping it to 100% would
    hide a planning defect behind a perfect number.
    """
    if planned is None or run is None or planned <= 0:
        return None
    return run / planned


def _factor_performance(ideal, run, produced):
    """``(ideal * produced) / run``, or ``None`` when an input is missing."""
    if ideal is None or run is None or produced is None or run <= 0:
        return None
    return (ideal * produced) / run


def _factor_quality(produced, good):
    """``good / produced``, or ``None`` when it cannot honestly be computed.

    Two refusals, each of which would otherwise produce a confident nonsense
    number:

    * ``produced`` of zero has no ratio — division by zero has no meaning, and
      both 0% and 100% would be fabrications;
    * ``good > produced`` is a **data error**, not a yield above 100%. Reporting
      it would give an OEE of 347% and a manager would rightly stop trusting
      every other figure in the payload. It is surfaced through the caller's
      ``good_exceeds_produced`` counter instead, exactly as P12 reports a defect
      count above output rather than clamping it.
    """
    if produced is None or good is None or produced <= 0:
        return None
    if good > produced:
        return None
    return good / produced


def _missing_inputs(factors, inputs):
    """Why each uncomputable factor could not be computed, named by input.

    Two different reasons a factor can fail, and conflating them would mislead the
    operator about what to fix:

    * the input is **absent** — not declared, blank, or unreadable. The fix is to
      declare it, and it is named here.
    * the input is **present but unusable** — a produced count of zero divides by
      zero, a good count above produced is a data error. The value is there; the
      *data* is the problem, so naming the input as "missing" would send the
      operator to look for a column that already exists.

    Both are reported, in separate lists, because the two fixes are different
    actions.
    """
    absent, unusable = set(), set()
    for factor in FACTORS:
        if factors.get(factor) is not None:
            continue
        for name in FACTOR_INPUTS[factor]:
            value = inputs.get(name)
            if value is None:
                absent.add(name)
                continue
            if name in ('planned_run_seconds', 'run_seconds',
                        'ideal_cycle_seconds') and value <= 0:
                unusable.add(name)
            elif name == 'produced' and value <= 0:
                unusable.add(name)
            elif name == 'good' and inputs.get('produced') is not None \
                    and value > inputs['produced']:
                unusable.add(name)
    return {'absent': sorted(absent), 'unusable': sorted(unusable)}


def report(engine, tenant, agent, step, *, station='', limit=MAX_STATIONS):
    """OEE per station, from values the customer declares and the register holds.

    The formula is ``availability * performance * quality`` and it is only
    reported when all three factors are computable. A partial product is not an
    OEE: an index assembled from one measured factor and two defaults is a number
    that looks complete and is not, which is worse than a refusal because nobody
    will question it.

    Every figure ships with all six inputs that produced it — planned run seconds,
    run seconds, ideal cycle seconds, produced, good and unplanned seconds — so a
    manager can check the arithmetic rather than trust the index. This is the same
    rule as ``workforce`` shipping the names behind a count.
    """
    limit = _bounded(limit, 'limit', 1, MAX_STATIONS)
    _declared_levels(tenant)
    entry = _resolve_register(tenant)
    rows, raw = _read_rows(engine, tenant, agent, entry)
    wanted = _text(station, 255).strip()

    default_plan = entry.get('planned_run_seconds')
    by_station, unreadable, skipped = {}, 0, 0

    for row in rows:
        name = _text(row.get(entry['station_column']), 255).strip()
        if not name:
            skipped += 1
            continue
        if wanted and name != wanted:
            continue
        bucket = by_station.setdefault(name, {
            'station': name, 'samples': 0,
            'planned': None, 'run': 0.0, 'run_read': False,
            'ideal': None, 'produced': 0.0, 'produced_read': False,
            'good': 0.0, 'good_read': False, 'unplanned': 0.0,
        })
        bucket['samples'] += 1

        # A blank cell is 'not recorded', so the declared constant serves as the
        # fallback — that is what a default is for, and the alternative (leaving
        # the plan unset) would refuse availability for a row the customer simply
        # did not fill in, while a *non-blank* unreadable cell is a real problem
        # and is counted rather than papered over with the default.
        if entry['planned_run_column']:
            raw_plan = row.get(entry['planned_run_column'])
            planned = _number(raw_plan)
            if planned is not None and planned > 0:
                bucket['planned'] = planned
            elif _text(raw_plan, 32).strip():
                unreadable += 1
            elif default_plan is not None and bucket['planned'] is None:
                bucket['planned'] = default_plan
        elif default_plan is not None and bucket['planned'] is None:
            bucket['planned'] = default_plan

        run = _number(row.get(entry['run_column'])) if entry['run_column'] else None
        if run is not None and run >= 0:
            bucket['run'] += run
            bucket['run_read'] = True
        elif entry['run_column'] and _text(row.get(entry['run_column']), 32).strip():
            unreadable += 1

        if entry['ideal_cycle_column']:
            ideal = _number(row.get(entry['ideal_cycle_column']))
            if ideal is not None and ideal > 0:
                bucket['ideal'] = ideal
            elif _text(row.get(entry['ideal_cycle_column']), 32).strip():
                unreadable += 1

        produced = _number(row.get(entry['produced_column'])) if entry['produced_column'] else None
        if produced is not None and produced >= 0:
            bucket['produced'] += produced
            bucket['produced_read'] = True
        elif entry['produced_column'] and _text(row.get(entry['produced_column']), 32).strip():
            unreadable += 1

        good = _number(row.get(entry['good_column'])) if entry['good_column'] else None
        if good is not None and good >= 0:
            bucket['good'] += good
            bucket['good_read'] = True
        elif entry['good_column'] and _text(row.get(entry['good_column']), 32).strip():
            unreadable += 1

        if entry['unplanned_column']:
            unplanned = _number(row.get(entry['unplanned_column']))
            if unplanned is not None and unplanned >= 0:
                bucket['unplanned'] += unplanned
            elif _text(row.get(entry['unplanned_column']), 32).strip():
                unreadable += 1

    stations = []
    good_exceeds_produced = 0
    for bucket in sorted(by_station.values(), key=lambda item: (-item['produced'], item['station'])):
        planned = bucket['planned']
        run = bucket['run'] if bucket['run_read'] else None
        ideal = bucket['ideal']
        produced = bucket['produced'] if bucket['produced_read'] else None
        good = bucket['good'] if bucket['good_read'] else None
        unplanned = bucket['unplanned'] if entry['unplanned_column'] else None

        # ``produced`` and ``good`` may have been read as an explicit zero; that is
        # a real zero and a real input, so the presence flags decide whether the
        # value is a *reading* rather than ``is not None``.
        inputs = {'planned_run_seconds': planned, 'run_seconds': run,
                  'ideal_cycle_seconds': ideal, 'produced': produced, 'good': good}
        if produced is not None and good is not None and good > produced:
            good_exceeds_produced += 1

        factors = {
            'availability': _factor_availability(planned, run),
            'performance': _factor_performance(ideal, run, produced),
            'quality': _factor_quality(produced, good),
        }

        computable = all(factors[name] is not None for name in FACTORS)
        oee = None
        if computable:
            product = 1.0
            for name in FACTORS:
                product *= factors[name]
            oee = product

        record = {
            'station': bucket['station'],
            'samples': bucket['samples'],
            'oee': _ratio(oee),
            'oee_computable': computable,
            'availability': _ratio(factors['availability']),
            'performance': _ratio(factors['performance']),
            'quality': _ratio(factors['quality']),
            'inputs': {
                'planned_run_seconds': planned,
                'run_seconds': run,
                'ideal_cycle_seconds': ideal,
                'produced': produced,
                'good': good,
                'unplanned_seconds': unplanned,
            },
            'not_computable': _missing_inputs(factors, inputs),
        }
        bound = _bind(tenant, bucket['station'])
        if bound:
            record['path'] = bound
        stations.append(record)
        if len(stations) >= limit:
            break

    unbound = sum(1 for record in stations if not record.get('path'))
    return {
        'view': 'oee',
        'register': entry['register'],
        'station': wanted,
        'stations': stations,
        'station_count': len(by_station),
        'computable_count': sum(1 for record in stations if record['oee_computable']),
        'good_exceeds_produced': good_exceeds_produced,
        'unbound': unbound,
        'unreadable': unreadable,
        'skipped': skipped,
        'scanned': len(rows),
        'truncated': len(by_station) > limit or raw.get('truncated', False),
        'complete': True,
        'source_errors': [],
        'authority': _authority(engine, tenant, agent),
        'note': ('OEE faqat uchala faktor ham hisoblanaganda beriladi. '
                 'Rejalashtirilgan ish vaqti va ideal sikl vaqti — mijozning '
                 'DEKLARATSIYASI, platforma ularni o‘ylab topmaydi. Faktor '
                 'hisoblanmasa — o‘sha faktor None, va o‘rniga to‘ldirilmaydi. '
                 'Xodim bo‘yicha ko‘rsatkich yo‘q.'),
    }


def _ratio(value):
    """A 0..1 ratio rounded for display, or ``None``.

    Rounded to four places, the same precision P12's yield uses, so two figures
    read side by side do not disagree in the last digit for display reasons.
    """
    if value is None:
        return None
    return round(value, 4)


# ------------------------------------------------------------------ andon


def andon(engine, tenant, agent, step, *, limit=MAX_STATIONS):
    """Which stations breached a declared threshold, as a fact.

    'Andon' is the alarm cord: a threshold breach a manager is told about without
    asking. This tool is the **detection** half only — it returns which stations
    breached which threshold, the measured value and the bound it crossed.
    Delivery reuses the P6 escalation coordinator, so this module does not grow a
    second notification path and a model can neither manufacture an alarm nor
    suppress one.

    **The platform does not decide that 85% is a bad OEE.** Thresholds are
    operator configuration; an alarm the customer did not configure is an opinion,
    and an opinion wearing a number is the failure mode this whole block exists to
    avoid.

    A station whose value is not computable is reported as ``breached: False``
    with a reason, never as a breach: an uncomputable OEE is a missing input, not
    a failing line, and conflating the two would page a manager about a column
    name.

    **The alarm must see every station the read saw.** ``limit`` truncates eagerly
    inside ``report`` (``station_count`` stays the true count, ``truncated`` says
    the list is short), which is fine for a report a manager reads — but an andon
    that silently stopped watching station 51 would report ``breach_count: 0`` for
    a plant that is on fire. So ``andon`` never inherits a display limit: it asks
    ``report`` for the full evaluated set so every station in the register is
    compared against the declared thresholds. What the caller's ``limit`` then
    bounds is only what is *returned*, and ``complete`` is derived from the read's
    own truncation rather than asserted, because "no breaches" and "no breaches in
    the part we looked at" are different sentences and only one is safe to page a
    manager with.
    """
    limit = _bounded(limit, 'limit', 1, MAX_STATIONS)
    declared = oee_config(tenant)['thresholds']
    if not declared:
        raise Forbidden('No OEE thresholds are declared for this tenant; there is '
                        'nothing to compare against')
    # Evaluate the whole evaluated set, not the caller's display window: an alarm
    # that skips a station is the defect this line exists to prevent.
    result = report(engine, tenant, agent, step, limit=MAX_STATIONS)
    breaches, not_evaluated = [], 0
    # Which metrics could not be compared, and on which stations. A bare count is
    # the defect this whole block exists to avoid: "3" does not tell a manager
    # whether availability or quality is missing, and naming the metric is exactly
    # the property ``not_computable`` holds for the factors. The count stays (it is
    # what a threshold-tuning operator greps for) and the names are added beside it.
    unevaluated_metrics = set()
    unevaluated_stations = []
    for record in result['stations']:
        missing_here = []
        for key in sorted(declared):
            value = record.get(key)
            if value is None:
                not_evaluated += 1
                unevaluated_metrics.add(key)
                missing_here.append(key)
                continue
            percent = value * PERCENT
            bounds = declared[key]
            hit = None
            if 'below' in bounds and percent < bounds['below']:
                hit = 'below'
            if 'above' in bounds and percent > bounds['above']:
                hit = 'above'
            if hit is None:
                continue
            breaches.append({
                'station': record['station'],
                'metric': key,
                'value': round(percent, 4),
                'threshold': bounds[hit],
                'breach': hit,
                'path': record.get('path'),
            })
        if missing_here:
            unevaluated_stations.append({'station': record['station'],
                                         'metrics': sorted(missing_here)})
    unbound = sum(1 for record in result['stations'] if not record.get('path'))
    return {
        'view': 'andon',
        'register': result['register'],
        'breaches': breaches,
        'breach_count': len(breaches),
        'thresholds': {key: dict(bounds) for key, bounds in sorted(declared.items())},
        'not_evaluated': not_evaluated,
        'not_evaluated_metrics': sorted(unevaluated_metrics),
        'not_evaluated_stations': unevaluated_stations,
        # Every evaluated station was compared; the returned list is the caller's
        # window into them, so a caller asking for 5 still gets breaches from all.
        'stations': result['stations'][:limit],
        'station_count': result['station_count'],
        'computable_count': result['computable_count'],
        'unbound': unbound,
        'unreadable': result['unreadable'],
        'skipped': result['skipped'],
        'scanned': result['scanned'],
        'truncated': result['truncated'],
        'evaluated_count': len(result['stations']),
        # Truncation is a caveat on a report and a hole in an alarm. An andon that
        # did not look at every station must never call itself complete, or the
        # absence of a breach reads as "the plant is fine" rather than "we only
        # looked at 50 of 60 stations".
        'complete': not result['truncated'],
        'source_errors': [],
        'authority': _authority(engine, tenant, agent),
        'note': ('Bu — chegara buzilishi FAKTI, hukm emas va jazo emas. '
                 'Chegaralar operator konfiguratsiyasi: platforma 85% ni '
                 '"yomon" deb qaror qilmaydi. Hisoblanmaydigan stansiya '
                 'buzilish EMAS — u yo‘q kirish. Agar truncated=true bo‘lsa, '
                 'bu ro‘yxat TO‘LIQ EMAS: ba’zi stansiyalar ko‘rilmagan va '
                 'ular haqida hech narsa aytilmaydi.'),
    }


# --------------------------------------------------------------- tool handlers


def _report_tool(engine, tenant, agent, args, step):
    return report(engine, tenant, agent, step, station=args.get('station', ''),
                  limit=args.get('limit', MAX_STATIONS))


def _andon_tool(engine, tenant, agent, args, step):
    return andon(engine, tenant, agent, step, limit=args.get('limit', MAX_STATIONS))


def register_oee_tools(registry):
    """Two read tools. No write path exists here at all.

    Read-only because OEE is an observation and an andon is a threshold check. An
    entity able to write would be able to declare its own plan time and then score
    itself against it — which is the definition of a number nobody can trust.
    """
    from .tools import Tool, obj, string
    limit = {'type': 'integer', 'minimum': 1, 'maximum': MAX_STATIONS}
    tools = [
        ('oee.report', obj({'station': string(255), 'limit': limit}, required=[]),
         _report_tool),
        ('oee.andon', obj({'limit': limit}, required=[]), _andon_tool),
    ]
    for name, schema, handler in tools:
        if name in registry.items:
            continue
        registry.add(Tool(name, 'read', schema, handler))
