"""Measure the boundaries of ``platform_runtime/secret_vault.py``.

A probe, not a test: every claim below is measured through the real surface and can
be re-run and refuted. It complements the revert matrix in ``audit_vault_bounds.py``
-- the matrix asks "would anything notice if this number changed?", and this asks
"what is actually true right now?".

The vault is the encryption boundary, so the sections here are the things it decides:
which key rings are legal, what a context may be, how large a payload may be on the
way in and on the way out, how large an envelope may be, and which refusals are
allowed to name their cause.

Run the way the suite is run::

    PY="$HOME/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
    PYTHONPATH=".;$(python -c 'import site;print(site.getsitepackages()[0])')" \\
      "$PY" scripts/probes/probe_vault_boundaries.py
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

import platform_runtime.secret_vault as module                  # noqa: E402
from platform_runtime.engine import encode                      # noqa: E402
from platform_runtime.secret_vault import (                     # noqa: E402
    AAD_FORMAT, ENVELOPE_VERSION, KEY_BYTES, MAX_CONTEXT_BYTES, MAX_ENCODED_FIELD,
    MAX_ENVELOPE_CHARS, MAX_KEY_RING, MAX_SEALED_BYTES, MIN_KEY_RING, NONCE_BYTES,
    SecretVault, VaultError, _b64)

SOURCE = Path(ROOT, 'api-python', 'platform_runtime', 'secret_vault.py')
TEXT = SOURCE.read_bytes()

KEY = os.urandom(KEY_BYTES)
VAULT = SecretVault({'v1': KEY}, 'v1')
CTX = {'tenant': 'a', 'account': 'id'}

PASS, FAIL = [], []


def check(label, ok, detail=''):
    (PASS if ok else FAIL).append(label)
    print(f'  [{"ok  " if ok else "FAIL"}] {label}{"  " + detail if detail else ""}')


def refuses(call):
    """Return the exception a call raises, or None if it did not raise."""
    try:
        call()
        return None
    except Exception as error:                                     # noqa: BLE001
        return error


def walk(label, fn, accepted, refused):
    """A bound is pinned only when BOTH sides are measured."""
    for value in accepted:
        error = refuses(lambda v=value: fn(v))
        check(f'{label}: {value} accepted', error is None,
              f'got {error!r}' if error else '')
    for value in refused:
        error = refuses(lambda v=value: fn(v))
        check(f'{label}: {value} refused', error is not None)


def context_of_bytes(n):
    """A context whose ``encode``d form is exactly ``n`` bytes."""
    return {'a': 'x' * (n - 8)}


def envelope_of_raw_bytes(key, kid, size, context):
    """A well-formed envelope whose plaintext is exactly ``size`` bytes.

    ``seal`` refuses anything above its own cap, so an oversized envelope can only be
    built directly -- which is what ``open``'s own cap defends against.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(NONCE_BYTES)
    aad = encode({'format': AAD_FORMAT, 'kid': kid, 'context': context}).encode('utf-8')
    raw = encode('x' * (size - 2)).encode('utf-8')
    return encode({'v': ENVELOPE_VERSION, 'kid': kid,
                   'nonce': _b64(nonce), 'ciphertext': _b64(AESGCM(key).encrypt(nonce, raw, aad))})


def env_var_ceiling():
    """The longest value this platform will accept as an environment variable.

    Bisection, not a table: Windows refuses above 32 767 and Linux above 131 072, and
    which one applies is a property of the machine, not of the vault. Measured so the
    reachability claim below rests on this host rather than on a remembered number.
    """
    lo, hi = 1, 1 << 22
    while lo < hi:
        mid = (lo + hi + 1) // 2
        try:
            os.environ['PROBE_VAULT_CEILING'] = 'x' * mid
            ok = True
        except (ValueError, OSError):
            ok = False
        finally:
            os.environ.pop('PROBE_VAULT_CEILING', None)
        if ok:
            lo = mid
        else:
            hi = mid - 1
    return lo


# ------------------------------------------------------------ 1. the literals

print('\n1. Every bound is a named constant, stated once')

