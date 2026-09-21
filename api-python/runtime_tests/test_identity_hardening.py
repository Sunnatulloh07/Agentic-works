import concurrent.futures
import hashlib
import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app import identity_store as s
from app.storage import db, tx, reset
from app.authorization import authorize_claims, directory_enabled
from app.customer360 import create_customer,add_order,CustomerError

PASSWORD='correct horse battery'


def rotate_process(path,raw):
    os.environ['APP_DB']=path;reset()
    try:s.rotate_session(raw);return 'ok'
    except s.AuthenticationError:return 'denied'
    finally:reset()


def bootstrap_process(path, i):
    os.environ['APP_DB']=path;reset()
    try:s.bootstrap_identity(f'p{i}@example.com',PASSWORD,'P','workspace','Workspace');return 'ok'
    except s.IdentityError:return 'denied'
    finally:reset()


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=str(Path(self.tmp.name)/'db.sqlite')
        self.env=patch.dict(os.environ,{'APP_DB':self.path,'ENV':'production','IDENTITY_DIRECTORY':'true'});self.env.start();reset()
        self.me=s.register_user('owner@example.com',PASSWORD,'Owner')
        s.create_workspace(self.me['id'],'workspace','Workspace')
    def tearDown(self):reset();self.env.stop();self.tmp.cleanup()
    def session(self):return s.create_session(self.me['id'],'workspace')
    def claims(self,m):return {'sub':m['user_id'],'tenant_id':m['workspace_id'],'token_type':'user','role':'forged','sid':m['session_id']}
    def test_session_role_comes_from_directory(self):
        _,m=self.session();self.assertEqual('owner',authorize_claims(self.claims(m))['role'])
    def test_logout_invalidates_access_not_only_refresh(self):
        raw,m=self.session();s.revoke_session(raw)
        with self.assertRaises(s.AuthenticationError):authorize_claims(self.claims(m))
    def test_refresh_replay_revokes_descendants_and_commits(self):
        raw,m=self.session();next_raw,next_m=s.rotate_session(raw)
        with self.assertRaises(s.AuthenticationError):s.rotate_session(raw)
        reset()
        with self.assertRaises(s.AuthenticationError):s.rotate_session(next_raw)
        with self.assertRaises(s.AuthenticationError):authorize_claims(self.claims(next_m))
    def test_rotation_retires_previous_access(self):
        raw,m=self.session();s.rotate_session(raw)
        with self.assertRaises(s.AuthenticationError):authorize_claims(self.claims(m))
    def test_password_not_plaintext_and_not_in_user_response(self):
        row=db().execute('SELECT password_hash FROM p_users WHERE id=?',(self.me['id'],)).fetchone()[0]
        self.assertTrue(row.startswith('scrypt$'));self.assertNotIn(PASSWORD,row)
        self.assertNotIn('password_hash',s.authenticate('OWNER@example.com',PASSWORD))
    def test_invalid_scrypt_parameters_rejected(self):
        self.assertFalse(s._password_ok(PASSWORD,'scrypt$999999999$8$1$'+'ab'*16+'$'+'00'*32))
    def test_refresh_not_stored_in_clear(self):
        raw,m=self.session();row=db().execute('SELECT * FROM p_sessions WHERE id=?',(m['session_id'],)).fetchone()
        self.assertNotIn(raw,str(dict(row)));self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(),row['refresh_hash'])
    def test_disabled_user_cannot_access_or_refresh(self):
        raw,m=self.session()
        with tx() as c:c.execute("UPDATE p_users SET status='disabled' WHERE id=?",(self.me['id'],))
        with self.assertRaises(s.AuthenticationError):authorize_claims(self.claims(m))
        with self.assertRaises(s.AuthenticationError):s.rotate_session(raw)
    def test_suspended_workspace_blocks_access(self):
        _,m=self.session()
        with tx() as c:c.execute("UPDATE p_workspaces SET status='suspended' WHERE id='workspace'")
        with self.assertRaises(s.AuthenticationError):authorize_claims(self.claims(m))
    def test_membership_version_invalidates_access(self):
        raw,m=self.session()
        with tx() as c:c.execute('UPDATE p_memberships SET version=version+1')
        with self.assertRaises(s.AuthenticationError):authorize_claims(self.claims(m))
        with self.assertRaises(s.AuthenticationError):s.rotate_session(raw)
    def test_cross_tenant_session_denied(self):
        _,m=self.session();cl=self.claims(m);cl['tenant_id']='other'
        with self.assertRaises(s.AuthenticationError):authorize_claims(cl)
    def test_account_session_not_workspace_token(self):
        _,m=s.create_session(self.me['id']);cl=self.claims(m)
        with self.assertRaises(s.AuthenticationError):authorize_claims(cl)
        cl['token_type']='account';self.assertEqual('account',authorize_claims(cl)['role'])
    def test_production_cannot_disable_directory(self):
        with patch.dict(os.environ,{'IDENTITY_DIRECTORY':'false','ALLOW_INSECURE_DEV':'true'}):self.assertTrue(directory_enabled())
    def test_legacy_user_token_denied(self):
        with self.assertRaises(s.AuthenticationError):authorize_claims({'token_type':'user','sub':'operator','tenant_id':'workspace'})
    def test_invite_registration_atomic_and_email_bound(self):
        _,token=s.create_invitation(self.me['id'],'workspace','new@example.com','viewer')
        with self.assertRaises(s.AuthenticationError):s.register_with_invitation('attacker@example.com',PASSWORD,'A',token)
        self.assertEqual(1,db().execute('SELECT count(*) FROM p_users').fetchone()[0])
        new=s.register_with_invitation('new@example.com',PASSWORD,'N',token)
        self.assertEqual('viewer',s.membership(new['id'],'workspace')['role'])
        with self.assertRaises(s.AuthenticationError):s.accept_invitation(new['id'],token)
    def test_inviter_revocation_invalidates_old_invitation(self):
        _,token=s.create_invitation(self.me['id'],'workspace','new@example.com','viewer')
        with tx() as c:c.execute("UPDATE p_memberships SET role='viewer'")
        with self.assertRaises(s.AuthenticationError):s.register_with_invitation('new@example.com',PASSWORD,'N',token)
    def test_invitation_expired_rejected(self):
        _,token=s.create_invitation(self.me['id'],'workspace','new@example.com','viewer')
        with tx() as c:c.execute('UPDATE p_invitations SET expires=1')
        with self.assertRaises(s.AuthenticationError):s.register_with_invitation('new@example.com',PASSWORD,'N',token)
    def test_invitation_expiring_exactly_now_is_rejected(self):
        """The boundary the `expires=1` test cannot reach.

        `test_invitation_expired_rejected` sets expires to epoch 1, which is about
        fifty-seven years in the past. Both `expires <= now` and `expires < now`
        refuse that, so the test passes whichever predicate is in force and measures
        nothing about WHERE the bound sits. Only equality discriminates: at
        `expires == now` the inclusive form refuses and the exclusive form accepts.

        The safe direction for an expiry is to treat the deadline second itself as
        expired -- a token is valid strictly before its expiry, never at it -- so
        equality must REFUSE. Measured against a frozen clock so the comparison is
        exact rather than racing the wall clock.
        """
        _,token=s.create_invitation(self.me['id'],'workspace','new@example.com','viewer')
        frozen=[1_790_000_000.0]
        with patch.object(s.time,'time',lambda:frozen[0]):
            with tx() as c:c.execute('UPDATE p_invitations SET expires=?',(frozen[0],))
            with self.assertRaises(s.AuthenticationError):
                s.register_with_invitation('new@example.com',PASSWORD,'N',token)
            # One second earlier it must still be accepted, so the test cannot pass
            # by refusing every invitation regardless of the bound.
            frozen[0]-=1
            me=s.register_with_invitation('new@example.com',PASSWORD,'N',token)
        self.assertEqual('new@example.com',me['email'])
    def test_session_expiring_exactly_now_is_refused(self):
        """The same bound on the session path, where the stakes are access.

        A session whose expiry equals the current second must not validate. As
        above, a far-future or far-past fixture cannot show this: only equality
        separates an inclusive bound from an exclusive one.
        """
        raw,m=self.session()
        frozen=[1_790_000_000.0]
        with patch.object(s.time,'time',lambda:frozen[0]):
            with tx() as c:c.execute('UPDATE p_sessions SET expires=? WHERE id=?',(frozen[0],m['session_id']))
            with self.assertRaises(s.AuthenticationError):
                s.validate_session(m['session_id'],m['user_id'],m['workspace_id'])
            # One second earlier it must be live, so the test cannot pass by
            # refusing every session.
            frozen[0]-=1
            self.assertEqual(m['user_id'],
                             s.validate_session(m['session_id'],m['user_id'],
                                                m['workspace_id'])['user_id'])
    def test_session_refresh_expiring_exactly_now_is_denied(self):
        """The refresh path applies the same bound, so the two cannot diverge.

        `validate_session` and `rotate_session` are two readers of one fact. If one
        treated the deadline second as live and the other as dead, a caller would be
        authenticated for access and refused for refresh in the same instant.

        Two sessions rather than one used twice: a rotation refused for expiry
        deliberately revokes the whole token family before raising (so a rollback
        cannot resurrect the descendants), which would poison a retry on the same
        token and make the comparison measure the revocation instead of the bound.
        """
        frozen=[1_790_000_000.0]
        raw_at,m_at=self.session()
        raw_before,m_before=self.session()
        with patch.object(s.time,'time',lambda:frozen[0]):
            with tx() as c:
                c.execute('UPDATE p_sessions SET expires=? WHERE id=?',(frozen[0],m_at['session_id']))
                c.execute('UPDATE p_sessions SET expires=? WHERE id=?',(frozen[0]+1,m_before['session_id']))
            with self.assertRaises(s.AuthenticationError):s.rotate_session(raw_at)
            self.assertEqual(m_before['user_id'],s.rotate_session(raw_before)[1]['user_id'])
    def test_list_sessions_hides_an_expiring_exactly_now_session(self):
        """The listing agrees with the gate, so an operator sees what a caller gets.

        `list_sessions` filters with `expires > now`, the exact complement of the
        gate's `expires <= now` refusal. At equality the session must be absent from
        the listing as well as refused by the gate -- otherwise an operator sees a
        live session that the platform will not honour.
        """
        raw,m=self.session()
        frozen=[1_790_000_000.0]
        with patch.object(s.time,'time',lambda:frozen[0]):
            with tx() as c:c.execute('UPDATE p_sessions SET expires=? WHERE id=?',(frozen[0],m['session_id']))
            self.assertEqual([],s.list_sessions(m['user_id']))
            frozen[0]-=1
            self.assertEqual([m['session_id']],
                             [r['id'] for r in s.list_sessions(m['user_id'])])
    def test_reinvite_cannot_revive_old_session(self):
        _,token=s.create_invitation(self.me['id'],'workspace','new@example.com','viewer')
        new=s.register_with_invitation('new@example.com',PASSWORD,'N',token);raw,m=s.create_session(new['id'],'workspace')
        s.revoke_membership(self.me['id'],'workspace',new['id'])
        _,token=s.create_invitation(self.me['id'],'workspace','new@example.com','operator');s.accept_invitation(new['id'],token)
        with self.assertRaises(s.AuthenticationError):authorize_claims(self.claims(m))
        with self.assertRaises(s.AuthenticationError):s.rotate_session(raw)
    def test_logout_all_only_affects_owner(self):
        _,m=self.session();other=s.register_user('other@example.com',PASSWORD,'Other');raw2,m2=s.create_session(other['id'])
        s.revoke_all_sessions(self.me['id'])
        with self.assertRaises(s.AuthenticationError):authorize_claims(self.claims(m))
        self.assertEqual(other['id'],s.rotate_session(raw2)[1]['user_id'])
    def test_raw_secrets_absent_from_audit(self):
        raw,m=self.session();_,token=s.create_invitation(self.me['id'],'workspace','new@example.com','viewer');s.revoke_session(raw)
        rows=str([dict(x) for x in db().execute('SELECT * FROM p_identity_audit')])
        for secret in [PASSWORD,raw,token,'new@example.com']:self.assertNotIn(secret,rows)
    def test_rate_limit_persists_across_reconnect(self):
        for _ in range(2):s.throttle('test','account',2)
        reset()
        with self.assertRaises(s.AuthRateLimited):s.throttle('test','account',2)
    def test_rate_limit_bound_is_exclusive_so_the_limit_call_itself_succeeds(self):
        # `count > limit` means the limit-th call PASSES and the next one fails.
        # Measured by re-reading the counter after each call, so a `>=` flip is
        # caught at the limit-th call rather than three calls later.
        for i in range(1,4):
            try:s.throttle('edge','acct',3)
            except s.AuthRateLimited:self.fail(f'call {i} is at or below the limit and must be accepted')
            n=db().execute('SELECT count FROM p_auth_limits').fetchone()['count']
            self.assertEqual(i,n)
        with self.assertRaises(s.AuthRateLimited):s.throttle('edge','acct',3)
        # The refused call still recorded itself: the counter is not rolled back.
        self.assertEqual(4,db().execute('SELECT count FROM p_auth_limits').fetchone()['count'])
    def test_window_index_advances_exactly_on_the_tick(self):
        # window=int(t//900). The last second of a window and the first second of
        # the next one must land in DIFFERENT windows, so the counter resets there
        # and nowhere earlier.
        base=900*10000
        with patch.object(s.time,'time',lambda:base-1):
            s.throttle('win','acct',1)
            self.assertEqual(base//900-1,db().execute('SELECT window FROM p_auth_limits').fetchone()['window'])
            with self.assertRaises(s.AuthRateLimited):s.throttle('win','acct',1)
        with patch.object(s.time,'time',lambda:base):
            s.throttle('win','acct',1)
            rows=[dict(r) for r in db().execute('SELECT window,count FROM p_auth_limits ORDER BY window')]
            self.assertEqual([{'window':base//900-1,'count':2},{'window':base//900,'count':1}],rows)
        with patch.object(s.time,'time',lambda:base-900):
            with self.assertRaises(s.AuthRateLimited):s.throttle('win','acct',1)
    def test_cleanup_retains_the_previous_window_and_drops_older_ones(self):
        # `DELETE WHERE window < window-2` keeps the current, the previous and the
        # one before that (it is `< window-2`, not `<= window-2`), and drops
        # everything older. Deleting any more eagerly would erase a counter that a
        # request from two windows back still belongs to.
        base=900*20000;cur=base//900
        with patch.object(s.time,'time',lambda:base):
            with tx() as c:
                for w in (cur-4,cur-3,cur-2,cur-1,cur):
                    c.execute('INSERT OR REPLACE INTO p_auth_limits VALUES(?,?,1)',(f'keep{w}',w))
            s.throttle('cleanup','acct',1)
            kept=sorted(r[0] for r in db().execute('SELECT DISTINCT window FROM p_auth_limits'))
            self.assertEqual([cur-2,cur-1,cur],kept)
    def test_throttle_buckets_do_not_share_a_counter(self):
        # The bucket is sha256(namespace:key), so a different account or a
        # different surface must not consume the same budget.
        with patch.object(s.time,'time',lambda:900*30000):
            s.throttle('ns','a',1)
            with self.assertRaises(s.AuthRateLimited):s.throttle('ns','a',1)
            s.throttle('ns','b',1)
            s.throttle('other','a',1)
            self.assertEqual(3,db().execute('SELECT count(*) FROM p_auth_limits').fetchone()[0])
    def test_parallel_refresh_at_most_one_success(self):
        raw,m=self.session()
        with concurrent.futures.ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn')) as pool:
            results=list(pool.map(rotate_process,[self.path]*2,[raw]*2))
        self.assertEqual(1,results.count('ok'));self.assertEqual(1,results.count('denied'))
        self.assertEqual(0,db().execute('SELECT count(*) FROM p_sessions WHERE revoked_at=0').fetchone()[0])
    def test_bootstrap_is_atomic_across_processes(self):
        reset();os.remove(self.path);db()
        with concurrent.futures.ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn')) as pool:
            results=list(pool.map(bootstrap_process,[self.path]*2,[1,2]))
        self.assertEqual(1,results.count('ok'));self.assertEqual(1,db().execute('SELECT count(*) FROM p_users').fetchone()[0])
    def test_nested_transaction_rollback(self):
        with self.assertRaises(RuntimeError):
            with tx():
                s.create_workspace(self.me['id'],'rollback-ws','No')
                raise RuntimeError()
        self.assertIsNone(s.membership(self.me['id'],'rollback-ws'))
    def test_customer_order_cannot_move_between_customers(self):
        a=create_customer('workspace','A',actor=self.me['id'])
        b=create_customer('workspace','B',actor=self.me['id'])
        add_order('workspace',a['id'],'order',actor=self.me['id'])
        with self.assertRaises(CustomerError):add_order('workspace',b['id'],'order',actor=self.me['id'])
    def test_freeze_rejects_customer_mutations(self):
        with tx() as c:c.execute('INSERT INTO p_freeze VALUES(?,1)',('workspace',))
        with self.assertRaises(s.AuthenticationError):create_customer('workspace','A',actor=self.me['id'])
    def test_new_schema_upgrade_revokes_legacy_refresh(self):
        raw,m=self.session()
        with tx() as c:
            c.execute('DELETE FROM p_identity_migrations')
            c.execute("UPDATE p_sessions SET family_id=''")
        reset();db()
        with self.assertRaises(s.AuthenticationError):s.rotate_session(raw)
        self.assertEqual(1,db().execute('SELECT count(*) FROM p_identity_migrations').fetchone()[0])
