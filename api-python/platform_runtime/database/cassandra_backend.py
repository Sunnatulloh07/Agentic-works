"""Bounded Cassandra exact-key transport using LWT, never blind retry or upsert.

Source/contract preview. Live driver, quorum, TLS, schema races and timeout
acceptance remain separate. Only ordinary text/bigint/boolean columns supported.
"""
import importlib
import ssl

from ..engine import Conflict, Forbidden
from .contract import MAX_INTEGER, mutations, name, record_key, scalar, text
from .transports import _credential, _receipt, _rows, validate_endpoint


def _external(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        raise RuntimeError('Cassandra provider failed; reconciliation required') from None


def validate_cassandra(raw, tenant, request):
    validate_endpoint(raw)
    if raw.get('tls') is not True:
        raise ValueError('Verified Cassandra TLS required')
    if raw['database'].lower().startswith('system'):
        raise Forbidden('System Cassandra keyspace denied')
    text(tenant, 256)
    if raw.get('isolation') == 'dedicated_database':
        if raw.get('bound_tenant') != tenant or 'tenant_column' in raw:
            raise Forbidden('Dedicated keyspace tenant binding mismatch')
    elif raw.get('isolation') != 'tenant_column':
        raise ValueError('Explicit Cassandra isolation required')
    if not isinstance(record_key(request.get('key')), str):
        raise ValueError('Cassandra preview requires an exact string key')


def _quoted(value):
    return '"' + name(value) + '"'


def _metadata(cluster, raw, request):
    keyspace = cluster.metadata.keyspaces.get(raw['database'])
    table = keyspace.tables.get(request['resource']) if keyspace else None
    if table is None:
        raise Forbidden('Ordinary Cassandra table metadata required')
    spec = raw['resources'][request['resource']]
    partition = [raw['tenant_column']] if raw['isolation'] == 'tenant_column' else [spec['key_field']]
    clustering = [spec['key_field']] if raw['isolation'] == 'tenant_column' else []
    if [c.name for c in table.partition_key] != partition or [c.name for c in table.clustering_key] != clustering:
        raise Forbidden('Cassandra primary key does not enforce configured isolation')
    if table.options.get('default_time_to_live', 0) != 0:
        raise Forbidden('TTL tables require separate version-reuse acceptance')
    # Static values belong to the whole partition, not a single record.
    if any(getattr(c, 'is_static', False) or c.cql_type == 'counter' for c in table.columns.values()):
        raise Forbidden('Static and counter columns denied')
    fields = set(spec['read_fields']) | set(partition) | set(clustering)
    if not fields.issubset(table.columns):
        raise Forbidden('Configured Cassandra columns missing')
    types = {f: table.columns[f].cql_type for f in fields}
    if any(t not in {'text', 'varchar', 'ascii', 'bigint', 'boolean'} for t in types.values()):
        raise Forbidden('Only portable scalar Cassandra columns supported')
    if any(types[f] != 'text' for f in partition + clustering) or types[spec['version_field']] != 'bigint':
        raise Forbidden('Cassandra identity must be text and version bigint')
    for field, value in request.get('values', {}).items():
        typ = types[field]
        scalar(value)
        if value is None:
            continue
        if typ in {'text', 'varchar', 'ascii'} and not isinstance(value, str):
            raise ValueError('Cassandra text value required')
        if typ == 'ascii' and not value.isascii():
            raise ValueError('Cassandra ASCII column cannot store this value')
        if typ == 'bigint' and type(value) is not int:
            raise ValueError('Cassandra bigint value required')
        if typ == 'boolean' and type(value) is not bool:
            raise ValueError('Cassandra boolean value required')
    return table


def compile_cql(raw, tenant, request):
    spec = raw['resources'][request['resource']]
    target = _quoted(raw['database']) + '.' + _quoted(request['resource'])
    identity = {raw['tenant_column']: tenant} if raw['isolation'] == 'tenant_column' else {}
    identity[spec['key_field']] = request['key']
    where = ' AND '.join(_quoted(f) + ' = %s' for f in identity)
    if request['operation'] == 'read':
        fields = list(dict.fromkeys(request['fields'] + list(identity) + [spec['version_field']]))
        return 'SELECT ' + ','.join(_quoted(f) for f in fields) + ' FROM ' + target + ' WHERE ' + where + ' LIMIT 1', list(identity.values())
    if request['operation'] == 'insert':
        values = mutations(raw, tenant, request)
        return ('INSERT INTO ' + target + ' (' + ','.join(_quoted(f) for f in values) + ') VALUES (' +
                ','.join('%s' for _ in values) + ') IF NOT EXISTS'), list(values.values())
    values = dict(request['values'])
    values[spec['version_field']] = request['expected_version'] + 1
    sql = ('UPDATE ' + target + ' SET ' + ','.join(_quoted(f) + ' = %s' for f in values) +
           ' WHERE ' + where + ' IF ' + _quoted(spec['version_field']) + ' = %s')
    return sql, list(values.values()) + list(identity.values()) + [request['expected_version']]


def cassandra_execute(raw, tenant, request, internal_path=None):
    validate_cassandra(raw, tenant, request)
    cluster = None
    try:
        module = importlib.import_module('cassandra')
        clusters = importlib.import_module('cassandra.cluster')
        auth = importlib.import_module('cassandra.auth')
        policies = importlib.import_module('cassandra.policies')
        query = importlib.import_module('cassandra.query')
        cluster = _external(clusters.Cluster, contact_points=[raw['host']], port=raw['port'],
            auth_provider=_external(auth.PlainTextAuthProvider, username=raw['user'], password=_credential(raw)),
            ssl_context=ssl.create_default_context(),
            load_balancing_policy=policies.WhiteListRoundRobinPolicy([raw['host']]),
            default_retry_policy=policies.FallthroughRetryPolicy(), connect_timeout=5,
            control_connection_timeout=5, token_metadata_enabled=False)
        session = _external(cluster.connect, raw['database'])
        session.row_factory = query.dict_factory
        _metadata(cluster, raw, request)
        sql, params = compile_cql(raw, tenant, request)
        statement = query.SimpleStatement(sql, consistency_level=module.ConsistencyLevel.QUORUM,
            serial_consistency_level=module.ConsistencyLevel.SERIAL, is_idempotent=False, fetch_size=2,
            retry_policy=policies.FallthroughRetryPolicy())
        result = _external(session.execute, statement, params, timeout=3)
        records = result.current_rows
        if result.has_more_pages or not isinstance(records, list) or len(records) > 1:
            raise RuntimeError('Cassandra returned an unbounded or unexpected response')
        if request['operation'] != 'read':
            if len(records) != 1 or records[0].get('[applied]') is not True:
                raise Conflict('Cassandra key exists, is missing or version changed')
            return _receipt(request)
        spec = raw['resources'][request['resource']]
        if records:
            record = records[0]
            if record.get(spec['key_field']) != request['key']:
                raise Forbidden('Cassandra record key mismatch')
            if raw['isolation'] == 'tenant_column' and record.get(raw['tenant_column']) != tenant:
                raise Forbidden('Cassandra tenant mismatch')
            version = record.get(spec['version_field'])
            if type(version) is not int or not 1 <= version <= MAX_INTEGER:
                raise Forbidden('Cassandra version missing or invalid')
        return _rows(([scalar(record.get(f)) for f in request['fields']] for record in records), request['fields'])
    except (Forbidden, Conflict, ValueError):
        raise
    except Exception:
        raise RuntimeError('Cassandra operation failed; reconcile before retry') from None
    finally:
        if cluster is not None:
            try:
                cluster.shutdown()
            except Exception:
                raise RuntimeError('Cassandra cleanup failed; reconcile before retry') from None
