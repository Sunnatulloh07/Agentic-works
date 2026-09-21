"""Exact-key Redis hashes with atomic insert/CAS. No scan, raw commands or Lua input.

Only this server-owned fixed script is executed. Redis durability depends on the
operator's persistence/replication configuration and requires live acceptance.
"""
import importlib
from ..engine import Conflict, Forbidden, digest, encode
from .contract import mutations, record_key, name
from .transports import _credential, _receipt, validate_endpoint

WRITE_SCRIPT = '''
local key = KEYS[1]
local operation = ARGV[1]
local version_field = ARGV[2]
local expected_version = ARGV[3]
local new_version = ARGV[4]
local exists = redis.call('EXISTS', key)
if operation == 'insert' then
  if exists ~= 0 then return 0 end
elseif operation == 'update' then
  if exists == 0 then return 0 end
  if redis.call('HGET', key, version_field) ~= expected_version then return 0 end
else
  return redis.error_reply('Unsupported managed operation')
end
local values = {}
for i = 5, #ARGV do values[#values + 1] = ARGV[i] end
values[#values + 1] = version_field
values[#values + 1] = new_version
redis.call('HSET', key, unpack(values))
return 1
'''


def redis_key(raw, tenant, request):
    key = record_key(request.get('key'))
    namespace = name(raw.get('namespace'))
    # JSON key hashing keeps integer 1 distinct from string "1". Each tenant has
    # a separate key space even when the DB uses a dedicated isolation binding.
    return namespace + ':' + name(request['resource']) + ':' + digest(tenant) + ':' + digest(key)


def validate_redis_request(raw, tenant, request):
    redis_key(raw, tenant, request)
    if request['operation'] == 'read' and 'key' not in request:
        raise ValueError('Redis reads require an exact record key')


def redis_execute(raw, tenant, request, internal_path=None):
    validate_endpoint(raw)
    validate_redis_request(raw, tenant, request)
    redis = importlib.import_module('redis')
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    client = redis.Redis(host=raw['host'], port=raw['port'], db=raw['database'], username=raw['user'],
                         password=_credential(raw), ssl=True, ssl_cert_reqs='required', ssl_check_hostname=True,
                         socket_connect_timeout=5, socket_timeout=3, decode_responses=True,
                         retry=Retry(NoBackoff(), 0), retry_on_timeout=False, retry_on_error=[])
    try:
        key = redis_key(raw, tenant, request)
        if request['operation'] == 'read':
            import json
            from .transports import _rows
            # A single HMGET is atomic. Include version to distinguish a missing
            # record from a legitimate projection containing all null fields.
            version = raw['resources'][request['resource']]['version_field']
            columns = request['fields'] + [version]
            cells = client.hmget(key, columns)
            if len(cells) != len(columns):
                raise RuntimeError('Invalid Redis read response')
            if cells[-1] is None:
                return {'rows': [], 'returned': 0, 'read_only': True}
            if any(value is not None and (not isinstance(value, str) or len(value.encode('utf-8')) > 16000) for value in cells):
                raise ValueError('Redis cell exceeds byte limit')
            values = [None if value is None else json.loads(value) for value in cells[:-1]]
            return _rows([values], request['fields'])
        data = mutations(raw, tenant, request)
        version_field = raw['resources'][request['resource']]['version_field']
        data.pop(version_field, None)
        version = 1 if request['operation'] == 'insert' else request['expected_version'] + 1
        argv = [request['operation'], version_field, str(request.get('expected_version', 0)), str(version)]
        for field, value in data.items():
            argv.extend([field, encode(value)])
        result = client.eval(WRITE_SCRIPT, 1, key, *argv)
        if result != 1:
            raise Conflict('Redis key exists, is missing or version changed')
        return _receipt(request)
    finally:
        client.close()
