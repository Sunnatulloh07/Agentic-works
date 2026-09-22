"""Declared bounds of the approval queue and the autonomy ladder (§156).

These two modules were the last pair in ``app/`` with **no coverage in any gated
test**.  The legacy ``tests/`` suite exercises both -- ``tests/conftest.py`` patches
``approvals._store``, ``approvals._outbox``, ``approvals._ladder`` and
``ladder._ladder_singleton`` -- and no gate runs ``tests/``: it is 33 tests red
because the API it drives was deliberately retired to ``410 Gone``.  So the whole of
``LadderStore`` and ``FileApprovalStore`` was pinned only in a suite nobody runs.
That is §155's finding, and this module is where it is paid off.

What the bounds are, and why each one is here rather than left as a literal:

* ``ladder``'s three policy numbers -- ``min_tasks=30``, ``max_err=0.05``,
  ``demote_err=0.20`` -- decide when an agent stops needing a human.  They were
  constructor defaults, so ``LadderStore(min_tasks=1)`` promotes an agent after a
  single successful task and nothing changes colour: the only signal is that agents
  became autonomous sooner.
* ``MIN_WINDOW`` was a second bare ``30`` one line under ``min_tasks``'s default --
  two literals that had to agree, with nothing making them.  They *do* have to
  agree, and this module proves why: ``record`` trims history to ``[-window:]``
  while promotion needs ``total >= min_tasks``, so a window below the threshold
  makes promotion **unreachable**, and a rule that can never fire raises nothing.
* ``MAX_PENDING_ROWS`` and ``MAX_REASON_CHARS`` were default arguments; the first
  decides how much pending work an operator is shown, the second bounds text that is
  stored and rendered back.
* ``DECISIONS`` was written out twice -- the store raises ``ValueError``, the route
  answers ``422``, and nothing forced the two to agree about what a decision is.

Two things this module deliberately does NOT claim:

* the phone mask is pinned behaviourally rather than by name.  Its ``{9,16}``
  quantifier is a PII bound, but naming it would mean building the pattern from an
  f-string -- a readable regex traded for a name nobody reads.  Feeding it one
  character either side of the floor proves the same thing, and it is pinned that
  way here.  The edges are leaks, and they are asserted as leaks.
* ``approvals.decide``'s route guard is driven directly, not over HTTP.  This module
  lives in ``runtime_tests``, which ``scripts/verify_offline.py`` runs behind an
  audit hook that raises on ``socket.connect``: ``TestClient`` would turn the offline
  gate red.  The route is a plain function, so calling it with a header stub measures
  the guard rather than a framework's round-trip through it.
"""

import inspect
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

from app import approvals, ladder
from app.storage import reset

APP = Path(approvals.__file__).resolve().parent


def source(module):
    with Path(module.__file__).resolve().open(encoding='utf-8', newline='') as handle:
        return handle.read()


def digits(count):
    """A deterministic digit run of the requested length."""
    return ''.join(str(i % 10) for i in range(count))


def method_body(text, name):
    """The body of a top-level ``def name`` in ``text``, up to the next ``def``.

    The docstring is dropped.  It quotes the same numbers the guards now name -- and
    a check for stray literals in a guard should look at the code, not at the prose
    that explains it.
    """
    start = re.search(r'^    def ' + re.escape(name) + r'\(', text, re.M)
    assert start, f'{name} not found'
    rest = text[start.end():]
    end = re.search(r'^    def ', rest, re.M)
    body = rest[:end.start()] if end else rest
    return re.sub(r'^.*?"""(?:.|\n)*?"""\n', '', body, count=1, flags=re.M)


class _Request:
    """The only thing the admin guards ask of a request: its headers."""

    def __init__(self, headers=None):
        self.headers = headers or {}


