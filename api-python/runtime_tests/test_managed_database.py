"""Real local SQLite acceptance plus portable-contract and authority regressions.

No customer data, network, installed database server or live credentials required.
"""
from contextlib import closing
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from platform_runtime.database.contract import (BACKENDS, MAX_INTEGER, catalog,
                                              normalize, parse_request,
                                              record_key, scalar, text,
                                              validate_config)
from platform_runtime.database.sql import compile_sql
from platform_runtime.database.gateway import tool_plan, tool_write, prepare
from platform_runtime.database.transports import sqlite_execute, validate_endpoint
from platform_runtime.engine import Engine, Forbidden, Conflict, encode
from platform_runtime.tools import build_registry
from platform_runtime.connectors import describe


def connection(path='/tmp/customer.sqlite'):
    return {'driver': 'sqlite_managed', 'contract_version': '1.1', 'enabled': True,
            'lifecycle': 'configured', 'generation': 1, 'agent_ids': ['ops'],
            'capabilities': ['read', 'plan_write', 'execute_write'],
            'isolation': 'tenant_column', 'tenant_column': 'tenant_id', 'path': str(path),
            'resources': {'contacts': {'key_field': 'id', 'version_field': 'version',
                'read_fields': ['id', 'version', 'name', 'status'],
                'insert_fields': ['name', 'status'], 'update_fields': ['name', 'status']}}}


def request(operation='update', **extra):
    out = {'operation': operation, 'resource': 'contacts'}
    if operation == 'read':
        out.update(fields=['id', 'name', 'version'])
    else:
        out.update(key='one', values={'name': 'Vali'})
        if operation == 'update':
            out['expected_version'] = 1
    out.update(extra)
    return out


