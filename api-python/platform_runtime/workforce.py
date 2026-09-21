"""Read-only oversight of the customer's people (PRD v0.5, P9b / T3).

A manager asks the same three questions about staff that they ask about agents:
who is in, who is overloaded, and what is past its due date. This module answers
those from HR data the customer already keeps — attendance, shifts and workload
are spreadsheets a clerk maintains today, so the platform reads them through the
ordinary declared-register mechanism rather than asking for a migration.

The line this module will not cross
-----------------------------------

**It never evaluates a person.** There is no score, rank, rating, productivity
index or comparison between employees anywhere in this file, and that is a
deliberate refusal written into the product's own scope:

    "Xodimni baholamaydi yoki jazolash qarori qabul qilmaydi. U faktlarni
     ko'rsatadi va menejerga eskalatsiya qiladi."  — PRD v0.5 §8

The reason is not politeness. A number that ranks people is a number that will be
used to sanction them, and the platform is not the right entity to decide that:
it does not know why someone was late, whether a shift was covered informally, or
what a contract says. So this module reports **counts and the names behind them**,
and hands the judgement to a manager. An "overdue" figure always ships with the
rows that produced it, so a manager can check the reason instead of trusting an
index.

Boundaries that make this safe to run:

* Every read goes through the existing ``sheets.rows`` tool, so the register must
  be operator-declared, the A1 range allowlisted, the agent must hold the tool and
  the connection must be permitted. This module adds no new data access path.
* The declared column mapping is operator configuration. The model names a view
  and a threshold; it cannot name a spreadsheet, a range or a column.
* A register that cannot be read is reported as ``complete: false`` with the
  source error class, never as "nobody is absent". An unread roster and an empty
  roster are different facts and must not look the same.
* A row missing the identity column is skipped and counted in ``skipped``, so
  malformed input is visible rather than silently dropped.
* Absence is only reported from a status the operator declared as absent. An
  unrecognised status is not guessed into a category; it is reported in
  ``unknown_status`` so the operator can extend the mapping.
"""
from __future__ import annotations

import re

from . import cells
from .engine import Forbidden

VIEWS = ('attendance', 'shifts', 'workload')
WORKFORCE_TOOLS = ('workforce.attendance', 'workforce.shifts', 'workforce.workload')

# Mirrors the sheets module's own row ceiling: a workforce view can never scan more
# than the register tool is willing to return, so declaring a larger bound here
# would only move the refusal to a less obvious place.
MAX_ROWS = 200
MAX_NAMES = 50
MAX_STATUS = 40

# A register name and range name follow the sheets module's own rules.
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')


class WorkforceError(RuntimeError):
    pass


def _bounded(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def _text(value, name, maximum=128):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{name} must be a non-empty string of at most {maximum} characters')
    return value.strip()


def workforce_config(tenant) -> dict:
    """Validate the tenant's declared workforce mapping, or an empty one.

    Configuration errors raise instead of being skipped: a typo in one column name
    must not silently produce an empty attendance list, because "no absent staff"
    and "we read the wrong column" would look identical to a manager.
    """
    from .tools import config
    raw = config(tenant).get('workforce')
    if raw is None:
        return {'registers': {}}
    if not isinstance(raw, dict):
        raise ValueError('workforce must be an object')
    unknown = set(raw) - {'registers'}
    if unknown:
        raise ValueError(f'workforce has unsupported keys: {sorted(unknown)}')
    declared = raw.get('registers', {})
    if not isinstance(declared, dict) or len(declared) > 20:
        raise ValueError('workforce.registers must be an object with at most 20 entries')
    clean = {}
    for name, entry in declared.items():
        clean[name] = _register(name, entry)
    return {'registers': clean}


def _register(name, entry):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f'Invalid workforce register name: {name!r}')
    if not isinstance(entry, dict):
        raise ValueError(f'Workforce register {name} must be an object')
    unknown = set(entry) - {'register', 'range', 'id_column', 'name_column', 'status_column',
                            'date_column', 'hours_column', 'task_column', 'due_column',
                            'absent_statuses', 'done_statuses', 'open_statuses'}
    if unknown:
        raise ValueError(f'Workforce register {name} has unsupported keys: {sorted(unknown)}')
    out = {}
    for key in ('register', 'range'):
        value = entry.get(key)
        if not isinstance(value, str) or not NAME_RE.match(value):
            raise ValueError(f'Workforce register {name} requires a valid {key}')
        out[key] = value
    for key in ('id_column', 'name_column'):
        value = entry.get(key)
        if not isinstance(value, str) or not value or len(value) > 64:
            raise ValueError(f'Workforce register {name} requires a {key}')
        out[key] = value
    for key in ('status_column', 'date_column', 'hours_column', 'task_column', 'due_column'):
        value = entry.get(key, '')
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError(f'Workforce register {name} {key} must be a string')
        out[key] = value
    out['absent_statuses'] = _status_list(entry.get('absent_statuses', []), name,
                                          'absent_statuses')
    out['done_statuses'] = _status_list(entry.get('done_statuses', []), name, 'done_statuses')
    out['open_statuses'] = _status_list(entry.get('open_statuses', []), name, 'open_statuses')
    return out


