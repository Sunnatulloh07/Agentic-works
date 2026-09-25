"""Boundary audit of ``app/storage.py`` — the revert matrix.

The shared storage layer had *behaviour* tests next door (customer 360, approvals)
but no bounds owner: the connection timeout, the delivery lease and the error
ceiling were inline numbers, so nothing could address them by name and widening
one was silent. Three constants and nine mutations later, each row is either RED
here or named as uncovered.

The spec is one file: ``runtime_tests.test_storage_bounds``. The delivery claim
path is also exercised by ``test_customer360_bounds``, which is included so a
bound pinned only next door cannot read as GREEN.

Run from the repository root::

    python scripts/probes/audit_storage_bounds.py --verify   # patterns only
    python scripts/probes/audit_storage_bounds.py            # full matrix
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

TARGET = 'app/storage.py'
PATTERN = 'runtime_tests.test_storage_bounds runtime_tests.test_customer360_bounds'

MUTATIONS = [
    ('connection timeout 30s -> 0s',
     b'CONNECT_TIMEOUT_SECONDS = 30.0',
     b'CONNECT_TIMEOUT_SECONDS = 0.0'),
    ('delivery lease 60s -> 6000s',
     b'DELIVERY_LEASE_SECONDS = 60',
     b'DELIVERY_LEASE_SECONDS = 6000'),
    ('error ceiling 500 -> 5000',
     b'MAX_DELIVERY_ERROR_CHARS = 500',
     b'MAX_DELIVERY_ERROR_CHARS = 5000'),
    ('error no longer truncated',
     b'error[:MAX_DELIVERY_ERROR_CHARS]',
     b'error'),
    ('lease floor 1s -> 0s',
     b'max(1, lease_seconds)',
     b'max(0, lease_seconds)'),
    ('empty worker accepted',
     b'if not worker_id.strip():',
     b'if False:'),
    ('duplicate inbound raises instead of ignoring',
     b'INSERT OR IGNORE INTO messages',
     b'INSERT INTO messages'),
    ('claim always reports success',
     b'''            (worker_id, current + max(1, lease_seconds), current,
             tenant_id, delivery_id, current),
        )
        return cursor.rowcount == 1''',
     b'''            (worker_id, current + max(1, lease_seconds), current,
             tenant_id, delivery_id, current),
        )
        return True'''),
    ('a settled delivery can be re-claimed',
     b"AND status IN ('pending','failed','claimed') ",
     b"AND status IN ('pending','failed','claimed','sent') "),
]

if __name__ == '__main__':
    if '--verify' in sys.argv:
        sys.exit(revert_matrix.verify(TARGET, MUTATIONS))
    sys.exit(revert_matrix.main(TARGET, PATTERN, MUTATIONS, revert_matrix.AUTO_BASELINE))