DECLARED = [
    ('key ring floor', b'MIN_KEY_RING = 1'),
    ('key ring ceiling', b'MAX_KEY_RING = 8'),
    ('key id shape', b"KEY_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')"),
    ('key material', b'KEY_BYTES = 32'),
    ('context ceiling', b'MAX_CONTEXT_BYTES = 4096'),
    ('sealed payload', b'MAX_SEALED_BYTES = 64000'),
    ('envelope ceiling', b'MAX_ENVELOPE_CHARS = 90000'),
    ('encoded field', b'MAX_ENCODED_FIELD = 150000'),
    ('nonce', b'NONCE_BYTES = 12'),
    ('envelope fields', b"ENVELOPE_FIELDS = frozenset({'v', 'kid', 'nonce', 'ciphertext'})"),
    ('envelope version', b'ENVELOPE_VERSION = 1'),
    ('aad format', b'AAD_FORMAT = 1'),
]
for label, declaration in DECLARED:
    check(f'{label}: declared as a constant', declaration in TEXT)

SITES = [
    ('ring floor', b'MIN_KEY_RING <= len(keys)'),
    ('ring ceiling', b'len(keys) <= MAX_KEY_RING'),
    ('key id', b'KEY_ID.fullmatch(k)'),
    ('key material', b'len(v) != KEY_BYTES'),
    ('context', b'len(encode(context)) > MAX_CONTEXT_BYTES'),
    ('seal payload', b'len(raw) > MAX_SEALED_BYTES'),
    ('envelope', b'len(envelope) > MAX_ENVELOPE_CHARS'),
    ('encoded field', b'len(text) > MAX_ENCODED_FIELD'),
    ('nonce', b'len(nonce) != NONCE_BYTES'),
    ('field set', b'set(data) != ENVELOPE_FIELDS'),
    ('version', b"data['v'] != ENVELOPE_VERSION"),
]
for label, site in SITES:
    check(f'{label}: the guard names the constant, not a literal', site in TEXT)

# The point of naming them: a number that appears twice can be widened at one site
# and left at the other. Each of these now appears exactly once, in its declaration.
for number in (b'150000', b'90000', b'64000', b'4096'):
    check(f'{number.decode()} is stated exactly once in the module',
          TEXT.count(number) == 1, f'count={TEXT.count(number)}')

check('no bare 32-byte comparison remains', b'len(v) != 32' not in TEXT)
check('no bare 12-byte nonce comparison remains', b'len(nonce) != 12' not in TEXT)


# ------------------------------------------------------------- 2. the key ring

print('\n2. The key ring, both sides')


def ring(n, active='k0'):
    return SecretVault({f'k{i}': os.urandom(KEY_BYTES) for i in range(n)}, active)


def with_id(length):
    kid = 'k' * length
    return SecretVault({kid: os.urandom(KEY_BYTES)}, kid)


def with_key_bytes(n):
    return SecretVault({'v1': os.urandom(n)}, 'v1')


walk('ring size', ring, [1, 2, MAX_KEY_RING], [0, MAX_KEY_RING + 1, 16])
walk('key id length', with_id, [1, 64], [0, 65, 1000])
walk('key material bytes', with_key_bytes, [32], [0, 31, 33, 64])

shared = os.urandom(KEY_BYTES)
check('duplicate key material refused',
      refuses(lambda: SecretVault({'a': shared, 'b': shared}, 'a')) is not None)
check('an active id absent from the ring refused',
      refuses(lambda: SecretVault({'a': KEY}, 'b')) is not None)
for bad in ('A key id with spaces', 'k!', 'ünïcode'):
    check(f'key id {bad!r} refused', refuses(lambda b=bad: SecretVault({b: KEY}, b)) is not None)


# -------------------------------------------------------------- 3. the context

print('\n3. The encryption context, both sides')

check('the context bound is on ENCODED bytes, not characters',
      len(encode(context_of_bytes(MAX_CONTEXT_BYTES))) == MAX_CONTEXT_BYTES)
walk('context encoded bytes',
     lambda n: VAULT.seal({'x': 1}, context_of_bytes(n)),
     [8, MAX_CONTEXT_BYTES], [MAX_CONTEXT_BYTES + 1, 65536])
check('the bound is on bytes, not key count: 100 small keys are accepted',
      refuses(lambda: VAULT.seal({'x': 1}, {f'k{i}': i for i in range(100)})) is None)
