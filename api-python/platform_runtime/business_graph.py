"""Operator-declared Business Graph: one read-only, cross-system entity view.

A customer's truth is spread over systems that will never be merged: a CRM holds
the deal, MoySklad holds stock, 1C holds the accounting price, and a spreadsheet
holds the margin a finance clerk typed last Tuesday. The graph does **not** merge
them. It resolves each declared attribute to *observations*, and every
observation keeps the source that produced it and when we read it.

Boundaries that make this safe:

* A graph source is an existing read tool (``connectors.read``, ``sheets.rows``,
  ``sheets.read``, ``database.read``) invoked through its ordinary handler. The
  graph therefore grants no authority of its own: if the agent may not read the
  connection or the register, it may not read it through the graph either. Every
  source's authority is checked *before* any provider I/O, so a read either has
  authority for all of its sources or reads nothing.
* Source arguments are operator configuration. The model may name an entity, an
  attribute and an id. It cannot name a table, a connection, a range or a URL.
* A conflict is reported, never silently resolved. Under ``report`` no value is
  selected at all; under ``primary_wins`` the operator's declared order picks one
  and the conflict is *still* reported. ``newest_wins`` is refused rather than
  faked, because ``observed`` is the local read time and not the provider's own
  update time.
* A failing source is recorded in ``source_errors`` and sets ``complete`` to
  ``false``. It is never turned into "this attribute does not exist": a missing
  value and an unread value must not look the same.
* An authority failure is re-raised rather than recorded. A denied connection is
  a policy answer, not a provider outage, and must not be softened into a
  partial read.

This module is read-only by construction. There is no write path here at all:
cross-system write needs per-source ownership, an approval and a reconcile pass,
and a weaker second route would be worse than having none.
"""
from __future__ import annotations

import json
import re

from .engine import Forbidden
from .tools import config

ENTITY_RE = re.compile(r'^[a-z][a-z0-9_.-]{0,63}$')
SOURCE_RE = re.compile(r'^[a-z][a-z0-9_.-]{0,63}$')
ATTRIBUTE_RE = re.compile(r'^[a-z][a-z0-9_.]{0,63}$')
_NUMERIC = re.compile(r'^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$')

# Only read tools may back a graph source. A write tool in this position would
# turn a read model into an unreviewed write path.
SAFE_SOURCE_TOOLS = frozenset({'connectors.read', 'sheets.rows', 'sheets.read',
                               'database.read'})
CONFLICT_POLICIES = ('report', 'primary_wins')

MAX_ENTITIES = 20
MAX_SOURCES = 8
MAX_MAP = 40
MAX_SCAN = 200
MAX_MATCHES = 100
MAX_CELL_CHARS = 200
# A priority table is operator configuration, so it is bounded like every other
# configured collection: an unbounded table is an unbounded validation cost.
MAX_PRIORITY_ATTRIBUTES = 40


class BusinessGraphError(RuntimeError):
    pass


def _text(value, name, maximum=128):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f'{name} must be a non-empty string of at most {maximum} characters')
    return value


def _cell(value):
    """Bound one provider value. Untrusted text is never reformatted or evaluated."""
    if value is None:
        return ''
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_CELL_CHARS]
    return str(value)[:MAX_CELL_CHARS]


def canonical(value):
    """Comparison form used only to decide whether two observations differ.

    A numeric-looking string and the same number count as one value: a database
    returns ``450000`` and a spreadsheet may return ``'450000'``, and reporting
    that as a price conflict would be noise. Anything else is compared as
    case-folded text.

    A number too large for a float is compared as text, deliberately. It cannot
    raise -- this is a read model, and a comparison helper that throws deletes the
    read -- and it must not collapse onto ``inf``, because two different enormous
    values would then compare EQUAL and the conflict this module exists to surface
    would silently disappear. Measured: ``'1' + '0' * 400`` and ``'2' + '0' * 400``
    both used to canonicalise to ``('n', inf)``.
    """
    if isinstance(value, bool):
        return ('b', value)
    if isinstance(value, (int, float)):
        try:
            return ('n', float(value))
        except OverflowError:
            # An integer past the float range. The text form is deterministic and
            # agrees with what the same value produces on the string path below.
            return ('s', str(value).strip().casefold())
    text = str(value).strip()
    if _NUMERIC.match(text):
        number = float(text)
        if -float('inf') < number < float('inf'):
            return ('n', number)
        # A numeric string longer than a float can hold parses to INFINITY without
        # raising, so distinct enormous values compared equal. Falling back to text
        # keeps them distinct, which is the whole job of this function.
        return ('s', text.casefold())
    return ('s', text.casefold())
