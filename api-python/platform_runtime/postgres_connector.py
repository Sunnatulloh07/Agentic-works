"""Bounded PostgreSQL base-table reads. No arbitrary SQL or request-supplied DSN.

Contract-tested only until the deployment runs the real PostgreSQL acceptance
suite. Network allowlisting, read-only DB role and RLS are deployment controls.
"""
import datetime
import decimal
import ipaddress
import json
import math
import os
import re
from .engine import Forbidden
from .connector_authority import require_read_access

NAME=re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')

# This module is the SQL boundary: everything below constrains what a tenant
# configuration may ask for and what a provider may return. They were literals at their
# call sites, which is how a limit stops being reviewable. The identifier ceiling is
# inside the pattern because PostgreSQL's own limit is 63 bytes and a longer name is
# truncated by the server rather than refused, so the check must happen here.
MAX_HOST_CHARS = 253
MIN_PORT = 1
DEFAULT_PORT = 5432
MAX_PORT = 65535
MAX_TABLES = 100
MAX_COLUMNS = 40
MAX_FILTER_CHARS = 1000
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
MAX_CELL_BYTES = 16000
MAX_RESULT_BYTES = 80000
CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 2000
LOCK_TIMEOUT_MS = 1000


def ident(value):
    if not isinstance(value,str) or not NAME.fullmatch(value):raise ValueError('Invalid SQL identifier')
    return '"'+value+'"'


