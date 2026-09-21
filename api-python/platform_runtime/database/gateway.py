"""Engine integration: configuration-bound approvals and one-shot write dispatch."""
import json
import sqlite3
from ..engine import Conflict, Forbidden, digest, encode
from ..tools import config
from .contract import (BACKENDS, EXECUTABLE, catalog, normalize, parse_request,
                       plan_fingerprint, text, validate_config)
from .transports import TRANSPORTS, validate_endpoint
from .redis_backend import redis_execute, validate_redis_request
from .mysql_backend import mysql_execute
from .sqlserver_backend import sqlserver_execute
from .oracle_backend import oracle_execute
from .neo4j_backend import neo4j_execute
from .elasticsearch_backend import elasticsearch_execute
from .graph_search_common import validate_graph_search
from .enterprise_common import validate_enterprise_request
from .dynamodb_backend import dynamodb_execute, validate_dynamodb
from .cassandra_backend import cassandra_execute, validate_cassandra
TRANSPORTS.update(redis_managed=redis_execute, mysql_managed=mysql_execute, mariadb_managed=mysql_execute,
                  sqlserver_managed=sqlserver_execute, oracle_managed=oracle_execute,
                  dynamodb_managed=dynamodb_execute, cassandra_managed=cassandra_execute,
                  neo4j_managed=neo4j_execute, elasticsearch_managed=elasticsearch_execute)

TOOLS = {'database.read', 'database.plan_write', 'database.write'}


def prepare(tenant, agent, tool, args):
    if tool not in TOOLS or not isinstance(args, dict):
        raise ValueError('Invalid managed database tool')
    required = {'connection', 'request_json'} | ({'plan_fingerprint'} if tool == 'database.write' else set())
    if set(args) != required:
        raise ValueError('Invalid managed database arguments')
    connection = text(args.get('connection'))
    try:
        entries = config(tenant).get('connections', {})
        raw = json.loads(encode(entries.get(connection)))
    except (OSError, AttributeError, TypeError, ValueError, RuntimeError):
        raise Forbidden('Database configuration unavailable') from None
    capability = {'database.read': 'read', 'database.plan_write': 'plan_write',
                  'database.write': 'execute_write'}[tool]
    validate_config(raw, tenant=tenant, agent=agent, capability=capability)
    request = normalize(raw, tenant, parse_request(args['request_json']))
    if (tool == 'database.read') != (request['operation'] == 'read'):
        raise ValueError('Database operation does not match tool risk')
    if tool != 'database.plan_write' and raw['driver'] not in EXECUTABLE:
        raise Forbidden('Database transport is not implemented')
    if raw['driver'] in {'postgres_managed', 'mongodb_managed', 'redis_managed', 'mysql_managed', 'mariadb_managed', 'sqlserver_managed', 'oracle_managed'}:
        validate_endpoint(raw)
    if raw['driver'] in {'sqlserver_managed', 'oracle_managed'}:
        validate_enterprise_request(raw, request)
    if raw['driver'] == 'redis_managed':
        validate_redis_request(raw, tenant, request)
    if raw['driver'] in {'neo4j_managed','elasticsearch_managed'}:
        validate_graph_search(raw,tenant,request)
    if raw['driver'] == 'dynamodb_managed':
        validate_dynamodb(raw, tenant, request)
    if raw['driver'] == 'cassandra_managed':
        validate_cassandra(raw, tenant, request)
    fingerprint = plan_fingerprint(tenant, agent, connection, raw, request)
    if tool == 'database.write':
        if 'plan_write' not in raw['capabilities']:
            raise Forbidden('Write planning capability required')
        if args.get('plan_fingerprint') != fingerprint:
            raise Conflict('Write plan changed; re-plan and re-approve')
    return raw, request, fingerprint


def validate_step(tenant, agent, tool, args):
    raw, request, fingerprint = prepare(tenant, agent, tool, args)
    return {'database_configuration': digest(raw), 'database_request': fingerprint}


def _allowed(engine, tenant, agent, tool, args):
    policy = engine.policy(tenant, agent)
    if tool not in policy.get('tools', []) or args.get('connection') not in policy.get('allowed_connections', []):
        raise Forbidden('Database tool or connection not permitted for agent')


def tool_plan(engine, tenant, agent, args, step):
    _allowed(engine, tenant, agent, 'database.plan_write', args)
    raw, request, fingerprint = prepare(tenant, agent, 'database.plan_write', args)
    return {'connection': args['connection'], 'driver': raw['driver'],
            'resource': request['resource'], 'operation': request['operation'],
            'key': request['key'], 'changes': request['values'],
            'expected_version': request.get('expected_version'),
            'maximum_affected': 1, 'approval_required': True,
            'transport_implemented': raw['driver'] in EXECUTABLE,
            'live_verified': False, 'plan_fingerprint': fingerprint}


