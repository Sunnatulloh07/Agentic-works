import os
import unittest
from unittest.mock import patch
from platform_runtime.crm.kommo_adapter import KommoAdapter


class KommoAdapterTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            'driver': 'kommo',
            'host': 'mycompany.kommo.com',
            'allowed_hosts': ['mycompany.kommo.com'],
            'token_env': 'KOMMO_TEST_TOKEN',
            'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write'],
        }

    def test_create_complex_lead_payload(self):
        calls = []

        def mock_transport(url, body=None, headers=None, method='GET', timeout=15):
            calls.append({'url': url, 'body': body, 'headers': headers, 'method': method})
            return [{'id': 112233, 'contact_id': 445566}]

        with patch.dict(os.environ, {'KOMMO_TEST_TOKEN': 'kommo_access_token_xyz'}):
            adapter = KommoAdapter(self.config, transport=mock_transport)
            res = adapter.create_lead({
                'title': 'Yangi So‘rov: Kommo Lead',
                'name': 'Farxod',
                'phone': '+998901234567',
                'email': 'farxod@tashkent.uz',
                'price': 3000000,
            })
            self.assertEqual('kommo', res['provider'])
            self.assertEqual('112233', res['lead_id'])
            self.assertEqual('445566', res['contact_id'])
            self.assertTrue(res['created'])

        self.assertEqual(1, len(calls))
        c = calls[0]
        self.assertEqual('https://mycompany.kommo.com/api/v4/leads/complex', c['url'])
        self.assertEqual('POST', c['method'])
        self.assertEqual({'Authorization': 'Bearer kommo_access_token_xyz'}, c['headers'])
        
        lead_data = c['body'][0]
        self.assertEqual('Yangi So‘rov: Kommo Lead', lead_data['name'])
        self.assertEqual(3000000, lead_data['price'])
        contact = lead_data['_embedded']['contacts'][0]
        self.assertEqual('Farxod', contact['first_name'])
        
        # Check custom fields (PHONE and EMAIL)
        fields = {f['field_code']: f['values'][0]['value'] for f in contact['custom_fields_values']}
        self.assertEqual('+998901234567', fields['PHONE'])
        self.assertEqual('farxod@tashkent.uz', fields['EMAIL'])

    def test_get_lead(self):
        def mock_transport(url, body=None, headers=None, method='GET', timeout=15):
            self.assertIn('leads/112233', url)
            return {
                'id': 112233,
                'name': 'VIP Buyurtma',
                'price': 7500000,
                'status_id': 142,
                'pipeline_id': 12,
                'created_at': 1726740000,
            }

        with patch.dict(os.environ, {'KOMMO_TEST_TOKEN': 'token'}):
            adapter = KommoAdapter(self.config, transport=mock_transport)
            lead = adapter.get_lead('112233')
            self.assertEqual('kommo', lead['provider'])
            self.assertEqual('112233', lead['id'])
            self.assertEqual('VIP Buyurtma', lead['title'])
            self.assertEqual(7500000, lead['price'])
            self.assertEqual(142, lead['status_id'])

    def test_find_leads_query(self):
        def mock_transport(url, body=None, headers=None, method='GET', timeout=15):
            self.assertIn('query=%2B998901234567', url)
            return {
                '_embedded': {
                    'leads': [
                        {'id': 1, 'name': 'Lead 1', 'price': 100000, 'status_id': 10},
                        {'id': 2, 'name': 'Lead 2', 'price': 200000, 'status_id': 20},
                    ]
                }
            }

        with patch.dict(os.environ, {'KOMMO_TEST_TOKEN': 'token'}):
            adapter = KommoAdapter(self.config, transport=mock_transport)
            leads = adapter.find_leads(query='+998901234567', limit=10)
            self.assertEqual(2, len(leads))
            self.assertEqual('1', leads[0]['id'])
            self.assertEqual('Lead 1', leads[0]['title'])

    def test_find_contacts(self):
        def mock_transport(url, body=None, headers=None, method='GET', timeout=15):
            return {
                '_embedded': {
                    'contacts': [
                        {'id': 88, 'name': 'Dilshod'},
                    ]
                }
            }

        with patch.dict(os.environ, {'KOMMO_TEST_TOKEN': 'token'}):
            adapter = KommoAdapter(self.config, transport=mock_transport)
            contacts = adapter.find_contacts(query='Dilshod')
            self.assertEqual(1, len(contacts))
            self.assertEqual('88', contacts[0]['id'])
            self.assertEqual('Dilshod', contacts[0]['name'])

    def test_create_deal(self):
        def mock_transport(url, body=None, headers=None, method='GET', timeout=15):
            self.assertEqual('POST', method)
            return {
                '_embedded': {
                    'leads': [{'id': 999}]
                }
            }

        with patch.dict(os.environ, {'KOMMO_TEST_TOKEN': 'token'}):
            adapter = KommoAdapter(self.config, transport=mock_transport)
            deal = adapter.create_deal({
                'title': 'Katta Savdo',
                'price': 12000000,
            })
            self.assertEqual('999', deal['deal_id'])


if __name__ == '__main__':
    unittest.main()