def graph_config(tenant) -> dict:
    """Validate and return the tenant's declared graph, or an empty one.

    Configuration errors raise instead of being skipped: a typo in one source
    must not silently shrink the graph, because a missing source looks exactly
    like a system that has no data. The same rule applies to a *known* key: a
    key this function accepts must reach its consumers, or it is a key that
    configures nothing while looking like it does.
    """
    raw = config(tenant).get('business_graph')
    if raw is None:
        return {'conflict_policy': 'report', 'entities': {}, 'source_priority': {}}
    if not isinstance(raw, dict):
        raise ValueError('business_graph must be an object')
    # Per-attribute source priority may be declared at the top level (PRD 6.3),
    # inside an entity, or both. An entity wins over the top level, because the
    # more specific declaration is the one an operator meant.
    unknown = set(raw) - {'conflict_policy', 'entities', 'source_priority'}
    if unknown:
        raise ValueError(f'business_graph has unsupported keys: {sorted(unknown)}')
    policy = raw.get('conflict_policy', 'report')
    if policy not in CONFLICT_POLICIES:
        if policy == 'newest_wins':
            raise ValueError('conflict_policy newest_wins is not implemented: observed is '
                             'the local read time, not the provider update time')
        raise ValueError(f'conflict_policy must be one of {list(CONFLICT_POLICIES)}')
    declared = raw.get('entities', {})
    if not isinstance(declared, dict) or not declared or len(declared) > MAX_ENTITIES:
        raise ValueError(f'business_graph requires 1..{MAX_ENTITIES} entities')
    shared = _priority_table(raw.get('source_priority'), 'business_graph.source_priority')
    entities = {}
    for name, entry in declared.items():
        if not isinstance(name, str) or not ENTITY_RE.match(name):
            raise ValueError(f'Invalid business_graph entity name: {name!r}')
        entities[name] = _entity(name, entry, shared)
    return {'conflict_policy': policy, 'entities': entities,
            'source_priority': shared}


def _priority_table(value, label):
    """An operator's per-attribute source order, validated against nothing yet.

    The attribute names and source names a table mentions are checked against
    the entity it applies to, in ``_entity``, because a top-level table may
    legitimately mention entities and attributes this tenant has not declared --
    what it may not do is mention an attribute whose *sources* do not exist.

    ``conflict_policy: report`` remains the default and the recommendation, so a
    declared priority does not by itself change what a caller sees: it changes
    what ``primary_wins`` would select, and it is echoed back so the operator can
    see that the platform read the declaration at all.
    """
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_PRIORITY_ATTRIBUTES:
        raise ValueError(f'{label} must be an object of at most '
                         f'{MAX_PRIORITY_ATTRIBUTES} attributes')
    table = {}
    for attribute, entry in value.items():
        if not isinstance(attribute, str) or not ATTRIBUTE_RE.match(attribute):
            raise ValueError(f'Invalid attribute {attribute!r} in {label}')
        if isinstance(entry, dict):
            unknown = set(entry) - {'primary', 'fallback'}
            if unknown:
                raise ValueError(f'{label}.{attribute} has unsupported keys: '
                                 f'{sorted(unknown)}')
            primary = entry.get('primary')
            fallback = entry.get('fallback', [])
            if not isinstance(fallback, list):
                raise ValueError(f'{label}.{attribute}.fallback must be a list')
        else:
            # A bare string is accepted as shorthand for {primary: <name>}.
            primary, fallback = entry, []
        if primary is not None and (not isinstance(primary, str) or not SOURCE_RE.match(primary)):
            raise ValueError(f'{label}.{attribute}.primary is not a valid source name')
        order = []
        if primary:
            order.append(primary)
        for name in fallback:
            if not isinstance(name, str) or not SOURCE_RE.match(name):
                raise ValueError(f'{label}.{attribute}.fallback has an invalid source name')
            if name not in order:
                order.append(name)
        if not order:
            raise ValueError(f'{label}.{attribute} declares no source at all')
        table[attribute] = order
    return table


