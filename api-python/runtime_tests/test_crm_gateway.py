import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from platform_runtime.engine import Engine, Conflict, Forbidden
from platform_runtime.tools import build_registry
from platform_runtime.connectors import describe


class CRMGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'gateway.db'
        self.tenant = 't_crm'
        self.agent = 'sales_bot'
        self.policy = {
            'tools': [
                'crm.lead.search', 'crm.lead.plan', 'crm.lead.create',
                'crm.deal.create', 'crm.contact.lookup',
                'crm.timeline.attach_call', 'crm.timeline.attach_message',
                'crm.lead.followup',
            ],
            'allowed_connections': ['crm_bitrix', 'crm_kommo'],
            'ladder': 'autonomous',
        }
        self.engine = Engine(self.db_path, build_registry(), lambda t, a: self.policy)
        
        self.config_data = {
            self.tenant: {
                'connections': {
                    'crm_bitrix': {
                        'driver': 'bitrix24',
                        'host': 'sales.bitrix24.com',
                        'allowed_hosts': ['sales.bitrix24.com'],
                        'token_env': 'B24_TOKEN',
                        'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write', 'reconcile'],
                        'agent_ids': [self.agent],
                    },
                    'crm_kommo': {
                        'driver': 'kommo',
                        'host': 'my.kommo.com',
                        'allowed_hosts': ['my.kommo.com'],
                        'token_env': 'KOMMO_TOKEN',
                        'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write', 'reconcile'],
                        'agent_ids': [self.agent],
                    }
                }
            }
        }
        self.cfg_file = Path(self.tmp.name) / 'integrations.json'
        self.cfg_file.write_text(json.dumps(self.config_data), encoding='utf-8')
        os.environ['PLATFORM_INTEGRATIONS_FILE'] = str(self.cfg_file)
        os.environ['B24_TOKEN'] = 'test_token_b24'
        os.environ['KOMMO_TOKEN'] = 'test_token_kommo'

    def tearDown(self):
        self.tmp.cleanup()

    def test_crm_tools_registered(self):
        registry = build_registry()
        crm_tools = [
            'crm.lead.search',
            'crm.lead.plan',
            'crm.lead.create',
            'crm.deal.create',
            'crm.contact.lookup',
        ]
        for name in crm_tools:
            t = registry.get(name)
            self.assertIsNotNone(t)
            self.assertIn(t.risk, {'read', 'write'})

        self.assertEqual('read', registry.get('crm.lead.search').risk)
        self.assertEqual('read', registry.get('crm.lead.plan').risk)
        self.assertEqual('write', registry.get('crm.lead.create').risk)
        self.assertEqual('write', registry.get('crm.deal.create').risk)
        self.assertEqual('read', registry.get('crm.contact.lookup').risk)

    def test_crm_lead_plan_generates_valid_fingerprint(self):
        registry = build_registry()
        tool = registry.get('crm.lead.plan')
        
        args = {
            'connection': 'crm_bitrix',
            'request': {
                'title': 'Yangi Mijoz',
                'name': 'Otabek',
                'phone': '+998901234567',
                'price': 1000000,
            }
        }
        res = tool.handler(self.engine, self.tenant, self.agent, args, 'step_1')
        self.assertEqual('crm_bitrix', res['connection'])
        self.assertEqual('bitrix24', res['driver'])
        self.assertIn('plan_fingerprint', res)
        self.assertEqual(64, len(res['plan_fingerprint']))

    def test_crm_lead_create_tampered_fingerprint_rejected(self):
        registry = build_registry()
        tool = registry.get('crm.lead.create')
        
        args = {
            'connection': 'crm_bitrix',
            'request': {
                'title': 'Yangi Mijoz',
                'phone': '+998901234567',
                'price': 1000000,
            },
            'plan_fingerprint': 'tampered_bad_fingerprint_0000000000000000000000000000000000000000',
        }
        with self.assertRaises(Conflict) as ctx:
            tool.handler(self.engine, self.tenant, self.agent, args, 'step_1')
        self.assertIn('plan changed', str(ctx.exception))

    def test_crm_lead_create_success_with_valid_fingerprint(self):
        registry = build_registry()
        plan_tool = registry.get('crm.lead.plan')
        create_tool = registry.get('crm.lead.create')
        
        req = {
            'title': 'Yangi Mijoz',
            'name': 'Otabek',
            'phone': '+998901234567',
            'price': 1000000,
        }
        plan_res = plan_tool.handler(self.engine, self.tenant, self.agent, {'connection': 'crm_bitrix', 'request': req}, 'step_1')
        fp = plan_res['plan_fingerprint']
        
        create_args = {
            'connection': 'crm_bitrix',
            'request': req,
            'plan_fingerprint': fp,
        }

        with patch('platform_runtime.crm.bitrix24_adapter.Bitrix24Adapter.create_lead') as mock_create:
            mock_create.return_value = {'provider': 'bitrix24', 'lead_id': '888', 'created': True}
            res = create_tool.handler(self.engine, self.tenant, self.agent, create_args, 'step_1')
            self.assertEqual('crm_bitrix', res['connection'])
            self.assertEqual('888', res['result']['lead_id'])
            self.assertEqual('step_1', res['idempotency_reference'])

    def test_unpermitted_connection_denied(self):
        registry = build_registry()
        tool = registry.get('crm.lead.search')
        args = {
            'connection': 'forbidden_crm',
            'phone': '+998901234567',
        }
        with self.assertRaises(Forbidden):
            tool.handler(self.engine, self.tenant, self.agent, args, 'step_1')

    def test_attach_call_record_to_timeline(self):
        registry = build_registry()
        tool = registry.get('crm.timeline.attach_call')
        args = {
            'connection': 'crm_bitrix',
            'call': {
                'lead_id': '101',
                'audio_url': 'https://storage.example.uz/calls/rec1.mp3',
                'transcript': 'Salom, narxlar qanaqa? Narxlar 100 ming som.',
                'duration_seconds': 45,
                'direction': 'inbound',
                'sentiment': 'positive',
                'summary': 'Mijoz narx so‘radi va rozi bo‘ldi',
            }
        }
        with patch('platform_runtime.crm.bitrix24_adapter.Bitrix24Adapter.attach_call_record') as mock_attach:
            mock_attach.return_value = {'provider': 'bitrix24', 'lead_id': '101', 'attached': True}
            res = tool.handler(self.engine, self.tenant, self.agent, args, 'step_call')
            self.assertEqual('crm_bitrix', res['connection'])
            self.assertTrue(res['result']['attached'])

    def test_attach_chat_message_to_timeline(self):
        registry = build_registry()
        tool = registry.get('crm.timeline.attach_message')
        args = {
            'connection': 'crm_kommo',
            'message': {
                'lead_id': '202',
                'channel': 'telegram',
                'direction': 'inbound',
                'text': 'Katalog bormi?',
            }
        }
        with patch('platform_runtime.crm.kommo_adapter.KommoAdapter.attach_message') as mock_attach:
            mock_attach.return_value = {'provider': 'kommo', 'lead_id': '202', 'attached': True}
            res = tool.handler(self.engine, self.tenant, self.agent, args, 'step_msg')
            self.assertEqual('crm_kommo', res['connection'])
            self.assertTrue(res['result']['attached'])

    def test_followup_scheduling_audited(self):
        registry = build_registry()
        tool = registry.get('crm.lead.followup')
        args = {
            'connection': 'crm_bitrix',
            'followup': {
                'lead_id': '303',
                'action': 'call',
                'channel': 'telegram',
                'reason': 'Mijoz narx so‘rab o‘ylashga ketgan, qayta bog‘lanish kerak',
                'scheduled_at': 1726745000,
            }
        }
        res = tool.handler(self.engine, self.tenant, self.agent, args, 'step_flw')
        self.assertEqual('crm_bitrix', res['connection'])
        self.assertEqual('scheduled', res['status'])

        # Verify audit log
        with self.engine.read() as db:
            audit = db.execute(
                "SELECT * FROM p_audit WHERE tenant=? AND action='crm.lead.followup_scheduled'",
                (self.tenant,)
            ).fetchone()
            self.assertIsNotNone(audit)
            self.assertEqual(self.agent, audit['actor'])

    def test_connectors_describe_includes_crm(self):
        items = describe(self.tenant)
        crm_items = [i for i in items if i.get('mode') == 'crm_typed_operations']
        self.assertEqual(2, len(crm_items))
        
        drivers = {i['driver'] for i in crm_items}
        self.assertEqual({'bitrix24', 'kommo'}, drivers)
        for i in crm_items:
            self.assertTrue(i['transport_implemented'])
            self.assertEqual('configured_not_live_verified', i['status'])


