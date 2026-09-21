"""Fake driver tests for MySQL/MariaDB, not live SQL or TLS proof."""
import types
import unittest
from unittest.mock import patch
from platform_runtime.database.mysql_backend import mysql_execute
from platform_runtime.database.contract import normalize
from platform_runtime.database.transports import validate_endpoint
from platform_runtime.engine import Conflict, Forbidden


def config(driver='mysql_managed'):
    return {'driver':driver,'host':'db.example','allowed_hosts':['db.example'],'port':3306,
            'database':'customer','schema':'customer','user':'writer','password_env':'TEST_DB_PASSWORD','tls':True,
            'isolation':'tenant_column','tenant_column':'tenant_id',
            'resources':{'contacts':{'key_field':'id','version_field':'version','read_fields':['id','name','version'],
                                     'insert_fields':['name'],'update_fields':['name']}}}


def request(operation='update'):
    if operation=='read':return {'operation':'read','resource':'contacts','fields':['id','name'],'limit':2}
    result={'operation':operation,'resource':'contacts','key':'one','values':{'name':'Vali'}}
    if operation=='update':result['expected_version']=1
    return result


class Cursor:
    def __init__(self,parent):self.p=parent;self.last='';self.rowcount=1
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,params=None):
        self.last=sql;self.p.calls.append((sql,params));self.rowcount=self.p.affected
        if self.p.fail and sql.startswith('UPDATE '):raise RuntimeError('interrupted')
    def fetchone(self):
        if 'information_schema.TABLES' in self.last:return (self.p.kind,self.p.engine)
        if 'information_schema.TRIGGERS' in self.last:return (1,) if self.p.trigger else None
        if self.last.startswith('SELECT '):return self.p.records.pop(0) if self.p.records else None
        return None
    def fetchall(self):return self.p.indexes if 'STATISTICS' in self.last else []


class DB:
    def __init__(self):
        self.calls=[];self.kind='BASE TABLE';self.engine='InnoDB';self.trigger=False;self.affected=1
        self.indexes=[('PRIMARY','tenant_id',None),('PRIMARY','id',None)];self.records=[('one','Ali')]
        self.committed=False;self.rolled_back=False;self.closed=False;self.fail=False
        self.cursors=types.SimpleNamespace(SSCursor=object())
    def connect(self,**kwargs):self.kwargs=kwargs;return self
    def cursor(self):return Cursor(self)
    def commit(self):self.committed=True
    def rollback(self):self.rolled_back=True
    def close(self):self.closed=True


class MySQLContracts(unittest.TestCase):
    def execute(self,db,operation='update',driver='mysql_managed'):
        raw=config(driver)
        with patch.dict('sys.modules',{'pymysql':db}),patch.dict('os.environ',{'TEST_DB_PASSWORD':'unit-test-only'}):
            return mysql_execute(raw,'a',normalize(raw,'a',request(operation)))

    def test_update_binds_values_scope_version_and_commits(self):
        db=DB();out=self.execute(db)
        self.assertEqual(2,out['version']);self.assertTrue(db.committed);self.assertTrue(db.closed)
        self.assertTrue(db.kwargs['ssl_verify_cert']);self.assertTrue(db.kwargs['ssl_verify_identity'])
        self.assertFalse(db.kwargs['autocommit'])
        sql,params=next((s,p) for s,p in db.calls if s.startswith('UPDATE '))
        self.assertEqual(['Vali','a','one',1],params);self.assertIn('`tenant_id` = %s',sql)

    def test_read_starts_readonly_transaction(self):
        db=DB();out=self.execute(db,'read')
        self.assertEqual([{'id':'one','name':'Ali'}],out['rows'])
        self.assertIn(('START TRANSACTION READ ONLY',None),db.calls)

    def test_insert_is_explicit_and_scoped(self):
        db=DB();self.execute(db,'insert')
        sql,params=next((s,p) for s,p in db.calls if s.startswith('INSERT INTO'))
        self.assertEqual(['Vali','one',1,'a'],params)

    def test_mariadb_timeout_uses_correct_session_setting(self):
        db=DB();self.execute(db,driver='mariadb_managed')
        self.assertIn(('SET SESSION max_statement_time=2',None),db.calls)
        self.assertFalse(any('MAX_EXECUTION_TIME' in s for s,p in db.calls))

    def test_views_and_nontransactional_tables_denied(self):
        for kind,engine in [('VIEW',None),('BASE TABLE','MyISAM')]:
            db=DB();db.kind=kind;db.engine=engine
            with self.subTest(kind=kind,engine=engine),self.assertRaises(Forbidden):self.execute(db)
            self.assertTrue(db.rolled_back);self.assertTrue(db.closed)

    def test_triggers_denied(self):
        db=DB();db.trigger=True
        with self.assertRaises(Forbidden):self.execute(db)
        self.assertFalse(any(s.startswith('UPDATE ') for s,p in db.calls))

    def test_missing_or_prefix_unique_index_denied(self):
        for indexes in [[],[('ix','id',3)]]:
            db=DB();db.indexes=indexes
            with self.subTest(indexes=indexes),self.assertRaises(Forbidden):self.execute(db)

    def test_missing_or_multiple_rows_rollback(self):
        for affected in [0,2,-1]:
            db=DB();db.affected=affected
            with self.subTest(affected=affected),self.assertRaises(Conflict):self.execute(db)
            self.assertTrue(db.rolled_back);self.assertFalse(db.committed)

    def test_driver_error_rolls_back_without_retry(self):
        db=DB();db.fail=True
        with self.assertRaises(RuntimeError):self.execute(db)
        self.assertTrue(db.rolled_back);self.assertTrue(db.closed)
        self.assertEqual(1,len([s for s,p in db.calls if s.startswith('UPDATE ')]))

    def test_schema_tls_and_system_db_validation(self):
        for extra in [{'tls':False},{'schema':'other'},{'schema':'mysql','database':'mysql'}]:
            with self.subTest(extra=extra),self.assertRaises((Forbidden,ValueError)):validate_endpoint({**config(),**extra})