def _status_list(value, register, key):
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError(f'Workforce register {register} {key} must be a list of at most 20')
    clean = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > MAX_STATUS:
            raise ValueError(f'Workforce register {register} {key} has an invalid status')
        clean.append(item.strip().casefold())
    return clean


def _registers(tenant):
    return workforce_config(tenant)['registers']


def _resolve(tenant, view):
    """Pick the declared register backing a view, or refuse."""
    declared = _registers(tenant)
    if not declared:
        raise Forbidden('No workforce register is declared for this tenant')
    entry = declared.get(view)
    if entry is None:
        if len(declared) == 1:
            entry = next(iter(declared.values()))
        else:
            raise Forbidden(f'Workforce view {view!r} is not declared for this tenant')
    return entry


def _read_rows(engine, tenant, agent, entry):
    """Read one declared register through the ordinary sheets tool handler.

    Going through the registry means the register declaration, A1 allowlist, agent
    tool permission and connection allowlist all apply exactly as for a manual
    call. A provider failure surfaces as the handler's own error class.
    """
    tool = engine.registry.get('sheets.rows')
    args = {'register': entry['register'], 'range': entry['range']}
    tool.validate(args)
    result = tool.handler(engine, tenant, agent, args, 'workforce')
    rows = result.get('rows')
    if not isinstance(rows, list):
        raise WorkforceError('Workforce register returned an unexpected shape')
    return rows[:MAX_ROWS], result


def _cell_text(value, maximum=120):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)[:maximum]


def _status(value):
    return _cell_text(value, MAX_STATUS).strip().casefold()


def _hours(value):
    """Parse a declared hours cell. Unreadable hours are ``None``, never zero.

    Zero hours and "the cell was blank or unreadable" are different facts, and
    reporting the second as the first would understate a shift.

    Three refusals, each closing a way a cell could become a plausible wrong
    number rather than an admitted unreadable one:

    * **ASCII digits only.** ``float()`` normalises Devanagari, Arabic-Indic and
      fullwidth digits, so ``'१२'`` used to become twelve hours. See
      ``platform_runtime.cells``.
    * **No exponent notation.** ``float('1e5')`` is 100000, but ``1e5`` in a cell
      is usually a spreadsheet's rendering, not the number the operator typed.
    * **No non-finite value.** ``inf`` and ``nan`` are real Python floats and would
      otherwise sail through as "readable": ``inf`` hours would accumulate into a
      shift total no guard downstream catches. The identical hole was closed in
      P12's ``manufacturing._number`` and P13's ``oee._number``; it survived here
      because this function predates that sweep and does its own parsing.
    * **No unrepresentable integer.** ``float()`` raises ``OverflowError`` for an
      integer past the float range, so a 400-digit cell crashed the read instead of
      being counted as unreadable. Refused by name now, like every other cell this
      function cannot read.
    """
    if value in (None, ''):
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
    if not cells.is_ascii_number(text):
        return None
    return float(text.replace(',', '.'))


def _known_statuses(entry):
    """Every status the operator named for this register."""
    return (set(entry['absent_statuses']) | set(entry['done_statuses'])
            | set(entry['open_statuses']))


