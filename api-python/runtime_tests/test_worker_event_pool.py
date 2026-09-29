"""Inbound events whose plan may call a model are planned on the pool, not the main loop.

Measured before the fix: app/worker.py drained ``e.process_event(t, planner)`` on
its one main thread. For a channel no conversation agent owns (the dashboard's
``web`` channel, for example) that planner is the one-shot LLM planner, which may
wait up to llm.EVENT_PLAN_DEADLINE_SECONDS (100 s, retries included) -- and while
it waited, every tenant's engine steps, conversation turns and the agent-loop
pump waited with it.

Pinned here:

* ``Engine.claim_event`` (one short write: the lease) and ``Engine.settle_event``
  (the plan, then the outcome written under the claim) are separable;
  ``process_event`` is still exactly the two in a row.
* ``EventRouting``: a conversation channel plans without a model call, so its
  events stay on the main thread and are answered in the same pass; every other
  channel is claimed by ``PlannerPool`` and planned on its threads.
* No event is planned twice under concurrency, a claim abandoned mid-plan is
  recovered once its lease runs out, and shutdown waits for a plan to commit.
"""
import collections
import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app.planning import conversation_agent
from app.worker import EventRouting, PlannerPool, conversation_channels
from platform_runtime import engine as E
from platform_runtime.agent_loop import AgentLoop
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry
from platform_runtime.usage_budget import UsageBudget

SECRET = 'https://api.telegram.org/bot123:SECRET'
STEPS = {'agent': 'ops', 'steps': [{'tool': 'reports.summary', 'args': {}}]}
TURN = {'agent': 'bot', 'conversation': {}}


def policy(tenant, agent):
    if agent == 'bot':
        return {'tools': ['telegram.send'], 'ladder': 'autonomous', 'approval': [],
                'allowed_recipients': [], 'allowed_connections': [], 'conversation': {'enabled': True}}
    if agent == 'ops':
        return {'tools': ['reports.summary'], 'ladder': 'autonomous', 'approval': []}
    raise Forbidden('Agent not in tenant pack')


def conversation_only(tenant):
    return frozenset({'telegram'})