def _entity(name, entry, shared=None):
    if not isinstance(entry, dict):
        raise ValueError(f'Business graph entity {name} must be an object')
    unknown = set(entry) - {'identity', 'sources', 'priority', 'source_priority'}
    if unknown:
        raise ValueError(f'Business graph entity {name} has unsupported keys: {sorted(unknown)}')
    identity = _text(entry.get('identity', 'id'), f'{name}.identity', 64)
    sources = entry.get('sources')
    if not isinstance(sources, dict) or not sources or len(sources) > MAX_SOURCES:
        raise ValueError(f'Business graph entity {name} requires 1..{MAX_SOURCES} sources')
    clean = {}
    for source_name, source in sources.items():
        if not isinstance(source_name, str) or not SOURCE_RE.match(source_name):
            raise ValueError(f'Invalid source name {source_name!r} in entity {name}')
        clean[source_name] = _source(name, source_name, source)
    # ``priority`` is the whole-entity order and is what ``_primary`` falls back
    # to for an attribute with no per-attribute declaration. It is optional, and
    # when it is omitted the order is the declaration order of `sources`.
    #
    # That fallback is stated rather than assumed because JSON objects are
    # unordered by specification: a caller who omits `priority` and re-serialises
    # the document can change which system wins a conflict without changing a
    # single value. It stays the fallback because it is the only order available,
    # but a conflict report always names the source that produced each value, so
    # a reordered declaration is visible in the output rather than silent.
    priority = entry.get('priority', list(clean))
    if (not isinstance(priority, list) or len(priority) != len(clean)
            or set(priority) != set(clean)):
        raise ValueError(f'Entity {name} priority must list every source exactly once')
    if len(priority) > 1 and entry.get('priority') is None:
        priority = sorted(clean)
    attributes = sorted({attribute for source in clean.values() for attribute in source['map']})
    if not attributes:
        raise ValueError(f'Entity {name} declares no mapped attributes')
    # The effective per-attribute order: the entity's own table wins over the
    # shared one. Both are checked against the sources this entity actually has,
    # because a priority naming a source the entity does not declare is a typo
    # that would silently make the declaration a no-op.
    own = _priority_table(entry.get('source_priority'), f'{name}.source_priority')
    table = {**(shared or {}), **own}
    for attribute, order in sorted(table.items()):
        if attribute not in attributes:
            raise ValueError(f'Entity {name} declares priority for attribute '
                             f'{attribute!r}, which no source maps')
        for source_name in order:
            if source_name not in clean:
                raise ValueError(f'Entity {name} priority for {attribute!r} names '
                                 f'source {source_name!r}, which the entity does not declare')
    return {'identity': identity, 'sources': clean, 'priority': priority,
            'order': priority, 'attributes': attributes, 'source_priority': table}



def _source(entity, name, source):
    if not isinstance(source, dict):
        raise ValueError(f'Source {entity}.{name} must be an object')
    unknown = set(source) - {'tool', 'args', 'key', 'map'}
    if unknown:
        raise ValueError(f'Source {entity}.{name} has unsupported keys: {sorted(unknown)}')
    tool = source.get('tool')
    if tool not in SAFE_SOURCE_TOOLS:
        raise ValueError(f'Source {entity}.{name} tool must be one of {sorted(SAFE_SOURCE_TOOLS)}')
    args = source.get('args')
    if not isinstance(args, dict) or not args:
        raise ValueError(f'Source {entity}.{name} requires operator-declared args')
    key = _text(source.get('key'), f'{entity}.{name}.key', 64)
    mapping = source.get('map')
    if not isinstance(mapping, dict) or not mapping or len(mapping) > MAX_MAP:
        raise ValueError(f'Source {entity}.{name} requires 1..{MAX_MAP} mapped attributes')
    clean_map = {}
    for attribute, field in mapping.items():
        if not isinstance(attribute, str) or not ATTRIBUTE_RE.match(attribute):
            raise ValueError(f'Invalid attribute {attribute!r} in source {entity}.{name}')
        clean_map[attribute] = _text(field, f'{entity}.{name}.{attribute}', 64)
    _require_readable_fields(entity, name, tool, args, key, clean_map)
    return {'tool': tool, 'args': args, 'key': key, 'map': clean_map}


def _require(engine, tenant, agent, tool_name):
    """The graph never widens an agent's scope: the graph tool must be held too."""
    if tool_name not in engine.policy(tenant, agent).get('tools', []):
        raise Forbidden(f'{tool_name} not permitted for agent')


