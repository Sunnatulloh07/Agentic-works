"""Custom HTTP adapter contract tests. Fake transport; no live provider is contacted.

The point of these tests is that an operator can onboard an arbitrary regional CRM
without the adapter becoming a general-purpose request primitive: destination,
method and body shape are all operator configuration, and every caller-supplied
value is percent-encoded before it reaches a URL.
"""
import os
import unittest
from unittest.mock import patch

from platform_runtime.crm.crm_contract import safe_relative_path, validate_custom_http_config
from platform_runtime.crm.custom_http_adapter import (
    CustomHTTPAdapter,
    CustomHTTPError,
    default_custom_transport,
)
from platform_runtime.engine import Conflict, Forbidden


OPERATIONS = {
    'find_leads': {'method': 'GET', 'path': '/leads?q={query}&limit={limit}'},
    'find_contacts': {'method': 'GET', 'path': '/contacts?q={query}&limit={limit}'},
    'create_lead': {'method': 'POST', 'path': '/leads',
                    'body': {'full_name': 'name', 'tel': 'phone', 'note': 'comments'}},
    'create_deal': {'method': 'POST', 'path': '/deals'},
    'attach_message': {'method': 'POST', 'path': '/leads/{lead_id}/notes'},
    'find_stalled_leads': {'method': 'GET', 'path': '/leads/stalled?minutes={minutes}&limit={limit}'},
}

BASE = {
    'driver': 'custom_webhook',
    'host': 'crm.example.uz',
    'allowed_hosts': ['crm.example.uz'],
    'base_path': '/api/v1',
    'credential_env': 'CUSTOM_CRM_TOKEN',
    'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write', 'reconcile'],
    'agent_ids': ['sales_bot'],
    'headers': {
        'Authorization': {'env': 'CUSTOM_CRM_TOKEN', 'prefix': 'Bearer '},
        'X-Tenant': 'demo',
    },
    'operations': OPERATIONS,
    'response_map': {
        'items': 'data',
        'id': 'id',
        'title': 'title',
        'phone': 'contacts.phone',
        'email': 'contacts.email',
        'status': 'stage',
    },
}


class RecordingTransport:
    def __init__(self, response=None):
        self.response = {} if response is None else response
        self.calls = []

    def __call__(self, url, body=None, headers=None, method='GET', timeout=15):
        self.calls.append({'url': url, 'body': body, 'headers': headers,
                           'method': method, 'timeout': timeout})
        return self.response


