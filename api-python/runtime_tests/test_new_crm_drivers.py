"""Gateway and reconciler integration for the 1C and custom HTTP drivers.

Exercises the real Engine, real SQLite state and the real tool handlers, with only
the provider socket replaced by a scripted transport. This is what proves the new
drivers are reachable through the same authorization, approval and reconciliation
path as Bitrix24 and Kommo rather than being a separate, weaker code path.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime.connectors import describe
from platform_runtime.crm.crm_contract import plan_crm_fingerprint
from platform_runtime.crm.crm_gateway import get_adapter
from platform_runtime.crm.crm_reconcile import CRMReconciler
from platform_runtime.engine import Conflict, Engine, Forbidden
from platform_runtime.tools import build_registry


AGENT = 'sales_bot'
TOOLS = [
    'crm.lead.search', 'crm.lead.plan', 'crm.lead.create', 'crm.deal.create',
    'crm.contact.lookup', 'crm.timeline.attach_call', 'crm.timeline.attach_message',
    'crm.lead.followup', 'crm.lead.stalled',
]

ONEC_CONFIG = {
    'driver': 'onec',
    'host': '1c.example.uz',
    'allowed_hosts': ['1c.example.uz'],
    'auth': 'basic',
    'basic_auth_env': 'ONEC_BASIC',
    'base_path': '/hs/leads',
    'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write', 'reconcile'],
    'agent_ids': [AGENT],
    'lifecycle': 'configured',
    'response_map': {'items': 'rows', 'id': 'Ref_Key', 'title': 'Description', 'phone': 'phone'},
}

CUSTOM_CONFIG = {
    'driver': 'custom_webhook',
    'host': 'crm.example.uz',
    'allowed_hosts': ['crm.example.uz'],
    'credential_env': 'CUSTOM_CRM_TOKEN',
    'base_path': '/api/v1',
    'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write', 'reconcile'],
    'agent_ids': [AGENT],
    'lifecycle': 'configured',
    'headers': {'Authorization': {'env': 'CUSTOM_CRM_TOKEN', 'prefix': 'Bearer '}},
    'operations': {
        'find_leads': {'method': 'GET', 'path': '/leads?q={query}&limit={limit}'},
        'find_contacts': {'method': 'GET', 'path': '/contacts?q={query}&limit={limit}'},
        'create_lead': {'method': 'POST', 'path': '/leads'},
        'attach_message': {'method': 'POST', 'path': '/leads/{lead_id}/notes'},
        'find_stalled_leads': {'method': 'GET', 'path': '/stalled?minutes={minutes}&limit={limit}'},
    },
    'response_map': {'items': 'data', 'id': 'id', 'title': 'title', 'phone': 'phone'},
}

PLACEHOLDER_CONFIG = {
    'driver': 'modme',
    'host': 'modme.example.uz',
    'allowed_hosts': ['modme.example.uz'],
    'token_env': 'MODME_TOKEN',
    'capabilities': ['discover', 'validate', 'read'],
}


class StubTransport:
    def __init__(self, response=None):
        self.response = {} if response is None else response
        self.calls = []

    def __call__(self, url, body=None, headers=None, method='GET', timeout=15):
        self.calls.append({'url': url, 'body': body, 'method': method})
        return self.response


class NewDriverGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tenant = 't_drivers'
        self.engine = Engine(Path(self.tmp.name) / 'gateway.db', build_registry(),
                             lambda t, a: {'tools': TOOLS,
                                           'allowed_connections': ['crm_onec', 'crm_custom'],
                                           'ladder': 'autonomous'})
        config = {self.tenant: {'connections': {'crm_onec': ONEC_CONFIG,
                                                'crm_custom': CUSTOM_CONFIG,
                                                'crm_placeholder': PLACEHOLDER_CONFIG}}}
        self.cfg_file = Path(self.tmp.name) / 'integrations.json'
        self.cfg_file.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        env = {'PLATFORM_INTEGRATIONS_FILE': str(self.cfg_file),
               'ONEC_BASIC': 'robot:secret', 'CUSTOM_CRM_TOKEN': 'token-value',
               'MODME_TOKEN': 'modme-token'}
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def tool(self, name):
        return build_registry().get(name)

    def call(self, name, args, step='s1'):
        return self.tool(name).handler(self.engine, self.tenant, AGENT, args, step)

    def test_new_tools_are_registered_with_correct_risk(self):
        registry = build_registry()
        self.assertEqual('read', registry.get('crm.lead.stalled').risk)
        self.assertTrue(registry.get('crm.lead.stalled').external)
        for name in ('crm.lead.search', 'crm.contact.lookup'):
            self.assertEqual('read', registry.get(name).risk)
        for name in ('crm.lead.create', 'crm.deal.create'):
            self.assertEqual('write', registry.get(name).risk)

    def test_onec_search_routes_through_query_mode(self):
        transport = StubTransport({'rows': [{'Ref_Key': 'A-1', 'Description': 'Anvar',
                                             'phone': '+998901234567'}]})
        with patch('platform_runtime.crm.onec_adapter.OneCAdapter._call',
                   side_effect=lambda path, method='GET', body=None: transport.response):
            result = self.call('crm.lead.search', {'connection': 'crm_onec', 'query': 'Anvar'})
        self.assertEqual('crm_onec', result['connection'])
        self.assertEqual(1, result['count'])
        self.assertEqual('A-1', result['leads'][0]['id'])

    def test_custom_http_search_routes_through_query_mode(self):
        response = {'data': [{'id': 'L-1', 'title': 'Anvar', 'phone': '+998901234567'}]}
        with patch('platform_runtime.crm.custom_http_adapter.CustomHTTPAdapter._call',
                   return_value=response):
            result = self.call('crm.lead.search', {'connection': 'crm_custom', 'query': 'Anvar'})
        self.assertEqual(1, result['count'])
        self.assertEqual('L-1', result['leads'][0]['id'])

    def test_plan_fingerprint_binds_connection_and_request(self):
        args = {'connection': 'crm_onec',
                'request': {'title': 'Yangi lid', 'phone': '+998901234567'}}
        first = self.call('crm.lead.plan', args)
        self.assertEqual(64, len(first['plan_fingerprint']))
        # A different connection must not share the approved fingerprint.
        other = plan_crm_fingerprint(self.tenant, AGENT, 'crm_custom', CUSTOM_CONFIG,
                                     first['request'])
        self.assertNotEqual(first['plan_fingerprint'], other)

    def test_driver_without_adapter_fails_closed(self):
        # modme is declared in CRM_DRIVERS but has no executable adapter yet.
        with self.assertRaises(Forbidden):
            self.call('crm.lead.search', {'connection': 'crm_placeholder', 'query': 'x'})

    def test_agent_without_tool_permission_is_denied(self):
        engine = Engine(Path(self.tmp.name) / 'denied.db', build_registry(),
                        lambda t, a: {'tools': ['crm.lead.search'],
                                      'allowed_connections': [], 'ladder': 'autonomous'})
        with self.assertRaises(Forbidden):
            build_registry().get('crm.lead.stalled').handler(
                engine, self.tenant, AGENT, {'connection': 'crm_onec'}, 's1')

    def test_connection_outside_agent_allowlist_is_denied(self):
        engine = Engine(Path(self.tmp.name) / 'conn.db', build_registry(),
                        lambda t, a: {'tools': TOOLS, 'allowed_connections': ['crm_onec'],
                                      'ladder': 'autonomous'})
        with self.assertRaises(Forbidden):
            build_registry().get('crm.lead.search').handler(
                engine, self.tenant, AGENT, {'connection': 'crm_custom', 'query': 'x'}, 's1')

    def test_stalled_feed_reports_provider_failure_as_conflict(self):
        # An empty result must never stand in for "provider unreachable", because
        # the follow-up loop would silently stop re-engaging leads.
        with patch('platform_runtime.crm.onec_adapter.OneCAdapter._call',
                   side_effect=RuntimeError('socket down')):
            with self.assertRaises(Conflict):
                self.call('crm.lead.stalled', {'connection': 'crm_onec', 'inactive_minutes': 60})

    def test_stalled_feed_returns_provider_leads(self):
        response = {'rows': [{'Ref_Key': 'A-1', 'Description': 'waiting'}]}
        with patch('platform_runtime.crm.onec_adapter.OneCAdapter._call', return_value=response):
            result = self.call('crm.lead.stalled', {'connection': 'crm_onec'})
        self.assertEqual(1, result['count'])
        self.assertEqual(120, result['inactive_minutes'])
        self.assertEqual('A-1', result['leads'][0]['id'])

    def test_connectors_describe_marks_implemented_and_placeholder_drivers(self):
        items = {item['id']: item for item in describe(self.tenant)}
        self.assertEqual('configured_not_live_verified', items['crm_onec']['status'])
        self.assertEqual('configured_not_live_verified', items['crm_custom']['status'])
        self.assertEqual('adapter_required', items['crm_placeholder']['status'])
        self.assertTrue(items['crm_onec']['transport_implemented'])
        self.assertFalse(items['crm_placeholder']['transport_implemented'])
        # No driver may claim live verification; that is a deployment-time gate.
        for item in items.values():
            self.assertFalse(item['live_verified'])


class StaleArticlePlaceholderTests(unittest.TestCase):
    """Guard the two remaining declarative-only drivers stay fail-closed."""

    def test_placeholder_driver_raises_a_named_error(self):
        for driver in ('modme', 'billz', 'moysklad', 'retailcrm', 'yclients', 'jowi', 'poster'):
            with self.subTest(driver=driver):
                with self.assertRaises(Forbidden) as raised:
                    get_adapter({'driver': driver, 'host': 'x.example.uz',
                                 'allowed_hosts': ['x.example.uz'], 'token_env': 'TOK'})
                self.assertIn('no executable adapter', str(raised.exception))

    def test_unknown_driver_is_distinguishable(self):
        with self.assertRaises(Forbidden) as raised:
            get_adapter({'driver': 'not_a_driver'})
        self.assertIn('Unsupported CRM driver', str(raised.exception))


class NewDriverReconcileTests(unittest.TestCase):
    """Owner-triggered read-only settlement for the new drivers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tenant = 't_reconcile'
        self.engine = Engine(Path(self.tmp.name) / 'reconcile.db', build_registry(),
                             lambda t, a: {'tools': TOOLS, 'ladder': 'autonomous'})
        self.connections = {'crm_onec': ONEC_CONFIG, 'crm_custom': CUSTOM_CONFIG}
        env = {'ONEC_BASIC': 'robot:secret', 'CUSTOM_CRM_TOKEN': 'token-value'}
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.reconciler = CRMReconciler(self.engine, lambda tenant: {'connections': self.connections})

    def uncertain_step(self, driver_connection, request):
        """Create a real task/step already marked uncertain, as a timeout would."""
        step = {'tool': 'crm.lead.create',
                'args': {'connection': driver_connection, 'request': request,
                         'plan_fingerprint': 'f' * 64}}
        task = self.engine.submit(self.tenant, 'web', 'k-' + driver_connection, AGENT, [step],
                                  actor='automation')
        with self.engine.tx() as db:
            row = db.execute('SELECT id FROM p_steps WHERE tenant=? AND task=?',
                             (self.tenant, task)).fetchone()
            db.execute("UPDATE p_steps SET status='uncertain' WHERE tenant=? AND id=?",
                       (self.tenant, row['id']))
        return row['id']

    def test_onec_positive_readback_settles_step(self):
        step_id = self.uncertain_step('crm_onec', {'title': 'Anvar aka', 'phone': '+998901234567'})
        response = {'rows': [{'Ref_Key': 'A-1', 'Description': 'Anvar aka',
                              'phone': '+998901234567'}]}
        with patch('platform_runtime.crm.onec_adapter.OneCAdapter._call', return_value=response):
            result = self.reconciler.reconcile_step(self.tenant, step_id, 'usr_owner')
        self.assertTrue(result['settled'])
        self.assertEqual('A-1', result['lead_id'])

    def test_onec_absent_match_stays_uncertain(self):
        step_id = self.uncertain_step('crm_onec', {'title': 'Anvar aka', 'phone': '+998901234567'})
        with patch('platform_runtime.crm.onec_adapter.OneCAdapter._call',
                   return_value={'rows': [{'Ref_Key': 'B-9', 'Description': 'Someone else'}]}):
            result = self.reconciler.reconcile_step(self.tenant, step_id, 'usr_owner')
        self.assertFalse(result['settled'])
        self.assertEqual('uncertain', result['status'])

    def test_onec_ambiguous_match_stays_uncertain(self):
        step_id = self.uncertain_step('crm_onec', {'title': 'Anvar aka', 'phone': '+998901234567'})
        response = {'rows': [{'Ref_Key': 'A-1', 'Description': 'Anvar aka'},
                             {'Ref_Key': 'A-2', 'Description': 'Anvar aka'}]}
        with patch('platform_runtime.crm.onec_adapter.OneCAdapter._call', return_value=response):
            result = self.reconciler.reconcile_step(self.tenant, step_id, 'usr_owner')
        self.assertFalse(result['settled'])
        self.assertIn('Ambiguous', result['reason'])

    def test_custom_http_positive_readback_settles_step(self):
        step_id = self.uncertain_step('crm_custom', {'title': 'Anvar aka', 'phone': '+998901234567'})
        response = {'data': [{'id': 'L-7', 'title': 'Anvar aka', 'phone': '+998901234567'}]}
        with patch('platform_runtime.crm.custom_http_adapter.CustomHTTPAdapter._call',
                   return_value=response):
            result = self.reconciler.reconcile_step(self.tenant, step_id, 'usr_owner')
        self.assertTrue(result['settled'])
        self.assertEqual('L-7', result['lead_id'])

    def test_provider_lookup_failure_never_settles(self):
        step_id = self.uncertain_step('crm_custom', {'title': 'Anvar aka', 'phone': '+998901234567'})
        with patch('platform_runtime.crm.custom_http_adapter.CustomHTTPAdapter._call',
                   side_effect=RuntimeError('socket down')):
            result = self.reconciler.reconcile_step(self.tenant, step_id, 'usr_owner')
        self.assertFalse(result['settled'])
        self.assertEqual('uncertain', result['status'])


if __name__ == '__main__':
    unittest.main()