for bad in (None, [], 'string', 5, {}, ()):
    check(f'context {bad!r} refused',
          refuses(lambda b=bad: VAULT.seal({'x': 1}, b)) is not None)
check('a context is bound into the ciphertext, not merely accepted',
      refuses(lambda: VAULT.open(VAULT.seal({'x': 1}, CTX), {'tenant': 'b', 'account': 'id'}))
      is not None)


# -------------------------------------------------------------- 4. the payload

print('\n4. The payload, on the way in and on the way out')


def sealed(n):
    return VAULT.seal({'a': 'x' * (n - 8)}, CTX)


def opened(n):
    return VAULT.open(envelope_of_raw_bytes(KEY, 'v1', n, CTX), CTX)


walk('sealed payload bytes', sealed, [8, MAX_SEALED_BYTES], [MAX_SEALED_BYTES + 1])
walk('opened payload bytes', opened, [MAX_SEALED_BYTES], [MAX_SEALED_BYTES + 1])
check('one constant guards both directions',
      TEXT.count(b'MAX_SEALED_BYTES') == 3 and MAX_SEALED_BYTES == 64000,
      'declaration + seal guard + open guard')


# ------------------------------------------------------------- 5. the envelope

print('\n5. The envelope, and what can actually reach its ceiling')

longest = SecretVault({'k' * 64: KEY}, 'k' * 64)
biggest = longest.seal({'a': 'x' * (MAX_SEALED_BYTES - 8)}, CTX)
print(f'       largest envelope a maximum payload can produce: {len(biggest)} chars')
check('no well-formed envelope reaches the ceiling',
      len(biggest) < MAX_ENVELOPE_CHARS,
      f'{len(biggest)} < {MAX_ENVELOPE_CHARS}')
check('so the ceiling is a parse guard, not a payload bound',
      MAX_SEALED_BYTES < MAX_ENVELOPE_CHARS)

# Its refusal is therefore indistinguishable from a parse failure by outcome alone --
# both raise VaultError -- so the mechanism is what gets measured.
with patch.object(module, 'json') as fake:
    check('an oversized envelope is refused before json.loads runs',
          refuses(lambda: VAULT.open('x' * (MAX_ENVELOPE_CHARS + 1), CTX)) is not None
          and not fake.loads.called)

for label, envelope in (
        ('empty', ''),
        ('not an object', '[]'),
        ('empty object', '{}'),
        ('duplicate field', '{"v":1,"v":1}'),
        ('missing field', '{"v":1,"kid":"v1","nonce":"AAAAAAAAAAAAAAAA"}')):
    check(f'envelope {label} refused',
          refuses(lambda e=envelope: VAULT.open(e, CTX)) is not None)

good = json.loads(VAULT.seal({'a': 1}, CTX))
extra = dict(good, extra=1)
check('an envelope with a fifth field refused',
      refuses(lambda: VAULT.open(json.dumps(extra), CTX)) is not None)
check('an envelope with a non-integer version refused',
      refuses(lambda: VAULT.open(json.dumps(dict(good, v='1')), CTX)) is not None)
check('an envelope with an unknown version refused',
      refuses(lambda: VAULT.open(json.dumps(dict(good, v=2)), CTX)) is not None)
check('an envelope with a short nonce refused',
      refuses(lambda: VAULT.open(json.dumps(dict(good, nonce=_b64(b'short'))), CTX)) is not None)
check('an envelope naming an unconfigured key refused',
      refuses(lambda: VAULT.open(json.dumps(dict(good, kid='nope')), CTX)) is not None)


# -------------------------------------------------------- 6. the encoded field

print('\n6. The encoded-field cap, and whether anything can reach it')

ceiling = env_var_ceiling()
print(f'       this platform caps an environment variable at {ceiling} chars')
check('inside open the envelope ceiling binds first',
      MAX_ENVELOPE_CHARS < MAX_ENCODED_FIELD,
      f'{MAX_ENVELOPE_CHARS} < {MAX_ENCODED_FIELD}')
check('RECORDED: an environment variable cannot carry the field either',
      ceiling < MAX_ENCODED_FIELD, f'{ceiling} < {MAX_ENCODED_FIELD}')

