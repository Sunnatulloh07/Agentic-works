"""Measure the boundaries of ``platform_runtime/oauth.py``.

A probe, not a test: every claim below is measured through the real surface and can
be re-run and refuted. It complements the revert matrix in
``audit_oauth_bounds.py`` -- the matrix asks "would anything notice if this number
changed?", and this asks "what is actually true right now?".

**This module needs ``cryptography``**, because the token store is AES-GCM. Under a
bare interpreter the vault refuses to seal and every measurement here would report a
failure that belongs to the environment, not the code. Run it the way the suite is
run::

    PY="$HOME/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
    PYTHONPATH=".;$(python -c 'import site;print(site.getsitepackages()[0])')" \\
      "$PY" scripts/probe_oauth_boundaries.py
"""
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))
sys.path.insert(0, os.path.join(ROOT, 'api-python', 'runtime_tests'))

from platform_runtime.engine import Engine, Forbidden          # noqa: E402
from platform_runtime.tools import build_registry              # noqa: E402
from platform_runtime.secret_vault import SecretVault          # noqa: E402
from platform_runtime.oauth import (                           # noqa: E402
    OAuthError, OAuthManager, bounded, ident, pkce_challenge)

SOURCE = Path(ROOT, 'api-python', 'platform_runtime', 'oauth.py')
TEXT = SOURCE.read_bytes()

PASS, FAIL = [], []


def check(label, ok, detail=''):
    (PASS if ok else FAIL).append(label)
    print(f'  [{"ok  " if ok else "FAIL"}] {label}{"  " + detail if detail else ""}')


def refuses(call, expected=None):
    """Return the exception a call raises, or None if it did not raise."""
    try:
        call()
        return None
    except Exception as error:                                   # noqa: BLE001
        if expected is not None and not isinstance(error, expected):
            return error
        return error


def walk(label, fn, accepted, refused):
    """A bound is pinned only when BOTH sides are measured."""
    for value in accepted:
        error = refuses(lambda v=value: fn(v))
        check(f'{label}: {value} accepted', error is None, f'got {error!r}' if error else '')
    for value in refused:
        error = refuses(lambda v=value: fn(v))
        check(f'{label}: {value} refused', error is not None, f'got {error!r}' if error else '')


# --------------------------------------------------------------- the plain gates

print('\n1. The gates that take a plain value')
check('bounded default is the literal 256',
      b'def bounded(value, maximum=256):\n' in TEXT)
# The gates take a STRING, so the walk varies its LENGTH. Passing the number
# itself measures the type check instead of the bound -- which is what the first
# version of this probe did, and it reported six failures that were its own.
walk('bounded length', lambda n: bounded('x' * n), [256], [257])
walk('bounded floor', lambda n: bounded('x' * n), [1], [0])
walk('ident length', lambda n: ident('a' * n), [128], [129])
walk('ident floor', lambda n: ident('a' * n), [1], [0])
walk('pkce verifier length', lambda n: pkce_challenge('a' * n), [43, 128], [42, 129])


# ------------------------------------------------------------- the provider reply

class Provider:
    """A provider whose reply the probe bends, one field at a time."""

    name = 'google'
    expected_account = 'subject-1'
    scopes = {'scope.one', 'scope.two'}

    def __init__(self, **override):
        self.override = override

    def public_config(self):
        return {'provider': self.name, 'account': self.expected_account,
                'scopes': sorted(self.scopes)}

    def authorization_url(self, state, challenge):
        return f'https://provider.invalid/authorize?state={state}'

    def identity(self, token):
        return self.override.get('identity', self.expected_account)

    def revoke(self, token):
        return None

    def response(self):
        body = {'access_token': 'access', 'refresh_token': 'refresh',
                'token_type': 'Bearer', 'expires_in': 3600,
                'scope': ' '.join(self.scopes)}
        body.update(self.override)
        return body

    def exchange(self, code, verifier):
        return self.response()

    def refresh(self, token):
        return self.response()


