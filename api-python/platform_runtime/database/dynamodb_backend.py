"""Exact-key DynamoDB API transport, source/contract preview, NOT live accepted.

A shared table MUST use (tenant attribute HASH, record attribute RANGE).
Dedicated tables use the record attribute HASH and an explicit tenant binding.
No Scan, Query, PartiQL, batch, upsert, SDK credential discovery or write retry.
"""
import importlib
import os
import re

from ..engine import Conflict, Forbidden
from .contract import MAX_INTEGER, name, record_key, scalar, text, mutations
from .transports import _receipt, _rows


def validate_dynamodb(raw, tenant, request):
    region = raw.get('region')
    if not isinstance(region, str) or not re.fullmatch(r'(?:us|eu|ap|ca|sa|me|af|il|mx)-(?:[a-z]+-)?[a-z]+-[1-9]', region):
        raise ValueError('Explicit commercial AWS region required')
    if not isinstance(raw.get('aws_account_id'), str) or not re.fullmatch(r'[0-9]{12}', raw['aws_account_id']):
        raise ValueError('Pinned AWS account required')
    if any(k in raw for k in ('endpoint_url', 'profile', 'role_arn', 'aws_access_key_id', 'aws_secret_access_key', 'aws_session_token')):
        raise ValueError('Endpoint overrides and inline AWS credentials denied')
    for k in ('access_key_env', 'secret_key_env', 'session_token_env'):
        ref = raw.get(k)
        if k == 'session_token_env' and ref is None:
            continue
        if not isinstance(ref, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', ref) or ref.startswith('DSEC_'):
            raise ValueError('Explicit signing credential reference required; HTTPS placeholders cannot be signed')
    text(tenant, 256)
    if raw.get('isolation') == 'dedicated_database':
        if raw.get('bound_tenant') != tenant or 'tenant_column' in raw:
            raise Forbidden('Dedicated table tenant binding mismatch')
    elif raw.get('isolation') != 'tenant_column':
        raise ValueError('Explicit isolation required')
    key = record_key(request.get('key'))
    if not isinstance(key, str) or len(key.encode('utf-8')) > 1024:
        raise ValueError('DynamoDB preview requires a bounded string record key')
    name(request['resource'])
    if len(request['resource']) < 3:
        raise ValueError('DynamoDB table name requires at least three characters')


def _external(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        response = getattr(exc, 'response', None)
        error = response.get('Error') if isinstance(response, dict) else None
        if isinstance(error, dict) and error.get('Code') == 'ConditionalCheckFailedException':
            raise Conflict('DynamoDB key exists, is missing or version changed') from None
        raise RuntimeError('DynamoDB provider failed; reconciliation required') from None


def _credentials(raw):
    values = {}
    for field, ref in [('aws_access_key_id', 'access_key_env'), ('aws_secret_access_key', 'secret_key_env'),
                       ('aws_session_token', 'session_token_env')]:
        if ref not in raw:
            continue
        value = os.environ.get(raw[ref], '')
        if not value:
            raise RuntimeError('AWS credential unavailable')
        values[field] = value
    return values


def _attribute(value):
    scalar(value)
    if value is None:
        return {'NULL': True}
    if type(value) is bool:
        return {'BOOL': value}
    if type(value) is int:
        return {'N': str(value)}
    return {'S': value}


def _decode(value):
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError('Invalid DynamoDB attribute')
    if set(value) == {'S'} and isinstance(value['S'], str):
        return scalar(value['S'])
    if set(value) == {'N'} and isinstance(value['N'], str) and re.fullmatch(r'-?(?:0|[1-9][0-9]{0,15})', value['N']):
        return scalar(int(value['N']))
    if set(value) == {'BOOL'} and type(value['BOOL']) is bool:
        return value['BOOL']
    if value == {'NULL': True} and type(value.get('NULL')) is bool:
        return None
    raise ValueError('Nonportable DynamoDB value denied')


def _key(raw, tenant, request):
    spec = raw['resources'][request['resource']]
    key = {spec['key_field']: {'S': request['key']}}
    if raw['isolation'] == 'tenant_column':
        key[raw['tenant_column']] = {'S': tenant}
    return key


def _table(client, raw, request):
    table = _external(client.describe_table, TableName=request['resource'])['Table']
    expected = 'arn:aws:dynamodb:' + raw['region'] + ':' + raw['aws_account_id'] + ':table/' + request['resource']
    if table.get('TableArn') != expected or table.get('TableStatus') != 'ACTIVE':
        raise Forbidden('DynamoDB table identity or lifecycle mismatch')
    if table.get('Replicas') or table.get('GlobalTableVersion'):
        raise Forbidden('Global tables require separate multi-region acceptance')
    spec = raw['resources'][request['resource']]
    schema = {spec['key_field']: 'HASH'}
    if raw['isolation'] == 'tenant_column':
        schema = {raw['tenant_column']: 'HASH', spec['key_field']: 'RANGE'}
    keys = table.get('KeySchema', [])
    if len(keys) != len(schema) or {k['AttributeName']: k['KeyType'] for k in keys} != schema:
        raise Forbidden('DynamoDB primary key must enforce tenant isolation')
    types = {k['AttributeName']: k['AttributeType'] for k in table.get('AttributeDefinitions', [])}
    if any(types.get(k) != 'S' for k in schema):
        raise Forbidden('DynamoDB identity attributes must be strings')
    return expected


def _checked_item(raw, tenant, request, item, *, expected_version=None):
    if not isinstance(item, dict):
        raise ValueError('Invalid DynamoDB item')
    spec = raw['resources'][request['resource']]
    for field, expected in _key(raw, tenant, request).items():
        if item.get(field) != expected:
            raise Forbidden('DynamoDB returned another record or tenant')
    version = _decode(item.get(spec['version_field']))
    if type(version) is not int or not 1 <= version <= MAX_INTEGER:
        raise Forbidden('DynamoDB version is not a positive integer')
    if expected_version is not None and version != expected_version:
        raise Conflict('DynamoDB returned an unexpected version')
    return item


def dynamodb_execute(raw, tenant, request, internal_path=None):
    validate_dynamodb(raw, tenant, request)
    client = None
    try:
        boto3 = importlib.import_module('boto3')
        Config = importlib.import_module('botocore.config').Config
        # Explicit credentials prevent implicit metadata/role/profile resolution.
        session = _external(boto3.Session, region_name=raw['region'], **_credentials(raw))
        client = _external(session.client, 'dynamodb', region_name=raw['region'],
            endpoint_url='https://dynamodb.' + raw['region'] + '.amazonaws.com',
            config=Config(connect_timeout=5, read_timeout=3,
                          retries={'mode': 'standard', 'total_max_attempts': 1}))
        table_arn = _table(client, raw, request)
        key = _key(raw, tenant, request)
        spec = raw['resources'][request['resource']]
        if request['operation'] == 'read':
            fields = list(dict.fromkeys(request['fields'] + list(key) + [spec['version_field']]))
            aliases = {'#f' + str(i): field for i, field in enumerate(fields)}
            response = _external(client.get_item, TableName=table_arn, Key=key, ConsistentRead=True,
                ProjectionExpression=','.join(aliases), ExpressionAttributeNames=aliases)
            if 'Item' not in response:
                return _rows([], request['fields'])
            item = _checked_item(raw, tenant, request, response['Item'])
            return _rows([[_decode(item[f]) if f in item else None for f in request['fields']]], request['fields'])
        aliases = {'#k' + str(i): field for i, field in enumerate(key)}
        if request['operation'] == 'insert':
            item = {field: _attribute(value) for field, value in mutations(raw, tenant, request).items()}
            response = _external(client.put_item, TableName=table_arn, Item=item,
                ConditionExpression=' AND '.join('attribute_not_exists(' + a + ')' for a in aliases),
                ExpressionAttributeNames=aliases, ReturnValues='NONE')
        else:
            aliases['#v'] = spec['version_field']
            values = {':expected': {'N': str(request['expected_version'])},
                      ':next': {'N': str(request['expected_version'] + 1)}}
            assignments = ['#v = :next']
            for i, (field, value) in enumerate(request['values'].items()):
                alias, parameter = '#a' + str(i), ':a' + str(i)
                aliases[alias] = field
                values[parameter] = _attribute(value)
                assignments.append(alias + ' = ' + parameter)
            response = _external(client.update_item, TableName=table_arn, Key=key,
                UpdateExpression='SET ' + ', '.join(assignments),
                ConditionExpression=' AND '.join('attribute_exists(#k' + str(i) + ')' for i in range(len(key))) + ' AND #v = :expected',
                ExpressionAttributeNames=aliases, ExpressionAttributeValues=values, ReturnValues='UPDATED_NEW')
            attrs = response.get('Attributes', {})
            if attrs.get(spec['version_field']) != values[':next']:
                raise RuntimeError('DynamoDB update receipt unavailable')
        if response.get('ResponseMetadata', {}).get('HTTPStatusCode') != 200:
            raise RuntimeError('DynamoDB write acknowledgement unavailable')
        return _receipt(request)
    except (Forbidden, Conflict, ValueError):
        raise
    except Exception as exc:
        response = getattr(exc, 'response', None)
        if isinstance(response, dict) and response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            raise Conflict('DynamoDB key exists, is missing or version changed') from None
        raise RuntimeError('DynamoDB operation failed; reconcile before retry') from None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                raise RuntimeError('DynamoDB cleanup failed; reconcile before retry') from None
