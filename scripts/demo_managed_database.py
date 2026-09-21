"""Offline, real SQLite plan -> approval -> scoped update -> read demonstration."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from contextlib import closing
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api-python'))
from platform_runtime.engine import Engine, encode
from platform_runtime.tools import build_registry


def main():
    with tempfile.TemporaryDirectory(prefix='managed-database-demo-') as temp:
        root = Path(temp)
        customer = root / 'customer.sqlite'
        with closing(sqlite3.connect(customer)) as db, db:
            db.executescript("CREATE TABLE contacts(tenant_id TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,name TEXT,PRIMARY KEY(tenant_id,id));"
                             "INSERT INTO contacts VALUES('demo','one',1,'Ali');"
                             "INSERT INTO contacts VALUES('other','one',1,'Other tenant');")
        raw = {'driver':'sqlite_managed','contract_version':'1.1','generation':1,'enabled':True,
               'lifecycle':'configured','agent_ids':['ops'],'capabilities':['read','plan_write','execute_write'],
               'isolation':'tenant_column','tenant_column':'tenant_id','path':str(customer),
               'resources':{'contacts':{'key_field':'id','version_field':'version',
                                        'read_fields':['id','name','version'],'insert_fields':['name'],'update_fields':['name']}}}
        settings = root / 'integrations.json'
        settings.write_text(encode({'demo':{'connections':{'customer':raw}}}))
        policy = {'tools':['database.read','database.plan_write','database.write'], 'allowed_connections':['customer'],
                  'ladder':'autonomous','approver_role':'owner','independent_approval':True}
        with patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE':str(settings),'PLATFORM_DB_ROOTS':encode([str(root)])}):
            engine = Engine(root / 'platform.sqlite', build_registry(), lambda tenant, agent:policy)
            args = {'connection':'customer','request_json':encode({'operation':'update','resource':'contacts',
                    'key':'one','expected_version':1,'values':{'name':'Vali'}})}
            plan_task = engine.submit('demo','web','plan','ops',[{'tool':'database.plan_write','args':args}],'creator')
            engine.tick('demo')
            preview = engine.get('demo',plan_task)['steps'][0]['result']
            args['plan_fingerprint'] = preview['plan_fingerprint']
            write_task = engine.submit('demo','web','write','ops',[{'tool':'database.write','args':args}],'creator')
            assert engine.tick('demo') is False
            assert engine.get('demo',write_task)['status'] == 'waiting_approval'
            sid = engine.get('demo',write_task)['steps'][0]['id']
            engine.approve('demo',sid,'independent-owner','approved','owner')
            engine.tick('demo')
            assert engine.get('demo',write_task)['status'] == 'succeeded'
            read_args = {'connection':'customer','request_json':encode({'operation':'read','resource':'contacts','fields':['id','name','version']})}
            read_task = engine.submit('demo','web','read','ops',[{'tool':'database.read','args':read_args}],'creator')
            engine.tick('demo')
            result = engine.get('demo',read_task)['steps'][0]['result']
            assert result['rows'] == [{'id':'one','name':'Vali','version':2}]
            with closing(sqlite3.connect(customer)) as db:
                assert db.execute("SELECT name FROM contacts WHERE tenant_id='other'").fetchone()[0] == 'Other tenant'
            print(encode({'demo':'managed_database_sqlite','approval_gate':'PASS','tenant_isolation':'PASS',
                          'version_cas':'PASS','rows':result['rows'],'live_network_test':False}))


if __name__ == '__main__':
    main()