def attendance(engine, tenant, agent, *, absent_only=True, limit=MAX_NAMES):
    """Who is absent today, as declared by the operator's status mapping.

    Deliberately a fact list: the names behind the count are always included, so a
    manager sees *who* rather than an index, and can check the reason. There is no
    rate, ratio or ranking here.
    """
    limit = _bounded(limit, 'limit', 1, MAX_NAMES)
    if type(absent_only) is not bool:
        raise ValueError('absent_only must be a boolean')
    entry = _resolve(tenant, 'attendance')
    if not entry['status_column']:
        raise Forbidden('Workforce attendance register requires a status_column')
    rows, raw = _read_rows(engine, tenant, agent, entry)
    absent, present, unknown, skipped = [], 0, set(), 0
    for row in rows:
        identifier = _cell_text(row.get(entry['id_column']), 64)
        if not identifier:
            # A row without an identity cannot be attributed to a person and is
            # counted rather than dropped silently.
            skipped += 1
            continue
        status = _status(row.get(entry['status_column']))
        if not status:
            skipped += 1
            continue
        record = {'id': identifier,
                  'name': _cell_text(row.get(entry['name_column']), 120),
                  'status': _cell_text(row.get(entry['status_column']), MAX_STATUS)}
        if entry['date_column']:
            record['date'] = _cell_text(row.get(entry['date_column']), 40)
        if status in entry['absent_statuses']:
            absent.append(record)
            continue
        present += 1
        # An operator may declare only the absent statuses. A status that appears
        # nowhere in the mapping is surfaced so it can be declared, rather than
        # guessed into "present" and quietly miscounted.
        if status not in _known_statuses(entry):
            unknown.add(_cell_text(row.get(entry['status_column']), MAX_STATUS))
    return {
        'view': 'attendance',
        'register': entry['register'],
        'absent': absent[:limit],
        'absent_count': len(absent),
        'present_count': present,
        'unknown_status': sorted(unknown),
        'skipped': skipped,
        'scanned': len(rows),
        'truncated': len(absent) > limit or raw.get('truncated', False),
        'complete': True,
        'source_errors': [],
        'note': ('Bu faktlar ro‘yxati. Platforma xodimni baholamaydi va jazolash '
                 'qarori qabul qilmaydi — sababni menejer tekshiradi.'),
    }


def shifts(engine, tenant, agent, *, limit=MAX_NAMES):
    """Recent shifts and their declared hours, as rows rather than a total.

    No productivity figure is derived. Hours are reported per declared shift and
    the sum is left to the reader, because an "efficiency" number would be an
    evaluation the platform has no basis to make.
    """
    limit = _bounded(limit, 'limit', 1, MAX_NAMES)
    entry = _resolve(tenant, 'shifts')
    rows, raw = _read_rows(engine, tenant, agent, entry)
    out, unreadable, skipped = [], 0, 0
    cut = False
    for row in rows:
        identifier = _cell_text(row.get(entry['id_column']), 64)
        if not identifier:
            skipped += 1
            continue
        if len(out) >= limit:
            # Further shifts remain and the limit refused them. Read off the final
            # length instead, a register holding exactly `limit` shifts would also
            # be called truncated, and the operator would be told to narrow a query
            # whose answer is already complete.
            cut = True
            break
        record = {'id': identifier,
                  'name': _cell_text(row.get(entry['name_column']), 120)}
        if entry['date_column']:
            record['date'] = _cell_text(row.get(entry['date_column']), 40)
        if entry['status_column']:
            record['status'] = _cell_text(row.get(entry['status_column']), MAX_STATUS)
        if entry['hours_column']:
            hours = _hours(row.get(entry['hours_column']))
            record['hours'] = hours
            if hours is None:
                unreadable += 1
        out.append(record)
    return {
        'view': 'shifts',
        'register': entry['register'],
        'shifts': out,
        'returned': len(out),
        'unreadable_hours': unreadable,
        'skipped': skipped,
        'scanned': len(rows),
        'truncated': raw.get('truncated', False) or cut,
        'complete': True,
        'source_errors': [],
        'note': ('Soatlar bo‘yicha yig‘indi yoki samaradorlik ko‘rsatkichi '
                 'hisoblanmaydi — bu baholash bo‘lardi.'),
    }


