"""Boundary audit of ``app/main.py`` — the revert matrix.

The front door had every surface inline: the CORS origin, method and header lists,
the tenant charset, the role set, the owner-only prefix, the legacy exemption
list, the no-store list and the runner prefix. None was addressable from a test,
so widening any of them was silent -- and these are the numbers that decide who
may reach the legacy API at all.

The spec is ``runtime_tests.test_main_bounds`` (offline, coroutine driven by hand)
plus ``integration_tests.test_cors_and_retirement_http``, which cannot join the
matrix (pytest, dependency-backed) but is where the same surfaces are pinned over
real HTTP. Rows below are marked when the only owner is the literal pin.

Run from the repository root::

    python scripts/probes/audit_main_bounds.py --verify   # patterns only
    python scripts/probes/audit_main_bounds.py            # full matrix
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

TARGET = 'app/main.py'
PATTERN = 'runtime_tests.test_main_bounds'

MUTATIONS = [
    ('default CORS origin moved',
     b"DEFAULT_CORS_ORIGIN = 'http://localhost:3000'",
     b"DEFAULT_CORS_ORIGIN = 'https://evil.example'"),
    ('tenant ceiling 64 -> 640',
     b'MAX_TENANT_CHARS = 64',
     b'MAX_TENANT_CHARS = 640'),
    ('a new role joins the set',
     b"ROLES = frozenset({'owner', 'operator', 'integrator', 'viewer'})",
     b"ROLES = frozenset({'owner', 'operator', 'integrator', 'viewer', 'device'})"),
    ('mutation roles narrowed to owner',
     b"MUTATION_ROLES = frozenset({'owner', 'operator'})",
     b"MUTATION_ROLES = frozenset({'owner'})"),
    ('platform drops out of the legacy exemptions',
     b"LEGACY_EXEMPT_PREFIXES = ('/webhooks/', '/platform/', '/auth/', '/identity/')",
     b"LEGACY_EXEMPT_PREFIXES = ('/webhooks/', '/auth/', '/identity/')"),
    ('no-store list loses platform',
     b"NO_STORE_PREFIXES = ('/identity/', '/platform/')",
     b"NO_STORE_PREFIXES = ('/identity/',)"),
    ('runner prefix renamed',
     b"LEGACY_RUNNER_PREFIX = '/runner/'",
     b"LEGACY_RUNNER_PREFIX = '/runnerx/'"),
    ('owner-only prefix renamed',
     b"OWNER_ONLY_PREFIX = '/ladder/'",
     b"OWNER_ONLY_PREFIX = '/ladderx/'"),
    # These three are behaviour-only: the constants stay, the branch does not.
    ('the runner retirement is skipped',
     b'if path.startswith(LEGACY_RUNNER_PREFIX):',
     b'if False:'),
    ('POST escapes the mutation retirement',
     b'if request.method not in {"GET", "HEAD", "OPTIONS"}:',
     b'if request.method not in {"GET", "HEAD", "OPTIONS", "POST"}:'),
    ('the owner-only prefix is no longer special',
     b'required = {"owner"} if path.startswith(OWNER_ONLY_PREFIX) else MUTATION_ROLES',
     b'required = MUTATION_ROLES'),
]

if __name__ == '__main__':
    if '--verify' in sys.argv:
        sys.exit(revert_matrix.verify(TARGET, MUTATIONS))
    sys.exit(revert_matrix.main(TARGET, PATTERN, MUTATIONS, revert_matrix.AUTO_BASELINE))
