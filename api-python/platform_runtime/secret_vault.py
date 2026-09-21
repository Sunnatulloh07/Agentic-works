"""Versioned AES-256-GCM envelopes, bound to a caller-supplied security context.

No custom cryptography, plaintext fallback, secret logging or key generation at
startup. Production operators inject a key ring; losing a key loses its data.
This is an encryption boundary, not a hosted KMS or secret-access policy.
"""
import base64
import json
import os
import re
from .engine import encode


class VaultError(RuntimeError):
    pass


def _b64(raw):
    return base64.b64encode(raw).decode('ascii')


def _unb64(text):
    if not isinstance(text, str) or len(text) > 150000:
        raise VaultError('Invalid encrypted envelope')
    return base64.b64decode(text, validate=True)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise VaultError('Duplicate envelope field')
        result[key] = value
    return result


class SecretVault:
    def __init__(self, keys, active):
        if not isinstance(keys, dict) or not 1 <= len(keys) <= 8 or active not in keys:
            raise VaultError('Configured encryption key ring required')
        if any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', k)
               or type(v) is not bytes or len(v) != 32 for k, v in keys.items()):
            raise VaultError('AES-256 key ring invalid')
        # Duplicate key material under another ID would hide rotation mistakes.
        if len(set(keys.values())) != len(keys):
            raise VaultError('Duplicate encryption keys denied')
        self._keys = dict(keys)
        self.active = active

    @classmethod
    def from_environment(cls):
        """Read only fixed variable names, never expose configured values."""
        try:
            data = json.loads(os.environ.get('PLATFORM_VAULT_KEYS_JSON', ''), object_pairs_hook=_unique)
            active = os.environ.get('PLATFORM_VAULT_ACTIVE_KEY', '')
            if not isinstance(data, dict):
                raise ValueError()
            return cls({k: _unb64(v) for k, v in data.items()}, active)
        except Exception:
            raise VaultError('Encryption configuration unavailable') from None

    def _aad(self, context, kid):
        if not isinstance(context, dict) or not context or len(encode(context)) > 4096:
            raise VaultError('Bounded encryption context required')
        return encode({'format': 1, 'kid': kid, 'context': context}).encode('utf-8')

    def seal(self, value, context):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            raw = encode(value).encode('utf-8')
            if len(raw) > 64000:
                raise ValueError()
            nonce = os.urandom(12)
            ciphertext = AESGCM(self._keys[self.active]).encrypt(nonce, raw, self._aad(context, self.active))
            return encode({'v': 1, 'kid': self.active, 'nonce': _b64(nonce), 'ciphertext': _b64(ciphertext)})
        except Exception:
            raise VaultError('Secret encryption failed') from None

    def open(self, envelope, context):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            if not isinstance(envelope, str) or len(envelope) > 90000:
                raise ValueError()
            data = json.loads(envelope, object_pairs_hook=_unique)
            if set(data) != {'v', 'kid', 'nonce', 'ciphertext'} or type(data['v']) is not int or data['v'] != 1:
                raise ValueError()
            nonce = _unb64(data['nonce'])
            if len(nonce) != 12 or data['kid'] not in self._keys:
                raise ValueError()
            raw = AESGCM(self._keys[data['kid']]).decrypt(
                nonce, _unb64(data['ciphertext']), self._aad(context, data['kid']))
            if len(raw) > 64000:
                raise ValueError()
            return json.loads(raw, object_pairs_hook=_unique)
        except Exception:
            raise VaultError('Secret authentication failed') from None

    def rewrap(self, envelope, context):
        return self.seal(self.open(envelope, context), context)
