"""1C adapter contract tests. Fake transport; no live 1C instance is contacted.

These assert the boundaries that make 1C safe to expose to an agent: pinned host
and base path, credential only from the environment, percent-encoded placeholders,
operator-declared response mapping and no unverifiable write reported as success.
"""
import base64
import os
import unittest
from unittest.mock import patch

from platform_runtime.crm.crm_contract import ONEC_STATUS_MAP, build_path, dig
from platform_runtime.crm.onec_adapter import OneCAdapter, OneCHTTPError, default_onec_transport
from platform_runtime.engine import Conflict, Forbidden


BASE = {
    'driver': 'onec',
    'host': '1c.example.uz',
    'allowed_hosts': ['1c.example.uz'],
    'base_path': '/agent-platform/hs/leads',
    'auth': 'basic',
    'basic_auth_env': 'ONEC_BASIC',
    'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write', 'reconcile'],
    'agent_ids': ['sales_bot'],
    'response_map': {
        'items': 'result.rows',
        'id': 'Ref_Key',
        'title': 'Description',
        'phone': 'phone',
        'email': 'email',
        'status': 'Статус',
    },
}


class RecordingTransport:
    """Captures requests and replays a scripted response."""

    def __init__(self, response=None):
        self.response = {} if response is None else response
        self.calls = []

    def __call__(self, url, body=None, headers=None, method='GET', timeout=15):
        self.calls.append({'url': url, 'body': body, 'headers': headers,
                           'method': method, 'timeout': timeout})
        return self.response


