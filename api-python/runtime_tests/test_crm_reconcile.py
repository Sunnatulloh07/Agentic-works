import json
import os
import tempfile
import unittest
from pathlib import Path
from platform_runtime.engine import Engine, Conflict, Forbidden, encode
from platform_runtime.tools import build_registry
from platform_runtime.crm.crm_reconcile import CRMReconciler


class MockCRMAdapter:
    def __init__(self, leads_to_return=None):
        self.leads = leads_to_return or []

    def find_leads(self, phone=None, email=None, query=None, limit=5):
        return self.leads


class CRMReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'reconcile.db'
        self.tenant = 't_reconcile'
        self.actor = 'admin_user'
        self.policy = {'tools': ['crm.lead.create'], 'ladder': 'autonomous'}

        def authority(db, tenant, channel, actor, roles):
            if actor == 'unauthorized_stranger':
                raise Forbidden('Denied stranger')

        self.engine = Engine(self.db_path, build_registry(), lambda t, a: self.policy, authority=authority)
        
        # Insert a task
        with self.engine.tx() as db:
            db.execute(
                "INSERT INTO p_tasks(id,tenant,channel,event_key,fingerprint,agent,actor,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ('task_1', self.tenant, 'web', 'ev_1', 'fp_1', 'retail_agent', self.actor, 'running', 1000.0, 1000.0)
            )

        self.conn_config = {
            'driver': 'bitrix24',
            'host': 'mycrm.bitrix24.com',
            'allowed_hosts': ['mycrm.bitrix24.com'],
            'token_env': 'CRM_TOKEN',
            'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write', 'reconcile'],
        }
        self.tenant_config = {
            'connections': {
                'crm_b24': self.conn_config,
            }
        }
        os.environ['CRM_TOKEN'] = 'secret_token_123'

    def tearDown(self):
        self.tmp.cleanup()

    def _create_step(self, step_id, status='uncertain', tool='crm.lead.create'):
        args = {
            'connection': 'crm_b24',
            'request': {
                'title': 'Buyurtma #55',
                'phone': '+998901234567',
                'price': 450000,
            }
        }
        with self.engine.tx() as db:
            db.execute(
                "INSERT INTO p_steps(id,task,tenant,position,tool,args,risk,approval_needed,fingerprint,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (step_id, 'task_1', self.tenant, 0, tool, encode(args), 'write', 1, 'fp_step', status)
            )

    def test_positive_evidence_settles_uncertain_step(self):
        self._create_step('step_unc_1', status='uncertain')
        mock_adapter = MockCRMAdapter(leads_to_return=[
            {
                'id': 'b24_lead_999',
                'title': 'Buyurtma #55',
                'phone': '+998901234567',
            }
        ])

        reconciler = CRMReconciler(
            self.engine,
            config_resolver=lambda t: self.tenant_config,
            adapter_factory=lambda driver, raw: mock_adapter,
        )

        res = reconciler.reconcile_step(self.tenant, 'step_unc_1', self.actor)
        self.assertTrue(res['settled'])
        self.assertEqual('succeeded', res['status'])
        self.assertEqual('b24_lead_999', res['lead_id'])

        # Verify DB state
        with self.engine.read() as db:
            step = db.execute("SELECT * FROM p_steps WHERE tenant=? AND id='step_unc_1'", (self.tenant,)).fetchone()
            self.assertEqual('succeeded', step['status'])
            result = json.loads(step['result'])
            self.assertTrue(result['reconciled'])
            self.assertEqual('b24_lead_999', result['lead_id'])

            # Verify audit record
            audit = db.execute(
                "SELECT * FROM p_audit WHERE tenant=? AND action='crm.reconcile_settled'",
                (self.tenant,)
            ).fetchone()
            self.assertIsNotNone(audit)
            self.assertEqual('task_1', audit['task'])

    def test_no_match_keeps_uncertain(self):
        self._create_step('step_unc_2', status='uncertain')
        mock_adapter = MockCRMAdapter(leads_to_return=[])

        reconciler = CRMReconciler(
            self.engine,
            config_resolver=lambda t: self.tenant_config,
            adapter_factory=lambda driver, raw: mock_adapter,
        )

        res = reconciler.reconcile_step(self.tenant, 'step_unc_2', self.actor)
        self.assertFalse(res['settled'])
        self.assertEqual('uncertain', res['status'])

        # Verify DB step remains uncertain
        with self.engine.read() as db:
            step = db.execute("SELECT * FROM p_steps WHERE tenant=? AND id='step_unc_2'", (self.tenant,)).fetchone()
            self.assertEqual('uncertain', step['status'])

    def test_ambiguous_matches_keeps_uncertain(self):
        self._create_step('step_unc_3', status='uncertain')
        # Multiple leads found
        mock_adapter = MockCRMAdapter(leads_to_return=[
            {'id': 'lead_1', 'title': 'Buyurtma #55', 'phone': '+998901234567'},
            {'id': 'lead_2', 'title': 'Buyurtma #55', 'phone': '+998901234567'},
        ])

        reconciler = CRMReconciler(
            self.engine,
            config_resolver=lambda t: self.tenant_config,
            adapter_factory=lambda driver, raw: mock_adapter,
        )

        res = reconciler.reconcile_step(self.tenant, 'step_unc_3', self.actor)
        self.assertFalse(res['settled'])
        self.assertEqual('uncertain', res['status'])
        self.assertIn('Ambiguous', res['reason'])

    def test_non_uncertain_step_rejected(self):
        self._create_step('step_succeeded', status='succeeded')
        reconciler = CRMReconciler(
            self.engine,
            config_resolver=lambda t: self.tenant_config,
        )
        with self.assertRaises(Conflict):
            reconciler.reconcile_step(self.tenant, 'step_succeeded', self.actor)

    def test_unauthorized_actor_denied(self):
        self._create_step('step_unc_4', status='uncertain')
        reconciler = CRMReconciler(
            self.engine,
            config_resolver=lambda t: self.tenant_config,
        )
        with self.assertRaises(Forbidden):
            reconciler.reconcile_step(self.tenant, 'step_unc_4', 'unauthorized_stranger')


if __name__ == '__main__':
    unittest.main()
