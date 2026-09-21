"""Boundary audit of ``platform_runtime/secret_vault.py`` -- the revert matrix.

The vault is where a credential is stored, so every bound here is either a limit on
what may be encrypted or a limit on what will be accepted as ciphertext. The scan
selected it for the same mechanical reason as the two modules before it: large
literals and no named constants.

Every bound is now a named constant, so a mutation targets the constant's own value.
That is deliberate: mutating ``MAX_KEY_RING = 8`` to ``9`` moves the guard, and the
test that pins it asserts the number 8 rather than the name, so the two cannot drift
together.

Enumerated bounds, and whether anything was checking them:

| Bound | Value | Site | First matrix |
|---|---|---|---|
| key ring floor | 1 | ``SecretVault.__init__`` | GREEN |
| key ring ceiling | 8 | ``SecretVault.__init__`` | GREEN |
| key id shape | ``[A-Za-z0-9_-]{1,64}`` | key ring validation | GREEN |
| key material | exactly 32 bytes | key ring validation | RED |
| duplicate key material | refused | key ring validation | RED |
| encryption context | non-empty, 4096 bytes | ``_aad`` | GREEN |
| sealed payload | 64 000 bytes | ``seal`` | RED |
| opened payload | 64 000 bytes | ``open`` | GREEN |
| envelope | 90 000 chars | ``open`` | GREEN |
| envelope fields | exactly four, ``v == 1`` | ``open`` | GREEN |
| nonce | exactly 12 bytes | ``open`` | RED |
| encoded field | 150 000 chars | ``_unb64`` | GREEN |

Two refusals are not bounds but the mis-reporting this phase fixed: a refusal caused
by the CALLER's own argument was reported as a crypto failure. A fix nobody pins can
be reverted silently, so they are measured like everything else -- including the
asymmetry that ``open`` must NOT name its context error, because a distinct message
would reveal whether a key id is configured.

Three of the greens are not defects and are recorded as measurements:

* ``opened payload`` -- only reachable by an envelope this module did not produce,
  because ``seal`` refuses the same size first. Pinned with a hand-built envelope.
* ``envelope 90 000`` -- no well-formed envelope reaches it (the largest possible is
  85 478 chars), so its refusal is indistinguishable from a parse failure. Pinned by
  asserting the mechanism: ``json.loads`` is never reached.
* ``encoded field 150 000`` -- no caller reaches it on any platform (``open`` caps the
  envelope at 90 000 first; Windows caps an environment variable at 32 767 and Linux
  at 131 072). Pinned as the direct call it is.

The measured spec is two files: ``test_secret_vault.py`` owns the vault's own
contract, and ``test_oauth.py`` is its largest consumer, so a bound that only shows
up through the token store is still measured. A single-file spec would report such a
bound GREEN merely because its test lives next door.

Run from the repository root::

    python scripts/audit_vault_bounds.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

TARGET = 'platform_runtime/secret_vault.py'
PATTERN = 'runtime_tests.test_secret_vault runtime_tests.test_oauth'

MUTATIONS = [
    ('key ring floor 1',
     b'MIN_KEY_RING = 1',
     b'MIN_KEY_RING = 0'),
    ('key ring ceiling 8',
     b'MAX_KEY_RING = 8',
     b'MAX_KEY_RING = 9'),
    ('key id ceiling 64',
     b"r'[A-Za-z0-9_-]{1,64}'",
     b"r'[A-Za-z0-9_-]{1,65}'"),
    ('key material 32 bytes',
     b'KEY_BYTES = 32',
     b'KEY_BYTES = 33'),
    ('duplicate key material',
     b'if len(set(keys.values())) != len(keys):',
     b'if False and len(set(keys.values())) != len(keys):'),
    ('context ceiling 4096',
     b'MAX_CONTEXT_BYTES = 4096',
     b'MAX_CONTEXT_BYTES = 40960'),
    ('context must be non-empty',
     b'        if (not isinstance(context, dict) or not context\n'
     b'                or len(encode(context)) > MAX_CONTEXT_BYTES):',
     b'        if (not isinstance(context, dict)\n'
     b'                or len(encode(context)) > MAX_CONTEXT_BYTES):'),
    ('sealed payload 64000',
     b'MAX_SEALED_BYTES = 64000',
     b'MAX_SEALED_BYTES = 640000'),
    ('opened payload check',
     b'            if len(raw) > MAX_SEALED_BYTES:\n'
     b'                raise ValueError()\n'
     b'            return json.loads(raw',
     b'            if False:\n'
     b'                raise ValueError()\n'
     b'            return json.loads(raw'),
    ('envelope 90000',
     b'MAX_ENVELOPE_CHARS = 90000',
     b'MAX_ENVELOPE_CHARS = 900000'),
    ('encoded field 150000',
     b'MAX_ENCODED_FIELD = 150000',
     b'MAX_ENCODED_FIELD = 1500000'),
    ('envelope field set',
     b'set(data) != ENVELOPE_FIELDS',
     b'not set(data) >= ENVELOPE_FIELDS'),
    ('envelope version 1',
     b'ENVELOPE_VERSION = 1',
     b'ENVELOPE_VERSION = 2'),
    ('aad format 1',
     b'AAD_FORMAT = 1',
     b'AAD_FORMAT = 2'),
    ('nonce 12 bytes',
     b'NONCE_BYTES = 12',
     b'NONCE_BYTES = 13'),
    ('seal names caller error',
     b'        except VaultError:\n'
     b'            raise\n'
     b'        except Exception:\n'
     b"            raise VaultError('Secret encryption failed') from None",
     b'        except Exception:\n'
     b"            raise VaultError('Secret encryption failed') from None"),
    ('open stays generic',
     b'        except Exception:\n'
     b"            raise VaultError('Secret authentication failed') from None",
     b'        except VaultError:\n'
     b'            raise\n'
     b'        except Exception:\n'
     b"            raise VaultError('Secret authentication failed') from None"),
]

if __name__ == '__main__':
    if '--check' in sys.argv:
        sys.exit(revert_matrix.verify(TARGET, MUTATIONS))
    sys.exit(revert_matrix.main(TARGET, PATTERN, MUTATIONS))
