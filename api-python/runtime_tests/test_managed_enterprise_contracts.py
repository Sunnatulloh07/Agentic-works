"""Fake DB-API contracts only. These tests do not prove native SQL, TLS or DDL safety."""
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from platform_runtime.database.contract import catalog, normalize
from platform_runtime.database.gateway import prepare, tool_plan, tool_write, TRANSPORTS
from platform_runtime.database.sqlserver_backend import sqlserver_execute, _odbc_value
from platform_runtime.database.oracle_backend import oracle_execute
from platform_runtime.database.transports import validate_endpoint
from platform_runtime.engine import Conflict, Forbidden, Engine, encode
from platform_runtime.tools import build_registry


def config(driver='sqlserver_managed'):
    raw = {'driver': driver, 'contract_version': '1.1', 'generation': 1, 'enabled': True,
           'lifecycle': 'configured', 'agent_ids': ['ops'],
           'capabilities': ['read', 'plan_write', 'execute_write'],
           'host': 'db.example', 'allowed_hosts': ['db.example'], 'port': 1433,
           'database': 'customer', 'schema': 'sales', 'user': 'writer',
           'password_env': 'TEST_DB_PASSWORD', 'tls': True,
           'isolation': 'tenant_column', 'tenant_column': 'tenant_id',
           'resources': {'contacts': {'key_field': 'id', 'version_field': 'version',
               'read_fields': ['id', 'name', 'version'], 'insert_fields': ['name'], 'update_fields': ['name']}}}
    if driver == 'sqlserver_managed':
        raw['odbc_driver'] = 'ODBC Driver 18 for SQL Server'
    else:
        raw.update(port=2484, service_name='customer.example')
    return raw


def request(operation='update', **extra):
    out = {'operation': operation, 'resource': 'contacts'}
    if operation == 'read':
        out.update(fields=['id', 'name'], limit=2)
    else:
        out.update(key='one', values={'name': 'Vali'})
        if operation == 'update':
            out['expected_version'] = 1
    out.update(extra)
    return out


class Cursor:
    def __init__(self, db):
        self.db = db
        self.last = ''
        self.rowcount = -1
        self.closed = False
    def execute(self, sql, params=None):
        self.last = sql
        self.db.calls.append((sql, params))
        if self.db.fail_sql and sql.startswith(('UPDATE ', 'INSERT INTO ')):
            raise RuntimeError('secret-provider-detail')
        self.rowcount = self.db.affected if sql.startswith(('UPDATE ', 'INSERT INTO ')) else -1
    def fetchone(self):
        s, d = self.last, self.db
        if 'HAS_PERMS_BY_NAME' in s: return (d.permission,)
        if 'FROM sys.tables' in s: return d.table
        if 'FROM ALL_TABLES' in s: return d.table
        if 'FROM sys.triggers' in s or 'FROM ALL_TRIGGERS' in s: return (1,) if d.trigger else None
        if 'FROM sys.foreign_keys' in s or "CONSTRAINT_TYPE='R'" in s: return (1,) if d.foreign_key else None
        if s.startswith('SELECT ') and ('FROM [sales].[contacts]' in s or 'FROM "sales"."contacts"' in s):
            return d.records.pop(0) if d.records else None
        raise AssertionError('Unexpected fake fetchone: ' + s)
    def fetchall(self):
        s, d = self.last, self.db
        if 'FROM sys.columns' in s or 'FROM ALL_TAB_COLS' in s: return d.columns
        if 'FROM sys.indexes' in s or 'FROM ALL_CONSTRAINTS c' in s: return d.indexes
        if 'WITH (TABLOCKX,HOLDLOCK)' in s: return []
        raise AssertionError('Unexpected fake fetchall: ' + s)
    def close(self):
        self.closed = True
        if self.db.fail_close_cursor: raise RuntimeError('secret-cursor-detail')


