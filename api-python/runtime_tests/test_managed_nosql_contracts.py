"""Offline fake-SDK contracts, not native Cassandra or AWS acceptance."""
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from platform_runtime.database.contract import catalog, normalize
from platform_runtime.database.dynamodb_backend import dynamodb_execute, _attribute, _decode
from platform_runtime.database.cassandra_backend import cassandra_execute
from platform_runtime.database.gateway import prepare, tool_plan, tool_write, TRANSPORTS
from platform_runtime.engine import Engine, Forbidden, Conflict, encode
from platform_runtime.tools import build_registry


def config(driver='dynamodb_managed'):
    raw = {'driver': driver, 'contract_version': '1.1', 'generation': 1, 'enabled': True,
        'lifecycle': 'configured', 'agent_ids': ['ops'], 'capabilities': ['read', 'plan_write', 'execute_write'],
        'isolation': 'tenant_column', 'tenant_column': 'tenant_id',
        'resources': {'contacts': {'key_field': 'id', 'version_field': 'version',
            'read_fields': ['id', 'version', 'name', 'active'], 'insert_fields': ['name', 'active'],
            'update_fields': ['name', 'active']}}}
    if driver == 'dynamodb_managed':
        raw.update(region='us-east-1', aws_account_id='123456789012',
                   access_key_env='TEST_AWS_ACCESS', secret_key_env='TEST_AWS_SECRET')
    else:
        raw.update(host='cassandra.example', allowed_hosts=['cassandra.example'], port=9142,
                   database='customer', user='writer', password_env='TEST_DB_PASSWORD', tls=True)
    return raw


def request(operation='update', **extra):
    result = {'operation': operation, 'resource': 'contacts', 'key': 'one'}
    if operation == 'read':
        result.update(fields=['id', 'name'], limit=2)
    else:
        result['values'] = {'name': 'Vali'}
        if operation == 'update':
            result['expected_version'] = 1
    return {**result, **extra}


class DynamoFixture:
    def __init__(self):
        self.client = MagicMock()
        self.table = {'TableArn': 'arn:aws:dynamodb:us-east-1:123456789012:table/contacts', 'TableStatus': 'ACTIVE',
            'KeySchema': [{'AttributeName': 'tenant_id', 'KeyType': 'HASH'}, {'AttributeName': 'id', 'KeyType': 'RANGE'}],
            'AttributeDefinitions': [{'AttributeName': 'tenant_id', 'AttributeType': 'S'}, {'AttributeName': 'id', 'AttributeType': 'S'}]}
        self.client.describe_table.return_value = {'Table': self.table}
        self.item = {'tenant_id': {'S': 'a'}, 'id': {'S': 'one'}, 'version': {'N': '1'}, 'name': {'S': 'Ali'}}
        self.client.get_item.return_value = {'Item': self.item}
        self.client.put_item.return_value = {'ResponseMetadata': {'HTTPStatusCode': 200}}
        self.client.update_item.return_value = {'ResponseMetadata': {'HTTPStatusCode': 200}, 'Attributes': {'version': {'N': '2'}}}
        self.session = MagicMock(); self.session.client.return_value = self.client
        self.boto = types.SimpleNamespace(Session=MagicMock(return_value=self.session))
        self.conf = types.SimpleNamespace(Config=MagicMock(side_effect=lambda **kw: kw))
        self.modules = {'boto3': self.boto, 'botocore.config': self.conf}


class CassandraFixture:
    def __init__(self):
        self.client = MagicMock()
        self.session = MagicMock(); self.client.connect.return_value = self.session
        def col(n, t): return types.SimpleNamespace(name=n, cql_type=t, is_static=False)
        self.columns = {n: col(n, t) for n, t in [('tenant_id','text'),('id','text'),('version','bigint'),('name','text'),('active','boolean')]}
        self.table = types.SimpleNamespace(partition_key=[self.columns['tenant_id']], clustering_key=[self.columns['id']],
                                          columns=self.columns, options={'default_time_to_live': 0})
        self.client.metadata.keyspaces = {'customer': types.SimpleNamespace(tables={'contacts': self.table})}
        self.result = types.SimpleNamespace(current_rows=[{'[applied]': True}], has_more_pages=False)
        self.session.execute.return_value = self.result
        self.cluster = types.SimpleNamespace(Cluster=MagicMock(return_value=self.client))
        self.auth = types.SimpleNamespace(PlainTextAuthProvider=MagicMock())
        self.policy = types.SimpleNamespace(WhiteListRoundRobinPolicy=MagicMock(), FallthroughRetryPolicy=MagicMock())
        self.query = types.SimpleNamespace(dict_factory=object(), SimpleStatement=MagicMock(side_effect=lambda sql, **kw: types.SimpleNamespace(query_string=sql, **kw)))
        self.modules = {'cassandra': types.SimpleNamespace(ConsistencyLevel=types.SimpleNamespace(QUORUM=4, SERIAL=8)),
            'cassandra.cluster': self.cluster, 'cassandra.auth': self.auth, 'cassandra.policies': self.policy, 'cassandra.query': self.query}


