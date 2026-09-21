import json
import os
import unittest
from platform_runtime.secret_vault import SecretVault, VaultError


class SecretVaultTests(unittest.TestCase):
    def setUp(self):
        self.key = os.urandom(32)
        self.vault = SecretVault({'v1': self.key}, 'v1')
        self.ctx = {'tenant': 'a', 'account': 'id', 'generation': 1, 'purpose': 'oauth'}
        self.value = {'access_token': 'synthetic-only', 'refresh_token': 'fake-refresh'}

    def test_roundtrip(self):
        self.assertEqual(self.value, self.vault.open(self.vault.seal(self.value, self.ctx), self.ctx))

    def test_random_nonce_and_no_plaintext(self):
        a = self.vault.seal(self.value, self.ctx); b = self.vault.seal(self.value, self.ctx)
        self.assertNotEqual(a, b); self.assertNotIn('synthetic-only', a)

    def test_every_binding_field_is_authenticated(self):
        envelope = self.vault.seal(self.value, self.ctx)
        for field in self.ctx:
            with self.subTest(field=field), self.assertRaises(VaultError):
                self.vault.open(envelope, {**self.ctx, field: 'different'})

    def test_wrong_key_denied(self):
        envelope = self.vault.seal(self.value, self.ctx)
        with self.assertRaises(VaultError): SecretVault({'v1': os.urandom(32)}, 'v1').open(envelope, self.ctx)

    def test_ciphertext_tamper_denied(self):
        data = json.loads(self.vault.seal(self.value, self.ctx)); s = data['ciphertext']
        data['ciphertext'] = ('A' if s[0] != 'A' else 'B') + s[1:]
        with self.assertRaises(VaultError): self.vault.open(json.dumps(data), self.ctx)

    def test_key_id_is_authenticated(self):
        data = json.loads(self.vault.seal(self.value, self.ctx)); data['kid'] = 'unknown'
        with self.assertRaises(VaultError): self.vault.open(json.dumps(data), self.ctx)

    def test_rotation_keeps_old_key_readable(self):
        envelope = self.vault.seal(self.value, self.ctx)
        v = SecretVault({'v1': self.key, 'v2': os.urandom(32)}, 'v2')
        newer = v.rewrap(envelope, self.ctx)
        self.assertEqual('v2', json.loads(newer)['kid'])
        self.assertEqual(self.value, v.open(newer, self.ctx))
        with self.assertRaises(VaultError): self.vault.open(newer, self.ctx)

    def test_invalid_envelope_shapes(self):
        for envelope in ['', '{}', '[]', '{"v":1,"v":1}', 'x'*90001]:
            with self.subTest(size=len(envelope)), self.assertRaises(VaultError): self.vault.open(envelope, self.ctx)

    def test_no_weak_or_duplicate_keys(self):
        for keys, active in [({}, 'x'), ({'a': b'short'}, 'a'), ({'a': self.key}, 'b'),
                             ({'a': self.key, 'b': self.key}, 'b')]:
            with self.assertRaises(VaultError): SecretVault(keys, active)

    def test_bounded_payload(self):
        with self.assertRaises(VaultError): self.vault.seal({'token': 'x'*64001}, self.ctx)

    def test_errors_never_echo_payload(self):
        with self.assertRaises(VaultError) as error: self.vault.open('synthetic-only', self.ctx)
        self.assertNotIn('synthetic', str(error.exception))