def _require_readable_fields(entity, name, tool, args, key, mapping):
    """Fail loudly when a declared field cannot possibly be returned.

    ``connectors.read`` reads only an approved column allowlist, so a key or a
    mapped field outside it can never produce a value. Silently returning an
    empty attribute would look like "the system has no such field", so the
    declaration is refused instead.
    """
    if tool == 'connectors.read':
        columns = args.get('columns')
        if not isinstance(columns, list):
            raise ValueError(f'Source {entity}.{name} connectors.read requires columns')
        missing = sorted({key, *mapping.values()} - set(columns))
        if missing:
            raise ValueError(f'Source {entity}.{name} reads columns that are not declared: {missing}')
    if tool == 'database.read' and isinstance(args.get('request_json'), str):
        try:
            request = json.loads(args['request_json'])
        except ValueError:
            raise ValueError(f'Source {entity}.{name} request_json is not valid JSON') from None
        if isinstance(request, dict) and request.get('operation') == 'read':
            columns = request.get('columns')
            if isinstance(columns, list):
                missing = sorted({key, *mapping.values()} - set(columns))
                if missing:
                    raise ValueError(
                        f'Source {entity}.{name} reads columns that are not requested: {missing}')


def entity_names(tenant) -> list:
    return sorted(graph_config(tenant)['entities'])


def declaration(tenant, entity) -> dict:
    declared = graph_config(tenant)['entities']
    entry = declared.get(entity)
    if entry is None:
        raise Forbidden(f'Business graph entity {entity!r} is not declared for this tenant')
    return {**entry, 'name': entity}


def _register_connection(source, tenant):
    """The connection a sheets source reaches through its register, or ``''``.

    Deliberately tolerant: an unreadable register declaration is not this
    function's error to raise, and the tool's own validation will report it with
    a better message. Returning ``''`` here only means "no connection could be
    attributed to this source", never "the source is authorised".
    """
    if source['tool'] not in {'sheets.rows', 'sheets.read'}:
        return ''
    register = source['args'].get('register')
    if not isinstance(register, str) or not register:
        return ''
    from .sheets import registers
    entry = registers(tenant).get(register)
    if not isinstance(entry, dict):
        return ''
    connection = entry.get('connection')
    return connection if isinstance(connection, str) else ''


def preflight(engine, tenant, agent, entry):
    """Prove authority for every declared source before any provider I/O.

    Without this a graph read could return three sources and one ``source_errors``
    row, and the caller could not tell an outage from a scope mistake. With it, a
    missing permission fails the whole read and no source is touched at all.
    """
    policy = engine.policy(tenant, agent)
    for name in entry['order']:
        source = entry['sources'][name]
        tool = source['tool']
        if tool not in policy.get('tools', []):
            raise Forbidden(f'Graph source {name} requires {tool}, which this agent does not hold')
        # A source reaches a connection one of two ways: it names one in its args
        # (connectors, database) or, for sheets, it names a register that carries
        # one. Resolving both here is what makes "scope is proven before any
        # provider I/O" true for every source rather than only the ones whose
        # arguments happen to mention a connection.
        connection = source['args'].get('connection') or _register_connection(source, tenant)
        if connection:
            allowed = policy.get('allowed_connections') or []
            if connection not in allowed:
                raise Forbidden(f'Graph source {name} connection is not allowed for this agent')
        try:
            engine.registry.get(tool).validate(source['args'])
        except (LookupError, ValueError) as error:
            raise ValueError(f'Graph source {name} arguments are invalid for {tool}: {error}') from None
    return policy


def _rows(engine, tenant, agent, source, step):
    """Invoke the ordinary read handler. No new transport, no new authority."""
    payload = engine.registry.get(source['tool']).handler(
        engine, tenant, agent, source['args'], step)
    if not isinstance(payload, dict):
        raise BusinessGraphError('Source payload was not an object')
    rows = payload.get('rows')
    if not isinstance(rows, list):
        raise BusinessGraphError('Source payload carried no row list')
    return rows
