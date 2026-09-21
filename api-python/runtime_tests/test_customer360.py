import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.customer360 import CustomerError, CustomerNotFound, add_contact, add_order, create_customer, get_customer, link_channel_identity, list_customers
from app import identity_store as identity
from app.storage import reset


class Customer360Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'APP_DB': str(Path(self.tmp.name) / 'app.db'), 'ENV':'production', 'IDENTITY_DIRECTORY':'true'})
        self.env.start()
        reset()
        self.owner=identity.register_user('owner@example.com','fixture password 123','Owner')
        for tenant in ('tenant-a','tenant-b','tt'):
            identity.create_workspace(self.owner['id'],tenant,tenant)

    def tearDown(self):
        reset()
        self.env.stop()
        self.tmp.cleanup()

    def test_customer_360_is_tenant_scoped(self):
        a = create_customer('tenant-a', 'Ali', actor=self.owner['id'])
        b = create_customer('tenant-b', 'Ali', actor=self.owner['id'])
        add_contact('tenant-a', a['id'], 'phone', '+998 90 123 45 67', verified=True, actor=self.owner['id'])
        link_channel_identity('tenant-a', a['id'], 'telegram', 'chat-1', verified=True, actor=self.owner['id'])
        add_order('tenant-a', a['id'], 'order-1', total_minor=125000, actor=self.owner['id'])
        detail = get_customer('tenant-a', a['id'])
        self.assertEqual('Ali', detail['display_name'])
        self.assertEqual(1, len(detail['contacts']))
        self.assertEqual(1, len(detail['channel_identities']))
        self.assertEqual(1, len(detail['orders']))
        with self.assertRaises(CustomerNotFound):
            get_customer('tenant-b', a['id'])
        self.assertEqual([b['id']], [x['id'] for x in list_customers('tenant-b')])

    def test_channel_identity_never_cross_customer_merges(self):
        a = create_customer('tt', 'A', actor=self.owner['id'])
        b = create_customer('tt', 'B', actor=self.owner['id'])
        link_channel_identity('tt', a['id'], 'telegram', 'same-chat', verified=True, actor=self.owner['id'])
        with self.assertRaises(CustomerError):
            link_channel_identity('tt', b['id'], 'telegram', 'same-chat', verified=True, actor=self.owner['id'])

    def test_channel_identity_requires_explicit_verification(self):
        a = create_customer('tt', 'A', actor=self.owner['id'])
        with self.assertRaises(CustomerError):
            link_channel_identity('tt', a['id'], 'telegram', 'chat', verified=False, actor=self.owner['id'])

    def test_query_is_not_sql(self):
        create_customer('tt', 'Ali', actor=self.owner['id'])
        create_customer('tt', 'Vali', actor=self.owner['id'])
        result = list_customers('tt', query="' OR 1=1 --")
        self.assertEqual([], result)

    def test_duplicate_external_order_is_idempotent_update(self):
        a = create_customer('tt', 'A', actor=self.owner['id'])
        add_order('tt', a['id'], 'o-1', total_minor=100, actor=self.owner['id'])
        add_order('tt', a['id'], 'o-1', status='paid', total_minor=200, actor=self.owner['id'])
        self.assertEqual(1, len(get_customer('tt', a['id'])['orders']))
        self.assertEqual('paid', get_customer('tt', a['id'])['orders'][0]['status'])


if __name__ == '__main__':
    unittest.main()
