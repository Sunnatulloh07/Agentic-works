"""Neo4j direct TLS, property-only nodes, explicit transactions with no retry.

No graph traversal, procedures, relationship writes or model-supplied Cypher.
Lock/constraint concurrency and plugin side effects require native acceptance.
"""
import importlib
from ..engine import Conflict, Forbidden
from .contract import MAX_INTEGER, name, scalar, mutations
from .transports import _credential, _receipt, _rows
from .graph_search_common import external, endpoint, validate_graph_search


def quoted(value):return '`'+name(value)+'`'


def identity(raw, tenant, request):
    out={raw['resources'][request['resource']]['key_field']:request['key']}
    if raw['isolation']=='tenant_column':out[raw['tenant_column']]=tenant
    return out


def compile_cypher(raw, tenant, request):
    spec=raw['resources'][request['resource']];label=quoted(request['resource'])
    ids=identity(raw,tenant,request);params={'identity':ids}
    conditions=' AND '.join('n.'+quoted(f)+' = $identity.'+quoted(f) for f in ids)
    fields=list(dict.fromkeys(request.get('fields',[])+list(ids)+[spec['version_field']]))
    projection=','.join('n.'+quoted(f)+' AS '+quoted(f) for f in fields)
    if request['operation']=='insert':
        params['values']=mutations(raw,tenant,request)
        return 'CREATE (n:'+label+') SET n = $values RETURN '+projection,params,fields
    query='MATCH (n:'+label+') WHERE '+conditions+' '
    if request['operation']=='update':
        v='n.'+quoted(spec['version_field'])
        # Direct read/write dependency obtains the node write lock before the
        # following WITH/WHERE re-evaluates the expected version. No lost-update
        # guarantee is claimed until this contract is tested against native Neo4j.
        query+='SET '+v+' = '+v+' WITH n WHERE '+v+' = $expected '
        query+='SET n += $values, '+v+' = $next '
        params.update(expected=request['expected_version'],next=request['expected_version']+1,values=request['values'])
    return query+'RETURN '+projection+' LIMIT 2',params,fields


def _unique(tx,raw,request):
    result=external(tx.run,'SHOW CONSTRAINTS YIELD type, entityType, labelsOrTypes, properties RETURN type, entityType, labelsOrTypes, properties')
    records=external(result.fetch,101)
    if len(records)>100:raise Forbidden('Neo4j metadata exceeds bounded preview scope')
    spec=raw['resources'][request['resource']]
    key={spec['key_field']};scoped=key|({raw['tenant_column']} if raw['isolation']=='tenant_column' else set())
    for r in records:
        if (r.get('entityType')=='NODE' and r.get('labelsOrTypes')==[request['resource']] and
            r.get('type') in {'UNIQUENESS','NODE_PROPERTY_UNIQUENESS','NODE_KEY'} and set(r.get('properties',[])) in (key,scoped)):
            return
    raise Forbidden('Database-enforced unique Neo4j node identity required')


def neo4j_execute(raw,tenant,request,internal_path=None):
    validate_graph_search(raw,tenant,request)
    driver=session=tx=None
    committed=False
    try:
        module=importlib.import_module('neo4j')
        driver=external(module.GraphDatabase.driver,endpoint(raw,'bolt+s'),auth=(raw['user'],_credential(raw)),
            max_transaction_retry_time=0,max_connection_pool_size=1,connection_timeout=5,connection_acquisition_timeout=5)
        session=external(driver.session,database=raw['database'],default_access_mode='READ' if request['operation']=='read' else 'WRITE')
        tx=external(session.begin_transaction,timeout=3)
        _unique(tx,raw,request)
        query,params,fields=compile_cypher(raw,tenant,request)
        result=external(tx.run,query,params)
        records=external(result.fetch,2)
        if len(records)>1:raise Conflict('Neo4j identity returned multiple nodes')
        if request['operation']!='read' and len(records)!=1:raise Conflict('Neo4j record missing or version changed')
        spec=raw['resources'][request['resource']]
        for r in records:
            if any(type(r.get(k)) is not type(v) or r.get(k)!=v for k,v in identity(raw,tenant,request).items()):
                raise Forbidden('Neo4j record or tenant mismatch')
            version=r.get(spec['version_field'])
            if type(version) is not int or not 1<=version<=MAX_INTEGER:raise Forbidden('Invalid Neo4j version')
            if request['operation']!='read' and version!=_receipt(request)['version']:raise Conflict('Unexpected Neo4j write version')
        out=_rows(([scalar(r.get(f)) for f in request['fields']] for r in records),request['fields']) if request['operation']=='read' else _receipt(request)
        external(tx.commit);committed=True
        return out
    except (ValueError,Forbidden):raise
    except Exception:raise RuntimeError('Neo4j operation failed; reconciliation required') from None
    finally:
        failure=False
        for obj,method in [(tx,'rollback' if not committed else 'close'),(session,'close'),(driver,'close')]:
            if obj is not None:
                try:getattr(obj,method)()
                except Exception:failure=True
        if failure:raise RuntimeError('Neo4j cleanup failed; reconciliation required') from None
