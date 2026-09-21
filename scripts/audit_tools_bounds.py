"""Boundary audit of ``platform_runtime/tools.py`` — the revert matrix.

``tools.py`` is the gate every tool argument passes through, and the module that
tells a pack which names exist at all. The scan that selected it was mechanical:
it carried **thirteen** large numeric literals and **zero** named constants, so
every bound was invisible to a constant-based search and had to be counted by
hand. They are named now, which is what makes them mutable here by name.

Enumerated bounds, and whether anything is checking them:

| Bound | Value | Site |
|---|---|---|
| schema nesting | 64 levels | ``validate_schema`` recursion ceiling |
| argument object | 20 000 bytes | ``validate_schema`` object branch |
| array length | 0 .. 100 | ``validate_schema`` array defaults |
| string length | 0 .. 4 000 | ``validate_schema`` string defaults |
| integer range | ±10**12 | ``validate_schema`` integer defaults |
| ``string()`` helper | 4 000 | the default every tool schema uses |
| provider response | 1 000 000 bytes | ``post_json`` read ceiling |
| provider timeout | 25 s | ``post_json`` default |
| memory search | 10 rows | ``memory_search`` LIMIT |
| records list | 50 rows | ``records_list`` LIMIT |
| credential reference | ``[A-Z][A-Z0-9_]*`` | ``secret`` name shape |
| tool risk level | 4 values | ``Registry.add`` |

Two of the modes are not constants but the two defects this phase fixed, and they
are in the matrix for the same reason: a fix nobody pins can be reverted silently.

The measured spec is four files rather than one, because these bounds are pinned
across more than one: ``test_adapters.py`` owns the schema and transport bounds,
``test_registration_contract.py`` owns the registration contract, and
``test_pack_contract.py`` / ``test_engine.py`` are where the memory and records
read ceilings are exercised. A single-file spec would report a bound GREEN merely
because the test that pins it lives next door -- the instrument, not the code.

Run from the repository root::

    python scripts/audit_tools_bounds.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

TARGET = 'platform_runtime/tools.py'
PATTERN = ('runtime_tests.test_adapters runtime_tests.test_registration_contract '
           'runtime_tests.test_pack_contract runtime_tests.test_engine')

MUTATIONS = [
    ('schema nesting 64',
     b'MAX_SCHEMA_DEPTH = 64',
     b'MAX_SCHEMA_DEPTH = 65'),
    ('depth guard removed',
     b'if depth>MAX_SCHEMA_DEPTH:raise ValueError(\'Schema nesting too deep\')',
     b'if depth>10**9:raise ValueError(\'Schema nesting too deep\')'),
    ('argument object 20000 bytes',
     b'MAX_ARGUMENT_BYTES = 20000',
     b'MAX_ARGUMENT_BYTES = 200000'),
    ('array maxItems default 100',
     b'DEFAULT_MAX_ITEMS = 100',
     b'DEFAULT_MAX_ITEMS = 1000'),
    ('array minItems default 0',
     b"schema.get('minItems',0)",
     b"schema.get('minItems',1)"),
    ('string maxLength default 4000',
     b'DEFAULT_STRING_LENGTH = 4000',
     b'DEFAULT_STRING_LENGTH = 40000'),
    ('string minLength default 0',
     b"schema.get('minLength',0)",
     b"schema.get('minLength',1)"),
    ('integer bound 10**12',
     b'INTEGER_BOUND = 10 ** 12',
     b'INTEGER_BOUND = 10 ** 13'),
    ('string() helper default',
     b'def string(maximum=DEFAULT_STRING_LENGTH)',
     b'def string(maximum=40000)'),
    ('provider response ceiling',
     b'MAX_PROVIDER_RESPONSE = 1_000_000',
     b'MAX_PROVIDER_RESPONSE = 10_000_000'),
    ('provider timeout 25s',
     b'PROVIDER_TIMEOUT_SECONDS = 25',
     b'PROVIDER_TIMEOUT_SECONDS = 250'),
    ('memory search LIMIT 10',
     b'MEMORY_SEARCH_LIMIT = 10',
     b'MEMORY_SEARCH_LIMIT = 1000'),
    ('records list LIMIT 50',
     b'RECORDS_LIST_LIMIT = 50',
     b'RECORDS_LIST_LIMIT = 500'),
    ('credential name shape',
     b"CREDENTIAL_NAME = re.compile(r'[A-Z][A-Z0-9_]*')",
     b"CREDENTIAL_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]*')"),
    ('tool risk level set',
     b"RISK_LEVELS = frozenset({'read', 'write', 'destructive', 'physical'})",
     b"RISK_LEVELS = frozenset({'read', 'write', 'destructive', 'physical', 'unknown'})"),
    ('duplicate name not named',
     b"raise ValueError(f'Tool already registered: {tool.name}')",
     b"raise ValueError('Invalid tool registration')"),
    ('risk refusal not named',
     b"raise ValueError(f'Unknown tool risk level: {tool.risk}')",
     b"raise ValueError('Invalid tool registration')"),
]

if __name__ == '__main__':
    sys.exit(revert_matrix.main(TARGET, PATTERN, MUTATIONS))
