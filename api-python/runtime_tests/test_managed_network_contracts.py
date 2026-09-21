"""Transport contract tests with fakes. NOT live PostgreSQL/MongoDB acceptance."""
import types
import unittest
from unittest.mock import patch, MagicMock
from platform_runtime.database.contract import normalize
from platform_runtime.database.transports import postgres_execute, mongodb_execute, mongo_selector
from platform_runtime.engine import Conflict, Forbidden


def config(driver='postgres_managed'):
    return {'driver': driver, 'host': 'db.example', 'allowed_hosts': ['db.example'], 'port': 5432,
            'database': 'customer', 'schema': 'sales', 'user': 'writer',
            'password_env': 'TEST_DB_PASSWORD', 'sslmode': 'verify-full', 'tls': True,
            'isolation': 'tenant_column', 'tenant_column': 'tenant_id',
            'resources': {'contacts': {'key_field': 'id', 'version_field': 'version',
                'read_fields': ['id', 'name', 'version'], 'insert_fields': ['name'], 'update_fields': ['name']}}}


def req(operation='update'):
    if operation == 'read':
        return {'operation': 'read', 'resource': 'contacts', 'fields': ['id', 'name'], 'limit': 2}
    out = {'operation': operation, 'resource': 'contacts', 'key': 'one', 'values': {'name': 'Vali'}}
    if operation == 'update':
        out['expected_version'] = 1
    return out


class PGCursor:
    def __init__(self, parent, named=False):
        self.parent, self.named, self.last, self.rowcount = parent, named, '', 1
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params=None):
        self.last = sql; self.parent.calls.append((sql, params)); self.rowcount = self.parent.affected
        if self.parent.fail_sql and sql.startswith('UPDATE '):
            raise RuntimeError('transport-interrupted')
    def fetchone(self):
        if self.named:
            return self.parent.records.pop(0) if self.parent.records else None
        if 'pg_class' in self.last: return (17, self.parent.kind)
        if 'pg_trigger' in self.last: return (1,) if self.parent.trigger else None
        raise AssertionError(self.last)
    def fetchall(self): return [(keys,) for keys in self.parent.keys]


class PG:
    def __init__(self):
        self.calls=[]; self.kind='r'; self.trigger=False; self.affected=1
        self.keys=[['tenant_id', 'id']]; self.records=[('one', 'Ali')]
        self.closed=False; self.committed=False; self.rolled_back=False; self.fail_sql=False
    def connect(self, **kwargs): self.kwargs=kwargs; return self
    def cursor(self, name=None): return PGCursor(self, bool(name))
    def __enter__(self): return self
    def __exit__(self, kind, value, tb):
        self.committed = kind is None; self.rolled_back = kind is not None
    def close(self): self.closed=True


class PostgreSQLContracts(unittest.TestCase):
    def run_pg(self, db, operation='update'):
        raw=config(); request=normalize(raw,'a',req(operation))
        with patch.dict('sys.modules', {'psycopg': db}), patch.dict('os.environ', {'TEST_DB_PASSWORD': 'unit-test-only'}):
            return postgres_execute(raw,'a',request)

    def test_update_has_tls_scope_version_and_commit(self):
        db=PG(); result=self.run_pg(db)
        self.assertEqual(1,result['affected']); self.assertTrue(db.committed); self.assertTrue(db.closed)
        self.assertEqual('verify-full',db.kwargs['sslmode'])
        self.assertIn('statement_timeout=2000',db.kwargs['options'])
        sql,params=next((s,p) for s,p in db.calls if s.startswith('UPDATE '))
        self.assertEqual(['Vali','a','one',1],params)
        self.assertIn('"version" = "version" + 1',sql)

    def test_read_is_readonly_and_streamed(self):
        db=PG(); result=self.run_pg(db,'read')
        self.assertEqual([{'id':'one','name':'Ali'}],result['rows'])
        self.assertEqual('SET TRANSACTION READ ONLY',db.calls[0][0])
        self.assertIn('default_transaction_read_only=on',db.kwargs['options'])

    def test_insert_is_one_row(self):
        db=PG(); result=self.run_pg(db,'insert')
        self.assertEqual(1,result['version'])
        self.assertTrue(any(s.startswith('INSERT INTO ') for s,p in db.calls))

    def test_views_and_partitioned_tables_denied(self):
        for kind in ['v','m','p','f']:
            db=PG(); db.kind=kind
            with self.subTest(kind=kind),self.assertRaises(Forbidden): self.run_pg(db)
            self.assertTrue(db.rolled_back);self.assertTrue(db.closed)

    def test_trigger_denied(self):
        db=PG();db.trigger=True
        with self.assertRaises(Forbidden):self.run_pg(db)
        self.assertFalse(any(s.startswith('UPDATE ') for s,p in db.calls))

    def test_nonunique_table_denied(self):
        db=PG();db.keys=[]
        with self.assertRaises(Forbidden):self.run_pg(db)

    def test_zero_or_multiple_rows_roll_back(self):
        for count in [0,2,-1]:
            db=PG();db.affected=count
            with self.subTest(count=count),self.assertRaises(Conflict):self.run_pg(db)
            self.assertTrue(db.rolled_back);self.assertFalse(db.committed)

    def test_provider_failure_rolls_back_and_closes(self):
        db=PG();db.fail_sql=True
        with self.assertRaises(RuntimeError):self.run_pg(db)
        self.assertTrue(db.rolled_back);self.assertTrue(db.closed)

    def test_large_read_denied(self):
        db=PG();db.records=[('one','x'*17000)]
        with self.assertRaises(ValueError):self.run_pg(db,'read')
        self.assertTrue(db.closed)


