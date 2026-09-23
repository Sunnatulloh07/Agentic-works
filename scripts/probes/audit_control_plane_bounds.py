"""Boundary audit of the control plane -- the revert matrix (§153).

``app/platform_api.py`` is the single surface every tenant-facing write passes through:
717 lines, 88 ``Field(...)`` declarations, and until this phase every one of their limits
was an inline literal (``le=10``, ``max_length=500``). An inline literal cannot be
addressed by a test, so the question this matrix asks is blunt: *can the ceiling on how
hard automation may chase a customer be widened without a single test going red?*

The answer before this phase was yes for all of them, because no test named a number at
all -- ``grep -l 'le=10' runtime_tests/*.py`` matched nothing. The inventory listed
``platform_api.py`` as the largest unaudited module for exactly this reason.

The bounds are now named constants, and the mutation targets the constant's own value.
That is deliberate: mutating ``MAX_REENGAGEMENT_PER_CYCLE = 20`` to ``200`` moves the
guard, and the test that pins it asserts the number 20 rather than the name, so the two
cannot drift together.

Two mutation shapes appear, and the distinction is the finding:

* **ceilings and floors** (``MAX_*``, ``MIN_*``) widen or narrow a range. A green result
  means no test observed the range at all.
* **defaults** (``DEFAULT_*``) are not limits but policy: the value the platform picks
  when an operator does not choose one. A default is what the system does *by itself*,
  so it is arguably the more dangerous of the two, and it is asserted by reading the
  constructed model back rather than by expecting a refusal.

Enumerated bounds and where they stand after the matrix are tabulated in the phase
record (``docs/development/QOLGAN-ISHLAR-INVENTAR-UZ.md``, §9).

The measured spec is ``runtime_tests.test_control_plane_bounds`` alone, and that is a
deliberate correction to the earlier phases' habit of padding the spec. Those phases
included neighbouring contract modules because a bound could plausibly be pinned next
door. Here the blast radius was measured before it was assumed, and the result is the
finding: **widening any bound in this file is invisible to every runtime contract
module.** ``test_reengagement``, ``test_escalation``, ``test_briefing`` and
``test_engine`` exercise the loops behind these endpoints, not the HTTP contract in
front of them, so they stay green no matter how wide the ceiling becomes -- which is
precisely why the ceilings went unnamed for so long, and precisely why
``test_control_plane_bounds`` had to be written. Padding the spec with four modules that
cost 70 s each to prove a point they do not make would turn a two-minute matrix into a
seventy-minute one.

Run from the repository root::

    python scripts/audit_control_plane_bounds.py
    python scripts/audit_control_plane_bounds.py --check
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

PATTERN = 'runtime_tests.test_control_plane_bounds'

MODULES = [
    ('app/platform_api.py', [
        # --- the non-empty floor, which every identifier field shares ----------
        ('non-empty floor 1',
         b'MIN_NON_EMPTY = 1',
         b'MIN_NON_EMPTY = 0'),

        # --- text ceilings ----------------------------------------------------
        ('identifier 128',
         b'MAX_IDENTIFIER_CHARS = 128',
         b'MAX_IDENTIFIER_CHARS = 1280'),
        ('key 256',
         b'MAX_KEY_CHARS = 256',
         b'MAX_KEY_CHARS = 2560'),
        ('external id 256',
         b'MAX_EXTERNAL_ID_CHARS = 256',
         b'MAX_EXTERNAL_ID_CHARS = 2560'),
        ('evidence 500',
         b'MAX_EVIDENCE_CHARS = 500',
         b'MAX_EVIDENCE_CHARS = 5000'),
        ('text 4000',
         b'MAX_TEXT_CHARS = 4000',
         b'MAX_TEXT_CHARS = 40000'),
        ('query 500',
         b'MAX_QUERY_CHARS = 500',
         b'MAX_QUERY_CHARS = 5000'),
        ('question 2000',
         b'MAX_QUESTION_CHARS = 2000',
         b'MAX_QUESTION_CHARS = 20000'),
        ('display name 256',
         b'MAX_DISPLAY_NAME_CHARS = 256',
         b'MAX_DISPLAY_NAME_CHARS = 2560'),
        ('content 100000',
         b'MAX_CONTENT_CHARS = 100_000',
         b'MAX_CONTENT_CHARS = 1_000_000'),
        ('briefing title 120',
         b'MAX_BRIEFING_TITLE_CHARS = 120',
         b'MAX_BRIEFING_TITLE_CHARS = 1200'),
        ('section ref 64',
         b'MAX_SECTION_REF_CHARS = 64',
         b'MAX_SECTION_REF_CHARS = 640'),

        # --- vector and knowledge ceilings ------------------------------------
        ('vector dimension 1024',
         b'MAX_VECTOR_DIMENSION = 1024',
         b'MAX_VECTOR_DIMENSION = 10240'),
        ('vectors per document 224',
         b'MAX_VECTORS_PER_DOCUMENT = 224',
         b'MAX_VECTORS_PER_DOCUMENT = 2240'),
        ('knowledge results 5',
         b'MAX_KNOWLEDGE_RESULTS = 5',
         b'MAX_KNOWLEDGE_RESULTS = 50'),
        ('knowledge results floor 1',
         b'MIN_KNOWLEDGE_RESULTS = 1',
         b'MIN_KNOWLEDGE_RESULTS = 0'),
        ('dimension ceiling 1024',
         b'MAX_DIMENSION = 1024',
         b'MAX_DIMENSION = 10240'),
        ('version ceiling',
         b'MAX_VERSION = 2 ** 31',
         b'MAX_VERSION = 2 ** 32'),
        ('delete version floor 1',
         b'MIN_DELETE_VERSION = 1',
         b'MIN_DELETE_VERSION = 0'),

        # --- money ------------------------------------------------------------
        ('order total 10**15',
         b'MAX_MONEY_MINOR = 10 ** 15',
         b'MAX_MONEY_MINOR = 10 ** 16'),
        ('budget limit 10**15',
         b'MAX_LIMIT_MICRO = 10 ** 15',
         b'MAX_LIMIT_MICRO = 10 ** 16'),
        ('max inflight 100',
         b'MAX_MAX_INFLIGHT = 100',
         b'MAX_MAX_INFLIGHT = 1000'),

        # --- step ceilings ----------------------------------------------------
        ('submit steps 20',
         b'MAX_SUBMIT_STEPS = 20',
         b'MAX_SUBMIT_STEPS = 200'),
        ('sections 12',
         b'MAX_SECTIONS = 12',
         b'MAX_SECTIONS = 120'),
        ('agent run steps 12',
         b'MAX_AGENT_RUN_STEPS = 12',
         b'MAX_AGENT_RUN_STEPS = 120'),
        ('supervisor hops 3',
         b'MAX_SUPERVISOR_HOPS = 3',
         b'MAX_SUPERVISOR_HOPS = 30'),
        ('keywords per section 20',
         b'MAX_KEYWORDS_PER_SECTION = 20',
         b'MAX_KEYWORDS_PER_SECTION = 200'),

        # --- wall clock -------------------------------------------------------
        ('max seconds 86400',
         b'MAX_MAX_SECONDS = 86_400',
         b'MAX_MAX_SECONDS = 864_000'),
        ('max seconds floor 60',
         b'MIN_MAX_SECONDS = 60',
         b'MIN_MAX_SECONDS = 6'),
        ('schedule interval ceiling',
         b'MAX_SCHEDULE_INTERVAL_SECONDS = 31_536_000',
         b'MAX_SCHEDULE_INTERVAL_SECONDS = 315_360_000'),
        ('schedule interval floor',
         b'MIN_SCHEDULE_INTERVAL_SECONDS = 60',
         b'MIN_SCHEDULE_INTERVAL_SECONDS = 6'),

        # --- re-engagement autonomy ceilings ----------------------------------
        ('inactive minutes ceiling',
         b'MAX_INACTIVE_MINUTES = 20_160',
         b'MAX_INACTIVE_MINUTES = 201_600'),
        ('cooldown ceiling',
         b'MAX_COOLDOWN_SECONDS = 2_592_000',
         b'MAX_COOLDOWN_SECONDS = 25_920_000'),
        ('cooldown floor',
         b'MIN_COOLDOWN_SECONDS = 300',
         b'MIN_COOLDOWN_SECONDS = 30'),
        ('reengagement attempts 10',
         b'MAX_REENGAGEMENT_ATTEMPTS = 10',
         b'MAX_REENGAGEMENT_ATTEMPTS = 100'),
        ('reengagement per cycle 20',
         b'MAX_REENGAGEMENT_PER_CYCLE = 20',
         b'MAX_REENGAGEMENT_PER_CYCLE = 200'),
        ('cycle interval ceiling',
         b'MAX_CYCLE_INTERVAL_SECONDS = 604_800',
         b'MAX_CYCLE_INTERVAL_SECONDS = 6_048_000'),
        ('cycle interval floor',
         b'MIN_CYCLE_INTERVAL_SECONDS = 300',
         b'MIN_CYCLE_INTERVAL_SECONDS = 30'),

        # --- escalation autonomy ceilings -------------------------------------
        ('escalation per cycle 50',
         b'MAX_ESCALATION_PER_CYCLE = 50',
         b'MAX_ESCALATION_PER_CYCLE = 500'),
        ('max age days 365',
         b'MAX_MAX_AGE_DAYS = 365',
         b'MAX_MAX_AGE_DAYS = 3650'),

        # --- briefing ceilings ------------------------------------------------
        ('hour 23',
         b'MAX_HOUR = 23',
         b'MAX_HOUR = 230'),
        ('minute 59',
         b'MAX_MINUTE = 59',
         b'MAX_MINUTE = 590'),
        ('timezone offset 1440',
         b'MAX_TIMEZONE_OFFSET_MINUTES = 1440',
         b'MAX_TIMEZONE_OFFSET_MINUTES = 14400'),
        ('briefing rows 50',
         b'MAX_BRIEFING_ROWS = 50',
         b'MAX_BRIEFING_ROWS = 500'),

        # --- defaults: what the platform does when nobody chooses -------------
        ('default inactive minutes',
         b'DEFAULT_INACTIVE_MINUTES = 120',
         b'DEFAULT_INACTIVE_MINUTES = 1200'),
        ('default cooldown',
         b'DEFAULT_COOLDOWN_SECONDS = 86_400',
         b'DEFAULT_COOLDOWN_SECONDS = 864_000'),
        ('default attempts',
         b'DEFAULT_REENGAGEMENT_ATTEMPTS = 2',
         b'DEFAULT_REENGAGEMENT_ATTEMPTS = 20'),
        ('default per cycle',
         b'DEFAULT_REENGAGEMENT_PER_CYCLE = 5',
         b'DEFAULT_REENGAGEMENT_PER_CYCLE = 50'),
        ('default cycle interval',
         b'DEFAULT_CYCLE_INTERVAL_SECONDS = 3600',
         b'DEFAULT_CYCLE_INTERVAL_SECONDS = 36000'),
        ('default escalation per cycle',
         b'DEFAULT_ESCALATION_PER_CYCLE = 10',
         b'DEFAULT_ESCALATION_PER_CYCLE = 100'),
        ('default max age days',
         b'DEFAULT_MAX_AGE_DAYS = 30',
         b'DEFAULT_MAX_AGE_DAYS = 300'),
        ('default hour',
         b'DEFAULT_HOUR = 8',
         b'DEFAULT_HOUR = 80'),
        ('default briefing rows',
         b'DEFAULT_BRIEFING_ROWS = 5',
         b'DEFAULT_BRIEFING_ROWS = 50'),
        ('default knowledge results',
         b'DEFAULT_KNOWLEDGE_RESULTS = 4',
         b'DEFAULT_KNOWLEDGE_RESULTS = 40'),
        ('default max inflight',
         b'DEFAULT_MAX_INFLIGHT = 4',
         b'DEFAULT_MAX_INFLIGHT = 40'),
        ('default max seconds',
         b'DEFAULT_MAX_SECONDS = 1800',
         b'DEFAULT_MAX_SECONDS = 18000'),
        ('default agent run steps',
         b'DEFAULT_AGENT_RUN_STEPS = 6',
         b'DEFAULT_AGENT_RUN_STEPS = 60'),
        ('default max sections',
         b'DEFAULT_MAX_SECTIONS = 6',
         b'DEFAULT_MAX_SECTIONS = 60'),
        ('default supervisor steps',
         b'DEFAULT_SUPERVISOR_STEPS = 6',
         b'DEFAULT_SUPERVISOR_STEPS = 60'),
        ('default reengagement steps',
         b'DEFAULT_REENGAGEMENT_STEPS = 4',
         b'DEFAULT_REENGAGEMENT_STEPS = 40'),
        ('default timezone offset',
         b'DEFAULT_TIMEZONE_OFFSET_MINUTES = 300',
         b'DEFAULT_TIMEZONE_OFFSET_MINUTES = 3000'),
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
