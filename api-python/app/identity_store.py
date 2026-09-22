"""Preview directory with atomic membership, session-family fencing and audit.

Local passwords are a staging foundation, not a replacement for production
OIDC/MFA/email delivery. HTTP provisioning is admin/invite gated.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from .storage import db, tx

EMAIL_RE = re.compile(r'^[^@\s]{1,128}@[^@\s]{1,255}$')
SLUG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$')
ROLES = {'owner', 'operator', 'integrator', 'viewer'}
ACCOUNT = '__account__'

# Declared bounds (§155).  The TTL and throttle numbers used to be default
# arguments and inline comparisons, which no test could address by value; the
# regular expressions above are pinned behaviourally instead, by feeding them a
# local part and a slug one character past each ceiling.
MAX_SESSION_TTL = 30 * 86400
MIN_SESSION_TTL = 60
THROTTLE_LIMIT = 20
THROTTLE_WINDOW_SECONDS = 900
INVITATION_TTL_SECONDS = 86_400
MIN_INVITATION_TTL_SECONDS = 300
MAX_INVITATION_TTL_SECONDS = 604_800

# Text ceilings.  A pin that lived only in ``tests/`` was not a pin: that suite is
# run by no gate and is 35 red against an API retired to 410 Gone.
MAX_EMAIL_CHARS = 320
MIN_PASSWORD_CHARS = 12
MAX_PASSWORD_CHARS = 256
MIN_CANDIDATE_PASSWORD_CHARS = 1
MIN_TOKEN_CHARS = 20
MAX_TOKEN_CHARS = 256

# Key-derivation parameters.  These are security bounds, not tunables: lowering
# the cost factor lowers the cost of attacking every stored password, and
# nothing used to notice.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16

# Cardinality guard: the last owner of a workspace may not be removed.
LAST_OWNER_GUARD = 1
# Fixed valid dummy hash makes unknown-user login perform the same scrypt work.
DUMMY = (f'scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$'
        + 'ab' * SALT_BYTES + '$' + '00' * SCRYPT_DKLEN)


class IdentityError(ValueError): pass
class AuthenticationError(PermissionError): pass
class AuthRateLimited(RuntimeError): pass


def _email(value):
    if not isinstance(value, str) or not EMAIL_RE.fullmatch(value.strip()) or len(value) > MAX_EMAIL_CHARS:
        raise IdentityError('Invalid email')
    return value.strip().casefold()


def _name(value, field, maximum=256):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise IdentityError('Invalid ' + field)
    return value.strip()


def _workspace_id(value):
    value = _name(value, 'workspace_id', 64)
    if not SLUG_RE.fullmatch(value): raise IdentityError('Invalid workspace_id')
    return value


def _password_hash(password, *, salt=None):
    if not isinstance(password, str) or not MIN_PASSWORD_CHARS <= len(password) <= MAX_PASSWORD_CHARS:
        raise IdentityError(
            f'Password must have {MIN_PASSWORD_CHARS}..{MAX_PASSWORD_CHARS} characters')
    salt = salt or secrets.token_bytes(SALT_BYTES)
    value = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
                           p=SCRYPT_P, dklen=SCRYPT_DKLEN)
    return (f'scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$'
            + salt.hex() + '$' + value.hex())


def _password_ok(password, encoded):
    if not isinstance(password, str) or not MIN_CANDIDATE_PASSWORD_CHARS <= len(password) <= MAX_PASSWORD_CHARS: return False
    try:
        algo, n, r, p, salt, expected = encoded.split('$')
        if ((algo, n, r, p) != ('scrypt', str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P))
                or len(salt) != SALT_BYTES * 2 or len(expected) != SCRYPT_DKLEN * 2):
            return False
        value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(value.hex(), expected)
    except (ValueError, TypeError): return False


def _token_hash(value):
    if not isinstance(value, str) or not MIN_TOKEN_CHARS <= len(value) <= MAX_TOKEN_CHARS:
        raise AuthenticationError('Invalid token')
    return hashlib.sha256(value.encode()).hexdigest()


def audit(c, workspace, actor, action, target=''):
    # Explicit IDs only, never password, invitation, refresh token, email or provider data.
    c.execute('INSERT INTO p_identity_audit(workspace_id,actor,action,target,created) VALUES(?,?,?,?,?)',
              (workspace, actor, action, target, time.time()))


def throttle(namespace, key, limit=THROTTLE_LIMIT, window_seconds=THROTTLE_WINDOW_SECONDS):
    """Durable fixed-window throttle, must run before expensive password verification."""
    bucket = hashlib.sha256((namespace + ':' + str(key)[:512]).encode()).hexdigest()
    window = int(time.time() // window_seconds)
    with tx() as c:
        c.execute('DELETE FROM p_auth_limits WHERE window<?', (window-2,))
        c.execute('INSERT INTO p_auth_limits VALUES(?,?,1) ON CONFLICT(bucket,window) DO UPDATE SET count=count+1', (bucket,window))
        count = c.execute('SELECT count FROM p_auth_limits WHERE bucket=? AND window=?', (bucket,window)).fetchone()[0]
    if count > limit: raise AuthRateLimited('Try again later')


def _user(c, user_id):
    row = c.execute('SELECT id,email,display_name,status FROM p_users WHERE id=?', (user_id,)).fetchone()
    if not row or row['status'] != 'active': raise AuthenticationError('Inactive user')
    return dict(row)


def user(user_id): return _user(db(), user_id)


def _register(c, email, password_hash, display_name):
    uid = 'usr_' + uuid.uuid4().hex; now = time.time()
    try:
        c.execute('INSERT INTO p_users VALUES(?,?,?,?,?,?,?,?)',
                  (uid,email,'local:'+email,display_name,password_hash,'active',now,now))
    except sqlite3.IntegrityError as exc: raise IdentityError('Account unavailable') from exc
    audit(c, '', uid, 'user.created', uid)
    return _user(c, uid)


def register_user(email, password, display_name):
    """Trusted provisioning/test primitive, not public self-registration."""
    email, name = _email(email), _name(display_name, 'display_name')
    encoded = _password_hash(password)
    with tx() as c: return _register(c, email, encoded, name)


def authenticate(email, password):
    try: email = _email(email)
    except IdentityError: raise AuthenticationError('Invalid credentials')
    throttle('login-account', email)
    row = db().execute('SELECT * FROM p_users WHERE email=?', (email,)).fetchone()
    ok = _password_ok(password, row['password_hash'] if row else DUMMY)
    if not ok or not row or row['status'] != 'active': raise AuthenticationError('Invalid credentials')
    return user(row['id'])


def _workspace(c, uid, wid, name, plan, region):
    _user(c, uid); now = time.time()
    try:
        c.execute('INSERT INTO p_workspaces VALUES(?,?,?,?,?,?,?)', (wid,name,plan,'active',region,now,now))
        c.execute('INSERT INTO p_memberships VALUES(?,?,?,?,?,?,?)', (wid,uid,'owner','active',1,now,now))
    except sqlite3.IntegrityError as exc: raise IdentityError('Workspace unavailable') from exc
    audit(c, wid, uid, 'workspace.created', wid)
    return {'id':wid, 'name':name, 'plan':plan, 'status':'active', 'region':region, 'role':'owner'}


def create_workspace(user_id, workspace_id, name, *, plan='starter', region='uz'):
    """Trusted admin provisioning primitive; HTTP users cannot claim pack names or plans."""
    wid = _workspace_id(workspace_id); name = _name(name, 'name')
    plan, region = _name(plan,'plan',64), _name(region,'region',32)
    with tx() as c: return _workspace(c,user_id,wid,name,plan,region)


def bootstrap_identity(email, password, display_name, workspace_id, workspace_name):
    email = _email(email); encoded = _password_hash(password)
    name = _name(display_name,'display_name'); wid = _workspace_id(workspace_id)
    wname = _name(workspace_name,'workspace_name')
    with tx() as c:
        if c.execute('SELECT 1 FROM p_users LIMIT 1').fetchone(): raise IdentityError('Already initialized')
        me = _register(c,email,encoded,name)
        _workspace(c,me['id'],wid,wname,'starter','uz')
        return me


def _membership(c, uid, wid):
    row = c.execute('''SELECT m.workspace_id,m.user_id,m.role,m.status,m.version,
      w.name,w.plan,w.region,w.status workspace_status,u.status user_status
      FROM p_memberships m JOIN p_workspaces w ON w.id=m.workspace_id
      JOIN p_users u ON u.id=m.user_id WHERE m.user_id=? AND m.workspace_id=?''',(uid,wid)).fetchone()
    if not row or row['status']!='active' or row['workspace_status']!='active' or row['user_status']!='active': return None
    return dict(row)


def membership(user_id, workspace_id):
    return _membership(db(),user_id,_workspace_id(workspace_id))


def _owner(c, actor, wid):
    m = _membership(c,actor,wid)
    if not m or m['role']!='owner': raise AuthenticationError('Owner required')
    return m


def list_workspaces(user_id):
    user(user_id)
    return [dict(r) for r in db().execute('''SELECT w.id,w.name,w.plan,w.status,w.region,m.role,m.version
      FROM p_memberships m JOIN p_workspaces w ON w.id=m.workspace_id
      WHERE m.user_id=? AND m.status='active' AND w.status='active' ORDER BY w.created''',(user_id,))]


def create_invitation(actor_id, workspace_id, email, role, ttl_seconds=INVITATION_TTL_SECONDS):
    wid = _workspace_id(workspace_id); email = _email(email)
    if role not in ROLES-{'owner'}: raise IdentityError('Invalid invitation role')
    if type(ttl_seconds) is not int or not MIN_INVITATION_TTL_SECONDS<=ttl_seconds<=MAX_INVITATION_TTL_SECONDS: raise IdentityError('Invalid expiry')
    raw=secrets.token_urlsafe(32); iid='inv_'+uuid.uuid4().hex; now=time.time()
    with tx() as c:
        _owner(c,actor_id,wid)
        c.execute('INSERT INTO p_invitations VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (iid,wid,email,role,_token_hash(raw),now+ttl_seconds,0,0,actor_id,now))
        audit(c,wid,actor_id,'invitation.created',iid)
    return {'id':iid,'workspace_id':wid,'email':email,'role':role,'expires':now+ttl_seconds},raw


def _invitation(c, raw, email):
    r=c.execute('SELECT * FROM p_invitations WHERE token_hash=?',(_token_hash(raw),)).fetchone()
    if not r or r['accepted_at'] or r['revoked_at'] or r['expires']<=time.time() or r['email']!=email:
        raise AuthenticationError('Invalid invitation')
    _owner(c,r['created_by'],r['workspace_id'])
    return r


def _accept(c, uid, invite):
    wid=invite['workspace_id']; now=time.time()
    existing=_membership(c,uid,wid)
    if not existing:
        c.execute('''INSERT INTO p_memberships VALUES(?,?,?,?,?,?,?) ON CONFLICT(workspace_id,user_id)
          DO UPDATE SET role=excluded.role,status='active',version=p_memberships.version+1,updated=excluded.updated''',
                  (wid,uid,invite['role'],'active',1,now,now))
    c.execute('UPDATE p_invitations SET accepted_at=? WHERE id=?',(now,invite['id']))
    audit(c,wid,uid,'invitation.accepted',invite['id'])
    return _membership(c,uid,wid)


def accept_invitation(user_id, raw_token):
    with tx() as c:
        me=_user(c,user_id)
        return _accept(c,user_id,_invitation(c,raw_token,me['email']))


def register_with_invitation(email, password, display_name, raw_token):
    email=_email(email); name=_name(display_name,'display_name')
    # Validate token before expensive hashing, then revalidate atomically.
    _invitation(db(),raw_token,email)
    encoded=_password_hash(password)
    with tx() as c:
        invite=_invitation(c,raw_token,email)
        me=_register(c,email,encoded,name)
        _accept(c,me['id'],invite)
        return me


def revoke_membership(actor_id, workspace_id, target_user_id):
    wid=_workspace_id(workspace_id)
    with tx() as c:
        _owner(c,actor_id,wid)
        target=c.execute('SELECT * FROM p_memberships WHERE workspace_id=? AND user_id=?',(wid,target_user_id)).fetchone()
        if not target or target['status']!='active': raise IdentityError('Membership not found')
        if target['role']=='owner' and c.execute("SELECT count(*) FROM p_memberships WHERE workspace_id=? AND role='owner' AND status='active'",(wid,)).fetchone()[0]<=LAST_OWNER_GUARD:
            raise IdentityError('Last owner cannot be revoked')
        now=time.time()
        c.execute("UPDATE p_memberships SET status='revoked',version=version+1,updated=? WHERE workspace_id=? AND user_id=?",(now,wid,target_user_id))
        c.execute('UPDATE p_sessions SET revoked_at=? WHERE user_id=? AND workspace_id=? AND revoked_at=0',(now,target_user_id,wid))
        audit(c,wid,actor_id,'membership.revoked',target_user_id)


def _session_identity(c, uid, wid):
    _user(c,uid)
    if wid==ACCOUNT: return {'workspace_id':ACCOUNT,'user_id':uid,'role':'account','version':0}
    m=_membership(c,uid,wid)
    if not m: raise AuthenticationError('Active membership required')
    return m


def _new_session(c, uid, wid, expires, *, family=None, previous=''):
    m=_session_identity(c,uid,wid); sid='ses_'+uuid.uuid4().hex; raw=secrets.token_urlsafe(48)
    c.execute('''INSERT INTO p_sessions(id,user_id,workspace_id,refresh_hash,expires,revoked_at,
      rotated_from,created,family_id,membership_version) VALUES(?,?,?,?,?,0,?,?,?,?)''',
              (sid,uid,wid,_token_hash(raw),expires,previous,time.time(),family or sid,m['version']))
    return raw,{**m,'session_id':sid,'session_expires':expires}


def create_session(user_id, workspace_id=ACCOUNT, ttl_seconds=MAX_SESSION_TTL):
    if type(ttl_seconds) is not int or not MIN_SESSION_TTL<=ttl_seconds<=MAX_SESSION_TTL: raise IdentityError('Invalid session TTL')
    if workspace_id!=ACCOUNT: _workspace_id(workspace_id)
    with tx() as c:
        result=_new_session(c,user_id,workspace_id,time.time()+ttl_seconds)
        audit(c,workspace_id,user_id,'session.created',result[1]['session_id'])
        return result


def validate_session(sid, user_id, workspace_id):
    c=db(); row=c.execute('SELECT * FROM p_sessions WHERE id=? AND user_id=? AND workspace_id=?',(sid,user_id,workspace_id)).fetchone()
    if not row or row['revoked_at'] or row['expires']<=time.time(): raise AuthenticationError('Invalid session')
    m=_session_identity(c,user_id,workspace_id)
    if row['membership_version']!=m['version']: raise AuthenticationError('Membership changed')
    return {**m,'session_id':sid}


def rotate_session(raw_token):
    hashed=_token_hash(raw_token); denied=False; result=None; now=time.time()
    with tx() as c:
        row=c.execute('SELECT * FROM p_sessions WHERE refresh_hash=?',(hashed,)).fetchone()
        if not row: denied=True
        elif row['revoked_at'] or row['expires']<=now:
            # Commit replay revocation before raising, so rollback cannot resurrect descendants.
            c.execute('UPDATE p_sessions SET revoked_at=? WHERE family_id=? AND revoked_at=0',(now,row['family_id']))
            audit(c,row['workspace_id'],row['user_id'],'session.replay_or_expired',row['id']); denied=True
        else:
            try:
                m=_session_identity(c,row['user_id'],row['workspace_id'])
                if row['membership_version']!=m['version']: raise AuthenticationError('Membership changed')
            except AuthenticationError:
                c.execute('UPDATE p_sessions SET revoked_at=? WHERE family_id=?',(now,row['family_id'])); denied=True
            if not denied:
                c.execute('UPDATE p_sessions SET revoked_at=? WHERE id=?',(now,row['id']))
                result=_new_session(c,row['user_id'],row['workspace_id'],row['expires'],family=row['family_id'],previous=row['id'])
                audit(c,row['workspace_id'],row['user_id'],'session.rotated',row['id'])
    if denied: raise AuthenticationError('Invalid refresh session')
    return result


def revoke_session(raw_token):
    hashed=_token_hash(raw_token)
    with tx() as c:
        r=c.execute('SELECT * FROM p_sessions WHERE refresh_hash=?',(hashed,)).fetchone()
        if r:
            c.execute('UPDATE p_sessions SET revoked_at=? WHERE family_id=? AND revoked_at=0',(time.time(),r['family_id']))
            audit(c,r['workspace_id'],r['user_id'],'session.logout',r['id'])


def revoke_all_sessions(user_id):
    with tx() as c:
        _user(c,user_id)
        c.execute('UPDATE p_sessions SET revoked_at=? WHERE user_id=? AND revoked_at=0',(time.time(),user_id))
        audit(c,'',user_id,'session.logout_all',user_id)


def list_sessions(user_id):
    user(user_id)
    return [dict(r) for r in db().execute('SELECT id,workspace_id,created,expires FROM p_sessions WHERE user_id=? AND revoked_at=0 AND expires>?',(user_id,time.time()))]
