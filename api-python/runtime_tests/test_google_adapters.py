"""Real Engine/SQLite approval paths + fake provider transport; no live Google."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry
from platform_runtime.google_adapters import GoogleAdapter, SCOPES, api_transport
from platform_runtime.oauth import OAuthManager, OAuthError
from platform_runtime.secret_vault import SecretVault
from test_oauth import FakeProvider
import os
from urllib.parse import parse_qs,urlsplit


class GoogleAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.policy={'tools':list(SCOPES),'ladder':'autonomous','allowed_connections':['google'], 'independent_approval':True}
        self.e=Engine(Path(self.tmp.name)/'db',build_registry(),lambda t,a:self.policy)
        self.p=FakeProvider();self.p.scopes=set(SCOPES.values())|self.p.scopes
        self.m=OAuthManager(self.e,SecretVault({'v1':os.urandom(32)},'v1'),self.p)
        state=parse_qs(urlsplit(self.m.begin('a','google','owner','session')['authorization_url']).query)['state'][0]
        self.m.complete('a','google','owner','session',state,'code')
        self.resources={'recipient_emails':['customer@example.invalid'],'calendar_ids':['primary']}
        self.calls=[];self.response={'id':'provider-record'};self.failure=None;self.hook=None
        def transport(method,url,token,body=None):
            self.calls.append((method,url,token,body))
            if self.hook:self.hook()
            if self.failure:raise self.failure
            if body and 'id' in body:return {'id':body['id']}
            return copy.deepcopy(self.response)
        self.adapter=GoogleAdapter(self.e,self.m,self.resources,transport)
        for name in SCOPES:
            old=self.e.registry.get(name)
            from platform_runtime.tools import Tool
            self.e.registry.items[name]=Tool(old.name,old.risk,old.schema,
                lambda e,t,a,args,key,n=name:self.adapter.execute(t,args['connection'],a,n,args,key),external=True)
        for target,value in [('platform_runtime.google_adapters.configured_manager',self.m),
                             ('platform_runtime.google_adapters.configured_resources',self.resources)]:
            patcher=patch(target,return_value=value);patcher.start();self.addCleanup(patcher.stop)
    def submit(self,name,args,key='task'):
        return self.e.submit('a','web',key,'ops',[{'tool':name,'args':{'connection':'google',**args}}],'owner')
    def step(self,task):return self.e.get('a',task)['steps'][0]
    def finish(self,task,approve=False):
        if approve:self.e.approve('a',self.step(task)['id'],'approver','approved','owner')
        self.e.tick('a');return self.step(task)
    def test_registered_seven_typed_tools(self):self.assertEqual(7,len([n for n in self.e.registry.items if n.startswith('google.')]))
    def test_list_read_real_engine_path(self):
        self.response={'messages':[{'id':'m'}]}
        s=self.finish(self.submit('google.gmail.list',{'limit':3,'query':'invoice'}))
        self.assertEqual('succeeded',s['status']);self.assertTrue(s['result']['untrusted_content'])
        self.assertIn('maxResults=3',self.calls[0][1])
    def test_read_id_url_encoded_not_url_destination(self):
        s=self.finish(self.submit('google.gmail.read',{'id':'https://evil.invalid/?x=y'}))
        self.assertEqual('succeeded',s['status']);self.assertIn('https%3A%2F%2F',self.calls[0][1])
        self.assertEqual('gmail.googleapis.com',urlsplit(self.calls[0][1]).hostname)
    def test_drive_lists_only_metadata_fields(self):
        self.finish(self.submit('google.drive.list',{}))
        self.assertIn('files%28id%2Cname',self.calls[0][1]);self.assertIn('trashed',self.calls[0][1])
    def test_calendar_allowlist(self):
        with self.assertRaises(Forbidden):self.submit('google.calendar.list',{'calendar':'other'})
        self.assertEqual([],self.calls)
    def test_recipient_allowlist(self):
        with self.assertRaises(Forbidden):self.submit('google.gmail.send',{'to':'attacker@example.invalid','subject':'Hi','text':'private'})
    def test_email_header_injection_denied(self):
        for args in [{'to':'customer@example.invalid\nBcc:evil@example.invalid','subject':'a','text':'b'},
                     {'to':'customer@example.invalid','subject':'a\r\nBcc: evil','text':'b'}]:
            with self.assertRaises((Forbidden,ValueError)):self.submit('google.gmail.send',args)
    def test_unknown_fields_fail_before_provider(self):
        with self.assertRaises(ValueError):self.submit('google.gmail.list',{'url':'https://attacker.invalid'})
    def test_write_waits_for_approval(self):
        tid=self.submit('google.gmail.send',{'to':'customer@example.invalid','subject':'Salom','text':'Hisobot'})
        s=self.finish(tid);self.assertEqual('waiting_approval',s['status']);self.assertEqual([],self.calls)
        s=self.finish(tid,True);self.assertEqual('succeeded',s['status']);self.assertEqual(1,len(self.calls))
    def test_gmail_receipt_excludes_token(self):
        tid=self.submit('google.gmail.send',{'to':'customer@example.invalid','subject':'Salom','text':'Hisobot'})
        s=self.finish(tid,True);self.assertEqual('provider-record',s['result']['external_id'])
        self.assertNotIn('fake-access',json.dumps(s['result']))
    def test_timeout_write_is_uncertain_and_never_retried(self):
        self.failure=TimeoutError('synthetic-secret')
        tid=self.submit('google.gmail.send',{'to':'customer@example.invalid','subject':'a','text':'b'})
        s=self.finish(tid,True);self.assertEqual('uncertain',s['status']);self.e.tick('a');self.assertEqual(1,len(self.calls))
        with self.e.read() as db:row=db.execute('SELECT status FROM p_google_dispatch').fetchone()
        self.assertEqual('uncertain',row['status']);self.assertNotIn('synthetic-secret',json.dumps(s))
    def test_missing_write_receipt_is_uncertain(self):
        self.response={};tid=self.submit('google.gmail.send',{'to':'customer@example.invalid','subject':'a','text':'b'})
        self.assertEqual('uncertain',self.finish(tid,True)['status'])
    def test_calendar_event_has_stable_id_and_no_guest_notifications(self):
        args={'calendar':'primary','summary':'Meeting','start':'2026-09-20T10:00:00+05:00','end':'2026-09-20T11:00:00+05:00'}
        tid=self.submit('google.calendar.create',args)
        self.assertEqual('succeeded',self.finish(tid,True)['status'])
        self.assertIn('sendUpdates=none',self.calls[0][1]);self.assertTrue(self.calls[0][3]['id'].startswith('ap'))
    def test_naive_calendar_times_denied(self):
        args={'calendar':'primary','summary':'Meeting','start':'2026-09-20T10:00:00','end':'2026-09-20T11:00:00'}
        with self.assertRaises(ValueError):self.submit('google.calendar.create',args)
    def test_calendar_end_before_start_denied(self):
        args={'calendar':'primary','summary':'Meeting','start':'2026-09-20T11:00:00Z','end':'2026-09-20T10:00:00Z'}
        with self.assertRaises(ValueError):self.submit('google.calendar.create',args)
    def test_no_handler_use_without_active_step(self):
        with self.assertRaises(Forbidden):self.adapter.execute('a','google','ops','google.gmail.list',{'connection':'google'},'fake-step')
        self.assertEqual([],self.calls)
    def test_connection_revoked_before_dispatch(self):
        tid=self.submit('google.gmail.list',{});self.m.revoke('a','google','owner',False);self.finish(tid)
        self.assertEqual([],self.calls)
    def test_read_revoked_while_provider_responds_is_not_published(self):
        tid=self.submit('google.gmail.list',{})
        self.hook=lambda:self.m.revoke('a','google','owner',False)
        self.assertEqual('failed',self.finish(tid)['status'])
    def test_changed_resource_policy_invalidates_approval(self):
        tid=self.submit('google.gmail.send',{'to':'customer@example.invalid','subject':'a','text':'b'})
        self.e.approve('a',self.step(tid)['id'],'approver','approved','owner')
        self.resources['recipient_emails'].append('new@example.invalid')
        self.finish(tid);self.assertEqual([],self.calls)
    def test_late_provider_response_cannot_overwrite_uncertain_dispatch(self):
        tid=self.submit('google.gmail.send',{'to':'customer@example.invalid','subject':'a','text':'b'})
        def recover():
            with self.e.tx() as db:db.execute("UPDATE p_google_dispatch SET status='uncertain'")
        self.hook=recover
        self.assertEqual('uncertain',self.finish(tid,True)['status'])
        with self.e.read() as db:self.assertEqual('uncertain',db.execute('SELECT status FROM p_google_dispatch').fetchone()[0])

    def test_disallowed_http_host_rejected_without_network(self):
        for url in ['http://www.googleapis.com/drive/v3/files','https://evil.invalid/drive/v3/files',
                    'https://u:p@www.googleapis.com/drive/v3/files','https://www.googleapis.com/not-allowed']:
            with self.assertRaises(OAuthError):api_transport('GET',url,'fake')
