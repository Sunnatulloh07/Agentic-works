"""Fake Redis transport contracts. Script semantics still need real Redis tests."""
import types
import unittest
from unittest.mock import MagicMock, patch
from platform_runtime.database.contract import normalize
from platform_runtime.database.redis_backend import redis_execute, redis_key, WRITE_SCRIPT
from platform_runtime.database.transports import validate_endpoint
from platform_runtime.engine import Conflict


def config():
    return {'driver':'redis_managed','host':'redis.example','allowed_hosts':['redis.example'],
            'port':6380,'database':0,'user':'writer','password_env':'TEST_DB_PASSWORD','tls':True,
            'namespace':'customers','isolation':'tenant_column','tenant_column':'tenant_id',
            'resources':{'contacts':{'key_field':'id','version_field':'version','read_fields':['id','name','version'],
                                     'insert_fields':['name'],'update_fields':['name']}}}


def request(operation='update',key='one'):
    if operation=='read': return {'operation':'read','resource':'contacts','key':key,'fields':['id','name'],'limit':1}
    result={'operation':operation,'resource':'contacts','key':key,'values':{'name':'Vali'}}
    if operation=='update':result['expected_version']=1
    return result


class RedisContracts(unittest.TestCase):
    def setUp(self):
        self.client=MagicMock();self.client.eval.return_value=1
        self.module=types.SimpleNamespace(Redis=MagicMock(return_value=self.client))
        self.modules={'redis':self.module,
                      'redis.backoff':types.SimpleNamespace(NoBackoff=MagicMock()),
                      'redis.retry':types.SimpleNamespace(Retry=MagicMock())}

    def execute(self,operation='update'):
        raw=config()
        with patch.dict('sys.modules',self.modules),patch.dict('os.environ',{'TEST_DB_PASSWORD':'unit-test-only'}):
            return redis_execute(raw,'a',normalize(raw,'a',request(operation)))

    def test_key_is_tenant_scoped_and_type_preserving(self):
        raw=config()
        self.assertNotEqual(redis_key(raw,'a',request()),redis_key(raw,'b',request()))
        self.assertNotEqual(redis_key(raw,'a',request(key=1)),redis_key(raw,'a',request(key='1')))

    def test_fixed_script_atomic_cas_and_no_retry_configuration(self):
        out=self.execute()
        self.assertEqual(2,out['version'])
        args=self.client.eval.call_args.args
        self.assertEqual(WRITE_SCRIPT,args[0]);self.assertEqual(1,args[1])
        self.assertEqual(('update','version','1','2','name','"Vali"'),args[3:])
        kwargs=self.module.Redis.call_args.kwargs
        self.assertTrue(kwargs['ssl']);self.assertTrue(kwargs['ssl_check_hostname'])
        self.assertEqual('required',kwargs['ssl_cert_reqs'])
        self.assertFalse(kwargs['retry_on_timeout'])
        self.assertEqual(0,self.modules['redis.retry'].Retry.call_args.args[1])
        self.client.close.assert_called_once()

    def test_insert_injects_key_and_tenant(self):
        out=self.execute('insert');self.assertEqual(1,out['version'])
        args=self.client.eval.call_args.args
        self.assertIn('tenant_id',args);self.assertIn('"a"',args);self.assertIn('"one"',args)

    def test_failed_cas_is_not_success(self):
        self.client.eval.return_value=0
        with self.assertRaises(Conflict):self.execute()
        self.client.close.assert_called_once()

    def test_read_uses_single_bounded_hmget(self):
        self.client.hmget.return_value=['"one"','"Ali"','1']
        out=self.execute('read')
        self.assertEqual([{'id':'one','name':'Ali'}],out['rows'])
        self.assertEqual(['id','name','version'],self.client.hmget.call_args.args[1])
        self.client.eval.assert_not_called()

    def test_absent_key_is_empty_result(self):
        self.client.hmget.return_value=[None,None,None]
        self.assertEqual([],self.execute('read')['rows'])

    def test_provider_failure_closes_connection(self):
        self.client.eval.side_effect=RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):self.execute()
        self.client.close.assert_called_once()

    def test_tls_logical_database_and_namespace_validation(self):
        for extra in [{'tls':False},{'database':True},{'database':16},{'namespace':'unsafe:*'}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):validate_endpoint({**config(),**extra})

    def test_missing_key_never_turns_into_scan(self):
        raw=config();req=request('read');del req['key']
        with self.assertRaises(ValueError):redis_key(raw,'a',req)
