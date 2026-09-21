"""Camera events as one more bounded read source (PRD v0.5, P11b / T4).

The customer already owns cameras and, usually, an edge box that runs a model.
That is not the gap. The gap the PRD names is the compute layer *between* the
camera and a structured output:

    "The infrastructure gap is not the camera. It is the compute layer between
     the camera and a structured data output."

So this module is an **event reader**, not a video system. It reads a register
the operator already writes — a VMS table or a webhook landing log — and turns
each row into a structured event bound to an asset from P11.

The two rules this module exists to enforce
-------------------------------------------

**1. Frames never reach the platform.** Only ``{station, event, timestamp,
confidence}``. This is the same rule as voice in v0.4 ("audio never reaches the
planner, only the transcript"), and here it is not merely cheaper — it is
legally required. Under Uzbekistan Law No. O'RQ-1125 (26.03.2026) biometric data
must be stored locally and entered in a state register, so a pipeline that
carried frames to the cloud would put the customer in breach. Keeping the frame
on the edge is therefore a **requirement**, not an optimisation.

**2. Person-identifying events cannot be read through the ordinary path.** The
PRD splits the tool surface in two for exactly this reason:

    ``vision.station_event`` — impersonal (defect, cycle time, idle, SOP
    deviation), milder threshold.
    ``vision.person_event`` — **biometric**, ``human_led`` mandatory.

That split is implemented here as a hard gate rather than as documentation. An
event class the operator declares as person-identifying can only be read when the
agent's ladder is ``human_led`` *and* the operator has acknowledged the biometric
obligation. A person event read by an ``autonomous`` agent is refused by name,
because an autonomous agent cannot be the entity that satisfies a legal duty.

Boundaries that make this safe to run:

* Every read goes through the existing ``sheets.rows`` tool, so the register must
  be operator-declared, the A1 range allowlisted, the agent must hold the tool and
  the connection must be permitted. This module adds no new data access path.
* An event whose station cannot be placed in the asset hierarchy is **reported**,
  not dropped and not bound to a guessed asset. An event bound to the wrong plant
  is worse than an event reported as unbound.
* Reads are **windowed and limited**. The stream is continuous in reality, so an
  unbounded read would pour millions of rows into a planner; ``since``/``until``
  and ``limit`` are both bounded, and ``truncated`` always ships.
* A confidence figure is reported as the register's own text and is **never**
  converted into a correctness claim. The PRD's own warning applies: vendor
  accuracy figures depend on camera angle and lighting.
"""
from __future__ import annotations

import re

from . import assets
from .engine import Forbidden

VISION_TOOLS = ('vision.station_event', 'vision.person_event', 'vision.summary')

# Mirrors the sheets module's own row ceiling: a vision read can never scan more
# than the register tool will return, so declaring a larger bound would only move
# the refusal somewhere less obvious.
MAX_ROWS = 200
MAX_EVENTS = 200
MAX_CLASSES = 32
MAX_CLASS_CHARS = 64
# ``MAX_WINDOW_DAYS = 31`` used to sit here and nothing read it. See ``telephony``
# for the same removal: the span between ``since`` and ``until`` is validated for
# FORMAT and never for distance, so a declared span ceiling would have been a limit
# nobody enforces -- and restating it in two modules is what let it stay invisible.

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')

# An ISO calendar date. The window is compared as text against the register's own
# cell, deliberately: parsing provider text would guess at ambiguous formats such
# as 10.01.2026 and silently include or exclude the wrong day. ``[0-9]`` and not
# ``\d``, so a Unicode date cannot be normalised by a later change that starts
# parsing it. See ``platform_runtime.cells``.
DAY_RE = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')

# The declared sensitivity of an event class. ``station`` is impersonal; the other
# two are not, and only ``station`` may be read on the ordinary path.
SENSITIVITIES = ('station', 'person', 'biometric')


class VisionError(RuntimeError):
    pass


