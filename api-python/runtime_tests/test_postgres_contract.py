"""Fake driver tests exercise emitted statements; NOT PostgreSQL live acceptance."""
import decimal
import datetime
import os
import types
import unittest
from unittest.mock import patch
from platform_runtime.postgres_connector import validate_config,compile_read,read,cell
from platform_runtime.connector_contract import descriptor
from platform_runtime.engine import Forbidden


def config():return {'driver':'postgres_readonly','host':'db.example','allowed_hosts':['db.example'],'port':5432,
 'database':'db','user':'reader','password_env':'TEST_PG_PASSWORD','sslmode':'verify-full','schema':'sales',
 'isolation':'tenant_column','tenant_column':'tenant_id','tables':{'contacts':['id','tenant_id','name']}}
REQ={'connection':'db','table':'contacts','columns':['id','name'],'limit':5}


class FakeCursor:
    def __init__(self,driver,named=False):self.d=driver;self.named=named
    def __enter__(self):return self
    def __exit__(self,*args):self.d.closed+=1
    def execute(self,sql,params=None):self.d.calls.append((sql,params))
    def fetchone(self):
        if not self.named:return (self.d.kind,)
        return self.d.records.pop(0) if self.d.records else None


class FakeDriver:
    def __init__(self):self.calls=[];self.closed=0;self.records=[(1,'Ali')];self.kind='r';self.kwargs=None
    def connect(self,**kwargs):self.kwargs=kwargs;return self
    def cursor(self,name=None):return FakeCursor(self,bool(name))
    def __enter__(self):return self
    def __exit__(self,*args):self.closed+=1


class PostgresTests(unittest.TestCase):
    def test_conninfo_expansion_in_database_denied(self):
        for name in ['host=evil password=secret','postgresql://evil/db']:
            with self.assertRaises(ValueError):validate_config({**config(),'database':name})
    def test_explicit_allowlist_required(self):
        c=config();del c['allowed_hosts']
        with self.assertRaises(ValueError):validate_config(c)
    def test_tls_verification_required(self):
        for mode in ['require','disable','prefer','allow',None]:
            with self.subTest(mode=mode),self.assertRaises(ValueError):validate_config({**config(),'sslmode':mode})
    def test_explicit_isolation_required(self):
        c=config();del c['isolation']
        with self.assertRaises(ValueError):validate_config(c)
    def test_every_table_needs_tenant_column(self):
        c=config();c['tables']['orders']=['id']
        with self.assertRaises(ValueError):validate_config(c)
    def test_sql_is_schema_qualified_and_tenant_bound(self):
        _,sql,params=compile_read('tenant-a',config(),REQ)
        self.assertIn('FROM "sales"."contacts"',sql);self.assertIn('"tenant_id" = %s',sql);self.assertEqual(['tenant-a',5],params)
    def test_filter_injection_is_parameter(self):
        attack="' OR 1=1 --";_,sql,params=compile_read('a',config(),{**REQ,'where':{'column':'name','equals':attack}})
        self.assertNotIn(attack,sql);self.assertEqual(['a',attack,5],params)
    def test_forbidden_sql_and_column(self):
        with self.assertRaises(ValueError):compile_read('a',config(),{**REQ,'sql':'DROP TABLE contacts'})
        with self.assertRaises(Forbidden):compile_read('a',config(),{**REQ,'columns':['password']})
    def test_system_schema_denied(self):
        with self.assertRaises(Forbidden):validate_config({**config(),'schema':'pg_catalog'})
    def test_decimal_precision_and_binary_omission(self):
        self.assertEqual('1.2300',cell(decimal.Decimal('1.2300')));self.assertEqual('[binary omitted]',cell(b'secret'))
        self.assertEqual('2026-09-14',cell(datetime.date(2026,9,14)))
    def test_nonfinite_and_complex_types_denied(self):
        for v in [float('nan'),decimal.Decimal('Infinity'),{'secret':1}]:
            with self.assertRaises(ValueError):cell(v)
    def test_contract_emits_readonly_and_closes(self):
        d=FakeDriver()
        with patch.dict(os.environ,{'TEST_PG_PASSWORD':'unit-test-only'}),patch.dict('sys.modules',{'psycopg':d}):result=read('a',config(),REQ)
        self.assertEqual([{'id':1,'name':'Ali'}],result['rows']);self.assertEqual('SET TRANSACTION READ ONLY',d.calls[0][0]);self.assertEqual(3,d.closed)
        self.assertEqual('verify-full',d.kwargs['sslmode']);self.assertIn('statement_timeout=2000',d.kwargs['options'])
    def test_view_denied_before_data_read(self):
        d=FakeDriver();d.kind='v'
        with patch.dict(os.environ,{'TEST_PG_PASSWORD':'unit-test-only'}),patch.dict('sys.modules',{'psycopg':d}),self.assertRaises(Forbidden):read('a',config(),REQ)
        self.assertFalse(any('LIMIT' in sql for sql,_ in d.calls));self.assertEqual(2,d.closed)
    def test_large_row_rejected_and_connection_closed(self):
        d=FakeDriver();d.records=[(1,'x'*17000)]
        with patch.dict(os.environ,{'TEST_PG_PASSWORD':'unit-test-only'}),patch.dict('sys.modules',{'psycopg':d}),self.assertRaises(ValueError):read('a',config(),REQ)
        self.assertEqual(3,d.closed)
    def test_declared_health_cannot_fake_probe(self):
        d=descriptor('db',{'driver':'postgres_readonly','lifecycle':'healthy'},live_drivers={'postgres_readonly'})
        self.assertEqual('configured_not_live_verified',d.status)
    def test_readonly_cannot_advertise_write(self):
        with self.assertRaises(ValueError):descriptor('db',{'driver':'postgres_readonly','capabilities':['execute_write']},live_drivers={'postgres_readonly'})
