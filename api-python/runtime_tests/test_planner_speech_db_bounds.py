"""Declared bounds of the planner, speech and database-read layer (§152).

The same question as §151, asked of the modules the inventory listed as having neither a
probe nor a phase: ``agent_planner``, ``speech``, ``postgres_connector`` and
``crm/crm_reconcile``. Each is a place where a number decided something -- how much
context a model may see, how much audio may be posted, how many rows may come back, how
many candidate records may be fetched before an exact-match rule runs -- and each number
was an inline literal.

Every assertion states the bound's own **number**, never the constant's name, so
widening a constant cannot move its test along with it. Where a bound cannot be reached
through the real surface, the test says so and pins the mechanism instead.

The measured spec is five files: ``test_agent_planner``, ``test_telephony``,
``test_foundation_v02``, ``test_postgres_contract`` and ``test_crm_reconcile``.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from platform_runtime.agent_planner import (MAX_CONTEXT_BYTES, MAX_MODEL_NAME_CHARS,
                                            MAX_OUTPUT_TOKENS, ResultPlanner)
from platform_runtime.crm.crm_reconcile import (CRMReconciler, EXACT_MATCHES_REQUIRED,
                                                MAX_CANDIDATE_MATCHES)
from platform_runtime.engine import Engine
from platform_runtime.postgres_connector import (CONNECT_TIMEOUT_SECONDS,
                                                 DEFAULT_LIMIT, DEFAULT_PORT,
                                                 LOCK_TIMEOUT_MS, MAX_CELL_BYTES,
                                                 MAX_COLUMNS, MAX_FILTER_CHARS,
                                                 MAX_HOST_CHARS, MAX_LIMIT,
                                                 MAX_PORT, MAX_RESULT_BYTES,
                                                 MAX_TABLES, MIN_PORT,
                                                 STATEMENT_TIMEOUT_MS, cell,
                                                 compile_read, read, validate_config)
from platform_runtime.speech import (AishaREST, MAX_AUDIO, MAX_AUDIO_PATH_CHARS,
                                     MAX_RESPONSE_BYTES, MAX_SPEED, MAX_TRANSCRIPT_CHARS,
                                     MAX_TTS_CHARS, MIN_SPEED, ORIGIN,
                                     TRANSPORT_TIMEOUT_SECONDS, SpeechError)
from platform_runtime.tools import build_registry


class DeclaredValuesTests(unittest.TestCase):
    def test_planner_declared_values_are_the_audited_ones(self):
        self.assertEqual(MAX_CONTEXT_BYTES, 64000)
        self.assertEqual(MAX_OUTPUT_TOKENS, 1600)
        self.assertEqual(MAX_MODEL_NAME_CHARS, 256)

    def test_speech_declared_values_are_the_audited_ones(self):
        self.assertEqual(MAX_AUDIO, 1000000)
        self.assertEqual(MAX_RESPONSE_BYTES, 1000000)
        self.assertEqual(TRANSPORT_TIMEOUT_SECONDS, 60)
        self.assertEqual(MAX_TTS_CHARS, 1000)
        self.assertEqual(MIN_SPEED, 0.5)
        self.assertEqual(MAX_SPEED, 2.0)
        self.assertEqual(MAX_AUDIO_PATH_CHARS, 1000)
        self.assertEqual(MAX_TRANSCRIPT_CHARS, 80000)

    def test_database_declared_values_are_the_audited_ones(self):
        self.assertEqual(MAX_HOST_CHARS, 253)
        self.assertEqual(MIN_PORT, 1)
        self.assertEqual(MAX_PORT, 65535)
        self.assertEqual(DEFAULT_PORT, 5432)
        self.assertEqual(MAX_TABLES, 100)
        self.assertEqual(MAX_COLUMNS, 40)
        self.assertEqual(MAX_FILTER_CHARS, 1000)
        self.assertEqual(DEFAULT_LIMIT, 50)
        self.assertEqual(MAX_LIMIT, 100)
        self.assertEqual(MAX_CELL_BYTES, 16000)
        self.assertEqual(MAX_RESULT_BYTES, 80000)
        self.assertEqual(CONNECT_TIMEOUT_SECONDS, 5)
        self.assertEqual(STATEMENT_TIMEOUT_MS, 2000)
        self.assertEqual(LOCK_TIMEOUT_MS, 1000)

    def test_reconcile_declared_values_are_the_audited_ones(self):
        self.assertEqual(MAX_CANDIDATE_MATCHES, 5)
        self.assertEqual(EXACT_MATCHES_REQUIRED, 1)


# ------------------------------------------------------------------- planner

def planner_engine(tmp):
    return Engine(Path(tmp) / 'planner.db', build_registry(), lambda tenant, agent: {
        'tools': ['reports.summary'], 'ladder': 'autonomous'})


class PlannerBoundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {'llm': {'model': 'unit-model', 'key_env': 'UNIT_MODEL_KEY',
                            'base_url': 'https://example.invalid/v1', 'agent_loop_enabled': True}}
        self.context = {'run_id': 'unit-run', 'agent': 'ops', 'input': 'x',
                        'remaining_steps': 1, 'remaining_calls': 1, 'observations': []}
        self.calls = []
        for target, value in (('platform_runtime.agent_planner.config', self.cfg),
                              ('platform_runtime.agent_planner.secret', 'unit-placeholder')):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def planner(self):
        def transport(url, body, headers):
            self.calls.append(body)
            return {'choices': [{'finish_reason': 'stop',
                                 'message': {'content': json.dumps({'action': 'final', 'answer': 'ok', 'evidence_ids': []})}}]}
        return ResultPlanner(planner_engine(self.tmp.name), transport)

    def test_model_name_ceiling_is_two_hundred_and_fifty_six(self):
        self.cfg['llm']['model'] = 'm' * MAX_MODEL_NAME_CHARS
        self.planner()('tenant', self.context)
        self.assertEqual(1, len(self.calls))
        self.cfg['llm']['model'] = 'm' * (MAX_MODEL_NAME_CHARS + 1)
        with self.assertRaises(RuntimeError):
            self.planner()('tenant', self.context)
        self.assertEqual(1, len(self.calls))          # refused before any provider call

    def test_output_token_ceiling_reaches_the_provider(self):
        self.planner()('tenant', self.context)
        self.assertEqual(1600, self.calls[0]['max_tokens'])

    def test_context_ceiling_is_sixty_four_kilobytes(self):
        # The serialized context is what is measured, so the input has to be large
        # enough that the encoded form crosses the bound.
        roomy = {**self.context, 'input': 'x' * (MAX_CONTEXT_BYTES - 2048)}
        self.planner()('tenant', roomy)
        self.assertEqual(1, len(self.calls))
        over = {**self.context, 'input': 'x' * (MAX_CONTEXT_BYTES * 2)}
        with self.assertRaises(ValueError):
            self.planner()('tenant', over)
        self.assertEqual(1, len(self.calls))


# -------------------------------------------------------------------- speech

class SpeechBoundTests(unittest.TestCase):
    def provider(self, body=None, status=201):
        seen = []

        def transport(url, data, headers):
            seen.append((url, data, headers))
            return status, (body if body is not None else {'audio_path': '/media/tts_audios/ok.wav'})
        return AishaREST('unit-key', transport), seen

    def test_tts_character_ceiling_is_one_thousand(self):
        provider, seen = self.provider()
        provider.synthesize('x' * MAX_TTS_CHARS)
        self.assertEqual(1, len(seen))
        with self.assertRaises(ValueError):
            provider.synthesize('x' * (MAX_TTS_CHARS + 1))
        self.assertEqual(1, len(seen))

    def test_speed_floor_and_ceiling(self):
        provider, _ = self.provider()
        for speed in (MIN_SPEED, MAX_SPEED):
            provider.synthesize('ok', speed=speed)
        for speed in (MIN_SPEED - 0.01, MAX_SPEED + 0.01, True):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                provider.synthesize('ok', speed=speed)

    def test_audio_ceiling_is_one_megabyte(self):
        provider, seen = self.provider(body={'transcript': 'salom'}, status=200)
        provider.transcribe(b'a' * MAX_AUDIO)
        self.assertEqual(1, len(seen))
        with self.assertRaises(ValueError):
            provider.transcribe(b'a' * (MAX_AUDIO + 1))
        with self.assertRaises(ValueError):
            provider.transcribe(b'')
        self.assertEqual(1, len(seen))

    def test_transcript_ceiling_is_eighty_thousand(self):
        provider, _ = self.provider(body={'transcript': 'x' * MAX_TRANSCRIPT_CHARS}, status=200)
        provider.transcribe(b'a')
        provider, _ = self.provider(body={'transcript': 'x' * (MAX_TRANSCRIPT_CHARS + 1)}, status=200)
        with self.assertRaises(SpeechError):
            provider.transcribe(b'a')

    def test_audio_path_ceiling_is_one_thousand(self):
        prefix, suffix = '/media/tts_audios/', '.wav'
        longest = prefix + 'a' * (MAX_AUDIO_PATH_CHARS - len(prefix) - len(suffix)) + suffix
        self.assertEqual(MAX_AUDIO_PATH_CHARS, len(longest))
        provider, _ = self.provider(body={'audio_path': longest})
        provider.synthesize('ok')
        provider, _ = self.provider(body={'audio_path': longest + 'a'})
        with self.assertRaises(SpeechError):
            provider.synthesize('ok')

    def test_response_ceiling_and_timeout_are_pinned_at_the_call(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'{"transcript":"x"}'
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        from platform_runtime.speech import http
        with patch('urllib.request.build_opener', return_value=opener):
            self.assertEqual((200, {'transcript': 'x'}), http(ORIGIN + '/x', b'data', {}))
        self.assertEqual(MAX_RESPONSE_BYTES + 1, response.read.call_args.args[0])
        self.assertEqual(TRANSPORT_TIMEOUT_SECONDS, opener.open.call_args.kwargs['timeout'])

    def test_oversized_response_is_refused(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'x' * (MAX_RESPONSE_BYTES + 1)
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        from platform_runtime.speech import http
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(SpeechError):
                http(ORIGIN + '/x', b'data', {})


# ---------------------------------------------------------------- postgres

def pg_config(**overrides):
    base = {'driver': 'postgres_readonly', 'host': 'db.example', 'allowed_hosts': ['db.example'],
            'port': DEFAULT_PORT, 'database': 'db', 'user': 'reader',
            'password_env': 'TEST_PG_PASSWORD', 'sslmode': 'verify-full', 'schema': 'sales',
            'isolation': 'tenant_column', 'tenant_column': 'tenant_id',
            'tables': {'contacts': ['id', 'tenant_id', 'name']}}
    return {**base, **overrides}


PG_REQUEST = {'connection': 'db', 'table': 'contacts', 'columns': ['id', 'name'], 'limit': 5}


class FakeCursor:
    def __init__(self, driver, named=False):
        self.d, self.named = driver, named

    def __enter__(self): return self
    def __exit__(self, *args): self.d.closed += 1
    def execute(self, sql, params=None): self.d.calls.append((sql, params))

    def fetchone(self):
        if not self.named: return (self.d.kind,)
        return self.d.records.pop(0) if self.d.records else None


class FakeDriver:
    def __init__(self, records=None):
        self.calls, self.closed = [], 0
        self.records = records if records is not None else [(1, 'Ali')]
        self.kind, self.kwargs = 'r', None

    def connect(self, **kwargs): self.kwargs = kwargs; return self
    def cursor(self, name=None): return FakeCursor(self, bool(name))
    def __enter__(self): return self
    def __exit__(self, *args): self.closed += 1


class DatabaseBoundTests(unittest.TestCase):
    def test_host_ceiling_is_two_hundred_and_fifty_three(self):
        longest = 'a' * (MAX_HOST_CHARS - 4) + '.com'
        self.assertEqual(MAX_HOST_CHARS, len(longest))
        validate_config(pg_config(host=longest, allowed_hosts=[longest]))
        too_long = 'a' * (MAX_HOST_CHARS - 3) + '.com'
        with self.assertRaises(ValueError):
            validate_config(pg_config(host=too_long, allowed_hosts=[too_long]))

    def test_port_range_and_default(self):
        self.assertEqual(5432, validate_config(pg_config())['port'])
        validate_config(pg_config(port=MIN_PORT))
        validate_config(pg_config(port=MAX_PORT))
        for port in (0, MAX_PORT + 1, True, '5432'):
            with self.subTest(port=port), self.assertRaises(ValueError):
                validate_config(pg_config(port=port))

    def test_table_ceiling_is_one_hundred(self):
        columns = ['id', 'tenant_id']
        allowed = {'t%d' % n: columns for n in range(MAX_TABLES)}
        validate_config(pg_config(tables=allowed))
        over = {**allowed, 't%d' % MAX_TABLES: columns}
        with self.assertRaises(ValueError):
            validate_config(pg_config(tables=over))
        with self.assertRaises(ValueError):
            validate_config(pg_config(tables={}))

    def test_column_ceiling_is_forty(self):
        cols = ['c%d' % n for n in range(MAX_COLUMNS - 1)] + ['tenant_id']
        validate_config(pg_config(tables={'contacts': cols}))
        over = cols + ['one_more']
        with self.assertRaises(ValueError):
            validate_config(pg_config(tables={'contacts': over}))

    def test_query_limit_range_and_default(self):
        self.assertIn(DEFAULT_LIMIT, compile_read('a', pg_config(), {'connection': 'db', 'table': 'contacts', 'columns': ['id']})[2])
        for limit in (1, MAX_LIMIT):
            compile_read('a', pg_config(), {**PG_REQUEST, 'limit': limit})
        for limit in (0, MAX_LIMIT + 1, True, '5'):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                compile_read('a', pg_config(), {**PG_REQUEST, 'limit': limit})

    def test_filter_ceiling_is_one_thousand(self):
        compile_read('a', pg_config(), {**PG_REQUEST, 'where': {'column': 'name', 'equals': 'x' * MAX_FILTER_CHARS}})
        with self.assertRaises(Exception):
            compile_read('a', pg_config(), {**PG_REQUEST, 'where': {'column': 'name', 'equals': 'x' * (MAX_FILTER_CHARS + 1)}})

    def test_cell_ceiling_is_sixteen_thousand_bytes(self):
        def size_of(n): return len(json.dumps({'id': 1, 'name': 'x' * n}, ensure_ascii=False).encode())
        n = MAX_CELL_BYTES - size_of(0)
        self.assertEqual(MAX_CELL_BYTES, size_of(n))
        driver = FakeDriver(records=[(1, 'x' * n)])
        with patch.dict(os.environ, {'TEST_PG_PASSWORD': 'unit-only'}), patch.dict('sys.modules', {'psycopg': driver}):
            self.assertEqual(1, read('a', pg_config(), PG_REQUEST)['returned'])
        driver = FakeDriver(records=[(1, 'x' * (n + 1))])
        with patch.dict(os.environ, {'TEST_PG_PASSWORD': 'unit-only'}), patch.dict('sys.modules', {'psycopg': driver}):
            with self.assertRaises(ValueError):
                read('a', pg_config(), PG_REQUEST)

    def test_result_ceiling_is_eighty_thousand_bytes(self):
        """Five rows fit, six do not -- so the ceiling is on the batch, not the cell."""
        row = 'x' * 15000
        five = FakeDriver(records=[(n, row) for n in range(5)])
        with patch.dict(os.environ, {'TEST_PG_PASSWORD': 'unit-only'}), patch.dict('sys.modules', {'psycopg': five}):
            self.assertEqual(5, read('a', pg_config(), {**PG_REQUEST, 'limit': 10})['returned'])
        six = FakeDriver(records=[(n, row) for n in range(6)])
        with patch.dict(os.environ, {'TEST_PG_PASSWORD': 'unit-only'}), patch.dict('sys.modules', {'psycopg': six}):
            with self.assertRaises(ValueError):
                read('a', pg_config(), {**PG_REQUEST, 'limit': 10})

    def test_timeouts_are_pinned_on_the_connection(self):
        driver = FakeDriver()
        with patch.dict(os.environ, {'TEST_PG_PASSWORD': 'unit-only'}), patch.dict('sys.modules', {'psycopg': driver}):
            read('a', pg_config(), PG_REQUEST)
        self.assertEqual(5, driver.kwargs['connect_timeout'])
        self.assertIn('statement_timeout=2000', driver.kwargs['options'])
        self.assertIn('lock_timeout=1000', driver.kwargs['options'])
        self.assertIn('default_transaction_read_only=on', driver.kwargs['options'])

    def test_identifier_ceiling_is_sixty_three_bytes(self):
        """PostgreSQL truncates a longer identifier rather than refusing it, so a name
        that is too long would silently address a different table."""
        validate_config(pg_config(tables={'t' * 63: ['id', 'tenant_id']}))
        with self.assertRaises(ValueError):
            validate_config(pg_config(tables={'t' * 64: ['id', 'tenant_id']}))


# ---------------------------------------------------------------- reconcile

class RecordingAdapter:
    def __init__(self, leads):
        self.leads, self.limits = leads, []

    def find_leads(self, phone=None, email=None, query=None, limit=20):
        self.limits.append(limit)
        return self.leads


class ReconcileBoundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tenant, self.actor = 't_reconcile', 'admin_user'
        self.engine = Engine(Path(self.tmp.name) / 'reconcile.db', build_registry(),
                             lambda t, a: {'tools': ['crm.lead.create'], 'ladder': 'autonomous'})
        with self.engine.tx() as db:
            db.execute('INSERT INTO p_tasks(id,tenant,channel,event_key,fingerprint,agent,actor,status,created,updated)'
                       ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                       ('task_1', self.tenant, 'web', 'ev_1', 'fp_1', 'retail_agent', self.actor, 'running', 1000.0, 1000.0))
        self.config = {'connections': {'crm_b24': {
            'driver': 'bitrix24', 'host': 'mycrm.bitrix24.com', 'allowed_hosts': ['mycrm.bitrix24.com'],
            'token_env': 'CRM_TOKEN',
            'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write', 'reconcile']}}}
        os.environ['CRM_TOKEN'] = 'unit-only'

    def step(self, step_id, position=0):
        from platform_runtime.engine import encode
        args = {'connection': 'crm_b24', 'request': {'title': 'Buyurtma #55', 'phone': '+998901234567'}}
        with self.engine.tx() as db:
            db.execute('INSERT INTO p_steps(id,task,tenant,position,tool,args,risk,approval_needed,fingerprint,status)'
                       ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (step_id, 'task_1', self.tenant, position, 'crm.lead.create', encode(args), 'write', 1, 'fp_step', 'uncertain'))

    def reconciler(self, adapter):
        return CRMReconciler(self.engine, config_resolver=lambda t: self.config,
                             adapter_factory=lambda driver, raw: adapter)

    def test_candidate_limit_is_five(self):
        self.step('step_limit')
        adapter = RecordingAdapter([])
        self.reconciler(adapter).reconcile_step(self.tenant, 'step_limit', self.actor)
        self.assertEqual([MAX_CANDIDATE_MATCHES], adapter.limits)

    def test_exactly_one_exact_match_settles_and_two_do_not(self):
        lead = {'id': 'b24_1', 'title': 'Buyurtma #55', 'phone': '+998901234567'}
        self.step('step_one')
        settled = self.reconciler(RecordingAdapter([lead])).reconcile_step(self.tenant, 'step_one', self.actor)
        self.assertTrue(settled['settled'])
        self.step('step_two', position=1)
        ambiguous = self.reconciler(RecordingAdapter([lead, {**lead, 'id': 'b24_2'}])).reconcile_step(
            self.tenant, 'step_two', self.actor)
        self.assertFalse(ambiguous['settled'])
        self.assertEqual('uncertain', ambiguous['status'])

    def test_a_partial_match_is_not_a_match(self):
        """The rule is exact equality on phone or title. A record that merely resembles
        the request must not settle the step -- that is how a duplicate is created."""
        self.step('step_partial', position=2)
        near = {'id': 'b24_3', 'title': 'Buyurtma #550', 'phone': '+9989012345678'}
        result = self.reconciler(RecordingAdapter([near])).reconcile_step(self.tenant, 'step_partial', self.actor)
        self.assertFalse(result['settled'])
        self.assertEqual('uncertain', result['status'])


if __name__ == '__main__':
    unittest.main()