def _bounded(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def _day(value, name):
    if value is None or value == '':
        return ''
    if not isinstance(value, str) or not DAY_RE.match(value):
        raise ValueError(f'{name} must be an ISO date (YYYY-MM-DD)')
    return value


def _class_list(value, register, key):
    if not isinstance(value, list) or len(value) > MAX_CLASSES:
        raise ValueError(f'Vision register {register} {key} must be a list of at most '
                         f'{MAX_CLASSES}')
    clean = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > MAX_CLASS_CHARS:
            raise ValueError(f'Vision register {register} {key} has an invalid event class')
        clean.append(item.strip().casefold())
    return clean


def _register(name, entry):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f'Invalid vision register name: {name!r}')
    if not isinstance(entry, dict):
        raise ValueError(f'Vision register {name} must be an object')
    unknown = set(entry) - {'register', 'range', 'station_column', 'event_column',
                            'timestamp_column', 'confidence_column', 'sensitivity',
                            'person_classes', 'biometric_ack'}
    if unknown:
        raise ValueError(f'Vision register {name} has unsupported keys: {sorted(unknown)}')
    out = {}
    for key in ('register', 'range'):
        value = entry.get(key)
        if not isinstance(value, str) or not NAME_RE.match(value):
            raise ValueError(f'Vision register {name} requires a valid {key}')
        out[key] = value
    # The station column is the join to the asset hierarchy. Without it an event
    # has no identity, which is the one thing P11 exists to provide.
    for key in ('station_column', 'event_column', 'timestamp_column'):
        value = entry.get(key)
        if not isinstance(value, str) or not value or len(value) > 64:
            raise ValueError(f'Vision register {name} requires a {key}')
        out[key] = value
    confidence = entry.get('confidence_column', '')
    if not isinstance(confidence, str) or len(confidence) > 64:
        raise ValueError(f'Vision register {name} confidence_column must be a string')
    out['confidence_column'] = confidence
    sensitivity = entry.get('sensitivity', 'station')
    if sensitivity not in SENSITIVITIES:
        raise ValueError(f'Vision register {name} sensitivity must be one of '
                         f'{list(SENSITIVITIES)}')
    out['sensitivity'] = sensitivity
    out['person_classes'] = _class_list(entry.get('person_classes', []), name,
                                        'person_classes')
    ack = entry.get('biometric_ack', False)
    if type(ack) is not bool:
        raise ValueError(f'Vision register {name} biometric_ack must be a boolean')
    out['biometric_ack'] = ack
    # A register declared person-identifying must name the classes that are. An
    # empty list would make the declaration meaningless: every row would be read
    # on the impersonal path while the register claims to be sensitive.
    if sensitivity in ('person', 'biometric') and not out['person_classes']:
        raise ValueError(f'Vision register {name} is declared {sensitivity} but names no '
                         f'person_classes')
    return out


def vision_config(tenant) -> dict:
    """Validate the tenant's declared vision mapping, or an empty one.

    Configuration errors raise instead of being skipped: a typo in a column name
    must not silently produce an empty event list, because "no safety incident"
    and "we read the wrong column" would look identical to a manager.
    """
    from .tools import config
    raw = config(tenant).get('vision')
    if raw is None:
        return {'registers': {}}
    if not isinstance(raw, dict):
        raise ValueError('vision must be an object')
    unknown = set(raw) - {'registers'}
    if unknown:
        raise ValueError(f'vision has unsupported keys: {sorted(unknown)}')
    declared = raw.get('registers', {})
    if not isinstance(declared, dict) or len(declared) > 20:
        raise ValueError('vision.registers must be an object with at most 20 entries')
    return {'registers': {name: _register(name, entry)
                          for name, entry in declared.items()}}


def _registers(tenant):
    return vision_config(tenant)['registers']


def person_classes(tenant):
    """Every class this tenant declared as person-identifying, across all registers.

    Deliberately **tenant-wide, not per-register**. A class is a property of the
    *event*, not of the table it happens to sit in: if ``yuz`` is identifying in
    the access register it is identifying in the shop-floor register too. Scoping
    the vocabulary per register would mean a face-detection row landing in the
    wrong sheet is read on the impersonal path, which is precisely the mistake the
    legal boundary exists to prevent.
    """
    names = set()
    for entry in _registers(tenant).values():
        names.update(entry['person_classes'])
    return frozenset(names)


