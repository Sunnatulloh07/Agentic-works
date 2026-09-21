"""SQL Server 2019+ source adapter, NOT live-verified or production accepted.

Requires Microsoft ODBC Driver 18, pyodbc, verified TLS and object VIEW
DEFINITION. Writes take a conservative table lock; no retry or bulk DML.
SQL Server has no SET TRANSACTION READ ONLY equivalent: read-only credentials
remain a deployment requirement, not an ApplicationIntent safety claim.
"""
import importlib
from ..engine import Conflict, Forbidden
from .contract import name, selector
from .sql import compile_sql
from .transports import _credential, _receipt, _rows, validate_endpoint
from .enterprise_common import bounded_records, close_resources, require_unique, resource_fields


def _odbc_value(value):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Invalid ODBC connection value')
    return '{' + value.replace('}', '}}') + '}'


def _connect(raw):
    driver = importlib.import_module('pyodbc')
    # This must occur before the first pyodbc connection in the worker process.
    # A dedicated worker avoids reusing sessions with unknown session settings.
    driver.pooling = False
    host = raw['host']
    if ':' in host:
        host = '[' + host + ']'
    parts = [('DRIVER', 'ODBC Driver 18 for SQL Server'),
             ('SERVER', 'tcp:' + host + ',' + str(raw['port'])),
             ('DATABASE', raw['database']), ('UID', raw['user']),
             ('PWD', _credential(raw))]
    connection_string = ';'.join(k + '=' + _odbc_value(v) for k, v in parts)
    connection_string += ';Encrypt=yes;TrustServerCertificate=no;ConnectRetryCount=0;MARS_Connection=no;'
    try:
        return driver.connect(connection_string, autocommit=False, timeout=5)
    except Exception:
        raise RuntimeError('SQL Server connection failed; driver detail suppressed') from None


def _table(cur, raw, request):
    target = '[' + name(raw['schema']) + '].[' + name(request['resource']) + ']'
    cur.execute("SELECT HAS_PERMS_BY_NAME(?, 'OBJECT', 'VIEW DEFINITION')", [target])
    permission = cur.fetchone()
    if not permission or permission[0] != 1:
        raise Forbidden('SQL Server object metadata visibility required')
    cur.execute('SELECT t.object_id,t.temporal_type,t.is_memory_optimized,t.is_filetable,t.is_node,t.is_edge '
                'FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id '
                'WHERE s.name=? AND t.name=? AND t.is_ms_shipped=0',
                [raw['schema'], request['resource']])
    row = cur.fetchone()
    if not row or any(v != 0 for v in row[1:]):
        raise Forbidden('Only ordinary SQL Server base tables supported')
    return row[0], target


def _columns(cur, raw, tenant, request, object_id):
    cur.execute('SELECT c.name,ty.name,c.is_computed,c.is_identity,c.generated_always_type,'
                'c.is_nullable,c.collation_name,ty.is_user_defined,c.max_length '
                'FROM sys.columns c JOIN sys.types ty ON ty.user_type_id=c.user_type_id '
                'WHERE c.object_id=?', [object_id])
    columns = {r[0]: r[1:] for r in cur.fetchall()}
    for field in resource_fields(raw, request):
        row = columns.get(field)
        if not row or row[0] not in {'nvarchar', 'varchar', 'bigint', 'int', 'smallint', 'tinyint', 'bit'}:
            raise Forbidden('SQL Server preview requires ordinary bounded scalar columns')
        if row[0] in {'nvarchar', 'varchar'} and not 1 <= row[7] <= 8000:
            raise Forbidden('SQL Server MAX and oversized text columns denied')
        if row[1] or row[2] or row[3] or row[6]:
            raise Forbidden('Generated, identity and custom SQL Server columns denied')
    spec = raw['resources'][request['resource']]
    version = columns[spec['version_field']]
    if version[0] != 'bigint' or version[4]:
        raise Forbidden('SQL Server version requires nonnullable bigint')
    for field in [spec['key_field']] + ([raw['tenant_column']] if raw['isolation'] == 'tenant_column' else []):
        row = columns[field]
        if row[4]:
            raise Forbidden('SQL Server key and tenant columns must be nonnullable')
        if row[0] in {'varchar', 'nvarchar'}:
            if row[0] != 'nvarchar' or not (row[5] or '').endswith('_BIN2'):
                raise Forbidden('SQL Server textual identity requires nvarchar BIN2 collation')
        elif field == raw.get('tenant_column') or row[0] not in {'bigint', 'int', 'smallint', 'tinyint'}:
            raise Forbidden('Unsupported SQL Server identity column')
    for field, value in selector(raw, tenant, request).items():
        textual = columns[field][0] == 'nvarchar'
        if textual != isinstance(value, str):
            raise ValueError('SQL Server selector type does not match provisioned column')


def _write_schema(cur, raw, request, object_id):
    cur.execute('SELECT TOP (1) 1 FROM sys.triggers WHERE parent_id=? AND is_disabled=0', [object_id])
    if cur.fetchone():
        raise Forbidden('Managed SQL Server writes deny enabled triggers')
    cur.execute('SELECT TOP (1) 1 FROM sys.foreign_keys WHERE parent_object_id=? OR referenced_object_id=?',
                [object_id, object_id])
    if cur.fetchone():
        raise Forbidden('Managed SQL Server writes require isolated tables without foreign keys')
    cur.execute('SELECT i.index_id,c.name FROM sys.indexes i '
                'JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id '
                'JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id '
                'WHERE i.object_id=? AND i.is_unique=1 AND i.is_disabled=0 AND i.is_hypothetical=0 '
                'AND i.has_filter=0 AND i.type IN (1,2) AND ic.key_ordinal>0 AND ic.is_included_column=0 '
                'ORDER BY i.index_id,ic.key_ordinal', [object_id])
    indexes = {}
    for index, column in cur.fetchall():
        indexes.setdefault(index, set()).add(column)
    require_unique(raw, request, list(indexes.values()))


def sqlserver_execute(raw, tenant, request, internal_path=None):
    validate_endpoint(raw)
    sql, params = compile_sql(raw, tenant, request)
    conn = _connect(raw)
    cur = None
    try:
        conn.timeout = 2
        cur = conn.cursor()
        cur.execute('SET XACT_ABORT ON')
        cur.execute('SET NOCOUNT OFF')
        cur.execute('SET ANSI_WARNINGS ON')
        cur.execute('SET ARITHABORT ON')
        cur.execute('SET LOCK_TIMEOUT 1000')
        object_id, target = _table(cur, raw, request)
        write = request['operation'] != 'read'
        if write:
            cur.execute('SELECT TOP (1) 1 FROM ' + target + ' WITH (TABLOCKX,HOLDLOCK)')
            cur.fetchall()
            # Recheck after acquiring a lock; live concurrent-DDL acceptance is
            # still required and administrators must not mutate allowed DDL.
            locked_id, _ = _table(cur, raw, request)
            if locked_id != object_id:
                raise Forbidden('SQL Server resource changed during validation')
        _columns(cur, raw, tenant, request, object_id)
        if write:
            _write_schema(cur, raw, request, object_id)
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
            pass  # Original failure remains primary, gateway marks uncertain.
        if isinstance(exc, (Conflict, Forbidden, ValueError)) or not isinstance(exc, Exception):
            raise
        raise RuntimeError('SQL Server operation failed; outcome requires reconciliation') from None
    finally:
        try:
            close_resources(conn, cur)
        except Exception:
            raise RuntimeError('SQL Server cleanup failed; outcome requires reconciliation') from None
