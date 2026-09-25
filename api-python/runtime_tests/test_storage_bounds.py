"""Declared bounds of the shared storage layer (app/storage.py).

The delivery queue had behaviour tests elsewhere but no *bounds* owner: the
connection timeout, the lease length and the error ceiling were inline numbers,
so widening one was silent. They are named now and pinned here, literally and
behaviourally, on a real SQLite file in a temporary APP_DB.

Two things this file deliberately does NOT cover, named so the gaps stay
visible: the identity schema is ``identity_schema.py``'s (its migration runs
through ``db()`` here only as far as "opening twice is idempotent"), and
``tx()``'s savepoint nesting is exercised by the modules that use it, not here.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from app import storage
from app.domain import Channel, InboundMessage
from app.domain_enums import DeliveryStatus


def message(key='k1', tenant='t', channel=Channel.TELEGRAM):
    return InboundMessage(tenant_id=tenant, channel=channel, external_key=key,
                          sender_id='555', conversation_id='chat-555', text='Salom',
                          correlation_id='c1')


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'APP_DB': os.path.join(self.tmp.name, 'app.db')})
        env.start()
        self.addCleanup(env.stop)
        storage.reset()
        self.addCleanup(storage.reset)


class DeclaredBoundTests(StorageTestCase):
    def test_connection_timeout(self):
        self.assertEqual(storage.CONNECT_TIMEOUT_SECONDS, 30.0)

    def test_delivery_lease(self):
        self.assertEqual(storage.DELIVERY_LEASE_SECONDS, 60)

    def test_error_ceiling(self):
        self.assertEqual(storage.MAX_DELIVERY_ERROR_CHARS, 500)

    def test_no_inline_literal_survives(self):
        source = open(storage.__file__, encoding='utf-8').read()
        self.assertIn('CONNECT_TIMEOUT_SECONDS = 30.0', source)
        self.assertIn('DELIVERY_LEASE_SECONDS = 60', source)
        self.assertIn('MAX_DELIVERY_ERROR_CHARS = 500', source)
        self.assertNotIn('timeout=30.0', source)
        self.assertNotIn('error[:500]', source)
        self.assertNotIn('lease_seconds: int = 60', source)


class DeliveryTests(StorageTestCase):
    def test_a_key_is_one_delivery_per_tenant(self):
        first = storage.create_delivery('t', 'order', 'o1', 'telegram', 'k1')
        self.assertEqual(first, storage.create_delivery('t', 'order', 'o2', 'telegram', 'k1'))
        self.assertNotEqual(first, storage.create_delivery('t', 'order', 'o3', 'telegram', 'k2'))
        self.assertNotEqual(first, storage.create_delivery('other', 'order', 'o4', 'telegram', 'k1'))

    def test_a_claim_is_exclusive_until_its_lease_expires(self):
        d = storage.create_delivery('t', 'order', 'o1', 'telegram', 'k1')
        self.assertTrue(storage.claim_delivery('t', d, 'w1', now=1000))
        self.assertFalse(storage.claim_delivery('t', d, 'w2', now=1000))
        self.assertTrue(storage.claim_delivery('t', d, 'w2', now=1000 + storage.DELIVERY_LEASE_SECONDS))
        self.assertFalse(storage.claim_delivery('t', d, 'w3', now=1000 + storage.DELIVERY_LEASE_SECONDS))

    def test_a_claim_is_scoped_to_the_tenant(self):
        d = storage.create_delivery('t', 'order', 'o1', 'telegram', 'k1')
        self.assertFalse(storage.claim_delivery('other', d, 'w1', now=1000))
        self.assertTrue(storage.claim_delivery('t', d, 'w1', now=1000))

    def test_an_empty_worker_is_refused(self):
        d = storage.create_delivery('t', 'order', 'o1', 'telegram', 'k1')
        with self.assertRaises(ValueError):
            storage.claim_delivery('t', d, '   ', now=1000)

    def test_a_lease_below_one_second_is_clamped_not_accepted(self):
        d = storage.create_delivery('t', 'order', 'o1', 'telegram', 'k1')
        self.assertTrue(storage.claim_delivery('t', d, 'w1', now=1000, lease_seconds=0))
        self.assertEqual(storage.get_delivery('t', d)['lease_until'], 1001)

    def test_the_lease_default_is_the_declared_one(self):
        d = storage.create_delivery('t', 'order', 'o1', 'telegram', 'k1')
        storage.claim_delivery('t', d, 'w1', now=1000)
        self.assertEqual(storage.get_delivery('t', d)['lease_until'],
                         1000 + storage.DELIVERY_LEASE_SECONDS)

    def test_update_settles_the_claim_and_counts_the_attempt(self):
        d = storage.create_delivery('t', 'order', 'o1', 'telegram', 'k1')
        storage.claim_delivery('t', d, 'w1', now=1000)
        self.assertTrue(storage.update_delivery('t', d, DeliveryStatus.SENT, external_id='x1'))
        row = storage.get_delivery('t', d)
        self.assertEqual((row['status'], row['external_id'], row['attempt_count'],
                          row['claimed_by'], row['lease_until']),
                         ('sent', 'x1', 1, '', 0))

    def test_the_error_ceiling_truncates(self):
        d = storage.create_delivery('t', 'order', 'o1', 'telegram', 'k1')
        storage.update_delivery('t', d, DeliveryStatus.FAILED, error='x' * 4000)
        self.assertEqual(len(storage.get_delivery('t', d)['error']),
                         storage.MAX_DELIVERY_ERROR_CHARS)

    def test_a_settled_delivery_cannot_be_claimed_again(self):
        d = storage.create_delivery('t', 'order', 'o1', 'telegram', 'k1')
        storage.update_delivery('t', d, DeliveryStatus.SENT)
        self.assertFalse(storage.claim_delivery('t', d, 'w1', now=1000))

    def test_update_of_another_tenants_delivery_is_refused(self):
        d = storage.create_delivery('t', 'order', 'o1', 'telegram', 'k1')
        self.assertFalse(storage.update_delivery('other', d, DeliveryStatus.SENT))

    def test_get_is_scoped_to_the_tenant(self):
        d = storage.create_delivery('t', 'order', 'o1', 'telegram', 'k1')
        self.assertIsNone(storage.get_delivery('other', d))
        self.assertIsNotNone(storage.get_delivery('t', d))


class InboundAndSchemaTests(StorageTestCase):
    def test_an_inbound_message_is_recorded_once(self):
        self.assertTrue(storage.record_inbound(message()))
        self.assertFalse(storage.record_inbound(message()))
        self.assertTrue(storage.record_inbound(message(key='k2')))

    def test_the_key_is_scoped_by_channel_and_tenant(self):
        self.assertTrue(storage.record_inbound(message()))
        self.assertTrue(storage.record_inbound(message(channel=Channel.WEB)))
        self.assertTrue(storage.record_inbound(message(tenant='other')))

    def test_opening_the_same_database_twice_is_idempotent(self):
        storage.db()
        storage.reset()
        storage.db()  # the DDL and the migration run again on an existing file
        self.assertTrue(storage.record_inbound(message()))


if __name__ == '__main__':
    unittest.main()

