"""Agent-loop regression source. Offline execution evidence is recorded in docs/verification/v033/."""
import concurrent.futures
import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime.agent_loop import (ACTIVE, MAX_HISTORY_BYTES, MAX_OBSERVATION_BYTES,
                                         MAX_SECONDS, MAX_STEPS, PLANNER_LEASE_SECONDS,
                                         AgentLoop, LoopDecisionError)
from platform_runtime.engine import (Conflict, Engine, Forbidden, NotFound,
                                     RateLimited)
from platform_runtime.tools import Registry, Tool, obj, string, build_registry


def reserve_process(path, run_id):
    e = Engine(path, build_registry(), lambda tenant, agent: {
        'tools': ['reports.summary'], 'ladder': 'autonomous'}, clock=lambda: 1000)
    _, reservation = AgentLoop(e)._reserve('tenant', run_id)
    return bool(reservation)


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'loop.db'
        self.now = 1000
        self.writes = []
        self.registry = Registry()
        self.registry.add(Tool('lookup.customer', 'read', obj({}), lambda *args: {'customer_id': 'cust-7'}))
        self.registry.add(Tool('lookup.orders', 'read', obj({'customer_id': string(128)}),
                               lambda *args: {'total_minor': 4200, 'currency': 'UZS'}))
        self.registry.add(Tool('test.write', 'write', obj({'customer_id': string(128)}), self.write, external=True))
        self.registry.add(Tool('device.read', 'read', obj({'file': string(128)}), runner=True))
        self.policy = lambda tenant, agent: {
            'tools': list(self.registry.items), 'ladder': 'autonomous',
        }
        self.e = Engine(self.path, self.registry, self.policy, clock=lambda: self.now)
        self.loop = AgentLoop(self.e)

    def write(self, engine, tenant, agent, args, key):
        self.writes.append((args, key))
        return {'external_id': 'receipt-1'}

    def create(self, key='run', **kwargs):
        return self.loop.create('tenant', key, 'ops', 'Mijoz buyurtmasini top', 'actor', **kwargs)

    def tool(self, name='lookup.customer', args=None):
        return {'action': 'tool', 'tool': name, 'args': args or {}}

    def current(self, run_id):
        return self.loop.get('tenant', run_id)

    def start_task(self, decision=None, **kwargs):
        run_id = self.create(**kwargs)
        self.loop.tick('tenant', lambda tenant, context: decision or self.tool())
        return run_id, self.current(run_id)['current_task']

    def final(self, tenant, context):
        return {'action': 'final', 'answer': 'Natija tool dalillari bilan ko‘rsatilgan.',
                'evidence_ids': [item['evidence_id'] for item in context['observations']]}

    def test_run_identity_is_idempotent_but_changed_payload_is_conflict(self):
        run_id = self.create()
        self.assertEqual(run_id, self.create())
        with self.assertRaises(Conflict):
            self.loop.create('tenant', 'run', 'ops', 'Different request', 'actor')
        with self.assertRaises(Conflict):
            self.loop.create('tenant', 'run', 'ops', 'Mijoz buyurtmasini top', 'other-actor')

    def test_run_read_is_tenant_scoped_and_hides_claim(self):
        run_id = self.create()
        with self.assertRaises(NotFound):
            self.loop.get('other', run_id)
        self.assertEqual([], self.loop.list('other'))
        _, reservation = self.loop._reserve('tenant', run_id)
        detail = self.current(run_id)
        self.assertNotIn('claim', detail)
        self.assertNotIn(reservation['claim'], json.dumps(detail))

    def test_step_and_time_budgets_are_exact_integers(self):
        for value in (True, 1.0, '1', 0, 13):
            with self.subTest(max_steps=value), self.assertRaises(ValueError):
                self.create(max_steps=value)
        for value in (True, 60.0, '60', 59, 86401):
            with self.subTest(max_seconds=value), self.assertRaises(ValueError):
                self.create(max_seconds=value)

    def test_second_step_uses_actual_first_step_result(self):
        run_id, _ = self.start_task()
        self.e.tick('tenant')
        observed = []
        def next_step(tenant, context):
            observed.extend(context['observations'])
            return self.tool('lookup.orders', {'customer_id': context['observations'][0]['result']['customer_id']})
        self.loop.tick('tenant', next_step)
        second = self.e.get('tenant', self.current(run_id)['current_task'])
        self.assertEqual({'customer_id': 'cust-7'}, second['steps'][0]['args'])
        self.assertEqual('lookup.customer', observed[0]['tool'])
        self.e.tick('tenant')
        self.loop.tick('tenant', self.final)
        result = self.current(run_id)
        self.assertEqual('succeeded', result['status'])
        self.assertEqual(2, len(result['evidence_ids']))
        self.assertEqual(3, result['calls'])
        self.assertEqual('not_performed', result['semantic_fact_check'])

    def test_waiting_task_does_not_spend_another_planner_call(self):
        run_id, _ = self.start_task()
        calls = []
        self.assertFalse(self.loop.tick('tenant', lambda *args: calls.append(args)))
        self.assertEqual([], calls)
        self.assertEqual(1, self.current(run_id)['calls'])

    def test_all_writes_still_require_per_action_approval(self):
        run_id, task_id = self.start_task(self.tool('test.write', {'customer_id': 'cust-7'}))
        self.assertFalse(self.e.tick('tenant'))
        self.assertEqual([], self.writes)
        step_id = self.e.get('tenant', task_id)['steps'][0]['id']
        self.e.approve('tenant', step_id, 'owner', 'approved', 'owner')
        self.assertTrue(self.e.tick('tenant'))
        self.assertEqual(1, len(self.writes))
        self.loop.tick('tenant', self.final)
        self.assertEqual('succeeded', self.current(run_id)['status'])

    def test_claimed_running_task_cancel_is_uncertain_and_fenced(self):
        run_id, task_id = self.start_task()
        step = self.e.claim('tenant', 'worker')
        self.assertEqual('uncertain', self.loop.cancel('tenant', run_id, 'owner'))
        self.assertEqual('uncertain', self.e.get('tenant', task_id)['status'])
        with self.assertRaises(Conflict):
            self.e.finish('tenant', step['id'], step['claim'], {'value': 'late'})
        with self.assertRaises(Forbidden):
            self.e.dispatch_allowed('tenant', step)

    def test_pending_cancel_is_idempotent_and_does_not_call_model(self):
        run_id = self.create()
        self.assertEqual('cancelled', self.loop.cancel('tenant', run_id, 'owner'))
        self.assertEqual('cancelled', self.loop.cancel('tenant', run_id, 'owner'))
        calls = []
        self.assertFalse(self.loop.tick('tenant', lambda *args: calls.append(args)))
        self.assertEqual([], calls)

    def test_cancellation_during_planning_discards_late_decision(self):
        run_id = self.create()
        def planner(tenant, context):
            self.loop.cancel(tenant, run_id, 'owner')
            return self.tool()
        self.loop.tick('tenant', planner)
        self.assertEqual('cancelled', self.current(run_id)['status'])
        self.assertEqual([], self.e.list_tasks('tenant'))

    def test_freeze_before_planning_pauses_without_spending_call(self):
        run_id = self.create()
        self.e.freeze('tenant', True, 'owner')
        calls = []
        self.assertFalse(self.loop.tick('tenant', lambda *args: calls.append(args)))
        self.assertEqual([], calls)
        self.assertEqual(0, self.current(run_id)['calls'])
        self.e.freeze('tenant', False, 'owner')
        self.loop.tick('tenant', lambda *args: self.tool())
        self.assertEqual('waiting_task', self.current(run_id)['status'])

    def test_freeze_during_planning_prevents_task_creation(self):
        run_id = self.create()
        def planner(tenant, context):
            self.e.freeze(tenant, True, 'owner')
            return self.tool()
        self.loop.tick('tenant', planner)
        self.assertEqual('escalated', self.current(run_id)['status'])
        self.assertEqual([], self.e.list_tasks('tenant'))

    def test_deadline_blocks_claim_even_without_coordinator_tick(self):
        run_id, _ = self.start_task(max_seconds=60)
        self.now += 61
        self.assertIsNone(self.e.claim('tenant', 'worker'))
        self.loop.tick('tenant', lambda *args: self.fail('No planner after deadline'))
        self.assertEqual('escalated', self.current(run_id)['status'])

    def test_deadline_blocks_approval(self):
        _, task_id = self.start_task(self.tool('test.write', {'customer_id': 'cust-7'}), max_seconds=60)
        step_id = self.e.get('tenant', task_id)['steps'][0]['id']
        self.now += 60
        with self.assertRaises(Forbidden):
            self.e.approve('tenant', step_id, 'owner', 'approved', 'owner')
        self.assertEqual([], self.writes)

    def test_deadline_includes_approval_wait_and_cancels_pending_approval(self):
        run_id, task_id = self.start_task(self.tool('test.write', {'customer_id': 'cust-7'}), max_seconds=60)
        self.e.tick('tenant')
        self.now += 61
        self.loop.tick('tenant', lambda *args: self.fail('No planner after deadline'))
        self.assertEqual('escalated', self.current(run_id)['status'])
        step = self.e.get('tenant', task_id)['steps'][0]
        self.assertEqual('cancelled', step['status'])
        self.assertEqual('rejected', step['approval_status'])

    def test_planner_lease_expiry_has_no_automatic_retry(self):
        run_id = self.create()
        self.loop._reserve('tenant', run_id)
        self.now += 61
        calls = []
        self.loop.tick('tenant', lambda *args: calls.append(args))
        self.assertEqual([], calls)
        self.assertEqual('planner_lease_expired_no_retry', self.current(run_id)['error'])
        self.assertEqual(1, self.current(run_id)['calls'])

    def test_late_model_response_cannot_submit(self):
        run_id = self.create()
        def planner(*args):
            self.now += 61
            return self.tool()
        self.loop.tick('tenant', planner)
        self.assertEqual('escalated', self.current(run_id)['status'])
        self.assertEqual([], self.e.list_tasks('tenant'))

    def test_step_budget_allows_final_call_not_another_tool(self):
        run_id, _ = self.start_task(max_steps=1)
        self.e.tick('tenant')
        self.loop.tick('tenant', lambda *args: self.tool('lookup.orders', {'customer_id': 'cust-7'}))
        self.assertEqual('escalated', self.current(run_id)['status'])
        self.assertEqual(1, len(self.e.list_tasks('tenant')))
        self.assertEqual(2, self.current(run_id)['calls'])

    def test_final_at_step_budget_boundary(self):
        run_id, _ = self.start_task(max_steps=1)
        self.e.tick('tenant')
        self.loop.tick('tenant', self.final)
        self.assertEqual('succeeded', self.current(run_id)['status'])

    def test_duplicate_action_is_not_executed_again(self):
        run_id, _ = self.start_task()
        self.e.tick('tenant')
        self.loop.tick('tenant', lambda *args: self.tool())
        self.assertEqual('escalated', self.current(run_id)['status'])
        self.assertEqual(1, len(self.e.list_tasks('tenant')))

    def test_final_requires_existing_successful_evidence(self):
        run_id = self.create()
        self.loop.tick('tenant', lambda *args: {'action': 'final', 'answer': 'Invented answer', 'evidence_ids': ['step:invented']})
        self.assertEqual('escalated', self.current(run_id)['status'])
        self.assertEqual('', self.current(run_id)['answer'])

    def test_cross_run_evidence_cannot_support_answer(self):
        first, task = self.start_task()
        self.e.tick('tenant')
        self.loop.tick('tenant', self.final)
        evidence = self.current(first)['evidence_ids']
        second = self.create(key='second')
        self.loop.tick('tenant', lambda *args: {'action': 'final', 'answer': 'Wrong context', 'evidence_ids': evidence})
        self.assertEqual('escalated', self.current(second)['status'])

    def test_model_cannot_supply_permissions_or_device(self):
        decisions = [
            {**self.tool(), 'role': 'owner'},
            {**self.tool(), 'tenant': 'other'},
            {**self.tool(), 'approval': True},
            {'action': 'tool', 'tool': 'device.read', 'args': {'file': '/secret'}},
            {'action': 'tool', 'tool': 'not.allowed', 'args': {}},
            {'action': 'tool', 'tool': 'lookup.customer', 'args': {'tenant': 'other'}},
        ]
        for i, decision in enumerate(decisions):
            with self.subTest(decision=decision):
                run_id = self.create(key='invalid-' + str(i))
                self.loop.tick('tenant', lambda *args: decision)
                self.assertEqual('escalated', self.current(run_id)['status'])
        self.assertEqual([], self.e.list_tasks('tenant'))

    def test_clarification_is_terminal_not_a_fake_success(self):
        run_id = self.create()
        self.loop.tick('tenant', lambda *args: {'action': 'ask', 'question': 'Qaysi mijoz kerak?'})
        result = self.current(run_id)
        self.assertEqual('needs_input', result['status'])
        self.assertEqual([], result['evidence_ids'])
        self.assertEqual([], self.e.list_tasks('tenant'))

    def test_provider_failure_is_sanitized_and_never_retried(self):
        run_id = self.create()
        calls = []
        def fail(*args):
            calls.append(1)
            raise RuntimeError('unit-private-provider-detail')
        self.loop.tick('tenant', fail)
        self.loop.tick('tenant', fail)
        self.assertEqual([1], calls)
        self.assertEqual('planner_failed_no_retry', self.current(run_id)['error'])
        self.assertNotIn('unit-private-provider-detail', json.dumps(self.current(run_id)))

    def test_invalid_link_audit_rolls_back_entire_task_creation(self):
        run_id = self.create()
        original = self.e.audit
        def audit(c, tenant, task, action, actor, data=None):
            if action == 'agent_run.task_linked':
                raise ValueError('unit-validation-failure')
            return original(c, tenant, task, action, actor, data)
        with patch.object(self.e, 'audit', side_effect=audit):
            self.loop.tick('tenant', lambda *args: self.tool())
        self.assertEqual([], self.e.list_tasks('tenant'))
        self.assertEqual([], self.current(run_id)['turns'])
        self.assertEqual('escalated', self.current(run_id)['status'])

    def test_oversized_observation_is_not_silently_truncated(self):
        self.registry.items['lookup.customer'] = Tool('lookup.customer', 'read', obj({}), lambda *args: {'value': 'x' * 13000})
        run_id, _ = self.start_task()
        self.e.tick('tenant')
        calls = []
        self.loop.tick('tenant', lambda *args: calls.append(args))
        self.assertEqual([], calls)
        self.assertEqual('observation_unavailable_or_too_large', self.current(run_id)['error'])

    def test_two_coordinators_cannot_reserve_same_run(self):
        run_id = self.create()
        _, first = self.loop._reserve('tenant', run_id)
        second_loop = AgentLoop(Engine(self.path, self.registry, self.policy, clock=lambda: self.now))
        changed, second = second_loop._reserve('tenant', run_id)
        self.assertTrue(first)
        self.assertFalse(changed)
        self.assertIsNone(second)
        self.assertEqual(1, self.current(run_id)['calls'])

    def test_multiprocess_planner_reservation_at_most_once(self):
        run_id = self.create()
        with concurrent.futures.ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context('spawn')) as pool:
            outcomes = list(pool.map(reserve_process, [str(self.path)] * 2, [run_id] * 2))
        self.assertEqual(1, outcomes.count(True))
        self.assertEqual(1, self.current(run_id)['calls'])


    def test_uncertain_external_write_is_never_automatically_retried(self):
        attempts=[]
        def ambiguous(*args):
            attempts.append(1)
            raise RuntimeError('unit-provider-result-unknown')
        self.registry.items['test.write']=Tool('test.write','write',obj({'customer_id':string(128)}),ambiguous,external=True)
        run_id,task_id=self.start_task(self.tool('test.write',{'customer_id':'cust-7'}))
        step_id=self.e.get('tenant',task_id)['steps'][0]['id']
        self.e.approve('tenant',step_id,'owner','approved','owner')
        self.e.tick('tenant')
        planner_calls=[]
        self.loop.tick('tenant',lambda *args:planner_calls.append(args))
        self.e.tick('tenant')
        self.loop.tick('tenant',lambda *args:planner_calls.append(args))
        self.assertEqual([1],attempts)
        self.assertEqual([],planner_calls)
        self.assertEqual('uncertain',self.current(run_id)['status'])
        self.e.reconcile('tenant',step_id,'owner','owner','failed','Provider receipt checked')
        self.assertFalse(self.loop.tick('tenant',lambda *args:planner_calls.append(args)))
        self.assertEqual('uncertain',self.current(run_id)['status'])

    def test_freeze_between_reservation_and_model_prevents_provider_call(self):
        run_id=self.create()
        original=self.loop._reserve
        def reserve_then_freeze(*args):
            result=original(*args)
            self.e.freeze('tenant',True,'owner')
            return result
        calls=[]
        with patch.object(self.loop,'_reserve',side_effect=reserve_then_freeze):
            self.loop.tick('tenant',lambda *args:calls.append(args))
        self.assertEqual([],calls)
        self.assertEqual('escalated',self.current(run_id)['status'])
        self.assertEqual([],self.e.list_tasks('tenant'))

    # ---------------------------------------------- fazza 28: measured bounds
    #
    # Every test below was added because a revert proved its absence. Seventeen
    # mutations were walked over this module's bounds and TWELVE left the suite
    # green. Two of those twelve are the interesting ones: `MAX_STEPS` and
    # `MAX_SECONDS` are pinned from the rejecting side only, so a NARROWED ceiling
    # is invisible -- `MAX_STEPS = 6` passed the whole suite while silently
    # refusing a twelve-step run.

    def test_the_step_budget_edges_are_accepted_not_merely_bounded(self):
        """`0` and `13` prove a bound EXISTS, never that it IS 12.

        The existing test walks `0` and `13` as refusals, which pins the ceiling
        from one side only: a ceiling of 6 also refuses 13. Both accepted edges are
        asserted here, so a narrowed ceiling fails.
        """
        for value in (1, MAX_STEPS):
            with self.subTest(max_steps=value):
                run_id = self.create('steps-%d' % value, max_steps=value)
                self.assertEqual(value, self.current(run_id)['max_steps'])
        for value in (0, MAX_STEPS + 1, True, 1.0, '12'):
            with self.subTest(max_steps=value):
                with self.assertRaises(ValueError):
                    self.create('steps-bad-%s' % value, max_steps=value)

    def test_the_time_budget_edges_are_accepted_not_merely_bounded(self):
        """Same shape for the wall clock: the floor is 60, not 1."""
        for value in (60, MAX_SECONDS):
            with self.subTest(max_seconds=value):
                run_id = self.create('secs-%d' % value, max_seconds=value)
                self.assertEqual(self.now + value, self.current(run_id)['deadline'])
        for value in (59, MAX_SECONDS + 1, True, 60.0, '60'):
            with self.subTest(max_seconds=value):
                with self.assertRaises(ValueError):
                    self.create('secs-bad-%s' % value, max_seconds=value)

    def test_the_run_input_ceiling_is_four_thousand_characters(self):
        for length, accepted in ((1, True), (4000, True), (4001, False)):
            with self.subTest(length=length):
                if accepted:
                    self.create(key='text-%d' % length)
                else:
                    with self.assertRaises(ValueError):
                        self.loop.create('tenant', 'text-%d' % length, 'ops',
                                         'a' * length, 'actor')
        for blank in ('', '   '):
            with self.subTest(blank=repr(blank)):
                with self.assertRaises(ValueError):
                    self.loop.create('tenant', 'blank', 'ops', blank, 'actor')

    def test_the_run_identity_limits_are_the_documented_four(self):
        """tenant 64, key 256, agent 128, actor 128 -- each walked at its edge."""
        for field, limit in (('tenant', 64), ('key', 256), ('agent', 128), ('actor', 128)):
            for length, accepted in ((limit, True), (limit + 1, False)):
                args = {'tenant': 'tenant', 'key': 'id-%d-%s' % (length, field),
                        'agent': 'ops', 'actor': 'actor'}
                args[field] = 'z' * length
                with self.subTest(field=field, length=length):
                    if accepted:
                        self.loop.create(args['tenant'], args['key'], args['agent'],
                                         'Mijoz buyurtmasini top', args['actor'])
                    else:
                        with self.assertRaises(ValueError):
                            self.loop.create(args['tenant'], args['key'], args['agent'],
                                             'Mijoz buyurtmasini top', args['actor'])

    def test_the_active_run_queue_ceiling_is_one_hundred(self):
        """99 active runs admit a hundredth; the hundred-and-first is refused."""
        for index in range(99):
            self.create(key='queue-%03d' % index)
        self.create(key='queue-099')
        with self.assertRaises(RateLimited):
            self.create(key='queue-100')

    def test_the_queue_ceiling_counts_a_waiting_run(self):
        """The set is stated once, and this is what stating it three times cost.

        A `waiting_task` run is active. If the queue count stopped including it,
        the ceiling would not apply to waiting runs at all -- and the fazza-28
        audit measured that the `create` copy of the set could drift while the
        whole suite stayed green.
        """
        # The waiting run must be the ONLY run when it is ticked: `tick` walks
        # candidates in `created` order, so a queued run would be reserved first.
        run_id, _ = self.start_task(key='wait-000')
        self.assertEqual('waiting_task', self.current(run_id)['status'])
        for index in range(1, 99):
            self.create(key='wait-%03d' % index)
        # 99 active: one waiting, ninety-eight pending.
        self.create(key='wait-099')
        with self.assertRaises(RateLimited):
            self.create(key='wait-100')

    def test_the_decision_ceilings_are_the_documented_values(self):
        row = {'tenant': 'tenant', 'agent': 'ops', 'steps': 1, 'max_steps': MAX_STEPS}
        wide = [{'evidence_id': 'step:%d' % i} for i in range(MAX_STEPS + 1)]
        ids = [item['evidence_id'] for item in wide]

        for length, accepted in ((8000, True), (8001, False)):
            with self.subTest(answer=length):
                decision = {'action': 'final', 'answer': 'a' * length, 'evidence_ids': ids[:1]}
                if accepted:
                    self.assertEqual('final', self.loop._decision(row, wide, decision))
                else:
                    with self.assertRaises(LoopDecisionError):
                        self.loop._decision(row, wide, decision)

        for length, accepted in ((1000, True), (1001, False)):
            with self.subTest(question=length):
                decision = {'action': 'ask', 'question': 'q' * length}
                if accepted:
                    self.assertEqual('ask', self.loop._decision(row, wide, decision))
                else:
                    with self.assertRaises(LoopDecisionError):
                        self.loop._decision(row, wide, decision)

        for count, accepted in ((1, True), (MAX_STEPS, True), (0, False),
                                (MAX_STEPS + 1, False)):
            with self.subTest(evidence=count):
                decision = {'action': 'final', 'answer': 'a', 'evidence_ids': ids[:count]}
                if accepted:
                    self.assertEqual('final', self.loop._decision(row, wide, decision))
                else:
                    with self.assertRaises(LoopDecisionError):
                        self.loop._decision(row, wide, decision)

    def test_the_context_byte_ceilings_are_the_documented_values(self):
        """The literals are asserted as exact lines, not as substrings."""
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'agent_loop.py').read_text(encoding='utf-8')
        lines = set(source.splitlines())
        for literal in ('MAX_STEPS = 12', 'MAX_SECONDS = 86400',
                        'MAX_OBSERVATION_BYTES = 12000', 'MAX_HISTORY_BYTES = 48000',
                        'PLANNER_LEASE_SECONDS = 60'):
            with self.subTest(literal=literal):
                self.assertIn(literal, lines)
        self.assertEqual(12000, MAX_OBSERVATION_BYTES)
        self.assertEqual(48000, MAX_HISTORY_BYTES)
        self.assertEqual(60, PLANNER_LEASE_SECONDS)

    def test_the_active_status_set_is_stated_once(self):
        """One fact, three readers -- the fix was to stop restating it.

        `_reserve` compared against ACTIVE while `create` and `tick` each spelled
        the same three statuses out inside SQL. Measured: changing the `create`
        copy so it dropped 'waiting_task' left the whole suite green, so the
        statement guarding the queue ceiling was the one nothing watched.
        """
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'agent_loop.py').read_text(encoding='utf-8')
        self.assertIn("ACTIVE = ('pending', 'planning', 'waiting_task')", source)
        self.assertIn("_ACTIVE_PLACEHOLDERS = ','.join('?' * len(ACTIVE))", source)
        self.assertEqual(0, source.count("('pending','planning','waiting_task')"))
        # One declaration plus two SQL sites. Counting the bare word 'ACTIVE'
        # is not a measurement: '_ACTIVE_PLACEHOLDERS' contains it.
        self.assertEqual(3, source.count('_ACTIVE_PLACEHOLDERS'))


if __name__ == '__main__':
    unittest.main()
