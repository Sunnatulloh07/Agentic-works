"""Versioned AES-256-GCM envelopes, bound to a caller-supplied security context.

No custom cryptography, plaintext fallback, secret logging or key generation at
startup. Production operators inject a key ring; losing a key loses its data.
This is an encryption boundary, not a hosted KMS or secret-access policy.

Every limit below is named. An inline literal is invisible to a scan that looks for
constants, so the guard and the test that pins it cannot be found together; naming
them is what makes the revert matrix possible at all.
"""
import base64
import json
import os
import re
from .engine import encode

# Key ring. ``MIN_KEY_RING`` restates an intent that ``active in keys`` already
# enforces -- an empty ring cannot contain the active id -- and is kept for that
# reason alone; the probe records the redundancy rather than leaving it implied.
MIN_KEY_RING = 1
MAX_KEY_RING = 8
KEY_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
KEY_BYTES = 32

# Caller-supplied encryption context: a non-empty object, bounded in encoded bytes.
MAX_CONTEXT_BYTES = 4096

# Payload ceiling, applied on the way in (``seal``) and on the way out (``open``).
MAX_SEALED_BYTES = 64000

# Envelope ceilings. ``MAX_ENVELOPE_CHARS`` is tested before ``json.loads``, so it is
# a parse guard, not a payload bound; no well-formed envelope ever reaches it.
MAX_ENVELOPE_CHARS = 90000
MAX_ENCODED_FIELD = 150000

NONCE_BYTES = 12
ENVELOPE_FIELDS = frozenset({'v', 'kid', 'nonce', 'ciphertext'})
ENVELOPE_VERSION = 1
AAD_FORMAT = 1


class VaultError(RuntimeError):
    pass


def _b64(raw):
    return base64.b64encode(raw).decode('ascii')


def _unb64(text):
    if not isinstance(text, str) or len(text) > MAX_ENCODED_FIELD:
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
        if (not isinstance(keys, dict) or not MIN_KEY_RING <= len(keys) <= MAX_KEY_RING
                or active not in keys):
            raise VaultError('Configured encryption key ring required')
        if any(not isinstance(k, str) or not KEY_ID.fullmatch(k)
               or type(v) is not bytes or len(v) != KEY_BYTES for k, v in keys.items()):
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
        if (not isinstance(context, dict) or not context
                or len(encode(context)) > MAX_CONTEXT_BYTES):
            raise VaultError('Bounded encryption context required')
        return encode({'format': AAD_FORMAT, 'kid': kid, 'context': context}).encode('utf-8')

    def seal(self, value, context):
        """Encrypt ``value`` under the active key, bound to ``context``.

        A refusal caused by the caller's own context is re-raised as itself rather
        than folded into "encryption failed": the caller can fix their argument, and
        a crypto-shaped message sends them looking in the wrong place.
        """
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            raw = encode(value).encode('utf-8')
            if len(raw) > MAX_SEALED_BYTES:
                raise ValueError()
            nonce = os.urandom(NONCE_BYTES)
            ciphertext = AESGCM(self._keys[self.active]).encrypt(
                nonce, raw, self._aad(context, self.active))
            return encode({'v': ENVELOPE_VERSION, 'kid': self.active,
                           'nonce': _b64(nonce), 'ciphertext': _b64(ciphertext)})
        except VaultError:
            raise
        except Exception:
            raise VaultError('Secret encryption failed') from None

    def open(self, envelope, context):
        """Decrypt an envelope, or refuse with a single uninformative message.

        The message is deliberately identical for a malformed envelope, an unknown
        key id and a malformed context. Naming the malformed context would separate
        it from an unknown key id, which is an oracle for which key ids this vault
        holds. ``seal`` has no such exposure and does name the caller's own error.
        """
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            if not isinstance(envelope, str) or len(envelope) > MAX_ENVELOPE_CHARS:
                raise ValueError()
            data = json.loads(envelope, object_pairs_hook=_unique)
            if (set(data) != ENVELOPE_FIELDS or type(data['v']) is not int
                    or data['v'] != ENVELOPE_VERSION):
                raise ValueError()
            nonce = _unb64(data['nonce'])
            if len(nonce) != NONCE_BYTES or data['kid'] not in self._keys:
                raise ValueError()
            raw = AESGCM(self._keys[data['kid']]).decrypt(
                nonce, _unb64(data['ciphertext']), self._aad(context, data['kid']))
            if len(raw) > MAX_SEALED_BYTES:
                raise ValueError()
            return json.loads(raw, object_pairs_hook=_unique)
        except Exception:
            raise VaultError('Secret authentication failed') from None

    def rewrap(self, envelope, context):
        return self.seal(self.open(envelope, context), context)