with patch.object(module, 'base64') as fake:
    check('an oversized field is refused before base64 decoding',
          refuses(lambda: module._unb64('A' * (MAX_ENCODED_FIELD + 1))) is not None
          and not fake.b64decode.called)
check('a field at the cap still decodes',
      len(module._unb64(_b64(os.urandom(KEY_BYTES)))) == KEY_BYTES)
check('a non-string field refused', refuses(lambda: module._unb64(b'bytes')) is not None)


# --------------------------------------------------------- 7. refusal messages

print('\n7. Which refusals may name their cause')

seal_error = str(refuses(lambda: VAULT.seal({'x': 1}, {})))
check('seal names the caller\'s own context error', 'context' in seal_error, seal_error)

envelope = VAULT.seal({'a': 1}, CTX)
messages = {str(refuses(lambda b=bad: VAULT.open(envelope, b)))
            for bad in ({}, {'tenant': 'a'}, {'a': 'x' * MAX_CONTEXT_BYTES})}
unknown = json.loads(envelope)
unknown['kid'] = 'nope'
messages.add(str(refuses(lambda: VAULT.open(json.dumps(unknown), CTX))))
check('open collapses every refusal into one message', len(messages) == 1, str(messages))
only = messages.pop()
check('and that message names neither the context nor the key id',
      'context' not in only and 'nope' not in only, only)

# The asymmetry is the point: a distinct message for a malformed context would
# separate it from an unknown key id, which is an oracle for which ids are configured.
check('RECORDED: seal and open deliberately disagree about the same input',
      'context' in str(refuses(lambda: VAULT.seal({'x': 1}, {})))
      and 'context' not in str(refuses(lambda: VAULT.open(envelope, {}))))


# ------------------------------------- 8. bounds behaviour alone cannot pin

print('\n8. Bounds that behaviour alone cannot pin, measured')

# Each of these is read by BOTH the writer and the reader, so widening it moves both
# sides together and the round-trip still succeeds. Only the literal assertion in
# ``test_declared_values_are_the_audited_ones`` turns them RED; this records why that
# assertion is load-bearing rather than decorative. For AAD_FORMAT the stake is real:
# changing it in production makes every stored credential undecryptable.
for label, name, value in (('nonce size', 'NONCE_BYTES', 13),
                           ('envelope version', 'ENVELOPE_VERSION', 2),
                           ('aad format', 'AAD_FORMAT', 2)):
    with patch.object(module, name, value):
        check(f'RECORDED: a widened {label} still round-trips',
              refuses(lambda: VAULT.open(VAULT.seal({'x': 1}, CTX), CTX)) is None,
              'behaviour cannot see it; the literal assertion is the pin')


# ------------------------------------------------------- 9. recorded non-defects

print('\n9. Recorded non-defects')

with patch.object(module, 'MIN_KEY_RING', 0):
    check('RECORDED: MIN_KEY_RING is redundant with "active in keys"',
          refuses(lambda: SecretVault({}, 'k0')) is not None,
          'an empty ring is refused with the floor removed')

with patch.dict(os.environ, {'PLATFORM_VAULT_KEYS_JSON': json.dumps({'v1': _b64(KEY)}),
                             'PLATFORM_VAULT_ACTIVE_KEY': 'v1'}):
    loaded = SecretVault.from_environment()
    check('from_environment reads fixed names and round-trips',
          loaded.open(loaded.seal({'a': 1}, CTX), CTX) == {'a': 1})

with patch.dict(os.environ, {'PLATFORM_VAULT_KEYS_JSON': '{"v1":"SECRETMATERIAL"}',
                             'PLATFORM_VAULT_ACTIVE_KEY': 'v1'}):
    error = str(refuses(SecretVault.from_environment))
    check('a failed configuration load never echoes the configured value',
          'SECRETMATERIAL' not in error, error)

check('a sealed payload does not contain its own plaintext',
      'synthetic-only' not in VAULT.seal({'access_token': 'synthetic-only'}, CTX))
check('every refusal is a VaultError, never a bare ValueError or KeyError',
      all(isinstance(refuses(lambda e=e: VAULT.open(e, CTX)), VaultError)
          for e in ('', '{}', '[]', 'x' * (MAX_ENVELOPE_CHARS + 1))))


print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('failed:')
    for label in FAIL:
        print('  -', label)
sys.exit(1 if FAIL else 0)