def _resolve(tenant, sensitivity):
    """Pick the declared register matching ``sensitivity``, or refuse."""
    declared = _registers(tenant)
    if not declared:
        raise Forbidden('No vision register is declared for this tenant')
    matching = [entry for entry in declared.values() if entry['sensitivity'] == sensitivity]
    if not matching:
        raise Forbidden(f'No vision register is declared with sensitivity {sensitivity!r}')
    return matching[0]


def _ladder(engine, tenant, agent):
    return str(engine.policy(tenant, agent).get('ladder', ''))


def _require_human_led(engine, tenant, agent, entry):
    """The biometric gate.

    A person-identifying register may only be read by a ``human_led`` agent, and
    only when the operator has acknowledged the biometric obligation. This is the
    PRD's split made executable:

        ``vision.station_event`` (impersonal) vs ``vision.person_event``
        (**biometric**, ``human_led`` mandatory)

    The refusal is by name and names the reason, because an operator who sees
    ``Forbidden`` with no explanation will disable the check rather than fix the
    configuration.
    """
    if not entry['biometric_ack']:
        raise Forbidden(
            f'Vision register {entry["register"]!r} is person-identifying but '
            f'biometric_ack is not set. Person-identifying vision data is biometric '
            f'under Uzbekistan Law No. O\'RQ-1125: it may not be processed until the '
            f'operator acknowledges the local-storage and state-register obligation')
    ladder = _ladder(engine, tenant, agent)
    if ladder != 'human_led':
        raise Forbidden(
            f'Person-identifying vision events require a human_led agent, but '
            f'{agent!r} is {ladder or "unset"!r}. A legal duty cannot be discharged '
            f'by an autonomous agent')


def _read_rows(engine, tenant, agent, entry):
    """Read one declared register through the ordinary sheets tool handler.

    Going through the registry means the register declaration, A1 allowlist, agent
    tool permission and connection allowlist all apply exactly as for a manual
    call. A provider failure surfaces as the handler's own error class.
    """
    tool = engine.registry.get('sheets.rows')
    args = {'register': entry['register'], 'range': entry['range']}
    tool.validate(args)
    result = tool.handler(engine, tenant, agent, args, 'vision')
    rows = result.get('rows')
    if not isinstance(rows, list):
        raise VisionError('Vision register returned an unexpected shape')
    return rows[:MAX_ROWS], result


def _cell_text(value, maximum=120):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)[:maximum]


def _event_class(entry, cell):
    """The declared class of one row, casefolded, or ``''`` when unreadable."""
    text = _cell_text(cell, MAX_CLASS_CHARS).strip().casefold()
    return text


def _is_person(event_class, identifying):
    """Whether a row's class is one the tenant declared as person-identifying."""
    return bool(event_class) and event_class in identifying


def _bind(tenant, station):
    """Place a station cell in the asset hierarchy as an **asset**, or ``None``.

    The PRD binds each event to the ``asset`` entity so it can appear on that
    asset's ``graph.timeline``. An intern node — a plant, a shop, a line — is not
    an asset and has no timeline, so an event naming one is reported as unbound
    rather than attached to a node the customer never asked about. That is a
    tolerance decision made in one direction on purpose: attaching a defect to
    "the plant" because the row said ``zavod-1`` would invent an owner.

    Matching is by segment, never by string prefix: ``zavod-1`` must not claim
    ``zavod-10``'s events, which the naive prefix test would do silently.
    """
    try:
        segments = assets.parse_path(tenant, station)
    except (ValueError, Forbidden):
        return None
    return segments


def _window(row_day, since, until):
    """Whether a row's day falls in the declared window.

    Compared as text on ISO dates. A cell that is not an ISO date is treated as
    outside the window only when a window was asked for, and is otherwise
    reported so the operator can see the format problem rather than lose the row.
    """
    if not since and not until:
        return True
    if not DAY_RE.match(row_day or ''):
        return False
    if since and row_day < since:
        return False
    if until and row_day > until:
        return False
    return True


