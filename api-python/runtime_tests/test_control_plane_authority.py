"""v0.3.1 source-only regressions. Authored, not executed by the agent."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import identity_store as identity
from app.customer360 import add_contact, add_order, create_customer, link_channel_identity
from app.runtime_authority import runtime_authority
from app.storage import reset, tx
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry


class ControlPlaneAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'authority.db'
        self.env = patch.dict(os.environ, {
            'APP_DB': str(self.path), 'ENV': 'production',
            'IDENTITY_DIRECTORY': 'true',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        reset()
        self.addCleanup(reset)
        self.owner = identity.register_user('owner@example.com', 'fixture password 123', 'Owner')['id']
        identity.create_workspace(self.owner, 'work', 'Work')
        _, invitation = identity.create_invitation(self.owner, 'work', 'member@example.com', 'operator')
        self.member = identity.register_with_invitation(
            'member@example.com', 'fixture password 123', 'Member', invitation,
        )['id']
        self.e = Engine(self.path, build_registry(), lambda tenant, agent: {
            'tools': ['reports.summary', 'records.create', 'fs.list'],
            'ladder': 'autonomous',
        }, authority=runtime_authority)
        self.steps = [{'tool': 'reports.summary', 'args': {}}]

    def submit(self, actor=None, key='task', steps=None):
        return self.e.submit('work', 'web', key, 'ops', steps or self.steps, actor or self.member)

    def revoke_member(self):
        identity.revoke_membership(self.owner, 'work', self.member)

    def row(self, sql, params=()):
        with self.e.read() as c:
            found = c.execute(sql, params).fetchone()
            return dict(found) if found else None

    def failed_event(self, channel='web'):
        payload = {'sender': self.member if channel == 'web' else 'provider-user', 'text': 'hello'}
        self.e.accept_event('work', channel, 'event', payload)
        # Fixture state only. No planner or provider is called by this test.
        with self.e.tx() as c:
            c.execute("UPDATE p_events SET status='failed' WHERE tenant='work'")
        return payload

    def test_revoked_operator_cannot_cancel_queued_task(self):
        tid = self.submit()
        before = self.e.get('work', tid)
        self.revoke_member()
        with self.assertRaises(Forbidden):
            self.e.cancel('work', tid, self.member)
        self.assertEqual(before, self.e.get('work', tid))

    def test_active_operator_can_cancel_while_frozen(self):
        tid = self.submit()
        self.e.freeze('work', True, self.owner)
        self.e.cancel('work', tid, self.member)
        self.assertEqual('cancelled', self.e.get('work', tid)['status'])

    def test_claimed_owner_role_does_not_authorize_operator_reconcile(self):
        tid = self.submit()
        step = self.e.claim('work', 'worker')
        self.e.cancel('work', tid, self.owner)
        with self.assertRaises(Forbidden):
            self.e.reconcile('work', step['id'], self.member, 'owner', 'succeeded', 'receipt')
        self.assertEqual('uncertain', self.e.get('work', tid)['status'])

    def test_owner_can_reconcile_uncertain_step_while_frozen(self):
        tid = self.submit()
        step = self.e.claim('work', 'worker')
        self.e.freeze('work', True, self.owner)
        self.e.reconcile('work', step['id'], self.owner, 'owner', 'failed', 'operator evidence')
        self.assertEqual('failed', self.e.get('work', tid)['status'])

    def test_reconcile_rejects_non_string_or_overlong_evidence(self):
        for evidence in (None, True, 1, [], ' ', 'x' * 501):
            with self.subTest(evidence_type=type(evidence).__name__), self.assertRaises(ValueError):
                self.e.reconcile('work', 'missing', self.owner, 'owner', 'failed', evidence)

    def test_demoted_owner_cannot_freeze(self):
        with tx() as c:
            c.execute("UPDATE p_memberships SET role='viewer' WHERE user_id=?", (self.owner,))
        with self.assertRaises(Forbidden):
            self.e.freeze('work', True, self.owner)
        self.assertIsNone(self.row('SELECT * FROM p_freeze WHERE tenant=?', ('work',)))

    def test_owner_can_unfreeze(self):
        self.e.freeze('work', True, self.owner)
        self.e.freeze('work', False, self.owner)
        self.assertEqual(0, self.row('SELECT * FROM p_freeze WHERE tenant=?', ('work',))['stopped'])

    def test_freeze_requires_boolean_without_coercion(self):
        for value in ('false', 'true', 0, 1, None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.e.freeze('work', value, self.owner)
        self.assertIsNone(self.row('SELECT * FROM p_freeze WHERE tenant=?', ('work',)))

    def test_disabled_owner_cannot_mutate_control_plane(self):
        tid = self.submit()
        with tx() as c:
            c.execute("UPDATE p_users SET status='disabled' WHERE id=?", (self.owner,))
        for action in (
            lambda: self.e.freeze('work', True, self.owner),
            lambda: self.e.cancel('work', tid, self.owner),
            lambda: self.e.device('work', 'pc', actor=self.owner),
        ):
            with self.assertRaises(Forbidden):
                action()

    def test_suspended_workspace_rejects_control_plane_mutation(self):
        with tx() as c:
            c.execute("UPDATE p_workspaces SET status='suspended' WHERE id='work'")
        with self.assertRaises(Forbidden):
            self.e.freeze('work', False, self.owner)
        with self.assertRaises(Forbidden):
            self.e.device('work', 'pc', True, actor=self.owner)

    def test_unknown_workspace_cannot_be_created_by_freeze_or_device(self):
        with self.assertRaises(Forbidden):
            self.e.freeze('unknown', True, self.owner)
        with self.assertRaises(Forbidden):
            self.e.device('unknown', 'pc', actor=self.owner)

    def test_device_enrollment_requires_explicit_owner(self):
        for actor in ('', self.member, 'non-member'):
            with self.subTest(actor=actor), self.assertRaises(Forbidden):
                self.e.device('work', 'pc', actor=actor)
        self.assertIsNone(self.row('SELECT * FROM p_devices WHERE tenant=?', ('work',)))

    def test_revoked_second_owner_cannot_rotate_device(self):
        with tx() as c:
            c.execute("UPDATE p_memberships SET role='owner' WHERE user_id=?", (self.member,))
        self.e.device('work', 'pc', actor=self.member)
        self.revoke_member()
        with self.assertRaises(Forbidden):
            self.e.device('work', 'pc', actor=self.member)
        self.assertEqual(1, self.row('SELECT generation FROM p_devices WHERE tenant=?', ('work',))['generation'])

    def test_frozen_workspace_denies_enrollment_but_allows_revocation(self):
        self.e.device('work', 'pc', actor=self.owner)
        self.e.freeze('work', True, self.owner)
        with self.assertRaises(Forbidden):
            self.e.device('work', 'pc', actor=self.owner)
        self.assertEqual(2, self.e.device('work', 'pc', True, actor=self.owner))
        self.assertEqual(1, self.row('SELECT revoked FROM p_devices WHERE tenant=?', ('work',))['revoked'])

    def test_device_mutation_and_audit_share_transaction(self):
        self.e.device('work', 'pc', actor=self.owner)
        with patch.object(self.e, 'audit', side_effect=RuntimeError('audit storage unavailable')):
            with self.assertRaises(RuntimeError):
                self.e.device('work', 'pc', True, actor=self.owner)
        row = self.row('SELECT generation,revoked FROM p_devices WHERE tenant=?', ('work',))
        self.assertEqual({'generation': 1, 'revoked': 0}, row)
        audit = self.row("SELECT actor,data FROM p_audit WHERE action='device.enrolled'")
        self.assertEqual(self.owner, audit['actor'])
        self.assertEqual({'device': 'pc', 'generation': 1}, json.loads(audit['data']))

    def test_device_audit_failure_rolls_back_tasks_and_approvals(self):
        self.e.device('work', 'pc', actor=self.owner)
        self.e.policy = lambda tenant, agent: {'tools': ['fs.list'], 'ladder': 'human_led'}
        tid = self.submit(steps=[{'tool': 'fs.list', 'args': {'dir': '/safe'}, 'device': 'pc'}])
        before = self.e.get('work', tid)
        with patch.object(self.e, 'audit', side_effect=RuntimeError('audit storage unavailable')):
            with self.assertRaises(RuntimeError):
                self.e.device('work', 'pc', True, actor=self.owner)
        self.assertEqual(before, self.e.get('work', tid))
        self.assertEqual('pending', self.row('SELECT status FROM p_approvals')['status'])

    def test_device_rotation_retires_pending_approval(self):
        self.e.device('work', 'pc', actor=self.owner)
        self.e.policy = lambda tenant, agent: {'tools': ['fs.list'], 'ladder': 'human_led'}
        tid = self.submit(steps=[{'tool': 'fs.list', 'args': {'dir': '/safe'}, 'device': 'pc'}])
        self.e.device('work', 'pc', actor=self.owner)
        self.assertEqual('cancelled', self.e.get('work', tid)['status'])
        approval = self.row('SELECT status,actor,decided FROM p_approvals')
        self.assertEqual('rejected', approval['status'])
        self.assertEqual(self.owner, approval['actor'])
        self.assertIsNotNone(approval['decided'])

    def test_device_service_validates_identity_and_boolean(self):
        for device in ('', '../pc', 'x' * 129, 'pc\n', None):
            with self.subTest(device=device), self.assertRaises(ValueError):
                self.e.device('work', device, actor=self.owner)
        for revoked in ('false', 0, 1, None):
            with self.subTest(revoked=revoked), self.assertRaises(ValueError):
                self.e.device('work', 'pc', revoked, actor=self.owner)

    def test_revoked_operator_cannot_retry_external_event(self):
        self.failed_event('telegram')
        self.revoke_member()
        with self.assertRaises(Forbidden):
            self.e.retry_event('work', 'telegram', 'event', self.member)
        self.assertEqual('failed', self.row('SELECT status FROM p_events')['status'])

    def test_active_operator_can_retry_external_event(self):
        self.failed_event('telegram')
        self.e.retry_event('work', 'telegram', 'event', self.member)
        self.assertEqual('pending', self.row('SELECT status FROM p_events')['status'])

    def test_active_owner_cannot_requeue_revoked_web_sender(self):
        self.failed_event()
        self.revoke_member()
        with self.assertRaises(Forbidden):
            self.e.retry_event('work', 'web', 'event', self.owner)
        self.assertEqual('failed', self.row('SELECT status FROM p_events')['status'])

    def test_task_replay_rechecks_creator_authority(self):
        self.submit()
        self.revoke_member()
        with self.assertRaises(Forbidden):
            self.submit()

    def test_authorized_task_replay_remains_available_while_frozen(self):
        tid = self.submit()
        self.e.freeze('work', True, self.owner)
        self.assertEqual(tid, self.submit())

    def test_event_replay_rechecks_sender_authority(self):
        payload = {'sender': self.member, 'text': 'hello'}
        self.e.accept_event('work', 'web', 'event', payload)
        self.revoke_member()
        with self.assertRaises(Forbidden):
            self.e.accept_event('work', 'web', 'event', payload)

    def test_authorized_event_replay_is_read_only_while_frozen(self):
        payload = {'sender': self.member, 'text': 'hello'}
        self.e.accept_event('work', 'web', 'event', payload)
        self.e.freeze('work', True, self.owner)
        result = self.e.accept_event('work', 'web', 'event', payload)
        self.assertTrue(result['duplicate'])
        self.assertEqual(1, self.row('SELECT count FROM p_quota')['count'])

    def test_schedule_requires_exact_integer_interval(self):
        for interval in (True, 60.0, '60', None, 59, 31_536_001):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.e.schedule('work', 'schedule', 'ops', self.steps, interval, actor=self.owner)

    def test_non_object_event_rejected_before_mutation(self):
        for payload in (None, [], 'text', 1, True):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.e.accept_event('work', 'web', 'event', payload)
        self.assertIsNone(self.row('SELECT * FROM p_quota'))

    def test_missing_customer_actor_is_not_trusted_service_identity(self):
        customer = create_customer('work', 'Existing', actor=self.owner)['id']
        for mutation in (
            lambda: create_customer('work', 'Anonymous'),
            lambda: add_contact('work', customer, 'email', 'a@example.com'),
            lambda: add_order('work', customer, 'order'),
            lambda: link_channel_identity('work', customer, 'telegram', 'chat', verified=True),
        ):
            with self.assertRaises(identity.AuthenticationError):
                mutation()
        self.assertEqual(1, self.row('SELECT count(*) n FROM p_customers')['n'])
        for table in ('p_customer_contacts', 'p_customer_orders', 'p_channel_identities'):
            self.assertEqual(0, self.row('SELECT count(*) n FROM ' + table)['n'])

    def test_revoked_actor_cannot_write_customer(self):
        self.revoke_member()
        with self.assertRaises(identity.AuthenticationError):
            create_customer('work', 'Denied', actor=self.member)

    def test_disabled_actor_cannot_write_customer(self):
        with tx() as c:
            c.execute("UPDATE p_users SET status='disabled' WHERE id=?", (self.member,))
        with self.assertRaises(identity.AuthenticationError):
            create_customer('work', 'Denied', actor=self.member)

    def test_suspended_workspace_cannot_write_customer(self):
        with tx() as c:
            c.execute("UPDATE p_workspaces SET status='suspended' WHERE id='work'")
        with self.assertRaises(identity.AuthenticationError):
            create_customer('work', 'Denied', actor=self.owner)

    def test_customer_actor_from_different_workspace_denied(self):
        outsider = identity.register_user('outsider@example.com', 'fixture password 123', 'Outsider')['id']
        identity.create_workspace(outsider, 'other', 'Other')
        with self.assertRaises(identity.AuthenticationError):
            create_customer('work', 'Denied', actor=outsider)


    def test_agent_run_creator_revoked_before_model_call(self):
        from platform_runtime.agent_loop import AgentLoop
        loop=AgentLoop(self.e)
        run_id=loop.create('work','run','ops','Hisobot',self.member)
        self.revoke_member()
        calls=[]
        loop.tick('work',lambda *args:calls.append(args))
        self.assertEqual([],calls)
        self.assertEqual('escalated',loop.get('work',run_id)['status'])
        self.assertEqual([],self.e.list_tasks('work'))

    def test_agent_run_creator_revoked_during_planning_cannot_submit(self):
        from platform_runtime.agent_loop import AgentLoop
        loop=AgentLoop(self.e)
        run_id=loop.create('work','run','ops','Hisobot',self.member)
        def planner(*args):
            self.revoke_member()
            return {'action':'tool','tool':'reports.summary','args':{}}
        loop.tick('work',planner)
        self.assertEqual('escalated',loop.get('work',run_id)['status'])
        self.assertEqual([],self.e.list_tasks('work'))

    def test_agent_run_cancel_checks_current_member(self):
        from platform_runtime.agent_loop import AgentLoop
        loop=AgentLoop(self.e)
        run_id=loop.create('work','run','ops','Hisobot',self.owner)
        self.revoke_member()
        with self.assertRaises(Forbidden):loop.cancel('work',run_id,self.member)
        self.assertEqual('pending',loop.get('work',run_id)['status'])

    def test_agent_channel_is_directory_authorized(self):
        self.revoke_member()
        with self.assertRaises(Forbidden):
            self.e.submit('work','agent','run','ops',self.steps,self.member)



if __name__ == '__main__':
    unittest.main()