def tool_read(engine, tenant, agent, args, step):
    _allowed(engine, tenant, agent, 'database.read', args)
    raw, request, _ = prepare(tenant, agent, 'database.read', args)
    result = TRANSPORTS[raw['driver']](raw, tenant, request, engine.path)
    return {**result, 'connection': args['connection'], 'driver': raw['driver']}


def tool_write(engine, tenant, agent, args, step):
    _allowed(engine, tenant, agent, 'database.write', args)
    raw, request, fingerprint = prepare(tenant, agent, 'database.write', args)
    # Claim the single provider dispatch durably BEFORE crossing the customer DB
    # boundary. Crashes/uncertain writes cannot be replayed by calling the handler.
    with engine.tx() as db:
        engine.require_active(db, tenant)
        row = db.execute('SELECT s.*,t.agent,t.actor,t.channel FROM p_steps s JOIN p_tasks t '
                         'ON t.id=s.task AND t.tenant=s.tenant WHERE s.tenant=? AND s.id=?',
                         (tenant, step)).fetchone()
        if (not row or row['tool'] != 'database.write' or row['agent'] != agent
                or row['status'] != 'running' or row['lease'] <= engine.clock()
                or row['args'] != encode(args) or not row['approval_needed']):
            raise Forbidden('Active approved database write step required')
        engine.require_task_parent(db, tenant, row['task'])
        engine.require_authority(db, tenant, row['channel'], row['actor'])
        current = engine._validated(tenant, agent, [{'tool': 'database.write', 'args': args}])[0]
        if current[4] != row['fingerprint']:
            raise Forbidden('Database write authority changed after approval')
        approval = db.execute('SELECT * FROM p_approvals WHERE tenant=? AND step=?', (tenant, step)).fetchone()
        if (not approval or approval['status'] != 'consumed' or approval['expires'] <= engine.clock()
                or approval['fingerprint'] != row['fingerprint']):
            raise Forbidden('Consumed unexpired matching approval required')
        role = ('owner',) if engine.policy(tenant, agent).get('approver_role') == 'owner' else ('owner', 'operator')
        engine.require_authority(db, tenant, 'approval', approval['actor'], role)
        try:
            db.execute('INSERT INTO p_database_dispatch(tenant,step,task,fingerprint,status,created,updated) '
                       "VALUES(?,?,?,?,'dispatching',?,?)",
                       (tenant, step, row['task'], fingerprint, engine.clock(), engine.clock()))
        except sqlite3.IntegrityError:
            raise Conflict('Database step already dispatched; reconcile instead of retrying') from None
        engine.audit(db, tenant, row['task'], 'database.write.dispatching', row['actor'],
                     {'step': step, 'connection': args['connection'], 'driver': raw['driver'],
                      'operation': request['operation'], 'plan_fingerprint': fingerprint})
    try:
        result = TRANSPORTS[raw['driver']](raw, tenant, request, engine.path)
    except Exception:
        with engine.tx() as db:
            db.execute("UPDATE p_database_dispatch SET status='uncertain',updated=? WHERE tenant=? AND step=?",
                       (engine.clock(), tenant, step))
            engine.audit(db, tenant, row['task'], 'database.write.uncertain', 'database', {'step': step})
        raise
    receipt = {**result, 'connection': args['connection'], 'driver': raw['driver'],
               'dispatch_id': step, 'plan_fingerprint': fingerprint, 'automatic_retry': False}
    with engine.tx() as db:
        db.execute("UPDATE p_database_dispatch SET status='committed',receipt=?,updated=? WHERE tenant=? AND step=?",
                   (encode(receipt), engine.clock(), tenant, step))
        engine.audit(db, tenant, row['task'], 'database.write.committed', 'database',
                     {'step': step, 'affected': result['affected'], 'plan_fingerprint': fingerprint})
    return receipt


def tool_catalog(engine, tenant, agent, args, step):
    return {'contract_version': '1.1', 'backends': catalog(),
            'universal_native_support': False, 'raw_queries_allowed': False,
            'writes_require_approval': True}


def describe_managed(connection, raw):
    backend = BACKENDS[raw['driver']]
    return {'id': connection, 'driver': raw['driver'], 'contract_version': raw.get('contract_version'),
            'lifecycle': raw.get('lifecycle'), 'capabilities': raw.get('capabilities', []),
            'status': 'revoked' if raw.get('lifecycle') == 'revoked' else backend.stage,
            'mode': 'managed_approved_operations', 'agent_ids': raw.get('agent_ids', []),
            'transport_implemented': raw['driver'] in EXECUTABLE, 'live_verified': False}