def _scan(engine, tenant, agent, entry, *, since='', until='', limit=MAX_EVENTS):
    """Read, window and bind one register. The shared body of both reads."""
    limit = _bounded(limit, 'limit', 1, MAX_EVENTS)
    declared = assets._declared_levels(tenant)
    if not declared:
        raise Forbidden('No asset hierarchy is declared for this tenant')
    rows, raw = _read_rows(engine, tenant, agent, entry)
    identifying = person_classes(tenant)
    wants_person = entry['sensitivity'] != 'station'
    events, unbound, undated, skipped = [], 0, 0, 0
    matched = 0
    for row in rows:
        station = _cell_text(row.get(entry['station_column']), 255).strip()
        event_class = _event_class(entry, row.get(entry['event_column']))
        if not station or not event_class:
            # Without a station and a class there is nothing to bind or report.
            skipped += 1
            continue
        # A class declared identifying anywhere is identifying everywhere, so a
        # person row that lands in the shop-floor sheet is dropped by the
        # impersonal read rather than returned as an ordinary event.
        if _is_person(event_class, identifying) is not wants_person:
            continue
        day = _cell_text(row.get(entry['timestamp_column']), 40).strip()
        if not DAY_RE.match(day or ''):
            undated += 1
            continue
        if not _window(day, since, until):
            continue
        matched += 1
        segments = _bind(tenant, station)
        if segments is None:
            # Reported, never guessed. An event bound to the wrong plant is worse
            # than an event the manager can see is unbound.
            unbound += 1
            continue
        if len(events) >= limit:
            continue
        record = {
            'station': station,
            'path': assets.format_path(segments),
            'event': event_class,
            'day': day,
        }
        if entry['confidence_column']:
            confidence = _cell_text(row.get(entry['confidence_column']), 32).strip()
            if confidence:
                # Reported verbatim. Never converted to a correctness claim.
                record['confidence'] = confidence
        events.append(record)
    return {
        'register': entry['register'],
        'sensitivity': entry['sensitivity'],
        'events': events,
        'count': matched,
        'returned': len(events),
        'unbound': unbound,
        'undated': undated,
        'skipped': skipped,
        'scanned': len(rows),
        # `matched` counts every row that passed the WINDOW, including the ones that
        # could not bind to an asset and never became events, so `matched > limit`
        # reported a cut list whenever unbound rows outnumbered the limit -- and the
        # caller was sent to re-run a query that returns the same rows. The list is
        # cut exactly when the BINDABLE rows exceed what came back. `raw['truncated']`
        # is the Sheets read's own flag and still counts: a cut source read means a
        # cut event list however this register behaved.
        'truncated': matched - unbound > len(events) or raw.get('truncated', False),
        'complete': True,
        'source_errors': [],
    }


def station_event(engine, tenant, agent, *, since='', until='', limit=MAX_EVENTS):
    """Impersonal events: defects, cycle times, idle spans, SOP deviations.

    This is the ordinary path. It is still windowed and bounded, because the
    stream is continuous in reality and an unbounded read would pour the whole
    day onto the planner.
    """
    since, until = _day(since, 'since'), _day(until, 'until')
    _check_window(since, until)
    entry = _resolve(tenant, 'station')
    result = _scan(engine, tenant, agent, entry, since=since, until=until, limit=limit)
    result['view'] = 'station_event'
    result['note'] = ('Bu hodisalar fakt. Ishonch ko‘rsatkichi reestrning o‘z matni '
                      'sifatida beriladi va to‘g‘rilik da’vosi emas.')
    result['authority'] = {'agent': agent, 'ladder': _ladder(engine, tenant, agent)}
    return result


def person_event(engine, tenant, agent, *, since='', until='', limit=MAX_EVENTS):
    """Person-identifying events. Biometric, therefore ``human_led`` only.

    The gate runs **before** any register is read, so an agent without the right
    ladder cannot cause provider I/O against biometric data at all.
    """
    since, until = _day(since, 'since'), _day(until, 'until')
    _check_window(since, until)
    entry = _resolve(tenant, 'person')
    _require_human_led(engine, tenant, agent, entry)
    result = _scan(engine, tenant, agent, entry, since=since, until=until, limit=limit)
    result['view'] = 'person_event'
    result['note'] = ('Bu biometrik toifa. Yuzni tanish modeli mijozniki; platforma '
                      'model o‘qitmaydi va kadr saqlamaydi.')
    result['authority'] = {'agent': agent, 'ladder': _ladder(engine, tenant, agent)}
    return result