class DB:
    def __init__(self, driver='sqlserver_managed'):
        self.driver = driver
        self.calls = []
        self.connect_calls = 0
        self.affected = 1
        self.permission = 1
        self.trigger = False
        self.foreign_key = False
        self.fail_sql = self.fail_commit = self.fail_rollback = self.fail_close_cursor = False
        self.fail_cursor = self.fail_connect = False
        self.closed = self.committed = self.rolled_back = False
        self.records = [('one', 'Ali')]
        self.indexes = [('pk', 'tenant_id'), ('pk', 'id')]
        self.cur = None
        if driver == 'sqlserver_managed':
            self.table = (17, 0, 0, 0, 0, 0)
            self.columns = [
                ('id', 'nvarchar', 0, 0, 0, False, 'Latin1_General_100_BIN2', False, 512),
                ('tenant_id', 'nvarchar', 0, 0, 0, False, 'Latin1_General_100_BIN2', False, 512),
                ('version', 'bigint', 0, 0, 0, False, None, False, 8),
                ('name', 'nvarchar', 0, 0, 0, True, 'Latin1_General_100_CI_AS', False, 4000)]
        else:
            self.table = ('N', 'NO', None, 'NO', 'N')
            self.columns = [
                ('id', 'VARCHAR2', 'NO', 'NO', 'NO', 'N', None, None, 'USING_NLS_COMP', 4000),
                ('tenant_id', 'VARCHAR2', 'NO', 'NO', 'NO', 'N', None, None, 'BINARY', 4000),
                ('version', 'NUMBER', 'NO', 'NO', 'NO', 'N', 19, 0, None, 22),
                ('name', 'VARCHAR2', 'NO', 'NO', 'NO', 'Y', None, None, 'USING_NLS_COMP', 4000)]
    def connect(self, *args, **kwargs):
        self.connect_calls += 1
        self.connect_args, self.connect_kwargs = args, kwargs
        if self.fail_connect: raise RuntimeError('secret-connect-detail')
        return self
    def cursor(self):
        if self.fail_cursor: raise RuntimeError('secret-cursor-creation-detail')
        self.cur = Cursor(self)
        return self.cur
    def commit(self):
        if self.fail_commit: raise RuntimeError('secret-commit-detail')
        self.committed = True
    def rollback(self):
        self.rolled_back = True
        if self.fail_rollback: raise RuntimeError('secret-rollback-detail')
    def close(self): self.closed = True
    def is_thin_mode(self): return True
    def ConnectParams(self, **kwargs):
        self.params = kwargs
        return types.SimpleNamespace(**kwargs)


