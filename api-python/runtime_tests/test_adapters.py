import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from platform_runtime.engine import Engine,encode
from platform_runtime.tools import (build_registry,validate_schema,obj,string,post_json,
                                    secret,Registry,Tool,MAX_SCHEMA_DEPTH,MAX_ARGUMENT_BYTES,
                                    DEFAULT_MAX_ITEMS,DEFAULT_STRING_LENGTH,INTEGER_BOUND,
                                    MAX_PROVIDER_RESPONSE,PROVIDER_TIMEOUT_SECONDS,
                                    MEMORY_SEARCH_LIMIT,RECORDS_LIST_LIMIT,
                                    CREDENTIAL_NAME,RISK_LEVELS)
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

def _object_of_bytes(n):
    """An object whose serialised form is exactly ``n`` bytes.

    ``encode`` writes compact JSON, so ``{"a":""}`` is eight bytes and the padding
    sets the size exactly. The test asserts that precondition rather than assuming
    it -- the first version of this helper assumed a space after the colon and was
    one byte short.
    """
    return {'a': 'x' * (n - 8)}


def _nested(levels):
    """A schema and a matching value, nested ``levels`` deep."""
    schema = {'type': 'string'}
    value = 'x'
    for _ in range(levels):
        schema = {'type': 'object', 'properties': {'a': schema}, 'required': ['a']}
        value = {'a': value}
    return schema, value


class _Response:
    def __init__(self, body): self.body = body
    def read(self, size): return self.body[:size]
    def __enter__(self): return self
    def __exit__(self, *exc): return False


