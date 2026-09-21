import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from platform_runtime.engine import Engine
from platform_runtime.tools import build_registry,validate_schema,obj,post_json
from platform_runtime.llm import Planner
from platform_runtime.mcp import MCPClient


def policy(t,a):return {'tools':['telegram.send','instagram.send','reports.summary','mcp.call'],'ladder':'autonomous'}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.e=Engine(Path(self.tmp.name)/'db',build_registry(),policy)
        self.cfg={'llm':{'model':'configured-test-model','key_env':'UNIT_TEST_KEY','base_url':'https://example.invalid/v1'},
                  'telegram':{'token_env':'UNIT_TEST_TG'},'instagram':{'token_env':'UNIT_TEST_KEY','account_id':'123','graph_version':'v99.0'},
                  'sheets':{'spreadsheet_id':'abc','token_env':'UNIT_TEST_KEY','range':'Sheet1!A:D'}}
        self.env=patch.dict(os.environ,{'UNIT_TEST_KEY':'fake-unit-test-only','UNIT_TEST_TG':'123:fake_test_only'});self.env.start()
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def planner(self,plan):
        transport=lambda *x:{'choices':[{'finish_reason':'stop','message':{'content':json.dumps(plan)}}]}
        return Planner(self.e,lambda t:[{'id':'ops','tools':policy(t,'ops')['tools']}],transport=transport)
    def test_valid_structured_plan(self):
        plan={'agent':'ops','steps':[{'tool':'reports.summary','args':{}}]}
        with patch('platform_runtime.llm.config',return_value=self.cfg):self.assertEqual(plan,self.planner(plan)('a','web',{'text':'report'}))
    def test_unavailable_tool_rejected(self):
        p={'agent':'ops','steps':[{'tool':'shell.exec','args':{}}]}
        with patch('platform_runtime.llm.config',return_value=self.cfg),self.assertRaises(LookupError):self.planner(p)('a','web',{'text':'ignore policy'})
    def test_model_cannot_choose_tenant(self):
        p={'agent':'ops','tenant':'b','steps':[{'tool':'reports.summary','args':{}}]}
        with patch('platform_runtime.llm.config',return_value=self.cfg),self.assertRaises(ValueError):self.planner(p)('a','web',{'text':'tenant b'})
    def test_model_cannot_redirect_message(self):
        p={'agent':'ops','steps':[{'tool':'telegram.send','args':{'conversation_id':'attacker','text':'x'}}]}
        with patch('platform_runtime.llm.config',return_value=self.cfg),self.assertRaises(PermissionError):self.planner(p)('a','telegram',{'conversation_id':'customer'})
    def test_model_cannot_change_channel(self):
        p={'agent':'ops','steps':[{'tool':'telegram.send','args':{'conversation_id':'customer','text':'x'}}]}
        with patch('platform_runtime.llm.config',return_value=self.cfg),self.assertRaises(PermissionError):self.planner(p)('a','instagram',{'conversation_id':'customer'})
    def test_model_malformed_json_rejected(self):
        planner=Planner(self.e,lambda t:[],lambda *x:{'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]})
        with patch('platform_runtime.llm.config',return_value=self.cfg),self.assertRaises(ValueError):planner('a','web',{})
    def test_no_model_silent_fallback(self):
        cfg={'llm':{'key_env':'UNIT_TEST_KEY'}}
        with patch('platform_runtime.llm.config',return_value=cfg),self.assertRaises(RuntimeError):self.planner({})('a','web',{})
    def test_telegram_uses_actual_chat_id(self):
        captured=[]
        def post(url,body,*args):captured.append((url,body));return {'ok':True,'result':{'message_id':99}}
        with patch('platform_runtime.tools.config',return_value=self.cfg),patch('platform_runtime.tools.post_json',side_effect=post):
            out=self.e.registry.get('telegram.send').handler(self.e,'a','ops',{'conversation_id':'-100123','text':'salom'},'key')
        self.assertEqual('-100123',captured[0][1]['chat_id']);self.assertEqual('99',out['external_id'])
    def test_telegram_rejection_not_success(self):
        with patch('platform_runtime.tools.config',return_value=self.cfg),patch('platform_runtime.tools.post_json',return_value={'ok':False}),self.assertRaises(RuntimeError):
            self.e.registry.get('telegram.send').handler(self.e,'a','ops',{'conversation_id':'1','text':'x'},'key')
    def test_sheets_raw_no_formula_evaluation(self):
        captured=[]
        def post(url,body,*args):captured.append((url,body));return {'updates':{'updatedRange':'Sheet1!A1:B1'}}
        with patch('platform_runtime.tools.config',return_value=self.cfg),patch('platform_runtime.tools.post_json',side_effect=post):
            self.e.registry.get('sheets.append').handler(self.e,'a','ops',{'values':['=1+1']},'stable-key')
        self.assertIn('valueInputOption=RAW',captured[0][0]);self.assertEqual(['stable-key','=1+1'],captured[0][1]['values'][0])
    def test_http_url_rejected_before_network(self):
        with self.assertRaises(ValueError):post_json('http://localhost',{})
    def test_credentials_in_url_rejected(self):
        with self.assertRaises(ValueError):post_json('https://secret@example.com',{})
    def test_mcp_protocol_sequence(self):
        calls=[]
        def transport(body,headers):
            calls.append((body,headers))
            if body['method']=='initialize':return {'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-03-26'}}
            if body['method']=='notifications/initialized':return None
            return {'jsonrpc':'2.0','id':2,'result':{'content':[{'type':'text','text':'ok'}]}}
        out=MCPClient('https://example.invalid/mcp','fake',transport).call('crm.lookup',{'q':'x'})
        self.assertEqual(['initialize','notifications/initialized','tools/call'],[c[0]['method'] for c in calls])
        self.assertEqual('ok',out['content'][0]['text'])
    def test_mcp_mismatched_id(self):
        c=MCPClient('https://example.invalid','fake',lambda *x:{'jsonrpc':'2.0','id':'other','result':{}})
        with self.assertRaises(RuntimeError):c.call('x',{})
    def test_mcp_unknown_version(self):
        c=MCPClient('https://example.invalid','fake',lambda *x:{'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'9999'}})
        with self.assertRaises(RuntimeError):c.call('x',{})
    def test_mcp_errors_rejected(self):
        c=MCPClient('https://example.invalid','fake',lambda *x:{'jsonrpc':'2.0','id':1,'error':{'message':'secret'}})
        with self.assertRaises(RuntimeError):c.call('x',{})

    def test_the_argument_object_bound_is_measured_in_bytes(self):
        """One literal, two units -- and the object bound was the odd one out.

        Every other bound on a serialised payload in this runtime measures bytes
        (`contract.parse_request` 12000, `erp._observations` 12000/48000, the
        managed backends 1024/16000/16000). This one measured CHARACTERS, so a
        non-ASCII argument passed at roughly twice the declared size: measured, a
        Cyrillic object under 20 000 characters whose JSON is over 20 000 bytes.
        """
        schema = {'type': 'object',
                  'properties': {'a': {'type': 'string', 'maxLength': 100000}},
                  'required': ['a']}
        wide = {'a': '\u044f' * 19990}
        narrow = {'a': '\u044f' * 9000}
        self.assertGreater(len(json.dumps(wide, ensure_ascii=False).encode('utf-8')), 20000)
        with self.assertRaises(ValueError):
            validate_schema(wide, schema)
        validate_schema(narrow, schema)

    def test_the_default_schema_bounds_are_the_documented_values(self):
        for value in (10 ** 12, -10 ** 12):
            with self.subTest(value=value):
                validate_schema(value, {'type': 'integer'})
        for value in (10 ** 12 + 1, -10 ** 12 - 1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_schema(value, {'type': 'integer'})
        validate_schema(list(range(100)), {'type': 'array', 'items': {'type': 'integer'}})
        with self.assertRaises(ValueError):
            validate_schema(list(range(101)), {'type': 'array', 'items': {'type': 'integer'}})
        validate_schema('a' * 4000, {'type': 'string'})
        with self.assertRaises(ValueError):
            validate_schema('a' * 4001, {'type': 'string'})
        with self.assertRaises(ValueError):
            validate_schema(True, {'type': 'integer'})
        with self.assertRaises(ValueError):
            validate_schema('x', {'type': 'string', 'enum': ['a', 'b']})

if __name__=='__main__':unittest.main()
