"""Oracle 19c+ thin TCPS source adapter. Fake tests are NOT live acceptance.

Exact quoted identifiers, ordinary nonpartitioned heap tables, binary identity
comparison, immediate unique constraints, no enabled triggers or foreign keys.
No Oracle Client initialization, arbitrary DSN, session pool or automatic retry.
"""
import importlib
from ..engine import Conflict, Forbidden
from .contract import name, selector
from .sql import compile_sql
from .transports import _credential, _receipt, _rows, validate_endpoint
from .enterprise_common import (bounded_records, close_resources, require_unique,
                                resource_fields, validate_enterprise_request)


def _connect(raw):
    driver = importlib.import_module('oracledb')
    if not driver.is_thin_mode():
        raise Forbidden('Oracle managed preview requires Thin mode')
    params = driver.ConnectParams(host=raw['host'], port=raw['port'],
                                  service_name=raw['service_name'], protocol='tcps',
                                  ssl_server_dn_match=True, retry_count=0,
                                  tcp_connect_timeout=5, server_type='dedicated', stmtcachesize=0)
    try:
        return driver.connect(user=raw['user'], password=_credential(raw), params=params)
    except Exception:
        raise RuntimeError('Oracle connection failed; driver detail suppressed') from None


def _table(cur, raw, request):
    params = [raw['schema'], request['resource']]
    cur.execute('SELECT t.TEMPORARY,t.NESTED,t.IOT_TYPE,t.PARTITIONED,t.SECONDARY '
                'FROM ALL_TABLES t WHERE t.OWNER=:1 AND t.TABLE_NAME=:2 '
                'AND NOT EXISTS (SELECT 1 FROM ALL_EXTERNAL_TABLES e WHERE e.OWNER=t.OWNER AND e.TABLE_NAME=t.TABLE_NAME) '
                'AND NOT EXISTS (SELECT 1 FROM ALL_MVIEWS m WHERE m.OWNER=t.OWNER AND m.MVIEW_NAME=t.TABLE_NAME) '
                'AND NOT EXISTS (SELECT 1 FROM ALL_OBJECT_TABLES o WHERE o.OWNER=t.OWNER AND o.TABLE_NAME=t.TABLE_NAME)', params)
    row = cur.fetchone()
    if not row or tuple(row) != ('N', 'NO', None, 'NO', 'N'):
        raise Forbidden('Only ordinary Oracle heap tables supported')


def _columns(cur, raw, tenant, request):
    cur.execute('SELECT COLUMN_NAME,DATA_TYPE,VIRTUAL_COLUMN,HIDDEN_COLUMN,IDENTITY_COLUMN,'
                'NULLABLE,DATA_PRECISION,DATA_SCALE,COLLATION,DATA_LENGTH FROM ALL_TAB_COLS '
                'WHERE OWNER=:1 AND TABLE_NAME=:2', [raw['schema'], request['resource']])
    columns = {r[0]: r[1:] for r in cur.fetchall()}
    for field in resource_fields(raw, request):
        row = columns.get(field)
        if not row or row[0] not in {'VARCHAR2', 'NVARCHAR2', 'NUMBER'} or tuple(row[1:4]) != ('NO', 'NO', 'NO'):
            raise Forbidden('Oracle preview requires ordinary scalar columns without generation')
        if row[0] in {'VARCHAR2', 'NVARCHAR2'} and not 1 <= row[8] <= 4000:
            raise Forbidden('Oracle oversized text columns denied')
        if row[0] == 'NUMBER' and row[6] != 0:
            raise Forbidden('Oracle preview requires integer NUMBER columns')
    spec = raw['resources'][request['resource']]
    version = columns[spec['version_field']]
    if (version[0] != 'NUMBER' or version[4] != 'N' or version[5] is None
            or version[5] < 16 or version[6] != 0):
        raise Forbidden('Oracle version requires nonnullable NUMBER(16..38,0)')
    for field in [spec['key_field']] + ([raw['tenant_column']] if raw['isolation'] == 'tenant_column' else []):
        row = columns[field]
        if row[4] != 'N':
            raise Forbidden('Oracle key and tenant columns must be nonnullable')
        if row[0] in {'VARCHAR2', 'NVARCHAR2'}:
            if row[7] not in {'BINARY', 'USING_NLS_COMP'}:
                raise Forbidden('Oracle identity columns require binary collation')
        elif field == raw.get('tenant_column'):
            raise Forbidden('Oracle tenant column must be variable-length text')
    for field, value in selector(raw, tenant, request).items():
        textual = columns[field][0] in {'VARCHAR2', 'NVARCHAR2'}
        if textual != isinstance(value, str):
            raise ValueError('Oracle selector type does not match provisioned column')