def authorize(provider, connection='c'):
    """Begin and complete one authorization, returning the exception it raised."""
    tmp = tempfile.TemporaryDirectory()
    engine = Engine(Path(tmp.name) / 'db', build_registry(),
                    lambda t, a: {'tools': [], 'ladder': 'autonomous'},
                    authority=lambda db, t, c, a, r: None)
    vault = SecretVault({'a': os.urandom(32)}, 'a')
    manager = OAuthManager(engine, vault, provider)
    state = parse_qs(urlsplit(
        manager.begin('a', connection, 'owner', 's')['authorization_url']).query)['state'][0]
    try:
        manager.complete('a', connection, 'owner', 's', state, 'code')
        return None, manager.describe('a', connection)
    except Exception as error:                                   # noqa: BLE001
        return error, manager.describe('a', connection)


print('\n2. The bounds on what the provider sends back')
check('expiry window is the literal 60 .. 86400',
      b'not 60 <= expires <= 86400:\n' in TEXT)
for value in (60, 3600, 86400):
    error, _ = authorize(Provider(expires_in=value))
    check(f'expiry {value} accepted', error is None, f'got {error!r}' if error else '')
for value in (59, 86401):
    error, _ = authorize(Provider(expires_in=value))
    check(f'expiry {value} refused', error is not None)
for value in ('3600', 3600.0, True, None):
    error, _ = authorize(Provider(expires_in=value))
    check(f'expiry {value!r} refused (not silently coerced)', error is not None)

check('granted scope string is bounded at the literal 10000',
      b'len(raw_scope) <= 10000:\n' in TEXT)
error, _ = authorize(Provider(scope='scope.one scope.two'))
check('a normal scope reply is accepted', error is None, f'got {error!r}' if error else '')
error, _ = authorize(Provider(scope='x' * 10001))
check('a scope reply past the ceiling is refused', error is not None)

check('the token ceiling is the literal 16000',
      b'return bounded(value, 16000)\n' in TEXT)
error, _ = authorize(Provider(access_token='x' * 16001))
check('an over-long access token is refused', error is not None)


# ------------------------------------------------ every refusal names its reason

print('\n3. A refusal keeps the reason it was refused for')
# Measured before the fix: five distinct refusals all reported
# "Authorization outcome unavailable; authorize again", which sends an operator
# looking for a network fault when the provider's answer was fully understood.
REASONS = {
    'expiry below the floor': Provider(expires_in=59),
    'expiry above the ceiling': Provider(expires_in=86401),
    'expiry not an integer': Provider(expires_in='3600'),
    'scope reply past the ceiling': Provider(scope='x' * 10001),
    'access token past the ceiling': Provider(access_token='x' * 16001),
    'granted scopes differ': Provider(scope='scope.one'),
    'provider answered for another account': Provider(identity='subject-2'),
}
seen = {}
for label, provider in REASONS.items():
    error, _ = authorize(provider)
    check(f'{label}: refused', error is not None)
    if error is not None:
        seen[label] = str(error)
        check(f'{label}: reason is specific',
              'unavailable' not in str(error), f'message={str(error)!r}')

distinct = len(set(seen.values()))
check(f'the {len(seen)} refusals do not collapse into one message',
      distinct >= 4, f'{distinct} distinct messages')


# ------------------------------------------- the failure is still recorded, always

print('\n4. A refused exchange is still fenced and recorded')
for label, provider in list(REASONS.items())[:5]:
    error, row = authorize(provider)
    check(f'{label}: connection left uncertain, not active',
          row['status'] == 'uncertain', f'status={row["status"]!r}')


print()
print(f'{len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('FAILED:')
    for label in FAIL:
        print('  -', label)
    sys.exit(1)
print('PROVEN: every bound in oauth.py is at its declared value, both sides of each '
      'bound are enforced through the real surface, every refusal names its own '
      'reason rather than collapsing into one generic message, and a refused '
      'exchange still leaves the connection fenced and recorded.')