class CustomHTTPAdapterTests(unittest.TestCase):
    def adapter(self, transport=None, **overrides):
        config = {**BASE, **overrides}
        return CustomHTTPAdapter(config, transport=transport or RecordingTransport())

    def build(self, transport=None, **overrides):
        """Build under a valid credential environment."""
        with patch.dict(os.environ, {'CUSTOM_CRM_TOKEN': 'secret-token'}):
            return self.adapter(transport=transport, **overrides)

    def test_credential_comes_from_environment_with_prefix(self):
        with patch.dict(os.environ, {'CUSTOM_CRM_TOKEN': 'secret-token'}):
            adapter = self.adapter()
        self.assertEqual('Bearer secret-token', adapter.headers['Authorization'])
        self.assertEqual('demo', adapter.headers['X-Tenant'])

    def test_missing_credential_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                self.adapter()

    def test_host_header_override_denied(self):
        with patch.dict(os.environ, {'CUSTOM_CRM_TOKEN': 'secret-token'}):
            with self.assertRaises(ValueError):
                self.adapter(headers={'Host': 'evil.example.com'})

    def test_invalid_header_name_denied(self):
        with patch.dict(os.environ, {'CUSTOM_CRM_TOKEN': 'secret-token'}):
            for name in ['Bad Header', 'X\nInjected', '']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.adapter(headers={name: 'value'})

    def test_header_must_reference_a_real_environment_variable(self):
        with patch.dict(os.environ, {'CUSTOM_CRM_TOKEN': 'secret-token'}):
            with self.assertRaises(ValueError):
                self.adapter(headers={'Authorization': {'env': 'lowercase_ref'}})
            with self.assertRaises(RuntimeError):
                self.adapter(headers={'Authorization': {'env': 'ABSENT_REF'}})

    def test_host_must_be_allowlisted(self):
        with patch.dict(os.environ, {'CUSTOM_CRM_TOKEN': 'secret-token'}):
            with self.assertRaises(Forbidden):
                self.adapter(host='evil.example.com')

    def test_request_url_is_host_and_base_path_pinned(self):
        transport = RecordingTransport({'data': []})
        adapter = self.build(transport=transport)
        adapter.find_leads(query='Ali', limit=5)
        self.assertEqual('https://crm.example.uz/api/v1/leads?q=Ali&limit=5',
                         transport.calls[0]['url'])
        self.assertEqual('GET', transport.calls[0]['method'])

    def test_query_is_percent_encoded_so_plan_cannot_retarget(self):
        transport = RecordingTransport({'data': []})
        adapter = self.build(transport=transport)
        adapter.find_leads(query='x&limit=999&path=/../admin?')
        url = transport.calls[0]['url']
        self.assertTrue(url.startswith('https://crm.example.uz/api/v1/leads?q='))
        self.assertNotIn('limit=999', url)
        self.assertNotIn('/../admin', url)
        self.assertEqual(1, url.count('?'))

    def test_undeclared_operation_is_denied(self):
        adapter = self.build(operations={'find_leads': OPERATIONS['find_leads']})
        with self.assertRaises(Forbidden):
            adapter.create_lead({'title': 'x', 'phone': '+998901234567'})

    def test_create_lead_requires_provider_identifier(self):
        adapter = self.build(transport=RecordingTransport({'ok': True}))
        with self.assertRaises(Conflict):
            adapter.create_lead({'title': 'Yangi lid', 'phone': '+998901234567'})

    def test_create_lead_uses_declared_body_shape_only(self):
        transport = RecordingTransport({'id': 'L-1'})
        adapter = self.build(transport=transport)
        adapter.create_lead({'title': 'Yangi lid', 'phone': '+998901234567',
                             'comments': 'Ertaga qo‘ng‘iroq'})
        body = transport.calls[0]['body']
        self.assertEqual({'full_name', 'tel', 'note'}, set(body))
        self.assertEqual('+998901234567', body['tel'])
        self.assertEqual('POST', transport.calls[0]['method'])
        self.assertEqual('https://crm.example.uz/api/v1/leads', transport.calls[0]['url'])

    def test_undeclared_body_pointer_is_rejected(self):
        operations = dict(OPERATIONS)
        operations['create_lead'] = {'method': 'POST', 'path': '/leads',
                                     'body': {'evil': 'not_a_real_field'}}
        adapter = self.build(operations=operations)
        with self.assertRaises(ValueError):
            adapter.create_lead({'title': 'Yangi lid', 'phone': '+998901234567'})

    def test_response_map_traverses_nested_fields(self):
        payload = {'data': [{'id': 'L-9', 'title': 'Anvar aka',
                             'contacts': {'phone': '+998901234567', 'email': 'A@Example.uz'},
                             'stage': 'won'}]}
        adapter = self.build(transport=RecordingTransport(payload))
        leads = adapter.find_leads(query='Anvar')
        self.assertEqual('L-9', leads[0]['id'])
        self.assertEqual('+998901234567', leads[0]['phone'])
        self.assertEqual('a@example.uz', leads[0]['email'])
        self.assertEqual('won', leads[0]['status'])

    def test_missing_mapped_field_stays_absent(self):
        adapter = self.build(transport=RecordingTransport({'data': [{'id': 'L-1'}]}))
        leads = adapter.find_leads(query='x')
        self.assertEqual('', leads[0]['title'])
        self.assertEqual('', leads[0]['phone'])
        self.assertEqual('', leads[0]['status'])

    def test_bare_array_response_is_accepted(self):
        adapter = self.build(transport=RecordingTransport([{'id': 'L-2', 'title': 'Direct'}]))
        leads = adapter.find_leads(query='x')
        self.assertEqual('L-2', leads[0]['id'])

    def test_limit_is_clamped_to_contract_maximum(self):
        transport = RecordingTransport({'data': []})
        adapter = self.build(transport=transport)
        adapter.find_leads(query='x', limit=9999)
        self.assertIn('limit=50', transport.calls[0]['url'])

    def test_lead_id_placeholder_is_encoded(self):
        transport = RecordingTransport({'id': 'N-1'})
        adapter = self.build(transport=transport)
        adapter.attach_message('L/1', {'channel': 'telegram', 'direction': 'inbound', 'text': 'Salom'})
        self.assertEqual('https://crm.example.uz/api/v1/leads/L%2F1/notes', transport.calls[0]['url'])
        # Channel and direction are labelled the same way as the packaged adapters,
        # so a timeline entry reads identically regardless of the CRM behind it.
        self.assertEqual({'comment': '[TELEGRAM] Mijoz:\nSalom'}, transport.calls[0]['body'])

    def test_call_record_reuses_message_timeline_when_not_declared(self):
        transport = RecordingTransport({'id': 'N-2'})
        adapter = self.build(transport=transport)
        adapter.attach_call_record('L-3', {'direction': 'inbound', 'transcript': 'Salom',
                                           'duration_seconds': 12, 'sentiment': 'positive',
                                           'summary': 'Narx so‘radi'})
        self.assertIn('/leads/L-3/notes', transport.calls[0]['url'])
        self.assertIn('Transkript', transport.calls[0]['body']['comment'])

    def test_stalled_feed_drops_rows_without_identifier(self):
        payload = {'data': [{'id': 'L-1', 'title': 'actionable'}, {'title': 'no identifier'}]}
        adapter = self.build(transport=RecordingTransport(payload))
        leads = adapter.find_stalled_leads(inactive_minutes=90)
        self.assertEqual(['L-1'], [lead['id'] for lead in leads])

    def test_timeout_bounds_enforced(self):
        with patch.dict(os.environ, {'CUSTOM_CRM_TOKEN': 'secret-token'}):
            for bad in [0, 61, 'ten', True]:
                with self.subTest(timeout=bad), self.assertRaises(ValueError):
                    self.adapter(timeout_seconds=bad)

    def test_attach_path_must_address_the_lead(self):
        # Without {lead_id} every note would be posted to one endpoint and attached
        # to the wrong record, so the configuration is refused rather than trusted.
        operations = dict(OPERATIONS)
        operations['attach_message'] = {'method': 'POST', 'path': '/notes'}
        with self.assertRaises(ValueError):
            self.build(operations=operations)