class OneCAdapterTests(unittest.TestCase):
    def adapter(self, transport=None, **overrides):
        config = {**BASE, **overrides}
        return OneCAdapter(config, transport=transport or RecordingTransport())

    def test_basic_credential_is_encoded_from_environment(self):
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter()
        expected = 'Basic ' + base64.b64encode(b'robot:secret').decode('ascii')
        self.assertEqual(expected, adapter.headers['Authorization'])

    def test_bearer_mode_uses_token_reference(self):
        with patch.dict(os.environ, {'ONEC_TOKEN': 'token-value'}):
            adapter = self.adapter(auth='bearer', token_env='ONEC_TOKEN')
        self.assertEqual('Bearer token-value', adapter.headers['Authorization'])

    def test_credential_env_is_accepted_as_shared_reference(self):
        with patch.dict(os.environ, {'ONEC_CRED': 'robot:secret'}):
            adapter = self.adapter(basic_auth_env='', credential_env='ONEC_CRED')
        self.assertTrue(adapter.headers['Authorization'].startswith('Basic '))

    def test_missing_credential_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                self.adapter()

    def test_malformed_basic_credential_rejected(self):
        # A value without a colon cannot produce a valid Basic header.
        with patch.dict(os.environ, {'ONEC_BASIC': 'nocolonsecret'}):
            with self.assertRaises(ValueError):
                self.adapter()

    def test_unknown_auth_mode_rejected(self):
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            with self.assertRaises(ValueError):
                self.adapter(auth='digest')

    def test_host_must_be_allowlisted(self):
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            with self.assertRaises(Forbidden):
                self.adapter(host='evil.example.com')

    def test_request_url_is_host_and_base_path_pinned(self):
        transport = RecordingTransport({'result': {'rows': []}})
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=transport)
            adapter.find_leads(query='Ali', limit=5)
        self.assertEqual('https://1c.example.uz/agent-platform/hs/leads/search?q=Ali&limit=5',
                         transport.calls[0]['url'])
        self.assertEqual('GET', transport.calls[0]['method'])

    def test_base_path_traversal_rejected(self):
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            for bad in ['../admin', '/a/../../b', 'https://evil.example.com', '//evil.example.com',
                        '/a\\b', '/a:b', 'a/b']:
                with self.subTest(base_path=bad), self.assertRaises(ValueError):
                    self.adapter(base_path=bad)

    def test_query_is_percent_encoded_so_plan_cannot_retarget(self):
        transport = RecordingTransport({'result': {'rows': []}})
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=transport)
            adapter.find_leads(query='x&limit=9999 /../admin?#')
        url = transport.calls[0]['url']
        self.assertNotIn('limit=9999', url)
        self.assertNotIn('/../admin', url)
        self.assertTrue(url.startswith('https://1c.example.uz/agent-platform/hs/leads/search?q='))
        # The '?' inside the value must be encoded, not open a second query string.
        self.assertEqual(1, url.count('?'))

    def test_response_map_drives_field_extraction(self):
        payload = {'result': {'rows': [
            {'Ref_Key': 'A-1', 'Description': 'Anvar aka', 'phone': '+998901234567',
             'email': 'ANVAR@Example.UZ', 'Статус': 'ВРаботе'},
        ]}}
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=RecordingTransport(payload))
            leads = adapter.find_leads(query='Anvar')
        self.assertEqual(1, len(leads))
        self.assertEqual('A-1', leads[0]['id'])
        self.assertEqual('Anvar aka', leads[0]['title'])
        self.assertEqual('+998901234567', leads[0]['phone'])
        self.assertEqual('anvar@example.uz', leads[0]['email'])
        self.assertEqual('in_progress', leads[0]['status'])
        self.assertEqual('ВРаботе', leads[0]['provider_status'])

    def test_unknown_provider_status_is_not_invented(self):
        payload = {'result': {'rows': [{'Ref_Key': 'A-2', 'Статус': 'НаСогласовании'}]}}
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=RecordingTransport(payload))
            leads = adapter.find_leads(query='x')
        self.assertEqual('', leads[0]['status'])
        self.assertEqual('НаСогласовании', leads[0]['provider_status'])

    def test_every_mapped_status_is_canonical(self):
        self.assertEqual({'new', 'in_progress', 'won', 'lost'}, set(ONEC_STATUS_MAP.values()))

    def test_bare_array_response_is_accepted(self):
        payload = [{'Ref_Key': 'A-3', 'Description': 'Direct array'}]
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=RecordingTransport(payload))
            leads = adapter.find_leads(query='x')
        self.assertEqual('A-3', leads[0]['id'])

    def test_create_lead_requires_provider_identifier(self):
        # A 2xx without an identifier cannot prove which record was created.
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=RecordingTransport({'ok': True}))
            with self.assertRaises(Conflict):
                adapter.create_lead({'title': 'Yangi lid', 'phone': '+998901234567'})

    def test_create_lead_reports_mapped_identifier(self):
        transport = RecordingTransport({'Ref_Key': 'LEAD-9'})
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=transport)
            result = adapter.create_lead({'title': 'Yangi lid', 'phone': '+998901234567'})
        self.assertEqual('LEAD-9', result['lead_id'])
        self.assertEqual('POST', transport.calls[0]['method'])
        self.assertEqual('Новый', transport.calls[0]['body']['Статус'])

    def test_created_id_pointer_handles_wrapped_create_response(self):
        # 1C HTTP services commonly wrap the created document, so the operator can
        # point created_id at the envelope while list rows keep their own pointer.
        transport = RecordingTransport({'result': {'Ref_Key': 'LEAD-77'}})
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=transport, response_map={
                'items': 'result.rows', 'id': 'Ref_Key', 'created_id': 'result.Ref_Key'})
            result = adapter.create_lead({'title': 'Yangi lid', 'phone': '+998901234567'})
        self.assertEqual('LEAD-77', result['lead_id'])

    def test_create_deal_uses_reverse_status_map(self):
        transport = RecordingTransport({'Ref_Key': 'DEAL-1'})
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=transport)
            adapter.create_deal({'title': 'Shartnoma', 'price': 500000, 'stage': 'won'})
        self.assertEqual('Выигран', transport.calls[0]['body']['Статус'])

    def test_lead_id_placeholder_is_encoded_in_comment_path(self):
        transport = RecordingTransport({'result': {'id': 'C-1'}})
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=transport)
            adapter.attach_message('A/1', {'channel': 'telegram', 'direction': 'inbound', 'text': 'Salom'})
        self.assertIn('/leads/A%2F1/comment', transport.calls[0]['url'])

    def test_limit_is_clamped_to_contract_maximum(self):
        transport = RecordingTransport({'result': {'rows': []}})
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=transport)
            adapter.find_leads(query='x', limit=5000)
        self.assertIn('limit=50', transport.calls[0]['url'])

    def test_stalled_feed_drops_rows_without_identifier(self):
        payload = {'result': {'rows': [
            {'Ref_Key': 'A-1', 'Description': 'actionable'},
            {'Description': 'no identifier'},
        ]}}
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            adapter = self.adapter(transport=RecordingTransport(payload))
            leads = adapter.find_stalled_leads(inactive_minutes=120)
        self.assertEqual(['A-1'], [lead['id'] for lead in leads])

    def test_timeout_bounds_enforced(self):
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            for bad in [0, 61, 'ten', True]:
                with self.subTest(timeout=bad), self.assertRaises(ValueError):
                    self.adapter(timeout_seconds=bad)

    def test_undeclared_path_options_are_validated_too(self):
        # find_path/contacts_path/stalled_path/comment_path are operator config just
        # like base_path, so a traversal or scheme value must not reach build_path.
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            for key in ('find_path', 'contacts_path', 'stalled_path', 'comment_path'):
                for bad in ['../admin', '//evil.example.com/x', 'https://evil.example.com/x',
                            '/ok/{host}']:
                    with self.subTest(key=key, value=bad), self.assertRaises(ValueError):
                        self.adapter(**{key: bad})
                with self.subTest(key=key), self.assertRaises(ValueError):
                    self.adapter(**{key: 123})

    def test_comment_path_must_declare_lead_id_placeholder(self):
        # A path without {lead_id} would silently post every comment to one endpoint.
        with patch.dict(os.environ, {'ONEC_BASIC': 'robot:secret'}):
            with self.assertRaises(ValueError):
                self.adapter(comment_path='/leads/comment')