def _write_schema(cur, raw, request):
    params = [raw['schema'], request['resource']]
    cur.execute("SELECT 1 FROM ALL_TRIGGERS WHERE TABLE_OWNER=:1 AND TABLE_NAME=:2 AND STATUS='ENABLED' AND ROWNUM=1", params)
    if cur.fetchone():
        raise Forbidden('Managed Oracle writes deny enabled triggers')
    cur.execute("SELECT 1 FROM ALL_CONSTRAINTS WHERE OWNER=:1 AND TABLE_NAME=:2 AND CONSTRAINT_TYPE='R' AND ROWNUM=1", params)
    if cur.fetchone():
        raise Forbidden('Managed Oracle writes deny foreign keys on the resource')
    cur.execute('SELECT c.CONSTRAINT_NAME,cc.COLUMN_NAME FROM ALL_CONSTRAINTS c '
                'JOIN ALL_CONS_COLUMNS cc ON cc.OWNER=c.OWNER AND cc.CONSTRAINT_NAME=c.CONSTRAINT_NAME '
                "WHERE c.OWNER=:1 AND c.TABLE_NAME=:2 AND c.CONSTRAINT_TYPE IN ('P','U') "
                "AND c.STATUS='ENABLED' AND c.VALIDATED='VALIDATED' AND c.DEFERRABLE='NOT DEFERRABLE' "
                'ORDER BY c.CONSTRAINT_NAME,cc.POSITION', params)
    indexes = {}
    for constraint, column in cur.fetchall():
        indexes.setdefault(constraint, set()).add(column)
    require_unique(raw, request, list(indexes.values()))


def oracle_execute(raw, tenant, request, internal_path=None):
    validate_endpoint(raw)
    validate_enterprise_request(raw, request)
    sql, params = compile_sql(raw, tenant, request)
    conn = _connect(raw)
    cur = None
    try:
        conn.autocommit = False
        conn.call_timeout = 2000  # Per round trip, NOT a whole-operation deadline.
        cur = conn.cursor()
        cur.arraysize = 20
        cur.prefetchrows = 20
        cur.execute("ALTER SESSION SET NLS_COMP=BINARY")
        cur.execute("ALTER SESSION SET NLS_SORT=BINARY")
        write = request['operation'] != 'read'
        if not write:
            cur.execute('SET TRANSACTION READ ONLY')
        _table(cur, raw, request)
        if write:
            target = '"' + name(raw['schema']) + '"."' + name(request['resource']) + '"'
            cur.execute('LOCK TABLE ' + target + ' IN ROW EXCLUSIVE MODE NOWAIT')
            _table(cur, raw, request)
        _columns(cur, raw, tenant, request)
        if write:
            _write_schema(cur, raw, request)
        cur.execute(sql, params)
        if write:
            if cur.rowcount != 1:
                raise Conflict('Database record missing or version changed')
            result = _receipt(request)
        else:
            result = _rows(bounded_records(cur, request['limit']), request['fields'])
        conn.commit()
        return result
    except BaseException as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        if isinstance(exc, (Conflict, Forbidden, ValueError)) or not isinstance(exc, Exception):
            raise
        raise RuntimeError('Oracle operation failed; outcome requires reconciliation') from None
    finally:
        try:
            close_resources(conn, cur)
        except Exception:
            raise RuntimeError('Oracle cleanup failed; outcome requires reconciliation') from None
