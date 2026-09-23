"""Measure the boundaries of ``platform_runtime/tools.py``.

A probe, not a test: every claim below is measured through the real surface and can
be re-run and refuted. It complements the revert matrix in
``audit_tools_bounds.py`` -- the matrix asks "would anything notice if this number
changed?", and this asks "what is actually true right now?".

``tools.py`` is the gate every tool argument passes through, so the sections here
are the things it decides: what a schema accepts, which registrations are legal,
what it will read from a provider, how much it reads back, and which environment
names a credential reference may resolve.

Run the way the suite is run::

    PY="$HOME/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
    PYTHONPATH=".;$(python -c 'import site;print(site.getsitepackages()[0])')" \\
      "$PY" scripts/probes/probe_tools_boundaries.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime.engine import Engine, encode                 # noqa: E402
from platform_runtime.tools import (                               # noqa: E402
    build_registry, validate_schema, string, secret, post_json, Registry, Tool,
    MAX_SCHEMA_DEPTH, MAX_ARGUMENT_BYTES, DEFAULT_MAX_ITEMS,
    DEFAULT_STRING_LENGTH, INTEGER_BOUND, MAX_PROVIDER_RESPONSE,
    PROVIDER_TIMEOUT_SECONDS, MEMORY_SEARCH_LIMIT, RECORDS_LIST_LIMIT,
    CREDENTIAL_NAME, RISK_LEVELS)

SOURCE = Path(ROOT, 'api-python', 'platform_runtime', 'tools.py')
TEXT = SOURCE.read_bytes()

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
        check(f'{label}: {value} refused', error is not None,
              f'got {error!r}' if error else '')


def nested(levels):
    """A schema and a matching value, nested ``levels`` deep."""
    schema = {'type': 'string'}
    value = 'x'
    for _ in range(levels):
        schema = {'type': 'object', 'properties': {'a': schema}, 'required': ['a']}
        value = {'a': value}
    return schema, value


def validate_nesting(levels):
    """``validate_schema`` takes the VALUE first and the schema second."""
    schema, value = nested(levels)
    return validate_schema(value, schema)


def object_of_bytes(n):
    return {'a': 'x' * (n - 8)}


def engine():
    tmp = tempfile.TemporaryDirectory()
    return tmp, Engine(Path(tmp.name) / 'db', build_registry(),
                       lambda t, a: {'tools': [], 'ladder': 'autonomous'})


class _Response:
    def __init__(self, body): self.body = body
    def read(self, size): return self.body[:size]
    def __enter__(self): return self
    def __exit__(self, *exc): return False


class _Opener:
    def __init__(self, body, capture):
        self.body, self.capture = body, capture
    def open(self, request, timeout=None):
        if self.capture is not None:
            self.capture['timeout'] = timeout
        return _Response(self.body)


# ------------------------------------------------------------ 1. the literals

print('\n1. The literals are stated in the source, not inferred')

for label, literal in (('nesting', b'MAX_SCHEMA_DEPTH = 64'),
                       ('object bytes', b'MAX_ARGUMENT_BYTES = 20000'),
                       ('maxItems', b'DEFAULT_MAX_ITEMS = 100'),
                       ('string length', b'DEFAULT_STRING_LENGTH = 4000'),
                       ('integer bound', b'INTEGER_BOUND = 10 ** 12'),
                       ('response ceiling', b'MAX_PROVIDER_RESPONSE = 1_000_000'),
                       ('timeout', b'PROVIDER_TIMEOUT_SECONDS = 25'),
                       ('memory rows', b'MEMORY_SEARCH_LIMIT = 10'),
                       ('records rows', b'RECORDS_LIST_LIMIT = 50')):
    check(f'{label} is the literal {literal.decode()}', literal in TEXT)

check('the module now names its bounds rather than inlining them',
      TEXT.count(b'MAX_SCHEMA_DEPTH') > 1 and TEXT.count(b'DEFAULT_MAX_ITEMS') > 1)
check('the registry still holds 88 tools', len(build_registry().items) == 88,
      f'got {len(build_registry().items)}')


# ------------------------------------------------------------- 2. the schemas

print('\n2. What a schema accepts, both sides')

walk('schema nesting', validate_nesting,
     [1, 3, MAX_SCHEMA_DEPTH], [MAX_SCHEMA_DEPTH + 1])
walk('argument object bytes', lambda n: validate_schema(
        object_of_bytes(n),
        {'type': 'object', 'properties': {'a': {'type': 'string', 'maxLength': 10 ** 6}},
         'required': ['a']}),
     [20000], [20001])
walk('array length', lambda n: validate_schema(
        list(range(n)), {'type': 'array', 'items': {'type': 'integer'}}),
     [0, 100], [101])
walk('string length', lambda n: validate_schema('a' * n, {'type': 'string'}),
     [0, 4000], [4001])
walk('integer range', lambda n: validate_schema(n, {'type': 'integer'}),
     [INTEGER_BOUND, -INTEGER_BOUND], [INTEGER_BOUND + 1, -INTEGER_BOUND - 1])
# The shared helper states the same 4000, so widening it must widen the refusal.
walk('string() helper', lambda n: validate_schema('a' * n, string()), [4000], [4001])
check('string() and the schema default are the same number',
      string()['maxLength'] == DEFAULT_STRING_LENGTH == 4000)

print('\n2b. The defect this phase fixed: the guard must refuse, not crash')

# Before the fix validate_schema recursed once per level and blew the 1000-frame
# stack. The declared `arguments_json` ceiling of 12000 characters carries about
# 1999 levels, so the crash was reachable through `mcp.call` rather than theoretical.
for levels in (MAX_SCHEMA_DEPTH + 1, 1000, 1500, 1999):
    schema, value = nested(levels)
    error = refuses(lambda: validate_schema(value, schema))
    check(f'nesting {levels}: refused with the ValueError contract',
          isinstance(error, ValueError),
          f'got {type(error).__name__}' if error else 'accepted')


def nested_value(levels):
    value = 'x'
    for _ in range(levels):
        value = {'a': value}
    return value


def deepest(predicate):
    """The deepest level at which ``predicate`` still holds, found by bisection.

    Bisection rather than a scan: probing past a recursive encoder's own ceiling
    raises the very error this measurement exists to reason about, so a
    ``RecursionError`` counts as "does not hold" rather than escaping.
    """
    lo, hi = 1, 8000
    while lo < hi:
        mid = (lo + hi + 1) // 2
        try:
            holds = predicate(mid)
        except RecursionError:
            holds = False
        if holds:
            lo = mid
        else:
            hi = mid - 1
    return lo


def serialised_length(levels):
    return len(json.dumps(nested_value(levels), separators=(',', ':')))


# `encode` runs on the WHOLE value before the guard recurses, and the JSON encoder
# is itself recursive, so there is a second ceiling. Measured, then compared with
# what the declared character bound can actually carry -- the point is not to fix it
# but to know whether it is reachable. Both numbers move with the stack depth of the
# process that asks, so neither is hard-coded: they are measured here and only their
# ORDER is asserted.
def encodable(levels):
    encode(nested_value(levels))
    return True


encoder_ceiling = deepest(encodable)
carried = deepest(lambda levels: serialised_length(levels) <= 12000)
check('the JSON encoder has a depth ceiling of its own',
      encoder_ceiling > carried, f'got {encoder_ceiling}')
check('RECORDED, NOT A DEFECT: that ceiling is unreachable, because the declared '
      'arguments_json bound of 12000 characters carries fewer levels',
      carried < encoder_ceiling, f'carries {carried} < encoder {encoder_ceiling}')


# ---------------------------------------------------------- 3. the registration

print('\n3. The registration contract names its two causes')

registry = Registry()
registry.add(Tool('x.y', 'read', {}))
duplicate = refuses(lambda: registry.add(Tool('x.y', 'read', {})))
unknown_risk = refuses(lambda: registry.add(Tool('z.w', 'bogus', {})))
check('a duplicate name is refused', isinstance(duplicate, ValueError))
check('an unknown risk is refused', isinstance(unknown_risk, ValueError))
check('the two refusals do not share one message',
      str(duplicate) != str(unknown_risk),
      f'{duplicate!s} / {unknown_risk!s}')
check('the duplicate message names the tool', 'x.y' in str(duplicate), str(duplicate))
check('the risk message names the level', 'bogus' in str(unknown_risk), str(unknown_risk))
check('the four risk levels are the declared four',
      RISK_LEVELS == {'read', 'write', 'destructive', 'physical'},
      str(sorted(RISK_LEVELS)))
check('no tool in the registry uses an undeclared risk',
      all(t.risk in RISK_LEVELS for t in build_registry().items.values()))


# ----------------------------------------------------------- 4. the transport

print('\n4. What it will read from a provider')

seen = {}


def post(body):
    with patch('platform_runtime.tools.urllib.request.build_opener',
               lambda *a, **k: _Opener(body, seen)):
        return post_json('https://example.invalid/x', {})


at_limit = b'"' + b'x' * (MAX_PROVIDER_RESPONSE - 2) + b'"'
over = b'"' + b'x' * (MAX_PROVIDER_RESPONSE - 1) + b'"'
check('the measured body lengths are the ones claimed',
      len(at_limit) == 1_000_000 and len(over) == 1_000_001)
check('a body at the ceiling is accepted', refuses(lambda: post(at_limit)) is None)
check('a body one byte over is refused',
      isinstance(refuses(lambda: post(over)), ValueError))
check('the timeout handed to the transport is the literal 25',
      seen.get('timeout') == PROVIDER_TIMEOUT_SECONDS == 25,
      f'got {seen.get("timeout")}')

for label, url in (('plain http', 'http://example.invalid/x'),
                   ('credentials in the url', 'https://user:pw@example.invalid/x'),
                   ('a fragment', 'https://example.invalid/x#f')):
    error = refuses(lambda u=url: post_json(u, {}))
    check(f'{label} is refused before any I/O', isinstance(error, ValueError))


# ------------------------------------------------------ 5. the read ceilings

print('\n5. How much it reads back')

tmp, e = engine()
try:
    with e.tx() as c:
        for i in range(25):
            c.execute('INSERT INTO p_memory VALUES(?,?,?,?,?)',
                      ('a', 'ops', f'k{i}', f'needle {i}', 0))
        for i in range(60):
            c.execute('INSERT INTO p_records VALUES(?,?,?,?,?)',
                      ('a', 'kind', f'id{i}', '{}', i))
    memory = e.registry.get('memory.search').handler(
        e, 'a', 'ops', {'query': 'needle'}, 'key')
    records = e.registry.get('records.list').handler(
        e, 'a', 'ops', {'kind': 'kind'}, 'key')
    check('25 rows stored, memory.search returns exactly 10',
          len(memory['matches']) == 10 == MEMORY_SEARCH_LIMIT,
          f'got {len(memory["matches"])}')
    check('60 rows stored, records.list returns exactly 50',
          len(records['records']) == 50 == RECORDS_LIST_LIMIT,
          f'got {len(records["records"])}')
    check('records.list is newest first, so the cap drops the oldest',
          records['records'][0]['id'] == 'id59')
finally:
    tmp.cleanup()


# --------------------------------------------------------- 6. the credential

print('\n6. Which environment names a credential reference may read')

check('the pattern is the literal uppercase shape',
      CREDENTIAL_NAME.pattern == '[A-Z][A-Z0-9_]*')
for accepted in ('PLATFORM_VAULT_KEYS_JSON', 'A', 'TOKEN_ENV_2'):
    check(f'{accepted!r} is a valid reference name',
          CREDENTIAL_NAME.fullmatch(accepted) is not None)
for refused in ('lowercase', 'MixedCase', '9LEADING', '', 'WITH-DASH', 'WITH SPACE'):
    check(f'{refused!r} is refused as a reference name',
          CREDENTIAL_NAME.fullmatch(refused) is None)

os.environ['PROBE_TOOLS_KEY'] = 'probe-value'
check('a declared name reads its value',
      secret({'token_env': 'PROBE_TOOLS_KEY'}, 'token_env') == 'probe-value')
check('a lowercase name is refused by secret()',
      isinstance(refuses(lambda: secret({'token_env': 'path'}, 'token_env')), RuntimeError))
# Recorded, not fixed: the shape check constrains CASE, not namespace, so a tenant
# config may name the platform's own variables. Measured so the limitation is a
# fact rather than a suspicion.
check('RECORDED: an unrelated platform variable is also a valid name',
      CREDENTIAL_NAME.fullmatch('PLATFORM_VAULT_KEYS_JSON') is not None)


print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('failed:')
    for label in FAIL:
        print('  -', label)
sys.exit(1 if FAIL else 0)
