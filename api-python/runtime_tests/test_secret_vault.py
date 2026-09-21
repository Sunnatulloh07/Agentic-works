import json
import os
import unittest
from unittest import mock

import platform_runtime.secret_vault as vault_module
from platform_runtime.engine import encode
from platform_runtime.secret_vault import (
    AAD_FORMAT, ENVELOPE_VERSION, KEY_BYTES, MAX_CONTEXT_BYTES, MAX_ENCODED_FIELD,
    MAX_ENVELOPE_CHARS, MAX_KEY_RING, MAX_SEALED_BYTES, MIN_KEY_RING, NONCE_BYTES,
    SecretVault, VaultError, _b64, _unb64)


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


def _envelope_of_raw_bytes(key, kid, size, context):
    """A well-formed envelope whose plaintext is exactly ``size`` bytes.

    ``seal`` refuses anything above its own cap, so an oversized envelope can only
    arrive from a peer that does not share this module's limits -- which is precisely
    what ``open``'s own cap defends against.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(NONCE_BYTES)
    aad = encode({'format': AAD_FORMAT, 'kid': kid, 'context': context}).encode('utf-8')
    raw = encode('x' * (size - 2)).encode('utf-8')
    ciphertext = AESGCM(key).encrypt(nonce, raw, aad)
    return encode({'v': ENVELOPE_VERSION, 'kid': kid,
                   'nonce': _b64(nonce), 'ciphertext': _b64(ciphertext)})


class DeclaredBoundTests(unittest.TestCase):
    """One test per declared bound, each measured AT the bound, not near it.

    The revert matrix widened all of these and the suite stayed green: the values were
    enforced but nothing pinned them. This class is what turns each one RED. Every
    assertion states the bound's own number rather than the constant's name, so
    widening a constant cannot move the test along with it.
    """

    def setUp(self):
        self.key = os.urandom(KEY_BYTES)
        self.vault = SecretVault({'v1': self.key}, 'v1')
        self.ctx = {'tenant': 'a', 'account': 'id'}

    # -- the numbers themselves ------------------------------------------------

    def test_declared_values_are_the_audited_ones(self):
        self.assertEqual(MIN_KEY_RING, 1)
        self.assertEqual(MAX_KEY_RING, 8)
        self.assertEqual(KEY_BYTES, 32)
        self.assertEqual(MAX_CONTEXT_BYTES, 4096)
        self.assertEqual(MAX_SEALED_BYTES, 64000)
        self.assertEqual(MAX_ENVELOPE_CHARS, 90000)
        self.assertEqual(MAX_ENCODED_FIELD, 150000)
        self.assertEqual(NONCE_BYTES, 12)
        self.assertEqual(ENVELOPE_VERSION, 1)
        self.assertEqual(AAD_FORMAT, 1)

    # -- key ring --------------------------------------------------------------

    def test_key_ring_ceiling_is_eight(self):
        keys = {f'k{i}': os.urandom(KEY_BYTES) for i in range(9)}
        with self.assertRaises(VaultError):
            SecretVault(keys, 'k0')
        SecretVault(dict(list(keys.items())[:8]), 'k0')

    def test_key_id_ceiling_is_sixty_four(self):
        SecretVault({'k' * 64: self.key}, 'k' * 64)
        with self.assertRaises(VaultError):
            SecretVault({'k' * 65: self.key}, 'k' * 65)

    # -- context ---------------------------------------------------------------

    def test_context_ceiling_is_4096_encoded_bytes(self):
        roomy = {'blob': 'x' * (MAX_CONTEXT_BYTES - 64)}
        self.assertLessEqual(len(encode(roomy)), MAX_CONTEXT_BYTES)
        self.vault.open(self.vault.seal({'a': 1}, roomy), roomy)
        over = {'blob': 'x' * MAX_CONTEXT_BYTES}
        self.assertGreater(len(encode(over)), MAX_CONTEXT_BYTES)
        with self.assertRaises(VaultError):
            self.vault.seal({'a': 1}, over)

    def test_empty_context_is_refused(self):
        with self.assertRaises(VaultError):
            self.vault.seal({'a': 1}, {})

    # -- envelope --------------------------------------------------------------

    def test_envelope_ceiling_is_checked_before_parsing(self):
        """No well-formed envelope reaches this bound, so its refusal cannot be told
        apart from a parse failure by outcome alone -- the cap exists to keep
        ``json.loads`` away from a huge string. The mechanism is asserted instead.
        Measured: the largest envelope a maximum payload can produce is 85 478 chars.
        """
        with mock.patch.object(vault_module, 'json') as fake:
            with self.assertRaises(VaultError):
                self.vault.open('x' * (MAX_ENVELOPE_CHARS + 1), self.ctx)
            fake.loads.assert_not_called()

    def test_envelope_field_set_is_exact(self):
        data = json.loads(self.vault.seal({'a': 1}, self.ctx))
        data['extra'] = 1
        with self.assertRaises(VaultError):
            self.vault.open(json.dumps(data), self.ctx)

    def test_opened_payload_ceiling_is_enforced_independently_of_seal(self):
        """``seal`` refuses an oversized payload, so this bound is only reachable by an
        envelope this module did not produce. It is what stops a peer that does not
        share the limit from handing back a 64 001-byte object."""
        envelope = _envelope_of_raw_bytes(self.key, 'v1', MAX_SEALED_BYTES + 1, self.ctx)
        self.assertLessEqual(len(envelope), MAX_ENVELOPE_CHARS)
        with self.assertRaises(VaultError):
            self.vault.open(envelope, self.ctx)
        allowed = _envelope_of_raw_bytes(self.key, 'v1', MAX_SEALED_BYTES, self.ctx)
        self.assertEqual('x' * (MAX_SEALED_BYTES - 2), self.vault.open(allowed, self.ctx))

    # -- encoded field ---------------------------------------------------------

    def test_oversized_encoded_field_is_never_decoded(self):
        """``_unb64`` caps the field BEFORE decoding it, so an oversized field is
        refused without doing the base64 work.

        No caller reaches this bound: inside ``open`` the envelope cap (90 000) binds
        first, and an environment variable cannot be this long -- Windows caps one at
        32 767 chars and Linux at 131 072, both below 150 000. So it is pinned as the
        direct call it is, and that reachability is recorded rather than assumed.
        """
        with mock.patch.object(vault_module, 'base64') as fake:
            with self.assertRaises(VaultError):
                _unb64('A' * (MAX_ENCODED_FIELD + 1))
            fake.b64decode.assert_not_called()
        self.assertEqual(KEY_BYTES, len(_unb64(_b64(os.urandom(KEY_BYTES)))))

    # -- refusal messages ------------------------------------------------------

    def test_seal_names_the_callers_context_error(self):
        """A malformed context is the caller's own argument, not a crypto failure.
        Reported as "encryption failed" it sends the caller looking in the wrong
        place, and a matrix that widens the handler finds nothing wrong."""
        with self.assertRaises(VaultError) as error:
            self.vault.seal({'a': 1}, {})
        self.assertIn('context', str(error.exception))

    def test_open_does_not_reveal_key_id_membership(self):
        """``open`` must NOT name the context error the way ``seal`` does. A distinct
        message would separate "unknown key id" from "known key id, bad context", and
        so tell an attacker which key ids this vault holds. Every refusal is asserted
        to produce the SAME message."""
        envelope = self.vault.seal({'a': 1}, self.ctx)
        messages = []
        for bad in [{}, {'tenant': 'a'}, {'blob': 'x' * MAX_CONTEXT_BYTES}]:
            with self.assertRaises(VaultError) as error:
                self.vault.open(envelope, bad)
            messages.append(str(error.exception))
        unknown = json.loads(envelope)
        unknown['kid'] = 'nope'
        with self.assertRaises(VaultError) as error:
            self.vault.open(json.dumps(unknown), self.ctx)
        messages.append(str(error.exception))
        self.assertEqual(1, len(set(messages)))
        self.assertNotIn('context', messages[0])
