"""Agent oversight contract tests (PRD v0.5, P9 / T3).

The behaviours that matter: oversight is read-only by construction, an unknown
agent is refused instead of reported as idle, a meter this host cannot read is
reported as unrecorded rather than zero, and cost is attributed honestly instead
of being guessed from a ledger key that does not carry the agent.
"""
import json
import tempfile
import unittest
from pathlib import Path

from platform_runtime.engine import Engine, Forbidden
from platform_runtime.oversight import (
    DEFAULT_WINDOW_SECONDS,
    MAX_EVENTS,
    MAX_WINDOW_SECONDS,
    VIEWS,
    _bounded,
    _dispatch,
    _known,
    _text,
    _window,
    DEFAULT_WINDOW_SECONDS,
    MAX_WINDOW_SECONDS,
    OVERSIGHT_TOOLS,
    activity,
    cost,
    health,
    register_oversight_tools,
)
from platform_runtime.tools import build_registry

TENANT = 't_oversight'
AGENT = 'sales.responder'
OTHER = 'sales.order_taker'

POLICY = {'tools': [*OVERSIGHT_TOOLS], 'ladder': 'human_assisted', 'agents': []}


class OversightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = Engine(self.root / 'platform.db', build_registry(),
                             lambda t, a: dict(POLICY))
        self.clock = [1000.0]
        self.engine.clock = lambda: self.clock[0]
        self.seed()

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self):
        """Real rows in the real tables, inserted through a real transaction."""
        now = self.clock[0]
        with self.engine.tx() as c:
            for task, agent, status in (
                    ('t1', AGENT, 'succeeded'), ('t2', AGENT, 'succeeded'),
                    ('t3', AGENT, 'escalated'), ('t4', OTHER, 'succeeded')):
                c.execute('''INSERT INTO p_tasks(id,tenant,channel,event_key,fingerprint,
                    agent,actor,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                          (task, TENANT, 'web', task, 'f' + task, agent, 'owner',
                           status, now - 60, now - 60))
                c.execute('''INSERT INTO p_steps(id,task,tenant,position,tool,args,risk,
                    approval_needed,fingerprint,status) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                          ('s_' + task, task, TENANT, 0, 'reports.summary', '{}',
                           'read', 0, 'sf' + task, status))
            c.execute('''INSERT INTO p_agent_runs(id,tenant,request_key,fingerprint,agent,
                actor,input,status,created,updated,deadline,max_steps,max_calls,calls,steps)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      ('r1', TENANT, 'k1', 'fp1', AGENT, 'owner', 'hello', 'succeeded',
                       now - 50, now - 40, now + 100, 6, 7, 1, 1))
            for entry in (('r2', 'escalated', 2), ('r3', 'succeeded', 1)):
                c.execute('''INSERT INTO p_agent_runs(id,tenant,request_key,fingerprint,
                    agent,actor,input,status,created,updated,deadline,max_steps,max_calls,
                    calls,steps) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (entry[0], TENANT, 'k_' + entry[0], 'fp_' + entry[0], AGENT,
                           'owner', 'x', entry[1], now - 30, now - 20 if entry[0] == 'r3' else now - 25,
                           now + 100, 6, 7, entry[2], 0))
            c.execute('''INSERT INTO p_agent_runs(id,tenant,request_key,fingerprint,agent,
                actor,input,status,created,updated,deadline,max_steps,max_calls,calls,steps)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      ('r_other', TENANT, 'k_o', 'fp_o', OTHER, 'owner', 'x',
                       'succeeded', now - 10, now - 9, now + 100, 6, 7, 1, 0))
            c.execute('''INSERT INTO p_audit(tenant,task,action,actor,data,created)
                VALUES(?,?,?,?,?,?)''',
                      (TENANT, 't1', 'task.succeeded', 'owner', '{}', now - 55))
            c.execute('''INSERT INTO p_audit(tenant,task,action,actor,data,created)
                VALUES(?,?,?,?,?,?)''',
                      (TENANT, 't4', 'task.succeeded', 'owner', '{}', now - 55))

    def call(self, name, args):
        return build_registry().get(name).handler(
            self.engine, TENANT, AGENT, args, 's1')

    # --------------------------------------------------------------- registration

    def test_every_oversight_tool_is_read_only(self):
        registry = build_registry()
        for name in OVERSIGHT_TOOLS:
            self.assertEqual('read', registry.get(name).risk, name)

    def test_oversight_registers_no_write_destructive_or_physical_tool(self):
        registry = build_registry()
        for name in OVERSIGHT_TOOLS:
            self.assertNotIn(registry.get(name).risk, {'write', 'destructive', 'physical'})

    def test_unknown_oversight_view_is_impossible_through_the_registry(self):
        # There is no generic entry point: each view is its own fixed tool name.
        for name in OVERSIGHT_TOOLS:
            self.assertTrue(name.startswith('agent.'))

    # ------------------------------------------------------------------ identity

    def test_an_undeclared_agent_is_refused_when_the_catalog_is_enumerable(self):
        self.engine.agent_catalog = lambda tenant: [{'id': AGENT}]
        with self.assertRaises(Forbidden):
            activity(self.engine, TENANT, OTHER)
        with self.assertRaises(Forbidden):
            health(self.engine, TENANT, OTHER)

    def test_a_malformed_agent_id_is_a_value_error(self):
        for bad in ('', '  ', 'Has Space', 'semi;colon', 'a' * 200):
            with self.assertRaises(ValueError):
                activity(self.engine, TENANT, bad)

    def test_an_agent_with_no_history_reports_zeroes_not_an_error(self):
        self.engine.agent_catalog = lambda tenant: [{'id': AGENT}, {'id': 'never.used'}]
        result = activity(self.engine, TENANT, 'never.used')
        self.assertEqual({}, result['tasks'])
        self.assertEqual({}, result['runs'])
        self.assertEqual([], result['events'])
        self.assertIsNone(result['last_activity'])

    # ------------------------------------------------------------------ activity

    def test_activity_counts_only_this_agents_tasks(self):
        result = activity(self.engine, TENANT, AGENT)
        self.assertEqual({'succeeded': 2, 'escalated': 1}, result['tasks'])
        # OTHER's task must not be counted.
        self.assertNotIn('t4', json.dumps(result))

    def test_activity_separates_task_and_run_counts(self):
        result = activity(self.engine, TENANT, AGENT)
        self.assertEqual({'succeeded': 2, 'escalated': 1}, result['runs'])
        self.assertEqual(3, sum(result['tasks'].values()))
        self.assertEqual(3, sum(result['runs'].values()))

    def test_activity_events_are_joined_through_the_task_not_a_substring(self):
        """Agent 'sales.responder' must not inherit 'sales.order_taker' events."""
        result = activity(self.engine, TENANT, AGENT)
        tasks = {event['task'] for event in result['events']}
        self.assertEqual({'t1'}, tasks)
        self.assertNotIn('t4', tasks)

    def test_activity_respects_the_reported_window(self):
        self.clock[0] = 1000.0
        result = activity(self.engine, TENANT, AGENT, since_seconds=1)
        self.assertEqual({}, result['tasks'])
        self.assertEqual({}, result['runs'])
        self.assertEqual(1000.0 - 1, result['since'])

    def test_the_window_start_is_inclusive_so_a_row_on_it_is_counted(self):
        """The predicate is `created >= since`; equality decides the sense.

        ``test_activity_respects_the_reported_window`` above walks a one-second
        window over rows 29-59 seconds old, so every row is far OUTSIDE it. That
        proves the window is not unbounded, and it passes under `>=` and under
        `>` alike: with no row anywhere near the boundary, both predicates return
        the same empty dict. It therefore measures nothing about the sense.

        Only a row placed EXACTLY on the boundary discriminates. ``setUp`` seeds
        rows 10-60 seconds before clock 1000, so the clock is first moved far past
        them and the boundary row is created against the new clock. With
        ``since_seconds=100`` at clock 100000 the window starts at 99900, so a task
        created at 99900.0 is inside under `>=` and outside under `>`. The window is
        inclusive on purpose: a report whose start moves by one tick must not
        silently drop the event at its own edge, because the operator reads the
        count as a fact about the agent rather than about the clock.
        """
        self.clock[0] = 100_000.0
        with self.engine.tx() as c:
            c.execute('''INSERT INTO p_tasks(id,tenant,channel,event_key,fingerprint,
                agent,actor,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                      ('edge', TENANT, 'web', 'edge', 'fedge', AGENT, 'owner',
                       'succeeded', 99_900.0, 99_900.0))
        # Sanity: the boundary is exactly where the window starts, and the rows
        # seeded by setUp are far outside it, so nothing else is in the window.
        result = activity(self.engine, TENANT, AGENT, since_seconds=100)
        self.assertEqual(99_900.0, result['since'])
        self.assertEqual({'succeeded': 1}, result['tasks'])
        # One tick of clock movement puts the same row outside, so the assertion
        # above is about the boundary and not about "the window is generous".
        self.clock[0] = 100_000.001
        result = activity(self.engine, TENANT, AGENT, since_seconds=100)
        self.assertEqual({}, result['tasks'])

    def test_all_three_activity_reads_share_the_inclusive_predicate(self):
        """A boundary row must be counted by tasks, runs and events alike.

        ``activity`` issues three reads -- ``p_tasks``, ``p_agent_runs`` and the
        ``p_audit`` join. All three carry ``created>=?``, but nothing placed a row
        on the boundary, so a drift in ONE of them was invisible. The audit read is
        the one worth guarding: it feeds ``events``, so if only that predicate
        became ``>`` the newest event would vanish from a report that still counts
        the task.
        """
        self.clock[0] = 100_000.0
        since = 99_900.0
        with self.engine.tx() as c:
            c.execute('''INSERT INTO p_tasks(id,tenant,channel,event_key,fingerprint,
                agent,actor,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                      ('edge', TENANT, 'web', 'edge', 'fedge', AGENT, 'owner',
                       'succeeded', since, since))
            c.execute('''INSERT INTO p_steps(id,task,tenant,position,tool,args,risk,
                approval_needed,fingerprint,status) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                      ('s_edge', 'edge', TENANT, 0, 'reports.summary', '{}', 'read', 0,
                       'sfe', 'succeeded'))
            c.execute('''INSERT INTO p_agent_runs(id,tenant,request_key,fingerprint,agent,
                actor,input,status,created,updated,deadline,max_steps,max_calls,calls,steps)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      ('r_edge', TENANT, 'k_edge', 'fpe', AGENT, 'owner', 'x',
                       'succeeded', since, since, since + 100, 6, 7, 1, 1))
            c.execute('''INSERT INTO p_audit(tenant,task,action,actor,data,created)
                VALUES(?,?,?,?,?,?)''',
                      (TENANT, 'edge', 'task.succeeded', 'owner', '{}', since))
        result = activity(self.engine, TENANT, AGENT, since_seconds=100)
        self.assertEqual({'succeeded': 1}, result['tasks'])
        self.assertEqual({'succeeded': 1}, result['runs'])
        self.assertEqual(['edge'], [event['task'] for event in result['events']])
        # The run read in ``cost`` is the same predicate, so it agrees.
        self.assertEqual(1, cost(self.engine, TENANT, AGENT,
                                 since_seconds=100)['runs'])

    def test_cost_reports_the_same_window_activity_does(self):
        """``cost`` has no bound of its own; it inherits ``_window``'s.

        ``since_seconds`` is validated in one place, ``_window``, and both
        ``activity`` and ``cost`` call it. The only test naming the bound called it
        through ``activity``, so the two views for one operator question could
        drift apart -- "what did it do" spanning seven days while "what did it
        cost" spans one -- with nothing to catch it. These assert the reported
        window is identical and that the bound reaches ``cost`` too.
        """
        self.clock[0] = 1000.0
        spelled = activity(self.engine, TENANT, AGENT, since_seconds=600)
        billed = cost(self.engine, TENANT, AGENT, since_seconds=600)
        self.assertEqual(spelled['since'], billed['since'])
        self.assertEqual(spelled['until'], billed['until'])
        self.assertEqual(400.0, billed['since'])
        for bad in (0, -1, MAX_WINDOW_SECONDS + 1):
            with self.assertRaises(ValueError):
                cost(self.engine, TENANT, AGENT, since_seconds=bad)
        # The documented lower and upper bounds are inclusive for cost as well.
        cost(self.engine, TENANT, AGENT, since_seconds=1)
        cost(self.engine, TENANT, AGENT, since_seconds=MAX_WINDOW_SECONDS)

    def test_a_longer_window_admits_rows_a_shorter_one_excludes(self):
        """A wide and a narrow window are genuinely different reads.

        ``since_seconds`` is the only thing that separates "today" from "this
        quarter", so if the argument were dropped anywhere on the way to SQL both
        windows would return the same rows and every count would silently be a
        lifetime total. The existing window tests only ever looked at one window
        at a time, so that collapse was unmeasured.
        """
        # The seed's oldest row is 60s before clock 1000. A 100s window at clock
        # 1000 would therefore still contain it, so the probe row is placed far
        # behind both windows instead of inside one of them.
        self.clock[0] = 1000.0
        with self.engine.tx() as c:
            c.execute('''INSERT INTO p_tasks(id,tenant,channel,event_key,fingerprint,
                agent,actor,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                      ('old', TENANT, 'web', 'old', 'fold', AGENT, 'owner',
                       'succeeded', 100.0, 100.0))
        # A window shorter than the seed rows' age sees only nothing at all,
        # because every row for this agent sits in the future of its start.
        narrow = activity(self.engine, TENANT, AGENT, since_seconds=1)
        self.assertEqual({}, narrow['tasks'])
        # A window that reaches back past the seed and the probe row sees both.
        # ``setUp`` seeds this agent with t1/t2 succeeded and t3 escalated, and the
        # probe row adds one more succeeded.
        wide = activity(self.engine, TENANT, AGENT, since_seconds=999)
        self.assertEqual({'succeeded': 3, 'escalated': 1}, wide['tasks'])
        self.assertNotEqual(narrow['since'], wide['since'])

    def test_activity_window_is_bounded(self):
        with self.assertRaises(ValueError):
            activity(self.engine, TENANT, AGENT, since_seconds=0)
        with self.assertRaises(ValueError):
            activity(self.engine, TENANT, AGENT, since_seconds=MAX_WINDOW_SECONDS + 1)
        # The documented bounds themselves are inside the range, not outside it.
        activity(self.engine, TENANT, AGENT, since_seconds=1)
        activity(self.engine, TENANT, AGENT, since_seconds=MAX_WINDOW_SECONDS)
        # A bool is not an int here: `type(x) is not int` refuses it, so `True`
        # cannot arrive as a one-second window through a truthy coercion.
        with self.assertRaises(ValueError):
            activity(self.engine, TENANT, AGENT, since_seconds=True)


    def test_activity_limit_is_bounded(self):
        with self.assertRaises(ValueError):
            activity(self.engine, TENANT, AGENT, limit=0)
        with self.assertRaises(ValueError):
            activity(self.engine, TENANT, AGENT, limit=10_000)

    def test_activity_truncation_is_reported(self):
        """A window with more events than fit is flagged, and one that fits is not.

        The seeded window holds exactly one event for this agent, so `limit=1`
        answers it completely and the flag must be False. Before the fix this test
        asserted True — the old `len(audit) >= limit` predicate happened to be
        satisfied by an exact fit, so the test passed while encoding the defect.
        The case is kept with the correct expectation, and a real cut is asserted
        right after it so the flag is shown to move.
        """
        result = activity(self.engine, TENANT, AGENT, limit=1)
        self.assertEqual(1, len(result['events']))
        self.assertFalse(result['truncated'])
        for _ in range(3):
            self.engine.audit_write(TENANT, 'task.succeeded', 'owner', {}, task='t1')
        result = activity(self.engine, TENANT, AGENT, limit=1)
        self.assertEqual(1, len(result['events']))
        self.assertTrue(result['truncated'])

    def test_activity_truncation_is_true_only_when_a_row_was_left_out(self):
        """The SQL caps at `limit`, so length alone cannot answer the question.

        A window holding exactly `limit` events and a window holding thousands are
        both returned by `LIMIT ?` as `limit` rows. Reporting the first as truncated
        sends an operator to widen a window that already contains everything, so
        the query asks for one row past the limit and reads the answer off that.
        """
        for _ in range(3):
            self.engine.audit_write(TENANT, 'task.succeeded', 'owner', {}, task='t1')
        result = activity(self.engine, TENANT, AGENT, limit=3)
        self.assertEqual(3, len(result['events']))
        self.assertTrue(result['truncated'])
        # Four rows for this agent in total, so a limit of four is an exact fit.
        result = activity(self.engine, TENANT, AGENT, limit=4)
        self.assertEqual(4, len(result['events']))
        self.assertFalse(result['truncated'])

    def test_activity_reports_the_last_activity_timestamp(self):
        result = activity(self.engine, TENANT, AGENT)
        self.assertEqual(980.0, result['last_activity'])

    # ---------------------------------------------------------------------- cost

    def test_cost_states_its_attribution_instead_of_guessing(self):
        """The ledger key does not carry the agent, so cost must say how it is derived."""
        result = cost(self.engine, TENANT, AGENT)
        self.assertEqual('agent_run_window', result['attribution'])
        self.assertIn('does not record the agent', result['attribution_note'])

    def test_cost_never_claims_a_per_agent_amount_it_cannot_know(self):
        result = cost(self.engine, TENANT, AGENT)
        self.assertNotIn('spent_micro', result)

    def test_cost_reports_run_and_call_counts_for_the_agent(self):
        result = cost(self.engine, TENANT, AGENT)
        self.assertEqual(3, result['runs'])
        self.assertEqual(4, result['planner_calls'])

    def test_cost_says_the_budget_is_unconfigured_rather_than_zero(self):
        result = cost(self.engine, TENANT, AGENT)
        self.assertFalse(result['budget_configured'])
        self.assertIsNone(result['currency'])
        self.assertEqual(0, result['tenant_spent_micro'])

    def test_cost_separates_in_flight_reservations_from_spent(self):
        with self.engine.tx() as c:
            c.execute('''INSERT INTO p_budget_settings(tenant,currency,limit_micro,
                max_inflight,generation) VALUES(?,?,?,?,?)''',
                      (TENANT, 'USD', 1_000_000, 4, 1))
            c.execute('''INSERT INTO p_budget_reservations(tenant,id,request_key,
                fingerprint,period,amount_micro,status,created,updated)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                      (TENANT, 'b1', 'model:abc', 'fp', '2026-09', 12_000,
                       'reserved', 990.0, 990.0))
            c.execute('''INSERT INTO p_budget_reservations(tenant,id,request_key,
                fingerprint,period,amount_micro,status,created,updated)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                      (TENANT, 'b2', 'model:def', 'fp', '2026-09', 8_000,
                       'settled', 990.0, 990.0))
        result = cost(self.engine, TENANT, AGENT)
        self.assertTrue(result['budget_configured'])
        self.assertEqual('USD', result['currency'])
        self.assertEqual(12_000, result['tenant_inflight_micro'])
        self.assertEqual({'reserved': {'calls': 1, 'amount_micro': 12_000},
                          'settled': {'calls': 1, 'amount_micro': 8_000}},
                         result['tenant_ledger_by_status'])

    # -------------------------------------------------------------------- health

    def test_health_reports_the_last_run(self):
        result = health(self.engine, TENANT, AGENT)
        # r3 is the most recently updated run.
        self.assertEqual('r3', result['last_run']['id'])
        self.assertEqual('succeeded', result['last_run']['status'])

    def test_health_computes_a_failure_rate_over_finished_runs(self):
        result = health(self.engine, TENANT, AGENT)
        self.assertEqual(3, result['finished_runs'])
        self.assertEqual(1 / 3, result['failure_rate'])

    def test_health_failure_rate_is_none_with_no_finished_runs(self):
        self.engine.agent_catalog = lambda tenant: [{'id': 'fresh.agent'}]
        result = health(self.engine, TENANT, 'fresh.agent')
        self.assertIsNone(result['failure_rate'])
        self.assertEqual(0, result['finished_runs'])

    def test_health_names_an_unreadable_meter_instead_of_reporting_zero(self):
        result = health(self.engine, TENANT, AGENT)
        self.assertIn('budget', result['not_recorded'])

    def test_health_reports_no_missing_meters_when_all_are_present(self):
        with self.engine.tx() as c:
            c.execute('''INSERT INTO p_budget_settings(tenant,currency,limit_micro,
                max_inflight,generation) VALUES(?,?,?,?,?)''',
                      (TENANT, 'USD', 1_000_000, 4, 1))
        result = health(self.engine, TENANT, AGENT)
        self.assertEqual([], result['not_recorded'])

    def test_health_reports_pending_work_and_approvals(self):
        with self.engine.tx() as c:
            c.execute('''INSERT INTO p_tasks(id,tenant,channel,event_key,fingerprint,
                agent,actor,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                      ('t_pending', TENANT, 'web', 'ev_p', 'fp_p', AGENT, 'owner',
                       'waiting_approval', 999.0, 999.0))
        result = health(self.engine, TENANT, AGENT)
        self.assertEqual(1, result['pending_tasks'])

    def test_health_reports_the_tenant_kill_switch(self):
        self.assertFalse(health(self.engine, TENANT, AGENT)['tenant_stopped'])
        with self.engine.tx() as c:
            c.execute('INSERT INTO p_freeze VALUES(?,?)', (TENANT, 1))
        self.assertTrue(health(self.engine, TENANT, AGENT)['tenant_stopped'])

    def test_health_scopes_runs_to_the_agent(self):
        result = health(self.engine, TENANT, AGENT)
        self.assertNotIn('r_other', json.dumps(result))
        self.assertEqual({'succeeded': 2, 'escalated': 1}, result['runs_by_status'])

    # ------------------------------------------------------------------ dispatch

    def test_each_tool_dispatches_its_own_view(self):
        self.assertEqual('agent_run_window', self.call('agent.cost', {'agent': AGENT})['attribution'])
        self.assertIn('last_run', self.call('agent.health', {'agent': AGENT}))
        self.assertIn('tasks', self.call('agent.activity', {'agent': AGENT}))

    def test_activity_accepts_an_optional_window_and_limit(self):
        result = self.call('agent.activity',
                           {'agent': AGENT, 'since_seconds': 3600, 'limit': 5})
        self.assertEqual(1000.0 - 3600, result['since'])

    def test_the_default_window_is_used_when_none_is_given(self):
        result = self.call('agent.activity', {'agent': AGENT})
        self.assertEqual(1000.0 - DEFAULT_WINDOW_SECONDS, result['since'])

    def test_health_and_cost_reject_an_undeclared_agent_through_the_registry(self):
        self.engine.agent_catalog = lambda tenant: [{'id': AGENT}]
        for name in ('agent.cost', 'agent.health'):
            with self.assertRaises(Forbidden):
                self.call(name, {'agent': 'not.declared'})

    def test_oversight_output_carries_no_pack_or_credential_material(self):
        blob = json.dumps(self.call('agent.activity', {'agent': AGENT}))
        self.assertNotIn('prompts/', blob)
        self.assertNotIn('token_env', blob)


class OversightBoundaryTests(OversightTests):
    """The exact caps, pinned by VALUE, and the declaration that became the guard."""

    def test_the_limit_constants_are_pinned(self):
        self.assertEqual(100, MAX_EVENTS)
        self.assertEqual(7 * 86400, DEFAULT_WINDOW_SECONDS)
        self.assertEqual(90 * 86400, MAX_WINDOW_SECONDS)
        self.assertEqual(('activity', 'cost', 'health'), VIEWS)

    def test_the_window_and_limit_bounds_are_walked(self):
        for value, accepted in ((1, True), (MAX_WINDOW_SECONDS, True), (0, False),
                                (MAX_WINDOW_SECONDS + 1, False)):
            with self.subTest(since_seconds=value):
                if accepted:
                    _window(self.engine, value)
                else:
                    with self.assertRaises(ValueError):
                        _window(self.engine, value)
        for value, accepted in ((1, True), (MAX_EVENTS, True), (0, False),
                                (MAX_EVENTS + 1, False)):
            with self.subTest(limit=value):
                if accepted:
                    activity(self.engine, TENANT, AGENT, 3600, value)
                else:
                    with self.assertRaises(ValueError):
                        activity(self.engine, TENANT, AGENT, 3600, value)

    def test_the_bounded_helper_refuses_a_non_int(self):
        for value in (1, 10):
            self.assertEqual(value, _bounded(value, 'x', 1, 10))
        for value in (0, 11, True, False, 1.0, '5', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _bounded(value, 'x', 1, 10)

    def test_the_view_set_is_the_guard(self):
        # VIEWS was declared and never read, so the closed set existed only in the
        # if-chain and in the registered tool names.
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'oversight.py').read_text(encoding='utf-8')
        self.assertIn('if view not in VIEWS:', source)
        for view in VIEWS:
            with self.subTest(view=view):
                self.assertIsInstance(
                    _dispatch('agent.' + view, self.engine, TENANT, AGENT, {}), dict)
        for name in ('agent.nope', 'agent.', 'agent.activity.extra', 'agent.health2'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _dispatch(name, self.engine, TENANT, AGENT, {})

    def test_max_runs_is_gone(self):
        # It bounded nothing that exists: `activity` aggregates the runs by status with
        # GROUP BY, so there is no run list for a cap to apply to. Removed rather than
        # inventing an enforcement point.
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'oversight.py').read_text(encoding='utf-8')
        self.assertNotIn('MAX_RUNS', source)

    def test_text_returns_the_value_it_validated(self):
        # The check tested the TRIMMED value and returned the RAW one, so `_known`
        # regex-matched a padded id: measured, `_known(' sales ')` raised
        # 'Invalid agent id' while `supervisor._name(' sales ')` returned 'sales'.
        self.assertEqual('sales', _text('  sales  ', 'agent', 128))
        self.assertEqual('sales', _text('sales', 'agent', 128))
        for value in ('', '   ', '\t', '\n'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _text(value, 'agent', 128)
        self.assertEqual('a' * 128, _text('a' * 128, 'agent', 128))
        with self.assertRaises(ValueError):
            _text('a' * 129, 'agent', 128)

    def test_the_agent_id_ceiling_is_one_hundred_and_twenty_eight(self):
        self.assertEqual('a' * 128, _known(self.engine, TENANT, 'a' * 128))
        with self.assertRaises(ValueError):
            _known(self.engine, TENANT, 'a' * 129)
        for value in ('Sales', 'a b', '-a', '', '   '):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _known(self.engine, TENANT, value)
        # A padded id is trimmed, as every other module's identifier guard does.
        self.assertEqual('sales', _known(self.engine, TENANT, '  sales  '))

    def test_the_activity_probe_reads_one_past_the_limit(self):
        # With `LIMIT` set to the limit itself, a result of exactly `limit` rows is
        # ambiguous: it is produced both by an agent with precisely that many events
        # and by one with thousands, so `truncated` would always read False.
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'oversight.py').read_text(encoding='utf-8')
        self.assertIn('probe = limit + 1', source)
        # Walked: a limit of 1 over a seeded agent reports the population honestly.
        report = activity(self.engine, TENANT, AGENT, DEFAULT_WINDOW_SECONDS, 1)
        self.assertLessEqual(len(report['events']), 1)
        self.assertIsInstance(report['truncated'], bool)

    def test_the_redundant_regex_bound_is_pinned_by_its_line(self):
        # AGENT_RE's `{0,127}` is SUBSUMED by `_text`'s length check: the regex allows at
        # most 129 characters and 129 is refused before the regex is reached, so a
        # widening changes nothing observable. Pinned by its exact line with the reason.
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'oversight.py').read_text(encoding='utf-8')
        self.assertIn("AGENT_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,127}$')", source)

    def test_the_tool_surface_is_the_declared_one(self):
        registry = build_registry()
        for name in OVERSIGHT_TOOLS:
            with self.subTest(name=name):
                self.assertIn(name, registry.items)
                self.assertEqual('read', registry.get(name).risk)
        activity_limit = registry.get('agent.activity').schema['properties']['limit']
        self.assertEqual(MAX_EVENTS, activity_limit['maximum'])
        self.assertEqual(1, activity_limit['minimum'])
        window = registry.get('agent.activity').schema['properties']['since_seconds']
        self.assertEqual(MAX_WINDOW_SECONDS, window['maximum'])
        self.assertEqual(1, window['minimum'])
