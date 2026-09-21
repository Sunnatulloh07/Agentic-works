"""Fake SDK contracts only. Native graph locking/search mapping acceptance deferred."""
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from platform_runtime.database.contract import normalize,catalog
from platform_runtime.database.neo4j_backend import neo4j_execute
from platform_runtime.database.elasticsearch_backend import elasticsearch_execute,document_id
from platform_runtime.database.gateway import tool_plan,tool_write,TRANSPORTS
from platform_runtime.engine import Engine,Forbidden,Conflict,encode
from platform_runtime.tools import build_registry
from test_managed_nosql_contracts import config as base_config,request,TEST_ENV


def config(driver='neo4j_managed'):
    raw=base_config(driver)
    raw.update(host='database.example',allowed_hosts=['database.example'],port=7687 if driver=='neo4j_managed' else 9200)
    return raw


class NeoFixture:
    def __init__(self):
        self.driver=MagicMock();self.session=MagicMock();self.tx=MagicMock()
        self.driver.session.return_value=self.session;self.session.begin_transaction.return_value=self.tx
        self.constraints=[{'type':'UNIQUENESS','entityType':'NODE','labelsOrTypes':['contacts'],'properties':['tenant_id','id']}]
        self.records=[{'id':'one','tenant_id':'a','version':2,'name':'Vali'}]
        self.calls=[]
        def run(query,params=None):
            self.calls.append((query,params));out=MagicMock()
            out.fetch.side_effect=lambda n:(self.constraints if query.startswith('SHOW ') else self.records)[:n]
            return out
        self.tx.run.side_effect=run
        self.module=types.SimpleNamespace(GraphDatabase=types.SimpleNamespace(driver=MagicMock(return_value=self.driver)))
        self.modules={'neo4j':self.module}


class ESFixture:
    def __init__(self):
        self.client=MagicMock()
        self.props={n:{'type':t} for n,t in [('id','keyword'),('tenant_id','keyword'),('version','long'),('name','text'),('active','boolean')]}
        self.mapping={'dynamic':'strict','properties':self.props}
        self.settings={}
        self.client.indices.get_mapping.return_value={'contacts':{'mappings':self.mapping}}
        self.client.indices.get_settings.return_value={'contacts':{'settings':{'index':self.settings}}}
        self.id=document_id(config('elasticsearch_managed'),'a',request())
        self.source={'id':'one','tenant_id':'a','version':1,'name':'Ali'}
        self.current={'found':True,'_index':'contacts','_id':self.id,'_source':self.source,'_seq_no':17,'_primary_term':2}
        self.client.get.return_value=self.current
        self.ack={'_index':'contacts','_id':self.id,'_shards':{'successful':2,'failed':0}}
        self.client.create.return_value={**self.ack,'result':'created'}
        self.client.index.return_value={**self.ack,'result':'updated'}
        self.module=types.SimpleNamespace(Elasticsearch=MagicMock(return_value=self.client))
        self.modules={'elasticsearch':self.module}