class DeclarativeConfigTests(unittest.TestCase):
    """The config validator is the gate: a bad declaration must never reach a socket."""

    def test_path_traversal_variants_rejected(self):
        for bad in ['../admin', '/a/../../b', '/a//b', '/a\\b', 'a/b', '//host/path',
                    'https://evil.example.com/x', '/a:b', '/a b', '/a#f']:
            with self.subTest(path=bad), self.assertRaises(ValueError):
                safe_relative_path(bad, 'path')

    def test_placeholder_allowlist_enforced(self):
        with self.assertRaises(ValueError):
            safe_relative_path('/x/{host}/y', 'path')
        with self.assertRaises(ValueError):
            safe_relative_path('/x/{query', 'path')
        self.assertEqual('/x/{query}', safe_relative_path('/x/{query}', 'path'))

    def test_operation_method_allowlist(self):
        for method in ['DELETE', 'TRACE', 'CONNECT', 'OPTIONS']:
            with self.subTest(method=method), self.assertRaises(ValueError):
                validate_custom_http_config(
                    {'operations': {'find_leads': {'method': method, 'path': '/leads'}}})

    def test_operations_map_is_mandatory(self):
        for bad in [{}, {'operations': {}}, {'operations': []}, {}]:
            with self.subTest(config=bad), self.assertRaises(ValueError):
                validate_custom_http_config(bad)

    def test_unknown_operation_name_rejected(self):
        with self.assertRaises(ValueError):
            validate_custom_http_config({'operations': {'delete_everything': {'method': 'POST',
                                                                             'path': '/x'}}})

    def test_base_path_traversal_rejected(self):
        for bad in ['../admin', 'https://evil.example.com', '//evil.example.com', '/a\\b']:
            with self.subTest(base_path=bad), self.assertRaises(ValueError):
                validate_custom_http_config({'base_path': bad,
                                            'operations': {'find_leads': {'method': 'GET',
                                                                          'path': '/leads'}}})

    def test_valid_config_is_normalised(self):
        declared = validate_custom_http_config({
            'base_path': '/api/v1/',
            'operations': {'find_leads': {'method': 'get', 'path': '/leads?q={query}'}},
            'timeout_seconds': 10,
        })
        self.assertEqual('/api/v1', declared['base_path'])
        self.assertEqual('GET', declared['operations']['find_leads']['method'])


class CustomHTTPTransportTests(unittest.TestCase):
    def test_transport_rejects_non_https_or_credential_bearing_url(self):
        for url in ['http://crm.example.uz/x', 'ftp://crm.example.uz/x',
                    'https://user:pw@crm.example.uz/x', 'https://crm.example.uz/x#frag']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                default_custom_transport(url)

    def test_http_error_is_sanitized(self):
        import io
        import urllib.error
        import urllib.request
        error = urllib.error.HTTPError('https://crm.example.uz/x', 502, 'Bad Gateway', {},
                                       io.BytesIO(b'upstream trace'))
        self.addCleanup(error.close)
        with patch.object(urllib.request.OpenerDirector, 'open', side_effect=error):
            with self.assertRaises(CustomHTTPError) as raised:
                default_custom_transport('https://crm.example.uz/x')
        message = str(raised.exception)
        self.assertIn('502', message)
        self.assertNotIn('Bad Gateway', message)
        self.assertNotIn('upstream trace', message)


if __name__ == '__main__':
    unittest.main()