class MongoContracts(unittest.TestCase):
    def setUp(self):
        self.collection=MagicMock();self.db=MagicMock();self.client=MagicMock()
        self.client.__getitem__.return_value=self.db
        self.db.__getitem__.return_value=self.collection
        self.db.list_collections.side_effect=lambda **kw:iter([{'name':'contacts','type':'collection','options':{}}])
        self.collection.list_indexes.side_effect=lambda:iter([{'key':{'tenant_id':1,'id':1},'unique':True}])
        self.collection.update_one.return_value=types.SimpleNamespace(acknowledged=True,matched_count=1)
        self.collection.insert_one.return_value=types.SimpleNamespace(acknowledged=True)
        self.module=types.SimpleNamespace(MongoClient=MagicMock(return_value=self.client))

    def run_mongo(self,operation='update'):
        raw=config('mongodb_managed');raw['port']=27017
        with patch.dict('sys.modules',{'pymongo':self.module}),patch.dict('os.environ',{'TEST_DB_PASSWORD':'unit-test-only'}):
            return mongodb_execute(raw,'a',normalize(raw,'a',req(operation)))

    def test_atomic_compare_and_set_and_no_retry(self):
        out=self.run_mongo()
        self.assertEqual(2,out['version'])
        self.collection.update_one.assert_called_once_with(
            mongo_selector(config('mongodb_managed'), 'a', normalize(config('mongodb_managed'), 'a', req())),
            {'$set':{'name':'Vali'},'$inc':{'version':1}},upsert=False)
        kwargs=self.module.MongoClient.call_args.kwargs
        self.assertTrue(kwargs['tls']);self.assertTrue(kwargs['directConnection'])
        self.assertFalse(kwargs['retryWrites']);self.assertFalse(kwargs['retryReads'])
        self.client.close.assert_called_once()

    def test_insert_injects_scope_and_version(self):
        self.run_mongo('insert')
        self.collection.insert_one.assert_called_once_with({'name':'Vali','id':'one','version':1,'tenant_id':'a'})

    def test_missing_unique_index_denied(self):
        self.collection.list_indexes.side_effect=lambda:iter([])
        with self.assertRaises(Forbidden):self.run_mongo()
        self.collection.update_one.assert_not_called()
        self.client.close.assert_called_once()

    def test_partial_unique_index_denied(self):
        self.collection.list_indexes.side_effect=lambda:iter([{'key':{'tenant_id':1,'id':1},'unique':True,'partialFilterExpression':{'active':True}}])
        with self.assertRaises(Forbidden):self.run_mongo()

    def test_collection_view_and_collation_denied(self):
        for info in [{'type':'view'}, {'type':'collection','options':{'collation':{'locale':'en'}}}]:
            self.db.list_collections.side_effect=lambda **kw:iter([info])
            with self.subTest(info=info),self.assertRaises(Forbidden):self.run_mongo()

    def test_version_conflict_does_not_upsert(self):
        self.collection.update_one.return_value=types.SimpleNamespace(acknowledged=True,matched_count=0)
        with self.assertRaises(Conflict):self.run_mongo()
        self.assertFalse(self.collection.update_one.call_args.kwargs['upsert'])

    def test_unacknowledged_write_is_not_success(self):
        self.collection.update_one.return_value=types.SimpleNamespace(acknowledged=False)
        with self.assertRaises(RuntimeError):self.run_mongo()

    def test_read_projects_fields_and_closes_cursor(self):
        cursor=MagicMock();cursor.limit.return_value=cursor;cursor.max_time_ms.return_value=cursor
        cursor.__iter__.return_value=iter([{'id':'one','name':'Ali'}])
        self.collection.find.return_value=cursor
        out=self.run_mongo('read')
        self.assertEqual([{'id':'one','name':'Ali'}],out['rows'])
        self.collection.find.assert_called_once_with(mongo_selector(config('mongodb_managed'), 'a', normalize(config('mongodb_managed'), 'a', req('read'))),{'id':1,'name':1,'_id':0})
        cursor.limit.assert_called_once_with(2);cursor.max_time_ms.assert_called_once_with(2000)
        cursor.close.assert_called_once()


    def test_tenant_selector_requires_scalar_type_and_literal_equality(self):
        raw=config('mongodb_managed')
        result=mongo_selector(raw, 'a', normalize(raw, 'a', req()))
        guards=result['$expr']['$and']
        self.assertIn({'$in':[{'$type':'$tenant_id'},['string']]},guards)
        self.assertIn({'$eq':['$tenant_id',{'$literal':'a'}]},guards)
        self.assertIn({'$in':[{'$type':'$version'},['int','long']]},guards)

    def test_dollar_record_key_stays_literal_not_field_expression(self):
        raw=config('mongodb_managed');request=req();request['key']='$secret'
        result=mongo_selector(raw, 'a', normalize(raw, 'a', request))
        self.assertIn({'$eq':['$id',{'$literal':'$secret'}]},result['$expr']['$and'])
