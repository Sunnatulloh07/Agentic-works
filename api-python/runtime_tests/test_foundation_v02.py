import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry, Tool, obj, mcp_call
from platform_runtime.connectors import read_rows, describe
from platform_runtime.speech import AishaREST, SpeechError, MAX_AUDIO
from app.config import validate_runtime_config, ConfigError
from app.security import dev_open, admin_ok


def policy(tenant, agent):
    return {'tools':['reports.summary','records.create','connectors.read','voice.tts'],
            'ladder':'autonomous','allowed_connections':['crm'],'independent_approval':True}


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.e=Engine(Path(self.tmp.name)/'platform.db',build_registry(),policy)
    def tearDown(self):self.tmp.cleanup()
    def task(self,steps=None,key='1'):
        return self.e.submit('a','web',key,'ops',steps or [{'tool':'reports.summary','args':{}}],'alice')
    def test_missing_env_not_open(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(dev_open());self.assertFalse(admin_ok(None))
    def test_dev_requires_explicit_opt_in(self):
        with patch.dict(os.environ,{'ENV':'dev'},clear=True):self.assertFalse(admin_ok(None))
    def test_explicit_dev_opt_in(self):
        with patch.dict(os.environ,{'ENV':'test','ALLOW_INSECURE_DEV':'true'},clear=True):self.assertTrue(admin_ok(None))
    def test_prod_ignores_insecure_flag(self):
        with patch.dict(os.environ,{'ENV':'production','ALLOW_INSECURE_DEV':'true'},clear=True):self.assertFalse(admin_ok(None))
    def test_config_dev_secrets_required(self):
        with self.assertRaises(ConfigError):validate_runtime_config({'ENV':'dev'})
    def test_known_demo_secret_rejected_outside_insecure_mode(self):
        with self.assertRaises(ConfigError):
            validate_runtime_config({'ENV':'production','JWT_SECRET':'dev-only-secret-change-in-prod-32chars',
                'ADMIN_TOKEN':'a'*32,'TELEGRAM_WEBHOOK_SECRET':'t'*32})
    def test_explicit_test_defaults(self):
        self.assertFalse(validate_runtime_config({'ENV':'test','ALLOW_INSECURE_DEV':'true'}).production)
    def test_independent_approval(self):
        tid=self.task([{'tool':'records.create','args':{'kind':'lead','title':'x','body':'x'}}])
        sid=self.e.get('a',tid)['steps'][0]['id']
        with self.assertRaises(Forbidden):self.e.approve('a',sid,'alice','approved','owner')
        self.e.approve('a',sid,'bob','approved','owner')
        self.assertTrue(self.e.tick('a'))
    def test_freeze_blocks_new_task_but_replay_safe(self):
        tid=self.task();self.e.freeze('a',True,'owner')
        self.assertEqual(tid,self.task())
        with self.assertRaises(Forbidden):self.task(key='2')
    def test_freeze_blocks_new_event(self):
        self.e.freeze('a',True,'owner')
        with self.assertRaises(Forbidden):self.e.accept_event('a','web','1',{'text':'x'})
    def test_freeze_blocks_schedule(self):
        self.e.freeze('a',True,'owner')
        with self.assertRaises(Forbidden):self.e.schedule('a','daily','ops',[{'tool':'reports.summary','args':{}}],60)
    def test_freeze_blocks_approval(self):
        tid=self.task([{'tool':'records.create','args':{'kind':'lead','title':'x','body':'x'}}]);sid=self.e.get('a',tid)['steps'][0]['id']
        self.e.freeze('a',True,'owner')
        with self.assertRaises(Forbidden):self.e.approve('a',sid,'bob','approved','owner')
    def test_freeze_after_claim_before_dispatch(self):
        calls=[]
        self.e.registry.add(Tool('test.read','read',obj({}),lambda *a:calls.append(1)))
        self.e.policy=lambda t,a:{'tools':['test.read'],'ladder':'autonomous'}
        self.task([{'tool':'test.read','args':{}}]);original=self.e.claim
        def frozen_claim(*a,**k):
            step=original(*a,**k);self.e.freeze('a',True,'owner');return step
        with patch.object(self.e,'claim',side_effect=frozen_claim):
            self.assertFalse(self.e.tick('a'))
        self.assertEqual([],calls)
        self.assertEqual('uncertain',self.e.list_tasks('a')[0]['status'])
    def test_agent_connection_denied_at_submit(self):
        with self.assertRaises(Forbidden):self.task([{'tool':'connectors.read','args':{'connection':'other','table':'contacts','columns':['name']}}])
    def test_tts_requires_approval(self):
        tid=self.task([{'tool':'voice.tts','args':{'text':'Salom'}}])
        self.assertFalse(self.e.tick('a'));self.assertEqual('waiting_approval',self.e.get('a',tid)['status'])
    def test_mcp_schema_required_before_network(self):
        cfg={'mcp':{'allowed_tools':['crm.read']}}
        with patch('platform_runtime.tools.config',return_value=cfg),self.assertRaises(PermissionError):
            mcp_call(None,'a','ops',{'name':'crm.read','arguments_json':'{}'},'s')
    def test_mcp_unknown_argument_rejected(self):
        cfg={'mcp':{'allowed_tools':['crm.read'],'tool_schemas':{'crm.read':obj({})}}}
        with patch('platform_runtime.tools.config',return_value=cfg),self.assertRaises(ValueError):
            mcp_call(None,'a','ops',{'name':'crm.read','arguments_json':'{"delete":true}'},'s')


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'customer.db'
        with sqlite3.connect(self.db) as c:
            c.executescript("CREATE TABLE contacts(id INTEGER PRIMARY KEY,name TEXT,password TEXT); INSERT INTO contacts VALUES(1,'Ali','hidden'); CREATE VIEW v AS SELECT * FROM contacts;")
        c.close()
        self.cfg={'connections':{'crm':{'driver':'sqlite_readonly','path':str(self.db),'tables':{'contacts':['id','name']}}}}
        self.env=patch.dict(os.environ,{'PLATFORM_DB_ROOTS':json.dumps([str(self.root)])});self.env.start()
        self.cp=patch('platform_runtime.connectors.config',side_effect=lambda tenant:self.cfg if tenant=='a' else {});self.cp.start()
        self.req={'connection':'crm','table':'contacts','columns':['id','name'],'limit':10}
    def tearDown(self):self.cp.stop();self.env.stop();self.tmp.cleanup()
    def test_actual_read(self):self.assertEqual([{'id':1,'name':'Ali'}],read_rows('a',self.req)['rows'])
    def test_column_scope(self):
        with self.assertRaises(Forbidden):read_rows('a',{**self.req,'columns':['password']})
    def test_cross_tenant_denied(self):
        with self.assertRaises(Forbidden):read_rows('b',self.req)
    def test_table_scope(self):
        with self.assertRaises(Forbidden):read_rows('a',{**self.req,'table':'sqlite_master'})
    def test_no_arbitrary_sql(self):
        with self.assertRaises(ValueError):read_rows('a',{**self.req,'sql':'DROP TABLE contacts'})
    def test_filter_parameterized(self):
        out=read_rows('a',{**self.req,'where':{'column':'name','equals':"' OR 1=1 --"}})
        self.assertEqual([],out['rows'])
    def test_exact_filter(self):
        out=read_rows('a',{**self.req,'where':{'column':'name','equals':'Ali'}})
        self.assertEqual(1,out['returned'])
    def test_limit_rejected(self):
        for limit in [0,101,True,'10']:
            with self.subTest(limit=limit),self.assertRaises(ValueError):read_rows('a',{**self.req,'limit':limit})
    def test_internal_database_denied(self):
        with self.assertRaises(Forbidden):read_rows('a',self.req,self.db)
    def test_mount_scope(self):
        with patch.dict(os.environ,{'PLATFORM_DB_ROOTS':json.dumps(['/var'])}),self.assertRaises(Forbidden):read_rows('a',self.req)
    def test_no_root_default(self):
        with patch.dict(os.environ,{'PLATFORM_DB_ROOTS':'[]'}),self.assertRaises(ValueError):read_rows('a',self.req)
    def test_view_denied(self):
        self.cfg['connections']['crm']['tables']['v']=['name']
        with self.assertRaises(Forbidden):read_rows('a',{**self.req,'table':'v','columns':['name']})
    def test_large_result_rejected(self):
        with sqlite3.connect(self.db) as c:c.execute('UPDATE contacts SET name=?',('x'*17000,))
        c.close()
        with self.assertRaises(ValueError):read_rows('a',self.req)
    def test_metadata_no_path(self):
        info=describe('a');self.assertNotIn(str(self.db),json.dumps(info));self.assertNotIn('password',json.dumps(info))
    def test_disabled_entry_does_not_break_catalog(self):
        self.cfg['connections']['disabled']={'enabled':False}
        self.assertEqual(['crm'],[c['id'] for c in describe('a')])
    def test_disabled_connection(self):
        self.cfg['connections']['crm']['enabled']=False
        with self.assertRaises(Forbidden):read_rows('a',self.req)
    def test_actual_engine_connection(self):
        engine=Engine(self.root/'platform.db',build_registry(),policy)
        tid=engine.submit('a','web','1','ops',[{'tool':'connectors.read','args':self.req}],'alice')
        self.assertTrue(engine.tick('a'))
        detail=engine.get('a',tid);self.assertEqual('succeeded',detail['status'])
        self.assertEqual('Ali',detail['steps'][0]['result']['rows'][0]['name'])


class SpeechTests(unittest.TestCase):
    def client(self,status=201,response=None):
        self.calls=[]
        def transport(url,data,headers):self.calls.append((url,data,headers));return status,response if response is not None else {'audio_path':'/media/tts_audios/test.wav'}
        return AishaREST('fake-unit-key',transport)
    def test_tts_exact_multipart_contract(self):
        result=self.client().synthesize('O‘zbekcha salom')
        url,data,headers=self.calls[0]
        self.assertEqual('https://back.aisha.group/api/v1/tts/post/',url)
        self.assertNotIn('fake-unit-key',url)
        self.assertIn(b'name="transcript"',data);self.assertIn('O‘zbekcha salom'.encode(),data)
        self.assertIn(b'Gulnoza',data);self.assertEqual('fake-unit-key',headers['X-Api-Key'])
        self.assertFalse(result['download_verified'])
    def test_tts_remote_path_rejected(self):
        for path in ['https://evil.invalid/a.wav','//evil.invalid/a.wav','/media/tts_audios/../secret.wav','/media/tts_audios/%2e%2e.wav','/media/tts_audios/a.wav?key=x']:
            with self.subTest(path=path),self.assertRaises(SpeechError):self.client(response={'audio_path':path}).synthesize('Salom')
    def test_no_empty_or_overlong_text(self):
        for text in ['', ' '*5,'x'*1001]:
            with self.assertRaises(ValueError):self.client().synthesize(text)
    def test_mood_and_speed_validation(self):
        with self.assertRaises(ValueError):self.client().synthesize('Salom',mood='fake')
        with self.assertRaises(ValueError):self.client().synthesize('Salom',speed=True)
    def test_unexpected_status_not_success(self):
        with self.assertRaises(SpeechError):self.client(status=202).synthesize('Salom')
    def test_no_retry_and_sanitized_failure(self):
        count=[]
        def fail(*a):count.append(1);raise RuntimeError('sensitive-provider-detail')
        with self.assertRaises(SpeechError) as cm:AishaREST('fake',fail).synthesize('Salom')
        self.assertNotIn('sensitive-provider-detail',str(cm.exception));self.assertEqual(1,len(count))
    def test_stt_exact_contract(self):
        result=self.client(200,{'transcript':'Salom'}).transcribe(b'RIFF-test-bytes')
        url,data,headers=self.calls[0];self.assertTrue(url.endswith('/api/v1/stt/post/'))
        self.assertIn(b'name="audio"; filename="audio.wav"',data)
        self.assertIn(b'RIFF-test-bytes',data);self.assertEqual('Salom',result['transcript'])
    def test_stt_limits(self):
        for data in [b'',b'x'*(MAX_AUDIO+1),'text']:
            with self.assertRaises(ValueError):self.client().transcribe(data)
    def test_stt_response_validated(self):
        with self.assertRaises(SpeechError):self.client(200,{'text':'wrong key'}).transcribe(b'data')

if __name__=='__main__':unittest.main()