class OneCTransportTests(unittest.TestCase):
    def test_transport_rejects_non_https_or_credential_bearing_url(self):
        for url in ['http://1c.example.uz/x', 'ftp://1c.example.uz/x',
                    'https://user:pw@1c.example.uz/x', 'https://1c.example.uz/x#frag']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                default_onec_transport(url)

    def test_http_error_is_sanitized(self):
        import io
        import urllib.error
        import urllib.request
        error = urllib.error.HTTPError('https://1c.example.uz/x', 500, 'Server Error', {},
                                       io.BytesIO(b'internal detail'))
        self.addCleanup(error.close)
        with patch.object(urllib.request.OpenerDirector, 'open', side_effect=error):
            with self.assertRaises(OneCHTTPError) as raised:
                default_onec_transport('https://1c.example.uz/x')
        message = str(raised.exception)
        self.assertIn('500', message)
        self.assertNotIn('Server Error', message)
        self.assertNotIn('internal detail', message)


class ContractHelperTests(unittest.TestCase):
    def test_build_path_percent_encodes_every_placeholder(self):
        rendered = build_path('/search?q={query}', {'query': 'a b/c?d&e=f'})
        self.assertEqual('/search?q=a%20b%2Fc%3Fd%26e%3Df', rendered)

    def test_build_path_requires_every_placeholder(self):
        with self.assertRaises(ValueError):
            build_path('/x/{query}/{limit}', {'query': 'a'})

    def test_build_path_bounds_value_length(self):
        with self.assertRaises(ValueError):
            build_path('/x/{query}', {'query': 'a' * 513})

    def test_dig_traverses_objects_and_returns_none_when_absent(self):
        document = {'a': {'b': {'c': 7}}}
        self.assertEqual(7, dig(document, 'a.b.c'))
        self.assertIsNone(dig(document, 'a.missing.c'))
        self.assertIsNone(dig(document, 'a.b.c.d'))

    def test_dig_rejects_expression_like_pointers(self):
        for pointer in ['a[0]', 'a.*', 'a b', 'a..b', 'a.b.c.d.e', '']:
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                dig({'a': 1}, pointer)


if __name__ == '__main__':
    unittest.main()