def summary(engine, tenant, agent, *, since='', until='', limit=MAX_EVENTS):
    """Event counts by class and by station, from impersonal events only.

    Deliberately **not** an OEE figure: performance and availability need cycle
    times and planned run time, which is P13's work. Counting events by class is
    what the register can actually support, and claiming more would be a number
    the customer cannot check.

    Person events are never counted here. A count of people-identified events is
    itself biometric processing, so it stays behind the gate.
    """
    since, until = _day(since, 'since'), _day(until, 'until')
    _check_window(since, until)
    limit = _bounded(limit, 'limit', 1, MAX_EVENTS)
    entry = _resolve(tenant, 'station')
    declared = assets._declared_levels(tenant)
    if not declared:
        raise Forbidden('No asset hierarchy is declared for this tenant')
    rows, raw = _read_rows(engine, tenant, agent, entry)
    identifying = person_classes(tenant)
    by_class, by_station, undated, skipped = {}, {}, 0, 0
    for row in rows:
        station = _cell_text(row.get(entry['station_column']), 255).strip()
        event_class = _event_class(entry, row.get(entry['event_column']))
        if not station or not event_class or _is_person(event_class, identifying):
            skipped += 1
            continue
        day = _cell_text(row.get(entry['timestamp_column']), 40).strip()
        if not DAY_RE.match(day or ''):
            undated += 1
            continue
        if not _window(day, since, until):
            continue
        by_class[event_class] = by_class.get(event_class, 0) + 1
        entry_station = by_station.setdefault(station, {'station': station, 'count': 0})
        entry_station['count'] += 1
    ordered = sorted(by_station.values(), key=lambda item: (-item['count'], item['station']))
    for item in ordered[:limit]:
        segments = _bind(tenant, item['station'])
        item['path'] = assets.format_path(segments) if segments else ''
    return {
        'view': 'summary',
        'register': entry['register'],
        'by_class': dict(sorted(by_class.items())),
        'by_station': ordered[:limit],
        'station_count': len(ordered),
        'undated': undated,
        'skipped': skipped,
        'scanned': len(rows),
        'truncated': len(ordered) > limit or raw.get('truncated', False),
        'complete': True,
        'source_errors': [],
        'note': ('Bu hodisa SONI. OEE emas: unumdorlik va mavjudlik uchun sikl '
                 'vaqti va rejalashtirilgan ish vaqti kerak — bu P13 ishi.'),
        'authority': {'agent': agent, 'ladder': _ladder(engine, tenant, agent)},
    }


def _check_window(since, until):
    if since and until and since > until:
        raise ValueError('since must not be after until')


# --------------------------------------------------------------- tool handlers


def _station_tool(engine, tenant, agent, args, step):
    return station_event(engine, tenant, agent, since=args.get('since', ''),
                         until=args.get('until', ''), limit=args.get('limit', MAX_EVENTS))


def _person_tool(engine, tenant, agent, args, step):
    return person_event(engine, tenant, agent, since=args.get('since', ''),
                        until=args.get('until', ''), limit=args.get('limit', MAX_EVENTS))


def _summary_tool(engine, tenant, agent, args, step):
    return summary(engine, tenant, agent, since=args.get('since', ''),
                   until=args.get('until', ''), limit=args.get('limit', MAX_EVENTS))


def register_vision_tools(registry):
    """Three read tools. No write path exists here at all.

    Read-only because a camera event is evidence: an entity able to edit or delete
    an event could rewrite what happened on a shop floor. The customer's VMS owns
    the record; the platform reads it.
    """
    from .tools import Tool, obj, string
    window = {'since': string(10), 'until': string(10),
              'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_EVENTS}}
    tools = [
        ('vision.station_event', obj(window, required=[]), _station_tool),
        ('vision.person_event', obj(window, required=[]), _person_tool),
        ('vision.summary', obj(window, required=[]), _summary_tool),
    ]
    for name, schema, handler in tools:
        if name in registry.items:
            continue
        registry.add(Tool(name, 'read', schema, handler))