def _collect_all(engine, tenant, agent, entry, step):
    """Read every source exactly once and bucket observations by entity id.

    ``search`` and ``conflicts`` need one view per id. Calling ``_collect`` per id
    would re-read the whole source for every id, so a bounded scan of 100 ids
    against 4 sources would issue 400 provider GETs for one tool call, and every
    one of them would return the same rows. This reads each source once and
    groups by key instead, so cost is one read per source regardless of id count.

    Returns ``(by_id, status, errors, now)`` where ``by_id`` maps an entity id to
    that id's ``{attribute: [observation, ...]}``.
    """
    now = engine.clock()
    by_id, status, errors = {}, [], []
    for name in entry['order']:
        source = entry['sources'][name]
        try:
            rows = _rows(engine, tenant, agent, source, step)
        except Forbidden:
            raise
        except Exception as error:
            errors.append({'source': name, 'error': type(error).__name__})
            status.append({'source': name, 'rows': 0, 'matched': 0, 'read': False})
            continue
        key_field = source['key']
        seen, matched = set(), 0
        for row in rows[:MAX_SCAN]:
            if not isinstance(row, dict):
                continue
            raw_key = row.get(key_field)
            if raw_key is None or str(raw_key).strip() == '':
                continue
            identifier = str(raw_key)
            if identifier in seen:
                continue
            seen.add(identifier)
            matched += 1
            bucket = by_id.setdefault(identifier, {})
            for attribute, field in source['map'].items():
                value = row.get(field)
                if value is None or value == '':
                    continue
                bucket.setdefault(attribute, []).append(
                    {'source': name, 'value': _cell(value), 'observed': now})
        status.append({'source': name, 'rows': len(rows), 'matched': matched, 'read': True})
    return by_id, status, errors, now


def _view_from(entry, entity_id, observations, conflict_policy, policy, agent,
               status, errors, now):
    """Build one entity view from already-collected observations."""
    return _view(entry, entity_id, observations, [], status, errors, now,
                 conflict_policy, policy, agent)


def _collect(engine, tenant, agent, entry, step, entity_id=None):
    """Read every source once and return observations plus per-source status."""
    now = engine.clock()
    observations, status, errors = {}, [], []
    for name in entry['order']:
        source = entry['sources'][name]
        try:
            rows = _rows(engine, tenant, agent, source, step)
        except Forbidden:
            raise
        except Exception as error:
            # A provider failure is named, never silently rendered as "no value".
            errors.append({'source': name, 'error': type(error).__name__})
            status.append({'source': name, 'rows': 0, 'matched': 0, 'read': False})
            continue
        key_field = source['key']
        matched, seen = 0, set()
        for row in rows[:MAX_SCAN]:
            if not isinstance(row, dict):
                continue
            raw_key = row.get(key_field)
            if raw_key is None or str(raw_key).strip() == '':
                continue
            identifier = str(raw_key)
            if entity_id is not None and identifier != entity_id:
                continue
            if identifier in seen:
                continue
            seen.add(identifier)
            matched += 1
            for attribute, field in source['map'].items():
                value = row.get(field)
                if value is None or value == '':
                    continue
                observations.setdefault(attribute, []).append(
                    {'source': name, 'value': _cell(value), 'observed': now})
        status.append({'source': name, 'rows': len(rows), 'matched': matched, 'read': True})
    return observations, status, errors, now


def _view(entry, entity_id, observations, conflicts, status, errors, now, conflict_policy,
          policy, agent):
    attributes = {}
    for attribute in sorted(observations):
        values = observations[attribute]
        distinct = {canonical(item['value']) for item in values}
        selected = {'value': values[0]['value'], 'source': values[0]['source']}
        entry_out = {'values': values, 'conflict': len(distinct) > 1, 'selected': selected}
        if entry_out['conflict']:
            chosen = None
            if conflict_policy == 'primary_wins':
                chosen = next(item for item in values
                              if item['source'] == _primary(entry, attribute, values))
                entry_out['selected'] = {'value': chosen['value'], 'source': chosen['source']}
            else:
                # report: pick nothing. A silently chosen number is how a
                # finance clerk ends up arguing with a salesperson.
                entry_out['selected'] = None
            conflicts.append({'attribute': attribute,
                              'values': [item['value'] for item in values],
                              'sources': [item['source'] for item in values],
                              'resolution': conflict_policy,
                              'selected_source': chosen['source'] if chosen else None})
        attributes[attribute] = entry_out
    return {'entity': entry.get('name', ''), 'identity': entry['identity'], 'id': entity_id,
            'attributes': attributes, 'conflicts': conflicts,
            'conflict_policy': conflict_policy, 'sources': status,
            'source_errors': errors, 'complete': not errors, 'observed': now,
            'authority': {'agent': agent, 'ladder': policy.get('ladder', '')}}