class EventPoolCase(unittest.TestCase):
    """Fixtures only: a real Engine, a planner that blocks on the model channel."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'events.db'
        self.now = 1000.0
        self.e = Engine(self.path, build_registry(lambda tenant, query: []), policy, clock=lambda: self.now)
        self.gate = threading.Event()
        self.model_started = threading.Event()
        self.lock = threading.Lock()
        self.planned = []
        self.logged = []
        self.delay = 0

    def planner(self, tenant, channel, payload):
        with self.lock:
            self.planned.append((tenant, channel, payload['text']))
        if channel == 'telegram':
            return dict(TURN)
        # The one-shot model planner: it waits for the provider.
        self.model_started.set()
        if not self.gate.wait(10):
            raise TimeoutError('test gate never opened')
        if self.delay:
            time.sleep(self.delay)
        return dict(STEPS)

    def web(self, tenant, key, text=None):
        self.e.accept_event(tenant, 'web', key, {'sender': 'owner', 'text': text or key})

    def chat(self, tenant, key, text=None):
        self.e.accept_event(tenant, 'telegram', key,
                            {'sender': '555', 'text': text or key, 'conversation_id': 'chat-' + tenant})

    def routing(self, fast=conversation_only, engine=None):
        return EventRouting(engine or self.e, self.planner, fast)

    def pool(self, routing, engine=None, **kwargs):
        pool = PlannerPool(AgentLoop(engine or self.e), lambda tenant, context: None, events=routing,
                           log=lambda *args: self.logged.append(args), **kwargs)
        # Open the gate first: a failed assertion must not leave shutdown waiting on it.
        self.addCleanup(pool.shutdown)
        self.addCleanup(self.gate.set)
        return pool

    def event(self, tenant, key):
        with self.e.read() as c:
            return dict(c.execute('SELECT * FROM p_events WHERE tenant=? AND event_key=?',
                                  (tenant, key)).fetchone())

    def turn(self, tenant, key):
        with self.e.read() as c:
            row = c.execute('SELECT status FROM p_conversation_turns WHERE tenant=? AND event_key=?',
                            (tenant, key)).fetchone()
        return row['status'] if row else None


class ClaimTests(EventPoolCase):
    def test_process_event_is_a_claim_then_a_settle(self):
        self.web('a', 'w1')
        claimed = self.e.claim_event('a')
        self.assertEqual(('w1', 'processing'), (claimed['event_key'], claimed['status']))
        self.assertEqual(self.now + E.EVENT_LEASE_SECONDS, claimed['lease'])
        self.assertEqual(claimed['claim'], self.event('a', 'w1')['claim'])
        self.gate.set()
        self.assertTrue(self.e.settle_event('a', claimed, self.planner))
        row = self.event('a', 'w1')
        self.assertEqual(('done', '', 0), (row['status'], row['claim'], row['lease']))

    def test_a_claim_can_be_limited_to_or_kept_away_from_channels(self):
        self.web('a', 'w1')
        self.chat('a', 'm1')
        self.assertIsNone(self.e.claim_event('a', channels=frozenset()))
        self.assertEqual('m1', self.e.claim_event('a', channels={'telegram'})['event_key'])
        self.assertIsNone(self.e.claim_event('a', channels={'telegram'}))
        self.assertIsNone(self.e.claim_event('a', skip={'web', 'telegram'}))
        self.assertEqual('w1', self.e.claim_event('a', skip={'telegram'})['event_key'])

    def test_a_filtered_claim_still_seeks_the_status_index(self):
        # A channel filter must not trade the (tenant,status) seek for the
        # (tenant,channel) index, which reads the channel's whole history.
        with self.e.read() as c:
            for channels, skip in ((['telegram'], ()), (None, ['telegram', 'whatsapp'])):
                with self.subTest(channels=channels, skip=skip):
                    sql = E.next_event_sql(channels, skip)
                    params = ['a', 1000] + list(channels or []) + list(skip)
                    plan = ' '.join(r['detail'] for r in c.execute('EXPLAIN QUERY PLAN ' + sql, params))
                    self.assertIn('p_events_status', plan)
        self.assertEqual(E.NEXT_EVENT_SQL, E.next_event_sql(None, ()))

    def test_a_frozen_tenant_claims_nothing(self):
        self.web('a', 'w1')
        with self.e.tx() as c:
            c.execute('INSERT INTO p_freeze VALUES(?,1)', ('a',))
        self.assertIsNone(self.e.claim_event('a'))


class RoutingTests(EventPoolCase):
    def test_a_slow_model_plan_does_not_delay_a_conversation_turn_or_an_engine_step(self):
        self.web('a', 'w1')
        self.chat('b', 'm1')
        self.e.submit('a', 'cron', 'job-1', 'ops', [{'tool': 'reports.summary', 'args': {}}], 'owner')
        routing = self.routing()
        pool = self.pool(routing)
        self.assertTrue(pool.pump(['a', 'b']))
        self.assertTrue(self.model_started.wait(5), 'the model call never started')
        started = time.monotonic()
        # The main thread is free while tenant a's model call waits.
        self.assertTrue(routing.process_fast('b'))
        self.assertTrue(self.e.tick('a', 'cloud:test'))
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0)
        self.assertEqual('queued', self.turn('b', 'm1'))
        self.assertEqual('processing', self.event('a', 'w1')['status'])
        self.gate.set()
        pool.shutdown()
        self.assertEqual('done', self.event('a', 'w1')['status'])

    def test_the_main_thread_never_claims_a_model_channel_event(self):
        self.web('a', 'w1')
        self.assertFalse(self.routing().process_fast('a'))
        self.assertEqual('pending', self.event('a', 'w1')['status'])
        self.assertEqual([], self.planned)

    def test_the_pool_never_claims_a_conversation_channel_event(self):
        self.chat('b', 'm1')
        routing = self.routing()
        pool = self.pool(routing)
        pool.pump(['b'])
        self.assertEqual({}, pool.inflight)
        self.assertEqual('pending', self.event('b', 'm1')['status'])
        self.assertTrue(routing.process_fast('b'))
        self.assertEqual('queued', self.turn('b', 'm1'))

    def test_an_unreadable_pack_sends_every_event_to_the_pool_planner(self):
        # Before the split a broken pack failed each event with its error type;
        # the channel lookup failing must not strand events as pending instead.
        def broken(tenant):
            raise RuntimeError(SECRET)
        self.chat('b', 'm1')
        routing = self.routing(fast=broken)
        self.assertFalse(routing.process_fast('b'))
        pool = self.pool(routing)
        pool.pump(['b'])
        pool.shutdown()
        self.assertEqual('done', self.event('b', 'm1')['status'])
        self.assertNotIn(SECRET, repr(self.logged))

    def test_conversation_channels_are_the_channels_a_conversation_agent_owns(self):
        agents = [
            {'id': 'ops', 'conversation': {'enabled': False},
             'triggers': [{'type': 'message', 'source': 'web'}]},
            {'id': 'bot', 'conversation': {'enabled': True},
             'triggers': [{'type': 'message', 'source': 'telegram'},
                          {'type': 'message', 'source': 'whatsapp'},
                          {'type': 'schedule', 'source': 'cron'}]},
        ]
        self.assertEqual(frozenset({'telegram', 'whatsapp'}),
                         conversation_channels(lambda tenant: agents, 'a', conversation_agent))
        self.assertEqual(frozenset(), conversation_channels(lambda tenant: [], 'a', conversation_agent))


class PoolTests(EventPoolCase):
    def test_no_event_is_planned_twice_by_two_workers_on_one_database(self):
        self.gate.set()
        self.delay = 0.005
        tenants = ['a', 'b']
        for tenant in tenants:
            for index in range(8):
                self.web(tenant, 'w%d' % index, '%s-w%d' % (tenant, index))
                self.chat(tenant, 'm%d' % index, '%s-m%d' % (tenant, index))
        errors = []

        def worker(engine):
            routing = self.routing(engine=engine)
            pool = PlannerPool(AgentLoop(engine), lambda tenant, context: None, events=routing,
                               workers=3, log=lambda *args: errors.append(args))
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    active = pool.pump(tenants)
                    for tenant in tenants:
                        active = routing.process_fast(tenant) or active
                    with engine.read() as c:
                        left = c.execute("SELECT count(*) n FROM p_events WHERE status IN ('pending','processing')").fetchone()['n']
                    if not left and not pool.inflight:
                        return
                    pool.idle(0.01)
            finally:
                pool.shutdown()
        # Two Engine objects on one file: two worker processes as far as SQL can tell.
        engines = [self.e, Engine(self.path, build_registry(lambda tenant, query: []), policy,
                                  clock=lambda: self.now)]
        threads = [threading.Thread(target=worker, args=(engine,)) for engine in engines]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual([], errors)
        counts = collections.Counter(text for _, _, text in self.planned)
        self.assertEqual(32, len(counts))
        self.assertEqual({1}, set(counts.values()), 'an event was planned twice')
        with self.e.read() as c:
            self.assertEqual(32, c.execute("SELECT count(*) n FROM p_events WHERE status='done'").fetchone()['n'])
            self.assertEqual(16, c.execute('SELECT count(*) n FROM p_tasks').fetchone()['n'])
            self.assertEqual(16, c.execute('SELECT count(*) n FROM p_conversation_turns').fetchone()['n'])

    def test_a_claim_abandoned_mid_plan_is_recovered_when_its_lease_runs_out(self):
        self.gate.set()
        self.web('a', 'w1')
        dead = self.e.claim_event('a')  # a worker claimed it, then died mid-plan
        self.now += E.EVENT_LEASE_SECONDS - 1
        self.assertIsNone(self.e.claim_event('a'), 'still leased: nobody may plan it twice')
        self.now += 2
        recovered = self.e.claim_event('a')
        self.assertEqual('w1', recovered['event_key'])
        self.assertNotEqual(dead['claim'], recovered['claim'])
        self.assertTrue(self.e.settle_event('a', recovered, self.planner))
        task = self.event('a', 'w1')
        # The dead worker's late commit neither plans again nor overwrites the outcome.
        self.e.settle_event('a', dead, self.planner)
        self.assertEqual(1, len(self.planned))
        self.assertEqual(task, self.event('a', 'w1'))
        self.assertEqual('done', task['status'])

    def test_a_crash_after_the_task_was_submitted_is_not_planned_again(self):
        self.web('a', 'w1')
        self.e.claim_event('a')
        # The dead worker planned and submitted, then died before its commit.
        task = self.e.submit('a', 'web', 'w1', 'ops', STEPS['steps'], 'owner')
        self.now += E.EVENT_LEASE_SECONDS + 1
        recovered = self.e.claim_event('a')
        self.assertTrue(self.e.settle_event('a', recovered, self.planner))
        self.assertEqual([], self.planned)
        self.assertIn(task, self.event('a', 'w1')['result'])

    def test_shutdown_waits_for_an_event_plan_and_commits_it(self):
        self.web('a', 'w1')
        pool = self.pool(self.routing())
        pool.pump(['a'])
        self.assertTrue(self.model_started.wait(5))
        opener = threading.Timer(0.3, self.gate.set)
        opener.start()
        self.addCleanup(opener.cancel)
        pool.shutdown()
        row = self.event('a', 'w1')
        self.assertEqual(('done', '', 0), (row['status'], row['claim'], row['lease']))

    def test_event_plans_share_the_tenant_bound(self):
        for index in range(3):
            self.web('a', 'w%d' % index)
        pool = self.pool(self.routing(), per_tenant=2)
        pool.pump(['a'])
        pool.pump(['a'])
        self.assertEqual(2, len(pool.inflight))
        self.assertEqual({('a', None)}, set(pool.inflight.values()))

    def test_event_plans_count_against_the_budget_parallel_limit(self):
        UsageBudget(self.e).configure('a', 'owner', 'USD', 10 ** 6, 1)
        for index in range(3):
            self.web('a', 'w%d' % index)
        pool = self.pool(self.routing())
        pool.pump(['a'])
        self.assertEqual(1, len(pool.inflight))

    def test_a_failing_claim_or_plan_costs_that_tenant_only_and_logs_the_type(self):
        self.gate.set()
        self.web('bad', 'w1')
        self.web('b', 'w1')
        routing = self.routing()
        claim = routing.claim

        def failing_claim(tenant):
            if tenant == 'bad':
                raise RuntimeError(SECRET)
            return claim(tenant)
        routing.claim = failing_claim
        pool = self.pool(routing)
        pool.pump(['bad', 'b'])
        pool.shutdown()
        self.assertEqual('done', self.event('b', 'w1')['status'])
        self.assertIn('process_event', repr(self.logged))
        self.assertIn('RuntimeError', repr(self.logged))
        self.assertNotIn(SECRET, repr(self.logged))


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main()
