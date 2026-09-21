"""Real temporary SQLite ledger tests. Model usage is an offline fake receipt."""
import concurrent.futures
import datetime
import tempfile
import unittest
from pathlib import Path

from platform_runtime.engine import Engine, Conflict, Forbidden, NotFound, RateLimited, digest
from platform_runtime.tools import build_registry
from platform_runtime.usage_budget import (UsageBudget, amount, bounded,
                                           metered_completion, token_cost)

class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=[datetime.datetime(2026,9,14,tzinfo=datetime.timezone.utc).timestamp()]
        self.e=Engine(Path(self.tmp.name)/'budget.db',build_registry(),lambda t,a:{},clock=lambda:self.now[0])
        self.b=UsageBudget(self.e);self.b.configure('a','owner','USD',100,10)
    def reserve(self,key='one',amount=50,tenant='a'):
        return self.b.reserve(tenant,key,digest({'key':key}),amount,'USD')
    def test_reserve_and_settle_real_ledger(self):
        r=self.reserve();self.assertEqual(50,self.b.summary('a')['reserved_micro'])
        self.b.dispatch('a',r);self.b.settle('a',r,20)
        s=self.b.summary('a');self.assertEqual(20,s['spent_micro']);self.assertEqual(0,s['reserved_micro']);self.assertEqual(0,s['inflight'])
    def test_exhaustion_blocks_before_reservation(self):
        self.reserve(amount=70)
        with self.assertRaises(RateLimited):self.reserve('two',31)
        self.assertEqual(70,self.b.summary('a')['reserved_micro'])
    def test_reservation_replay_idempotent(self):
        first=self.reserve();self.assertEqual(first,self.reserve());self.assertEqual(1,self.b.summary('a')['inflight'])
    def test_request_conflict(self):
        self.reserve()
        with self.assertRaises(Conflict):self.reserve(amount=49)
    def test_dispatch_only_once(self):
        r=self.reserve();self.b.dispatch('a',r)
        with self.assertRaises(Conflict):self.b.dispatch('a',r)
    def test_settlement_replay_exact(self):
        r=self.reserve();self.b.dispatch('a',r);self.b.settle('a',r,20);self.b.settle('a',r,20)
        self.assertEqual(20,self.b.summary('a')['spent_micro'])
        with self.assertRaises(Conflict):self.b.settle('a',r,21)
    def test_uncertain_never_refunded(self):
        r=self.reserve();self.b.dispatch('a',r);self.b.uncertain('a',r);self.b.uncertain('a',r)
        self.assertEqual(50,self.b.summary('a')['reserved_micro'])
        with self.assertRaises(Conflict):self.b.cancel('a',r)
        with self.assertRaises(Conflict):self.b.settle('a',r,0)
    def test_owner_reconcile_with_evidence(self):
        r=self.reserve();self.b.dispatch('a',r);self.b.uncertain('a',r)
        self.b.reconcile('a',r,'owner',25,'provider invoice reference 42')
        self.assertEqual(25,self.b.summary('a')['spent_micro']);self.assertEqual(0,self.b.summary('a')['reserved_micro'])
        with self.assertRaises(Conflict):self.b.reconcile('a',r,'owner',25,'same invoice')
    def test_reconcile_requires_evidence(self):
        r=self.reserve()
        with self.assertRaises(ValueError):self.b.reconcile('a',r,'owner',0,'')
    def test_cancel_before_dispatch(self):
        r=self.reserve();self.b.cancel('a',r);self.b.cancel('a',r)
        self.assertEqual(100,self.b.summary('a')['available_micro'])
        with self.assertRaises(Conflict):self.b.dispatch('a',r)
    def test_overrun_is_recorded_not_clipped(self):
        r=self.reserve();self.b.dispatch('a',r);self.b.settle('a',r,120)
        self.assertTrue(self.b.summary('a')['limit_exceeded']);self.assertEqual(120,self.b.summary('a')['spent_micro'])
        with self.assertRaises(RateLimited):self.reserve('two',1)
    def test_tenant_isolation(self):
        r=self.reserve();self.b.configure('b','owner','USD',100)
        self.assertEqual(0,self.b.summary('b')['reserved_micro'])
        for fn in [lambda:self.b.dispatch('b',r),lambda:self.b.reconcile('b',r,'owner',0,'proof')]:
            with self.assertRaises(NotFound):fn()
    def test_parallel_limit(self):
        self.b.configure('a','owner','USD',100,1);self.reserve(amount=1)
        with self.assertRaises(RateLimited):self.reserve('two',1)
    def test_month_boundary_settles_original_account(self):
        r=self.reserve();self.b.dispatch('a',r)
        self.now[0]=datetime.datetime(2026,10,1,tzinfo=datetime.timezone.utc).timestamp()
        self.b.settle('a',r,20)
        self.assertEqual(0,self.b.summary('a')['spent_micro'])
        with self.e.read() as db:self.assertEqual(20,db.execute("SELECT spent_micro FROM p_budget_accounts WHERE period='2026-09'").fetchone()[0])
    def test_prior_month_pending_still_blocks_parallelism(self):
        self.b.configure('a','owner','USD',100,1);self.reserve()
        self.now[0]=datetime.datetime(2026,10,1,tzinfo=datetime.timezone.utc).timestamp()
        with self.assertRaises(RateLimited):self.reserve('two',1)
    def test_currency_cannot_be_reinterpreted(self):
        with self.assertRaises(Conflict):self.b.configure('a','owner','UZS',100)
        with self.assertRaises(Forbidden):self.b.reserve('a','one',digest(1),1,'UZS')
    def test_configuration_authority(self):
        def auth(c,t,ch='',actor='',roles=()):
            if ch and (actor!='owner' or roles!=('owner',)):raise Forbidden('Owner required')
        self.e.authority=auth
        with self.assertRaises(Forbidden):self.b.configure('a','operator','USD',100)
        self.b.configure('a','owner','USD',100)
        r=self.reserve()
        with self.assertRaises(Forbidden):self.b.reconcile('a',r,'operator',0,'proof')
    def test_freeze_blocks_reserve_and_dispatch(self):
        r=self.reserve()
        with self.e.tx() as db:db.execute("INSERT INTO p_freeze VALUES('a',1)")
        with self.assertRaises(Forbidden):self.reserve('two')
        with self.assertRaises(Forbidden):self.b.dispatch('a',r)
        self.b.cancel('a',r)
    def test_parallel_reservations_cannot_overspend(self):
        def work(n):
            try:self.reserve(str(n),30);return True
            except RateLimited:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(3,sum(pool.map(work,range(20))))
        self.assertEqual(90,self.b.summary('a')['reserved_micro'])
    def test_duplicate_concurrent_reservations_count_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids=list(pool.map(lambda n:self.reserve('one',10),range(12)))
        self.assertEqual(1,len(set(ids)));self.assertEqual(10,self.b.summary('a')['reserved_micro'])
    def test_bad_amounts(self):
        for value in [True,-1,1.2,'2',10**16]:
            with self.subTest(value=value),self.assertRaises(ValueError):self.reserve(amount=value)
    def test_warning_threshold(self):
        self.reserve(amount=80);self.assertTrue(self.b.summary('a')['warning_80_percent'])
    def test_lowered_limit_fences_existing_reservation(self):
        r=self.reserve(amount=80);self.b.configure('a','owner','USD',50)
        with self.assertRaises(RateLimited):self.b.dispatch('a',r)
        self.b.cancel('a',r);self.assertEqual(0,self.b.summary('a')['reserved_micro'])
    def test_lowered_parallel_limit_fences_dispatch(self):
        r=self.reserve('one',10);self.reserve('two',10);self.b.configure('a','owner','USD',100,1)
        with self.assertRaises(RateLimited):self.b.dispatch('a',r)
    def test_prior_month_undispatched_requires_new_reservation(self):
        r=self.reserve()
        self.now[0]=datetime.datetime(2026,10,1,tzinfo=datetime.timezone.utc).timestamp()
        with self.assertRaises(Conflict):self.b.dispatch('a',r)
        self.b.cancel('a',r)
    def test_pending_list(self):
        r=self.reserve();self.assertEqual(r,self.b.pending('a')[0]['id'])

    def test_the_amount_floor_is_zero_or_one_depending_on_the_caller(self):
        """``amount(zero=False)`` is the floor for a LIMIT, ``zero=True`` for a charge.

        Measured: flipping ``(0 if zero else 1)`` to ``(0 if zero else 0)`` left the
        whole suite green, so the floor was unproven from the accepting side -- a
        configured budget of zero would have been accepted.
        """
        self.assertEqual(0, amount(0))
        self.assertEqual(1, amount(1, zero=False))
        with self.assertRaises(ValueError):
            amount(0, zero=False)
        with self.assertRaises(ValueError):
            self.b.configure('a', 'owner', 'USD', 0, 10)

    def test_the_parallel_call_ceiling_is_one_hundred(self):
        """Both edges: 100 is accepted and 101 is refused."""
        self.b.configure('a', 'owner', 'USD', 10 ** 6, 100)
        self.assertEqual(100, self.b.summary('a')['max_inflight'])
        for value in (0, 101, True):
            with self.subTest(max_inflight=value):
                with self.assertRaises(ValueError):
                    self.b.configure('a', 'owner', 'USD', 10 ** 6, value)

    def test_the_pending_limit_ceiling_is_one_hundred(self):
        self.assertEqual([], self.b.pending('a', 100))
        for value in (0, 101, True, None):
            with self.subTest(limit=value):
                with self.assertRaises(ValueError):
                    self.b.pending('a', value)

    def test_the_reconcile_evidence_ceiling_is_five_hundred(self):
        """Evidence is what makes a manual settlement auditable; it is bounded."""
        rid = self.reserve('evidence')
        self.b.dispatch('a', rid)
        self.b.uncertain('a', rid)
        self.b.reconcile('a', rid, 'owner', 1, 'e' * 500)
        rid2 = self.reserve('evidence2')
        with self.assertRaises(ValueError):
            self.b.reconcile('a', rid2, 'owner', 1, 'e' * 501)


class MeteringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.e=Engine(Path(self.tmp.name)/'meter.db',build_registry(),lambda t,a:{})
        self.b=UsageBudget(self.e);self.b.configure('a','owner','USD',100000,2)
        self.pricing={'currency':'USD','input_micro_per_million':1000000,'output_micro_per_million':2000000}
        self.cfg={'usage_budget':self.pricing}
        self.calls=[];self.response={'usage':{'prompt_tokens':100,'completion_tokens':20},'choices':[]}
    def transport(self,*args):self.calls.append(args);return self.response
    def invoke(self,key='one'):
        return metered_completion(self.e,'a',self.cfg,self.transport,'https://example.invalid',{'max_tokens':100,'messages':[]},{'Authorization':'test-only'},key)
    def test_metered_receipt(self):
        self.invoke();self.assertEqual(140,self.b.summary('a')['spent_micro']);self.assertEqual(1,len(self.calls))
    def test_missing_usage_retains_reserved(self):
        self.response={}
        with self.assertRaises(RuntimeError):self.invoke()
        self.assertGreater(self.b.summary('a')['reserved_micro'],0);self.assertEqual('uncertain',self.b.pending('a')[0]['status'])
    def test_invalid_usage_retains_reserved(self):
        self.response={'usage':{'prompt_tokens':True,'completion_tokens':0}}
        with self.assertRaises(RuntimeError):self.invoke()
        self.assertEqual(0,self.b.summary('a')['spent_micro'])
    def test_same_request_does_not_repeat_provider(self):
        self.invoke()
        with self.assertRaises(Conflict):self.invoke()
        self.assertEqual(1,len(self.calls))
    def test_low_budget_prevents_network(self):
        self.b.configure('a','owner','USD',1)
        with self.assertRaises(RateLimited):self.invoke()
        self.assertEqual([],self.calls)
    def test_required_config_missing(self):
        self.cfg={'usage_budget_required':True}
        with self.assertRaises(Forbidden):self.invoke()
        self.assertEqual([],self.calls)
    def test_opt_in_disabled_preserves_legacy_behavior(self):
        self.cfg={};self.assertEqual(self.response,self.invoke());self.assertEqual(0,self.b.summary('a')['spent_micro'])
    def test_fractional_microunits_round_up(self):
        self.assertEqual(1,token_cost(1,0,{'input_micro_per_million':1,'output_micro_per_million':0}))
    def test_nonboolean_required_flag_rejected(self):
        self.cfg={'usage_budget_required':'true'}
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual([],self.calls)
    def test_invalid_prices_fail_before_dispatch(self):
        self.pricing['input_micro_per_million']=-1
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual([],self.calls)

    # -------------------------------------------- fazza 30: measured bounds

    def test_a_bounded_identifier_refuses_what_the_contract_refuses(self):
        """``bounded`` and ``database.contract.text`` guard one fact and disagreed.

        Measured before the fix: this gate ACCEPTED a DEL character (0x7F) and a
        value with a leading space, while ``contract.text`` refused both -- so a
        tenant id, an actor or a piece of reconcile evidence was valid or not
        depending on which door it came through.
        """
        from platform_runtime.database import contract
        for value in ('a\x7fb', ' a', 'a ', 'a\x01b', '', '   '):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    bounded(value)
                with self.assertRaises(ValueError):
                    contract.text(value)
        self.assertEqual('a' * 256, bounded('a' * 256))
        with self.assertRaises(ValueError):
            bounded('a' * 257)

    def test_the_warning_threshold_is_inclusive_at_eighty_percent(self):
        """``(spent + reserved) * 5 >= limit * 4`` -- so exactly 80% warns."""
        self.b.configure('a', 'owner', 'USD', 100, 10)
        self.b.reserve('a', 'w79', digest({'w': 79}), 79, 'USD')
        self.assertFalse(self.b.summary('a')['warning_80_percent'])
        self.b.reserve('a', 'w1', digest({'w': 1}), 1, 'USD')
        self.assertTrue(self.b.summary('a')['warning_80_percent'])
        self.assertFalse(self.b.summary('a')['limit_exceeded'])

    def test_the_limit_is_inclusive_so_exactly_the_limit_is_not_exceeded(self):
        """``spent + reserved > limit`` -- equality is NOT an overrun."""
        self.b.configure('a', 'owner', 'USD', 100, 10)
        self.b.reserve('a', 'full', digest({'f': 1}), 100, 'USD')
        summary = self.b.summary('a')
        self.assertFalse(summary['limit_exceeded'])
        self.assertEqual(0, summary['available_micro'])
        with self.assertRaises(RateLimited):
            self.b.reserve('a', 'over', digest({'o': 1}), 1, 'USD')

    def test_the_parallel_ceiling_admits_exactly_max_inflight(self):
        """``count >= max_inflight`` refuses the NEXT one, not the ceiling one."""
        self.b.configure('a', 'owner', 'USD', 10 ** 6, 2)
        self.b.reserve('a', 'p1', digest({'p': 1}), 1, 'USD')
        self.b.reserve('a', 'p2', digest({'p': 2}), 1, 'USD')
        with self.assertRaises(RateLimited):
            self.b.reserve('a', 'p3', digest({'p': 3}), 1, 'USD')
        self.assertEqual(2, self.b.summary('a')['inflight'])

    def test_token_cost_rounds_up_and_bounds_its_inputs(self):
        pricing = {'input_micro_per_million': 1000000, 'output_micro_per_million': 2000000}
        self.assertEqual(0, token_cost(0, 0, pricing))
        self.assertEqual(1, token_cost(1, 0, pricing))
        self.assertEqual(1000000, token_cost(1000000, 0, pricing))
        # One micro per million rounds UP: a fraction of a microunit is still a cost.
        self.assertEqual(1, token_cost(1, 0, {'input_micro_per_million': 1,
                                              'output_micro_per_million': 0}))
        token_cost(10 ** 8, 0, pricing)
        with self.assertRaises(ValueError):
            token_cost(10 ** 8 + 1, 0, pricing)
        with self.assertRaises(ValueError):
            token_cost(-1, 0, pricing)

    def test_a_boolean_is_never_an_amount(self):
        for value in (True, False):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    amount(value)

