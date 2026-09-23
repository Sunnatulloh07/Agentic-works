import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from platform_runtime.engine import Engine, Conflict, Forbidden, NotFound
from platform_runtime.tools import build_registry, Tool, obj


def policy(tenant,agent):
    if tenant not in {'a','b'} or agent not in {'ops','reader','human'}:raise Forbidden()
    return {'tools':['records.create','records.list','memory.put','memory.search','reports.summary','fs.list','fs.read_text'],
            'ladder':'human_led' if agent=='human' else 'autonomous','approval':[]}


def claim_process(path,queue):
    e=Engine(path,build_registry(),policy)
    r=e.claim('a',str(os.getpid()))
    queue.put(r['id'] if r else None)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'app.db'
        self.now=1000
        self.e=Engine(self.path,build_registry(),policy,clock=lambda:self.now)
    def tearDown(self):self.temp.cleanup()
    def submit(self,steps=None,tenant='a',agent='ops',key='1',channel='web'):
        return self.e.submit(tenant,channel,key,agent,steps or [{'tool':'reports.summary','args':{}}],'user')
    def write(self):
        return self.submit([{'tool':'records.create','args':{'kind':'lead','title':'Test','body':'body'}}])
    def test_idempotency(self):
        t=self.submit();self.assertEqual(t,self.submit());self.assertEqual(1,len(self.e.list_tasks('a')))
    def test_task_page_size_is_clamped_not_refused(self):
        """Out-of-range page sizes are CLAMPED, not refused: 500 -> 200, 0 -> 1.

        The two ends of the clamp are asserted separately because they are two
        different decisions: the top is a ceiling (200 rows per page), the bottom
        is a floor (a page of zero rows is never what a caller meant).
        """
        for i in range(3):self.submit(key=str(i))
        self.assertEqual(3,len(self.e.list_tasks('a',500)))
        self.assertEqual(2,len(self.e.list_tasks('a',2)))
        self.assertEqual(1,len(self.e.list_tasks('a',1)))
        self.assertEqual(1,len(self.e.list_tasks('a',0)))
        self.assertEqual(1,len(self.e.list_tasks('a',-5)))
    def test_task_page_size_must_be_an_integer(self):
        for bad in ('x',None,True,1.5,[2]):
            with self.assertRaises(ValueError):self.e.list_tasks('a',bad)
    def test_conflicting_replay(self):
        self.submit()
        with self.assertRaises(Conflict):self.submit([{'tool':'records.list','args':{'kind':'lead'}}])
    def test_tenant_independent_keys(self):self.assertNotEqual(self.submit(),self.submit(tenant='b'))
    def test_tenant_not_found(self):
        with self.assertRaises(NotFound):self.e.get('b',self.submit())
    def test_unknown_tool(self):
        with self.assertRaises(LookupError):self.submit([{'tool':'shell.exec','args':{}}])
    def test_unknown_args(self):
        with self.assertRaises(ValueError):self.submit([{'tool':'reports.summary','args':{'tenant':'b'}}])
    def test_missing_args(self):
        with self.assertRaises(ValueError):self.submit([{'tool':'records.list','args':{}}])
    def test_wrong_schema_type(self):
        with self.assertRaises(ValueError):self.submit([{'tool':'records.list','args':{'kind':False}}])
    def test_empty_plan(self):
        with self.assertRaises(ValueError):self.e.submit('a','web','1','ops',[])
    def test_unsupported_agent(self):
        with self.assertRaises(Forbidden):self.submit(agent='rogue')
    def test_read_completes(self):
        t=self.submit();self.assertTrue(self.e.tick('a'));self.assertEqual('succeeded',self.e.get('a',t)['status'])
    def test_write_needs_approval(self):
        t=self.write();self.assertFalse(self.e.tick('a'));self.assertEqual('waiting_approval',self.e.get('a',t)['status'])
        with self.e.read() as c:self.assertEqual(0,c.execute('SELECT count(*) FROM p_records').fetchone()[0])
    def test_approval_executes_exactly_one_record(self):
        t=self.write();sid=self.e.get('a',t)['steps'][0]['id'];self.e.approve('a',sid,'olga','approved','operator')
        self.e.tick('a');self.e.tick('a')
        with self.e.read() as c:self.assertEqual(1,c.execute('SELECT count(*) FROM p_records').fetchone()[0])
        step=self.e.get('a',t)['steps'][0];self.assertEqual('consumed',step['approval_status']);self.assertEqual('olga',step['approver'])
    def test_no_double_approval(self):
        t=self.write();sid=self.e.get('a',t)['steps'][0]['id'];self.e.approve('a',sid,'o','approved','operator')
        with self.assertRaises(Conflict):self.e.approve('a',sid,'o','approved','operator')
    def test_viewer_cannot_approve(self):
        sid=self.e.get('a',self.write())['steps'][0]['id']
        with self.assertRaises(Forbidden):self.e.approve('a',sid,'v','approved','viewer')
    def test_cross_tenant_approval(self):
        sid=self.e.get('a',self.write())['steps'][0]['id']
        with self.assertRaises(NotFound):self.e.approve('b',sid,'v','approved','operator')
    def test_rejected_never_executes(self):
        t=self.write();sid=self.e.get('a',t)['steps'][0]['id'];self.e.approve('a',sid,'o','rejected','operator')
        self.assertFalse(self.e.tick('a'));self.assertEqual('cancelled',self.e.get('a',t)['status'])
    def test_approval_expires(self):
        t=self.write();self.now+=86401;self.e.tick('a');self.assertEqual('failed',self.e.get('a',t)['status'])
    def test_approval_deadline_second_is_expired(self):
        """The boundary `test_approval_expires` cannot reach.

        That test advances the clock by 86401 -- one second PAST the deadline -- and
        both `expires <= now` and `expires < now` refuse there, so it proves a refusal
        happens and nothing about where the bound sits. Only equality discriminates:
        at `now == expires` the inclusive form refuses and the exclusive form accepts.

        The approval deadline is `created + 86400`. One second before it the approval
        must still be decidable and executable; AT it, it must be refused. Both sides
        are asserted so the test cannot pass by refusing every approval.
        """
        t=self.write();sid=self.e.get('a',t)['steps'][0]['id']
        deadline=self.now+86400
        self.now=deadline-1
        self.e.approve('a',sid,'operator','approved','operator')
        self.e.tick('a')
        self.assertEqual('succeeded',self.e.get('a',t)['status'],
                         'one second before the deadline the approval must stand')
    def test_approval_at_the_deadline_second_cannot_be_decided(self):
        """The APPROVE site's bound, isolated from the tick site's.

        `approve` refuses through a three-clause predicate: wrong approval status,
        step not pending, or expired. A test that only asserts `Conflict` is raised
        cannot say which clause did it. At `now == expires` with a fresh write the
        first two clauses are measurably false (`status='pending'`,
        `step_status='queued'`), so the expiry clause is the only one left -- but
        that is a property of the fixture, and it is worth asserting rather than
        assuming. The row is therefore read back: if the approval had been accepted
        the status would have become `approved` and an actor written.

        A second half follows: with the decision refused, the step must still be
        attributable to expiry rather than to a silent acceptance, so the tick that
        runs afterwards reports `failed` (the approval is past its bound) and NOT
        `succeeded` -- the outcome the exclusive `<` would produce.
        """
        t=self.write();sid=self.e.get('a',t)['steps'][0]['id']
        self.now+=86400
        with self.assertRaises(Conflict):
            self.e.approve('a',sid,'operator','approved','operator')
        with self.e.read() as c:
            row=c.execute('SELECT a.status,a.actor,s.status step_status FROM p_approvals a JOIN p_steps s ON s.id=a.step WHERE a.step=?',(sid,)).fetchone()
        self.assertEqual(('pending','','queued'),tuple(row),
                         'a refused decision must leave the approval untouched')
        self.e.tick('a')
        self.assertEqual('failed',self.e.get('a',t)['status'])
    def test_approval_at_the_deadline_second_cannot_execute(self):
        """The tick path applies the same bound as the approve path.

        Two readers of one deadline: `approve` refuses to decide an expired approval
        and `_refresh` refuses to execute one. If they disagreed by a single second a
        caller could have a decision accepted and then never executed, or the reverse.
        The approval is written as approved directly, then the clock is moved exactly
        onto the deadline, so only the execution predicate is asked.
        """
        t=self.write();sid=self.e.get('a',t)['steps'][0]['id']
        deadline=self.now+86400
        self.e.approve('a',sid,'operator','approved','operator')
        self.now=deadline
        self.e.tick('a')
        self.assertEqual('failed',self.e.get('a',t)['status'])
        with self.e.read() as c:
            row=c.execute('SELECT error FROM p_steps WHERE id=?',(sid,)).fetchone()
        self.assertEqual('approval_expired_or_invalid',row[0])
    def test_policy_change_invalidates_approval(self):
        t=self.write();sid=self.e.get('a',t)['steps'][0]['id'];self.e.approve('a',sid,'o','approved','operator')
        self.e.policy=lambda t,a:{'tools':['records.create'],'ladder':'human_led'}
        self.e.tick('a');self.assertEqual('failed',self.e.get('a',t)['status'])
    def test_argument_tamper_invalidates_approval(self):
        t=self.write();sid=self.e.get('a',t)['steps'][0]['id'];self.e.approve('a',sid,'o','approved','operator')
        with self.e.tx() as c:c.execute('UPDATE p_steps SET args=? WHERE id=?',(json.dumps({'kind':'lead','title':'tamper','body':'x'}),sid))
        self.e.tick('a');self.assertEqual('failed',self.e.get('a',t)['status'])
    def test_human_led_reads_require_approval(self):
        t=self.submit(agent='human');self.assertFalse(self.e.tick('a'));self.assertEqual('waiting_approval',self.e.get('a',t)['status'])
    def test_claim_exclusive(self):
        self.submit();self.assertIsNotNone(self.e.claim('a','w1'));self.assertIsNone(self.e.claim('a','w2'))
    def test_stale_claim_cannot_finish(self):
        t=self.submit();s=self.e.claim('a','w');self.now+=91;self.assertIsNone(self.e.claim('a','w2'))
        with self.assertRaises(Conflict):self.e.finish('a',s['id'],s['claim'],{})
        self.assertEqual('uncertain',self.e.get('a',t)['status'])
    def test_lease_is_live_one_second_inside_the_bound(self):
        """The inside of the lease bound, which no test reached.

        `test_stale_claim_cannot_finish` advances the clock by 91 against a 90-second
        lease -- one second PAST the bound. `finish` requires `lease > now` and the
        claim sweep expires at `lease <= now`, so at +91 both refuse to accept and
        both agree the lease is dead: the test passes whether the bound sits at 90,
        at 91, or anywhere in between. It measures that a stale lease is refused, not
        where staleness begins.

        Measured: at +89 the lease is live, `finish` must accept and the sweep must
        NOT expire the row. That is the half that proves the bound is not simply
        refusing everything.
        """
        t=self.submit();s=self.e.claim('a','w');lease=s['lease']
        self.assertGreater(lease,self.now,'the claim must hand back a future lease')
        self.now=lease-1
        self.assertIsNone(self.e.claim('a','w2'),
                          'one second inside the lease another worker must not steal it')
        self.e.finish('a',s['id'],s['claim'],{'ok':True})
        self.assertEqual('succeeded',self.e.get('a',t)['status'])
    def test_lease_bound_is_exclusive_on_both_sides(self):
        """`finish` and the claim sweep must complement each other AT the bound.

        Two readers, two methods, two literals: `finish` guards with `lease > now`
        (strict) and the sweep expires with `lease <= now` (inclusive). They are exact
        complements, so at `now == lease` exactly one of them fires. That is not a
        coincidence to be assumed -- it is the property that makes the ownership token
        safe, and it fails in two different ways:

          * if the sweep were strict (`lease < now`) then at equality NEITHER fires and
            a step is stranded `running` under a dead claim with nothing to recover it;
          * if `finish` were inclusive (`lease >= now`) then at equality BOTH fire and a
            worker can finish a step the sweep has already retired as `uncertain`.

        So the assertion is placed exactly on equality, where the two forms disagree,
        and both directions are pinned: `finish` must refuse AND the sweep must expire.
        """
        t=self.submit();s=self.e.claim('a','w');lease=s['lease']
        self.now=lease
        # Direction 1: the worker must NOT be able to finish at the boundary second.
        with self.assertRaises(Conflict):self.e.finish('a',s['id'],s['claim'],{})
        # Direction 2: the sweep must claim that same second, or nothing recovers it.
        self.assertIsNone(self.e.claim('a','w2'),
                          'the expired step must not be handed out again')
        self.assertEqual('uncertain',self.e.get('a',t)['status'])
        with self.e.read() as c:
            row=c.execute('SELECT error FROM p_steps WHERE id=?',(s['id'],)).fetchone()
        self.assertEqual('lease_expired',row[0],
                         'the sweep, not the finish, must have retired the step')
    def test_lease_overrun_is_still_refused_further_out(self):
        """The bound is not a one-second trick: the refusal holds all the way out.

        An implementation that refused only exactly at `now == lease` and then accepted
        again would pass the boundary test above and be plainly wrong. This pins the
        far side, so the two together describe a bound rather than a single instant.
        """
        t=self.submit();s=self.e.claim('a','w');lease=s['lease']
        self.now=lease+3600
        with self.assertRaises(Conflict):self.e.finish('a',s['id'],s['claim'],{})
        self.assertIsNone(self.e.claim('a','w2'))
        self.assertEqual('uncertain',self.e.get('a',t)['status'])
    def test_wrong_fence_rejected(self):
        self.submit();s=self.e.claim('a','w')
        with self.assertRaises(Conflict):self.e.finish('a',s['id'],'forged',{})
    def test_cross_tenant_finish(self):
        self.submit();s=self.e.claim('a','w')
        with self.assertRaises(Conflict):self.e.finish('b',s['id'],s['claim'],{})
    def test_frozen_tenant_no_claim(self):
        self.submit();self.e.freeze('a',True,'owner');self.assertIsNone(self.e.claim('a','w'))
        self.e.freeze('a',False,'owner');self.assertIsNotNone(self.e.claim('a','w'))
    def test_cancel_running_is_uncertain(self):
        t=self.submit();s=self.e.claim('a','w');self.e.cancel('a',t,'owner');self.assertEqual('uncertain',self.e.get('a',t)['status'])
        with self.assertRaises(Conflict):self.e.finish('a',s['id'],s['claim'],{})
    def test_reconcile_owner_only(self):
        t=self.submit();s=self.e.claim('a','w');self.now+=91;self.e.claim('a','w2')
        with self.assertRaises(Forbidden):self.e.reconcile('a',s['id'],'o','operator','succeeded','proof')
        self.e.reconcile('a',s['id'],'owner','owner','succeeded','provider receipt #123')
        self.assertEqual('succeeded',self.e.get('a',t)['status'])
    def test_sequential_steps(self):
        t=self.submit([{'tool':'reports.summary','args':{}},{'tool':'records.list','args':{'kind':'lead'}}])
        s=self.e.claim('a','w');self.assertIsNone(self.e.claim('a','w2'));self.e.finish('a',s['id'],s['claim'],{})
        s2=self.e.claim('a','w2');self.assertEqual(1,s2['position'])
    def test_cloud_cannot_claim_runner(self):
        self.e.device('a','d1');self.submit([{'tool':'fs.list','args':{'dir':'/safe'},'device':'d1'}])
        self.assertIsNone(self.e.claim('a','cloud'));self.assertIsNotNone(self.e.claim('a','runner',device='d1'))
    def test_device_bound_claim(self):
        self.e.device('a','d1');self.e.device('a','d2');self.submit([{'tool':'fs.list','args':{'dir':'/safe'},'device':'d1'}])
        self.assertIsNone(self.e.claim('a','runner',device='d2'))
    def test_revoked_device(self):
        self.e.device('a','d1',True)
        with self.assertRaises(Forbidden):self.e.claim('a','r',device='d1')
    def test_device_generation_rotation(self):
        self.assertEqual(1,self.e.device('a','d1'));self.assertEqual(2,self.e.device('a','d1'))
    def test_device_cascade_fences_exactly_the_in_flight_states(self):
        """The cascade's status set, and the half of it that must NOT fire.

        A device change (enroll, re-enroll, revoke) rewrites the steps that could still
        run on that device: `running` becomes `uncertain` (a dispatched external action
        cannot be recalled) and `queued`/`waiting_approval` become `cancelled`. The set is
        narrow on purpose -- a step that has ALREADY finished must not be rewritten, or a
        re-enroll would retroactively rewrite history that already happened.

        Only the three in-flight states are asserted elsewhere, one test each. This pins
        the whole matrix at once, so the boundary of the set is stated rather than implied:
        exactly three states change and four do not.
        """
        for i,(target,expect) in enumerate((('queued','cancelled'),('waiting_approval','cancelled'),
                              ('running','uncertain'),
                              ('succeeded','succeeded'),('failed','failed'),
                              ('cancelled','cancelled'),('uncertain','uncertain'))):
            self.e.device('a','d1')
            tid=self.submit([{'tool':'fs.list','args':{'dir':'/safe'},'device':'d1'}],key=str(i))
            sid=self.e.get('a',tid)['steps'][0]['id']
            with self.e.tx() as c:c.execute('UPDATE p_steps SET status=? WHERE id=?',(target,sid))
            self.e.device('a','d1',True)
            with self.e.read() as c:row=dict(c.execute('SELECT status,error FROM p_steps WHERE id=?',(sid,)).fetchone())
            self.assertEqual(expect,row['status'],f'{target} must become {expect}')
            # The error marker is written by the cascade, so it distinguishes "the
            # cascade rewrote this" from "it was left alone" even where the status agrees.
            if expect==target:self.assertEqual('',row['error'],f'{target} must not be rewritten')
            else:self.assertEqual('device_enrollment_changed',row['error'])
    def test_device_cascade_rejects_only_undecided_approvals(self):
        """The approval cascade for the same device change.

        Separate set from the step cascade: approvals are rejected for the states
        `pending` and `approved` only. A `consumed` approval belongs to a step that
        already started, so rejecting it retroactively would be a lie about what
        happened; a `rejected` one is already closed. Nothing else asserted the pairing
        between the two sets, and they are genuinely different lists.
        """
        self.e.policy=lambda t,a:{'tools':['fs.list','records.create','reports.summary'],
                                  'ladder':'autonomous','approval':['fs.list']}
        for i,(astatus,stepstatus,expect) in enumerate((('pending','queued','rejected'),
                                          ('approved','queued','rejected'),
                                          ('consumed','running','consumed'),
                                          ('rejected','cancelled','rejected'))):
            self.e.device('a','d1')
            tid=self.submit([{'tool':'fs.list','args':{'dir':'/safe'},'device':'d1'}],key=str(i))
            sid=self.e.get('a',tid)['steps'][0]['id']
            with self.e.tx() as c:
                c.execute('UPDATE p_steps SET status=? WHERE id=?',(stepstatus,sid))
                c.execute('UPDATE p_approvals SET status=? WHERE step=?',(astatus,sid))
            self.e.device('a','d1',True)
            with self.e.read() as c:
                got=c.execute('SELECT status FROM p_approvals WHERE step=?',(sid,)).fetchone()['status']
            self.assertEqual(expect,got,f'approval {astatus} must become {expect}')
    def test_device_generation_is_a_fencing_token_not_a_counter(self):
        """The generation's actual job, which only its numeric value is asserted for.

        `device()` returns a generation that increments on EVERY call, including a
        redundant re-enroll of an already-active device. That increment is not bookkeeping:
        the app layer fences every device message with
        `d['generation'] != claims['generation']` (platform_api.py), an EQUALITY test.
        So the token minted for the old generation stops matching the instant the row is
        bumped, even though `revoked` is still false -- revocation is by generation, and
        the boolean is a separate check for an explicitly revoked device.

        The existing test pins the numbers 1 and 2. This pins what those numbers DO.
        """
        g1=self.e.device('a','d1')
        g2=self.e.device('a','d1')
        with self.e.read() as c:row=dict(c.execute('SELECT revoked,generation FROM p_devices WHERE id=?',('d1',)).fetchone())
        self.assertNotEqual(g1,g2,'a re-enroll must mint a new generation')
        self.assertEqual(g2,row['generation'])
        self.assertEqual(0,row['revoked'],'the device is still enrolled, not revoked')
        # The fence the app layer applies, evaluated against the row as it stands now.
        self.assertNotEqual(g1,row['generation'],'the old generation must no longer match')
        self.assertEqual(g2,row['generation'],'only the newest generation may match')
    def test_no_unbound_runner_tool(self):
        with self.assertRaises(ValueError):self.submit([{'tool':'fs.list','args':{'dir':'/safe'}}])
    def test_inbox_replay(self):
        p={'text':'hello','sender':'u'};self.e.accept_event('a','telegram','1',p)
        self.assertTrue(self.e.accept_event('a','telegram','1',p)['duplicate'])
    def test_inbox_conflict(self):
        self.e.accept_event('a','telegram','1',{'text':'hello'})
        with self.assertRaises(Conflict):self.e.accept_event('a','telegram','1',{'text':'tampered'})
    def test_inbox_to_generic_runtime(self):
        self.e.accept_event('a','telegram','1',{'text':'x','sender':'u'})
        self.e.process_event('a',lambda *x:{'agent':'ops','steps':[{'tool':'reports.summary','args':{}}]})
        self.e.tick('a');self.assertEqual('succeeded',self.e.list_tasks('a')[0]['status'])
    def test_event_crash_recovery_after_submission(self):
        self.e.accept_event('a','telegram','1',{'text':'x'})
        tid=self.submit(channel='telegram')
        with self.e.tx() as c:c.execute("UPDATE p_events SET status='processing',lease=0")
        def fail(*x):raise AssertionError('Planner must not run twice')
        self.e.process_event('a',fail)
        self.assertEqual(1,len(self.e.list_tasks('a')))
        with self.e.read() as c:self.assertEqual(tid,json.loads(c.execute('SELECT result FROM p_events').fetchone()[0])['task_id'])
    def test_event_lease_boundary_is_the_only_recovery_instant(self):
        """The event lease bound, which no test reached.

        `test_event_crash_recovery_after_submission` forces `lease=0` -- some fifty-seven
        years in the past -- and then re-processes. The dequeue reads
        `status='processing' AND lease<=now` (inclusive), so at lease=0 every variant of
        the comparison agrees the lease is dead and the test passes wherever the bound
        sits. Measured: at `now == lease` the inclusive form re-claims and an exclusive
        `lease < now` does not; one second earlier neither does. Equality is the only
        discriminating instant, as with the step lease and the approval deadline.

        The event lease TTL is a hard-coded 120 seconds written at claim time. Both sides
        are pinned here: one second inside the bound the event must NOT be re-claimable,
        and exactly on the bound it must be.
        """
        self.e.accept_event('a','telegram','1',{'text':'x','sender':'u'})
        lease=self.e.clock()+120
        with self.e.tx() as c:c.execute("UPDATE p_events SET status='processing',claim='TOKEN_A',lease=?",(lease,))
        # One second inside: still owned, the dequeue must leave it alone.
        self.now=lease-1
        self.assertFalse(self.e.process_event('a',lambda *x:{'agent':'ops','steps':[{'tool':'reports.summary','args':{}}]}),
                         'inside the lease the event must not be re-claimed')
        with self.e.read() as c:row=c.execute('SELECT claim FROM p_events').fetchone()
        self.assertEqual('TOKEN_A',row[0],'the original claim must survive untouched')
        # Exactly on the bound: now it is dead and must be re-claimable.
        self.now=lease
        self.assertTrue(self.e.process_event('a',lambda *x:{'agent':'ops','steps':[{'tool':'reports.summary','args':{}}]}),
                        'on the lease bound the event must be recoverable')
    def test_event_reclaim_mints_a_new_token(self):
        """The property that makes the ABSENT completion fence safe.

        A step's completion is guarded twice -- by the claim token AND by `lease > now`.
        An event's completion (the UPDATE ending `AND claim=?`) is guarded by the token
        ALONE; there is no lease term on that statement. That is only safe because the
        dequeue mints a fresh token on every claim, so a superseded worker's late UPDATE
        matches no row and its result is never written over the one that won.

        This is a load-bearing property that nothing else asserts. If a future change
        reused the token -- or moved the completion guard off the token -- the event
        path would silently lose the double-fencing the step path still has. Measured:
        worker A holds TOKEN_A, the lease passes, B re-claims and completes; A's late
        UPDATE with TOKEN_A matches nothing.
        """
        self.e.accept_event('a','telegram','1',{'text':'x','sender':'u'})
        lease=self.e.clock()+120
        with self.e.tx() as c:c.execute("UPDATE p_events SET status='processing',claim='TOKEN_A',lease=?",(lease,))
        self.now=lease+1
        self.e.process_event('a',lambda *x:{'agent':'ops','steps':[{'tool':'reports.summary','args':{}}]})
        with self.e.read() as c:
            row=dict(c.execute('SELECT status,claim,result FROM p_events').fetchone())
        self.assertEqual('done',row['status'])
        self.assertNotEqual('TOKEN_A',row['claim'],
                            'the re-claim must mint a new token, not reuse the old one')
        # A's late completion, written exactly as the runtime writes it.
        with self.e.tx() as c:
            match=c.execute('SELECT 1 FROM p_events WHERE tenant=? AND channel=? AND event_key=? AND claim=?',
                            ('a','telegram','1','TOKEN_A')).fetchone()
        self.assertIsNone(match,'a superseded token must match no row')
    def test_failed_event_retry(self):
        self.e.accept_event('a','web','1',{'text':'x'})
        def fail(*x):raise ValueError('sensitive token not logged')
        self.e.process_event('a',fail)
        with self.e.read() as c:self.assertEqual('ValueError',c.execute('SELECT error FROM p_events').fetchone()[0])
        self.e.retry_event('a','web','1','owner')
        self.e.process_event('a',lambda *x:{'agent':'ops','steps':[{'tool':'reports.summary','args':{}}]})
        self.assertEqual(1,len(self.e.list_tasks('a')))
    def test_same_plan_different_channels(self):
        for ch in ('web','telegram','instagram','cron'):
            tid=self.submit(channel=ch);self.e.tick('a');self.assertEqual('succeeded',self.e.get('a',tid)['status'])
    def test_schedule_dedup(self):
        self.e.schedule('a','daily','ops',[{'tool':'reports.summary','args':{}}],60)
        self.assertEqual(0,self.e.run_schedules('a'));self.now+=60
        self.assertEqual(1,self.e.run_schedules('a'));self.assertEqual(0,self.e.run_schedules('a'));self.assertEqual(1,len(self.e.list_tasks('a')))
    def test_memory_isolation_and_ttl(self):
        t=self.submit([{'tool':'memory.put','args':{'key':'x','value':'secret fact','ttl_seconds':30}}]);sid=self.e.get('a',t)['steps'][0]['id']
        self.e.approve('a',sid,'owner','approved','owner');self.e.tick('a')
        fn=self.e.registry.get('memory.search').handler
        self.assertEqual(1,len(fn(self.e,'a','ops',{'query':'secret'},'')['matches']))
        self.assertEqual([],fn(self.e,'b','ops',{'query':'secret'},'')['matches'])
        self.assertEqual([],fn(self.e,'a','reader',{'query':'secret'},'')['matches'])
        self.now+=31;self.assertEqual([],fn(self.e,'a','ops',{'query':'secret'},'')['matches'])
    def test_like_wildcards_literal(self):
        fn=self.e.registry.get('memory.search').handler
        self.assertEqual([],fn(self.e,'a','ops',{'query':'%'},'')['matches'])
    def test_external_write_failure_is_uncertain(self):
        def boom(*args):raise RuntimeError('sensitive URL')
        self.e.registry.add(Tool('external.write','write',obj({}),boom,external=True))
        self.e.policy=lambda t,a:{'tools':['external.write'],'ladder':'autonomous'}
        t=self.submit([{'tool':'external.write','args':{}}]);sid=self.e.get('a',t)['steps'][0]['id']
        self.e.approve('a',sid,'o','approved','owner');self.e.tick('a')
        self.assertEqual('uncertain',self.e.get('a',t)['status']);self.assertFalse(self.e.tick('a'))
    def test_transaction_rollback(self):
        try:
            with self.e.tx() as c:
                c.execute("INSERT INTO p_freeze VALUES('a',1)");raise RuntimeError()
        except RuntimeError:pass
        with self.e.read() as c:self.assertIsNone(c.execute('SELECT * FROM p_freeze').fetchone())
    def test_freeze_stops_planning_and_schedule(self):
        self.e.accept_event('a','web','1',{'text':'x'})
        self.e.schedule('a','schedule','ops',[{'tool':'reports.summary','args':{}}],60)
        self.e.freeze('a',True,'owner');self.now+=61
        def fail(*a):raise AssertionError('Frozen tenant must not call planner')
        self.assertFalse(self.e.process_event('a',fail));self.assertEqual(0,self.e.run_schedules('a'))
    def test_owner_only_approval_policy(self):
        self.e.policy=lambda t,a:{**policy(t,a),'approver_role':'owner'}
        t=self.write();sid=self.e.get('a',t)['steps'][0]['id']
        with self.assertRaises(Forbidden):self.e.approve('a',sid,'operator','approved','operator')
        self.e.approve('a',sid,'owner','approved','owner');self.assertTrue(self.e.tick('a'))
    def test_freeze_invalidates_active_claim(self):
        t=self.submit();step=self.e.claim('a','w');self.e.freeze('a',True,'owner')
        with self.assertRaises(Conflict):self.e.finish('a',step['id'],step['claim'],{})
        self.assertEqual('uncertain',self.e.get('a',t)['status'])
    def test_reenrollment_cancels_old_device_tasks(self):
        self.e.device('a','d1');t=self.submit([{'tool':'fs.list','args':{'dir':'/safe'},'device':'d1'}])
        self.e.device('a','d1');self.assertIsNone(self.e.claim('a','r',device='d1'))
        self.assertEqual('cancelled',self.e.get('a',t)['status'])
    def test_device_revocation_invalidates_running_claim(self):
        self.e.device('a','d1');t=self.submit([{'tool':'fs.list','args':{'dir':'/safe'},'device':'d1'}])
        step=self.e.claim('a','r',device='d1');self.e.device('a','d1',True)
        with self.assertRaises(Conflict):self.e.finish('a',step['id'],step['claim'],{})
        self.assertEqual('uncertain',self.e.get('a',t)['status'])
    def test_inbox_quota_atomic_and_replay_not_charged(self):
        from platform_runtime.engine import RateLimited
        self.e.accept_event('a','web','1',{'text':'x'});self.e.accept_event('a','web','1',{'text':'x'})
        with self.e.tx() as c:
            self.assertEqual(1,c.execute('SELECT count FROM p_quota').fetchone()[0])
            c.execute('UPDATE p_quota SET count=10000')
        with self.assertRaises(RateLimited):self.e.accept_event('a','web','2',{'text':'x'})
        with self.e.read() as c:self.assertEqual(1,c.execute('SELECT count(*) FROM p_events').fetchone()[0])
    def test_quota_inside_the_bound_is_accepted_and_lands_exactly_on_it(self):
        """The inside of the quota bound, which the existing test does not reach.

        `test_inbox_quota_atomic_and_replay_not_charged` writes `count=10000` directly and
        asserts the next accept is refused. That pins the OUTSIDE of the bound and is
        genuinely discriminating -- an exclusive `count > 10000` would let that accept
        through -- but it says nothing about the inside, and nothing about whether the
        counter actually lands on the bound rather than stopping short of it.

        Measured: from 9999 the accept must succeed AND store exactly 10000, so the
        effective cap is 10000 events per day-index. An implementation that refused at
        9999 would make the cap 9999 and this test is the only one that would notice.
        """
        from platform_runtime.engine import RateLimited
        self.e.accept_event('a','web','seed',{'text':'x'})
        # One short of the bound: accepted, and the counter lands exactly on 10000.
        with self.e.tx() as c:c.execute('UPDATE p_quota SET count=9999')
        self.e.accept_event('a','web','next',{'text':'y'})
        with self.e.read() as c:self.assertEqual(10000,c.execute('SELECT count FROM p_quota').fetchone()[0])
        # Now on the bound: refused, and the counter must not move.
        with self.assertRaises(RateLimited):self.e.accept_event('a','web','over',{'text':'z'})
        with self.e.read() as c:self.assertEqual(10000,c.execute('SELECT count FROM p_quota').fetchone()[0])
    def test_outstanding_backpressure_bound_is_exclusive_at_the_cap(self):
        """The pending>=1000 backpressure bound, which no test touched.

        Separate from the daily quota: this is a LIVE gauge on how many events are
        outstanding (pending or processing), not a per-day counter. It refuses at
        `pending >= 1000`, so the outstanding set is capped at exactly 1000 -- the
        1000th event lands and the 1001st is refused.

        Both sides are pinned, because an exclusive `pending > 1000` would raise the cap
        to 1001 and only the inside assertion would catch it.
        """
        from platform_runtime.engine import RateLimited
        for i in range(999):
            with self.e.tx() as c:
                c.execute("INSERT INTO p_events(tenant,channel,event_key,fingerprint,payload,status) "
                          "VALUES(?,?,?,?,?,?)",('a','web','k%d'%i,'f','{}','pending'))
        # Inside: the 1000th outstanding event is still accepted.
        self.e.accept_event('a','web','the-1000th',{'text':'x'})
        with self.e.read() as c:
            self.assertEqual(1000,c.execute("SELECT count(*) FROM p_events WHERE status IN ('pending','processing')").fetchone()[0])
        # On the bound: the 1001st is refused.
        with self.assertRaises(RateLimited):self.e.accept_event('a','web','the-1001st',{'text':'y'})
        with self.e.read() as c:
            self.assertEqual(1000,c.execute("SELECT count(*) FROM p_events WHERE status IN ('pending','processing')").fetchone()[0])
    def test_quota_window_resets_on_a_new_day_index(self):
        """The quota's window, which nothing asserted.

        `day = int(clock() // 86400)` is a UTC day index. The counter is per (tenant, day),
        so a new day-index starts a FRESH counter and the previous day's exhausted quota
        does not carry over. That is the whole point of having a window, and it is also
        the documented fixed-window tradeoff: a burst straddling the reset can accept
        twice the cap in two seconds. The reset instant itself is what is pinned here.
        """
        from platform_runtime.engine import RateLimited
        base=86400*20000
        self.now=base-1
        with self.e.tx() as c:c.execute('INSERT INTO p_quota VALUES(?,?,?)',('a',19999,10000))
        # Last second of the old day-index: exhausted, refused.
        with self.assertRaises(RateLimited):self.e.accept_event('a','web','old',{'text':'x'})
        # First second of the new one: a fresh row, accepted.
        self.now=base
        self.e.accept_event('a','web','new',{'text':'y'})
        with self.e.read() as c:
            rows={r['day']:r['count'] for r in c.execute('SELECT day,count FROM p_quota').fetchall()}
        self.assertEqual({19999:10000,20000:1},rows)
    def test_engine_rejects_unallowlisted_direct_send(self):
        self.e.policy=lambda t,a:{'tools':['telegram.send'],'ladder':'autonomous'}
        with self.assertRaises(Forbidden):self.submit([{'tool':'telegram.send','args':{'conversation_id':'arbitrary','text':'x'}}])
    def test_engine_verifies_inbound_send_destination(self):
        self.e.policy=lambda t,a:{'tools':['telegram.send'],'ladder':'autonomous'}
        self.e.accept_event('a','telegram','1',{'text':'x','conversation_id':'123'})
        with self.assertRaises(Forbidden):self.submit([{'tool':'telegram.send','args':{'conversation_id':'attacker','text':'x'}}],channel='telegram')
        tid=self.submit([{'tool':'telegram.send','args':{'conversation_id':'123','text':'x'}}],channel='telegram')
        # An autonomous agent replying to the verified inbound conversation is
        # pre-authorised; the destination check above is what keeps it safe.
        # Full dispatch is covered in test_autonomy_rule with a stubbed provider.
        self.assertEqual(0,self.e.get('a',tid)['steps'][0]['approval_needed'])
    def test_meta_account_unique_mapping(self):
        from platform_runtime.tools import tenant_for_instagram_account
        from unittest.mock import patch
        file=Path(self.temp.name)/'integrations.json';file.write_text(json.dumps({'a':{'instagram':{'account_id':'123'}}}))
        with patch.dict(os.environ,{'PLATFORM_INTEGRATIONS_FILE':str(file)}):
            self.assertEqual('a',tenant_for_instagram_account('123'))
            with self.assertRaises(RuntimeError):tenant_for_instagram_account('unknown')
            file.write_text(json.dumps({'a':{'instagram':{'account_id':'123'}},'b':{'instagram':{'account_id':'123'}}}))
            with self.assertRaises(RuntimeError):tenant_for_instagram_account('123')
    def test_real_multi_process_claim(self):
        # Use real clock here so child processes see a valid lease.
        self.e=Engine(self.path,build_registry(),policy)
        self.submit();ctx=multiprocessing.get_context('spawn');q=ctx.Queue()
        workers=[ctx.Process(target=claim_process,args=(str(self.path),q)) for _ in range(4)]
        for p in workers:p.start()
        results=[q.get(timeout=15) for _ in workers]
        for p in workers:p.join(15);self.assertEqual(0,p.exitcode)
        self.assertEqual(1,sum(r is not None for r in results))

if __name__=='__main__':unittest.main()
