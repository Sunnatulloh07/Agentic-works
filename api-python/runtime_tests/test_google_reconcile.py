import base64
import copy
import json
import unittest
import test_google_adapters as fixtures
from platform_runtime.engine import Conflict,Forbidden
from platform_runtime.google_adapters import authorize_args,GoogleHTTPError
from platform_runtime.google_reconcile import GoogleReconciler


class GoogleReconcileTests(unittest.TestCase):
    setUp=fixtures.GoogleAdapterTests.setUp
    submit=fixtures.GoogleAdapterTests.submit
    step=fixtures.GoogleAdapterTests.step
    finish=fixtures.GoogleAdapterTests.finish
    def pending(self,calendar=False):
        self.failure=TimeoutError()
        args=({'calendar':'primary','summary':'Salom','start':'2026-09-20T10:00:00+05:00','end':'2026-09-20T11:00:00+05:00'} if calendar
              else {'to':'customer@example.invalid','subject':'O‘zbekcha','text':'Salom\nHisobot\n'})
        name='google.calendar.create' if calendar else 'google.gmail.send'
        tid=self.submit(name,args);step=self.finish(tid,True);self.assertEqual('uncertain',step['status'])
        self.tid=tid;self.sid=step['id'];self.args={'connection':'google',**args}
        _,_,body,self.reference=authorize_args(name,self.args,self.resources,self.sid)
        if calendar:self.responses=[{**body,'status':'confirmed'}]
        else:self.responses=[{'messages':[{'id':'sent'}]},{'id':'sent','labelIds':['SENT'],'raw':body['raw']}]
        self.lookups=[];self.lookup_hook=None
        def transport(method,url,token,body):
            self.lookups.append((method,url,body))
            if self.lookup_hook:self.lookup_hook()
            response=self.responses.pop(0)
            if isinstance(response,Exception):raise response
            return copy.deepcopy(response)
        self.r=GoogleReconciler(self.e,self.m,self.resources,transport)
    def reconcile(self,**kw):return self.r.reconcile('a',self.sid,'owner',**kw)
    def status(self):return self.step(self.tid)['status']
    def test_calendar_positive_readback_settles_step_and_journal(self):
        self.pending(True);r=self.reconcile();self.assertEqual('succeeded',r['status']);self.assertEqual('succeeded',self.status())
        self.assertEqual('GET',self.lookups[0][0]);self.assertEqual(1,len(self.calls))
        with self.e.read() as db:self.assertEqual('succeeded',db.execute('SELECT status FROM p_google_dispatch').fetchone()[0])
    def test_gmail_unique_sent_mime_match(self):
        self.pending();r=self.reconcile();self.assertEqual('succeeded',r['status']);self.assertEqual(2,len(self.lookups))
        self.assertTrue(all(x[0]=='GET' and x[2] is None for x in self.lookups));self.assertNotIn('fake-access',json.dumps(r))
    def test_calendar_equivalent_timezone_is_accepted(self):
        self.pending(True);self.responses[0]['start']={'dateTime':'2026-09-20T05:00:00Z'};self.responses[0]['end']={'dateTime':'2026-09-20T06:00:00Z'}
        self.assertEqual('succeeded',self.reconcile()['status'])
    def test_calendar_wrong_id_unresolved(self):
        self.pending(True);self.responses[0]['id']='different';self.assertEqual('uncertain',self.reconcile()['status'])
    def test_calendar_wrong_correlation_unresolved(self):
        self.pending(True);self.responses[0]['extendedProperties']['private']['platform_step']='wrong'
        self.assertEqual('uncertain',self.reconcile()['status'])
    def test_calendar_mutated_content_unresolved(self):
        self.pending(True);self.responses[0]['summary']='Not the approved action';self.assertEqual('uncertain',self.reconcile()['status'])
    def test_calendar_cancelled_does_not_prove_no_delivery(self):
        self.pending(True);self.responses[0]['status']='cancelled';self.assertEqual('uncertain',self.reconcile()['status'])
    def test_not_found_never_marks_failed_or_retries(self):
        self.pending(True);self.responses=[GoogleHTTPError(404)];self.assertEqual('uncertain',self.reconcile()['status']);self.assertEqual(1,len(self.calls))
    def test_gmail_no_matches_is_not_failure(self):
        self.pending();self.responses=[{}];self.assertEqual('uncertain',self.reconcile()['status'])
    def test_gmail_multiple_matches_are_ambiguous(self):
        self.pending();self.responses=[{'messages':[{'id':'x'},{'id':'y'}]}];self.assertEqual('uncertain',self.reconcile()['status'])
    def test_gmail_page_token_not_unique(self):
        self.pending();self.responses[0]['nextPageToken']='more';self.assertEqual('uncertain',self.reconcile()['status'])
    def test_gmail_non_sent_label_rejected(self):
        self.pending();self.responses[1]['labelIds']=['INBOX'];self.assertEqual('uncertain',self.reconcile()['status'])
    def test_gmail_wrong_body_rejected(self):
        self.pending();raw=base64.urlsafe_b64decode(self.responses[1]['raw']);self.responses[1]['raw']=base64.urlsafe_b64encode(raw.replace(b'Hisobot',b'Other')).decode()
        self.assertEqual('uncertain',self.reconcile()['status'])
    def test_gmail_header_injection_and_multiple_recipients_rejected(self):
        self.pending();raw=base64.urlsafe_b64decode(self.responses[1]['raw']);self.responses[1]['raw']=base64.urlsafe_b64encode(b'Bcc: extra@example.invalid\r\n'+raw).decode()
        self.assertEqual('uncertain',self.reconcile()['status'])
    def test_gmail_duplicate_message_id_rejected(self):
        self.pending();raw=base64.urlsafe_b64decode(self.responses[1]['raw']);line=next(x for x in raw.split(b'\r\n') if x.startswith(b'Message-ID:'))
        self.responses[1]['raw']=base64.urlsafe_b64encode(line+b'\r\n'+raw).decode();self.assertEqual('uncertain',self.reconcile()['status'])
    def test_gmail_wrong_provider_id_rejected(self):
        self.pending();self.responses[1]['id']='other';self.assertEqual('uncertain',self.reconcile()['status'])
    def test_repeated_settlement_denied_before_readback(self):
        self.pending(True);self.reconcile();n=len(self.lookups)
        with self.assertRaises(Conflict):self.reconcile()
        self.assertEqual(n,len(self.lookups))
    def test_original_resource_policy_required(self):
        self.pending(True);self.resources['calendar_ids'].append('other')
        with self.assertRaises(Forbidden):self.reconcile()
        self.assertEqual([],self.lookups)
    def test_dispatch_fingerprint_tamper_rejected(self):
        self.pending(True)
        with self.e.tx() as db:db.execute("UPDATE p_google_dispatch SET fingerprint='changed'")
        with self.assertRaises(Forbidden):self.reconcile()
        self.assertEqual([],self.lookups)
    def test_revoke_during_lookup_does_not_settle(self):
        self.pending(True);self.lookup_hook=lambda:self.m.revoke('a','google','owner',False)
        with self.assertRaises(Forbidden):self.reconcile()
        self.assertEqual('uncertain',self.status())
    def test_session_revoked_before_transaction_does_not_settle(self):
        self.pending(True)
        def guard(db):
            if db.in_transaction:raise Forbidden('Session revoked')
        with self.assertRaises(Forbidden):self.reconcile(session_guard=guard)
        self.assertEqual('uncertain',self.status())
    def test_concurrent_manual_settlement_is_not_overwritten(self):
        self.pending(True);self.lookup_hook=lambda:self.e.reconcile('a',self.sid,'owner','owner','failed','Operator evidence')
        with self.assertRaises(Conflict):self.reconcile()
        self.assertEqual('failed',self.status())
    def test_transport_failure_preserves_uncertain(self):
        self.pending(True);self.responses=[TimeoutError()]
        with self.assertRaises(TimeoutError):self.reconcile()
        self.assertEqual('uncertain',self.status())
    def test_missing_read_scope_does_not_send(self):
        self.pending()
        with self.e.tx() as db:
            scopes=json.loads(db.execute('SELECT scopes FROM p_oauth_connections').fetchone()[0]);scopes.remove('https://www.googleapis.com/auth/gmail.readonly')
            db.execute('UPDATE p_oauth_connections SET scopes=?',(json.dumps(scopes),))
        with self.assertRaises(Forbidden):self.reconcile()
        self.assertEqual([],self.lookups)
    def test_calendar_malformed_properties_stays_uncertain(self):
        self.pending(True);self.responses[0]['extendedProperties']='invalid'
        self.assertEqual('uncertain',self.reconcile()['status'])
    def test_calendar_added_attendees_not_approved(self):
        self.pending(True);self.responses[0]['attendees']=[{'email':'other@example.invalid'}]
        self.assertEqual('uncertain',self.reconcile()['status'])
    def test_gmail_labels_must_be_array(self):
        self.pending();self.responses[1]['labelIds']='SENT'
        self.assertEqual('uncertain',self.reconcile()['status'])
    def test_authority_removed_before_commit(self):
        self.pending(True);self.block=False
        def authority(db,tenant,channel,actor,roles):
            if channel and self.block:raise Forbidden('Membership revoked')
        self.e.authority=authority;self.lookup_hook=lambda:setattr(self,'block',True)
        with self.assertRaises(Forbidden):self.reconcile()
        self.assertEqual('uncertain',self.status())
