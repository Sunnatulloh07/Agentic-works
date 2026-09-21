"""Bounded cross-database operations, not a raw SQL/NoSQL escape hatch."""
import json
import re
from dataclasses import dataclass
from ..engine import Forbidden, digest, encode

VERSION = '1.1'
NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')
MAX_INTEGER = 2**53 - 1


@dataclass(frozen=True)
class Backend:
    family: str
    stage: str
    dependency: str | None = None


BACKENDS = {
    'sqlite_managed': Backend('sql', 'local_transport', 'sqlite3'),
    'postgres_managed': Backend('sql', 'network_transport_unverified', 'psycopg'),
    'mysql_managed': Backend('sql', 'network_transport_unverified', 'pymysql'),
    'mariadb_managed': Backend('sql', 'network_transport_unverified', 'pymysql'),
    'sqlserver_managed': Backend('sql', 'network_transport_unverified', 'pyodbc'),
    'oracle_managed': Backend('sql', 'network_transport_unverified', 'oracledb'),
    'mongodb_managed': Backend('document', 'network_transport_unverified', 'pymongo'),
    'redis_managed': Backend('key_value', 'network_transport_unverified', 'redis'),
    'dynamodb_managed': Backend('key_value', 'network_transport_unverified', 'boto3'),
    'cassandra_managed': Backend('wide_column', 'network_transport_unverified', 'cassandra-driver'),
    'neo4j_managed': Backend('graph', 'network_transport_unverified', 'neo4j'),
    'elasticsearch_managed': Backend('search', 'network_transport_unverified', 'elasticsearch'),
}
EXECUTABLE = frozenset(k for k, v in BACKENDS.items() if v.stage in {
    'local_transport', 'network_transport_unverified'})


def name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError('Plain database identifier required')
    return value


def _utf8_size(value, label='string'):
    """The UTF-8 byte length, or a ValueError naming the real problem.

    ``str.encode`` raises ``UnicodeEncodeError`` for a lone surrogate, and
    ``json.loads`` PRODUCES one from a ``\\udXXX`` escape -- so a request body can
    carry a value that is a ``str`` and still cannot be measured, stored or
    digested. Three functions here bound a value by its UTF-8 size, so the
    measurement is exactly where the refusal belongs.

    It belongs there because of what the caller can handle: this contract answers
    with ``ValueError`` or ``Forbidden``, and an uncaught ``UnicodeEncodeError``
    out of a validation path is a 500 where a refusal was meant. Measured before
    the fix: ``scalar('\\ud800')`` raised ``UnicodeEncodeError`` while
    ``text('\\ud800')`` -- the same value, in the same file -- accepted it.
    """
    try:
        return len(value.encode('utf-8'))
    except UnicodeEncodeError:
        raise ValueError(f'{label} is not encodable as UTF-8') from None


def text(value, maximum=128):
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('Invalid bounded string')
    # A bounded string is one this contract can carry, and a lone surrogate cannot
    # be encoded. Accepting it here while `scalar` refuses it gave the same value
    # two different answers depending on which gate it reached first.
    _utf8_size(value)
    return value


def names(value, *, nonempty=False):
    if not isinstance(value, list) or not int(nonempty) <= len(value) <= 40:
        raise ValueError('Bounded field list required')
    checked = [name(v) for v in value]
    if len(checked) != len(set(checked)):
        raise ValueError('Duplicate fields')
    return checked


def scalar(value):
    # Exact integers and decimal-as-string are portable. Nested documents,
    # binary, floats and operator expressions require a future typed extension.
    if value is None or type(value) is bool:
        return value
    if type(value) is int and -MAX_INTEGER <= value <= MAX_INTEGER:
        return value
    if isinstance(value, str) and _utf8_size(value) <= 4000:
        return value
    raise ValueError('Only bounded portable scalar values are supported')


