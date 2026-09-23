"""Boundary audit of the planner, speech and database-read layer -- the revert matrix (§152).

The same question as §151, asked of the four modules the inventory listed as having
neither a probe nor a phase: ``agent_planner``, ``speech``, ``postgres_connector`` and
``crm/crm_reconcile``. Each is a place where a number decided something -- how much
context a model may see, how much audio may be posted, how many rows may come back, how
many candidate records may be fetched before an exact-match rule runs.

The literals are now named constants, so a mutation targets the constant's own value.
That is deliberate: mutating ``MAX_CELL_BYTES = 16000`` to ``160000`` moves the guard,
and the test that pins it asserts the number 16000 rather than the name, so the two
cannot drift together.

Enumerated bounds and where they stand after the matrix:

| Bound | Value | Site | Matrix |
|---|---|---|---|
| planner model name | 256 chars | ``ResultPlanner.__call__`` | RED |
| planner output tokens | 1 600 | ``ResultPlanner.__call__`` | RED |
| planner context | 64 000 B | ``ResultPlanner.__call__`` | RED |
| speech response | 1 000 000 B | ``speech.http`` | RED |
| speech timeout | 60 s | ``speech.http`` | RED |
| TTS characters | 1 000 | ``AishaREST.synthesize`` | RED |
| speech speed floor | 0.5 | ``AishaREST.synthesize`` | RED |
| speech speed ceiling | 2.0 | ``AishaREST.synthesize`` | RED |
| audio path | 1 000 chars | ``AishaREST.synthesize`` | RED |
| transcript | 80 000 chars | ``AishaREST.transcribe`` | RED |
| audio payload | 1 000 000 B | ``AishaREST.transcribe`` | RED |
| database host | 253 chars | ``validate_config`` | RED |
| database port floor | 1 | ``validate_config`` | RED |
| database port ceiling | 65 535 | ``validate_config`` | RED |
| database default port | 5 432 | ``validate_config`` | RED |
| table ceiling | 100 | ``validate_config`` | RED |
| column ceiling | 40 | ``validate_config`` | RED |
| filter length | 1 000 chars | ``compile_read`` | RED |
| default limit | 50 | ``compile_read`` | RED |
| maximum limit | 100 | ``compile_read`` | RED |
| cell size | 16 000 B | ``read`` | RED |
| result size | 80 000 B | ``read`` | RED |
| connect timeout | 5 s | ``read`` | RED |
| statement timeout | 2 000 ms | ``read`` | RED |
| lock timeout | 1 000 ms | ``read`` | RED |
| identifier ceiling | 63 chars | ``NAME`` | RED |
| candidate matches | 5 | ``CRMReconciler`` | RED |
| exact matches required | 1 | ``CRMReconciler`` | RED |

**Result: 28 mutations, 28 red, 0 unpinned, 0 unmeasured, restore verified on all four
targets.** No bound in this phase came out GREEN, which is the first time that has
happened -- and it is a property of the modules, not of the instrument: each of these
numbers already refused something, and the only thing missing was a test that stated it.

Two bounds needed a different *shape* of assertion, and the shape is the finding:

* ``DEFAULT_PORT`` is only reachable as the *absence* of a port in configuration, so a
  mutation is detected by a value assertion rather than by a refusal. That test reads
  differently from its neighbours on purpose.
* ``MIN_SPEED`` is the boundary where the module stops trusting the caller. The
  neighbouring ``bool`` rejection is asserted separately because ``True`` is an ``int``
  in Python and would otherwise pass a naive numeric range check -- a defect this module
  already avoided, and now pins.

The control in this phase is **not green**, and that is recorded rather than worked
around: ``test_foundation_v02`` contains a POSIX mount test that Windows cannot satisfy.
``revert_matrix`` therefore accepts a baseline *signature* (``AUTO_BASELINE``) and
requires each mutation to change it, which is stricter than requiring green -- a mutation
that removed the pre-existing error would also be caught.

The measured spec is six files. A single-file spec would report a bound GREEN merely
because the test that pins it lives next door: ``test_agent_planner``, ``test_telephony``,
``test_foundation_v02``, ``test_postgres_contract`` and ``test_crm_reconcile`` are where
a widened bound would actually be felt.

Run from the repository root::

    python scripts/audit_planner_speech_db_bounds.py
    python scripts/audit_planner_speech_db_bounds.py --check
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

PATTERN = ('runtime_tests.test_planner_speech_db_bounds runtime_tests.test_agent_planner '
           'runtime_tests.test_telephony runtime_tests.test_foundation_v02 '
           'runtime_tests.test_postgres_contract runtime_tests.test_crm_reconcile')

MODULES = [
    ('platform_runtime/agent_planner.py', [
        ('planner model name 256',
         b'MAX_MODEL_NAME_CHARS = 256',
         b'MAX_MODEL_NAME_CHARS = 2560'),
        ('planner output tokens 1600',
         b'MAX_OUTPUT_TOKENS = 1600',
         b'MAX_OUTPUT_TOKENS = 16000'),
        ('planner context 64000',
         b'MAX_CONTEXT_BYTES = 64000',
         b'MAX_CONTEXT_BYTES = 640000'),
    ]),
    ('platform_runtime/speech.py', [
        ('speech response 1000000',
         b'MAX_RESPONSE_BYTES = 1_000_000',
         b'MAX_RESPONSE_BYTES = 10_000_000'),
        ('speech timeout 60',
         b'TRANSPORT_TIMEOUT_SECONDS = 60',
         b'TRANSPORT_TIMEOUT_SECONDS = 600'),
        ('tts chars 1000',
         b'MAX_TTS_CHARS = 1000',
         b'MAX_TTS_CHARS = 10000'),
        ('speed floor 0.5',
         b'MIN_SPEED = 0.5',
         b'MIN_SPEED = 0.0'),
        ('speed ceiling 2.0',
         b'MAX_SPEED = 2.0',
         b'MAX_SPEED = 20.0'),
        ('audio path 1000',
         b'MAX_AUDIO_PATH_CHARS = 1000',
         b'MAX_AUDIO_PATH_CHARS = 10000'),
        ('transcript 80000',
         b'MAX_TRANSCRIPT_CHARS = 80_000',
         b'MAX_TRANSCRIPT_CHARS = 800_000'),
        ('audio payload 1000000',
         b'MAX_AUDIO = 1_000_000',
         b'MAX_AUDIO = 10_000_000'),
    ]),
    ('platform_runtime/postgres_connector.py', [
        ('host 253',
         b'MAX_HOST_CHARS = 253',
         b'MAX_HOST_CHARS = 2530'),
        ('port floor 1',
         b'MIN_PORT = 1',
         b'MIN_PORT = 0'),
        ('port ceiling 65535',
         b'MAX_PORT = 65535',
         b'MAX_PORT = 65536'),
        ('default port 5432',
         b'DEFAULT_PORT = 5432',
         b'DEFAULT_PORT = 5433'),
        ('tables 100',
         b'MAX_TABLES = 100',
         b'MAX_TABLES = 1000'),
        ('columns 40',
         b'MAX_COLUMNS = 40',
         b'MAX_COLUMNS = 400'),
        ('filter 1000',
         b'MAX_FILTER_CHARS = 1000',
         b'MAX_FILTER_CHARS = 10000'),
        ('default limit 50',
         b'DEFAULT_LIMIT = 50',
         b'DEFAULT_LIMIT = 500'),
        ('max limit 100',
         b'MAX_LIMIT = 100',
         b'MAX_LIMIT = 1000'),
        ('cell 16000',
         b'MAX_CELL_BYTES = 16000',
         b'MAX_CELL_BYTES = 160000'),
        ('result 80000',
         b'MAX_RESULT_BYTES = 80000',
         b'MAX_RESULT_BYTES = 800000'),
        ('connect timeout 5',
         b'CONNECT_TIMEOUT_SECONDS = 5',
         b'CONNECT_TIMEOUT_SECONDS = 50'),
        ('statement timeout 2000',
         b'STATEMENT_TIMEOUT_MS = 2000',
         b'STATEMENT_TIMEOUT_MS = 20000'),
        ('lock timeout 1000',
         b'LOCK_TIMEOUT_MS = 1000',
         b'LOCK_TIMEOUT_MS = 10000'),
        ('identifier 63',
         b"NAME=re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')",
         b"NAME=re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')"),
    ]),
    ('platform_runtime/crm/crm_reconcile.py', [
        ('candidate matches 5',
         b'MAX_CANDIDATE_MATCHES = 5',
         b'MAX_CANDIDATE_MATCHES = 50'),
        ('exact matches required 1',
         b'EXACT_MATCHES_REQUIRED = 1',
         b'EXACT_MATCHES_REQUIRED = 2'),
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
        code |= revert_matrix.main(target, PATTERN, mutations,
                                   baseline_signature=revert_matrix.AUTO_BASELINE)
    sys.exit(code)
