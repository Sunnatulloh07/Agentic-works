"""Boundary audit of the outbound provider transport layer -- the revert matrix (§151).

Four modules answer one question: **is every limit on bytes this platform did not
author pinned?** ``google_oauth``, ``model_transport``, ``model_response`` and ``mcp``
each consume a peer's data -- a provider, a local inference endpoint, a model, an MCP
server -- and each enforced its limits with inline literals.

The scan selected them for the same mechanical reason as the phases before: large
literals, no named constants, and no probe. The literals are now named, so a mutation
targets the constant's own value. That is deliberate: mutating
``MAX_REQUEST_BODY_BYTES = 64000`` to ``640000`` moves the guard, and the test that pins
it asserts the number 64000 rather than the name, so the two cannot drift together.

Enumerated bounds and where they stand after the matrix:

| Bound | Value | Site | Matrix |
|---|---|---|---|
| provider response | 1 000 000 B | ``strict_json`` | RED |
| request body | 64 000 B | ``google_transport`` | RED |
| bearer credential | 16 000 chars | ``google_transport`` | RED |
| error body | 16 000 B | ``google_transport`` | RED |
| client id | 512 chars | ``GoogleOAuth.__init__`` | RED |
| callback URI | 2 000 chars | ``GoogleOAuth.__init__`` | RED |
| expected subject | 256 chars | ``GoogleOAuth.__init__`` | RED |
| transport timeout | 25 s | ``google_transport`` | RED |
| config schema | 1 | ``public_config`` | RED |
| client id suffix | ``\\.apps\\.googleusercontent\\.com`` | ``GoogleOAuth.__init__`` | GREEN → fixed |
| secret reference | ``[A-Z][A-Z0-9_]{1,127}`` | ``GoogleOAuth.__init__`` | RED |
| local port floor | 1 024 | ``validate_url`` | RED |
| local port ceiling | 65 535 | ``validate_url`` | GREEN → recorded |
| local request | 128 000 B | ``transport_for`` | RED |
| local response | 128 000 B | ``transport_for`` | RED |
| local timeout | 30 s | ``transport_for`` | RED |
| decision text | 20 000 B | ``parse_decision`` | RED |
| JSON depth | 32 | ``parse_decision`` | RED |
| JSON nodes | 5 000 | ``parse_decision`` | RED |
| required choices | 1 | ``parse_decision`` | RED |
| MCP response | 1 000 000 B | ``MCPClient._http`` | RED |
| MCP timeout | 25 s | ``MCPClient._http`` | RED |
| MCP initial version | ``2025-03-26`` | ``MCPClient.__init__`` | RED |
| MCP accepted versions | 2 | ``MCPClient.call`` | RED |

**Result: 24 mutations, 23 red, 1 recorded, 0 unmeasured, restore verified.**

Two greens came out of the first pass and only one of them was a defect:

* ``client id suffix`` -- the rule that a client id must be a Google client id could be
  deleted outright and the whole suite stayed green. The existing ceiling test fed only
  well-formed ids, so it pinned the length and left the shape unmeasured. Fixed by
  ``test_client_id_must_be_a_google_client_id``, which re-runs RED.
* ``local port ceiling`` -- ``urlsplit`` refuses a port above 65 535 before the
  comparison runs, so widening ``MAX_PORT`` changes no outcome and no test could tell.
  This one is **recorded**, not fixed: the bound is real, its testability is not.

Three further bounds are RECORDED rather than pinned, because measurement showed the
real surface cannot reach them:

* ``MAX_CREDENTIAL_CHARS`` and ``MAX_ERROR_BODY_BYTES`` are reachable only through a
  socket, so they are pinned by asserting the size handed to ``read``, with the
  transport faked. The refusal itself is what is asserted, and the assertion would
  still hold if the guard moved.
* the MCP SSE ``data:`` prefix width is structural (``'data:'`` is 5 characters), not a
  policy number; changing it changes the protocol, and the protocol has no negotiable
  form here.

The measured spec is five files. A single-file spec would report a bound GREEN merely
because the test that pins it lives next door -- and for this layer the consumers are
the point: ``test_oauth``, ``test_local_model_transport``, ``test_model_response`` and
``test_adapters`` are where a widened bound would actually be felt.

Run from the repository root::

    python scripts/probes/audit_transport_bounds.py
    python scripts/probes/audit_transport_bounds.py --check
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

PATTERN = ('runtime_tests.test_outbound_transport_bounds runtime_tests.test_oauth '
           'runtime_tests.test_model_response runtime_tests.test_local_model_transport '
           'runtime_tests.test_adapters')

MODULES = [
    ('platform_runtime/google_oauth.py', [
        ('provider response 1000000',
         b'MAX_PROVIDER_RESPONSE_BYTES = 1_000_000',
         b'MAX_PROVIDER_RESPONSE_BYTES = 10_000_000'),
        ('request body 64000',
         b'MAX_REQUEST_BODY_BYTES = 64000',
         b'MAX_REQUEST_BODY_BYTES = 640000'),
        ('credential 16000',
         b'MAX_CREDENTIAL_CHARS = 16000',
         b'MAX_CREDENTIAL_CHARS = 160000'),
        ('error body 16000',
         b'MAX_ERROR_BODY_BYTES = 16000',
         b'MAX_ERROR_BODY_BYTES = 160000'),
        ('client id 512',
         b'MAX_CLIENT_ID_CHARS = 512',
         b'MAX_CLIENT_ID_CHARS = 5120'),
        ('callback uri 2000',
         b'MAX_REDIRECT_URI_CHARS = 2000',
         b'MAX_REDIRECT_URI_CHARS = 20000'),
        ('expected subject 256',
         b'MAX_SUBJECT_CHARS = 256',
         b'MAX_SUBJECT_CHARS = 2560'),
        ('transport timeout 25',
         b'TRANSPORT_TIMEOUT_SECONDS = 25',
         b'TRANSPORT_TIMEOUT_SECONDS = 250'),
        ('config schema 1',
         b'CONFIG_SCHEMA_VERSION = 1',
         b'CONFIG_SCHEMA_VERSION = 2'),
        ('client id suffix',
         b"r'[A-Za-z0-9._-]+\\.apps\\.googleusercontent\\.com'",
         b"r'[A-Za-z0-9._-]+'"),
        ('secret ref ceiling 127',
         b"r'[A-Z][A-Z0-9_]{1,127}'",
         b"r'[A-Z][A-Z0-9_]{1,128}'"),
    ]),
    ('platform_runtime/model_transport.py', [
        ('local port floor 1024',
         b'MIN_LOCAL_PORT = 1024',
         b'MIN_LOCAL_PORT = 0'),
        ('local port ceiling 65535',
         b'MAX_PORT = 65535',
         b'MAX_PORT = 65536'),
        ('local request 128000',
         b'MAX_LOCAL_REQUEST_BYTES = 128000',
         b'MAX_LOCAL_REQUEST_BYTES = 1280000'),
        ('local response 128000',
         b'MAX_LOCAL_RESPONSE_BYTES = 128000',
         b'MAX_LOCAL_RESPONSE_BYTES = 1280000'),
        ('local timeout 30',
         b'LOCAL_TIMEOUT_SECONDS = 30',
         b'LOCAL_TIMEOUT_SECONDS = 300'),
    ]),
    ('platform_runtime/model_response.py', [
        ('decision bytes 20000',
         b'MAX_DECISION_BYTES = 20000',
         b'MAX_DECISION_BYTES = 200000'),
        ('json depth 32',
         b'MAX_JSON_DEPTH = 32',
         b'MAX_JSON_DEPTH = 64'),
        ('json nodes 5000',
         b'MAX_JSON_NODES = 5000',
         b'MAX_JSON_NODES = 50000'),
        ('required choices 1',
         b'REQUIRED_CHOICES = 1',
         b'REQUIRED_CHOICES = 2'),
    ]),
    ('platform_runtime/mcp.py', [
        ('mcp response 1000000',
         b'MAX_RESPONSE_BYTES = 1_000_000',
         b'MAX_RESPONSE_BYTES = 10_000_000'),
        ('mcp timeout 25',
         b'TRANSPORT_TIMEOUT_SECONDS = 25',
         b'TRANSPORT_TIMEOUT_SECONDS = 250'),
        ('mcp initial version',
         b"INITIAL_PROTOCOL_VERSION = '2025-03-26'",
         b"INITIAL_PROTOCOL_VERSION = '2025-06-18'"),
        ('mcp accepted versions',
         b"SUPPORTED_PROTOCOL_VERSIONS = frozenset({'2025-03-26', '2025-06-18'})",
         b"SUPPORTED_PROTOCOL_VERSIONS = frozenset({'2025-03-26', '2025-06-18', '2024-11-05'})"),
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
