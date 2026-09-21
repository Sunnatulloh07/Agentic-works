"""Shared narrow-schema checks for unverified enterprise SQL transports."""
from ..engine import Forbidden


def resource_fields(raw, request):
    spec = raw['resources'][request['resource']]
    fields = set(spec['read_fields']) | set(spec['insert_fields']) | set(spec['update_fields'])
    fields |= {spec['key_field'], spec['version_field']}
    if raw['isolation'] == 'tenant_column':
        fields.add(raw['tenant_column'])
    return fields


def require_unique(raw, request, indexes):
    spec = raw['resources'][request['resource']]
    key = {spec['key_field']}
    scoped = key | ({raw['tenant_column']} if raw['isolation'] == 'tenant_column' else set())
    if key not in indexes and scoped not in indexes:
        raise Forbidden('Database-enforced unique record key required')


def validate_enterprise_request(raw, request):
    if raw['driver'] == 'oracle_managed':
        # Oracle SQL before 23c has no BOOLEAN column, and empty text becomes
        # NULL. Refuse lossy portability instead of silently changing values.
        values = list(request.get('values', {}).values())
        if 'key' in request:
            values.append(request['key'])
        if any(type(v) is bool or v == '' for v in values):
            raise ValueError('Oracle preview does not support boolean or empty-string values')


def bounded_records(cur, limit):
    for _ in range(limit):
        row = cur.fetchone()
        if row is None:
            break
        yield row


def close_resources(conn, cur):
    # Always try the connection even if cursor cleanup fails. A cleanup failure
    # is not success; the gateway conservatively records an uncertain dispatch.
    try:
        if cur is not None:
            cur.close()
    finally:
        conn.close()