class NeoContracts(unittest.TestCase):
    def setUp(self):self.fake=NeoFixture()
    def run_db(self,operation='update',raw=None,extra=None):
        raw=raw or config()
        if operation=='insert':self.fake.records[0]['version']=1
        with patch.dict('sys.modules',self.fake.modules),patch.dict('os.environ',TEST_ENV):
            return neo4j_execute(raw,'a',normalize(raw,'a',request(operation,**(extra or {}))))
    def test_direct_verified_tls_no_transaction_retry(self):
        self.run_db();args=self.fake.module.GraphDatabase.driver.call_args
        self.assertEqual('bolt+s://database.example:7687',args.args[0]);self.assertEqual(0,args.kwargs['max_transaction_retry_time'])
        self.fake.tx.commit.assert_called_once();self.fake.driver.close.assert_called_once()
    def test_lock_dependency_before_version_check(self):
        self.assertEqual(2,self.run_db()['version'])
        query,params=self.fake.calls[-1]
        self.assertIn('SET n.`version` = n.`version` WITH n WHERE n.`version` = $expected',query)
        self.assertEqual({'id':'one','tenant_id':'a'},params['identity']);self.assertEqual(1,params['expected'])
        self.assertEqual(2,params['next'])
    def test_insert_unique_create_not_merge(self):
        self.run_db('insert');query,params=self.fake.calls[-1]
        self.assertTrue(query.startswith('CREATE '));self.assertNotIn('MERGE',query)
        self.assertEqual('a',params['values']['tenant_id']);self.assertEqual(1,params['values']['version'])
    def test_read_projection_and_limit(self):
        result=self.run_db('read');self.assertEqual([{'id':'one','name':'Vali'}],result['rows'])
        self.assertTrue(self.fake.calls[-1][0].endswith('LIMIT 2'))
        self.assertEqual('READ',self.fake.driver.session.call_args.kwargs['default_access_mode'])
    def test_missing_unique_constraint_denied(self):
        self.fake.constraints=[]
        with self.assertRaises(Forbidden):self.run_db()
        self.assertEqual(1,len(self.fake.calls));self.fake.tx.rollback.assert_called_once()
    def test_unrelated_constraint_denied(self):
        self.fake.constraints[0]['properties']=['name']
        with self.assertRaises(Forbidden):self.run_db()
    def test_no_record_version_conflict(self):
        self.fake.records=[]
        with self.assertRaises(Conflict):self.run_db()
        self.fake.tx.commit.assert_not_called()
    def test_wrong_returned_tenant_denied(self):
        self.fake.records[0]['tenant_id']='b'
        with self.assertRaises(Forbidden):self.run_db()
    def test_wrong_version_denied(self):
        self.fake.records[0]['version']=99
        with self.assertRaises(Conflict):self.run_db()
    def test_multiple_records_roll_back(self):
        self.fake.records*=2
        with self.assertRaises(Conflict):self.run_db()
        self.fake.tx.rollback.assert_called_once()
    def test_provider_failure_sanitized(self):
        self.fake.tx.run.side_effect=ValueError('driver-secret')
        with self.assertRaises(RuntimeError) as caught:self.run_db()
        self.assertNotIn('secret',str(caught.exception));self.fake.driver.close.assert_called_once()
    def test_commit_failure_uncertain(self):
        self.fake.tx.commit.side_effect=RuntimeError('secret')
        with self.assertRaises(RuntimeError):self.run_db()
        self.fake.tx.rollback.assert_called_once();self.fake.driver.close.assert_called_once()
    def test_sensitive_schema_and_tls_denied(self):
        for extra in [{'database':'system'},{'tls':False},{'host':'evil'}]:
            with self.subTest(extra=extra),self.assertRaises((Forbidden,ValueError)):self.run_db(raw={**config(),**extra})
        self.fake.module.GraphDatabase.driver.assert_not_called()
    def test_cypher_injection_bound_as_data(self):
        attack="x' MATCH (n) DETACH DELETE n"
        self.run_db(extra={'values':{'name':attack}})
        query,params=self.fake.calls[-1];self.assertNotIn(attack,query);self.assertEqual(attack,params['values']['name'])
    def test_dedicated_binding_has_no_tenant_predicate(self):
        raw=config();raw.pop('tenant_column');raw.update(isolation='dedicated_database',bound_tenant='a')
        self.fake.constraints[0]['properties']=['id'];self.run_db(raw=raw)
        self.assertNotIn('tenant_id',self.fake.calls[-1][1]['identity'])
    def test_missing_read_returns_empty(self):
        self.fake.records=[];self.assertEqual([],self.run_db('read')['rows'])
    def test_complex_returned_property_denied(self):
        self.fake.records[0]['name']=['bad']
        with self.assertRaises(ValueError):self.run_db('read')
    def test_cleanup_failure_still_closes_driver(self):
        self.fake.session.close.side_effect=RuntimeError('secret')
        with self.assertRaises(RuntimeError):self.run_db()
        self.fake.driver.close.assert_called_once()