class EnterpriseMixin:
    DRIVER = None
    def execute(self, db=None, operation='update', extra=None, raw_extra=None):
        db = db or DB(self.DRIVER)
        raw = {**config(self.DRIVER), **(raw_extra or {})}
        if raw['isolation'] == 'dedicated_database':
            raw.pop('tenant_column', None)
        req = normalize(raw, 'a', request(operation, **(extra or {})))
        module = 'pyodbc' if self.DRIVER == 'sqlserver_managed' else 'oracledb'
        fn = sqlserver_execute if self.DRIVER == 'sqlserver_managed' else oracle_execute
        with patch.dict('sys.modules', {module: db}), patch.dict('os.environ', {'TEST_DB_PASSWORD': 'unit-test-only'}):
            return fn(raw, 'a', req)
    def test_update_commits_scoped_versioned_bindings(self):
        db = DB(self.DRIVER); result = self.execute(db)
        self.assertEqual(2, result['version']); self.assertTrue(db.committed); self.assertTrue(db.closed)
        self.assertTrue(db.cur.closed); self.assertEqual(1, db.connect_calls)
        sql, params = next((s,p) for s,p in db.calls if s.startswith('UPDATE '))
        self.assertEqual(['Vali', 'a', 'one', 1], params)
        self.assertIn('tenant_id', sql); self.assertIn('version', sql)
    def test_insert_initializes_scope_and_version(self):
        db = DB(self.DRIVER); self.assertEqual(1, self.execute(db, 'insert')['version'])
        sql, params = next((s,p) for s,p in db.calls if s.startswith('INSERT INTO '))
        self.assertEqual(['Vali', 'one', 1, 'a'], params)
    def test_read_is_bounded_and_projected(self):
        db = DB(self.DRIVER); db.records = [('one','Ali'), ('two','Vali'), ('three','Never fetched')]
        result = self.execute(db, 'read')
        self.assertEqual(2, result['returned']); self.assertEqual(1,len(db.records)); self.assertTrue(result['read_only'])
        self.assertFalse(any(s.startswith(('INSERT ', 'UPDATE ')) for s,p in db.calls))
    def test_sql_injection_value_stays_parameter(self):
        db = DB(self.DRIVER); attack = "O'zbek'; DROP TABLE contacts; --"
        self.execute(db, extra={'values': {'name': attack}})
        sql,params = next((s,p) for s,p in db.calls if s.startswith('UPDATE '))
        self.assertNotIn(attack, sql); self.assertEqual(attack,params[0])
    def test_conflict_rolls_back(self):
        for affected in [0, 2, -1]:
            db = DB(self.DRIVER); db.affected = affected
            with self.subTest(affected=affected), self.assertRaises(Conflict): self.execute(db)
            self.assertTrue(db.rolled_back); self.assertTrue(db.closed); self.assertFalse(db.committed)
    def test_trigger_denied_before_dml(self):
        db=DB(self.DRIVER);db.trigger=True
        with self.assertRaises(Forbidden):self.execute(db)
        self.assertFalse(any(s.startswith('UPDATE ') for s,p in db.calls));self.assertTrue(db.closed)
    def test_foreign_key_denied(self):
        db=DB(self.DRIVER);db.foreign_key=True
        with self.assertRaises(Forbidden):self.execute(db)
        self.assertTrue(db.rolled_back)
    def test_missing_unique_key_denied(self):
        db=DB(self.DRIVER);db.indexes=[]
        with self.assertRaises(Forbidden):self.execute(db)
    def test_unrelated_unique_key_denied(self):
        db=DB(self.DRIVER);db.indexes=[('ix','name')]
        with self.assertRaises(Forbidden):self.execute(db)
    def test_global_unique_record_key_supported(self):
        db=DB(self.DRIVER);db.indexes=[('pk','id')]
        self.assertEqual(1,self.execute(db)['affected'])
    def test_view_or_missing_table_denied(self):
        db=DB(self.DRIVER);db.table=None
        with self.assertRaises(Forbidden):self.execute(db)
    def test_missing_configured_column_denied(self):
        db=DB(self.DRIVER);db.columns=db.columns[:-1]
        with self.assertRaises(Forbidden):self.execute(db)
    def test_provider_failure_is_sanitized_without_retry(self):
        db=DB(self.DRIVER);db.fail_sql=True
        with self.assertRaises(RuntimeError) as caught:self.execute(db)
        self.assertNotIn('secret',str(caught.exception));self.assertTrue(db.rolled_back);self.assertTrue(db.closed)
        self.assertEqual(1,len([s for s,p in db.calls if s.startswith('UPDATE ')]))
    def test_commit_failure_is_not_success_or_retry(self):
        db=DB(self.DRIVER);db.fail_commit=True
        with self.assertRaises(RuntimeError) as caught:self.execute(db)
        self.assertNotIn('secret',str(caught.exception));self.assertTrue(db.rolled_back);self.assertTrue(db.closed)
        self.assertEqual(1,db.connect_calls)
    def test_rollback_failure_still_closes(self):
        db=DB(self.DRIVER);db.fail_sql=db.fail_rollback=True
        with self.assertRaises(RuntimeError) as caught:self.execute(db)
        self.assertNotIn('secret',str(caught.exception));self.assertTrue(db.closed)
    def test_cursor_creation_failure_closes_connection(self):
        db=DB(self.DRIVER);db.fail_cursor=True
        with self.assertRaises(RuntimeError):self.execute(db)
        self.assertTrue(db.closed);self.assertTrue(db.rolled_back)
    def test_cursor_close_failure_still_closes_connection(self):
        db=DB(self.DRIVER);db.fail_close_cursor=True
        with self.assertRaises(RuntimeError) as caught:self.execute(db)
        self.assertNotIn('secret',str(caught.exception));self.assertTrue(db.closed)
    def test_connect_failure_sanitized_without_retry(self):
        db=DB(self.DRIVER);db.fail_connect=True
        with self.assertRaises(RuntimeError) as caught:self.execute(db)
        self.assertNotIn('secret',str(caught.exception));self.assertEqual(1,db.connect_calls)
    def test_large_result_denied(self):
        db=DB(self.DRIVER);db.records=[('one','x'*17000)]
        with self.assertRaises(ValueError):self.execute(db,'read')
        self.assertTrue(db.closed)
    def test_bad_endpoint_fails_before_connect(self):
        for extra in [{'tls':False},{'port':True},{'allowed_hosts':[]},{'password_env':'DSEC_DB_PASSWORD'},{'host':'evil.example'}]:
            db=DB(self.DRIVER)
            with self.subTest(extra=extra),self.assertRaises((Forbidden,ValueError)):self.execute(db,raw_extra=extra)
            self.assertEqual(0,db.connect_calls)
    def test_wrong_record_key_type_denied(self):
        db=DB(self.DRIVER)
        with self.assertRaises(ValueError):self.execute(db,extra={'key':123})
        self.assertFalse(any(s.startswith('UPDATE ') for s,p in db.calls))
    def test_unbounded_text_columns_denied(self):
        db=DB(self.DRIVER);row=list(db.columns[0]);row[-1]=100000;db.columns[0]=tuple(row)
        with self.assertRaises(Forbidden):self.execute(db)
    def test_nullable_identity_column_denied(self):
        db=DB(self.DRIVER);row=list(db.columns[0]);row[5]=True if self.DRIVER=='sqlserver_managed' else 'Y';db.columns[0]=tuple(row)
        with self.assertRaises(Forbidden):self.execute(db)
    def test_dedicated_database_has_no_tenant_predicate(self):
        db=DB(self.DRIVER);db.indexes=[('pk','id')];self.execute(db,raw_extra={'isolation':'dedicated_database','bound_tenant':'a'})
        sql,params=next((s,p) for s,p in db.calls if s.startswith('UPDATE '))
        self.assertEqual(['Vali','one',1],params);self.assertNotIn('tenant_id',sql)
    def test_catalog_truthful_and_registered(self):
        row=next(r for r in catalog() if r['driver']==self.DRIVER)
        self.assertTrue(row['transport_implemented']);self.assertFalse(row['live_verified'])
        self.assertEqual('network_transport_unverified',row['stage']);self.assertIn(self.DRIVER,TRANSPORTS)