def validate_config(raw, *, agent=None):
    require_read_access(raw, agent=agent)
    if not isinstance(raw,dict) or raw.get('driver')!='postgres_readonly' or raw.get('enabled',True) is not True:
        raise Forbidden('Connection unavailable')
    host=raw.get('host');hosts=raw.get('allowed_hosts')
    if not isinstance(host,str) or not isinstance(hosts,list) or not hosts or host not in hosts or len(host)>MAX_HOST_CHARS:
        raise ValueError('Explicit PostgreSQL host allowlist required')
    try:ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?',host):raise ValueError('Invalid host')
    port=raw.get('port',DEFAULT_PORT)
    if type(port) is not int or not MIN_PORT<=port<=MAX_PORT:raise ValueError('Invalid port')
    if raw.get('sslmode')!='verify-full':raise ValueError('Verified TLS required')
    for key in ('database','user'):
        # libpq can expand dbname strings containing '=' or URI syntax. Forbid conninfo expansion.
        if not isinstance(raw.get(key),str) or not NAME.fullmatch(raw[key]):
            raise ValueError('Plain database and role names required')
    if not isinstance(raw.get('password_env'),str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*',raw['password_env']):
        raise ValueError('Invalid credential reference')
    schema=raw.get('schema','public');ident(schema)
    if schema.startswith('pg_') or schema=='information_schema':raise Forbidden('System schema denied')
    tables=raw.get('tables')
    if not isinstance(tables,dict) or not 1<=len(tables)<=MAX_TABLES:raise ValueError('Explicit tables required')
    for table,cols in tables.items():
        ident(table)
        if not isinstance(cols,list) or not 1<=len(cols)<=MAX_COLUMNS or any(not isinstance(c,str) for c in cols) or len(set(cols))!=len(cols):
            raise ValueError('Invalid columns')
        for col in cols:ident(col)
    isolation=raw.get('isolation')
    if isolation not in {'dedicated_database','tenant_column'}:raise ValueError('Explicit data isolation required')
    if isolation=='tenant_column':
        col=raw.get('tenant_column');ident(col)
        if any(col not in columns for columns in tables.values()):raise ValueError('Tenant column required for every table')
    elif raw.get('tenant_column'):raise ValueError('Ambiguous isolation')
    return {**raw,'port':port,'schema':schema}


def compile_read(tenant,raw,request,*,agent=None):
    item=validate_config(raw,agent=agent)
    if not isinstance(request,dict) or set(request)-{'connection','table','columns','limit','where'}:
        raise ValueError('Invalid read fields')
    table=request.get('table');columns=request.get('columns')
    if not isinstance(table,str) or table not in item['tables']:raise Forbidden('Table not permitted')
    approved=item['tables'][table]
    if not isinstance(columns,list) or not 1<=len(columns)<=MAX_COLUMNS or any(not isinstance(c,str) for c in columns) or len(set(columns))!=len(columns):
        raise ValueError('Invalid columns')
    if any(col not in approved for col in columns):raise Forbidden('Column not permitted')
    limit=request.get('limit',DEFAULT_LIMIT)
    if type(limit) is not int or not 1<=limit<=MAX_LIMIT:raise ValueError('Invalid limit')
    where=request.get('where',{})
    if not isinstance(where,dict) or set(where) not in (set(),{'column','equals'}):raise ValueError('Invalid filter')
    clauses=[];parameters=[]
    if item['isolation']=='tenant_column':
        clauses.append(ident(item['tenant_column'])+' = %s');parameters.append(tenant)
    if where:
        if where['column'] not in approved or not isinstance(where['equals'],str) or len(where['equals'])>MAX_FILTER_CHARS:
            raise Forbidden('Filter not permitted')
        clauses.append(ident(where['column'])+' = %s');parameters.append(where['equals'])
    sql='SELECT '+','.join(ident(col) for col in columns)+' FROM '+ident(item['schema'])+'.'+ident(table)
    if clauses:sql+=' WHERE '+' AND '.join(clauses)
    sql+=' LIMIT %s';parameters.append(limit)
    return item,sql,parameters


def cell(value):
    if isinstance(value,(bytes,bytearray,memoryview)):return '[binary omitted]'
    if isinstance(value,(datetime.date,datetime.time)):return value.isoformat()
    if isinstance(value,decimal.Decimal):
        if not value.is_finite():raise ValueError('Non-finite decimal')
        return str(value)  # Preserve money precision; never coerce to floating point.
    if isinstance(value,float) and not math.isfinite(value):raise ValueError('Non-finite float')
    if value is None or isinstance(value,(str,int,float,bool)):return value
    raise ValueError('Unsupported database type')


def read(tenant,raw,request,*,agent=None):
    item,sql,params=compile_read(tenant,raw,request,agent=agent)
    password=os.environ.get(item['password_env'],'')
    if not password:raise RuntimeError('Database credential unavailable')
    try:import psycopg
    except ImportError as exc:raise RuntimeError('PostgreSQL dependency unavailable') from exc
    try:
        with psycopg.connect(host=item['host'],port=item['port'],dbname=item['database'],user=item['user'],
                             password=password,sslmode='verify-full',connect_timeout=CONNECT_TIMEOUT_SECONDS,
                             options='-c statement_timeout=%d -c lock_timeout=%d -c default_transaction_read_only=on' % (STATEMENT_TIMEOUT_MS, LOCK_TIMEOUT_MS)) as conn:
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute("SELECT c.relkind FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s",(item['schema'],request['table']))
                kind=cur.fetchone()
                if not kind or kind[0] not in {'r','p'}:raise Forbidden('Only base tables allowed')
            # Named server-side cursor bounds fetch batches, rather than buffering all rows at client.
            with conn.cursor(name='platform_read') as cur:
                cur.execute(sql,params);rows=[];total=0;columns=request['columns'];limit=request.get('limit',DEFAULT_LIMIT)
                for _ in range(limit):
                    record=cur.fetchone()
                    if record is None:break
                    value={column:cell(v) for column,v in zip(columns,record)}
                    size=len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())
                    if size>MAX_CELL_BYTES or total+size>MAX_RESULT_BYTES:raise ValueError('Result exceeds limits')
                    total+=size;rows.append(value)
        return {'connection':request['connection'],'table':request['table'],'rows':rows,'returned':len(rows),
                'limit':limit,'read_only':True,'driver':'postgres_readonly'}
    except (Forbidden,ValueError):raise
    except Exception as exc:raise RuntimeError('PostgreSQL read failed') from exc