def _primary(entry, attribute, values):
    """First source in the operator's declared order that supplied the attribute.

    The order is the per-attribute table when the operator declared one for this
    attribute (PRD 6.3, where ``price`` may come from MoySklad while ``tax_category``
    comes from 1C), and the whole-entity ``priority`` otherwise. Falling back to
    the entity order rather than to declaration order is what keeps a partially
    declared table from quietly reordering every other attribute.
    """
    present = {item['source'] for item in values}
    order = entry['source_priority'].get(attribute) or entry['priority']
    for name in order:
        if name in present:
            return name
    return values[0]['source']


def resolve(engine, tenant, agent, entity, entity_id, step):
    """One entity view: observations, selection and reported conflicts."""
    entry = declaration(tenant, entity)
    policy = preflight(engine, tenant, agent, entry)
    conflict_policy = graph_config(tenant)['conflict_policy']
    observations, status, errors, now = _collect(
        engine, tenant, agent, entry, step, entity_id=entity_id)
    return _view(entry, entity_id, observations, [], status, errors, now,
                 conflict_policy, policy, agent)
def identifiers(engine, tenant, agent, entity, step, limit=MAX_MATCHES, collected=None):
    """Bounded id set seen across the entity's sources, in priority order.

    ``collected`` lets a caller that has already read the sources pass its
    ``_collect_all`` result in, so one tool call reads each source once instead of
    once for the id list and again for the per-id views.

    The third element says whether the list was **cut short**, not whether it
    happens to be full. Those are different questions: a tenant with exactly
    ``limit`` ids is answered completely, and reporting ``truncated=True`` for it
    tells the operator to narrow a query that is already as narrow as it can be.
    The only honest evidence of truncation is that one more id was available and
    the limit refused it, so the flag is set at the moment of refusal rather than
    read back off the length afterwards.
    """
    entry = declaration(tenant, entity)
    preflight(engine, tenant, agent, entry)
    limit = bounded(limit, 'limit', 1, MAX_MATCHES)
    if collected is not None:
        by_id, _, _, _ = collected
        ordered, seen = [], set()
        for identifier in by_id:
            if identifier in seen:
                continue
            if len(ordered) >= limit:
                # There is at least one id left to take, so this really is a cut.
                return ordered, [], True
            seen.add(identifier)
            ordered.append(identifier)
        return ordered, [], False
    found, seen, errors = [], set(), []
    for name in entry['order']:
        source = entry['sources'][name]
        try:
            rows = _rows(engine, tenant, agent, source, step)
        except Forbidden:
            raise
        except Exception as error:
            errors.append({'source': name, 'error': type(error).__name__})
            continue
        for row in rows[:MAX_SCAN]:
            if not isinstance(row, dict):
                continue
            raw = row.get(source['key'])
            if raw is None or str(raw).strip() == '':
                continue
            identifier = str(raw)
            if identifier in seen:
                continue
            if len(found) >= limit:
                return found, errors, True
            seen.add(identifier)
            found.append(identifier)
    return found, errors, False


def search(engine, tenant, agent, entity, attribute, equals, step, limit=MAX_MATCHES):
    """Ids whose declared attribute equals a value. Bounded, priority ordered."""
    entry = declaration(tenant, entity)
    if attribute not in entry['attributes']:
        raise Forbidden(f'Attribute {attribute!r} is not declared on entity {entity!r}')
    limit = bounded(limit, 'limit', 1, MAX_MATCHES)
    policy = preflight(engine, tenant, agent, entry)
    conflict_policy = graph_config(tenant)['conflict_policy']
    # One read per source, then every declared id is matched in memory. Reading the
    # sources once per id would multiply provider cost by the id count for no gain.
    by_id, status, errors, now = _collect_all(engine, tenant, agent, entry, step)
    target = canonical(equals)
    ids = identifiers(engine, tenant, agent, entity, step, MAX_MATCHES,
                      collected=(by_id, status, errors, now))[0]
    matches = []
    cut = False
    for entity_id in ids:
        view = _view_from(entry, entity_id, by_id.get(entity_id, {}), conflict_policy,
                          policy, agent, status, errors, now)
        attribute_view = view['attributes'].get(attribute)
        if not attribute_view:
            continue
        if not any(canonical(item['value']) == target for item in attribute_view['values']):
            continue
        if len(matches) >= limit:
            # A further match exists and the limit refused it: that, and only that,
            # is evidence of a cut. Deriving `truncated` from `len(matches) >= limit`
            # afterwards would report a cut for a tenant that genuinely has exactly
            # `limit` matches, sending the operator to narrow a query that is already
            # as narrow as it can be. The scan continues past a full list so that a
            # later match can be seen; `by_id` is already in memory, so the extra
            # work is bounded by MAX_MATCHES with no additional provider read.
            cut = True
            break
        chosen = attribute_view['selected'] or attribute_view['values'][0]
        matches.append({'id': entity_id, 'attribute': attribute,
                        'value': chosen['value'], 'source': chosen['source'],
                        'conflict': attribute_view['conflict']})
    # A search that could not read a source is not a search that found nothing.
    return {'entity': entity, 'attribute': attribute, 'matches': matches,
            'truncated': cut, 'complete': not errors,
            'source_errors': errors}