class SQLServerContracts(EnterpriseMixin,unittest.TestCase):
    DRIVER='sqlserver_managed'
    def test_verified_tls_no_retry_driver_and_timeouts(self):
        db=DB(self.DRIVER);self.execute(db)
        cs=db.connect_args[0]
        for item in ['DRIVER={ODBC Driver 18 for SQL Server}','Encrypt=yes','TrustServerCertificate=no','ConnectRetryCount=0','SERVER={tcp:db.example,1433}']:
            self.assertIn(item,cs)
        self.assertFalse(db.pooling);self.assertFalse(db.connect_kwargs['autocommit'])
        self.assertEqual(5,db.connect_kwargs['timeout']);self.assertEqual(2,db.timeout)
        self.assertIn(('SET LOCK_TIMEOUT 1000',None),db.calls)
    def test_odbc_braces_and_semicolon_are_escaped(self):
        self.assertEqual('{x}};Encrypt=no;PWD={y}',_odbc_value('x};Encrypt=no;PWD={y'))
        with self.assertRaises(ValueError):_odbc_value('bad\x00value')
    def test_system_database_and_driver_injection_denied(self):
        for extra in [{'database':'master'},{'schema':'sys'},{'odbc_driver':'arbitrary-driver'},{'database':'customer;Encrypt=no'}]:
            with self.subTest(extra=extra),self.assertRaises((ValueError,Forbidden)):validate_endpoint({**config(),**extra})
    def test_missing_metadata_visibility_fails_closed(self):
        db=DB(self.DRIVER);db.permission=None
        with self.assertRaises(Forbidden):self.execute(db)
    def test_temporal_memory_and_graph_tables_denied(self):
        for index in range(1,6):
            db=DB(self.DRIVER);row=list(db.table);row[index]=1;db.table=tuple(row)
            with self.subTest(index=index),self.assertRaises(Forbidden):self.execute(db)
    def test_case_insensitive_tenant_collation_denied(self):
        db=DB(self.DRIVER);row=list(db.columns[1]);row[6]='Latin1_General_100_CI_AS';db.columns[1]=tuple(row)
        with self.assertRaises(Forbidden):self.execute(db)
    def test_identity_and_computed_columns_denied(self):
        for index in [2,3,4,7]:
            db=DB(self.DRIVER);row=list(db.columns[0]);row[index]=1;db.columns[0]=tuple(row)
            with self.subTest(index=index),self.assertRaises(Forbidden):self.execute(db)
    def test_write_lock_and_index_filter_contract(self):
        db=DB(self.DRIVER);self.execute(db)
        self.assertTrue(any('WITH (TABLOCKX,HOLDLOCK)' in s for s,p in db.calls))
        sql=next(s for s,p in db.calls if 'FROM sys.indexes' in s)
        for guard in ['i.has_filter=0','i.is_disabled=0','ic.is_included_column=0','ic.key_ordinal>0']:
            self.assertIn(guard,sql)
    def test_read_limit_is_bound_top(self):
        db=DB(self.DRIVER);self.execute(db,'read')
        sql,params=next((s,p) for s,p in db.calls if s.startswith('SELECT TOP (?)'))
        self.assertEqual([2,'a'],params)
        self.assertFalse(any('TABLOCKX' in s for s,p in db.calls))


