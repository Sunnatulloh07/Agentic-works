"""Boundary audit of the approval queue and the autonomy ladder -- the revert matrix (§156).

This is the last pair of modules in ``app/`` with **no coverage in any gated test**.
``LadderStore`` and ``FileApprovalStore`` are exercised only by ``tests/``, which no
gate runs and which is 33 tests red because the API it drives was deliberately
retired to ``410 Gone``.  That is §155's finding -- a pin in an ungated suite is not a
pin -- and this phase is where it is paid off.

Unlike §155, there is no *separate* consumer to measure: ``grep`` over
``runtime_tests`` and ``integration_tests`` finds no other user of these two stores,
and the app modules that import them (``main.py``, ``operator.py``, ``pipeline.py``,
``stats.py``) do so only to mount routers.  So the pattern below is one file, and the
honest statement of what that buys is this: the pins are behavioural -- they drive the
real store through real SQLite and read the real row back -- but no *independent
caller* would notice a widened bound.  That is weaker than §155, where
``test_identity_store`` was a second file, and it is recorded rather than glossed.

Enumerated bounds and what the matrix found:

| Module | Bound | Value | Before |
|---|---|---|---|
| ``ladder.py`` | promotion threshold | 30 tasks | constructor default |
| ``ladder.py`` | promotion error bar | 0.05 | constructor default |
| ``ladder.py`` | demotion error bar | 0.20 | constructor default |
| ``ladder.py`` | automatic cap | on | constructor default |
| ``ladder.py`` | history window floor | 30 | **unnamed second literal** |
| ``ladder.py`` | the window expression | ``max(MIN_WINDOW, min_tasks)`` | **unnamed** |
| ``ladder.py`` | rung order | ``LEVELS`` | unnamed |
| ``approvals.py`` | pending queue ceiling | 100 rows | default argument |
| ``approvals.py`` | reason ceiling | 200 chars | **unnamed literal** |
| ``approvals.py`` | id entropy | 12 hex chars | **unnamed literal** |
| ``approvals.py`` | decision vocabulary | 2 words | **written out twice** |
| ``approvals.py`` | phone-mask floor | 9 body chars | in a regex quantifier |
| ``approvals.py`` | phone-mask ceiling | 16 body chars | in a regex quantifier |

The two rows worth pausing on are the policy trio and the window floor.

The policy trio -- ``min_tasks``, ``max_err``, ``demote_err`` -- is not a set of
tunables.  It is the answer to "when does an agent stop needing a human", and it lived
in constructor defaults, so ``LadderStore(min_tasks=1)`` promotes an agent after one
successful task and nothing changes colour.  The matrix now shows every one of them
RED.

``MIN_WINDOW`` was a second bare ``30`` one line under ``min_tasks``'s default: two
literals that had to agree, with nothing making them.  They *do* have to agree, and
the reason is not cosmetic.  ``record`` trims history to ``[-window:]`` while
promotion needs ``total >= min_tasks``; a window below the threshold means the count
can never reach it, so the agent is never promoted -- a rule that can never fire, and
one that raises nothing.  ``max(MIN_WINDOW, min_tasks)`` is what removes the
question, and it is measured on both sides of the floor.

Two bounds are pinned behaviourally rather than by name, and both are leaks that the
tests assert AS leaks: the phone mask's ``{9,16}`` quantifier leaves a body shorter
than nine characters in the clear, and a body longer than sixteen keeps its tail.

Run from the repository root::

    python scripts/audit_approval_ladder_bounds.py --check
    python scripts/audit_approval_ladder_bounds.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

PATTERN = 'runtime_tests.test_approval_ladder_bounds'

MODULES = [
    ('app/ladder.py', [
        # --- the policy trio: when an agent stops needing a human -----------------
        ('promotion threshold 30',
         b'DEFAULT_MIN_TASKS = 30',
         b'DEFAULT_MIN_TASKS = 3'),
        ('promotion error bar 0.05',
         b'DEFAULT_MAX_ERR = 0.05',
         b'DEFAULT_MAX_ERR = 0.5'),
        ('demotion error bar 0.20',
         b'DEFAULT_DEMOTE_ERR = 0.20',
         b'DEFAULT_DEMOTE_ERR = 0.02'),
        ('automatic cap on',
         b'auto_cap: bool = True',
         b'auto_cap: bool = False'),
        # --- the window floor, and the expression that applies it ----------------
        ('history window floor 30',
         b'MIN_WINDOW = 30',
         b'MIN_WINDOW = 1'),
        ('the window takes the larger of floor and threshold',
         b'self.window = max(MIN_WINDOW, min_tasks)',
         b'self.window = min_tasks'),
        # --- the ladder itself ---------------------------------------------------
        ('rung order',
         b'LEVELS = ["human_led", "human_assisted", "autonomous"]',
         b'LEVELS = ["human_led", "human_assisted"]'),
        # --- the guards ----------------------------------------------------------
        ('promotion needs the threshold',
         b'total >= self.min_tasks',
         b'total > self.min_tasks'),
        ('the error bar is inclusive',
         b'errs / total <= self.max_err',
         b'errs / total < self.max_err'),
        ('the demotion bar is exclusive',
         b'errs / total > self.demote_err',
         b'errs / total >= self.demote_err'),
        ('no rung above the top',
         b'idx < len(LEVELS) - 1',
         b'idx <= len(LEVELS) - 1'),
        ('history is trimmed to the window',
         b'[-self.window:]',
         b'[:]'),
        ('a promotion restarts the window',
         b'entry["level"] = nxt\n'
         b'                        entry["outcomes"] = []',
         b'entry["level"] = nxt\n'
         b'                        entry["outcomes"] = entry["outcomes"]'),
        ('a demotion restarts the window',
         b'entry["level"] = LEVELS[idx - 1]\n'
         b'                    entry["outcomes"] = []',
         b'entry["level"] = LEVELS[idx - 1]\n'
         b'                    entry["outcomes"] = entry["outcomes"]'),
        ('the automatic cap is consulted',
         b'if not (self.auto_cap and nxt == "autonomous"):',
         b'if True:'),
        # --- the key, and the identifier guard -----------------------------------
        ('the key carries the tenant',
         b'return f"{tenant}:{agent_id}"',
         b'return f"{agent_id}"'),
        ('an identifier with two colons is refused',
         b'agent_id.count(":") != 1',
         b'agent_id.count(":") != 2'),
        # --- the owner endpoint --------------------------------------------------
        ('the level vocabulary is enforced',
         b'if level not in LEVELS:',
         b'if False:'),
        ('a manual move restarts the window',
         b'self._save(agent_id, level, [])',
         b'self._save(agent_id, level, ["x"])'),
    ]),
    ('app/approvals.py', [
        # --- the ceilings --------------------------------------------------------
        ('pending queue ceiling 100',
         b'MAX_PENDING_ROWS = 100',
         b'MAX_PENDING_ROWS = 1000'),
        ('reason ceiling 200',
         b'MAX_REASON_CHARS = 200',
         b'MAX_REASON_CHARS = 2000'),
        ('id entropy 12 hex characters',
         b'APPROVAL_ID_BYTES = 12',
         b'APPROVAL_ID_BYTES = 6'),
        ('the queue default is the ceiling',
         b'limit: int = MAX_PENDING_ROWS',
         b'limit: int = MAX_PENDING_ROWS * 10'),
        ('the reason is stored up to the ceiling',
         b'[:MAX_REASON_CHARS]',
         b'[:MAX_REASON_CHARS * 2]'),
        ('the id slice uses the declared entropy',
         b'hex[:APPROVAL_ID_BYTES]',
         b'hex[:APPROVAL_ID_BYTES // 2]'),
        ('the id carries the tenant',
         b'id=f"{tenant}-{uuid.uuid4().hex[:APPROVAL_ID_BYTES]}"',
         b'id=f"{uuid.uuid4().hex[:APPROVAL_ID_BYTES]}"'),
        # --- the decision vocabulary ---------------------------------------------
        ('one vocabulary, one spelling',
         b'DECISIONS = ("approved", "rejected")',
         b'DECISIONS = ("approved", "rejected", "maybe")'),
        ('the store enforces the vocabulary',
         b'if decision not in DECISIONS:',
         b'if False:'),
        ('the route enforces the vocabulary',
         b'if req.decision not in DECISIONS:',
         b'if False:'),
        ('the stored status is the decision',
         b'(decision, int(time.time()), actor,',
         b'(DECISIONS[0], int(time.time()), actor,'),
        # --- the phone mask ------------------------------------------------------
        # The operand is part of the pattern because ``{9,16}`` on its own also
        # appears in the comment above the mask, and a mutation that matches twice
        # measures nothing -- the matrix reports it AMBIGUOUS for exactly this reason.
        ('the mask floor 9',
         b'[\\d\\s\\-()]{9,16}',
         b'[\\d\\s\\-()]{8,16}'),
        ('the mask ceiling 16',
         b'[\\d\\s\\-()]{9,16}',
         b'[\\d\\s\\-()]{9,20}'),
        ('the mask is bounded at all',
         b'[\\d\\s\\-()]{9,16}',
         b'[\\d\\s\\-()]{9,}'),
        ('the mask matches the country code',
         b'\\+998[\\d\\s\\-()]',
         b'\\+997[\\d\\s\\-()]'),
        ('the queue masks what it shows',
         b'_mask_phone(r["summary"] or "")',
         b'(r["summary"] or "")'),
        # --- the decide path -----------------------------------------------------
        ('the reason is sanitised',
         b're.sub(r"[<>]", "", reason or "")',
         b'(reason or "")'),
        ('an unknown approval is not found',
         b'raise LookupError("not found")',
         b'pass'),
        ('an already decided approval is refused',
         b'if r["status"] != "pending":',
         b'if False:'),
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