class ContractTests(unittest.TestCase):
    def test_backend_matrix_never_claims_universal_live_support(self):
        self.assertGreaterEqual(len(BACKENDS), 12)
        self.assertTrue(all(not row['live_verified'] for row in catalog()))
        self.assertEqual(['read','insert','update'], next(r for r in catalog() if r['driver'] == 'neo4j_managed')['operations'])

    def test_duplicate_json_keys_denied(self):
        with self.assertRaises(ValueError):
            parse_request('{"operation":"read","operation":"update"}')

    def test_nonfinite_and_deep_json_denied(self):
        for raw in ['{"key":NaN}', '[' * 1500 + '0' + ']' * 1500]:
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                parse_request(raw)

    def test_unknown_operation_fields_denied(self):
        for extra in [{'sql': 'DELETE FROM contacts'}, {'tenant': 'other'}, {'query': {'$where': 'x'}},
                      {'operation': 'delete'}, {'operation': 'upsert'}, {'operation': []}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                normalize(connection(), 'a', request(**extra))

    def test_protected_fields_cannot_be_written(self):
        for field in ['id', 'version', 'tenant_id', 'secret', '$set', 'nested.field']:
            with self.subTest(field=field), self.assertRaises((ValueError, Forbidden)):
                normalize(connection(), 'a', request(values={field: 'evil'}))

    def test_nested_values_floats_binary_and_large_ints_denied(self):
        for value in [{'$gt': 1}, ['x'], 1.25, float('nan'), b'x', 2**60]:
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError):
                normalize(connection(), 'a', request(values={'name': value}))

    def test_money_string_keeps_precision(self):
        result = normalize(connection(), 'a', request(values={'name': '1234567890.123400'}))
        self.assertEqual('1234567890.123400', result['values']['name'])

    def test_expected_version_strict(self):
        for version in [None, True, 0, -1, '1', 2**60]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                normalize(connection(), 'a', request(expected_version=version))

    def test_read_bounds_and_projection(self):
        for extra in [{'limit': 0}, {'limit': 101}, {'limit': True}, {'fields': ['secret']},
                      {'fields': ['id', 'id']}, {'fields': []}]:
            with self.subTest(extra=extra), self.assertRaises((ValueError, Forbidden)):
                normalize(connection(), 'a', request('read', **extra))

    def test_missing_agent_generation_and_capabilities_fail_closed(self):
        for extra in [{'agent_ids': []}, {'agent_ids': ['other']}, {'enabled': 'true'},
                      {'generation': True}, {'generation': 0}, {'capabilities': ['reconcile']},
                      {'contract_version': '1.0'}, {'lifecycle': 'revoked'}]:
            with self.subTest(extra=extra), self.assertRaises((ValueError, Forbidden)):
                validate_config({**connection(), **extra}, tenant='a', agent='ops', capability='read')

    def test_dedicated_database_requires_exact_tenant_binding(self):
        raw = connection(); raw.pop('tenant_column'); raw.update(isolation='dedicated_database', bound_tenant='a')
        validate_config(raw, tenant='a', agent='ops', capability='read')
        with self.assertRaises(Forbidden):
            validate_config(raw, tenant='b', agent='ops', capability='read')

    def test_inline_credentials_denied(self):
        for field in ['password', 'dsn', 'url', 'token']:
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_config({**connection(), field: 'not-a-secret'}, tenant='a', agent='ops', capability='read')

    def test_six_sql_dialects_parameterize_values_and_enforce_scope(self):
        attack = "O'zbek'; DROP TABLE contacts; --"
        for driver in ['sqlite', 'postgres', 'mysql', 'mariadb', 'sqlserver', 'oracle']:
            raw = {**connection(), 'driver': driver + '_managed', 'schema': 'sales'}
            req = normalize(raw, 'a', request(values={'name': attack}))
            sql, params = compile_sql(raw, 'a', req)
            with self.subTest(driver=driver):
                self.assertNotIn(attack, sql)
                self.assertIn(attack, params)
                self.assertEqual([attack, 'a', 'one', 1], params)
                self.assertIn('tenant_id', sql)
                self.assertIn('version', sql)
                self.assertIn('WHERE', sql)

    def test_sql_read_limit_dialects(self):
        for driver, marker in [('sqlite', 'LIMIT ?'), ('postgres', 'LIMIT %s'), ('mysql', 'LIMIT %s'),
                               ('mariadb', 'LIMIT %s'), ('sqlserver', 'TOP (?)'), ('oracle', 'FETCH FIRST :2 ROWS ONLY')]:
            raw = {**connection(), 'driver': driver + '_managed', 'schema': 'sales'}
            sql, params = compile_sql(raw, 'a', normalize(raw, 'a', request('read', limit=3)))
            with self.subTest(driver=driver):
                self.assertIn(marker, sql)
                self.assertIn(3, params)
                self.assertIn('a', params)

    def test_insert_injects_tenant_key_and_initial_version(self):
        raw = connection()
        sql, values = compile_sql(raw, 'a', normalize(raw, 'a', request('insert')))
        self.assertTrue(sql.startswith('INSERT INTO'))
        self.assertEqual(['Vali', 'one', 1, 'a'], values)

    def test_network_endpoints_require_tls_allowlist_and_plain_database(self):
        base = {**connection(), 'driver': 'postgres_managed', 'host': 'db.example',
                'allowed_hosts': ['db.example'], 'port': 5432, 'database': 'customer',
                'user': 'writer', 'password_env': 'TEST_DB_PASSWORD', 'sslmode': 'verify-full', 'schema': 'sales'}
        validate_endpoint(base)
        for extra in [{'allowed_hosts': []}, {'host': 'evil.example'}, {'sslmode': 'require'},
                      {'database': 'host=evil.example'}, {'port': True}, {'schema': 'pg_catalog'},
                      {'password_env': 'DSEC_DB_PASSWORD'}]:
            with self.subTest(extra=extra), self.assertRaises((ValueError, Forbidden)):
                validate_endpoint({**base, **extra})


class SQLiteManagedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.customer = self.root / 'customer.sqlite'
        with closing(sqlite3.connect(self.customer)) as db, db:
            db.executescript("CREATE TABLE contacts(tenant_id TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,name TEXT,status TEXT,PRIMARY KEY(tenant_id,id));"
                             "INSERT INTO contacts VALUES('a','one',1,'Ali','new');"
                             "INSERT INTO contacts VALUES('b','one',1,'Other tenant','new');")
        self.raw = connection(self.customer)
        self.config_patch = patch('platform_runtime.database.gateway.config', side_effect=lambda t: {'connections': {'customer': self.raw}})
        self.config_patch.start()
        self.env = patch.dict(os.environ, {'PLATFORM_DB_ROOTS': json.dumps([str(self.root)])})
        self.env.start()
        self.policy = {'tools': ['database.catalog', 'database.read', 'database.plan_write', 'database.write'],
                       'allowed_connections': ['customer'], 'ladder': 'autonomous',
                       'approver_role': 'owner', 'independent_approval': True}
        self.e = Engine(self.root / 'platform.sqlite', build_registry(), lambda t, a: self.policy)
        self.count = 0

    def tearDown(self):
        self.config_patch.stop(); self.env.stop(); self.temp.cleanup()

    def args(self, req=None):
        return {'connection': 'customer', 'request_json': encode(req or request())}

    def planned(self, req=None):
        args = self.args(req)
        plan = tool_plan(self.e, 'a', 'ops', args, 'preview')
        return {**args, 'plan_fingerprint': plan['plan_fingerprint']}

    def submit(self, args=None, tool='database.write'):
        self.count += 1
        tid = self.e.submit('a', 'web', str(self.count), 'ops',
                            [{'tool': tool, 'args': args or self.planned()}], 'creator')
        return tid, self.e.get('a', tid)['steps'][0]['id']

    def approve(self, sid):
        self.e.approve('a', sid, 'reviewer', 'approved', 'owner')

    def rows(self):
        with closing(sqlite3.connect(self.customer)) as db, db:
            return db.execute('SELECT tenant_id,id,version,name FROM contacts ORDER BY tenant_id,id').fetchall()

    def test_real_read_excludes_other_tenant(self):
        tid, _ = self.submit(self.args(request('read')), 'database.read')
        self.assertTrue(self.e.tick('a'))
        step = self.e.get('a', tid)['steps'][0]
        result = step['result']
        self.assertEqual([{'id': 'one', 'name': 'Ali', 'version': 1}], result['rows'])

    def test_autonomous_policy_cannot_skip_write_approval(self):
        tid, sid = self.submit()
        self.assertFalse(self.e.tick('a'))
        self.assertEqual('waiting_approval', self.e.get('a', tid)['status'])
        self.assertEqual('Ali', self.rows()[0][3])

    def test_real_approved_update_is_atomic_scoped_and_audited(self):
        tid, sid = self.submit(); self.approve(sid)
        self.assertTrue(self.e.tick('a'))
        self.assertEqual('succeeded', self.e.get('a', tid)['status'])
        self.assertEqual([('a', 'one', 2, 'Vali'), ('b', 'one', 1, 'Other tenant')], self.rows())
        with self.e.read() as db:
            receipt = db.execute('SELECT * FROM p_database_dispatch').fetchone()
            actions = [r['action'] for r in db.execute('SELECT action FROM p_audit')]
        self.assertEqual('committed', receipt['status'])
        self.assertIn('database.write.dispatching', actions)
        self.assertIn('database.write.committed', actions)

    def test_real_approved_insert(self):
        tid, sid = self.submit(self.planned(request('insert', key='two'))); self.approve(sid)
        self.e.tick('a')
        self.assertEqual('succeeded', self.e.get('a', tid)['status'])
        self.assertIn(('a', 'two', 1, 'Vali'), self.rows())

    def test_same_step_cannot_dispatch_twice(self):
        args = self.planned(); tid, sid = self.submit(args); self.approve(sid)
        step = self.e.claim('a', 'unit-test')
        tool_write(self.e, 'a', 'ops', args, sid)
        with self.assertRaises(Conflict):
            tool_write(self.e, 'a', 'ops', args, sid)
        self.assertEqual(2, self.rows()[0][2])

    def test_stale_version_does_not_overwrite(self):
        tid, sid = self.submit(self.planned(request(expected_version=2))); self.approve(sid)
        self.e.tick('a')
        self.assertEqual('uncertain', self.e.get('a', tid)['status'])
        self.assertEqual('Ali', self.rows()[0][3])

    def test_duplicate_insert_is_not_retried(self):
        tid, sid = self.submit(self.planned(request('insert'))); self.approve(sid)
        self.e.tick('a')
        self.assertEqual('uncertain', self.e.get('a', tid)['status'])
        self.assertFalse(self.e.tick('a'))
        self.assertEqual(2, len(self.rows()))

    def test_direct_handler_without_step_denied(self):
        with self.assertRaises(Forbidden):
            tool_write(self.e, 'a', 'ops', self.planned(), 'invented-step')
        self.assertEqual('Ali', self.rows()[0][3])

    def test_direct_handler_before_approval_denied(self):
        args = self.planned(); _, sid = self.submit(args)
        with self.assertRaises(Forbidden):
            tool_write(self.e, 'a', 'ops', args, sid)

    def test_plan_tampering_before_submission_denied(self):
        args = self.planned(); args['request_json'] = encode(request(values={'name': 'Evil'}))
        with self.assertRaises(Conflict):
            self.submit(args)

    def test_generation_change_invalidates_plan(self):
        args = self.planned(); self.raw['generation'] += 1
        with self.assertRaises(Conflict):
            self.submit(args)

    def test_config_change_after_approval_prevents_claim(self):
        tid, sid = self.submit(); self.approve(sid); self.raw['generation'] += 1
        self.assertFalse(self.e.tick('a'))
        self.assertEqual('failed', self.e.get('a', tid)['status'])
        self.assertEqual('Ali', self.rows()[0][3])

    def test_revoke_after_claim_prevents_dispatch(self):
        args = self.planned(); _, sid = self.submit(args); self.approve(sid)
        self.e.claim('a', 'unit-test'); self.raw['lifecycle'] = 'revoked'
        with self.assertRaises(Forbidden):
            tool_write(self.e, 'a', 'ops', args, sid)

    def test_pack_connection_removed_after_claim_prevents_dispatch(self):
        args = self.planned(); _, sid = self.submit(args); self.approve(sid)
        self.e.claim('a', 'unit-test'); self.policy['allowed_connections'] = []
        with self.assertRaises(Forbidden):
            tool_write(self.e, 'a', 'ops', args, sid)

    def test_actor_cannot_self_approve(self):
        _, sid = self.submit()
        with self.assertRaises(Forbidden):
            self.e.approve('a', sid, 'creator', 'approved', 'owner')

    def test_other_tenant_cannot_reuse_step(self):
        args = self.planned(); _, sid = self.submit(args); self.approve(sid); self.e.claim('a', 'unit-test')
        with self.assertRaises((Forbidden, Conflict)):
            tool_write(self.e, 'b', 'ops', args, sid)

    def test_expired_approval_denied_at_handler(self):
        args = self.planned(); _, sid = self.submit(args); self.approve(sid); self.e.claim('a', 'unit-test')
        with self.e.tx() as db:
            db.execute('UPDATE p_approvals SET expires=0 WHERE step=?', (sid,))
        with self.assertRaises(Forbidden):
            tool_write(self.e, 'a', 'ops', args, sid)

    def test_table_trigger_denied_and_no_side_effects(self):
        with closing(sqlite3.connect(self.customer)) as db, db:
            db.executescript("CREATE TABLE hidden(value TEXT); CREATE TRIGGER surprise AFTER UPDATE ON contacts BEGIN INSERT INTO hidden VALUES('changed'); END;")
        tid, sid = self.submit(); self.approve(sid); self.e.tick('a')
        self.assertEqual('uncertain', self.e.get('a', tid)['status'])
        with closing(sqlite3.connect(self.customer)) as db, db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM hidden').fetchone()[0])
        self.assertEqual('Ali', self.rows()[0][3])

    def test_platform_database_and_hardlink_denied(self):
        for path in [self.e.path, str(self.root / 'alias.sqlite')]:
            if path != self.e.path:
                os.link(self.e.path, path)
            raw = {**self.raw, 'path': path}
            with self.subTest(path=path), self.assertRaises(Forbidden):
                sqlite_execute(raw, 'a', normalize(raw, 'a', request()), self.e.path)

    def test_nonunique_table_fails_closed(self):
        with closing(sqlite3.connect(self.customer)) as db, db:
            db.executescript('ALTER TABLE contacts RENAME TO old_contacts; CREATE TABLE contacts AS SELECT * FROM old_contacts;')
        with self.assertRaises(Forbidden):
            sqlite_execute(self.raw, 'a', normalize(self.raw, 'a', request()), self.e.path)

    def test_view_denied(self):
        with closing(sqlite3.connect(self.customer)) as db, db:
            db.executescript('ALTER TABLE contacts RENAME TO old_contacts; CREATE VIEW contacts AS SELECT * FROM old_contacts;')
        with self.assertRaises(Forbidden):
            sqlite_execute(self.raw, 'a', normalize(self.raw, 'a', request('read')), self.e.path)

    def test_unknown_backend_cannot_plan_or_execute(self):
        self.raw['driver'] = 'unknown_managed'
        with self.assertRaises(Forbidden):
            self.planned()

    def test_no_sql_read_through_write_risk_mismatch(self):
        with self.assertRaises(ValueError):
            prepare('a', 'ops', 'database.read', self.args(request()))

    def test_provider_failure_recorded_uncertain_without_retry(self):
        tid, sid = self.submit(); self.approve(sid)
        with patch.dict('platform_runtime.database.gateway.TRANSPORTS',
                        {'sqlite_managed': lambda *a: (_ for _ in ()).throw(RuntimeError('sensitive-provider-message'))}):
            self.e.tick('a')
        task = self.e.get('a', tid)
        self.assertEqual('uncertain', task['status'])
        self.assertNotIn('sensitive-provider-message', encode(task))
        self.assertFalse(self.e.tick('a'))

    def test_sql_injection_text_is_only_data(self):
        attack = "O'zbek'; DROP TABLE contacts; --"
        tid, sid = self.submit(self.planned(request(values={'name': attack}))); self.approve(sid); self.e.tick('a')
        self.assertEqual('succeeded', self.e.get('a', tid)['status'])
        self.assertEqual(attack, self.rows()[0][3])

    def test_existing_catalog_accepts_mixed_managed_connection(self):
        with patch('platform_runtime.connectors.config', return_value={'connections': {'customer': self.raw}}):
            out = describe('a')
        self.assertEqual('managed_approved_operations', out[0]['mode'])
        self.assertFalse(out[0]['live_verified'])


    def test_two_workers_same_version_only_one_update_commits(self):
        from concurrent.futures import ThreadPoolExecutor
        first, sid1 = self.submit(); second, sid2 = self.submit()
        self.approve(sid1); self.approve(sid2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda worker: self.e.tick('a', worker), ['worker1', 'worker2']))
        states = sorted([self.e.get('a', first)['status'], self.e.get('a', second)['status']])
        self.assertEqual(['succeeded', 'uncertain'], states)
        self.assertEqual(2, self.rows()[0][2])
        self.assertEqual('Other tenant', self.rows()[1][3])

    def test_freeze_after_claim_denies_handler(self):
        args = self.planned(); _, sid = self.submit(args); self.approve(sid); self.e.claim('a', 'worker')
        with self.e.tx() as db:
            db.execute('INSERT INTO p_freeze VALUES(?,?)', ('a', 1))
        with self.assertRaises(Forbidden):
            tool_write(self.e, 'a', 'ops', args, sid)
        self.assertEqual('Ali', self.rows()[0][3])

    def test_expired_lease_denies_handler(self):
        args = self.planned(); _, sid = self.submit(args); self.approve(sid); self.e.claim('a', 'worker')
        with self.e.tx() as db:
            db.execute('UPDATE p_steps SET lease=0 WHERE id=?', (sid,))
        with self.assertRaises(Forbidden):
            tool_write(self.e, 'a', 'ops', args, sid)

    def test_revoked_approver_denies_handler(self):
        args = self.planned(); _, sid = self.submit(args); self.approve(sid); self.e.claim('a', 'worker')
        def authority(db, tenant, channel, actor, roles):
            if channel == 'approval':
                raise Forbidden('Approver revoked')
        self.e.authority = authority
        with self.assertRaises(Forbidden):
            tool_write(self.e, 'a', 'ops', args, sid)
        self.assertEqual('Ali', self.rows()[0][3])

    def test_audit_does_not_copy_database_field_values(self):
        tid, sid = self.submit(self.planned(request(values={'name': 'private-person-value'})))
        self.approve(sid); self.e.tick('a')
        with self.e.read() as db:
            audit = [dict(row) for row in db.execute('SELECT * FROM p_audit')]
        self.assertNotIn('private-person-value', encode(audit))

    def test_config_endpoint_swap_after_claim_invalidates_approval(self):
        args = self.planned(); _, sid = self.submit(args); self.approve(sid); self.e.claim('a', 'worker')
        self.raw['path'] = str(self.root / 'different.sqlite')
        with self.assertRaises(Conflict):
            tool_write(self.e, 'a', 'ops', args, sid)


    # ------------------------------------------------ fazza 27: measured bounds

    def test_the_version_ceiling_itself_is_accepted_and_one_past_it_is_not(self):
        """``MAX_INTEGER`` is the largest integer this contract calls portable.

        ``test_expected_version_strict`` walks ``2**60``, which is out of range
        under ANY ceiling -- so it proves a bound exists and never that the bound
        is ``MAX_INTEGER``. The ceiling itself was refused, while ``scalar`` in the
        same file and all four backend guards accept it.
        """
        for version in (1, MAX_INTEGER - 1, MAX_INTEGER):
            with self.subTest(version=version):
                out = normalize(connection(), 'a', request(expected_version=version))
                self.assertEqual(version, out['expected_version'])
        for version in (MAX_INTEGER + 1, 0, -1, True, None, '1'):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    normalize(connection(), 'a', request(expected_version=version))

    def test_every_version_guard_uses_the_same_ceiling(self):
        """One fact, four readers -- read from the source, not assumed.

        ``contract.normalize`` used a strict ``<`` while ``cassandra``,
        ``dynamodb``, ``elasticsearch`` and ``neo4j`` all used ``<=``. At exactly
        ``MAX_INTEGER`` the contract refused and the backends accepted, and the
        contract refused it with "Update requires positive expected_version" --
        false, because the version IS positive, it is simply the ceiling.
        """
        database = Path(__file__).resolve().parents[1] / 'platform_runtime' / 'database'
        contract = (database / 'contract.py').read_text(encoding='utf-8')
        self.assertIn('not 1 <= version <= MAX_INTEGER', contract)
        self.assertNotIn('not 1 <= version < MAX_INTEGER', contract)
        for name in ('cassandra_backend.py', 'dynamodb_backend.py',
                     'elasticsearch_backend.py', 'neo4j_backend.py'):
            with self.subTest(backend=name):
                backend = (database / name).read_text(encoding='utf-8')
                self.assertIn('<=MAX_INTEGER', backend.replace(' ', ''))

    def test_a_lone_surrogate_is_refused_by_name_not_by_crash(self):
        """``json.loads`` produces a lone surrogate from a JSON ``udXXX`` escape sequence.

        ``scalar`` measured the value's UTF-8 size, so it raised
        ``UnicodeEncodeError`` out of a validation path whose contract is
        ``ValueError`` -- while ``text``, in the same file, ACCEPTED the same
        value. Same value, two answers, and one of them was a crash.
        """
        surrogate = json.loads('"\ud800"')
        self.assertEqual('\ud800', surrogate)
        for label, call in (('scalar', lambda: scalar(surrogate)),
                            ('record_key', lambda: record_key(surrogate)),
                            ('text', lambda: text(surrogate))):
            with self.subTest(gate=label):
                with self.assertRaises(ValueError) as caught:
                    call()
                # `UnicodeEncodeError` IS a `ValueError` -- `UnicodeError` derives
                # from it -- so `assertRaises(ValueError)` alone is satisfied by the
                # very crash this test exists to rule out. Measured: the first
                # version of this test left the crash-green revert GREEN.
                self.assertNotIsInstance(caught.exception, UnicodeError)
                self.assertIn('not encodable as UTF-8', str(caught.exception))
        # The value really does reach a request body through the parser, so the
        # refusal has to happen in the contract rather than at the transport.
        self.assertEqual({'x': '\ud800'}, parse_request('{"x": "\\ud800"}'))
        # The parser measures the RAW text before it parses, and that measurement
        # is the other place a surrogate can arrive: a body decoded with
        # `surrogateescape` carries one before any JSON escape is involved.
        with self.assertRaises(ValueError) as caught:
            parse_request('{"x": "' + surrogate + '"}')
        self.assertNotIsInstance(caught.exception, UnicodeError)
        self.assertIn('not encodable as UTF-8', str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            normalize(connection(), 'a', request(values={'name': surrogate}))
        self.assertNotIsInstance(caught.exception, UnicodeError)
        self.assertIn('not encodable as UTF-8', str(caught.exception))

    def test_ordinary_text_is_still_carried(self):
        """The surrogate refusal must not narrow what the contract accepts.

        Uzbek text carries ``o'``, ``g'`` and the em dash, all of which encode
        cleanly; a gate that refused them would be worse than the bug it fixed.
        """
        for value in ("o'zbek — tasdiq", '450000', '2026-09-20'):
            with self.subTest(value=value):
                self.assertEqual(value, text(value))
                self.assertEqual(value, scalar(value))
