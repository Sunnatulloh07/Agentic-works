"""Shared validation for property-node and exact-document source previews."""
from ..engine import Conflict, Forbidden
from .contract import name, record_key, text
from .transports import validate_endpoint


def validate_graph_search(raw, tenant, request):
    validate_endpoint(raw)
    if raw.get('tls') is not True: raise ValueError('Verified database TLS required')
    text(tenant, 256)
    if not isinstance(record_key(request.get('key')), str): raise ValueError('Exact string record key required')
    if raw.get('isolation') == 'dedicated_database':
        if raw.get('bound_tenant') != tenant or 'tenant_column' in raw: raise Forbidden('Dedicated tenant binding mismatch')
    elif raw.get('isolation') != 'tenant_column': raise ValueError('Explicit isolation required')
    if raw['driver'] == 'neo4j_managed' and raw['database'].lower() == 'system': raise Forbidden('Neo4j system database denied')
    if raw['driver'] == 'elasticsearch_managed':
        resource=name(request['resource'])
        if resource.startswith('_') or resource != resource.lower(): raise ValueError('Ordinary lowercase index required')


def endpoint(raw, scheme):
    host=raw['host']
    if ':' in host:host='['+host+']'
    return scheme+'://'+host+':'+str(raw['port'])


def external(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        code=getattr(exc,'code','')
        if getattr(exc,'status_code',None)==409 or code=='Neo.ClientError.Schema.ConstraintValidationFailed':
            raise Conflict('Database key exists or record version changed') from None
        raise RuntimeError('Database provider failed; reconciliation required') from None
