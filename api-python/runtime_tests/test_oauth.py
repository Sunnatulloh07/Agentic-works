"""Real SQLite and AES-GCM, deterministic fake OAuth provider, zero network."""
import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit
from platform_runtime.engine import Engine, Conflict, Forbidden
from platform_runtime.tools import build_registry
from platform_runtime.secret_vault import SecretVault
from platform_runtime.oauth import OAuthManager, OAuthError, InvalidGrant, RefreshInProgress, pkce_challenge, AccessGrant
from platform_runtime.google_oauth import GoogleOAuth, BASE_SCOPES

SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'


class FakeProvider:
    name = 'google'
    expected_account = 'expected-subject'
    scopes = {SCOPE, *BASE_SCOPES}
    def __init__(self):
        self.calls = []; self.before_exchange = None; self.before_refresh = None
        self.account = self.expected_account; self.scope_override = None; self.fail = None
    def public_config(self): return {'provider': self.name, 'account': self.expected_account, 'scopes': sorted(self.scopes)}
    def authorization_url(self, state, challenge): return 'https://accounts.google.com/test?state='+state+'&challenge='+challenge
    def response(self): return {'access_token': 'fake-access', 'refresh_token': 'fake-refresh', 'token_type': 'Bearer',
        'expires_in': 3600, 'scope': ' '.join(self.scope_override if self.scope_override is not None else self.scopes)}
    def exchange(self, code, verifier):
        self.calls.append(('exchange', code)); self.verifier = verifier
        if self.before_exchange: self.before_exchange()
        if self.fail: raise self.fail
        return self.response()
    def refresh(self, token):
        self.calls.append(('refresh', token))
        if self.before_refresh: self.before_refresh()
        if self.fail: raise self.fail
        return self.response()
    def identity(self, token): return self.account
    def revoke(self, token):
        self.calls.append(('revoke', token))
        if self.fail: raise self.fail


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = [1000.0]
        self.policy = {'tools': ['reports.summary'], 'ladder': 'autonomous', 'allowed_connections': ['mail']}
        def authority(db, tenant, channel, actor, roles):
            if channel and actor != 'owner': raise Forbidden('Owner required')
        self.e = Engine(Path(self.tmp.name)/'db', build_registry(), lambda t,a: self.policy,
                        clock=lambda: self.now[0], authority=authority)
        self.v = SecretVault({'a': os.urandom(32)}, 'a'); self.p = FakeProvider()
        self.m = OAuthManager(self.e, self.v, self.p)
    def begin(self, tenant='a', connection='mail', actor='owner', session='session'):
        result = self.m.begin(tenant, connection, actor, session)
        return parse_qs(urlsplit(result['authorization_url']).query)['state'][0]
    def complete(self, state=None, **kw):
        values = dict(tenant='a', connection='mail', actor='owner', session='session', state=state or self.begin(), code='fake-code')
        values.update(kw); return self.m.complete(**values)
    def access(self): return self.m.access('a','mail','ops',[SCOPE])
    def expire(self): self.now[0] += 3550

    def test_pkce_rfc_vector(self):
        self.assertEqual('E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM', pkce_challenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'))
    def test_begin_encrypted_state_and_no_plaintext(self):
        state = self.begin()
        with self.e.read() as db:
            row = dict(db.execute('SELECT * FROM p_oauth_states').fetchone())
        self.assertNotIn(state, json.dumps(row)); self.assertNotIn('verifier', row['envelope'])
    def test_success_public_status_never_contains_tokens(self):
        status = self.complete()
        self.assertEqual('active', status['status']); self.assertNotIn('fake-access', json.dumps(status))
        self.assertEqual('fake-access', self.access().token); self.assertNotIn('fake-access', repr(self.access()))
    def test_database_does_not_persist_plaintext_credentials(self):
        self.complete()
        with self.e.read() as db: text = '\n'.join(db.iterdump())
        for secret in ['fake-access','fake-refresh','fake-code',self.p.verifier]: self.assertNotIn(secret,text)
    def test_wrong_state_does_not_call_provider(self):
        self.begin()
        with self.assertRaises(Forbidden): self.complete('wrong')
        self.assertEqual([],self.p.calls)
    def test_replay_rejected_before_network(self):
        state=self.begin(); self.complete(state)
        with self.assertRaises(Forbidden): self.complete(state)
        self.assertEqual(1,len(self.p.calls))
    def test_expired_state_denied(self):
        state=self.begin(); self.now[0]+=601
        with self.assertRaises(Forbidden): self.complete(state)
        self.assertEqual([],self.p.calls)
    def test_wrong_session_denied_without_consuming_valid_state(self):
        state=self.begin()
        with self.assertRaises(Forbidden): self.complete(state,session='other')
        self.assertEqual('active',self.complete(state)['status'])
    def test_wrong_actor_denied(self):
        state=self.begin()
        with self.assertRaises(Forbidden): self.complete(state,actor='viewer')
    def test_cross_tenant_state_denied(self):
        state=self.begin(); self.begin('b')
        with self.assertRaises(Forbidden): self.complete(state,tenant='b')
        self.assertEqual([],self.p.calls)
    def test_cross_connection_state_denied(self):
        state=self.begin(); self.begin(connection='other')
        with self.assertRaises(Forbidden): self.complete(state,connection='other')
    def test_new_begin_fences_old_state(self):
        old=self.begin(); self.begin()
        with self.assertRaises(Forbidden): self.complete(old)
    def test_parallel_callback_single_exchange(self):
        state=self.begin()
        def call():
            try: self.complete(state); return 1
            except (Forbidden,Conflict): return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(1,sum(pool.map(lambda _:call(),range(4))))
        self.assertEqual(1,len(self.p.calls))
    def test_account_mismatch_fail_closed(self):
        self.p.account='attacker'
        with self.assertRaises(OAuthError): self.complete()
        self.assertEqual('uncertain',self.m.describe('a','mail')['status'])
    def test_scope_downgrade_denied(self):
        self.p.scope_override=BASE_SCOPES
        with self.assertRaises(OAuthError): self.complete()
    def test_scope_escalation_denied(self):
        self.p.scope_override=self.p.scopes|{'extra'}
        with self.assertRaises(OAuthError): self.complete()
    def test_invalid_grant_requires_reauth(self):
        self.p.fail=InvalidGrant('fake-secret-detail')
        with self.assertRaises(OAuthError) as error:self.complete()
        self.assertNotIn('fake-secret',str(error.exception))
        self.assertEqual('reauth_required',self.m.describe('a','mail')['status'])
    def test_transport_failure_not_retried(self):
        self.p.fail=TimeoutError('fake-secret-detail'); state=self.begin()
        with self.assertRaises(OAuthError): self.complete(state)
        with self.assertRaises(Forbidden): self.complete(state)
        self.assertEqual(1,len(self.p.calls))
    def test_unexpired_token_does_not_refresh(self):
        self.complete(); self.access(); self.access(); self.assertEqual(1,len(self.p.calls))
    def test_expiry_refresh(self):
        self.complete(); self.expire(); self.access(); self.assertEqual('refresh',self.p.calls[-1][0])
    def test_parallel_refresh_only_one_dispatch(self):
        self.complete(); self.expire(); entered=threading.Event(); release=threading.Event()
        def hook(): entered.set(); self.assertTrue(release.wait(5))
        self.p.before_refresh=hook
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.access); self.assertTrue(entered.wait(5))
            with self.assertRaises(RefreshInProgress): self.access()
            release.set(); self.assertEqual('fake-access',first.result().token)
        self.assertEqual(1,sum(c[0]=='refresh' for c in self.p.calls))
    def test_refresh_preserves_unrotated_refresh_token(self):
        self.complete(); self.expire()
        response=self.p.response(); del response['refresh_token']; del response['scope']
        self.p.response=lambda:response
        self.assertEqual('fake-access',self.access().token)
    def test_refresh_revoke_race_fenced(self):
        self.complete(); self.expire()
        self.p.before_refresh=lambda:self.m.revoke('a','mail','owner',False)
        with self.assertRaises(OAuthError): self.access()
        self.assertEqual('revoked',self.m.describe('a','mail')['status'])
    def test_exchange_revoke_race_fenced(self):
        self.p.before_exchange=lambda:self.m.revoke('a','mail','owner',False)
        with self.assertRaises(OAuthError): self.complete()
        self.assertEqual('revoked',self.m.describe('a','mail')['status'])
    def test_freeze_before_exchange_store_fails(self):
        def freeze():
            with self.e.tx() as db:db.execute("INSERT INTO p_freeze VALUES('a',1)")
        self.p.before_exchange=freeze
        with self.assertRaises(OAuthError):self.complete()
    def test_revocation_idempotent(self):
        self.complete(); self.m.revoke('a','mail','owner'); self.m.revoke('a','mail','owner')
        self.assertEqual(1,sum(c[0]=='revoke' for c in self.p.calls))
        with self.assertRaises(Forbidden):self.access()
    def test_remote_revoke_failure_does_not_restore_local_access(self):
        self.complete(); self.p.fail=TimeoutError()
        result=self.m.revoke('a','mail','owner')
        self.assertEqual('uncertain',result['remote_status'])
        with self.assertRaises(Forbidden):self.access()
    def test_revoke_allowed_while_frozen(self):
        self.complete()
        with self.e.tx() as db:db.execute("INSERT INTO p_freeze VALUES('a',1)")
        self.assertTrue(self.m.revoke('a','mail','owner',False)['local_revoked'])
    def test_agent_acl_deny(self):
        self.complete(); self.policy['allowed_connections']=[]
        with self.assertRaises(Forbidden):self.access()
    def test_operation_scope_deny(self):
        self.complete()
        with self.assertRaises(Forbidden):self.m.access('a','mail','ops',['unknown'])
    def test_config_change_invalidates_access(self):
        self.complete(); self.p.expected_account='other'
        other=OAuthManager(self.e,self.v,self.p)
        with self.assertRaises(Forbidden):other.access('a','mail','ops',[SCOPE])
    def test_stale_refresh_requires_explicit_recovery(self):
        self.complete()
        with self.e.tx() as db:db.execute("UPDATE p_oauth_connections SET status='refreshing',started=?",(self.now[0],))
        with self.assertRaises(Conflict):self.m.recover_stale('a','mail','owner')
        self.now[0]+=121; self.m.recover_stale('a','mail','owner')
        with self.assertRaises(Forbidden):self.access()
        self.assertEqual(1,len(self.p.calls))
    def test_key_rewrap_preserves_grant(self):
        self.complete();self.m.rewrap('a','mail','owner');self.assertEqual('fake-access',self.access().token)
    def test_generation_fence_blocks_pre_revoke_grant(self):
        self.complete();grant=self.access();self.m.revoke('a','mail','owner',False)
        with self.assertRaises(Forbidden):self.m.fence('a','mail','ops',[SCOPE],grant)
    def test_corrupt_envelope_does_not_block_local_revocation(self):
        self.complete()
        with self.e.tx() as db: db.execute("UPDATE p_oauth_connections SET envelope='corrupt'")
        result = self.m.revoke('a','mail','owner')
        self.assertTrue(result['local_revoked']); self.assertEqual('unavailable',result['remote_status'])
        self.assertEqual('revoked',self.m.describe('a','mail')['status'])

    def test_remote_revoke_outbox_retries_and_blocks_reauthorization(self):
        self.complete(); self.p.fail = TimeoutError()
        self.assertEqual('uncertain', self.m.revoke('a','mail','owner')['remote_status'])
        with self.assertRaises(Conflict): self.begin()
        self.p.fail = None
        self.assertEqual('confirmed', self.m.retry_revocations('a','mail','owner')[0]['status'])
        self.assertTrue(self.begin())

    def test_wrong_account_issued_token_is_cleaned_up(self):
        self.p.account = 'wrong'
        with self.assertRaises(OAuthError): self.complete()
        self.assertEqual('revoke', self.p.calls[-1][0])
        with self.e.read() as db:
            row = db.execute('SELECT * FROM p_oauth_revocations').fetchone()
        self.assertEqual('confirmed', row['status']); self.assertEqual('', row['envelope'])

    def test_active_reauthorization_cannot_orphan_old_refresh_token(self):
        self.complete()
        with self.assertRaises(Conflict): self.begin()

    def test_session_revoked_during_exchange_prevents_store_and_cleans_token(self):
        state=self.begin()
        def guard(db):raise Forbidden('Session revoked')
        with self.assertRaises(OAuthError):
            self.m.complete('a','mail','owner','session',state,'code',guard)
        self.assertEqual('uncertain',self.m.describe('a','mail')['status'])
        self.assertEqual('revoke',self.p.calls[-1][0])

    def test_read_scope_check_applies_even_on_cached_token(self):
        self.complete()
        with self.e.tx() as db:db.execute("UPDATE p_oauth_connections SET scopes='[]'")
        with self.assertRaises(Forbidden):self.access()
    def test_state_ttl_bound_is_the_exact_instant_of_expiry(self):
        # begin() stores `expires = now + 600` and complete() reads
        # `pending['expires'] <= now`, so the LAST usable second is now+599 and
        # the callback at exactly now+600 is already expired. The pre-existing
        # test jumps 601 seconds, which is past the bound under both senses.
        self.now[0]=1000.0;state=self.begin();self.now[0]=1599.0
        self.complete(state)
        # A second state, since begin() refuses to start while a connection is
        # still 'active' -- the flow has to be closed before it can be reopened.
        self.m.revoke('a','mail','owner',False)
        self.now[0]=2000.0;state=self.begin();self.p.calls.clear();self.now[0]=2600.0
        with self.assertRaises(Forbidden):self.complete(state)
        self.assertEqual([],self.p.calls)
    def test_token_headroom_bound_is_sixty_seconds_exclusive(self):
        # access() reuses a cached token only while `expires > now + 60`, so at
        # exactly 60 seconds of remaining life it refreshes. Off by one in either
        # direction is invisible to `test_expiry_refresh`, which expires far out.
        self.complete()
        with self.e.read() as db:expires=db.execute('SELECT expires FROM p_oauth_connections').fetchone()['expires']
        p=self.p
        def call():
            p.calls.clear();self.access();return p.calls[-1][0] if p.calls else 'reuse'
        self.now[0]=expires-61;self.assertEqual('reuse',call())
        # A refresh moves the stored expiry forward, so re-read it each round.
        self.now[0]=expires-60;self.assertEqual('refresh',call())
        with self.e.read() as db:expires=db.execute('SELECT expires FROM p_oauth_connections').fetchone()['expires']
        self.now[0]=expires-60;self.assertEqual('refresh',call())
    def test_fence_rejects_at_the_expiry_instant_not_after(self):
        # fence() reads `row['expires'] <= now` -- equality is ALREADY invalid, so
        # a grant one second inside its life still works and the exact expiry
        # second does not. `test_generation_fence_blocks_pre_revoke_grant` only
        # exercises the generation term.
        now=1000.0;self.now[0]=now;self.complete()
        with self.e.read() as db:row=dict(db.execute('SELECT expires,generation FROM p_oauth_connections').fetchone())
        grant=self.access()
        good=AccessGrant(grant.token,grant.generation,row['expires'],grant.account)
        self.now[0]=row['expires']-1;self.m.fence('a','mail','ops',[SCOPE],good)
        self.now[0]=row['expires']
        with self.assertRaises(Forbidden):self.m.fence('a','mail','ops',[SCOPE],good)
        self.now[0]=row['expires']+1
        with self.assertRaises(Forbidden):self.m.fence('a','mail','ops',[SCOPE],good)
    def test_stale_attempt_bound_is_exclusive_at_one_twenty(self):
        # recover_stale() refuses while `started + 120 > now`, so an attempt that
        # has burned exactly 120 seconds is recoverable and one at 119 is not.
        # The pre-existing test jumps 121, which is past the bound either way.
        self.complete()
        for elapsed in (119,120,121):
            with self.e.tx() as db:
                db.execute("UPDATE p_oauth_connections SET status='refreshing',started=?,attempt='x'",(1000.0,))
            self.now[0]=1000.0+elapsed
            if elapsed < 120:
                with self.assertRaises(Conflict):self.m.recover_stale('a','mail','owner')
            else:
                self.assertEqual('uncertain',self.m.recover_stale('a','mail','owner')['status'])
            # recover_stale clears the envelope, and a repeated refresh-guard
            # failure would leave a revoked-less connection, so close and reopen.
            self.m.revoke('a','mail','owner',False)
            self.now[0]=2000.0+elapsed;self.complete()


class GoogleOAuthContractTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'enabled':True,'client_id':'fake.apps.googleusercontent.com','client_secret_env':'FAKE_GOOGLE_SECRET',
                  'redirect_uri':'https://app.example.invalid/oauth/google','expected_subject':'subject',
                  'scopes':sorted(BASE_SCOPES|{SCOPE})}
        self.calls=[]
        def transport(*args,**kw):self.calls.append((args,kw));return {'email_verified':True,'sub':'subject'}
        self.p=GoogleOAuth(self.cfg,lambda name:'synthetic-secret',transport)
    def test_pkce_s256_and_offline_consent(self):
        query=parse_qs(urlsplit(self.p.authorization_url('state','challenge')).query)
        self.assertEqual(['S256'],query['code_challenge_method']);self.assertEqual(['offline'],query['access_type'])
        self.assertNotIn('client_secret',query)
    def test_exchange_has_exact_redirect(self):
        self.p.exchange('code','verifier');fields=self.calls[0][0][2]
        self.assertEqual(self.cfg['redirect_uri'],fields['redirect_uri']);self.assertEqual('verifier',fields['code_verifier'])
    def test_identity_uses_pinned_userinfo(self):self.assertEqual('subject',self.p.identity('fake-token'))
    def test_disabled_or_unknown_fields_fail(self):
        for extra in [{'enabled':False},{'url':'https://attacker.invalid'},{'scopes':['openid']},{'enabled':'true'}]:
            with self.assertRaises(ValueError):GoogleOAuth({**self.cfg,**extra},lambda _: 'fake')
    def test_unsafe_redirects_fail(self):
        for url in ['http://app.invalid/cb','https://u:p@app.invalid/cb','https://app.invalid/cb?next=x','https://app.invalid/cb#f']:
            with self.assertRaises(ValueError):GoogleOAuth({**self.cfg,'redirect_uri':url},lambda _:'fake')
