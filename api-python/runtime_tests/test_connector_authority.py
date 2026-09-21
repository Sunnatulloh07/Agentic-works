"""Offline regressions: revocation, agent binding, capability and strict metadata."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime.agent_loop import AgentLoop
from platform_runtime.connector_contract import descriptor
from platform_runtime.connectors import describe, read_rows, tool_read, probe_read_connection
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.postgres_connector import compile_read
from platform_runtime.tools import build_registry


class ConnectorAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.customer = self.root / 'customer.db'
        db = sqlite3.connect(self.customer)
        try:
            db.executescript("CREATE TABLE contacts(id INTEGER, name TEXT); INSERT INTO contacts VALUES(1,'Ali');")
            db.commit()
        finally:
            db.close()
        self.raw = {'driver': 'sqlite_readonly', 'path': str(self.customer),
                    'tables': {'contacts': ['id', 'name']}}
        self.cfg = {'connections': {'crm': self.raw}}
        self.req = {'connection': 'crm', 'table': 'contacts', 'columns': ['id', 'name']}
        self.env = patch.dict(os.environ, {'PLATFORM_DB_ROOTS': json.dumps([str(self.root)])})
        self.env.start()
        self.config_patch = patch('platform_runtime.connectors.config',
                                  side_effect=lambda tenant: self.cfg if tenant == 'a' else {})
        self.config_patch.start()
        self.e = Engine(self.root / 'platform.db', build_registry(), self.policy)

    def tearDown(self):
        self.config_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def policy(self, tenant, agent):
        return {'tools': ['connectors.read'], 'allowed_connections': ['crm'], 'ladder': 'autonomous'}

    def submit(self, key='1', agent='ops'):
        return self.e.submit('a', 'web', key, agent,
                             [{'tool': 'connectors.read', 'args': self.req}], 'owner')

    def test_revoked_sqlite_denied_before_database_path(self):
        self.raw['lifecycle'] = 'revoked'
        with patch('platform_runtime.connectors.database_path') as path, self.assertRaises(Forbidden):
            read_rows('a', self.req)
        path.assert_not_called()

    def test_non_executable_lifecycles_denied(self):
        for state in ['draft', 'authorizing', 'verifying', 'revoked']:
            self.raw['lifecycle'] = state
            with self.subTest(state=state), self.assertRaises(Forbidden):
                read_rows('a', self.req)

    def test_executable_lifecycles_read_without_claiming_health(self):
        for state in ['configured', 'healthy', 'degraded']:
            self.raw['lifecycle'] = state
            with self.subTest(state=state):
                self.assertEqual('Ali', read_rows('a', self.req)['rows'][0]['name'])
                self.assertEqual('configured_not_live_verified', describe('a')[0]['status'])

    def test_revoked_status_is_visible_and_not_configured(self):
        self.raw['lifecycle'] = 'revoked'
        self.assertEqual('revoked', describe('a')[0]['status'])

    def test_revoked_connection_rejected_at_submit(self):
        self.raw['lifecycle'] = 'revoked'
        with self.assertRaises(Forbidden):
            self.submit()
        self.assertEqual([], self.e.list_tasks('a'))

    def test_revocation_after_queue_prevents_claim(self):
        tid = self.submit()
        self.raw['lifecycle'] = 'revoked'
        with patch('platform_runtime.connectors.database_path') as path:
            self.assertFalse(self.e.tick('a'))
        path.assert_not_called()
        self.assertEqual('failed', self.e.get('a', tid)['status'])

    def test_revocation_after_claim_prevents_dispatch_read(self):
        tid = self.submit()
        original = self.e.claim
        def claim_then_revoke(*args, **kwargs):
            step = original(*args, **kwargs)
            self.raw['lifecycle'] = 'revoked'
            return step
        with patch.object(self.e, 'claim', side_effect=claim_then_revoke), \
             patch('platform_runtime.connectors.database_path') as path:
            self.e.tick('a')
        path.assert_not_called()
        self.assertEqual('failed', self.e.get('a', tid)['status'])

    def test_connection_agent_scope_rejected_at_submit(self):
        self.raw['agent_ids'] = ['finance']
        with self.assertRaises(Forbidden):
            self.submit(agent='ops')

    def test_connection_agent_scope_rechecked_at_dispatch(self):
        self.raw['agent_ids'] = ['finance']
        with patch('platform_runtime.connectors.database_path') as path, self.assertRaises(Forbidden):
            tool_read(self.e, 'a', 'ops', self.req, 'step')
        path.assert_not_called()

    def test_matching_agent_allowed(self):
        self.raw['agent_ids'] = ['ops']
        tid = self.submit()
        self.assertTrue(self.e.tick('a'))
        self.assertEqual('succeeded', self.e.get('a', tid)['status'])

    def test_agent_scope_tightened_after_queue(self):
        self.raw['agent_ids'] = ['ops']
        tid = self.submit()
        self.raw['agent_ids'] = ['finance']
        self.assertFalse(self.e.tick('a'))
        self.assertEqual('failed', self.e.get('a', tid)['status'])

    def test_nonempty_agent_scope_requires_direct_adapter_context(self):
        self.raw['agent_ids'] = ['ops']
        with self.assertRaises(Forbidden):
            read_rows('a', self.req)

    def test_empty_agent_scope_preserves_pack_policy(self):
        self.raw['agent_ids'] = []
        self.e.policy = lambda tenant, agent: {**self.policy(tenant, agent), 'allowed_connections': []}
        with self.assertRaises(Forbidden):
            tool_read(self.e, 'a', 'ops', self.req, 'step')

    def test_capability_without_read_denied(self):
        self.raw['capabilities'] = ['discover', 'validate']
        with self.assertRaises(Forbidden):
            read_rows('a', self.req)

    def test_contract_version_checked_before_read(self):
        self.raw['contract_version'] = '99'
        with self.assertRaises(ValueError):
            read_rows('a', self.req)

    def test_write_capability_cannot_bypass_readonly_contract(self):
        self.raw['capabilities'] = ['read', 'execute_write']
        with self.assertRaises(ValueError):
            read_rows('a', self.req)

    def test_cross_tenant_dispatch_denied(self):
        with self.assertRaises(Forbidden):
            tool_read(self.e, 'b', 'ops', self.req, 'step')

    def test_invalid_connection_entry_fails_closed(self):
        for raw in [None, [], 'oops', 1]:
            self.cfg['connections']['crm'] = raw
            with self.subTest(raw=raw), self.assertRaises((Forbidden, ValueError)):
                tool_read(self.e, 'a', 'ops', self.req, 'step')

    def test_postgres_revocation_applies_before_compiling_sql(self):
        raw = {'driver': 'postgres_readonly', 'host': 'db.example', 'allowed_hosts': ['db.example'],
               'database': 'db', 'user': 'reader', 'password_env': 'TEST_ONLY_PG_PASSWORD',
               'sslmode': 'verify-full', 'isolation': 'dedicated_database',
               'tables': {'contacts': ['id', 'name']}, 'lifecycle': 'revoked'}
        with self.assertRaises(Forbidden):
            compile_read('a', raw, self.req)

    def test_postgres_nonempty_agent_scope_requires_context(self):
        raw = {'driver': 'postgres_readonly', 'host': 'db.example', 'allowed_hosts': ['db.example'],
               'database': 'db', 'user': 'reader', 'password_env': 'TEST_ONLY_PG_PASSWORD',
               'sslmode': 'verify-full', 'isolation': 'dedicated_database',
               'tables': {'contacts': ['id', 'name']}, 'agent_ids': ['ops']}
        with self.assertRaises(Forbidden):
            compile_read('a', raw, self.req)

    def test_platform_database_hardlink_alias_denied(self):
        alias = self.root / 'alias.db'
        os.link(self.customer, alias)
        self.raw['path'] = str(alias)
        with self.assertRaises(Forbidden):
            read_rows('a', self.req, self.customer)

    def test_scoped_probe_requires_explicit_matching_agent(self):
        self.raw['agent_ids'] = ['ops']
        for agent in [None, 'finance']:
            with self.subTest(agent=agent), self.assertRaises(Forbidden):
                probe_read_connection(self.e, 'a', 'crm', actor='owner', agent=agent)

    def test_scoped_probe_succeeds_without_disclosing_rows_or_path(self):
        self.raw['agent_ids'] = ['ops']
        result = probe_read_connection(self.e, 'a', 'crm', actor='owner', agent='ops')
        self.assertTrue(result['ok'])
        self.assertFalse(result['persisted'])
        self.assertEqual('probe_succeeded', result['connection']['status'])
        self.assertNotIn('Ali', json.dumps(result))
        self.assertNotIn(str(self.customer), json.dumps(result))
        self.assertNotIn('rows', result)
        self.assertNotIn('lifecycle', self.raw)

    def test_unscoped_admin_probe_still_supported(self):
        self.assertTrue(probe_read_connection(self.e, 'a', 'crm', actor='owner')['ok'])

    def test_probe_cannot_bypass_pack_allowlist(self):
        self.e.policy = lambda tenant, agent: {**self.policy(tenant, agent), 'allowed_connections': []}
        with self.assertRaises(Forbidden):
            probe_read_connection(self.e, 'a', 'crm', actor='owner', agent='ops')

    def test_probe_cannot_bypass_pack_tool_scope(self):
        self.e.policy = lambda tenant, agent: {**self.policy(tenant, agent), 'tools': []}
        with self.assertRaises(Forbidden):
            probe_read_connection(self.e, 'a', 'crm', actor='owner', agent='ops')

    def test_probe_denied_when_frozen(self):
        self.e.freeze('a', True, 'owner')
        with self.assertRaises(Forbidden):
            probe_read_connection(self.e, 'a', 'crm', actor='owner')

    def test_probe_requires_actor(self):
        for actor in ['', None, 1]:
            with self.subTest(actor=actor), self.assertRaises(Forbidden):
                probe_read_connection(self.e, 'a', 'crm', actor=actor)

    def test_probe_rechecks_actor_before_io(self):
        checks = []
        def authority(c, tenant, channel, actor, roles):
            if channel == 'web':
                checks.append((actor, roles))
                if len(checks) == 2:
                    raise Forbidden('Actor revoked')
        self.e.authority = authority
        with patch('platform_runtime.connectors.database_path') as path, self.assertRaises(Forbidden):
            probe_read_connection(self.e, 'a', 'crm', actor='owner')
        path.assert_not_called()
        self.assertEqual([('owner', ('owner', 'integrator'))] * 2, checks)

    def test_agent_loop_uses_actual_sqlite_result_with_stub_planner(self):
        self.raw['agent_ids'] = ['ops']
        loop = AgentLoop(self.e)
        run_id = loop.create('a', 'loop-read', 'ops', 'Mijozni top', 'owner')
        loop.tick('a', lambda tenant, context: {'action': 'tool', 'tool': 'connectors.read', 'args': self.req})
        self.assertTrue(self.e.tick('a'))
        observed = []
        def final(tenant, context):
            observed.extend(context['observations'])
            return {'action': 'final', 'answer': context['observations'][0]['result']['rows'][0]['name'],
                    'evidence_ids': [context['observations'][0]['evidence_id']]}
        loop.tick('a', final)
        result = loop.get('a', run_id)
        self.assertEqual('succeeded', result['status'])
        self.assertEqual('Ali', result['answer'])
        self.assertEqual('connectors.read', observed[0]['tool'])
        self.assertEqual('not_performed', result['semantic_fact_check'])

    def test_agent_loop_rejects_revoked_connector_without_creating_task(self):
        loop = AgentLoop(self.e)
        run_id = loop.create('a', 'loop-revoked', 'ops', 'Mijozni top', 'owner')
        self.raw['lifecycle'] = 'revoked'
        loop.tick('a', lambda tenant, context: {'action': 'tool', 'tool': 'connectors.read', 'args': self.req})
        self.assertEqual('escalated', loop.get('a', run_id)['status'])
        self.assertEqual([], self.e.list_tasks('a'))

    def test_agent_loop_rechecks_connection_agent_scope_after_planning(self):
        loop = AgentLoop(self.e)
        run_id = loop.create('a', 'loop-scope', 'ops', 'Mijozni top', 'owner')
        def planner(tenant, context):
            self.raw['agent_ids'] = ['finance']
            return {'action': 'tool', 'tool': 'connectors.read', 'args': self.req}
        loop.tick('a', planner)
        self.assertEqual('escalated', loop.get('a', run_id)['status'])
        self.assertEqual([], self.e.list_tasks('a'))


class StrictConnectorMetadataTests(unittest.TestCase):
    def test_enabled_requires_json_boolean(self):
        for enabled in ['false', 'true', 0, 1, None, [], {}]:
            with self.subTest(enabled=enabled), self.assertRaises(ValueError):
                descriptor('crm', {'driver': 'sqlite_readonly', 'enabled': enabled})

    def test_bad_lifecycle_is_validation_error_not_type_error(self):
        for state in [[], {}, True, 1, None, 'unknown']:
            with self.subTest(state=state), self.assertRaises(ValueError):
                descriptor('crm', {'driver': 'sqlite_readonly', 'lifecycle': state})

    def test_capability_list_is_unique_bounded_and_typed(self):
        for caps in [['read', 'read'], [None], [{}], 'read', [], ['read'] * 100]:
            with self.subTest(caps=caps), self.assertRaises(ValueError):
                descriptor('crm', {'driver': 'sqlite_readonly', 'capabilities': caps})

    def test_agent_and_scope_lists_are_strict(self):
        for key in ['agent_ids', 'scopes']:
            for value in [[''], [' '], ['ops', 'ops'], [None], 'ops', ['ops'] * 101]:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    descriptor('crm', {'driver': 'sqlite_readonly', key: value})

    def test_driver_whitespace_is_not_silently_rewritten(self):
        with self.assertRaises(ValueError):
            descriptor('crm', {'driver': ' sqlite_readonly '})


class DeclaredBoundTests(unittest.TestCase):
    """Fazza 33: the connector layer's ceilings, almost none of which were pinned.

    Twenty-one revert modes were walked and FOURTEEN left the suite green. The read
    authority gate (`connector_authority`) was well covered; `connectors` and
    `connector_contract` were almost entirely unmeasured.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.customer = self.root / 'customer.db'
        db = sqlite3.connect(self.customer)
        try:
            db.executescript(
                "CREATE TABLE contacts(id INTEGER, name TEXT);"
                "INSERT INTO contacts VALUES(1,'Ali'),(2,'Vali');")
            db.commit()
        finally:
            db.close()
        self.raw = {'driver': 'sqlite_readonly', 'path': str(self.customer),
                    'tables': {'contacts': ['id', 'name']}}
        self.cfg = {'connections': {'crm': self.raw}}
        self.env = patch.dict(os.environ,
                              {'PLATFORM_DB_ROOTS': json.dumps([str(self.root)])})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config_patch = patch('platform_runtime.connectors.config',
                                  side_effect=lambda tenant: self.cfg if tenant == 'a' else {})
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def read(self, **extra):
        request = {'connection': 'crm', 'table': 'contacts', 'columns': ['id']}
        request.update(extra)
        return read_rows('a', request, self.root / 'platform.db')

    def test_the_connection_name_gate_matches_its_siblings(self):
        """One fact, three gates, and this one used a length-only check.

        `connector_contract._string` and `database.contract.text` both refuse a value
        with surrounding whitespace or a DEL character. `authorize_read_connection`
        checked only `1 <= len(name) <= 128`, so the same value was refused by two
        gates and accepted by the third -- the split fazza 30 found between
        `usage_budget.bounded` and `contract.text`.
        """
        from platform_runtime.connector_contract import _string
        from platform_runtime.connectors import authorize_read_connection
        from platform_runtime.database import contract
        for value in (' crm', 'crm ', 'c\x7frm', 'c\x01rm', '', '   ', 'a' * 129):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    authorize_read_connection('a', value)
                with self.assertRaises(ValueError):
                    _string(value, 'connection', 128)
                with self.assertRaises(ValueError):
                    contract.text(value)
        # and the ceiling itself is still accepted, through the GATE -- not merely
        # through a direct `_string` call with an explicit maximum, which would leave
        # the call site's own ceiling unpinned.
        self.assertEqual('a' * 128, _string('a' * 128, 'connection', 128))
        with self.assertRaises(Forbidden):
            authorize_read_connection('a', 'a' * 128)   # length is fine; not in config
        with self.assertRaises(ValueError):
            authorize_read_connection('a', 'a' * 129)   # length is not

    def test_the_identifier_ceiling_is_sixty_three_characters(self):
        from platform_runtime.connectors import IDENTIFIER, identifier
        self.assertTrue(IDENTIFIER.match('a' * 63))
        self.assertFalse(IDENTIFIER.match('a' * 64))
        self.assertFalse(IDENTIFIER.match('1a'))
        self.assertEqual('"tbl"', identifier('tbl'))

    def test_the_column_allowlist_ceiling_is_forty(self):
        # The table must CARRY the columns the allowlist names, or the authorizer
        # denies the read and sqlite raises DatabaseError instead of the ceiling's
        # ValueError. Measured the hard way.
        for count, accepted in ((1, True), (40, True), (41, False)):
            columns = ['c%d' % index for index in range(count)]
            db = sqlite3.connect(self.customer)
            try:
                db.execute('DROP TABLE IF EXISTS allowlist')
                db.execute('CREATE TABLE allowlist(%s)' % ','.join(columns))
                db.commit()
            finally:
                db.close()
            self.cfg['connections']['crm']['tables'] = {'allowlist': columns}
            with self.subTest(count=count):
                if accepted:
                    read_rows('a', {'connection': 'crm', 'table': 'allowlist',
                                    'columns': ['c0']}, self.root / 'platform.db')
                else:
                    with self.assertRaises(ValueError):
                        read_rows('a', {'connection': 'crm', 'table': 'allowlist',
                                        'columns': ['c0']}, self.root / 'platform.db')

    def test_the_request_column_ceiling_is_forty(self):
        """The ceiling is the allowlist's size, so both are walked together."""
        many = ['c%d' % index for index in range(40)]
        db = sqlite3.connect(self.customer)
        try:
            db.execute('CREATE TABLE wide(%s)' % ','.join(many))
            db.commit()
        finally:
            db.close()
        self.cfg['connections']['crm']['tables'] = {'wide': many}
        read_rows('a', {'connection': 'crm', 'table': 'wide', 'columns': many},
                  self.root / 'platform.db')
        with self.assertRaises(Forbidden):
            read_rows('a', {'connection': 'crm', 'table': 'wide',
                            'columns': many + ['extra']}, self.root / 'platform.db')
        with self.assertRaises(Forbidden):
            read_rows('a', {'connection': 'crm', 'table': 'wide', 'columns': []},
                      self.root / 'platform.db')
        # The request-side ceiling cannot be reached behaviourally: `columns` must be
        # a subset of the allowlist, and the allowlist is already capped at 40. It is
        # defensive redundancy, so the literal is pinned where it is written.
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'connectors.py').read_text(encoding='utf-8')
        self.assertIn('not 1 <= len(columns) <= 40', source)
        self.assertEqual(2, source.count('40'))

    def test_the_limit_ceiling_is_one_hundred(self):
        for limit, accepted in ((1, True), (100, True), (0, False), (101, False),
                                (True, False), (50.0, False)):
            with self.subTest(limit=limit):
                if accepted:
                    self.read(limit=limit)
                else:
                    with self.assertRaises(ValueError):
                        self.read(limit=limit)

    def test_the_filter_value_ceiling_is_one_thousand_characters(self):
        self.read(where={'column': 'id', 'equals': 'x' * 1000})
        with self.assertRaises(Forbidden):
            self.read(where={'column': 'id', 'equals': 'x' * 1001})

    def test_the_result_byte_ceilings(self):
        """16 000 per row and 80 000 in total, both measured through a real read."""
        # The framing is MEASURED, not assumed: `encode({'blob': ''})` is
        # `{"blob":""}`, and its length is what a cell of n characters adds to. An
        # earlier version assumed ten and put the "at the ceiling" cell one byte over.
        from platform_runtime.engine import encode
        framing = len(encode({'blob': ''}))
        self.assertEqual(11, framing)
        at_ceiling = 16_000 - framing
        over_ceiling = at_ceiling + 1
        def rebuild(table, sizes):
            db = sqlite3.connect(self.customer)
            try:
                db.execute('DROP TABLE IF EXISTS %s' % table)
                db.execute('CREATE TABLE %s(id INTEGER, blob TEXT)' % table)
                db.executemany('INSERT INTO %s VALUES(?,?)' % table,
                               [(index, 'x' * size) for index, size in enumerate(sizes)])
                db.commit()
            finally:
                db.close()
            self.cfg['connections']['crm']['tables'] = {table: ['id', 'blob']}
            return lambda: read_rows('a', {'connection': 'crm', 'table': table,
                                           'columns': ['blob'], 'limit': 100},
                                     self.root / 'platform.db')

        at_row_ceiling = rebuild('atrow', [at_ceiling])
        self.assertEqual(at_ceiling, len(at_row_ceiling()['rows'][0]['blob']))
        past_row_ceiling = rebuild('pastrow', [over_ceiling])
        with self.assertRaises(ValueError):
            past_row_ceiling()

        # Five rows of exactly the per-row ceiling is exactly 80 000 bytes in total,
        # which is NOT over the total ceiling; a sixth row is.
        at_total = rebuild('attotal', [at_ceiling] * 5)
        self.assertEqual(5, at_total()['returned'])
        past_total = rebuild('pasttotal', [at_ceiling] * 6)
        with self.assertRaises(ValueError):
            past_total()

        # ...and the TOTAL ceiling exactly. Five rows of the per-row ceiling gives
        # 80 000, and a sixth row jumps to 96 000 -- both sides of the mutation read
        # the same way. A cumulative 80 001 is the only total that separates
        # `> 80_000` from `> 80_001`, so the rows are sized to land on it.
        sizes = [16_000] * 4 + [1_001, 15_000]          # 80 001 in total
        exactly_over = rebuild('over', [size - framing for size in sizes])
        with self.assertRaises(ValueError):
            exactly_over()
        sizes[-1] -= 1                                   # 80 000 in total
        exactly_at = rebuild('exactly', [size - framing for size in sizes])
        self.assertEqual(6, exactly_at()['returned'])

    def test_the_query_limits_are_the_documented_values(self):
        """No cheap behavioural path: the literal and the line reading it are pinned."""
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'connectors.py').read_text(encoding='utf-8')
        for line in ('db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 100_000)',
                     'db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16_000)',
                     'deadline = time.monotonic() + 2',
                     'return int(calls >= 1000 or time.monotonic() >= deadline)'):
            with self.subTest(line=line):
                self.assertIn(line, source)

    def test_the_tool_schema_agrees_with_the_runtime_ceilings(self):
        """The model-facing contract and the runtime must state the same numbers."""
        properties = build_registry().get('connectors.read').schema['properties']
        self.assertEqual(128, properties['connection']['maxLength'])
        self.assertEqual(63, properties['table']['maxLength'])
        self.assertEqual(1, properties['columns']['minItems'])
        self.assertEqual(40, properties['columns']['maxItems'])
        self.assertEqual(63, properties['columns']['items']['maxLength'])
        self.assertEqual(1, properties['limit']['minimum'])
        self.assertEqual(100, properties['limit']['maximum'])
        self.assertEqual(63, properties['where']['properties']['column']['maxLength'])
        self.assertEqual(1000, properties['where']['properties']['equals']['maxLength'])

    def test_a_query_cannot_reach_a_table_outside_the_allowlist(self):
        with self.assertRaises(Forbidden):
            self.read(table='sqlite_master', columns=['name'])

if __name__ == '__main__':
    unittest.main()