class FreshDatabaseTests(unittest.TestCase):
    """Base case: one database file per test, and the stores bound to it.

    ``storage.reset()`` closes the connection; it does not delete the file.  So a
    shared ``APP_DB`` makes each test start from whatever the last one left behind.
    The first draft of the probe behind this module did exactly that and reported a
    ladder level that an earlier scenario had promoted as if it were policy -- the
    same class of mistake as §155's misattributed platform row.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {
            'APP_DB': str(Path(self.tmp.name) / 'app.db'),
            'ENV': 'test',
            'ALLOW_INSECURE_DEV': 'true',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        # Dev-open, so ``scope_ok`` passes and the route reaches its own guards
        # rather than stopping at the admin check.
        os.environ.pop('ADMIN_TOKEN', None)
        reset()
        self.addCleanup(reset)

        self.store = approvals.FileApprovalStore()
        self.ladder = ladder.LadderStore()
        self.addCleanup(setattr, approvals, '_store', approvals._store)
        approvals._store = self.store

    def submit(self, summary='probe', tenant='demo-retail', payload=None, kind='probe'):
        return self.store.submit(tenant, kind, 'ops.probe', summary,
                                 {'n': 1} if payload is None else payload)

    def decide_via_route(self, approval_id, decision, reason=''):
        return approvals.decide(approval_id, approvals.DecideRequest(decision=decision,
                                                                    reason=reason),
                                _Request(), None)


class DeclaredBoundTests(unittest.TestCase):
    """Every promoted number, pinned to its literal value."""

    def test_approval_queue_bounds(self):
        self.assertEqual(approvals.MAX_PENDING_ROWS, 100)
        self.assertEqual(approvals.MAX_REASON_CHARS, 200)
        self.assertEqual(approvals.APPROVAL_ID_BYTES, 12)

    def test_the_decision_vocabulary(self):
        self.assertEqual(approvals.DECISIONS, ('approved', 'rejected'))

    def test_the_ladder_policy_defaults(self):
        self.assertEqual(ladder.DEFAULT_MIN_TASKS, 30)
        self.assertEqual(ladder.DEFAULT_MAX_ERR, 0.05)
        self.assertEqual(ladder.DEFAULT_DEMOTE_ERR, 0.20)
        self.assertEqual(ladder.MIN_WINDOW, 30)

    def test_the_promotion_bar_is_below_the_demotion_bar(self):
        """Hysteresis.  If they crossed, one history would promote and demote at once.

        The two thresholds are read against the same window, so a promotion bar at or
        above the demotion bar would make an agent's level depend on which branch of
        the same ``if`` ran first.
        """
        self.assertLess(ladder.DEFAULT_MAX_ERR, ladder.DEFAULT_DEMOTE_ERR)

    def test_the_history_window_covers_the_promotion_threshold(self):
        """The invariant that keeps promotion reachable.

        ``record`` trims to ``[-window:]`` and promotion needs ``total >= min_tasks``.
        A window shorter than the threshold means the count can never reach it, so the
        agent is never promoted -- a dead rule, and one that raises nothing.
        """
        self.assertGreaterEqual(ladder.MIN_WINDOW, ladder.DEFAULT_MIN_TASKS)
        for min_tasks in (1, 5, 29, 30, 31, 100):
            with self.subTest(min_tasks=min_tasks):
                store = ladder.LadderStore(min_tasks=min_tasks)
                self.assertGreaterEqual(store.window, store.min_tasks)

    def test_the_defaults_are_the_constructor_defaults(self):
        parameters = inspect.signature(ladder.LadderStore.__init__).parameters
        self.assertEqual(parameters['min_tasks'].default, ladder.DEFAULT_MIN_TASKS)
        self.assertEqual(parameters['max_err'].default, ladder.DEFAULT_MAX_ERR)
        self.assertEqual(parameters['demote_err'].default, ladder.DEFAULT_DEMOTE_ERR)
        self.assertIs(parameters['auto_cap'].default, True)

    def test_the_queue_default_is_the_declared_ceiling(self):
        parameters = inspect.signature(approvals.FileApprovalStore.pending).parameters
        self.assertEqual(parameters['limit'].default, approvals.MAX_PENDING_ROWS)

    def test_the_policy_numbers_are_usable_as_thresholds(self):
        self.assertGreater(ladder.DEFAULT_MIN_TASKS, 0)
        self.assertGreater(ladder.DEFAULT_MAX_ERR, 0.0)
        self.assertLess(ladder.DEFAULT_MAX_ERR, 1.0)
        self.assertGreater(ladder.DEFAULT_DEMOTE_ERR, 0.0)
        self.assertLessEqual(ladder.DEFAULT_DEMOTE_ERR, 1.0)
        self.assertGreater(approvals.APPROVAL_ID_BYTES, 0)


class PhoneMaskTests(unittest.TestCase):
    """The PII mask, pinned at both edges -- where both edges leak.

    ``PHONE_MASK`` is ``\\+998[\\d\\s\\-()]{9,16}``.  The quantifier is a bound on how
    many body characters the mask will consume, and it has a floor as well as a
    ceiling.  Neither edge is a defect introduced by naming these bounds; both were
    already there, and both are the reason the number is worth writing down.
    """

    def test_a_body_at_the_floor_is_masked(self):
        body = digits(9)
        self.assertEqual('+998***', approvals._mask_phone('+998' + body))

    def test_a_body_one_below_the_floor_is_left_in_the_clear(self):
        """The floor leak: a short number is not masked at all.

        Nine body characters is the shortest real body after ``+998``, so anything
        shorter is assumed not to be a phone number and passes through untouched.
        Asserted as a leak rather than as correct behaviour -- a truncated or
        partially-typed number reaches the operator UI verbatim.
        """
        body = digits(8)
        self.assertEqual('+998' + body, approvals._mask_phone('+998' + body))

    def test_a_body_at_the_ceiling_is_masked(self):
        self.assertEqual('+998***', approvals._mask_phone('+998' + digits(16)))

    def test_a_body_one_past_the_ceiling_keeps_its_tail(self):
        """The ceiling leak: the mask consumes sixteen characters and stops.

        The quantifier is bounded, so a longer digit run is masked only up to the
        ceiling and the remainder survives into the summary.
        """
        body = digits(17)
        self.assertEqual('+998***' + body[16], approvals._mask_phone('+998' + body))

    def test_the_separators_a_phone_is_written_with_are_consumed(self):
        for written in ('+998 90 123 45 67', '+998(90)123-45-67', '+998-90-123-45-67'):
            with self.subTest(written=written):
                self.assertEqual('+998***', approvals._mask_phone(written))

    def test_text_without_a_phone_is_returned_unchanged(self):
        for text in ('order 42 for Acme', '', '998 90 123 45 67'):
            with self.subTest(text=text):
                self.assertEqual(text, approvals._mask_phone(text))


class ApprovalQueueTests(FreshDatabaseTests):
    def test_the_ceiling_bounds_what_an_operator_is_shown(self):
        for index in range(approvals.MAX_PENDING_ROWS + 5):
            self.submit(f'summary {index}')
        self.assertEqual(approvals.MAX_PENDING_ROWS, len(self.store.pending('demo-retail')))

    def test_a_queue_at_the_ceiling_is_shown_whole(self):
        for index in range(approvals.MAX_PENDING_ROWS):
            self.submit(f'summary {index}')
        self.assertEqual(approvals.MAX_PENDING_ROWS, len(self.store.pending('demo-retail')))

    def test_a_limit_of_zero_shows_nothing(self):
        """``LIMIT 0`` is a legal query, not an error -- and not "no limit"."""
        for index in range(5):
            self.submit(f'summary {index}')
        self.assertEqual([], self.store.pending('demo-retail', limit=0))

    def test_an_explicit_limit_is_honoured(self):
        for index in range(5):
            self.submit(f'summary {index}')
        self.assertEqual(1, len(self.store.pending('demo-retail', limit=1)))

    def test_one_tenants_queue_is_not_anothers(self):
        self.submit('mine', tenant='tenant-a')
        self.submit('theirs', tenant='tenant-b')
        self.assertEqual(['mine'], [r['summary'] for r in self.store.pending('tenant-a')])
        self.assertEqual(['theirs'], [r['summary'] for r in self.store.pending('tenant-b')])
        self.assertEqual([], self.store.pending('tenant-c'))

    def test_a_decided_approval_leaves_the_queue(self):
        approval = self.submit('decide me')
        self.assertEqual(1, len(self.store.pending('demo-retail')))
        self.store.decide(approval.id, 'approved')
        self.assertEqual([], self.store.pending('demo-retail'))

    def test_the_queue_masks_the_summary_it_shows(self):
        self.submit('call +998 90 123 45 67 now')
        self.assertEqual(['call +998***now'],
                         [r['summary'] for r in self.store.pending('demo-retail')])

    def test_the_queue_shows_a_short_number_in_the_clear(self):
        """The floor leak, at the surface the operator actually reads."""
        self.submit('call +99890123 now')
        self.assertEqual(['call +99890123 now'],
                         [r['summary'] for r in self.store.pending('demo-retail')])


class ApprovalIdentityTests(FreshDatabaseTests):
    def test_the_id_is_the_tenant_and_the_declared_entropy(self):
        approval = self.submit(tenant='acme')
        prefix, suffix = approval.id.rsplit('-', 1)
        self.assertEqual('acme', prefix)
        self.assertEqual(approvals.APPROVAL_ID_BYTES, len(suffix))

    def test_the_suffix_is_lowercase_hex(self):
        suffix = self.submit(tenant='acme').id.rsplit('-', 1)[1]
        self.assertRegex(suffix, r'^[0-9a-f]+$')

    def test_the_entropy_is_the_declared_width_not_a_prefix_of_it(self):
        """A shorter slice would make ids collide sooner and a neighbour guessable."""
        ids = {self.submit(tenant='acme', summary=f's{i}').id for i in range(50)}
        self.assertEqual(50, len(ids))
        for value in ids:
            self.assertEqual(approvals.APPROVAL_ID_BYTES, len(value.rsplit('-', 1)[1]))

    def test_a_repeated_update_id_does_not_queue_twice(self):
        """The same channel update must not become two pending approvals."""
        first = self.submit(payload={'update_id': 77}, tenant='acme')
        second = self.submit(payload={'update_id': 77}, tenant='acme')
        self.assertEqual(first.id, second.id)
        self.assertEqual(1, len(self.store.pending('acme')))

    def test_a_different_update_id_queues_separately(self):
        self.submit(payload={'update_id': 77}, tenant='acme')
        self.submit(payload={'update_id': 78}, tenant='acme')
        self.assertEqual(2, len(self.store.pending('acme')))


class ApprovalReasonTests(FreshDatabaseTests):
    def test_a_reason_is_stored_up_to_the_ceiling(self):
        approval = self.submit()
        self.store.decide(approval.id, 'approved',
                          'z' * (approvals.MAX_REASON_CHARS + 50), 'operator')
        self.assertEqual(approvals.MAX_REASON_CHARS,
                         len(self.store.get(approval.id)['reason']))

    def test_a_reason_at_the_ceiling_is_stored_whole(self):
        approval = self.submit()
        reason = 'z' * approvals.MAX_REASON_CHARS
        self.store.decide(approval.id, 'approved', reason, 'operator')
        self.assertEqual(reason, self.store.get(approval.id)['reason'])

    def test_angle_brackets_are_stripped_before_storage(self):
        """The reason is rendered back into a page, so the tags go first."""
        approval = self.submit()
        self.store.decide(approval.id, 'approved', '<script>alert(1)</script>', 'operator')
        stored = self.store.get(approval.id)['reason']
        self.assertNotIn('<', stored)
        self.assertNotIn('>', stored)
        self.assertEqual('scriptalert(1)/script', stored)

    def test_the_actor_is_recorded(self):
        approval = self.submit()
        self.store.decide(approval.id, 'approved', 'looks right', 'operator@example.invalid')
        row = self.store.get(approval.id)
        self.assertEqual('operator@example.invalid', row['decided_by'])
        self.assertEqual('looks right', row['reason'])

    def test_a_second_decision_is_refused(self):
        approval = self.submit()
        self.store.decide(approval.id, 'approved')
        with self.assertRaises(ValueError):
            self.store.decide(approval.id, 'rejected')

    def test_an_unknown_approval_is_not_found(self):
        with self.assertRaises(LookupError):
            self.store.decide('no-such-approval', 'approved')

    def test_the_stored_status_is_the_decision(self):
        """One spelling of the vocabulary, not three.

        ``status`` used to be written as ``"approved" if decision == "approved" else
        "rejected"`` -- the same two words a third time, after the guard above had
        already established that ``decision`` is one of them.
        """
        for decision in approvals.DECISIONS:
            with self.subTest(decision=decision):
                approval = self.submit()
                returned, _ = self.store.decide(approval.id, decision)
                self.assertEqual(decision, returned['status'])
                self.assertEqual(decision, self.store.get(approval.id)['status'])


class ApprovalVocabularyTests(FreshDatabaseTests):
    """One vocabulary, two callers: the store raises ``ValueError``, the route ``422``."""

    def test_the_store_refuses_a_decision_outside_the_vocabulary(self):
        approval = self.submit()
        for decision in ('maybe', 'APPROVED', 'approve', '', 'reject'):
            with self.subTest(decision=decision):
                with self.assertRaises(ValueError):
                    self.store.decide(approval.id, decision)

    def test_the_store_accepts_each_declared_decision(self):
        for decision in approvals.DECISIONS:
            with self.subTest(decision=decision):
                approval = self.submit()
                returned, _ = self.store.decide(approval.id, decision)
                self.assertEqual(decision, returned['status'])

    def test_the_route_refuses_a_decision_outside_the_vocabulary(self):
        approval = self.submit()
        with self.assertRaises(HTTPException) as caught:
            self.decide_via_route(approval.id, 'maybe')
        self.assertEqual(422, caught.exception.status_code)

    def test_the_route_accepts_each_declared_decision(self):
        for decision in approvals.DECISIONS:
            with self.subTest(decision=decision):
                approval = self.submit()
                result = self.decide_via_route(approval.id, decision, 'via route')
                self.assertTrue(result['ok'])
                self.assertEqual(decision, result['status'])

    def test_the_route_reports_an_unknown_approval(self):
        with self.assertRaises(HTTPException) as caught:
            self.decide_via_route('no-such-approval', 'approved')
        self.assertEqual(404, caught.exception.status_code)

    def test_the_route_reports_an_already_decided_approval(self):
        approval = self.submit()
        self.decide_via_route(approval.id, 'approved')
        with self.assertRaises(HTTPException) as caught:
            self.decide_via_route(approval.id, 'rejected')
        self.assertEqual(409, caught.exception.status_code)


class LadderPromotionTests(FreshDatabaseTests):
    """``min_tasks`` and ``max_err``: when an agent stops needing a human."""

    KEY = 'demo-retail:ops.probe'

    def run_outcomes(self, outcomes, store=None):
        store = store or self.ladder
        return [store.record(self.KEY, ok) for ok in outcomes]

    def test_promotion_waits_for_the_threshold(self):
        """One short of the threshold is not the threshold."""
        store = ladder.LadderStore()
        self.assertEqual(ladder.LEVELS[0],
                         self.run_outcomes([True] * (store.min_tasks - 1), store)[-1])
        self.assertEqual(ladder.LEVELS[1],
                         self.run_outcomes([True], store)[-1])

    def test_the_error_bar_is_inclusive(self):
        """``errs / total <= max_err`` promotes; the ratio is not rounded."""
        store = ladder.LadderStore()
        errors = 1
        self.assertLessEqual(errors / store.min_tasks, store.max_err)
        self.run_outcomes([False] * errors + [True] * (store.min_tasks - errors), store)
        self.assertEqual(ladder.LEVELS[1], store.level(self.KEY))

    def test_the_error_bar_is_inclusive_exactly_at_the_bar(self):
        """``<=`` and not ``<``, measured where the bar is exactly reachable.

        The default policy cannot express this: 5% of 30 is 1.5, so no integer error
        count lands on the bar and ``<`` would pass every test above.  Twenty can --
        one error in twenty is exactly 5% -- and that is the only configuration in
        which the two operators differ.
        """
        store = ladder.LadderStore(min_tasks=20, max_err=0.05)
        self.assertEqual(store.max_err, 1 / store.min_tasks)
        for index in range(store.min_tasks):
            store.record(self.KEY, index != 0)
        self.assertEqual(ladder.LEVELS[1], store.level(self.KEY))

    def test_one_error_past_the_bar_does_not_promote(self):
        store = ladder.LadderStore()
        errors = 2
        self.assertGreater(errors / store.min_tasks, store.max_err)
        self.assertLessEqual(errors / store.min_tasks, store.demote_err)
        self.run_outcomes([False] * errors + [True] * (store.min_tasks - errors), store)
        self.assertEqual(ladder.LEVELS[0], store.level(self.KEY))

    def test_promotion_clears_the_history(self):
        """The window restarts at every rung -- deliberately, per the module docstring.

        Asserted because it is what makes the *next* promotion need a full fresh run:
        without it, one long history would carry an agent up two rungs at once.
        """
        store = ladder.LadderStore()
        self.run_outcomes([True] * store.min_tasks, store)
        self.assertEqual(ladder.LEVELS[1], store.level(self.KEY))
        self.assertEqual([], store._read_all()[self.KEY]['outcomes'])

    def test_the_automatic_cap_stops_at_human_assisted(self):
        """``auto_cap=True``: ``autonomous`` is reachable only from the owner endpoint.

        This is the audit S29 guard against self-approve farming -- an agent that
        could promote itself to ``autonomous`` would be able to approve its own
        write-actions.
        """
        store = ladder.LadderStore()
        store.set_level(self.KEY, ladder.LEVELS[1])
        self.run_outcomes([True] * store.min_tasks, store)
        self.assertEqual(ladder.LEVELS[1], store.level(self.KEY))

    def test_the_cap_removed_reaches_autonomous(self):
        """The cap is what holds it, not the arithmetic."""
        store = ladder.LadderStore(auto_cap=False)
        store.set_level(self.KEY, ladder.LEVELS[1])
        self.run_outcomes([True] * store.min_tasks, store)
        self.assertEqual(ladder.LEVELS[2], store.level(self.KEY))

    def test_an_agent_already_at_the_top_is_not_promoted_past_it(self):
        store = ladder.LadderStore(auto_cap=False)
        store.set_level(self.KEY, ladder.LEVELS[-1])
        self.run_outcomes([True] * store.min_tasks, store)
        self.assertEqual(ladder.LEVELS[-1], store.level(self.KEY))

    def test_the_returned_level_agrees_with_the_stored_one(self):
        store = ladder.LadderStore()
        levels = self.run_outcomes([True] * store.min_tasks, store)
        self.assertEqual(store.level(self.KEY), levels[-1])


class LadderDemotionTests(FreshDatabaseTests):
    KEY = 'demo-retail:ops.probe'

    def run_errors(self, errors, store):
        for index in range(store.min_tasks):
            store.record(self.KEY, index >= errors)

    def test_the_demotion_bar_is_exclusive(self):
        """``errs / total > demote_err``: exactly the bar is not past it."""
        store = ladder.LadderStore()
        errors = 6
        self.assertEqual(store.demote_err, errors / store.min_tasks)
        store.set_level(self.KEY, ladder.LEVELS[2])
        self.run_errors(errors, store)
        self.assertEqual(ladder.LEVELS[2], store.level(self.KEY))

    def test_one_error_past_the_bar_demotes_one_step(self):
        store = ladder.LadderStore()
        errors = 7
        self.assertGreater(errors / store.min_tasks, store.demote_err)
        store.set_level(self.KEY, ladder.LEVELS[2])
        self.run_errors(errors, store)
        self.assertEqual(ladder.LEVELS[1], store.level(self.KEY))

    def test_demotion_never_goes_below_the_floor(self):
        store = ladder.LadderStore()
        store.set_level(self.KEY, ladder.LEVELS[0])
        self.run_errors(store.min_tasks, store)
        self.assertEqual(ladder.LEVELS[0], store.level(self.KEY))

    def test_demotion_clears_the_history(self):
        store = ladder.LadderStore()
        store.set_level(self.KEY, ladder.LEVELS[2])
        self.run_errors(7, store)
        self.assertEqual(ladder.LEVELS[1], store.level(self.KEY))
        self.assertEqual([], store._read_all()[self.KEY]['outcomes'])


class LadderWindowTests(FreshDatabaseTests):
    """``MIN_WINDOW``: the floor that keeps the promotion rule reachable."""

    KEY = 'demo-retail:ops.probe'

    def test_the_floor_is_load_bearing(self):
        """``window`` is ``max(MIN_WINDOW, min_tasks)``, not ``min_tasks``.

        Below the floor the two are equal and the assertion is vacuous, so the sweep
        covers thresholds on both sides of it.
        """
        for min_tasks in (1, 5, 29):
            with self.subTest(min_tasks=min_tasks):
                self.assertEqual(ladder.MIN_WINDOW,
                                 ladder.LadderStore(min_tasks=min_tasks).window)
        for min_tasks in (31, 100):
            with self.subTest(min_tasks=min_tasks):
                self.assertEqual(min_tasks, ladder.LadderStore(min_tasks=min_tasks).window)

    def test_history_is_retained_for_the_whole_window(self):
        """Measured through the stored row, because the level alone cannot show it."""
        store = ladder.LadderStore(min_tasks=1)
        for _ in range(ladder.MIN_WINDOW + 5):
            store.record(self.KEY, False)
        self.assertEqual(ladder.MIN_WINDOW, len(store._read_all()[self.KEY]['outcomes']))

    def test_a_small_threshold_still_promotes(self):
        """Without the floor this is the dead rule: a window of ``min_tasks`` that is
        also the trim length would let the count reach the threshold, but a window
        below it would never.  The floor removes the question."""
        store = ladder.LadderStore(min_tasks=1)
        self.assertEqual(ladder.LEVELS[1], store.record(self.KEY, True))

    def test_the_window_is_never_below_the_threshold(self):
        for min_tasks in range(1, 41):
            with self.subTest(min_tasks=min_tasks):
                store = ladder.LadderStore(min_tasks=min_tasks)
                self.assertGreaterEqual(store.window, store.min_tasks)

    def test_a_run_longer_than_the_window_is_trimmed_from_the_front(self):
        """The newest outcomes are kept; the oldest are dropped.

        The run has to be one that never promotes, or the promotion clears the
        history and there is nothing left to measure: ten failures in a hundred keeps
        the ratio above ``max_err`` and below ``demote_err``, so the agent stays put
        and the window fills up.
        """
        store = ladder.LadderStore(min_tasks=100)
        for index in range(store.window + 3):
            store.record(self.KEY, index >= 10)
        outcomes = store._read_all()[self.KEY]['outcomes']
        self.assertEqual(store.window, len(outcomes))
        self.assertEqual(7, outcomes.count(False),
                         'the first three outcomes should have been trimmed away')


class LadderIsolationTests(FreshDatabaseTests):
    def test_the_key_carries_the_tenant(self):
        self.assertEqual('acme:ops.probe', ladder.key('acme', 'ops.probe'))

    def test_one_tenants_history_is_not_anothers(self):
        store = ladder.LadderStore(min_tasks=1)
        store.record(ladder.key('tenant-a', 'ops.probe'), True)
        self.assertEqual(ladder.LEVELS[1], store.level(ladder.key('tenant-a', 'ops.probe')))
        self.assertEqual(ladder.LEVELS[0], store.level(ladder.key('tenant-b', 'ops.probe')))

    def test_two_agents_in_one_tenant_are_separate(self):
        store = ladder.LadderStore(min_tasks=1)
        store.record(ladder.key('acme', 'a'), True)
        self.assertEqual(ladder.LEVELS[1], store.level(ladder.key('acme', 'a')))
        self.assertEqual(ladder.LEVELS[0], store.level(ladder.key('acme', 'b')))

    def test_an_empty_identifier_is_refused(self):
        for agent_id in ('', None):
            with self.subTest(agent_id=repr(agent_id)):
                with self.assertRaises(ValueError):
                    self.ladder.level(agent_id)

    def test_an_identifier_with_two_colons_is_refused(self):
        """The key is ``tenant:agent``; a second colon means ``key()`` was bypassed."""
        with self.assertRaises(ValueError):
            self.ladder.level('acme:ops:probe')

    def test_all_levels_reads_every_agent_in_one_pass(self):
        store = ladder.LadderStore(min_tasks=1)
        store.record(ladder.key('acme', 'a'), True)
        store.record(ladder.key('acme', 'b'), False)
        self.assertEqual({ladder.key('acme', 'a'): ladder.LEVELS[1],
                          ladder.key('acme', 'b'): ladder.LEVELS[0]},
                         store.all_levels())


class LadderVocabularyTests(FreshDatabaseTests):
    KEY = 'demo-retail:ops.probe'

    def test_the_levels_are_the_declared_ladder(self):
        self.assertEqual(['human_led', 'human_assisted', 'autonomous'], ladder.LEVELS)

    def test_an_agent_starts_human_led(self):
        self.assertEqual(ladder.LEVELS[0], self.ladder.level(self.KEY))

    def test_each_declared_level_can_be_set(self):
        for level in ladder.LEVELS:
            with self.subTest(level=level):
                self.assertEqual(level, self.ladder.set_level(self.KEY, level))
                self.assertEqual(level, self.ladder.level(self.KEY))

    def test_an_unknown_level_is_refused(self):
        for level in ('auto', 'AUTONOMOUS', '', 'human'):
            with self.subTest(level=level):
                with self.assertRaises(ValueError):
                    self.ladder.set_level(self.KEY, level)

    def test_setting_a_level_clears_the_history(self):
        """A manual move starts a fresh window, like a promotion does."""
        store = ladder.LadderStore(min_tasks=1)
        store.record(self.KEY, True)
        store.set_level(self.KEY, ladder.LEVELS[0])
        self.assertEqual([], store._read_all()[self.KEY]['outcomes'])


class NoUnnamedBoundTests(unittest.TestCase):
    """The invariant that keeps the promoted names from drifting back to literals."""

    def test_the_decision_vocabulary_is_written_once(self):
        text = source(approvals)
        self.assertEqual(1, text.count('("approved", "rejected")'),
                         'the decision vocabulary must be spelled exactly once')
        self.assertEqual(2, text.count('not in DECISIONS'),
                         'both guard sites -- store and route -- must use DECISIONS')

    def test_the_queue_ceiling_names_the_constant(self):
        self.assertIn('limit: int = MAX_PENDING_ROWS', source(approvals))

    def test_the_reason_ceiling_names_the_constant(self):
        body = method_body(source(approvals), 'decide')
        self.assertIn('[:MAX_REASON_CHARS]', body)
        self.assertNotIn('[:200]', body)

    def test_the_id_slice_names_the_constant(self):
        self.assertIn('hex[:APPROVAL_ID_BYTES]', source(approvals))

    def test_the_ladder_guards_name_their_constants(self):
        body = method_body(source(ladder), 'record')
        for pattern in (r'>= self\.min_tasks', r'<= self\.max_err', r'> self\.demote_err',
                        r'\[-self\.window:\]'):
            with self.subTest(pattern=pattern):
                self.assertRegex(body, pattern)

    def test_the_ladder_carries_no_bare_number_in_a_guard(self):
        """A number compared against in a guard is a bound that lost its name.

        Comparisons against ``0`` are allowed and are not bounds: ``idx > 0`` asks
        whether there is a rung below, not how many rungs there are.
        """
        body = method_body(source(ladder), 'record')
        hits = [line.strip() for line in body.splitlines()
                if re.search(r'(?:<=|>=|<|>)\s*-?[1-9]', line)
                and not line.strip().startswith('#')]
        self.assertEqual([], hits, f'name these bounds: {hits}')

    def test_the_window_floor_names_the_constant(self):
        self.assertIn('max(MIN_WINDOW, min_tasks)', source(ladder))


if __name__ == '__main__':
    unittest.main()
