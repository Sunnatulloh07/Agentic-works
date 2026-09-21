"""A failed inbound event must name the pack mistake, without leaking provider detail.

Before this, every planning failure was stored as a bare exception type name, so
an operator saw `Forbidden` in the inbox with no way to learn that the pack was
missing an agent or a tool. Provider exceptions keep the type-only treatment:
their messages can embed URLs, arguments or credentials.
"""
import json
import tempfile
import unittest
from pathlib import Path

from platform_runtime.engine import (Conflict, Engine, Forbidden, NotFound,
                                     RateLimited, event_error)
from platform_runtime.tools import build_registry


def policy(tenant, agent):
    if agent != 'ops':
        raise Forbidden('Agent not in tenant pack')
    return {'tools': ['reports.summary'], 'ladder': 'autonomous', 'approval': []}


class ReasonTests(unittest.TestCase):
    def test_platform_exceptions_carry_their_authored_reason(self):
        for error in (Forbidden('Agent not in tenant pack'),
                      Conflict('Idempotency key reused with different payload'),
                      NotFound('Task not found'),
                      RateLimited('Tenant inbox quota exhausted')):
            reason = event_error(error)
            self.assertTrue(reason.startswith(type(error).__name__))
            self.assertIn(str(error), reason)

    def test_unknown_exception_is_reported_by_type_only(self):
        secret = 'https://api.example/v1?key=SECRET-TOKEN'
        self.assertEqual('RuntimeError', event_error(RuntimeError(secret)))

    def test_value_error_outside_the_platform_hierarchy_stays_opaque(self):
        self.assertEqual('ValueError', event_error(ValueError('bearer abc123')))

    def test_reason_is_bounded(self):
        self.assertLessEqual(len(event_error(Forbidden('x' * 500))), 200)


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.e = Engine(Path(self.temp.name) / 'e.db', build_registry(), policy)
        self.e.accept_event('t', 'telegram', '1', {'text': 'salom', 'sender': 'u'})

    def stored(self):
        with self.e.read() as c:
            return dict(c.execute('SELECT status,error FROM p_events WHERE tenant=?', ('t',)).fetchone())

    def test_missing_agent_tells_the_operator_what_the_pack_lacks(self):
        self.e.process_event('t', lambda *a: {'agent': 'absent', 'steps': [{'tool': 'reports.summary', 'args': {}}]})
        row = self.stored()
        self.assertEqual('failed', row['status'])
        self.assertIn('Agent not in tenant pack', row['error'])

    def test_disallowed_tool_names_the_policy_rejection(self):
        self.e.process_event('t', lambda *a: {'agent': 'ops', 'steps': [{'tool': 'telegram.send', 'args': {'conversation_id': 'x', 'text': 'y'}}]})
        self.assertIn('Tool not allowed for agent', self.stored()['error'])

    def test_provider_failure_inside_the_planner_stays_opaque(self):
        def planner(*args):
            raise RuntimeError('https://api.telegram.org/bot123:SECRET/sendMessage')
        self.e.process_event('t', planner)
        row = self.stored()
        self.assertEqual('RuntimeError', row['error'])
        self.assertNotIn('SECRET', row['error'])

    def test_successful_planning_records_no_error(self):
        self.e.process_event('t', lambda *a: {'agent': 'ops', 'steps': [{'tool': 'reports.summary', 'args': {}}]})
        row = self.stored()
        self.assertEqual('done', row['status'])
        self.assertEqual('', row['error'])

    def test_failed_event_is_still_retryable_after_a_named_failure(self):
        self.e.process_event('t', lambda *a: {'agent': 'absent', 'steps': [{'tool': 'reports.summary', 'args': {}}]})
        self.e.retry_event('t', 'telegram', '1', 'owner')
        self.e.process_event('t', lambda *a: {'agent': 'ops', 'steps': [{'tool': 'reports.summary', 'args': {}}]})
        self.assertEqual('done', self.stored()['status'])
        with self.e.read() as c:
            saved = c.execute('SELECT result FROM p_events WHERE tenant=?', ('t',)).fetchone()['result']
        self.assertIn('task_id', json.loads(saved))


if __name__ == '__main__':
    unittest.main()
