"""Boundary audit of the application layer -- the revert matrix (§155).

``app/`` was the last unaudited layer and it held a different kind of gap from the
runtime modules. Those were *unnamed*; several of these were named and pinned only
in ``tests/`` -- a legacy suite that **no gate runs** and that is **35 tests red**
because the API it exercises was deliberately retired. The routes answer ``410
Gone``:

    Legacy runner disabled; use /platform/runner/ws and platform tasks
    Legacy mutation retired; use platform API

So ``MAX_PERSONA_CHARS`` and ``MAX_QUEUE`` looked pinned, and were not: a pin that
lives in a suite nobody runs is not a pin. The rest had no name at all -- the
session token lifetime was a bare ``900`` inside a ternary, the throttle's window
was a default argument, and the daily ceiling's warning threshold was ``0.8``
written out twice.

Enumerated bounds and what the matrix found:

| Module | Bound | Value | Before |
|---|---|---|---|
| ``auth.py`` | token ceiling | 86 400 s | unpinned |
| ``auth.py`` | token floor | 1 s | **unnamed** |
| ``auth.py`` | session token lifetime | 900 s | **unnamed** |
| ``identity_store.py`` | session ceiling | 2 592 000 s | unpinned |
| ``identity_store.py`` | session floor | 60 s | **unnamed** |
| ``identity_store.py`` | throttle limit | 20 | **unnamed** |
| ``identity_store.py`` | throttle window | 900 s | **unnamed** |
| ``identity_store.py`` | invitation default / floor / ceiling | 86 400 / 300 / 604 800 s | **unnamed** |
| ``identity_store.py`` | e-mail ceiling | 320 chars | **unnamed** |
| ``identity_store.py`` | password floor / ceiling | 12 / 256 | **unnamed** |
| ``identity_store.py`` | token floor / ceiling | 20 / 256 | **unnamed** |
| ``identity_store.py`` | scrypt cost factor | 16 384 | **unnamed** |
| ``identity_store.py`` | scrypt block size / parallelism | 8 / 1 | **unnamed** |
| ``identity_store.py`` | derived key length / salt bytes | 32 / 16 | **unnamed** |
| ``identity_store.py`` | last-owner guard | 1 | **unnamed** |
| ``limits.py`` | daily ceiling / floor | 1000 / 1 | **unnamed** |
| ``limits.py`` | counter TTL | 86 400 s | **unnamed** |
| ``limits.py`` | warning threshold | 0.8 | **unnamed** |
| ``packs.py`` | persona ceiling | 8 000 chars | pinned only in ``tests/`` |
| ``telegram.py`` | webhook body ceiling | 1 000 000 B | unpinned |
| ``trace.py`` | rotation threshold | 5 MiB | unpinned |
| ``runner_ws.py`` | queue ceiling | 100 | pinned only in ``tests/`` |
| ``runner_ws.py`` | result ceiling | 1 000 | unpinned |

The scrypt row is the one worth pausing on. Those five numbers are not tunables;
they are the cost of attacking every stored password. Nothing in the suite read
them, so ``SCRYPT_N = 16384`` could have become ``1024`` -- a 16× cheaper
brute-force -- and the only signal would have been that logins got faster.

The measured spec is two files. ``test_app_layer_bounds`` holds the pins and
``test_identity_store`` is the consumer: it creates real sessions, invitations and
passwords, so a widened TTL or a loosened password floor is felt by a caller rather
than only by the assertion that names it. ``test_identity_hardening`` would have
been a third, and was left out on measurement -- it costs 12 s per run, which would
turn a 3-minute matrix into a 10-minute one for no additional coverage.

Run from the repository root::

    python scripts/audit_app_layer_bounds.py
    python scripts/audit_app_layer_bounds.py --check
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

PATTERN = ('runtime_tests.test_app_layer_bounds '
           'runtime_tests.test_identity_store')

MODULES = [
    ('app/auth.py', [
        ('token ceiling 86400',
         b'TTL_SECONDS = 24 * 3600',
         b'TTL_SECONDS = 24 * 36000'),
        ('token floor 1',
         b'MIN_TOKEN_TTL_SECONDS = 1',
         b'MIN_TOKEN_TTL_SECONDS = 0'),
        ('session token lifetime 900',
         b'SESSION_TOKEN_TTL_SECONDS = 900',
         b'SESSION_TOKEN_TTL_SECONDS = 9000'),
    ]),
    ('app/identity_store.py', [
        ('session ceiling 2592000',
         b'MAX_SESSION_TTL = 30 * 86400',
         b'MAX_SESSION_TTL = 30 * 864000'),
        ('session floor 60',
         b'MIN_SESSION_TTL = 60',
         b'MIN_SESSION_TTL = 6'),
        ('throttle limit 20',
         b'THROTTLE_LIMIT = 20',
         b'THROTTLE_LIMIT = 200'),
        ('throttle window 900',
         b'THROTTLE_WINDOW_SECONDS = 900',
         b'THROTTLE_WINDOW_SECONDS = 9000'),
        ('invitation default 86400',
         b'INVITATION_TTL_SECONDS = 86_400',
         b'INVITATION_TTL_SECONDS = 864_000'),
        ('invitation floor 300',
         b'MIN_INVITATION_TTL_SECONDS = 300',
         b'MIN_INVITATION_TTL_SECONDS = 30'),
        ('invitation ceiling 604800',
         b'MAX_INVITATION_TTL_SECONDS = 604_800',
         b'MAX_INVITATION_TTL_SECONDS = 6_048_000'),
        ('email ceiling 320',
         b'MAX_EMAIL_CHARS = 320',
         b'MAX_EMAIL_CHARS = 3200'),
        ('password floor 12',
         b'MIN_PASSWORD_CHARS = 12',
         b'MIN_PASSWORD_CHARS = 1'),
        ('password ceiling 256',
         b'MAX_PASSWORD_CHARS = 256',
         b'MAX_PASSWORD_CHARS = 2560'),
        ('candidate password floor 1',
         b'MIN_CANDIDATE_PASSWORD_CHARS = 1',
         b'MIN_CANDIDATE_PASSWORD_CHARS = 0'),
        ('token floor 20',
         b'MIN_TOKEN_CHARS = 20',
         b'MIN_TOKEN_CHARS = 2'),
        ('token ceiling 256',
         b'MAX_TOKEN_CHARS = 256',
         b'MAX_TOKEN_CHARS = 2560'),
        ('scrypt cost factor',
         b'SCRYPT_N = 16384',
         b'SCRYPT_N = 1024'),
        ('scrypt block size',
         b'SCRYPT_R = 8',
         b'SCRYPT_R = 1'),
        ('scrypt parallelism',
         b'SCRYPT_P = 1',
         b'SCRYPT_P = 2'),
        ('scrypt key length',
         b'SCRYPT_DKLEN = 32',
         b'SCRYPT_DKLEN = 16'),
        ('salt bytes',
         b'SALT_BYTES = 16',
         b'SALT_BYTES = 8'),
        ('last owner guard',
         b'LAST_OWNER_GUARD = 1',
         b'LAST_OWNER_GUARD = 2'),
    ]),
    ('app/limits.py', [
        ('daily ceiling 1000',
         b'DEFAULT_DAILY_LIMIT = 1000',
         b'DEFAULT_DAILY_LIMIT = 10000'),
        ('daily floor 1',
         b'MIN_DAILY_LIMIT = 1',
         b'MIN_DAILY_LIMIT = 0'),
        ('counter ttl 86400',
         b'COUNTER_TTL_SECONDS = 86_400',
         b'COUNTER_TTL_SECONDS = 864_000'),
        ('warn fraction 0.8',
         b'WARN_FRACTION = 0.8',
         b'WARN_FRACTION = 0.08'),
    ]),
    ('app/packs.py', [
        ('persona ceiling 8000',
         b'MAX_PERSONA_CHARS = 8000',
         b'MAX_PERSONA_CHARS = 80000'),
    ]),
    ('app/telegram.py', [
        ('webhook body ceiling 1000000',
         b'MAX_BODY = 1_000_000',
         b'MAX_BODY = 10_000_000'),
    ]),
    ('app/trace.py', [
        ('rotation threshold 5MiB',
         b'MAX_BYTES = 5 * 1024 * 1024',
         b'MAX_BYTES = 50 * 1024 * 1024'),
    ]),
    ('app/runner_ws.py', [
        ('queue ceiling 100',
         b'MAX_QUEUE = 100',
         b'MAX_QUEUE = 1000'),
        ('result ceiling 1000',
         b'MAX_RESULTS = 1000',
         b'MAX_RESULTS = 10000'),
    ]),
]

if __name__ == '__main__':
    if '--check' in sys.argv:
        code = 0
        for target, mutations in MODULES:
            code |= revert_matrix.verify(target, mutations)
        sys.exit(code)
    code = 0
    for target, mutations in MODULES:
        print('=' * 72)
        code |= revert_matrix.main(target, PATTERN, mutations)
    sys.exit(code)
