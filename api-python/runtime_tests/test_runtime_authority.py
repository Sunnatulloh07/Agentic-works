import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import identity_store as s
from app.storage import tx,reset,db
from app.runtime_authority import runtime_authority
from platform_runtime.engine import Engine,Forbidden
from platform_runtime.tools import build_registry


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db'
        self.env=patch.dict(os.environ,{'APP_DB':str(self.path),'ENV':'production'});self.env.start();reset()
        self.owner=s.register_user('owner@example.com','long password 123','Owner');s.create_workspace(self.owner['id'],'work','Work')
        _,token=s.create_invitation(self.owner['id'],'work','member@example.com','operator')
        self.member=s.register_with_invitation('member@example.com','long password 123','Member',token)
        self.e=Engine(self.path,build_registry(),lambda t,a:{'tools':['reports.summary','records.create'],'ladder':'autonomous'},authority=runtime_authority)
        self.steps=[{'tool':'reports.summary','args':{}}]
    def tearDown(self):reset();self.env.stop();self.tmp.cleanup()
    def revoke(self):s.revoke_membership(self.owner['id'],'work',self.member['id'])
    def submit(self,steps=None):return self.e.submit('work','web','1','ops',steps or self.steps,self.member['id'])
    def test_queued_task_creator_revoked_before_claim(self):
        tid=self.submit();self.revoke();self.assertIsNone(self.e.claim('work','w'))
        self.assertEqual('failed',self.e.get('work',tid)['status'])
    def test_dispatch_fence_checks_membership_again(self):
        self.submit();step=self.e.claim('work','w');self.revoke()
        with self.assertRaises(Forbidden):self.e.dispatch_allowed('work',step)
    def test_suspended_workspace_no_claim(self):
        self.submit()
        with tx() as c:c.execute("UPDATE p_workspaces SET status='suspended'")
        self.assertIsNone(self.e.claim('work','w'))
    def test_disabled_user_cannot_enqueue_event(self):
        with tx() as c:c.execute("UPDATE p_users SET status='disabled' WHERE id=?",(self.member['id'],))
        with self.assertRaises(Forbidden):self.e.accept_event('work','web','evt',{'sender':self.member['id'],'text':'hello'})
    def test_revoked_event_sender_never_calls_planner(self):
        self.e.accept_event('work','web','evt',{'sender':self.member['id'],'text':'hello'});self.revoke()
        planner=[]
        self.e.process_event('work',lambda *args:planner.append(1))
        self.assertEqual([],planner)
    def test_revoked_approver_cannot_authorize_remaining_write(self):
        tid=self.e.submit('work','web','w','ops',[{'tool':'records.create','args':{'kind':'lead','title':'x','body':'x'}}],self.owner['id'])
        sid=self.e.get('work',tid)['steps'][0]['id'];self.e.approve('work',sid,self.member['id'],'approved','operator')
        self.revoke();self.assertIsNone(self.e.claim('work','worker'))
        self.assertEqual('approver_revoked',self.e.get('work',tid)['steps'][0]['error'])
    def test_unowned_schedule_cannot_run(self):
        with self.assertRaises(Forbidden):self.e.schedule('work','s','ops',self.steps,60)
    def test_owned_schedule_runs_only_for_active_owner(self):
        self.e.schedule('work','s','ops',self.steps,60,actor=self.owner['id'])
        with self.e.tx() as c:
            c.execute('UPDATE p_schedules SET next_due=1');c.execute("UPDATE p_workspaces SET status='suspended'")
        self.assertEqual(0,self.e.run_schedules('work'))
        self.assertEqual([],self.e.list_tasks('work'))
    def test_unknown_workspace_webhook_denied(self):
        with self.assertRaises(Forbidden):self.e.accept_event('unknown','telegram','1',{'sender':'external'})
