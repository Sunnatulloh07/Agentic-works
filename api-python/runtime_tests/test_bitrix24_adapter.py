import os
import unittest
from unittest.mock import patch
from platform_runtime.crm.bitrix24_adapter import Bitrix24Adapter, Bitrix24HTTPError
from platform_runtime.engine import Forbidden


class Bitrix24AdapterTests(unittest.TestCase):
    def setUp(self):
        self.config_webhook = {
            'driver': 'bitrix24',
            'host': 'mycrm.bitrix24.com',
            'allowed_hosts': ['mycrm.bitrix24.com'],
            'webhook_url_env': 'BITRIX24_TEST_WEBHOOK',
            'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write'],
        }
        self.config_oauth = {
            'driver': 'bitrix24',
            'host': 'mycrm.bitrix24.com',
            'allowed_hosts': ['mycrm.bitrix24.com'],
            'token_env': 'BITRIX24_TEST_TOKEN',
            'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write'],
        }

    def test_webhook_url_host_mismatch_rejected(self):
        with patch.dict(os.environ, {'BITRIX24_TEST_WEBHOOK': 'https://evil.bitrix24.com/rest/1/abc/'}):
            with self.assertRaises(Forbidden):
                Bitrix24Adapter(self.config_webhook)

    def test_create_lead_webhook(self):
        calls = []

        def mock_transport(url, body=None, headers=None, timeout=15):
            calls.append({'url': url, 'body': body, 'headers': headers})
            return {'result': 7788}

        with patch.dict(os.environ, {'BITRIX24_TEST_WEBHOOK': 'https://mycrm.bitrix24.com/rest/1/secret_code/'}):
            adapter = Bitrix24Adapter(self.config_webhook, transport=mock_transport)
            req = {
                'title': 'Telegram Lead #101',
                'name': 'Temur',
                'phone': '+998901234567',
                'email': 'temur@samarkand.uz',
                'price': 250000,
                'currency': 'UZS',
                'source': 'telegram',
                'comments': 'Katta buyurtma',
            }
            res = adapter.create_lead(req)
            self.assertEqual('bitrix24', res['provider'])
            self.assertEqual('7788', res['lead_id'])
            self.assertTrue(res['created'])

        self.assertEqual(1, len(calls))
        c = calls[0]
        self.assertEqual('https://mycrm.bitrix24.com/rest/1/secret_code/crm.lead.add.json', c['url'])
        self.assertEqual('Telegram Lead #101', c['body']['fields']['TITLE'])
        self.assertEqual('+998901234567', c['body']['fields']['PHONE'][0]['VALUE'])
        self.assertEqual('temur@samarkand.uz', c['body']['fields']['EMAIL'][0]['VALUE'])
        self.assertEqual(250000, c['body']['fields']['OPPORTUNITY'])

    def test_create_lead_oauth_bearer(self):
        calls = []

        def mock_transport(url, body=None, headers=None, timeout=15):
            calls.append({'url': url, 'body': body, 'headers': headers})
            return {'result': 9900}

        with patch.dict(os.environ, {'BITRIX24_TEST_TOKEN': 'oauth_access_token_123'}):
            adapter = Bitrix24Adapter(self.config_oauth, transport=mock_transport)
            res = adapter.create_lead({'title': 'OAuth Lead', 'phone': '+998901112233'})
            self.assertEqual('9900', res['lead_id'])

        self.assertEqual(1, len(calls))
        self.assertEqual({'Authorization': 'Bearer oauth_access_token_123'}, calls[0]['headers'])
        self.assertEqual('https://mycrm.bitrix24.com/rest/crm.lead.add.json', calls[0]['url'])

    def test_get_lead_status_mapping(self):
        def mock_transport(url, body=None, headers=None, timeout=15):
            return {
                'result': {
                    'ID': '1234',
                    'TITLE': 'Mijoz Zakaz',
                    'NAME': 'Zulayho',
                    'STATUS_ID': 'WON',
                    'OPPORTUNITY': '500000.00',
                    'CURRENCY_ID': 'UZS',
                    'PHONE': [{'VALUE': '+998909876543'}],
                    'EMAIL': [{'VALUE': 'zulayho@example.uz'}],
                    'DATE_CREATE': '2026-09-19T10:00:00Z',
                }
            }

        with patch.dict(os.environ, {'BITRIX24_TEST_TOKEN': 'token'}):
            adapter = Bitrix24Adapter(self.config_oauth, transport=mock_transport)
            lead = adapter.get_lead('1234')
            self.assertEqual('1234', lead['id'])
            self.assertEqual('won', lead['status'])
            self.assertEqual('WON', lead['raw_status'])
            self.assertEqual(500000, lead['price'])
            self.assertEqual('+998909876543', lead['phone'])
            self.assertEqual('zulayho@example.uz', lead['email'])

    def test_find_leads_by_phone(self):
        def mock_transport(url, body=None, headers=None, timeout=15):
            self.assertEqual('+998901112233', body['filter']['PHONE'])
            return {
                'result': [
                    {'ID': '1', 'TITLE': 'Lead 1', 'STATUS_ID': 'NEW', 'OPPORTUNITY': '100000', 'CURRENCY_ID': 'UZS'},
                    {'ID': '2', 'TITLE': 'Lead 2', 'STATUS_ID': 'IN_PROCESS', 'OPPORTUNITY': '200000', 'CURRENCY_ID': 'UZS'},
                ]
            }

        with patch.dict(os.environ, {'BITRIX24_TEST_TOKEN': 'token'}):
            adapter = Bitrix24Adapter(self.config_oauth, transport=mock_transport)
            results = adapter.find_leads(phone='+998 90 111-22-33')
            self.assertEqual(2, len(results))
            self.assertEqual('1', results[0]['id'])
            self.assertEqual('new', results[0]['status'])
            self.assertEqual('in_progress', results[1]['status'])

    def test_create_deal(self):
        calls = []

        def mock_transport(url, body=None, headers=None, timeout=15):
            calls.append(body)
            return {'result': 555}

        with patch.dict(os.environ, {'BITRIX24_TEST_TOKEN': 'token'}):
            adapter = Bitrix24Adapter(self.config_oauth, transport=mock_transport)
            deal = adapter.create_deal({
                'title': 'Bitim #1',
                'price': 10000000,
                'currency': 'UZS',
                'stage': 'won',
                'contact_id': 'contact_12',
            })
            self.assertEqual('555', deal['deal_id'])

        self.assertEqual(1, len(calls))
        self.assertEqual('WON', calls[0]['fields']['STAGE_ID'])
        self.assertEqual('contact_12', calls[0]['fields']['CONTACT_ID'])

    def test_api_error_handling(self):
        def mock_transport(url, body=None, headers=None, timeout=15):
            return {'error': 'ERROR_CORE', 'error_description': 'Access denied'}

        with patch.dict(os.environ, {'BITRIX24_TEST_TOKEN': 'token'}):
            adapter = Bitrix24Adapter(self.config_oauth, transport=mock_transport)
            with self.assertRaises(Bitrix24HTTPError) as ctx:
                adapter.create_lead({'title': 'Fail Lead', 'phone': '+998901112233'})
            self.assertIn('Access denied', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
