"""Single-resource adapters. No retry, bulk mutation, DDL or arbitrary queries.

Network code is source/contract tested until real database acceptance is run.
Deployment must provision least-privilege roles and enforce network isolation.
"""
import importlib
import ipaddress
import os
import re
import sqlite3
import time
from ..engine import Conflict, Forbidden, encode
from ..postgres_connector import cell
from .contract import name, mutations, selector
from .sql import compile_sql


def _rows(records, fields):
    out, total = [], 0
    for record in records:
        row = {field: cell(value) for field, value in zip(fields, record)}
        size = len(encode(row).encode('utf-8'))
        if size > 16000 or total + size > 80000:
            raise ValueError('Database result exceeds byte limits')
        total += size
        out.append(row)
    return {'rows': out, 'returned': len(out), 'read_only': True}


def _receipt(request):
    return {'resource': request['resource'], 'operation': request['operation'],
            'affected': 1, 'key': request['key'],
            'version': 1 if request['operation'] == 'insert' else request['expected_version'] + 1}


def _credential(raw):
    ref = raw.get('password_env')
    if not isinstance(ref, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', ref):
        raise ValueError('Credential environment reference required')
    if ref.startswith('DSEC_'):
        # HTTPS-only secret placeholders must never enter native database protocols.
        raise ValueError('HTTPS secret placeholder is not a database password')
    value = os.environ.get(ref, '')
    if not value:
        raise RuntimeError('Database credential unavailable')
    return value


MAX_CA_PATH_CHARS = 4096


def _validate_mysql_ca(raw):
    """Shape of the optional MySQL CA declaration; the file itself is read at connect.

    A private CA (RDS, Azure, a customer's own) is named by an environment variable
    holding a file path (``ssl_ca_env``) or by an absolute path (``ssl_ca``) --
    never by inline PEM, which would put certificate material into tenant config.
    ``tls_verify`` exists only to be stated: verification cannot be switched off.
    """
    if 'tls_verify' in raw and raw['tls_verify'] is not True:
        raise ValueError('Unverified MySQL TLS is refused; declare ssl_ca or ssl_ca_env '
                         'for a private certificate authority')
    if 'ssl_ca' in raw and 'ssl_ca_env' in raw:
        raise ValueError('Ambiguous MySQL CA: declare ssl_ca or ssl_ca_env, not both')
    if 'ssl_ca_env' in raw:
        ref = raw['ssl_ca_env']
        if not isinstance(ref, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', ref) or ref.startswith('DSEC_'):
            raise ValueError('ssl_ca_env must name an environment variable holding a CA file path')
    if 'ssl_ca' in raw:
        _ca_path(raw['ssl_ca'])


def _ca_path(value):
    if (not isinstance(value, str) or not value or len(value) > MAX_CA_PATH_CHARS
            or '-----BEGIN' in value or '\n' in value or '\x00' in value
            or not os.path.isabs(value)):
        raise ValueError('MySQL CA must be an absolute file path, never an inline certificate')
    return value


def validate_endpoint(raw):
    host, allowed = raw.get('host'), raw.get('allowed_hosts')
    if (not isinstance(host, str) or not 1 <= len(host) <= 253 or not isinstance(allowed, list)
            or not 1 <= len(allowed) <= 100 or any(not isinstance(v, str) for v in allowed)
            or host not in allowed):
        raise ValueError('Explicit database host allowlist required')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
            raise ValueError('Invalid database hostname') from None
    port = raw.get('port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Explicit valid database port required')
    if raw['driver'] == 'redis_managed':
        if type(raw.get('database')) is not int or not 0 <= raw['database'] <= 15:
            raise ValueError('Redis logical database must be 0..15')
        name(raw.get('namespace'))
        if raw.get('tls') is not True:
            raise ValueError('Verified Redis TLS required')
    else:
        name(raw.get('database'))
    name(raw.get('user'))
    ref = raw.get('password_env')
    if not isinstance(ref, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', ref) or ref.startswith('DSEC_'):
        raise ValueError('Native database credential reference required')
    if raw['driver'] == 'postgres_managed':
        if raw.get('sslmode') != 'verify-full':
            raise ValueError('Verified PostgreSQL TLS required')
        schema = name(raw.get('schema'))
        if schema.startswith('pg_') or schema == 'information_schema':
            raise Forbidden('System database schema denied')
    elif raw['driver'] in {'mysql_managed', 'mariadb_managed'}:
        if raw.get('tls') is not True:
            raise ValueError('Verified MySQL TLS required')
        _validate_mysql_ca(raw)
        schema = name(raw.get('schema'))
        if schema != raw['database'] or schema.lower() in {'mysql', 'sys', 'information_schema', 'performance_schema'}:
            raise Forbidden('Explicit ordinary MySQL database/schema required')
    elif raw['driver'] == 'sqlserver_managed':
        if raw.get('tls') is not True:
            raise ValueError('Verified SQL Server TLS required')
        schema = name(raw.get('schema'))
        if raw['database'].lower() in {'master', 'model', 'msdb', 'tempdb'} or schema.lower() in {'sys', 'information_schema'}:
            raise Forbidden('System SQL Server database or schema denied')
        if raw.get('odbc_driver') != 'ODBC Driver 18 for SQL Server':
            raise ValueError('Microsoft ODBC Driver 18 required')
    elif raw['driver'] == 'oracle_managed':
        if raw.get('tls') is not True:
            raise ValueError('Verified Oracle TCPS required')
        schema = name(raw.get('schema'))
        service = raw.get('service_name')
        if not isinstance(service, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,252}', service):
            raise ValueError('Explicit bounded Oracle service_name required')
        if schema.upper() in {'SYS', 'SYSTEM', 'XDB', 'MDSYS', 'CTXSYS', 'WMSYS', 'OUTLN', 'DBSNMP', 'AUDSYS', 'ORDSYS'}:
            raise Forbidden('System Oracle schema denied')
    elif raw['driver'] == 'mongodb_managed':
        if raw.get('tls') is not True:
            raise ValueError('Verified MongoDB TLS required')
        if raw['database'] in {'admin', 'local', 'config'}:
            raise Forbidden('System MongoDB database denied')
    return raw


def _sqlite_schema(db, raw, request):
    table = request['resource']
    quoted = '"' + name(table) + '"'
    row = db.execute('SELECT type,sql FROM main.sqlite_master WHERE name=?', (table,)).fetchone()
    if not row or row[0] != 'table' or (row[1] or '').lstrip().upper().startswith('CREATE VIRTUAL'):
        raise Forbidden('Only customer base tables are permitted')
    columns = db.execute('PRAGMA main.table_xinfo(' + quoted + ')').fetchall()
    spec = raw['resources'][table]
    needed = set(spec['read_fields']) | set(spec['insert_fields']) | set(spec['update_fields'])
    needed |= {spec['key_field'], spec['version_field']}
    if raw['isolation'] == 'tenant_column':
        needed.add(raw['tenant_column'])
    ordinary = {r[1] for r in columns if not r[6]}
    if not needed.issubset(ordinary):
        raise Forbidden('Configured fields missing or generated')
    if request['operation'] == 'read':
        return needed
    if db.execute('SELECT 1 FROM main.sqlite_master WHERE type=? AND tbl_name=? LIMIT 1',
                  ('trigger', table)).fetchone():
        raise Forbidden('Managed SQLite writes deny table triggers')
    if db.execute('PRAGMA main.foreign_key_list(' + quoted + ')').fetchone():
        raise Forbidden('Managed SQLite writes require isolated resources without foreign keys')
    unique = [{r[1] for r in columns if r[5]}]
    for index in db.execute('PRAGMA main.index_list(' + quoted + ')').fetchall():
        if index[2] and not index[4]:
            # Index names are engine metadata, still quote safely rather than interpolate raw.
            quoted_index = '"' + index[1].replace('"', '""') + '"'
            unique.append({r[2] for r in db.execute('PRAGMA main.index_info(' + quoted_index + ')')})
    key = {spec['key_field']}
    scoped = key | ({raw['tenant_column']} if raw['isolation'] == 'tenant_column' else set())
    if key not in unique and scoped not in unique:
        raise Forbidden('Database-enforced unique record key required')
    return needed


def sqlite_execute(raw, tenant, request, internal_path):
    from ..connectors import database_path
    path = database_path(raw, internal_path)
    write = request['operation'] != 'read'
    sql, params = compile_sql(raw, tenant, request)
    mode = 'rw' if write else 'ro'
    db = sqlite3.connect(path.as_uri() + '?mode=' + mode, uri=True, timeout=1, isolation_level=None)
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA foreign_keys=ON')
        if not write:
            db.execute('PRAGMA query_only=ON')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 100000)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16000)
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        allowed = _sqlite_schema(db, raw, request)
        spec = raw['resources'][request['resource']]
        mutable = set(spec['update_fields']) | {spec['version_field']}
        def authorize(action, arg1, arg2, database, source):
            if source is not None:
                return sqlite3.SQLITE_DENY
            if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION}:
                return sqlite3.SQLITE_OK
            if database != 'main' or arg1 != request['resource']:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ and arg2 in allowed:
                return sqlite3.SQLITE_OK
            if write and request['operation'] == 'insert' and action == sqlite3.SQLITE_INSERT:
                return sqlite3.SQLITE_OK
            if write and request['operation'] == 'update' and action == sqlite3.SQLITE_UPDATE and arg2 in mutable:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        db.set_authorizer(authorize)
        deadline = time.monotonic() + 2
        ticks = 0
        def progress():
            nonlocal ticks
            ticks += 1
            return int(ticks > 1000 or time.monotonic() > deadline)
        db.set_progress_handler(progress, 1000)
        cursor = db.execute(sql, params)
        if write:
            if cursor.rowcount != 1:
                raise Conflict('Database record missing or version changed')
            result = _receipt(request)
        else:
            result = _rows(cursor, request['fields'])
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def postgres_execute(raw, tenant, request, internal_path=None):
    validate_endpoint(raw)
    driver = importlib.import_module('psycopg')
    sql, params = compile_sql(raw, tenant, request)
    write = request['operation'] != 'read'
    options = '-c statement_timeout=2000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=5000'
    if not write:
        options += ' -c default_transaction_read_only=on'
    conn = driver.connect(host=raw['host'], port=raw['port'], dbname=raw['database'], user=raw['user'],
                          password=_credential(raw), sslmode='verify-full', connect_timeout=5, options=options)
    try:
        with conn:
            with conn.cursor() as cur:
                if not write:
                    cur.execute('SET TRANSACTION READ ONLY')
                else:
                    target = '"' + name(raw['schema']) + '"."' + name(request['resource']) + '"'
                    cur.execute('LOCK TABLE ' + target + ' IN ROW EXCLUSIVE MODE')
                cur.execute('SELECT c.oid,c.relkind FROM pg_catalog.pg_class c '
                            'JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace '
                            'WHERE n.nspname=%s AND c.relname=%s', (raw['schema'], request['resource']))
                table = cur.fetchone()
                if not table or table[1] != 'r':
                    raise Forbidden('Only ordinary PostgreSQL base tables supported')
                if write:
                    cur.execute("SELECT 1 FROM pg_catalog.pg_trigger WHERE tgrelid=%s AND tgenabled <> 'D' LIMIT 1", (table[0],))
                    if cur.fetchone():
                        raise Forbidden('Managed PostgreSQL writes deny enabled triggers')
                    cur.execute('SELECT array_agg(a.attname ORDER BY ord.n) FROM pg_catalog.pg_index i '
                                'CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS ord(attnum,n) '
                                'JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ord.attnum '
                                'WHERE i.indrelid=%s AND i.indisunique AND i.indisvalid '
                                'AND i.indpred IS NULL AND i.indexprs IS NULL AND ord.n<=i.indnkeyatts '
                                'GROUP BY i.indexrelid', (table[0],))
                    unique = [set(r[0]) for r in cur.fetchall()]
                    spec = raw['resources'][request['resource']]
                    key = {spec['key_field']}
                    scoped = key | ({raw['tenant_column']} if raw['isolation'] == 'tenant_column' else set())
                    if key not in unique and scoped not in unique:
                        raise Forbidden('Database-enforced unique record key required')
                    cur.execute(sql, params)
                    if cur.rowcount != 1:
                        raise Conflict('Database record missing or version changed')
                    return _receipt(request)
            with conn.cursor(name='managed_database_read') as cur:
                cur.execute(sql, params)
                def records():
                    for _ in range(request['limit']):
                        row = cur.fetchone()
                        if row is None:
                            break
                        yield row
                return _rows(records(), request['fields'])
    finally:
        conn.close()


