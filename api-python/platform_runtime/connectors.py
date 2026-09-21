"""Operator-provisioned, tenant-scoped connectors. No arbitrary SQL, DSN or URL input.

SQLite is locally tested; PostgreSQL is contract-tested and requires live acceptance.
The DB must be mounted read-only by deployment. External DB isolation is its own boundary.
"""
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from .engine import encode, Forbidden
from .tools import config
from .connector_contract import (SUPPORTED_LOCAL_DRIVERS, _string, descriptor,
                                 public_descriptor)
from .connector_authority import require_read_access

IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('Invalid identifier')
    return '"' + value + '"'


def authorize_read_connection(tenant, name, *, agent=None):
    try:
        # The same bounded-identifier rules `connector_contract` applies to a
        # connection id: no surrounding whitespace, no control characters, no DEL.
        # A length-only check here let a name with a leading space or a DEL character
        # through while `_string` and `database.contract.text` refused the same
        # value -- the split fazza 30 found between `usage_budget.bounded` and
        # `contract.text`, fixed the same way: one rule, not two.
        name = _string(name, 'connection', 128)
    except ValueError:
        raise ValueError('Invalid connection') from None
    try:
        tenant_config = config(tenant)
    except (OSError, ValueError, RuntimeError):
        raise Forbidden('Connection configuration unavailable') from None
    if not isinstance(tenant_config, dict):
        raise Forbidden('Connection configuration unavailable')
    entries = tenant_config.get('connections', {})
    if not isinstance(entries, dict):
        raise ValueError('Invalid connection catalog')
    item = entries.get(name)
    require_read_access(item, agent=agent)
    return item


def connection(tenant, name, *, agent=None):
    item = authorize_read_connection(tenant, name, agent=agent)
    if item.get('enabled', True) is not True:
        raise Forbidden('Connection unavailable')
    if item.get('driver') != 'sqlite_readonly':
        raise ValueError('Connector driver is not implemented')
    tables = item.get('tables')
    if not isinstance(tables, dict) or not tables:
        raise ValueError('Explicit table and column allowlists required')
    for table, columns in tables.items():
        identifier(table)
        if not isinstance(columns, list) or not columns or len(columns) > 40:
            raise ValueError('Invalid column allowlist')
        for column in columns:
            identifier(column)
    return item


def describe(tenant):
    """Capability metadata only. Unsupported drivers remain visible but never healthy."""
    entries = config(tenant).get('connections', {})
    out = []
    for name, raw in entries.items():
        if not isinstance(raw, dict) or raw.get('enabled', True) is False:
            continue
        from .database.contract import BACKENDS
        if raw.get('driver') in BACKENDS:
            from .database.gateway import describe_managed
            item = describe_managed(name, raw)
            out.append(item)
            continue
        from .crm.crm_contract import CRM_DRIVERS
        if raw.get('driver') in CRM_DRIVERS:
            from .crm.crm_gateway import describe_crm
            item = describe_crm(name, raw)
            out.append(item)
            continue

        d = descriptor(name, raw, live_drivers=SUPPORTED_LOCAL_DRIVERS)
        if d.status == 'disabled':
            continue
        item = public_descriptor(d)
        if raw.get('driver') == 'sqlite_readonly' and isinstance(raw.get('tables'), dict):
            item['mode'] = 'read_only'
            item['tables'] = raw['tables']
        elif raw.get('driver') == 'postgres_readonly':
            item['mode'] = 'read_only'
        else:
            item['mode'] = 'adapter_required'
        out.append(item)
    return out


def database_path(item, internal_path=None):
    roots = json.loads(os.environ.get('PLATFORM_DB_ROOTS', '[]'))
    if not isinstance(roots, list) or not roots or any(not isinstance(r, str) or not Path(r).is_absolute() for r in roots):
        raise ValueError('Explicit absolute database mount roots required')
    raw = item.get('path', '')
    if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
        raise ValueError('Absolute provisioned database path required')
    resolved = Path(raw).resolve(strict=True)
    if not resolved.is_file() or not any(resolved.is_relative_to(Path(r).resolve(strict=True)) for r in roots):
        raise Forbidden('Database outside approved mounts')
    if internal_path and (resolved == Path(internal_path).resolve()
                          or (Path(internal_path).exists() and resolved.samefile(internal_path))):
        raise Forbidden('Platform database cannot be a customer connection')
    return resolved