class ESContracts(unittest.TestCase):
    def setUp(self):self.fake=ESFixture()
    def run_db(self,operation='update',raw=None,extra=None):
        raw=raw or config('elasticsearch_managed')
        with patch.dict('sys.modules',self.fake.modules),patch.dict('os.environ',TEST_ENV):
            return elasticsearch_execute(raw,'a',normalize(raw,'a',request(operation,**(extra or {}))))
    def test_https_no_sniff_or_retry(self):
        self.run_db();call=self.fake.module.Elasticsearch.call_args
        self.assertEqual('https://database.example:9200',call.args[0]);self.assertEqual(0,call.kwargs['max_retries'])
        self.assertFalse(call.kwargs['sniff_on_start']);self.assertFalse(call.kwargs['retry_on_timeout'])
        self.fake.client.close.assert_called_once()
    def test_insert_create_only_scoped_id(self):
        self.assertEqual(1,self.run_db('insert')['version']);kw=self.fake.client.create.call_args.kwargs
        self.assertEqual(self.fake.id,kw['id']);self.assertEqual('a',kw['document']['tenant_id']);self.assertEqual(1,kw['document']['version'])
        self.assertEqual('_none',kw['pipeline'])
    def test_update_native_cas_and_version(self):
        self.assertEqual(2,self.run_db()['version']);kw=self.fake.client.index.call_args.kwargs
        self.assertEqual(17,kw['if_seq_no']);self.assertEqual(2,kw['if_primary_term']);self.assertEqual(2,kw['document']['version'])
        self.assertEqual('Vali',kw['document']['name']);self.assertEqual('a',kw['document']['tenant_id'])
    def test_read_realtime_exact_and_bounded(self):
        self.assertEqual([{'id':'one','name':'Ali'}],self.run_db('read')['rows'])
        self.assertTrue(self.fake.client.get.call_args.kwargs['realtime']);self.fake.client.search.assert_not_called()
    def test_cross_tenant_hashes_differ(self):
        self.assertNotEqual(document_id(config(),'a',request()),document_id(config(),'b',request()))
    def test_mismatched_source_tenant_denied(self):
        self.fake.source['tenant_id']='b'
        with self.assertRaises(Forbidden):self.run_db()
        self.fake.client.index.assert_not_called()
    def test_wrong_native_id_denied(self):
        self.fake.current['_id']='wrong'
        with self.assertRaises(Forbidden):self.run_db()
    def test_no_native_concurrency_token_denied(self):
        del self.fake.current['_seq_no']
        with self.assertRaises(Forbidden):self.run_db()
    def test_wrong_expected_version_denied_before_index(self):
        self.fake.source['version']=3
        with self.assertRaises(Conflict):self.run_db()
        self.fake.client.index.assert_not_called()
    def test_version_conflict_from_provider_not_retried(self):
        exc=RuntimeError('secret');exc.status_code=409;self.fake.client.index.side_effect=exc
        with self.assertRaises(Conflict):self.run_db()
        self.fake.client.index.assert_called_once()
    def test_missing_document_read(self):
        exc=RuntimeError('secret');exc.status_code=404;self.fake.client.get.side_effect=exc
        self.assertEqual([],self.run_db('read')['rows'])
    def test_missing_document_update_cannot_create(self):
        self.fake.client.get.return_value={'found':False}
        with self.assertRaises(Conflict):self.run_db()
        self.fake.client.index.assert_not_called()
    def test_alias_denied(self):
        self.fake.client.indices.get_mapping.return_value={'real_contacts':{'mappings':self.fake.mapping}}
        with self.assertRaises(Forbidden):self.run_db()
    def test_ingest_pipeline_denied(self):
        self.fake.settings['final_pipeline']='mutate-record'
        with self.assertRaises(Forbidden):self.run_db()
    def test_runtime_and_dynamic_mapping_denied(self):
        self.fake.mapping['runtime']={'field':{'type':'keyword'}}
        with self.assertRaises(Forbidden):self.run_db()
    def test_source_filters_denied(self):
        self.fake.mapping['_source']={'excludes':['tenant_id']}
        with self.assertRaises(Forbidden):self.run_db()
    def test_wrong_field_type_denied(self):
        with self.assertRaises(ValueError):self.run_db(extra={'values':{'name':True}})
        self.fake.client.index.assert_not_called()
    def test_acknowledgement_failure_not_success(self):
        self.fake.client.index.return_value['_shards']={'failed':1,'successful':1}
        with self.assertRaises(RuntimeError):self.run_db()
    def test_wrong_ack_id_not_success(self):
        self.fake.client.index.return_value['_id']='wrong'
        with self.assertRaises(RuntimeError):self.run_db()
    def test_provider_valueerror_sanitized(self):
        self.fake.client.index.side_effect=ValueError('secret')
        with self.assertRaises(RuntimeError) as caught:self.run_db()
        self.assertNotIn('secret',str(caught.exception))
    def test_dedicated_binding(self):
        raw=config('elasticsearch_managed');raw.pop('tenant_column');raw.update(isolation='dedicated_database',bound_tenant='a')
        self.run_db('insert',raw=raw);self.assertNotIn('tenant_id',self.fake.client.create.call_args.kwargs['document'])
    def test_missing_found_flag_is_not_silent_empty(self):
        self.fake.client.get.return_value={}
        with self.assertRaises(RuntimeError):self.run_db('read')
    def test_merged_document_limit_checked_before_write(self):
        self.fake.source['unmodified']='x'*15500
        with self.assertRaises(ValueError):self.run_db(extra={'values':{'name':'v'*1000}})
        self.fake.client.index.assert_not_called()
    def test_invalid_endpoint_denied(self):
        for extra in [{'tls':False},{'host':'evil'},{'password_env':'DSEC_SECRET'}]:
            with self.subTest(extra=extra),self.assertRaises((ValueError,Forbidden)):self.run_db(raw={**config('elasticsearch_managed'),**extra})
        self.fake.module.Elasticsearch.assert_not_called()