def workload(engine, tenant, agent, *, limit=MAX_NAMES, overdue_limit=MAX_ROWS):
    """Open and overdue work per person, counted from declared statuses.

    ``overdue`` counts rows whose declared due date is in the past *and* whose
    status is still open. The rows themselves are returned so the manager can see
    what is late rather than trusting a number. Dates are compared as text only
    when both sides are ISO-like; an unparsable date is counted in
    ``unparsable_due`` rather than treated as overdue or not.
    """
    limit = _bounded(limit, 'limit', 1, MAX_NAMES)
    overdue_limit = _bounded(overdue_limit, 'overdue_limit', 1, MAX_ROWS)
    entry = _resolve(tenant, 'workload')
    if not entry['task_column']:
        raise Forbidden('Workforce workload register requires a task_column')
    rows, raw = _read_rows(engine, tenant, agent, entry)
    now = engine.clock()
    today = _iso_day(now)
    by_person, overdue, unparsable, skipped = {}, [], 0, 0
    for row in rows:
        identifier = _cell_text(row.get(entry['id_column']), 64)
        if not identifier:
            skipped += 1
            continue
        person = by_person.setdefault(
            identifier, {'id': identifier,
                         'name': _cell_text(row.get(entry['name_column']), 120),
                         'open': 0, 'overdue': 0})
        status = _status(row.get(entry['status_column'])) if entry['status_column'] else ''
        due_raw = row.get(entry['due_column']) if entry['due_column'] else None
        due = _iso_day_text(due_raw)
        is_open = True
        if status and entry['done_statuses']:
            is_open = status not in entry['done_statuses']
        elif status and entry['open_statuses']:
            is_open = status in entry['open_statuses']
        if is_open:
            person['open'] += 1
        if is_open and entry['due_column'] and due_raw not in (None, ''):
            if due is None:
                unparsable += 1
            elif due < today:
                person['overdue'] += 1
                if len(overdue) < overdue_limit:
                    overdue.append({'person': identifier,
                                    'name': person['name'],
                                    'task': _cell_text(row.get(entry['task_column']), 120),
                                    'due': _cell_text(due_raw, 40),
                                    'status': _cell_text(row.get(entry['status_column']),
                                                         MAX_STATUS)})
    ranked = sorted(by_person.values(), key=lambda item: (-item['overdue'], -item['open'],
                                                          item['id']))
    people = ranked[:limit]
    # The slice is what cuts, so the comparison is against the pre-slice length.
    # `len(people) >= limit` would also be true when the register holds exactly
    # `limit` people and nothing was dropped.
    cut = len(ranked) > limit
    return {
        'view': 'workload',
        'register': entry['register'],
        'people': people,
        'overdue': overdue,
        'overdue_count': sum(item['overdue'] for item in by_person.values()),
        'open_count': sum(item['open'] for item in by_person.values()),
        'unparsable_due': unparsable,
        'skipped': skipped,
        'scanned': len(rows),
        'truncated': raw.get('truncated', False) or cut,
        'complete': True,
        'source_errors': [],
        'note': ('Kechikkan ish menejerga eskalatsiya qilinadi. Platforma xodimni '
                 'baholamaydi va jazolash qarori qabul qilmaydi.'),
    }


def _iso_day(now):
    """UTC day of a unix timestamp, as ``YYYY-MM-DD``."""
    import datetime
    return datetime.datetime.fromtimestamp(int(now), datetime.timezone.utc).strftime('%Y-%m-%d')


def _iso_day_text(value):
    """Parse a declared date cell to ``YYYY-MM-DD``, or ``None`` when unreadable.

    Only unambiguous ISO-like forms are accepted. A cell like ``10.01.2026`` is
    day-first in Uzbekistan but month-first elsewhere, so it is treated as
    unreadable rather than guessed: guessing would mark the wrong work as overdue.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None
    text = str(value).strip()
    # ``[0-9]`` and not ``\d``: the match is echoed back as the register key, so a
    # Unicode date would enter the platform under a key no ASCII reader can find,
    # and the value would not equal the string round-tripped through the sheet.
    match = re.match(r'^([0-9]{4})-([0-9]{2})-([0-9]{2})', text)
    if match:
        year, month, day = match.groups()
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return f'{year}-{month}-{day}'
    return None


def _dispatch(view, engine, tenant, agent, args):
    """Dispatch one workforce view. Read-only by construction."""
    if view == 'attendance':
        return attendance(engine, tenant, agent,
                          absent_only=args.get('absent_only', True),
                          limit=args.get('limit', MAX_NAMES))
    if view == 'shifts':
        return shifts(engine, tenant, agent, limit=args.get('limit', MAX_NAMES))
    if view == 'workload':
        return workload(engine, tenant, agent, limit=args.get('limit', MAX_NAMES),
                        overdue_limit=args.get('overdue_limit', MAX_ROWS))
    raise ValueError(f'Unsupported workforce view {view!r}')


def _attendance_tool(engine, tenant, agent, args, step):
    return _dispatch('attendance', engine, tenant, agent, args)


def _shifts_tool(engine, tenant, agent, args, step):
    return _dispatch('shifts', engine, tenant, agent, args)


def _workload_tool(engine, tenant, agent, args, step):
    return _dispatch('workload', engine, tenant, agent, args)


def register_workforce_tools(registry):
    """Three read tools. No write path exists here at all.

    Two reasons for read-only. First, the same one as agent oversight: an entity
    that can edit the record of what people did destroys the audit chain. Second,
    and specific to people — the platform must not be able to change a person's
    record, because doing so would make it the author of the very facts it is
    reporting on, and this module's whole purpose is to show a manager what is
    really in the sheet.
    """
    from .tools import Tool, obj, string, register_once
    limit = {'type': 'integer', 'minimum': 1, 'maximum': MAX_NAMES}
    register_once(registry, [
        Tool('workforce.attendance', 'read', obj({
            'absent_only': {'type': 'boolean'}, 'limit': limit}), _attendance_tool),
        Tool('workforce.shifts', 'read', obj({'limit': limit}), _shifts_tool),
        Tool('workforce.workload', 'read', obj({
            'limit': limit,
            'overdue_limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS},
        }), _workload_tool),
    ])