TEST_ENV = {'TEST_AWS_ACCESS': 'offline-test-access', 'TEST_AWS_SECRET': 'offline-test-secret', 'TEST_DB_PASSWORD': 'offline-test-password'}


class DynamoContracts(unittest.TestCase):
    def setUp(self): self.fake = DynamoFixture()
    def run_db(self, operation='update', raw=None, extra=None):
        raw = raw or config()
        with patch.dict('sys.modules', self.fake.modules), patch.dict('os.environ', TEST_ENV):
            return dynamodb_execute(raw, 'a', normalize(raw, 'a', request(operation, **(extra or {}))))
    def test_insert_initializes_tenant_and_version(self):
        self.assertEqual(1, self.run_db('insert')['version'])
        kw = self.fake.client.put_item.call_args.kwargs
        self.assertEqual({'S':'a'}, kw['Item']['tenant_id']); self.assertEqual({'N':'1'}, kw['Item']['version'])
        self.assertIn('attribute_not_exists', kw['ConditionExpression'])
        self.assertTrue(kw['TableName'].startswith('arn:aws:'))
    def test_update_conditional_no_upsert(self):
        self.assertEqual(2, self.run_db()['version'])
        kw = self.fake.client.update_item.call_args.kwargs
        self.assertEqual({'N':'1'}, kw['ExpressionAttributeValues'][':expected'])
        self.assertIn('attribute_exists', kw['ConditionExpression']); self.assertIn('#v = :expected', kw['ConditionExpression'])
        self.assertEqual({'tenant_id':{'S':'a'},'id':{'S':'one'}}, kw['Key'])
    def test_no_retry_and_no_implicit_credentials(self):
        self.run_db()
        kw=self.fake.session.client.call_args.kwargs
        self.assertEqual(1, kw['config']['retries']['total_max_attempts'])
        self.assertEqual('https://dynamodb.us-east-1.amazonaws.com', kw['endpoint_url'])
        self.assertIn('aws_secret_access_key', self.fake.boto.Session.call_args.kwargs)
        self.fake.client.close.assert_called_once()
    def test_read_strong_consistency_projection_and_scope(self):
        self.assertEqual([{'id':'one','name':'Ali'}], self.run_db('read')['rows'])
        kw=self.fake.client.get_item.call_args.kwargs
        self.assertTrue(kw['ConsistentRead']); self.assertIn('tenant_id', kw['ExpressionAttributeNames'].values())
        self.fake.client.scan.assert_not_called(); self.fake.client.query.assert_not_called()
    def test_absent_read(self):
        self.fake.client.get_item.return_value={}
        self.assertEqual([],self.run_db('read')['rows'])
    def test_wrong_tenant_response_denied(self):
        self.fake.item['tenant_id']={'S':'b'}
        with self.assertRaises(Forbidden):self.run_db('read')
    def test_wrong_key_response_denied(self):
        self.fake.item['id']={'S':'two'}
        with self.assertRaises(Forbidden):self.run_db('read')
    def test_invalid_version_denied(self):
        for value in [{'N':'0'},{'BOOL':True},{'S':'1'}]:
            self.fake.item['version']=value
            with self.subTest(value=value),self.assertRaises((ValueError,Forbidden)):self.run_db('read')
    def test_wrong_account_denied_before_write(self):
        self.fake.table['TableArn']=self.fake.table['TableArn'].replace('123456789012','000000000000')
        with self.assertRaises(Forbidden):self.run_db()
        self.fake.client.update_item.assert_not_called()
    def test_wrong_region_denied(self):
        self.fake.table['TableArn']=self.fake.table['TableArn'].replace('us-east-1','eu-west-1')
        with self.assertRaises(Forbidden):self.run_db()
    def test_global_table_denied(self):
        self.fake.table['Replicas']=[{'RegionName':'eu-west-1'}]
        with self.assertRaises(Forbidden):self.run_db()
    def test_nonactive_table_denied(self):
        self.fake.table['TableStatus']='UPDATING'
        with self.assertRaises(Forbidden):self.run_db()
    def test_tenant_not_partition_key_denied(self):
        self.fake.table['KeySchema']=[{'AttributeName':'id','KeyType':'HASH'}]
        with self.assertRaises(Forbidden):self.run_db()
    def test_wrong_identity_type_denied(self):
        self.fake.table['AttributeDefinitions'][0]['AttributeType']='N'
        with self.assertRaises(Forbidden):self.run_db()
    def test_dedicated_binding(self):
        raw=config();raw.pop('tenant_column');raw.update(isolation='dedicated_database',bound_tenant='a')
        self.fake.table['KeySchema']=[{'AttributeName':'id','KeyType':'HASH'}]
        self.run_db('insert',raw=raw)
        self.assertNotIn('tenant_id',self.fake.client.put_item.call_args.kwargs['Item'])
    def test_missing_exact_key_denied_before_sdk(self):
        for key in [None,1,True,'']:
            with self.subTest(key=key),self.assertRaises(ValueError):self.run_db('read',extra={'key':key})
        self.fake.boto.Session.assert_not_called()
    def test_bad_config_rejected_before_sdk(self):
        for extra in [{'region':'us-east-1/evil'},{'aws_account_id':'wrong'},{'endpoint_url':'http://evil'},
                      {'secret_key_env':'DSEC_SECRET'},{'access_key_env':'bad'},{'profile':'default'}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.run_db(raw={**config(),**extra})
        self.fake.boto.Session.assert_not_called()
    def test_conditional_conflict_sanitized(self):
        exc=RuntimeError('provider-secret');exc.response={'Error':{'Code':'ConditionalCheckFailedException'}}
        self.fake.client.update_item.side_effect=exc
        with self.assertRaises(Conflict) as caught:self.run_db()
        self.assertNotIn('secret',str(caught.exception));self.fake.client.update_item.assert_called_once()
    def test_provider_failure_sanitized_no_retry(self):
        self.fake.client.update_item.side_effect=RuntimeError('provider-secret')
        with self.assertRaises(RuntimeError) as caught:self.run_db()
        self.assertNotIn('secret',str(caught.exception));self.fake.client.update_item.assert_called_once()
    def test_sdk_valueerror_does_not_leak_provider_detail(self):
        self.fake.client.update_item.side_effect=ValueError('provider-secret')
        with self.assertRaises(RuntimeError) as caught:self.run_db()
        self.assertNotIn('secret',str(caught.exception))
    def test_missing_acknowledgement_not_success(self):
        self.fake.client.put_item.return_value={}
        with self.assertRaises(RuntimeError):self.run_db('insert')
    def test_missing_update_receipt_not_success(self):
        self.fake.client.update_item.return_value={'ResponseMetadata':{'HTTPStatusCode':200}}
        with self.assertRaises(RuntimeError):self.run_db()
    def test_cleanup_failure_is_uncertain(self):
        self.fake.client.close.side_effect=RuntimeError('secret-cleanup')
        with self.assertRaises(RuntimeError) as caught:self.run_db()
        self.assertNotIn('secret',str(caught.exception))
    def test_scalar_roundtrip(self):
        for value in [None,True,False,0,-1,2**53-1,"o‘zbek",'']:
            with self.subTest(value=value):self.assertEqual(value,_decode(_attribute(value)))
    def test_complex_values_rejected(self):
        for value in [{'M':{}},{'L':[]},{'N':'1.5'},{'N':'NaN'},{'NULL':1},{'N':'9007199254740992'}]:
            with self.subTest(value=value),self.assertRaises(ValueError):_decode(value)


class CassandraContracts(unittest.TestCase):
    def setUp(self):self.fake=CassandraFixture()
    def run_db(self,operation='update',raw=None,extra=None):
        raw=raw or config('cassandra_managed')
        if operation=='read' and self.fake.result.current_rows==[{'[applied]':True}]:
            self.fake.result.current_rows=[{'id':'one','tenant_id':'a','version':1,'name':'Ali'}]
        with patch.dict('sys.modules',self.fake.modules),patch.dict('os.environ',TEST_ENV):
            return cassandra_execute(raw,'a',normalize(raw,'a',request(operation,**(extra or {}))))
    def test_update_lwt_scope_version(self):
        self.assertEqual(2,self.run_db()['version'])
        statement,params=self.fake.session.execute.call_args.args
        self.assertIn('IF "version" = %s',statement.query_string)
        self.assertEqual(['Vali',2,'a','one',1],params)
        self.assertNotIn('ALLOW FILTERING',statement.query_string)
    def test_insert_if_not_exists(self):
        self.assertEqual(1,self.run_db('insert')['version'])
        statement,params=self.fake.session.execute.call_args.args
        self.assertTrue(statement.query_string.endswith('IF NOT EXISTS'));self.assertIn('a',params)
    def test_quorum_serial_no_retry(self):
        self.run_db();statement=self.fake.session.execute.call_args.args[0]
        self.assertEqual(4,statement.consistency_level);self.assertEqual(8,statement.serial_consistency_level)
        self.assertFalse(statement.is_idempotent)
        self.fake.policy.WhiteListRoundRobinPolicy.assert_called_once_with(['cassandra.example'])
        self.fake.client.shutdown.assert_called_once()
        self.assertTrue(self.fake.cluster.Cluster.call_args.kwargs['ssl_context'].check_hostname)
    def test_read_bounded_exact_key(self):
        self.assertEqual([{'id':'one','name':'Ali'}],self.run_db('read')['rows'])
        self.assertTrue(self.fake.session.execute.call_args.args[0].query_string.endswith('LIMIT 1'))
    def test_wrong_tenant_denied(self):
        self.fake.result.current_rows=[{'id':'one','tenant_id':'b','version':1}]
        with self.assertRaises(Forbidden):self.run_db('read')
    def test_wrong_key_denied(self):
        self.fake.result.current_rows=[{'id':'other','tenant_id':'a','version':1}]
        with self.assertRaises(Forbidden):self.run_db('read')
    def test_invalid_version_denied(self):
        self.fake.result.current_rows=[{'id':'one','tenant_id':'a','version':True}]
        with self.assertRaises(Forbidden):self.run_db('read')
    def test_missing_read_returns_empty(self):
        self.fake.result.current_rows=[];self.assertEqual([],self.run_db('read')['rows'])
    def test_no_pagination(self):
        self.fake.result.has_more_pages=True
        with self.assertRaises(RuntimeError):self.run_db('read')
    def test_multiple_rows_denied(self):
        self.fake.result.current_rows=[{},{}]
        with self.assertRaises(RuntimeError):self.run_db('read')
    def test_lwt_conflict(self):
        self.fake.result.current_rows=[{'[applied]':False}]
        with self.assertRaises(Conflict):self.run_db()
    def test_lwt_ack_must_be_bool(self):
        self.fake.result.current_rows=[{'[applied]':1}]
        with self.assertRaises(Conflict):self.run_db()
    def test_static_columns_denied(self):
        self.fake.columns['name'].is_static=True
        with self.assertRaises(Forbidden):self.run_db()
        self.fake.session.execute.assert_not_called()
    def test_counter_columns_denied(self):
        self.fake.columns['name'].cql_type='counter'
        with self.assertRaises(Forbidden):self.run_db()
    def test_ttl_table_denied(self):
        self.fake.table.options['default_time_to_live']=60
        with self.assertRaises(Forbidden):self.run_db()
    def test_partition_mismatch_denied(self):
        self.fake.table.partition_key=[self.fake.columns['id']]
        with self.assertRaises(Forbidden):self.run_db()
    def test_clustering_mismatch_denied(self):
        self.fake.table.clustering_key=[]
        with self.assertRaises(Forbidden):self.run_db()
    def test_missing_schema_denied(self):
        self.fake.client.metadata.keyspaces={}
        with self.assertRaises(Forbidden):self.run_db()
    def test_wrong_value_type_denied(self):
        with self.assertRaises(ValueError):self.run_db(extra={'values':{'name':True}})
        self.fake.session.execute.assert_not_called()
    def test_wrong_version_column_denied(self):
        self.fake.columns['version'].cql_type='int'
        with self.assertRaises(Forbidden):self.run_db()
    def test_provider_failure_closes_sanitized(self):
        self.fake.session.execute.side_effect=RuntimeError('driver-secret')
        with self.assertRaises(RuntimeError) as caught:self.run_db()
        self.assertNotIn('secret',str(caught.exception));self.fake.client.shutdown.assert_called_once()
    def test_sdk_valueerror_sanitized(self):
        self.fake.session.execute.side_effect=ValueError('driver-secret')
        with self.assertRaises(RuntimeError) as caught:self.run_db()
        self.assertNotIn('secret',str(caught.exception))
    def test_bad_endpoint_no_connection(self):
        for extra in [{'tls':False},{'database':'system_auth'},{'host':'evil'},{'password_env':'DSEC_DB_PASSWORD'}]:
            with self.subTest(extra=extra),self.assertRaises((ValueError,Forbidden)):
                self.run_db(raw={**config('cassandra_managed'),**extra})
        self.fake.cluster.Cluster.assert_not_called()
    def test_missing_key_denied_before_connect(self):
        with self.assertRaises(ValueError):self.run_db('read',extra={'key':None})
        self.fake.cluster.Cluster.assert_not_called()
    def test_dedicated_binding(self):
        raw=config('cassandra_managed');raw.pop('tenant_column');raw.update(isolation='dedicated_database',bound_tenant='a')
        self.fake.table.partition_key=[self.fake.columns['id']];self.fake.table.clustering_key=[]
        self.run_db(raw=raw)
        self.assertNotIn('tenant_id',self.fake.session.execute.call_args.args[0].query_string)
    def test_injection_is_bound(self):
        attack="Ali'; DROP TABLE contacts; --"
        self.run_db(extra={'values':{'name':attack}})
        statement,params=self.fake.session.execute.call_args.args
        self.assertNotIn(attack,statement.query_string);self.assertIn(attack,params)


class NoSQLAuthority(unittest.TestCase):
    def test_catalog_registration_truthful(self):
        for driver in ['dynamodb_managed','cassandra_managed']:
            row=next(x for x in catalog() if x['driver']==driver)
            self.assertTrue(row['transport_implemented']);self.assertFalse(row['live_verified']);self.assertIn(driver,TRANSPORTS)
    def test_changed_config_invalidates_write_plan(self):
        for driver in ['dynamodb_managed','cassandra_managed']:
            raw=config(driver);args={'connection':'customer','request_json':encode(request())}
            with patch('platform_runtime.database.gateway.config',side_effect=lambda t:{'connections':{'customer':raw}}):
                args['plan_fingerprint']=prepare('a','ops','database.plan_write',args)[2]
                raw['generation']+=1
                with self.assertRaises(Conflict):prepare('a','ops','database.write',args)
    def test_independent_approval_dispatch_once_and_no_retry_after_timeout(self):
        for driver,factory in [('dynamodb_managed',DynamoFixture),('cassandra_managed',CassandraFixture)]:
            for fails in [False,True]:
                with self.subTest(driver=driver,fails=fails),tempfile.TemporaryDirectory() as tmp:
                    raw=config(driver);fake=factory()
                    write=fake.client.update_item if driver=='dynamodb_managed' else fake.session.execute
                    if fails:write.side_effect=RuntimeError('provider-secret')
                    policy={'tools':['database.plan_write','database.write'],'allowed_connections':['customer'],
                            'ladder':'autonomous','approver_role':'owner','independent_approval':True}
                    e=Engine(Path(tmp)/'platform.sqlite',build_registry(),lambda t,a:policy)
                    args={'connection':'customer','request_json':encode(request())}
                    with patch('platform_runtime.database.gateway.config',return_value={'connections':{'customer':raw}}),patch.dict('sys.modules',fake.modules),patch.dict('os.environ',TEST_ENV):
                        args['plan_fingerprint']=tool_plan(e,'a','ops',args,'preview')['plan_fingerprint']
                        task=e.submit('a','web','one','ops',[{'tool':'database.write','args':args}],'creator')
                        sid=e.get('a',task)['steps'][0]['id']
                        self.assertFalse(e.tick('a'));write.assert_not_called()
                        with self.assertRaises(Forbidden):e.approve('a',sid,'creator','approved','owner')
                        e.approve('a',sid,'reviewer','approved','owner');e.tick('a')
                        self.assertEqual('uncertain' if fails else 'succeeded',e.get('a',task)['status'])
                        self.assertFalse(e.tick('a'));write.assert_called_once()
                        with self.assertRaises((Forbidden,Conflict)):tool_write(e,'a','ops',args,sid)
                        self.assertNotIn('provider-secret',encode(e.get('a',task)))