if __name__ == '__main__':
    unittest.main()




class CRMAllowlistBoundaryTests(CRMGatewayTests):
    """An empty ``allowed_connections`` permits NOTHING.

    Every other module reads the policy that way; this gateway read it as "everything
    permitted" until fazza 39. It matters here more than anywhere else, because it is the
    ONLY gate for a CRM connection: the engine's own allowlist check covers
    ``{'connectors.read', 'database.read', 'database.plan_write', 'database.write'}`` and
    no ``crm.*`` tool is in that set.
    """

    def _engine(self, allowed, tag):
        return Engine(Path(self.tmp.name) / (tag + '.db'), build_registry(),
                      lambda t, a: {**self.policy, 'allowed_connections': allowed})

    def _search(self, engine):
        return build_registry().get('crm.lead.search').handler(
            engine, self.tenant, self.agent,
            {'connection': 'crm_bitrix', 'phone': '+998901234567'}, 's1')

    def test_an_empty_allowlist_permits_no_connection(self):
        with self.assertRaises(Forbidden):
            self._search(self._engine([], 'empty'))

    def test_an_absent_allowlist_permits_no_connection(self):
        engine = Engine(Path(self.tmp.name) / 'absent.db', build_registry(),
                        lambda t, a: {k: v for k, v in self.policy.items()
                                      if k != 'allowed_connections'})
        with self.assertRaises(Forbidden):
            self._search(engine)

    def test_the_adapter_is_never_reached(self):
        called = []
        with patch('platform_runtime.crm.crm_gateway.get_adapter',
                   side_effect=lambda *a, **k: called.append(1)):
            with self.assertRaises(Forbidden):
                self._search(self._engine([], 'noadapter'))
        self.assertEqual([], called)

    def test_a_tool_outside_the_policy_is_denied(self):
        engine = Engine(Path(self.tmp.name) / 'notool.db', build_registry(),
                        lambda t, a: {**self.policy, 'tools': ['crm.lead.plan']})
        with self.assertRaises(Forbidden):
            self._search(engine)

    def test_a_permitted_connection_still_reaches_the_adapter(self):
        with patch('platform_runtime.crm.crm_gateway.get_adapter') as factory:
            factory.return_value.find_leads.return_value = [{'id': '1'}]
            result = self._search(self._engine(['crm_bitrix'], 'permitted'))
        self.assertEqual(1, result['count'])