class GraphSearchAuthority(unittest.TestCase):
    def test_catalog_is_source_not_live_claim(self):
        for name in ['neo4j_managed','elasticsearch_managed']:
            row=next(r for r in catalog() if r['driver']==name)
            self.assertTrue(row['transport_implemented']);self.assertFalse(row['live_verified']);self.assertIn(name,TRANSPORTS)
    def test_approved_engine_write_and_replay_fence(self):
        for driver,factory in [('neo4j_managed',NeoFixture),('elasticsearch_managed',ESFixture)]:
            with self.subTest(driver=driver),tempfile.TemporaryDirectory() as tmp:
                raw=config(driver);fake=factory()
                policy={'tools':['database.plan_write','database.write'],'allowed_connections':['customer'],'ladder':'autonomous','approver_role':'owner','independent_approval':True}
                e=Engine(Path(tmp)/'platform.db',build_registry(),lambda t,a:policy)
                args={'connection':'customer','request_json':encode(request())}
                with patch('platform_runtime.database.gateway.config',return_value={'connections':{'customer':raw}}),patch.dict('sys.modules',fake.modules),patch.dict('os.environ',TEST_ENV):
                    args['plan_fingerprint']=tool_plan(e,'a','ops',args,'preview')['plan_fingerprint']
                    task=e.submit('a','web','one','ops',[{'tool':'database.write','args':args}],'creator');sid=e.get('a',task)['steps'][0]['id']
                    self.assertFalse(e.tick('a'));e.approve('a',sid,'reviewer','approved','owner');e.tick('a')
                    self.assertEqual('succeeded',e.get('a',task)['status'])
                    with self.assertRaises((Forbidden,Conflict)):tool_write(e,'a','ops',args,sid)

    def test_graph_search_provider_failure_leaves_uncertain_dispatch(self):
        for driver,factory in [('neo4j_managed',NeoFixture),('elasticsearch_managed',ESFixture)]:
            with self.subTest(driver=driver),tempfile.TemporaryDirectory() as tmp:
                raw=config(driver);fake=factory()
                if driver=='neo4j_managed':fake.tx.commit.side_effect=RuntimeError('provider-secret')
                else:fake.client.index.side_effect=RuntimeError('provider-secret')
                policy={'tools':['database.plan_write','database.write'],'allowed_connections':['customer'],'ladder':'autonomous','approver_role':'owner','independent_approval':True}
                e=Engine(Path(tmp)/'platform.db',build_registry(),lambda t,a:policy)
                args={'connection':'customer','request_json':encode(request())}
                with patch('platform_runtime.database.gateway.config',return_value={'connections':{'customer':raw}}),patch.dict('sys.modules',fake.modules),patch.dict('os.environ',TEST_ENV):
                    args['plan_fingerprint']=tool_plan(e,'a','ops',args,'preview')['plan_fingerprint']
                    task=e.submit('a','web','one','ops',[{'tool':'database.write','args':args}],'creator');sid=e.get('a',task)['steps'][0]['id']
                    e.approve('a',sid,'reviewer','approved','owner');e.tick('a')
                    self.assertEqual('uncertain',e.get('a',task)['status']);self.assertFalse(e.tick('a'))
                    self.assertNotIn('provider-secret',encode(e.get('a',task)))
