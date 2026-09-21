"""Operator-declared Google Sheets registers: bounded read access to business data.

Real Uzbek SMB data does not live in a fixed API resource. The finance team keeps its
own spreadsheet, HR keeps another, and a "Google Sheets-ga o'xshagan" tool keeps a
third. So an operator declares named *registers* — an OAuth connection, a spreadsheet
id and an allowlist of A1 ranges — and the agent may read only those.

Boundaries that make this safe:

* The spreadsheet id, the range and the credential are operator configuration. No tool
  argument, pack value or model output can supply a spreadsheet, a range or a URL.
* Only registers declared in ``sheets_registers`` exist. An undeclared name is refused
  rather than treated as an empty sheet, because an empty read looks like real data.
* Responses are bounded in rows, cell length and total bytes before they reach a model,
  so one wide sheet cannot silently blow up the planner context.
* Cell values are untrusted provider data. They are returned as data and truncated; the
  planner prompt already labels observations as untrusted.

Writes are deliberately absent here. The existing approval-gated ``sheets.append``
covers one declared range, and multi-range write needs the same transactional dispatch
fencing as the Google write path; adding a second, weaker write route would be worse
than having none.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .engine import Conflict, Forbidden
from .google_oauth import configured_manager
from .tools import config

MAX_RESPONSE_BYTES = 200_000
MAX_ROWS = 200
MAX_CELL_CHARS = 200
READ_SCOPE = 'https://www.googleapis.com/auth/spreadsheets.readonly'

REGISTER_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')
RANGE_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')
SPREADSHEET_ID_RE = re.compile(r'^[A-Za-z0-9_-]{20,120}$')
# A1 notation. Sheet names may be Cyrillic or contain spaces, which is normal in
# Uzbek and Russian spreadsheets; quotes, separators and control characters are not
# accepted, so a declared range cannot be spliced into a different request.
#
# A single bound must be a full cell (column + row). Inside a range Sheets also
# allows whole-column (`A:F`) and whole-row (`1:5`) bounds, so those are permitted
# only after a colon.
_SHEET_PART = r"[\w\u0400-\u04FF .\-]{1,64}"
_COL = r"[A-Z]{1,3}"
_ROW = r"[1-9][0-9]{0,6}"
_CELL = rf"{_COL}{_ROW}"
_BOUND = rf"(?:{_CELL}|{_COL}|{_ROW})"
A1_RE = re.compile(rf'^(?:{_SHEET_PART}!)?(?:{_CELL}(?::{_BOUND})?|{_COL}:{_BOUND}|{_ROW}:{_BOUND})?$')
CONNECTION_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')


class SheetsError(RuntimeError):
    pass


def bounded_int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def validate_range(value, name='range'):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f'{name} must be a bounded non-empty A1 string')
    if not A1_RE.match(value) or value.startswith('!') or value.endswith('!'):
        raise ValueError(f'{name} is not valid A1 notation')
    return value


def registers(tenant) -> dict:
    """Validate and return every declared register for a tenant.

    Configuration errors raise instead of being skipped: a typo in one register must
    not silently hide it, because a missing register would look like "no data".
    """
    raw = config(tenant).get('sheets_registers')
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > 50:
        raise ValueError('sheets_registers must be an object with at most 50 entries')
    out = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not REGISTER_RE.match(name):
            raise ValueError(f'Invalid sheets register name: {name!r}')
        if not isinstance(entry, dict):
            raise ValueError(f'Sheets register {name} must be an object')
        unknown = set(entry) - {'connection', 'spreadsheet_id', 'ranges', 'header_row', 'max_rows'}
        if unknown:
            raise ValueError(f'Sheets register {name} has unsupported keys: {sorted(unknown)}')
        connection = entry.get('connection')
        if not isinstance(connection, str) or not CONNECTION_RE.match(connection):
            raise ValueError(f'Sheets register {name} requires a connection name')
        spreadsheet_id = entry.get('spreadsheet_id')
        if not isinstance(spreadsheet_id, str) or not SPREADSHEET_ID_RE.match(spreadsheet_id):
            raise ValueError(f'Sheets register {name} requires a valid spreadsheet_id')
        ranges = entry.get('ranges')
        if not isinstance(ranges, dict) or not ranges or len(ranges) > 40:
            raise ValueError(f'Sheets register {name} requires 1..40 ranges')
        clean_ranges = {}
        for range_name, a1 in ranges.items():
            if not isinstance(range_name, str) or not RANGE_NAME_RE.match(range_name):
                raise ValueError(f'Invalid range name {range_name!r} in register {name}')
            clean_ranges[range_name] = validate_range(a1, f'{name}.{range_name}')
        header_row = entry.get('header_row', True)
        if type(header_row) is not bool:
            raise ValueError(f'Sheets register {name} header_row must be a boolean')
        out[name] = {
            'connection': connection,
            'spreadsheet_id': spreadsheet_id,
            'ranges': clean_ranges,
            'header_row': header_row,
            'max_rows': bounded_int(entry.get('max_rows', 100), f'{name}.max_rows', 1, 1000),
        }
    return out


def register_names(tenant) -> list:
    return sorted(registers(tenant))


def resolve(tenant, register, range_name) -> dict:
    """Resolve a declared register + range, or refuse."""
    declared = registers(tenant)
    entry = declared.get(register)
    if entry is None:
        raise Forbidden(f'Sheets register {register!r} is not declared for this tenant')
    if range_name not in entry['ranges']:
        raise Forbidden(f'Range {range_name!r} is not declared in register {register!r}')
    return {
        'register': register,
        'connection': entry['connection'],
        'spreadsheet_id': entry['spreadsheet_id'],
        'a1': entry['ranges'][range_name],
        'header_row': entry['header_row'],
        'max_rows': entry['max_rows'],
    }


def _http_get(url, token):
    """Single bounded GET. No redirect, no retry: a failed read stays failed."""
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token},
                                     method='GET')
    from .tools import NoRedirect
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise SheetsError('Sheets response exceeded the byte limit')
            return json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as error:
        # The provider body can echo the range, a spreadsheet id or an internal URL.
        raise SheetsError(f'Sheets HTTP status {error.code}') from None
    except SheetsError:
        raise
    except Exception:
        raise SheetsError('Sheets transport failure') from None


def fetch(engine, tenant, connection, spreadsheet_id, a1, transport=None):
    """Bounded single read of one declared range."""
    target = urllib.parse.quote(a1, safe='')
    url = (f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{target}'
           '?majorDimension=ROWS&valueRenderOption=UNFORMATTED_VALUE'
           '&dateTimeRenderOption=FORMATTED_STRING')
    manager = configured_manager(engine, tenant, connection)
    grant = manager.access(tenant, connection, 'sheets-reader', [READ_SCOPE])
    payload = transport(url, grant.access_token) if transport else _http_get(url, grant.access_token)
    if not isinstance(payload, dict):
        raise SheetsError('Sheets response was not an object')
    values = payload.get('values', [])
    if not isinstance(values, list):
        raise SheetsError('Sheets response values were not a list')
    return values


def _cell(value) -> Any:
    """Truncate one cell. Untrusted provider text is never reformatted or evaluated."""
    if value is None:
        return ''
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_CELL_CHARS]
    return str(value)[:MAX_CELL_CHARS]


def shape_rows(values, header_row: bool, limit: int):
    """Turn a values matrix into bounded rows and, when declared, keyed objects."""
    capped = values[:limit + (1 if header_row else 0)]
    matrix = [[_cell(cell) for cell in row] for row in capped if isinstance(row, list)]
    if not header_row:
        return [], matrix[:limit]
    if not matrix:
        return [], []
    raw_header = matrix[0]
    # Blank or duplicate header cells become positional names, so a lookup can never
    # silently read the wrong column when two headers share a label.
    header, seen = [], {}
    for index, name in enumerate(raw_header):
        label = str(name).strip() or f'column_{index + 1}'
        seen[label] = seen.get(label, 0) + 1
        header.append(label if seen[label] == 1 else f'{label}_{seen[label]}')
    rows = []
    # DEFENSIVE: `capped` above already bounds `matrix` to `limit + 1` rows, so
    # `matrix[1:]` and `matrix[1:limit + 1]` are the same list and this slice is
    # unreachable through `_read`. It is kept because it states the intent -- at most
    # `limit` DATA rows -- independently of the cap, and it is pinned by its exact
    # line in the tests so a future change to `capped` cannot quietly widen it.
    for row in matrix[1:limit + 1]:
        entry = {}
        for index, name in enumerate(header):
            entry[name] = row[index] if index < len(row) else ''
        for index in range(len(header), len(row)):
            entry[f'column_{index + 1}'] = row[index]
        rows.append(entry)
    return header, rows


# --------------------------------------------------------------------- tool layer

def tool_registers(engine, tenant, agent, args, step):
    """List declared registers and range names. Ids are never returned.

    The connection is filtered by the **same** allowlist rule as ``_authorize``:
    an empty ``allowed_connections`` permits nothing. It used to read an empty
    list as "everything permitted", which made one policy value mean "all
    forbidden" in ``engine.py``, ``connectors.py``, ``google_adapters.py`` and
    ``business_graph.py`` and "all permitted" here. A register listing is not
    itself data, but it is a map of what exists, and handing one out under a
    policy every other module treats as closed is the same defect at a smaller
    scale.
    """
    policy = engine.policy(tenant, agent)
    if 'sheets.registers' not in policy.get('tools', []):
        raise Forbidden('sheets.registers not permitted for agent')
    declared = registers(tenant)
    allowed = policy.get('allowed_connections') or []
    return {'registers': [{'register': name,
                           'ranges': sorted(entry['ranges']),
                           'header_row': entry['header_row']}
                          for name, entry in sorted(declared.items())
                          if entry['connection'] in allowed]}


def _authorize(engine, tenant, agent, connection):
    """The strict reading, matching every other module.

    An empty (or absent) ``allowed_connections`` permits **nothing**. The lenient
    ``if allowed and connection not in allowed`` form was wrong not because it was
    reachable in the normal dispatch path -- ``engine.py`` checks first, so no
    handler in this module is reached without passing it -- but because it made
    the same policy value mean two different things depending on which file read
    it. A security model that disagrees with itself is a security model whose
    next reader cannot tell which half is the contract.
    """
    policy = engine.policy(tenant, agent)
    allowed = policy.get('allowed_connections') or []
    if connection not in allowed:
        raise Forbidden('Agent connection access denied')
    return allowed


def _read(engine, tenant, agent, args, *, keyed):
    tool_name = 'sheets.rows' if keyed else 'sheets.read'
    policy = engine.policy(tenant, agent)
    if tool_name not in policy.get('tools', []):
        raise Forbidden(f'{tool_name} not permitted for agent')
    resolved = resolve(tenant, args['register'], args['range'])
    _authorize(engine, tenant, agent, resolved['connection'])
    limit = args.get('limit')
    limit = resolved['max_rows'] if limit is None else min(limit, resolved['max_rows'])
    # Clamped, not raised. A register may DECLARE `max_rows` up to 1000 while the hard
    # read ceiling is MAX_ROWS, so the effective limit is the smaller of the two.
    # Passing the declared value straight in made a register declaring `max_rows: 500`
    # fail every ordinary read -- one that passes no `limit` at all -- with
    # "limit must be an integer 1..200", a configuration `registers` had accepted.
    limit = bounded_int(min(limit, MAX_ROWS), 'limit', 1, MAX_ROWS)
    values = fetch(engine, tenant, resolved['connection'], resolved['spreadsheet_id'],
                   resolved['a1'])
    header, rows = shape_rows(values, resolved['header_row'], limit)
    # `header_row` is true by DEFAULT, so `values` carries a header row that is not
    # data. The matrix slice must therefore admit it, exactly as `shape_rows` does
    # (`values[:limit + 1]`). Slicing at `limit` returned `limit - 1` DATA rows, and in
    # the window where the sheet held exactly `limit` data rows it dropped the last one
    # while `truncated` -- which counts data rows -- reported False. Measured: limit=3
    # with [header, d1, d2, d3] returned [header, d1, d2] and truncated=False.
    header_offset = 1 if resolved['header_row'] else 0
    matrix = values[:limit + header_offset]
    shown = [[_cell(cell) for cell in row] for row in matrix if isinstance(row, list)]
    result = {
        'register': resolved['register'],
        'range': args['range'],
        'a1': resolved['a1'],
        # DATA rows in BOTH tools. Counting matrix rows here made one field mean two
        # different things depending on which read tool the agent called.
        'returned': len(rows) if keyed else max(0, len(shown) - header_offset),
        # DATA rows, not matrix rows. `values` carries the header when `header_row` is
        # true -- and true is the DEFAULT -- so `len(values) > limit` announced a cut
        # for a sheet with a header and exactly `limit` data rows, sending the caller
        # to re-run a query that returns the same rows. Measured: limit=2 with
        # [header, row1, row2] gave returned=2 and truncated=True.
        'truncated': max(0, len(values) - header_offset) > limit,
    }
    if keyed:
        result['header'] = header
        result['rows'] = rows
    else:
        result['values'] = shown
    return result


def tool_read(engine, tenant, agent, args, step):
    return _read(engine, tenant, agent, args, keyed=False)


def tool_rows(engine, tenant, agent, args, step):
    return _read(engine, tenant, agent, args, keyed=True)


def register_sheets_tools(registry):
    from .tools import Tool, obj, string, register_once
    read_schema = obj({
        'register': string(64),
        'range': string(64),
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS},
    }, required=['register', 'range'])
    register_once(registry, [
        Tool('sheets.registers', 'read', obj({}), tool_registers, external=True),
        Tool('sheets.read', 'read', read_schema, tool_read, external=True),
        Tool('sheets.rows', 'read', read_schema, tool_rows, external=True),
    ])