def record_key(value):
    scalar(value)
    if type(value) not in (str, int) or (isinstance(value, str) and not 1 <= len(value) <= 256):
        raise ValueError('Explicit string or integer record key required')
    return value


def _pairs(items):
    obj = {}
    for key, value in items:
        if key in obj:
            raise ValueError('Duplicate JSON field')
        obj[key] = value
    return obj


def _constant(value):
    raise ValueError('Non-finite JSON value')


def parse_request(raw):
    if not isinstance(raw, str):
        raise ValueError('Bounded request JSON required')
    if not 1 <= _utf8_size(raw, 'request') <= 12000:
        raise ValueError('Bounded request JSON required')
    try:
        out = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (RecursionError, json.JSONDecodeError):
        raise ValueError('Invalid request JSON') from None
    if not isinstance(out, dict):
        raise ValueError('Request must be an object')
    return out


def validate_config(raw, *, tenant, agent, capability):
    text(tenant, 256)
    text(agent)
    if not isinstance(raw, dict):
        raise Forbidden('Database connection unavailable')
    if raw.get('contract_version') != VERSION:
        raise ValueError('Managed database contract 1.1 required')
    if type(raw.get('enabled')) is not bool or raw['enabled'] is not True:
        raise Forbidden('Database connection disabled')
    if not isinstance(raw.get('lifecycle'), str) or raw['lifecycle'] not in {'configured', 'healthy', 'degraded'}:
        raise Forbidden('Database lifecycle does not permit execution')
    driver = raw.get('driver')
    if not isinstance(driver, str) or driver not in BACKENDS:
        raise Forbidden('Unknown database backend')
    if type(raw.get('generation')) is not int or raw['generation'] < 1:
        raise ValueError('Positive connection generation required')
    agents = raw.get('agent_ids')
    if not isinstance(agents, list) or not 1 <= len(agents) <= 100:
        raise ValueError('Explicit managed database agent bindings required')
    for item in agents:
        text(item)
    if len(agents) != len(set(agents)) or agent not in agents:
        raise Forbidden('Agent is not bound to database connection')
    caps = raw.get('capabilities')
    if (not isinstance(caps, list) or not caps or len(caps) > 3
            or any(not isinstance(v, str) or v not in {'read', 'plan_write', 'execute_write'} for v in caps)
            or len(caps) != len(set(caps))):
        raise ValueError('Invalid database capabilities')
    if capability not in caps:
        raise Forbidden('Database capability not permitted')
    if raw.get('isolation') == 'tenant_column':
        scope = name(raw.get('tenant_column'))
        if 'bound_tenant' in raw:
            raise ValueError('Ambiguous database isolation')
    elif raw.get('isolation') == 'dedicated_database':
        scope = None
        if raw.get('bound_tenant') != tenant or 'tenant_column' in raw:
            raise Forbidden('Dedicated database tenant binding mismatch')
    else:
        raise ValueError('Explicit database isolation required')
    resources = raw.get('resources')
    if not isinstance(resources, dict) or not 1 <= len(resources) <= 100:
        raise ValueError('Explicit database resources required')
    for resource, spec in resources.items():
        name(resource)
        if not isinstance(spec, dict) or set(spec) != {
                'key_field', 'version_field', 'read_fields', 'insert_fields', 'update_fields'}:
            raise ValueError('Invalid database resource definition')
        key, version = name(spec['key_field']), name(spec['version_field'])
        protected = [key, version] + ([scope] if scope else [])
        if len(protected) != len(set(protected)):
            raise ValueError('Key, version and tenant fields must differ')
        readable = names(spec['read_fields'], nonempty=True)
        insertable, updatable = names(spec['insert_fields']), names(spec['update_fields'])
        if any(v in protected for v in insertable + updatable):
            raise Forbidden('Protected database field is not writable by model')
        if not {key, version}.issubset(readable):
            raise ValueError('Read fields must expose record key and version')
        if not set(insertable + updatable).issubset(readable):
            raise ValueError('Writable fields must be reviewable')
    if any(k in raw for k in ('password', 'token', 'dsn', 'url', 'sql', 'query')):
        raise ValueError('Inline credentials and arbitrary connection strings are denied')
    return raw