class OracleContracts(EnterpriseMixin,unittest.TestCase):
    DRIVER='oracle_managed'
    def test_tcps_identity_validation_no_retry_and_call_timeout(self):
        db=DB(self.DRIVER);self.execute(db)
        self.assertEqual('tcps',db.params['protocol']);self.assertTrue(db.params['ssl_server_dn_match'])
        self.assertEqual(0,db.params['retry_count']);self.assertEqual(5,db.params['tcp_connect_timeout'])
        self.assertEqual('customer.example',db.params['service_name']);self.assertFalse(db.autocommit)
        self.assertEqual(2000,db.call_timeout)
    def test_thick_mode_denied_before_connect(self):
        db=DB(self.DRIVER);db.is_thin_mode=lambda:False
        with self.assertRaises(Forbidden):self.execute(db)
        self.assertEqual(0,db.connect_calls)
    def test_service_descriptor_injection_and_system_schema_denied(self):
        for extra in [{'service_name':'x)(HOST=evil)'},{'service_name':''},{'schema':'SYS'},{'service_name':'x/y'}]:
            with self.subTest(extra=extra),self.assertRaises((ValueError,Forbidden)):validate_endpoint({**config(self.DRIVER),**extra})
    def test_binary_session_and_readonly_transaction(self):
        db=DB(self.DRIVER);self.execute(db,'read')
        self.assertEqual(["ALTER SESSION SET NLS_COMP=BINARY","ALTER SESSION SET NLS_SORT=BINARY",'SET TRANSACTION READ ONLY'],[s for s,p in db.calls[:3]])
        self.assertFalse(any(s.startswith('LOCK TABLE') for s,p in db.calls))
    def test_empty_string_and_boolean_denied_before_connect(self):
        for value in ['',True,False]:
            db=DB(self.DRIVER)
            with self.subTest(value=value),self.assertRaises(ValueError):self.execute(db,extra={'values':{'name':value}})
            self.assertEqual(0,db.connect_calls)
    def test_null_value_remains_null(self):
        db=DB(self.DRIVER);self.execute(db,extra={'values':{'name':None}})
        sql,params=next((s,p) for s,p in db.calls if s.startswith('UPDATE '));self.assertIsNone(params[0])
    def test_partitioned_temporary_nested_tables_denied(self):
        for table in [('Y','NO',None,'NO','N'),('N','YES',None,'NO','N'),('N','NO','IOT','NO','N'),('N','NO',None,'YES','N')]:
            db=DB(self.DRIVER);db.table=table
            with self.subTest(table=table),self.assertRaises(Forbidden):self.execute(db)
    def test_nonbinary_collation_denied(self):
        db=DB(self.DRIVER);row=list(db.columns[1]);row[8]='BINARY_CI';db.columns[1]=tuple(row)
        with self.assertRaises(Forbidden):self.execute(db)
    def test_virtual_hidden_identity_columns_denied(self):
        for index in [2,3,4]:
            db=DB(self.DRIVER);row=list(db.columns[0]);row[index]='YES';db.columns[0]=tuple(row)
            with self.subTest(index=index),self.assertRaises(Forbidden):self.execute(db)
    def test_version_precision_and_scale_enforced(self):
        for precision,scale in [(10,0),(None,0),(19,2)]:
            db=DB(self.DRIVER);row=list(db.columns[2]);row[6:8]=[precision,scale];db.columns[2]=tuple(row)
            with self.subTest(precision=precision,scale=scale),self.assertRaises(Forbidden):self.execute(db)
    def test_nowait_lock_and_immediate_unique_constraints(self):
        db=DB(self.DRIVER);self.execute(db)
        self.assertTrue(any('IN ROW EXCLUSIVE MODE NOWAIT' in s for s,p in db.calls))
        sql=next(s for s,p in db.calls if 'FROM ALL_CONSTRAINTS c' in s)
        for guard in ["c.DEFERRABLE='NOT DEFERRABLE'","c.STATUS='ENABLED'","c.VALIDATED='VALIDATED'"]:
            self.assertIn(guard,sql)
    def test_read_limit_is_bound_and_identifiers_keep_exact_case(self):
        db=DB(self.DRIVER);self.execute(db,'read')
        sql,params=next((s,p) for s,p in db.calls if s.startswith('SELECT "id"'))
        self.assertIn('"sales"."contacts"',sql);self.assertIn('FETCH FIRST :2 ROWS ONLY',sql)
        self.assertEqual(['a',2],params)


