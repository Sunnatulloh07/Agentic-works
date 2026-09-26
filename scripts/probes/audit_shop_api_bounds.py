"""Boundary audit of ``app/shop_api.py`` — the revert matrix.

The dashboard's shop routes carried named constants that nothing pinned: a row
ceiling, a message ceiling, the channel list with its config-block map, two role
tuples and the credential-name shape. One inline literal survived naming
(``pack.products[:1000]``) and is ``MAX_PRODUCTS`` now -- found by the source
guard in the spec, not by a review.

The spec is ``runtime_tests.test_shop_api_bounds``: the queries' HTTP behaviour is
``integration_tests/test_shop_api.py``'s, which cannot join a unittest matrix, so
the rows below are the offline pins plus the three pure helpers (``_text``,
``_json``, ``_credential_refs``) whose behaviour runs anywhere.

Run from the repository root::

    python scripts/probes/audit_shop_api_bounds.py --verify   # patterns only
    python scripts/probes/audit_shop_api_bounds.py            # full matrix
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

TARGET = 'app/shop_api.py'
PATTERN = 'runtime_tests.test_shop_api_bounds'

MUTATIONS = [
    ('row ceiling 100 -> 1000',
     b'MAX_SHOP_ROWS = 100',
     b'MAX_SHOP_ROWS = 1000'),
    ('message ceiling 1000 -> 10000',
     b'MAX_MESSAGE_CHARS = 1000',
     b'MAX_MESSAGE_CHARS = 10000'),
    ('product ceiling 1000 -> 10000',
     b'MAX_PRODUCTS = 1000',
     b'MAX_PRODUCTS = 10000'),
    ('whatsapp loses its webhook block',
     b"('whatsapp', 'whatsapp_webhook', 'whatsapp_tokens')",
     b"('whatsapp', 'whatsapp_tokens')"),
    ('viewers join the operator surface',
     b"OPERATORS = ('owner', 'operator')",
     b"OPERATORS = ('owner', 'operator', 'viewer')"),
    ('integrators lose the owner',
     b"INTEGRATORS = ('owner', 'integrator')",
     b"INTEGRATORS = ('integrator',)"),
    ('credential names may be lowercase',
     b"re.compile(r'[A-Z][A-Z0-9_]*')",
     b"re.compile(r'[A-Za-z][A-Za-z0-9_]*')"),
    ('the message slice stops truncating',
     b'text[:MAX_MESSAGE_CHARS]',
     b'text'),
    ('a type mismatch leaks through the JSON helper',
     b'value if isinstance(value, type(default)) else default',
     b'value'),
]

if __name__ == '__main__':
    if '--verify' in sys.argv:
        sys.exit(revert_matrix.verify(TARGET, MUTATIONS))
    sys.exit(revert_matrix.main(TARGET, PATTERN, MUTATIONS, revert_matrix.AUTO_BASELINE))
