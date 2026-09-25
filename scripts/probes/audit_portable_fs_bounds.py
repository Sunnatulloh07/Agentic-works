"""Boundary audit of ``apps/runner/portable_fs.py`` — the revert matrix.

The runner's local filesystem helper was already named its numbers (``MAX_BYTES``,
``MAX_ENTRIES``, ``MAX_VISITED``) but nothing pinned them, and its **validation**
bounds -- the root count, the deny-list ceilings, the path length, the stdin
ceiling -- were inline in the middle of boolean expressions.

The spec is ``runtime_tests.test_portable_fs``, which has two halves: file
behaviour (Windows-blocked: O_NOFOLLOW, mkfifo, symlink privilege -- see
``test_platform_baseline.py``) and argument validation (runs everywhere). The
matrix here is measured on Windows, so a row whose only owner is the literal pin
is marked as such; the behaviour rows below are the ones that fail on this host.

Run from the repository root::

    python scripts/probes/audit_portable_fs_bounds.py --verify   # patterns only
    python scripts/probes/audit_portable_fs_bounds.py            # full matrix
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

TARGET = '../apps/runner/portable_fs.py'  # revert_matrix resolves it against api-python
PATTERN = 'runtime_tests.test_portable_fs'

MUTATIONS = [
    # Literal-pin rows (the file half is blocked on Windows).
    ('read ceiling 16000 -> 160000',
     b'MAX_BYTES = 16000',
     b'MAX_BYTES = 160000'),
    ('entry ceiling 200 -> 2000',
     b'MAX_ENTRIES = 200',
     b'MAX_ENTRIES = 2000'),
    ('visited ceiling 1000 -> 10000',
     b'MAX_VISITED = 1000',
     b'MAX_VISITED = 10000'),
    ('a deny word is dropped',
     b"DENY = ('.env', '.ssh', '.aws', 'credentials', 'password', 'parol', 'cvv')",
     b"DENY = ('.env', '.ssh', '.aws', 'credentials', 'password', 'cvv')"),
    # Behaviour rows: these fail on this host.
    ('an empty root list is allowed',
     b'not 1 <= len(folders) <= 32',
     b'not 0 <= len(folders) <= 32'),
    ('deny entries 100 -> 1000',
     b'len(deny) > 100',
     b'len(deny) > 1000'),
    ('deny entry length 128 -> 1280',
     b'len(v) > 128',
     b'len(v) > 1280'),
    ('path length 2000 -> 20000',
     b'len(value) > 2000',
     b'len(value) > 20000'),
    ('stdin ceiling 64000 -> 640000',
     b'len(data) > 64000',
     b'len(data) > 640000'),
    ('a third tool joins the surface',
     b"if tool not in {'fs.list', 'fs.read_text'} or not isinstance(args, dict):",
     b"if tool not in {'fs.list', 'fs.read_text', 'fs.write_text'} or not isinstance(args, dict):"),
    ('unexpected arguments are tolerated',
     b'if set(args) != {field}: raise ValueError(\'Unexpected filesystem arguments\')',
     b'if False: raise ValueError(\'Unexpected filesystem arguments\')'),
]

if __name__ == '__main__':
    if '--verify' in sys.argv:
        sys.exit(revert_matrix.verify(TARGET, MUTATIONS))
    sys.exit(revert_matrix.main(TARGET, PATTERN, MUTATIONS, revert_matrix.AUTO_BASELINE))