def conflicts(engine, tenant, agent, entity, step, limit=20):
    """Entities where two systems disagree, reported rather than resolved."""
    limit = bounded(limit, 'limit', 1, 50)
    entry = declaration(tenant, entity)
    policy = preflight(engine, tenant, agent, entry)
    conflict_policy = graph_config(tenant)['conflict_policy']
    by_id, status, errors, now = _collect_all(engine, tenant, agent, entry, step)
    ids = identifiers(engine, tenant, agent, entity, step, MAX_MATCHES,
                      collected=(by_id, status, errors, now))[0]
    found = []
    cut = False
    for entity_id in ids:
        view = _view_from(entry, entity_id, by_id.get(entity_id, {}), conflict_policy,
                          policy, agent, status, errors, now)
        for conflict in view['conflicts']:
            if len(found) >= limit:
                # A further conflict exists and the limit refused it. `limit` always
                # reaches here as a caller-supplied bound (default 20, maximum 50)
                # while MAX_MATCHES ids may still hold conflicts, so a cut is the
                # ordinary outcome and must be distinguished from an exact fit.
                cut = True
                break
            found.append({'id': entity_id, **conflict})
        if cut:
            break
    return {'entity': entity, 'conflicts': found, 'scanned': len(ids),
            'truncated': cut, 'complete': not errors,
            'source_errors': errors}


def explain(engine, tenant, agent, entity, entity_id, attribute, step):
    """Every observation behind one attribute, and why one was or was not chosen."""
    entry = declaration(tenant, entity)
    if attribute not in entry['attributes']:
        raise Forbidden(f'Attribute {attribute!r} is not declared on entity {entity!r}')
    view = resolve(engine, tenant, agent, entity, entity_id, step)
    declared_by = [name for name in entry['priority']
                   if attribute in entry['sources'][name]['map']]
    observed_by = [item['source'] for item in
                   view['attributes'].get(attribute, {}).get('values', [])]
    return {'entity': entity, 'id': entity_id, 'attribute': attribute,
            **view['attributes'].get(attribute, {'values': [], 'selected': None,
                                                 'conflict': False}),
            'declared_by': declared_by, 'observed_by': observed_by,
            'missing_from': [name for name in declared_by if name not in observed_by],
            'priority': entry['priority'],
            # The order that decides `selected` for THIS attribute, and where it
            # came from. A caller asking "why this number" must be able to see
            # that the answer was an operator declaration, not a preference the
            # platform invented, and must be able to see which declaration won.
            'attribute_priority': entry['source_priority'].get(
                attribute, entry['priority']),
            'priority_source': ('attribute' if attribute in entry['source_priority']
                                else 'entity'),
            'resolution': view['conflict_policy'],
            'complete': view['complete'], 'source_errors': view['source_errors']}