class EnterpriseAuthority(unittest.TestCase):
    def test_approved_engine_dispatch_and_replay_denial_for_each_new_transport(self):
        for driver in ['sqlserver_managed','oracle_managed']:
            with self.subTest(driver=driver),tempfile.TemporaryDirectory() as tmp:
                raw=config(driver);db=DB(driver)
                module='pyodbc' if driver=='sqlserver_managed' else 'oracledb'
                policy={'tools':['database.read','database.plan_write','database.write'],
                        'allowed_connections':['customer'],'ladder':'autonomous','approver_role':'owner','independent_approval':True}
                engine=Engine(Path(tmp)/'platform.sqlite',build_registry(),lambda t,a:policy)
                args={'connection':'customer','request_json':encode(request())}
                with patch('platform_runtime.database.gateway.config',return_value={'connections':{'customer':raw}}),patch.dict('sys.modules',{module:db}),patch.dict('os.environ',{'TEST_DB_PASSWORD':'unit-test-only'}):
                    plan=tool_plan(engine,'a','ops',args,'preview');args['plan_fingerprint']=plan['plan_fingerprint']
                    task=engine.submit('a','web','one','ops',[{'tool':'database.write','args':args}],'creator')
                    sid=engine.get('a',task)['steps'][0]['id']
                    self.assertFalse(engine.tick('a'));self.assertEqual(0,db.connect_calls)
                    engine.approve('a',sid,'reviewer','approved','owner');self.assertTrue(engine.tick('a'))
                    self.assertEqual('succeeded',engine.get('a',task)['status']);self.assertEqual(1,db.connect_calls)
                    with self.assertRaises((Forbidden,Conflict)):tool_write(engine,'a','ops',args,sid)
                    self.assertEqual(1,db.connect_calls)
                    with engine.read() as sql:
                        row=sql.execute('SELECT status,receipt FROM p_database_dispatch').fetchone()
                    self.assertEqual('committed',row['status']);self.assertFalse(json.loads(row['receipt'])['automatic_retry'])
    def test_configuration_change_invalidates_plan_for_both_backends(self):
        for driver in ['sqlserver_managed','oracle_managed']:
            raw=config(driver);args={'connection':'customer','request_json':encode(request())}
            with self.subTest(driver=driver),patch('platform_runtime.database.gateway.config',side_effect=lambda t:{'connections':{'customer':raw}}):
                _,_,fingerprint=prepare('a','ops','database.plan_write',args)
                args['plan_fingerprint']=fingerprint;raw['generation']+=1
                with self.assertRaises(Conflict):prepare('a','ops','database.write',args)
    def test_oracle_lossy_values_rejected_during_planning(self):
        raw=config('oracle_managed')
        with patch('platform_runtime.database.gateway.config',return_value={'connections':{'customer':raw}}):
            with self.assertRaises(ValueError):prepare('a','ops','database.plan_write',{'connection':'customer','request_json':encode(request(values={'name':''}))})

    def test_commit_failure_marks_engine_uncertain_and_never_retries(self):
        for driver in ['sqlserver_managed','oracle_managed']:
            with self.subTest(driver=driver),tempfile.TemporaryDirectory() as tmp:
                raw=config(driver);db=DB(driver);db.fail_commit=True
                module='pyodbc' if driver=='sqlserver_managed' else 'oracledb'
                policy={'tools':['database.plan_write','database.write'],'allowed_connections':['customer'],
                        'ladder':'autonomous','approver_role':'owner','independent_approval':True}
                engine=Engine(Path(tmp)/'platform.sqlite',build_registry(),lambda t,a:policy)
                args={'connection':'customer','request_json':encode(request())}
                with patch('platform_runtime.database.gateway.config',return_value={'connections':{'customer':raw}}),patch.dict('sys.modules',{module:db}),patch.dict('os.environ',{'TEST_DB_PASSWORD':'unit-test-only'}):
                    plan=tool_plan(engine,'a','ops',args,'preview');args['plan_fingerprint']=plan['plan_fingerprint']
                    task=engine.submit('a','web','one','ops',[{'tool':'database.write','args':args}],'creator')
                    sid=engine.get('a',task)['steps'][0]['id'];engine.approve('a',sid,'reviewer','approved','owner')
                    engine.tick('a');self.assertEqual('uncertain',engine.get('a',task)['status'])
                    self.assertFalse(engine.tick('a'));self.assertEqual(1,db.connect_calls)
                    self.assertNotIn('secret',encode(engine.get('a',task)))
                    with engine.read() as sql:
                        self.assertEqual('uncertain',sql.execute('SELECT status FROM p_database_dispatch').fetchone()[0])
