"""Exact-ID Elasticsearch documents with create and seq_no/primary_term CAS.

No full-text query, bulk, script, ingest pipeline, index creation or upsert.
Concrete strict-mapping indices only. Cluster metadata races need live acceptance.
"""
import importlib
from ..engine import Conflict, Forbidden, digest, encode
from .contract import MAX_INTEGER, mutations, scalar
from .transports import _credential, _rows, _receipt
from .graph_search_common import external, endpoint, validate_graph_search


def document_id(raw,tenant,request):
    return digest({'tenant':tenant,'resource':request['resource'],'key':request['key']})


def _schema(client,raw,request):
    index=request['resource']
    mappings=external(client.indices.get_mapping,index=index)
    settings=external(client.indices.get_settings,index=index)
    if set(mappings)!={index} or set(settings)!={index}:
        raise Forbidden('Concrete Elasticsearch index required; aliases and wildcards denied')
    mapping=mappings[index].get('mappings',{})
    if mapping.get('dynamic') not in ('strict',False) or mapping.get('runtime'):
        raise Forbidden('Explicit strict Elasticsearch mapping required')
    source=mapping.get('_source',{})
    if source.get('enabled',True) is not True or source.get('includes') or source.get('excludes'):
        raise Forbidden('Complete Elasticsearch source storage required')
    props=mapping.get('properties',{})
    spec=raw['resources'][index]
    fields=set(spec['read_fields'])|({raw['tenant_column']} if raw['isolation']=='tenant_column' else set())
    if not fields.issubset(props):raise Forbidden('Configured Elasticsearch fields missing')
    types={f:props[f].get('type') for f in fields}
    if any(t not in {'keyword','text','long','boolean'} for t in types.values()):raise Forbidden('Only scalar Elasticsearch fields supported')
    identities=[spec['key_field']]+([raw['tenant_column']] if raw['isolation']=='tenant_column' else [])
    if any(types[f]!='keyword' for f in identities) or types[spec['version_field']]!='long':
        raise Forbidden('Elasticsearch identity requires keyword and version long')
    options=settings[index].get('settings',{}).get('index',{})
    if any(options.get(f,'_none')!='_none' for f in ('default_pipeline','final_pipeline')):
        raise Forbidden('Elasticsearch ingest pipelines denied for managed documents')
    for field,value in request.get('values',{}).items():
        if value is None:continue
        typ=types[field]
        if ((typ in {'keyword','text'} and not isinstance(value,str)) or
            (typ=='long' and type(value) is not int) or (typ=='boolean' and type(value) is not bool)):
            raise ValueError('Elasticsearch value type does not match mapping')


def _get(client,raw,tenant,request):
    try:result=client.get(index=request['resource'],id=document_id(raw,tenant,request),realtime=True)
    except Exception as exc:
        if getattr(exc,'status_code',None)==404:return None
        raise RuntimeError('Elasticsearch read failed') from None
    if result.get('found') is False:return None
    if result.get('found') is not True:raise RuntimeError('Elasticsearch found flag unavailable')
    if result.get('_index')!=request['resource'] or result.get('_id')!=document_id(raw,tenant,request):
        raise Forbidden('Elasticsearch document identity mismatch')
    doc=result.get('_source');spec=raw['resources'][request['resource']]
    if not isinstance(doc,dict) or len(encode(doc).encode())>16000:raise ValueError('Elasticsearch document size limit')
    if type(doc.get(spec['key_field'])) is not str or doc[spec['key_field']]!=request['key']:
        raise Forbidden('Elasticsearch record key mismatch')
    if raw['isolation']=='tenant_column' and doc.get(raw['tenant_column'])!=tenant:raise Forbidden('Elasticsearch tenant mismatch')
    v=doc.get(spec['version_field'])
    if type(v) is not int or not 1<=v<=MAX_INTEGER:raise Forbidden('Invalid Elasticsearch version')
    for field in ('_seq_no','_primary_term'):
        if type(result.get(field)) is not int or result[field]<(1 if field=='_primary_term' else 0):
            raise Forbidden('Native Elasticsearch concurrency tokens required')
    return result


def elasticsearch_execute(raw,tenant,request,internal_path=None):
    validate_graph_search(raw,tenant,request)
    client=None
    try:
        module=importlib.import_module('elasticsearch')
        client=external(module.Elasticsearch,endpoint(raw,'https'),basic_auth=(raw['user'],_credential(raw)),
            request_timeout=3,max_retries=0,retry_on_timeout=False,
            sniff_on_start=False,sniff_before_requests=False,sniff_on_node_failure=False)
        _schema(client,raw,request)
        if request['operation']=='read':
            current=_get(client,raw,tenant,request)
            records=[] if current is None else [[scalar(current['_source'].get(f)) for f in request['fields']]]
            return _rows(records,request['fields'])
        if request['operation']=='insert':
            result=external(client.create,index=request['resource'],id=document_id(raw,tenant,request),
                document=mutations(raw,tenant,request),refresh='wait_for',pipeline='_none',wait_for_active_shards='all')
            expected='created'
        else:
            current=_get(client,raw,tenant,request)
            spec=raw['resources'][request['resource']]
            if current is None or current['_source'][spec['version_field']]!=request['expected_version']:
                raise Conflict('Elasticsearch record missing or version changed')
            doc={**current['_source'],**request['values'],spec['version_field']:request['expected_version']+1}
            if len(encode(doc).encode('utf-8'))>16000:raise ValueError('Updated Elasticsearch document exceeds managed read limit')
            result=external(client.index,index=request['resource'],id=document_id(raw,tenant,request),document=doc,
                if_seq_no=current['_seq_no'],if_primary_term=current['_primary_term'],
                refresh='wait_for',pipeline='_none',wait_for_active_shards='all')
            expected='updated'
        if (result.get('result')!=expected or result.get('_index')!=request['resource'] or
            result.get('_id')!=document_id(raw,tenant,request) or result.get('_shards',{}).get('failed')!=0 or
            type(result.get('_shards',{}).get('successful')) is not int or result['_shards']['successful']<1):
            raise RuntimeError('Elasticsearch write acknowledgement unavailable')
        return _receipt(request)
    except (ValueError,Forbidden):raise
    except Exception:raise RuntimeError('Elasticsearch operation failed; reconciliation required') from None
    finally:
        if client is not None:
            try:client.close()
            except Exception:raise RuntimeError('Elasticsearch cleanup failed; reconciliation required') from None