def normalize(raw, tenant, request):
    if not isinstance(request, dict):
        raise ValueError('Database request must be object')
    operation = request.get('operation')
    fields = {
        'read': {'operation', 'resource', 'fields', 'key', 'limit'},
        'insert': {'operation', 'resource', 'key', 'values'},
        'update': {'operation', 'resource', 'key', 'values', 'expected_version'},
    }
    if not isinstance(operation, str) or operation not in fields or set(request) - fields[operation]:
        raise ValueError('Unsupported database operation or fields')
    resource = name(request.get('resource'))
    spec = raw['resources'].get(resource)
    if spec is None:
        raise Forbidden('Database resource not permitted')
    out = {'operation': operation, 'resource': resource}
    if operation == 'read':
        selected = names(request.get('fields'), nonempty=True)
        if not set(selected).issubset(spec['read_fields']):
            raise Forbidden('Read field not permitted')
        limit = request.get('limit', 50)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Read limit must be 1..100')
        out.update(fields=selected, limit=limit)
        if 'key' in request:
            out['key'] = record_key(request['key'])
    else:
        out['key'] = record_key(request.get('key'))
        values = request.get('values')
        if not isinstance(values, dict) or not 1 <= len(values) <= 40:
            raise ValueError('Explicit bounded write values required')
        allowed = spec['insert_fields'] if operation == 'insert' else spec['update_fields']
        for key, value in values.items():
            name(key)
            if key not in allowed:
                raise Forbidden('Write field not permitted')
            scalar(value)
        out['values'] = dict(sorted(values.items()))
        if operation == 'update':
            version = request.get('expected_version')
            # `<=` and not `<`: MAX_INTEGER is the largest integer this contract
            # calls portable, and `scalar` above accepts it. The strict form made
            # `normalize` refuse a value that the same file, and all four backend
            # guards, accept -- and it refused it with "Update requires positive
            # expected_version", which is false: the version IS positive, it is
            # simply the ceiling. Measured, then made consistent.
            if type(version) is not int or not 1 <= version <= MAX_INTEGER:
                raise ValueError(
                    'Update requires a positive expected_version no greater than '
                    f'{MAX_INTEGER}')
            out['expected_version'] = version
    if len(encode(out).encode('utf-8')) > 12000:
        raise ValueError('Request exceeds byte limit')
    return out


def plan_fingerprint(tenant, agent, connection, raw, request):
    # Contains provisioned config and credential REFERENCES, never secret values.
    return digest({'tenant': tenant, 'agent': agent, 'connection': connection,
                   'config': raw, 'request': request})


def mutations(raw, tenant, request):
    spec = raw['resources'][request['resource']]
    data = dict(request['values'])
    if request['operation'] == 'insert':
        data[spec['key_field']] = request['key']
        data[spec['version_field']] = 1
        if raw['isolation'] == 'tenant_column':
            data[raw['tenant_column']] = tenant
    return data


def selector(raw, tenant, request):
    spec = raw['resources'][request['resource']]
    out = {}
    if raw['isolation'] == 'tenant_column':
        out[raw['tenant_column']] = tenant
    if 'key' in request:
        out[spec['key_field']] = request['key']
    if request['operation'] == 'update':
        out[spec['version_field']] = request['expected_version']
    return out


def catalog():
    return [{'driver': k, 'family': v.family, 'stage': v.stage,
             'transport_implemented': k in EXECUTABLE, 'live_verified': False,
             'dependency': v.dependency,
             'operations': ['read', 'insert', 'update'] if k in EXECUTABLE else []}
            for k, v in BACKENDS.items()]
