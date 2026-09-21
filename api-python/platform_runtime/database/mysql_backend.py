"""MySQL/MariaDB bounded operations on ordinary transactional InnoDB tables.

Optional PyMySQL dependency. Contract testing is not live server acceptance.
"""
import importlib
from ..engine import Conflict, Forbidden
from .transports import _credential, _receipt, _rows, validate_endpoint
from .sql import compile_sql
from .contract import name


def mysql_execute(raw, tenant, request, internal_path=None):
    validate_endpoint(raw)
    pymysql = importlib.import_module('pymysql')
    sql, params = compile_sql(raw, tenant, request)
    write = request['operation'] != 'read'
    conn = pymysql.connect(host=raw['host'], port=raw['port'], user=raw['user'],
                           password=_credential(raw), database=raw['database'],
                           ssl_verify_cert=True, ssl_verify_identity=True,
                           connect_timeout=5, read_timeout=3, write_timeout=3,
                           autocommit=False, charset='utf8mb4', cursorclass=pymysql.cursors.SSCursor)
    try:
        with conn.cursor() as cur:
            cur.execute('SET SESSION innodb_lock_wait_timeout=1')
            if raw['driver'] == 'mariadb_managed':
                cur.execute('SET SESSION max_statement_time=2')
            else:
                cur.execute('SET SESSION MAX_EXECUTION_TIME=2000')
            cur.execute('START TRANSACTION' if write else 'START TRANSACTION READ ONLY')
            # Open the provisioned resource without reading rows so the current
            # transaction holds a metadata lock while its shape is validated.
            target = '`' + name(raw['schema']) + '`.`' + name(request['resource']) + '`'
            cur.execute('SELECT 1 FROM ' + target + ' LIMIT 0')
            cur.fetchall()
            cur.execute('SELECT TABLE_TYPE,ENGINE FROM information_schema.TABLES '
                        'WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s', (raw['schema'], request['resource']))
            table = cur.fetchone()
            if not table or table[0] != 'BASE TABLE' or table[1] != 'InnoDB':
                raise Forbidden('Only ordinary transactional InnoDB tables are supported')
            # Drain the metadata cursor before the next statement with SSCursor.
            cur.fetchall()
            if write:
                cur.execute('SELECT 1 FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s '
                            'AND EVENT_OBJECT_TABLE=%s LIMIT 1', (raw['schema'], request['resource']))
                trigger = cur.fetchone()
                cur.fetchall()
                if trigger:
                    raise Forbidden('Managed MySQL writes deny table triggers')
                cur.execute('SELECT INDEX_NAME,COLUMN_NAME,SUB_PART FROM information_schema.STATISTICS '
                            'WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND NON_UNIQUE=0 '
                            'ORDER BY INDEX_NAME,SEQ_IN_INDEX', (raw['schema'], request['resource']))
                unique, invalid = {}, set()
                for index, column, prefix in cur.fetchall():
                    if column is None or prefix is not None:
                        invalid.add(index)
                    unique.setdefault(index, set()).add(column)
                keys = [fields for index, fields in unique.items() if index not in invalid]
                spec = raw['resources'][request['resource']]
                key = {spec['key_field']}
                scoped = key | ({raw['tenant_column']} if raw['isolation'] == 'tenant_column' else set())
                if key not in keys and scoped not in keys:
                    raise Forbidden('Database-enforced unique record key required')
            cur.execute(sql, params)
            if write:
                if cur.rowcount != 1:
                    raise Conflict('Database record missing or version changed')
                result = _receipt(request)
            else:
                def records():
                    for _ in range(request['limit']):
                        row = cur.fetchone()
                        if row is None:
                            break
                        yield row
                result = _rows(records(), request['fields'])
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