def read_rows(tenant, request, internal_path=None, *, agent=None):
    if not isinstance(request, dict) or set(request) - {'connection', 'table', 'columns', 'limit', 'where'}:
        raise ValueError('Invalid query fields')
    item = connection(tenant, request.get('connection'), agent=agent)
    table = request.get('table')
    approved = item['tables'].get(table)
    columns = request.get('columns')
    if not approved or not isinstance(columns, list) or not 1 <= len(columns) <= 40:
        raise Forbidden('Explicit approved columns required')
    if len(set(columns)) != len(columns) or any(col not in approved for col in columns):
        raise Forbidden('Column not permitted')
    limit = request.get('limit', 50)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError('Limit must be 1..100')
    where = request.get('where', {})
    if not isinstance(where, dict) or set(where) not in (set(), {'column', 'equals'}):
        raise ValueError('Only an exact-match filter is supported')
    parameters = []
    sql = 'SELECT ' + ','.join(identifier(col) for col in columns) + ' FROM ' + identifier(table)
    if where:
        if where['column'] not in approved or not isinstance(where['equals'], str) or len(where['equals']) > 1000:
            raise Forbidden('Filter not permitted')
        sql += ' WHERE ' + identifier(where['column']) + ' = ?'
        parameters.append(where['equals'])
    sql += ' LIMIT ?'
    parameters.append(limit)
    path = database_path(item, internal_path)
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 100_000)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16_000)
        # Views, virtual tables, functions, schema writes, ATTACH and PRAGMA are denied.
        row = db.execute('SELECT type,sql FROM sqlite_master WHERE name=?', (table,)).fetchone()
        if not row or row[0] != 'table' or (row[1] or '').lstrip().upper().startswith('CREATE VIRTUAL'):
            raise Forbidden('Only base tables are supported')
        def authorize(action, arg1, arg2, database, source):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and database == 'main' and arg1 == table and arg2 in approved and source is None:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        db.set_authorizer(authorize)
        deadline = time.monotonic() + 2
        calls = 0
        def progress():
            nonlocal calls
            calls += 1
            return int(calls >= 1000 or time.monotonic() >= deadline)
        db.set_progress_handler(progress, 1000)
        rows = []
        total = 0
        for record in db.execute(sql, parameters):
            value = {column: ('[binary omitted]' if isinstance(cell, bytes) else cell)
                     for column, cell in zip(columns, record)}
            size = len(encode(value).encode('utf-8'))
            if size > 16_000 or total + size > 80_000:
                raise ValueError('Result exceeds data minimization limit')
            total += size
            rows.append(value)
        return {'connection': request['connection'], 'table': table, 'rows': rows,
                'returned': len(rows), 'limit': limit, 'read_only': True}
    finally:
        db.close()


def tool_read(engine, tenant, agent, args, step):
    # Repeat agent scope at dispatch, including for direct internal invocations.
    if not isinstance(args, dict) or not isinstance(args.get('connection'), str):
        raise ValueError('Invalid connection request')
    if args['connection'] not in engine.policy(tenant, agent).get('allowed_connections', []):
        raise Forbidden('Connection not allowed for this agent')
    raw = authorize_read_connection(tenant, args['connection'], agent=agent)
    if raw.get('driver') == 'postgres_readonly':
        return read_postgres_rows(tenant, args, agent=agent)
    return read_rows(tenant, args, engine.path, agent=agent)


def _postgres_config(item):
    from .postgres_connector import validate_config
    return validate_config(item)


def read_postgres_rows(tenant, request, *, agent=None):
    from .postgres_connector import read
    if not isinstance(request,dict) or not isinstance(request.get('connection'),str):raise ValueError('Invalid connection')
    raw=authorize_read_connection(tenant,request['connection'],agent=agent)
    return read(tenant,raw,request,agent=agent)


def probe_read_connection(engine, tenant, name, *, actor, agent=None):
    """Owner/integrator-only single-table probe; never return sampled customer data.

    Scoped connections require a matching explicitly selected agent and its pack
    permission. This is a read probe, not a persisted lifecycle/health transition.
    Caller must also validate its session, as the engine only knows actor authority.
    """
    if not isinstance(actor, str) or not actor:
        raise Forbidden('Probe actor required')
    if agent is not None:
        try:
            agent = _string(agent, 'probe agent', 128)
        except ValueError:
            raise ValueError('Invalid probe agent') from None

    def authorized():
        with engine.read() as c:
            engine.require_authority(c, tenant, 'web', actor, ('owner', 'integrator'))
            engine.require_active(c, tenant)
        raw = authorize_read_connection(tenant, name, agent=agent)
        if agent is not None:
            policy = engine.policy(tenant, agent)
            if ('connectors.read' not in policy.get('tools', [])
                    or name not in policy.get('allowed_connections', [])):
                raise Forbidden('Probe agent not allowed for this connection')
        return raw

    raw = authorized()
    tables = raw.get('tables')
    if not isinstance(tables, dict) or not tables:
        raise ValueError('Connector table allowlist missing')
    table = next(iter(tables))
    columns = tables[table]
    if not isinstance(columns, list) or not columns:
        raise ValueError('Connector column allowlist missing')
    sample = {'connection': name, 'table': table, 'columns': columns[:1], 'limit': 1}
    raw = authorized()  # Repeat actor, freeze, lifecycle and pack checks before IO.
    if raw['driver'] == 'sqlite_readonly':
        read_rows(tenant, sample, engine.path, agent=agent)
    elif raw['driver'] == 'postgres_readonly':
        read_postgres_rows(tenant, sample, agent=agent)
    else:
        raise Forbidden('Read adapter unavailable')
    info = descriptor(name, raw, live_drivers=SUPPORTED_LOCAL_DRIVERS)
    return {'ok': True, 'connection': {**public_descriptor(info), 'status': 'probe_succeeded'},
            'verified_at': engine.clock(), 'scope': 'single_table_read_probe', 'persisted': False}