def bounded(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value
def timeline(engine, tenant, agent, entity, entity_id, step, limit=50):
    """Cross-system activity for one entity, in the order each source returned it.

    Deliberately **not** claimed to be chronological. Sorting provider text
    without parsing it would order ``10.01.2026`` before ``09.02.2026`` and look
    authoritative while being wrong, so each source keeps its own returned order
    and the caller sees ``position`` plus the source that produced the row.
    """
    entry = declaration(tenant, entity)
    policy = preflight(engine, tenant, agent, entry)
    limit = bounded(limit, 'limit', 1, MAX_SCAN)
    events, errors = [], []
    cut = False
    for name in entry['order']:
        source = entry['sources'][name]
        try:
            rows = _rows(engine, tenant, agent, source, step)
        except Forbidden:
            raise
        except Exception as error:
            errors.append({'source': name, 'error': type(error).__name__})
            continue
        position = 0
        for row in rows[:MAX_SCAN]:
            if not isinstance(row, dict):
                continue
            raw = row.get(source['key'])
            if raw is None or str(raw).strip() == '':
                continue
            if str(raw) != entity_id:
                continue
            if len(events) >= limit:
                # One more event was found and the limit refused it. Comparing the
                # final length against `limit` cannot tell this apart from an entity
                # that genuinely has exactly `limit` events, and the difference
                # matters: the first says "ask again with a bigger limit", the
                # second says "this is everything".
                cut = True
                break
            values = {attribute: _cell(row[field])
                      for attribute, field in source['map'].items()
                      if row.get(field) not in (None, '')}
            events.append({'source': name, 'position': position, 'values': values})
            position += 1
        if cut:
            break
    return {'entity': entity, 'id': entity_id, 'events': events, 'order': 'provider',
            'truncated': cut, 'complete': not errors,
            'source_errors': errors, 'observed': engine.clock(),
            'authority': {'agent': agent, 'ladder': policy.get('ladder', '')}}
# --------------------------------------------------------------------- tool layer

def tool_entities(engine, tenant, agent, args, step):
    """The declared graph shape. Connections, ranges and columns are never returned."""
    _require(engine, tenant, agent, 'graph.entities')
    declared = graph_config(tenant)
    return {'conflict_policy': declared['conflict_policy'],
            # The per-attribute priorities are echoed here for the same reason the
            # rest of the shape is: this tool's job is to show the operator what
            # the platform actually read. `source_priority` was whitelisted and
            # then silently dropped for several versions precisely because nothing
            # displayed it; returning it is what makes a mis-typed declaration
            # visible instead of merely inert.
            'source_priority': {attribute: list(order) for attribute, order
                                in sorted(declared['source_priority'].items())},
            'entities': [{'entity': name, 'identity': entry['identity'],
                          'attributes': entry['attributes'],
                          'sources': [{'source': source,
                                       'tool': entry['sources'][source]['tool']}
                                      for source in entry['order']],
                          'priority': entry['priority'],
                          'source_priority': {attribute: list(order)
                                              for attribute, order
                                              in sorted(entry['source_priority'].items())}}
                         for name, entry in sorted(declared['entities'].items())]}


def tool_entity(engine, tenant, agent, args, step):
    _require(engine, tenant, agent, 'graph.entity')
    return resolve(engine, tenant, agent, args['entity'], args['id'], step)


def tool_search(engine, tenant, agent, args, step):
    _require(engine, tenant, agent, 'graph.search')
    return search(engine, tenant, agent, args['entity'], args['attribute'],
                  args['equals'], step, args.get('limit', MAX_MATCHES))


def tool_timeline(engine, tenant, agent, args, step):
    _require(engine, tenant, agent, 'graph.timeline')
    return timeline(engine, tenant, agent, args['entity'], args['id'], step,
                    args.get('limit', 50))


def tool_conflicts(engine, tenant, agent, args, step):
    _require(engine, tenant, agent, 'graph.conflicts')
    return conflicts(engine, tenant, agent, args['entity'], step, args.get('limit', 20))


def tool_explain(engine, tenant, agent, args, step):
    _require(engine, tenant, agent, 'graph.explain')
    return explain(engine, tenant, agent, args['entity'], args['id'], args['attribute'], step)


def register_graph_tools(registry):
    from .tools import Tool, obj, string, register_once
    entity_args = {'entity': string(64), 'id': string(128)}
    register_once(registry, [
        Tool('graph.entities', 'read', obj({}), tool_entities),
        Tool('graph.entity', 'read', obj(entity_args), tool_entity),
        Tool('graph.timeline', 'read', obj({
            **entity_args,
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_SCAN},
        }), tool_timeline),
        Tool('graph.explain', 'read', obj({
            **entity_args, 'attribute': string(64),
        }), tool_explain),
        Tool('graph.search', 'read', obj({
            'entity': string(64), 'attribute': string(64), 'equals': string(200),
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_MATCHES},
        }, ['entity', 'attribute', 'equals']), tool_search),
        Tool('graph.conflicts', 'read', obj({
            'entity': string(64),
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
        }, ['entity']), tool_conflicts),
    ])