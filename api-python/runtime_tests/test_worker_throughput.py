"""Worker throughput: cheap stages drain, model calls run side by side.

Measured before the fix: app/worker.py ran one serial loop in which each pass
did at most one engine step, one conversation transition and ONE model call per
tenant, then slept 0.1 s. Twenty customers writing at once were answered one
model call at a time -- the last reply about 200 s later.

Two changes are pinned here:

* ``drain`` repeats the cheap stages (event pump, engine step, operator replies,
  conversation transitions) until none reports work, bounded per pass by rounds
  and by wall time, so a non-model transition never waits for the sleep.
* ``PlannerPool`` runs agent-loop model calls on a small thread pool, at most
  ``per_tenant`` at once per tenant, taking tenants round-robin. The run's own
  SQL claim is what prevents double planning; the pool only skips runs it
  already holds so it does not spend a transaction learning that.
"""
import logging
import tempfile
import threading
import unittest
from pathlib import Path

from app.worker import (DRAIN_SECONDS, MAX_DRAIN_ROUNDS, PLANNER_CONCURRENCY_PER_TENANT,
                        PlannerPool, drain)
from platform_runtime.agent_loop import AgentLoop
from platform_runtime.engine import Engine
from platform_runtime.tools import Registry, Tool, obj
from platform_runtime.usage_budget import UsageBudget

SECRET = 'https://api.telegram.org/bot123:SECRET'
ASK = {'action': 'ask', 'question': 'Qaysi o‘lcham kerak?'}


class Counter:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.results.pop(0) if self.results else False


class DrainTests(unittest.TestCase):
    def setUp(self):
        self.logged = []

    def log(self, *args):
        self.logged.append(args)

    def test_cheap_stages_repeat_until_no_work(self):
        engine, conversation = Counter([True, True, True]), Counter([True])
        self.assertTrue(drain('t', [('engine', engine), ('conversation', conversation)], log=self.log))
        self.assertEqual(4, engine.calls)
        self.assertEqual(4, conversation.calls)

    def test_no_work_reports_idle_after_one_round(self):
        stage = Counter([])
        self.assertFalse(drain('t', [('engine', stage)], log=self.log))
        self.assertEqual(1, stage.calls)

    def test_the_rounds_per_pass_are_bounded(self):
        self.assertEqual(50, MAX_DRAIN_ROUNDS)
        stage = Counter([True] * 1000)
        self.assertTrue(drain('t', [('engine', stage)], log=self.log))
        self.assertEqual(MAX_DRAIN_ROUNDS, stage.calls)

    def test_the_wall_time_per_pass_is_bounded(self):
        now = [0.0]

        def slow():
            now[0] += DRAIN_SECONDS / 2
            return True
        stage = Counter([True] * 1000)
        drain('t', [('slow', slow), ('engine', stage)], log=self.log, clock=lambda: now[0])
        self.assertEqual(2, stage.calls)

    def test_a_raising_stage_is_isolated_and_logged_by_type_only(self):
        ran = []

        def boom():
            ran.append('boom')
            raise RuntimeError(SECRET)
        engine = Counter([True])
        self.assertTrue(drain('t', [('boom', boom), ('engine', engine)], log=self.log))
        self.assertEqual(2, engine.calls)
        self.assertEqual(2, len(self.logged))
        self.assertNotIn(SECRET, repr(self.logged))
        self.assertIn('RuntimeError', repr(self.logged))


class PlannerPoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        registry = Registry()
        registry.add(Tool('lookup.customer', 'read', obj({}), lambda *args: {'id': 'c1'}))
        self.e = Engine(Path(self.tmp.name) / 'pool.db', registry,
                        lambda tenant, agent: {'tools': ['lookup.customer'], 'ladder': 'autonomous'})
        self.loop = AgentLoop(self.e)
        self.lock = threading.Lock()
        self.planned = []
        self.logged = []

    def create(self, tenant, count):
        return [self.loop.create(tenant, '%s-%d' % (tenant, index), 'ops', 'Savol', 'actor')
                for index in range(count)]

    def pool(self, planner, **kwargs):
        pool = PlannerPool(self.loop, planner, log=lambda *args: self.logged.append(args), **kwargs)
        self.addCleanup(pool.shutdown)
        return pool

    def recording(self, gate=None, barrier=None):
        def planner(tenant, context):
            with self.lock:
                self.planned.append((tenant, context['run_id']))
            if barrier is not None:
                barrier.wait()
            if gate is not None:
                gate.wait(10)
            return ASK
        return planner

    def status(self, run_id, tenant='a'):
        return self.loop.get(tenant, run_id)

    def test_runs_are_planned_concurrently(self):
        runs = self.create('a', PLANNER_CONCURRENCY_PER_TENANT)
        # Every planner waits for all the others: a serial loop would break the barrier.
        barrier = threading.Barrier(PLANNER_CONCURRENCY_PER_TENANT, timeout=10)
        pool = self.pool(self.recording(barrier=barrier))
        self.assertTrue(pool.pump(['a']))
        pool.shutdown()
        self.assertFalse(barrier.broken)
        self.assertEqual(['needs_input'] * len(runs), [self.status(r)['status'] for r in runs])

    def test_the_per_tenant_bound_holds_and_no_run_is_planned_twice(self):
        runs = self.create('a', 6)
        gate = threading.Event()
        pool = self.pool(self.recording(gate=gate), per_tenant=4)
        pool.pump(['a'])
        for _ in range(3):  # later passes while every call is still in flight
            pool.pump(['a'])
        self.assertEqual(4, len(pool.inflight))
        gate.set()
        pool.idle(10)
        while pool.inflight:
            pool.idle(10)
            pool.pump(['a'])
        pool.shutdown()
        self.assertEqual(sorted(runs), sorted(run for _, run in self.planned))
        for run in runs:
            with self.subTest(run=run):
                self.assertEqual(1, self.status(run)['calls'])
                self.assertEqual('needs_input', self.status(run)['status'])

    def test_tenants_are_served_round_robin(self):
        self.create('a', 5)
        self.create('b', 1)
        gate = threading.Event()
        pool = self.pool(self.recording(gate=gate), workers=2)
        pool.pump(['a', 'b'])
        self.assertEqual({'a', 'b'}, {tenant for tenant, _ in pool.inflight.values()})
        gate.set()

    def test_the_budget_parallel_limit_caps_the_tenant(self):
        UsageBudget(self.e).configure('a', 'owner', 'USD', 10 ** 6, 1)
        self.create('a', 3)
        gate = threading.Event()
        pool = self.pool(self.recording(gate=gate))
        pool.pump(['a'])
        self.assertEqual(1, len(pool.inflight))
        gate.set()

    def test_a_failing_planner_escalates_its_run_and_nothing_secret_is_logged(self):
        run = self.create('a', 1)[0]

        def planner(tenant, context):
            raise RuntimeError(SECRET)
        pool = self.pool(planner)
        pool.pump(['a'])
        pool.shutdown()
        self.assertEqual('planner_failed_no_retry', self.status(run)['error'])
        self.assertNotIn(SECRET, repr(self.logged))

    def test_a_tenant_that_raises_does_not_stop_the_others(self):
        run = self.create('b', 1)[0]
        original = self.loop.reserve_next

        def reserve_next(tenant, skip=()):
            if tenant == 'bad':
                raise RuntimeError(SECRET)
            return original(tenant, skip)
        self.loop.reserve_next = reserve_next
        pool = self.pool(self.recording())
        pool.pump(['bad', 'b'])
        pool.shutdown()
        self.assertEqual('needs_input', self.status(run, 'b')['status'])
        self.assertIn('RuntimeError', repr(self.logged))
        self.assertNotIn(SECRET, repr(self.logged))

    def test_idle_returns_as_soon_as_a_call_finishes(self):
        self.create('a', 1)
        pool = self.pool(self.recording())
        pool.pump(['a'])
        started = threading.Event()
        timer = threading.Timer(5, started.set); timer.start()
        pool.idle(5)
        self.assertFalse(started.is_set())
        timer.cancel()


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main()
