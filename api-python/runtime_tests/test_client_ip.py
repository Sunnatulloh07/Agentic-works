"""Per-IP throttle key: proxy-aware, and not spoofable through X-Forwarded-For.

Behind a reverse proxy every request arrives from the proxy's address, so a
throttle keyed on the raw peer puts every client in ONE bucket and a single
attacker locks everybody out. Trusting X-Forwarded-For blindly is the opposite
failure: the client writes that header, so it would pick its own bucket per
request. The rule measured here: only when the direct peer is a declared trusted
proxy (``TRUSTED_PROXIES``) is the header read, and then only the right-most
address that is not itself a trusted proxy counts.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import client_ip
from app import identity_store as store
from app.storage import reset

PROXIES = client_ip.parse_trusted_proxies('10.0.0.0/8, 192.168.1.10, fd00::/8')


class ClientAddressTests(unittest.TestCase):
    def key(self, peer, *forwarded, networks=PROXIES):
        return client_ip.client_address(peer, list(forwarded), networks)

    def test_without_trusted_proxies_the_header_is_ignored(self):
        self.assertEqual('203.0.113.7', self.key('203.0.113.7', '198.51.100.1', networks=()))
        self.assertEqual((), client_ip.parse_trusted_proxies(''))

    def test_an_untrusted_peer_cannot_choose_its_bucket(self):
        self.assertEqual('203.0.113.7', self.key('203.0.113.7', '198.51.100.1'))

    def test_a_trusted_peer_yields_the_rightmost_untrusted_hop(self):
        # The left-most entry is whatever the client wrote; the right-most one was
        # appended by our own proxy, so it is the one that cannot be forged.
        self.assertEqual('203.0.113.7', self.key('10.0.0.1', '6.6.6.6, 203.0.113.7'))

    def test_a_chain_of_trusted_proxies_is_walked_from_the_right(self):
        self.assertEqual('203.0.113.7',
                         self.key('10.0.0.1', '6.6.6.6, 203.0.113.7, 192.168.1.10, 10.2.3.4'))

    def test_several_header_lines_are_one_list(self):
        self.assertEqual('203.0.113.7', self.key('10.0.0.1', '6.6.6.6', '203.0.113.7'))

    def test_a_trusted_peer_without_the_header_is_the_key(self):
        self.assertEqual('10.0.0.1', self.key('10.0.0.1'))
        self.assertEqual('10.0.0.1', self.key('10.0.0.1', '  ,  '))

    def test_when_every_hop_is_a_proxy_the_leftmost_is_the_client(self):
        self.assertEqual('10.9.9.9', self.key('10.0.0.1', '10.9.9.9, 10.0.0.2'))

    def test_two_clients_behind_one_proxy_get_two_keys(self):
        self.assertNotEqual(self.key('10.0.0.1', '203.0.113.7'),
                            self.key('10.0.0.1', '203.0.113.8'))

    def test_ipv6_ports_brackets_and_mapped_addresses_are_normalised(self):
        self.assertEqual('2001:db8::1', self.key('fd00::1', '2001:db8::1'))
        self.assertEqual('2001:db8::1', self.key('10.0.0.1', '[2001:db8::1]:4711'))
        self.assertEqual('203.0.113.7', self.key('10.0.0.1', '203.0.113.7:4711'))
        self.assertEqual('203.0.113.7', self.key('::ffff:203.0.113.7'))
        self.assertEqual('203.0.113.7', self.key('::ffff:10.0.0.1', '203.0.113.7'))

    def test_a_garbage_hop_is_bounded_not_trusted(self):
        key = self.key('10.0.0.1', 'x' * 500)
        self.assertEqual('x' * client_ip.MAX_KEY_CHARS, key)

    def test_a_non_ip_peer_is_used_verbatim_and_bounded(self):
        self.assertEqual('testclient', self.key('testclient', '203.0.113.7'))
        self.assertEqual('unknown', self.key('', '203.0.113.7'))

    def test_an_invalid_trusted_proxy_entry_is_refused_by_name(self):
        with self.assertRaises(client_ip.ProxyConfigError) as caught:
            client_ip.parse_trusted_proxies('10.0.0.0/8, not-an-ip')
        self.assertIn('not-an-ip', str(caught.exception))

    def test_the_environment_variable_is_read(self):
        with patch.dict(os.environ, {'TRUSTED_PROXIES': '10.0.0.0/8'}):
            self.assertEqual('203.0.113.7', client_ip.client_address('10.0.0.1', ['203.0.113.7']))
        with patch.dict(os.environ, {'TRUSTED_PROXIES': ''}):
            self.assertEqual('10.0.0.1', client_ip.client_address('10.0.0.1', ['203.0.113.7']))

    def test_a_bad_proxy_list_stops_the_process_at_startup(self):
        """Refused at config time, not as a 500 on the first login."""
        from app.config import ConfigError, validate_runtime_config
        base = {'ENV': 'test', 'ALLOW_INSECURE_DEV': 'true'}
        with self.assertRaises(ConfigError) as caught:
            validate_runtime_config({**base, 'TRUSTED_PROXIES': '10.0.0.0/8,nope'})
        self.assertIn('TRUSTED_PROXIES', str(caught.exception))
        self.assertFalse(validate_runtime_config(
            {**base, 'TRUSTED_PROXIES': '10.0.0.0/8'}).production)

    def test_a_long_header_is_bounded(self):
        hops = ', '.join(['10.0.0.9'] * 5000 + ['203.0.113.7'])
        self.assertEqual('203.0.113.7', self.key('10.0.0.1', hops))


class AccountThrottleTests(unittest.TestCase):
    """The per-account half: many IPs cannot share out one account's guesses."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'APP_DB': str(Path(self.tmp.name) / 'db.sqlite'),
                                           'ENV': 'production', 'IDENTITY_DIRECTORY': 'true'})
        self.env.start()
        reset()

    def tearDown(self):
        reset()
        self.env.stop()
        self.tmp.cleanup()

    def test_login_is_throttled_per_account_independently_of_the_address(self):
        with patch.object(store.time, 'time', lambda: 900 * 40000):
            for _ in range(store.THROTTLE_LIMIT):
                with self.assertRaises(store.AuthenticationError):
                    store.authenticate('victim@example.com', 'wrong password!!')
            with self.assertRaises(store.AuthRateLimited):
                store.authenticate('VICTIM@example.com', 'wrong password!!')
            with self.assertRaises(store.AuthenticationError):
                store.authenticate('other@example.com', 'wrong password!!')


if __name__ == '__main__':
    unittest.main()