def mongo_selector(raw, tenant, request):
    filters = selector(raw, tenant, request)
    guards = []
    for field, value in filters.items():
        # Mongo's ordinary equality also matches array elements. Exact scalar
        # guards prevent documents with tenant_id=['a','b'] crossing isolation.
        allowed_types = ['int', 'long'] if type(value) is int else ['string']
        guards.append({'$in': [{'$type': '$' + field}, allowed_types]})
        guards.append({'$eq': ['$' + field, {'$literal': value}]})
    if guards:
        filters['$expr'] = {'$and': guards}
    return filters


def mongodb_execute(raw, tenant, request, internal_path=None):
    validate_endpoint(raw)
    pymongo = importlib.import_module('pymongo')
    # No SRV URL, discovery, redirects, retryable writes or unverified TLS option.
    client = pymongo.MongoClient(host=raw['host'], port=raw['port'], username=raw['user'],
                                password=_credential(raw), authSource=raw['database'], tls=True,
                                directConnection=True, retryWrites=False, retryReads=False,
                                serverSelectionTimeoutMS=5000, connectTimeoutMS=5000,
                                socketTimeoutMS=5000, timeoutMS=7000)
    try:
        db = client[raw['database']]
        info = next(db.list_collections(filter={'name': request['resource']}), None)
        if not info or info.get('type') != 'collection' or info.get('options', {}).get('collation'):
            raise Forbidden('Ordinary simple-collation MongoDB collection required')
        collection = db[request['resource']]
        filters = mongo_selector(raw, tenant, request)
        if request['operation'] == 'read':
            projection = {field: 1 for field in request['fields']}
            if '_id' not in projection:
                projection['_id'] = 0
            cursor = collection.find(filters, projection).limit(request['limit']).max_time_ms(2000)
            try:
                return _rows(([doc.get(field) for field in request['fields']] for doc in cursor), request['fields'])
            finally:
                cursor.close()
        spec = raw['resources'][request['resource']]
        key = {spec['key_field']}
        scoped = key | ({raw['tenant_column']} if raw['isolation'] == 'tenant_column' else set())
        unique = []
        for index in collection.list_indexes():
            fields = dict(index.get('key', {}))
            if ((index.get('unique') is True or fields == {'_id': 1})
                    and not index.get('partialFilterExpression') and not index.get('sparse')
                    and not index.get('collation') and all(v in (1, -1) for v in fields.values())):
                unique.append(set(fields))
        if key not in unique and scoped not in unique:
            raise Forbidden('Database-enforced unique record key required')
        values = mutations(raw, tenant, request)
        if request['operation'] == 'insert':
            result = collection.insert_one(values)
        else:
            result = collection.update_one(filters, {'$set': values, '$inc': {spec['version_field']: 1}}, upsert=False)
        if not result.acknowledged:
            raise RuntimeError('Unacknowledged MongoDB write')
        if request['operation'] == 'update' and result.matched_count != 1:
            raise Conflict('Database record missing or version changed')
        return _receipt(request)
    finally:
        client.close()


TRANSPORTS = {'sqlite_managed': sqlite_execute, 'postgres_managed': postgres_execute,
              'mongodb_managed': mongodb_execute}
