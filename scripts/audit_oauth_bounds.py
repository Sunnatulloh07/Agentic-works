"""Boundary audit of ``platform_runtime/oauth.py`` — the revert matrix.

Enumerated bounds, and whether anything is checking them:

| Bound | Value | Site |
|---|---|---|
| ``bounded`` default | 256 | the generic field gate |
| required scopes | 40 | how many a provider may declare |
| granted-scope string | 10 000 | the provider's own ``scope`` reply |
| token lifetime | 60 .. 86 400 | provider-declared ``expires_in`` |
| PKCE verifier | 43 .. 128 | RFC 7636 length |
| identifier | 1 .. 128 | connection and provider names |
| authorization code | 4 096 | the callback parameter |
| access / refresh token | 16 000 | provider-issued credential |

Run from the repository root::

    python scripts/audit_oauth_bounds.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

TARGET = 'platform_runtime/oauth.py'
PATTERN = 'test_oauth.py'

MUTATIONS = [
    ('bounded default 256',
     b'def bounded(value, maximum=256):',
     b'def bounded(value, maximum=257):'),
    ('required scopes 40',
     b'len(self.required_scopes) > 40:',
     b'len(self.required_scopes) > 41:'),
    ('granted scope 10000',
     b'len(raw_scope) <= 10000:',
     b'len(raw_scope) <= 10001:'),
    ('expiry ceiling 86400',
     b'not 60 <= expires <= 86400:',
     b'not 60 <= expires <= 86401:'),
    ('expiry floor 60',
     b'not 60 <= expires <= 86400:',
     b'not 59 <= expires <= 86400:'),
    ('PKCE floor 43',
     b"re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier)",
     b"re.fullmatch(r'[A-Za-z0-9._~-]{42,128}', verifier)"),
    ('identifier ceiling 128',
     b"re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value)",
     b"re.fullmatch(r'[A-Za-z0-9_.-]{1,129}', value)"),
    ('authorization code 4096',
     b'bounded(code, 4096)',
     b'bounded(code, 4097)'),
    ('access token 16000',
     b"bounded(body.get('access_token'), 16000)",
     b"bounded(body.get('access_token'), 16001)"),
]

if __name__ == '__main__':
    sys.exit(revert_matrix.main(TARGET, PATTERN, MUTATIONS))
