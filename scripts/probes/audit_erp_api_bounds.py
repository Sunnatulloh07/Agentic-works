"""Boundary audit of ``app/erp_api.py`` — the revert matrix.

The ERP posting surface carried three inline numbers and two inline role tuples:
who may read the ledger, who may settle an uncertain posting, and how much of each
is returned. They are named now, and this matrix checks that the offline owner
(``runtime_tests.test_erp_api_bounds``) plus the HTTP suite
(``integration_tests.test_erp_api``, which cannot join a unittest matrix) notice
when one moves.

Run from the repository root::

    python scripts/probes/audit_erp_api_bounds.py --verify   # patterns only
    python scripts/probes/audit_erp_api_bounds.py            # full matrix
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

TARGET = 'app/erp_api.py'
PATTERN = 'runtime_tests.test_erp_api_bounds'

MUTATIONS = [
    ('posting limit 100 -> 1000',
     b'POSTING_LIMIT = 100',
     b'POSTING_LIMIT = 1000'),
    ('evidence ceiling 1000 -> 10000',
     b'MAX_EVIDENCE_CHARS = 1000',
     b'MAX_EVIDENCE_CHARS = 10000'),
    ('external id ceiling 128 -> 1280',
     b'MAX_EXTERNAL_ID_CHARS = 128',
     b'MAX_EXTERNAL_ID_CHARS = 1280'),
    ('the query stops reading the constant',
     b'LIMIT {POSTING_LIMIT}',
     b'LIMIT 100'),
    ('the read is opened to viewers',
     b"api.identity(request, tenant, ('owner', 'operator'))",
     b"api.identity(request, tenant, ('owner', 'operator', 'viewer'))"),
    ('reconciliation is opened to operators',
     b"api.identity(request, tenant, ('owner',))",
     b"api.identity(request, tenant, ('owner', 'operator'))"),
]

if __name__ == '__main__':
    if '--verify' in sys.argv:
        sys.exit(revert_matrix.verify(TARGET, MUTATIONS))
    sys.exit(revert_matrix.main(TARGET, PATTERN, MUTATIONS, revert_matrix.AUTO_BASELINE))