class DeclaredBoundTests(unittest.TestCase):
    """Every bound ``platform_runtime/tools.py`` declares, pinned and walked.

    The literals are asserted directly rather than read back out of the module. A
    test that re-reads the constant moves BOTH sides of its own assertion when the
    constant widens, which is how six of ``sheets.py``'s bounds were once reported
    green (ULTRA-AUDIT §142.5). Each bound is walked on both sides, so a guard that
    refuses everything fails just as loudly as one that refuses nothing.

    Why this class exists: a fifteen-mode revert matrix left **ten** of these
    bounds green. The module was the gate every tool argument passes through, and
    the one whose ``Registry.add`` message the registration contract's own
    docstring calls out as uninformative -- yet nothing measured any of it.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.e = Engine(Path(self.tmp.name) / 'db', build_registry(), policy)
        self.env = patch.dict(os.environ, {'UNIT_TEST_KEY': 'fake-unit-test-only'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_the_argument_object_ceiling_is_20000_bytes(self):
        schema = {'type': 'object', 'properties': {'a': {'type': 'string', 'maxLength': 100000}},
                  'required': ['a']}
        self.assertEqual(20000, MAX_ARGUMENT_BYTES)
        at_limit = _object_of_bytes(20000)
        over = _object_of_bytes(20001)
        self.assertEqual(20000, len(encode(at_limit).encode('utf-8')))
        self.assertEqual(20001, len(encode(over).encode('utf-8')))
        validate_schema(at_limit, schema)
        with self.assertRaises(ValueError):
            validate_schema(over, schema)

    def test_the_array_and_string_floor_defaults_are_zero(self):
        validate_schema([], {'type': 'array', 'items': {'type': 'integer'}})
        validate_schema('', {'type': 'string'})
        with self.assertRaises(ValueError):
            validate_schema([], {'type': 'array', 'minItems': 1, 'items': {'type': 'integer'}})
        with self.assertRaises(ValueError):
            validate_schema('', {'type': 'string', 'minLength': 1})

    def test_the_default_ceilings_are_100_and_4000(self):
        self.assertEqual(100, DEFAULT_MAX_ITEMS)
        self.assertEqual(4000, DEFAULT_STRING_LENGTH)
        validate_schema(list(range(100)), {'type': 'array', 'items': {'type': 'integer'}})
        with self.assertRaises(ValueError):
            validate_schema(list(range(101)), {'type': 'array', 'items': {'type': 'integer'}})
        validate_schema('a' * 4000, {'type': 'string'})
        with self.assertRaises(ValueError):
            validate_schema('a' * 4001, {'type': 'string'})

    def test_the_integer_range_is_plus_minus_10_to_the_12(self):
        self.assertEqual(10 ** 12, INTEGER_BOUND)
        for edge in (INTEGER_BOUND, -INTEGER_BOUND):
            validate_schema(edge, {'type': 'integer'})
        for over in (INTEGER_BOUND + 1, -INTEGER_BOUND - 1):
            with self.assertRaises(ValueError):
                validate_schema(over, {'type': 'integer'})

    def test_the_shared_string_helper_states_the_same_ceiling(self):
        """One number, two places -- and only one of them was pinned.

        ``string()`` is the default every tool field uses, so widening it widens
        every field that declares no maximum. The matrix left it green while the
        schema branch's identical 4000 went red.
        """
        self.assertEqual(4000, string()['maxLength'])
        self.assertEqual(1, string()['minLength'])
        self.assertEqual(4000, DEFAULT_STRING_LENGTH)
        self.assertEqual(10, string(10)['maxLength'])
        validate_schema('a' * 4000, string())
        with self.assertRaises(ValueError):
            validate_schema('a' * 4001, string())

    def test_the_provider_response_ceiling_is_one_megabyte(self):
        self.assertEqual(1_000_000, MAX_PROVIDER_RESPONSE)
        seen = {}

        def build(*_args, **_kwargs):
            return type('Opener', (), {
                'open': lambda _self, request, timeout=None: (
                    seen.__setitem__('timeout', timeout),
                    _Response(seen['body']))[1]})()

        def post(body):
            seen['body'] = body
            with patch('platform_runtime.tools.urllib.request.build_opener', build):
                return post_json('https://example.invalid/x', {})

        at_limit = b'"' + b'x' * (1_000_000 - 2) + b'"'
        over = b'"' + b'x' * (1_000_001 - 2) + b'"'
        self.assertEqual(1_000_000, len(at_limit))
        self.assertEqual(1_000_001, len(over))
        self.assertEqual('x' * 5, post(b'"xxxxx"'))
        post(at_limit)
        with self.assertRaises(ValueError):
            post(over)

    def test_the_provider_timeout_is_25_seconds(self):
        self.assertEqual(25, PROVIDER_TIMEOUT_SECONDS)
        seen = {}
        opener = type('Opener', (), {
            'open': lambda _self, request, timeout=None: (
                seen.__setitem__('timeout', timeout), _Response(b'{}'))[1]})()
        with patch('platform_runtime.tools.urllib.request.build_opener', return_value=opener):
            post_json('https://example.invalid/x', {})
        self.assertEqual(25, seen['timeout'])

    def test_the_read_ceilings_are_10_and_50_rows(self):
        self.assertEqual(10, MEMORY_SEARCH_LIMIT)
        self.assertEqual(50, RECORDS_LIST_LIMIT)
        with self.e.tx() as c:
            for i in range(25):
                c.execute('INSERT INTO p_memory VALUES(?,?,?,?,?)',
                          ('a', 'ops', f'k{i}', f'needle {i}', 0))
            for i in range(60):
                c.execute('INSERT INTO p_records VALUES(?,?,?,?,?)',
                          ('a', 'kind', f'id{i}', '{}', i))
        memory = self.e.registry.get('memory.search').handler(
            self.e, 'a', 'ops', {'query': 'needle'}, 'key')
        records = self.e.registry.get('records.list').handler(
            self.e, 'a', 'ops', {'kind': 'kind'}, 'key')
        self.assertEqual(10, len(memory['matches']))
        self.assertEqual(50, len(records['records']))

    def test_a_credential_reference_is_an_uppercase_name(self):
        self.assertEqual('[A-Z][A-Z0-9_]*', CREDENTIAL_NAME.pattern)
        self.assertIsNotNone(CREDENTIAL_NAME.fullmatch('PLATFORM_VAULT_KEYS_JSON'))
        for refused in ('path', 'lower', '9LEADING', '', 'WITH-DASH', 'with space'):
            with self.subTest(refused=refused):
                self.assertIsNone(CREDENTIAL_NAME.fullmatch(refused))
        self.assertEqual('fake-unit-test-only',
                         secret({'token_env': 'UNIT_TEST_KEY'}, 'token_env'))
        with self.assertRaises(RuntimeError):
            secret({'token_env': 'lowercase'}, 'token_env')

    def test_the_registry_names_which_refusal_it_is(self):
        """Two different causes, two different messages.

        Before the fix a duplicate name, an unknown risk level and both at once
        each produced the single string ``Invalid tool registration`` -- the very
        message ``test_registration_contract``'s docstring calls out as saying
        nothing about the real cause. The refusal is unchanged; the reason is now
        readable.
        """
        self.assertEqual({'read', 'write', 'destructive', 'physical'}, set(RISK_LEVELS))
        registry = Registry()
        registry.add(Tool('x.y', 'read', {}))
        with self.assertRaises(ValueError) as duplicate:
            registry.add(Tool('x.y', 'read', {}))
        with self.assertRaises(ValueError) as unknown_risk:
            registry.add(Tool('z.w', 'bogus', {}))
        self.assertIn('already registered', str(duplicate.exception))
        self.assertIn('risk', str(unknown_risk.exception))
        self.assertNotEqual(str(duplicate.exception), str(unknown_risk.exception))

    def test_the_schema_guard_refuses_instead_of_crashing(self):
        """A guard that raises something other than its contract is not a guard.

        Measured before the fix: a 1500-level schema raised ``RecursionError``,
        which is not the ``ValueError`` every caller catches, so ``mcp.call``
        could crash on an operator-declared schema rather than refuse it.
        """
        self.assertEqual(64, MAX_SCHEMA_DEPTH)
        schema, value = _nested(MAX_SCHEMA_DEPTH)
        validate_schema(value, schema)
        schema, value = _nested(MAX_SCHEMA_DEPTH + 1)
        with self.assertRaises(ValueError) as caught:
            validate_schema(value, schema)
        self.assertIn('nesting', str(caught.exception))
        schema, value = _nested(1500)
        with self.assertRaises(ValueError):
            validate_schema(value, schema)


if __name__=='__main__':unittest.main()
