import concurrent.futures
import tempfile
from pathlib import Path
import unittest
from platform_runtime.engine import Engine,Conflict,Forbidden
from platform_runtime.tools import build_registry
from platform_runtime.sync_store import SyncStore


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=[1000.0]
        self.e=Engine(Path(self.tmp.name)/'db',build_registry(),lambda t,a:{'tools':[],'ladder':'autonomous'},clock=lambda:self.now[0])
        with self.e.tx() as db:
            for tenant in ['a','b']:
                db.execute("INSERT INTO p_oauth_connections VALUES(?,?,?,?,?,1,'active','[]','',2000,'',0,'owner',1000)",(tenant,'google','google','subject','hash'))
        self.s=SyncStore(self.e);self.claim=self.s.claim('a','google','gmail','owner')['claim']
    def event(self,version=1,deleted=False,text='hello'):return {'id':'one','version':version,'deleted':deleted,'data':{} if deleted else {'text':text}}
    def commit(self,events=None,key='page',before='',after='next'):
        return self.s.commit('a','google','gmail','owner',self.claim,key,before,after,events if events is not None else [self.event()])
    def records(self):return self.s.records('a','google','gmail','owner')
    def test_commit_and_read(self):
        self.assertEqual(1,self.commit()['changed_records']);self.assertEqual('hello',self.records()[0]['data']['text'])
        self.assertTrue(self.records()[0]['untrusted_content'])
    def test_same_page_replay_is_idempotent(self):self.assertEqual(self.commit(),self.commit())
    def test_conflicting_page_replay_denied(self):
        self.commit()
        with self.assertRaises(Conflict):self.commit([self.event(text='changed')])
    def test_cursor_compare_and_swap(self):
        self.commit()
        with self.assertRaises(Conflict):self.commit(key='other',before='wrong')
    def test_stale_version_does_not_overwrite(self):
        self.commit([self.event(2,text='new')]);self.commit([self.event(1)],key='p2',before='next',after='n2')
        self.assertEqual('new',self.records()[0]['data']['text'])
    def test_conflicting_record_version_rolls_back_whole_page(self):
        self.commit()
        events=[{'id':'two','version':1,'deleted':False,'data':{'x':1}},self.event(text='different')]
        with self.assertRaises(Conflict):self.commit(events,key='p2',before='next',after='n2')
        self.assertEqual(1,len(self.records()))
        with self.e.read() as db:self.assertEqual('next',db.execute('SELECT cursor FROM p_sync_streams').fetchone()[0])
    def test_tombstone_purges_record_body(self):
        self.commit();self.commit([self.event(2,True)],key='delete',before='next',after='n2')
        row=self.records()[0];self.assertTrue(row['deleted']);self.assertEqual({},row['data'])
    def test_stale_event_cannot_resurrect_tombstone(self):
        self.commit([self.event(2,True)]);self.commit(key='p2',before='next',after='n2')
        self.assertTrue(self.records()[0]['deleted'])
    def test_newer_version_can_restore(self):
        self.commit([self.event(2,True)]);self.commit([self.event(3)],key='p2',before='next',after='n2')
        self.assertFalse(self.records()[0]['deleted'])
    def test_cross_tenant_claim_denied(self):
        with self.assertRaises((Conflict,LookupError)):self.s.commit('b','google','gmail','owner',self.claim,'p','','n',[self.event()])
    def test_parallel_claim_single_owner(self):
        self.s.release('a','google','gmail','owner',self.claim)
        def claim(_):
            try:self.s.claim('a','google','gmail','owner');return 1
            except Conflict:return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:self.assertEqual(1,sum(pool.map(claim,range(8))))
    def test_expired_lease_cannot_commit(self):
        self.now[0]+=61
        with self.assertRaises(Conflict):self.commit()
    def test_takeover_fences_old_claim(self):
        self.now[0]+=61;new=self.s.claim('a','google','gmail','owner')['claim']
        with self.assertRaises(Conflict):self.commit()
        self.claim=new;self.assertEqual(1,self.commit()['revision'])
    def test_revoke_blocks_commit_and_read(self):
        with self.e.tx() as db:db.execute("UPDATE p_oauth_connections SET status='revoked' WHERE tenant='a'")
        with self.assertRaises(Forbidden):self.commit()
        with self.assertRaises(Forbidden):self.records()
    def test_generation_change_requires_explicit_reset(self):
        self.commit()
        with self.e.tx() as db:db.execute("UPDATE p_oauth_connections SET generation=2 WHERE tenant='a'")
        with self.assertRaises(Conflict):self.records()
        self.s.reset('a','google','gmail','owner');self.assertEqual([],self.records())
    def test_freeze_blocks_all_sync(self):
        with self.e.tx() as db:db.execute("INSERT INTO p_freeze VALUES('a',1)")
        with self.assertRaises(Forbidden):self.commit()
    def test_renew_current_claim(self):
        self.now[0]+=50;self.s.renew('a','google','gmail','owner',self.claim);self.now[0]+=20;self.commit()
    def test_page_bounds(self):
        for events in [[self.event()]*101,[self.event(),self.event()],[self.event(version=True)],
                       [{**self.event(),'extra':1}],[self.event(text='x'*20001)],
                       [{'id':'x','version':1,'deleted':True,'data':{'secret':'must be cleared'}}]]:
            with self.assertRaises(ValueError):self.commit(events)
    def test_no_provider_cursor_order_assumed(self):
        self.commit(after='opaque:z');self.commit([],key='p2',before='opaque:z',after='opaque:a')
    def test_reset_fences_running_sync(self):
        self.s.reset('a','google','gmail','owner')
        with self.assertRaises(Conflict):self.commit()
