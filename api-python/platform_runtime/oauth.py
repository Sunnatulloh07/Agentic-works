"""Transactional provider OAuth lifecycle with encrypted tokens and generation fences.

No provider network I/O occurs inside a SQLite transaction. Ambiguous exchange or
refresh failures require reauthorization, never a blind token re-dispatch. Core
methods are service boundaries; the HTTP adapter supplies authenticated actor and
session, and tool handlers enforce their active engine step separately.
"""
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from .engine import Conflict, Forbidden, NotFound, encode, digest

SCHEMA = '''
CREATE TABLE IF NOT EXISTS p_oauth_connections(
 tenant TEXT NOT NULL, id TEXT NOT NULL, provider TEXT NOT NULL,
 account TEXT NOT NULL, config_hash TEXT NOT NULL, generation INTEGER NOT NULL,
 status TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT '[]',
 envelope TEXT NOT NULL DEFAULT '', expires REAL NOT NULL DEFAULT 0,
 attempt TEXT NOT NULL DEFAULT '', started REAL NOT NULL DEFAULT 0,
 owner TEXT NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS p_oauth_states(
 state_hash TEXT PRIMARY KEY, tenant TEXT NOT NULL, connection TEXT NOT NULL,
 actor TEXT NOT NULL, session_hash TEXT NOT NULL, generation INTEGER NOT NULL,
 config_hash TEXT NOT NULL, expires REAL NOT NULL,
 envelope TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS p_oauth_state_connection ON p_oauth_states(tenant,connection);
CREATE TABLE IF NOT EXISTS p_oauth_revocations(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, connection TEXT NOT NULL,
 generation INTEGER NOT NULL, config_hash TEXT NOT NULL, envelope TEXT NOT NULL,
 status TEXT NOT NULL, attempt TEXT NOT NULL DEFAULT '', started REAL NOT NULL DEFAULT 0,
 updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS p_oauth_revoke_pending ON p_oauth_revocations(tenant,connection,status);
'''


class OAuthError(RuntimeError):
    pass


class InvalidGrant(OAuthError):
    pass


class RefreshInProgress(Conflict):
    pass


def bounded(value, maximum=256):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Bounded OAuth field required')
    return value


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value):
        raise ValueError('Invalid OAuth identifier')
    return value


def opaque_hash(value):
    return hashlib.sha256(bounded(value, 4096).encode()).hexdigest()


def pkce_challenge(verifier):
    import base64
    if not isinstance(verifier, str) or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier):
        raise ValueError('Invalid PKCE verifier')
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')


@dataclass(frozen=True)
class AccessGrant:
    # Never serialize this object into public responses or log it.
    token: str
    generation: int
    expires: float
    account: str

    def __repr__(self):
        return 'AccessGrant(<redacted>)'


class OAuthManager:
    def __init__(self, engine, vault, provider):
        self.e, self.vault, self.provider = engine, vault, provider
        self.config_hash = digest(provider.public_config())
        self.name = ident(provider.name)
        self.account = bounded(provider.expected_account)
        self.required_scopes = frozenset(provider.scopes)
        if not self.required_scopes or len(self.required_scopes) > 40:
            raise ValueError('Explicit OAuth scopes required')
        for scope in self.required_scopes: bounded(scope, 256)

    def _row(self, db, tenant, connection):
        row = db.execute('SELECT * FROM p_oauth_connections WHERE tenant=? AND id=?', (tenant, connection)).fetchone()
        if not row: raise NotFound('OAuth connection not found')
        return row

    def _context(self, tenant, connection, generation, purpose='tokens'):
        return {'tenant': tenant, 'connection': connection, 'generation': generation,
                'provider': self.name, 'account': self.account, 'purpose': purpose}

    def _configured(self, row):
        if (row['provider'] != self.name or row['account'] != self.account or row['config_hash'] != self.config_hash):
            raise Forbidden('OAuth configuration changed; authorize again')

    def _owner(self, db, tenant, actor, *, active=True):
        self.e.require_authority(db, tenant, 'web', actor, ('owner',))
        if active: self.e.require_active(db, tenant)

    def begin(self, tenant, connection, actor, session):
        ident(tenant); ident(connection); bounded(actor); bounded(session)
        state = secrets.token_urlsafe(32); verifier = secrets.token_urlsafe(64)
        challenge = pkce_challenge(verifier)
        url = self.provider.authorization_url(state, challenge)
        with self.e.tx() as db:
            self._owner(db, tenant, actor)
            if db.execute("SELECT 1 FROM p_oauth_revocations WHERE tenant=? AND connection=? AND status!='confirmed'", (tenant, connection)).fetchone():
                raise Conflict('Resolve pending remote revocation before reauthorization')
            old = db.execute('SELECT generation,status FROM p_oauth_connections WHERE tenant=? AND id=?', (tenant, connection)).fetchone()
            if old and old['status'] in {'active','refreshing','exchanging'}:
                raise Conflict('Disconnect active or in-flight OAuth connection before reconnecting')
            generation = old['generation'] + 1 if old else 1
            pending = self.vault.seal({'verifier': verifier}, self._context(tenant, connection, generation, 'pkce'))
            # Reauthorization fences old token calls immediately. Provider revoke
            # is a separate operation and never implied by this local transition.
            db.execute('DELETE FROM p_oauth_states WHERE tenant=? AND connection=?', (tenant, connection))
            db.execute('INSERT INTO p_oauth_connections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
                       'ON CONFLICT(tenant,id) DO UPDATE SET provider=excluded.provider,account=excluded.account,'
                       'config_hash=excluded.config_hash,generation=excluded.generation,status=excluded.status,'
                       "scopes='[]',envelope='',expires=0,attempt='',started=0,owner=excluded.owner,updated=excluded.updated",
                       (tenant, connection, self.name, self.account, self.config_hash, generation,
                        'authorizing', '[]', '', 0, '', 0, actor, self.e.clock()))
            db.execute('INSERT INTO p_oauth_states VALUES(?,?,?,?,?,?,?,?,?,0)',
                       (opaque_hash(state), tenant, connection, actor, opaque_hash(session), generation,
                        self.config_hash, self.e.clock()+600, pending))
            self.e.audit(db, tenant, '', 'oauth.started', actor, {'connection': connection, 'generation': generation})
        return {'authorization_url': url, 'connection': connection, 'generation': generation, 'expires_in': 600}

    def _tokens(self, body, old=None):
        if not isinstance(body, dict) or body.get('token_type', '').lower() != 'bearer':
            raise OAuthError('Invalid provider token response')

        def credential(value, name):
            """Bound a provider-issued credential, keeping this method's contract.

            ``bounded`` raises ``ValueError``, and ``complete`` runs ``_tokens``
            inside a ``try`` whose broad handler re-wraps everything as
            'Authorization outcome unavailable; authorize again'. So a token that was
            simply too long was reported as an UNKNOWN outcome: the operator is sent
            to look for a network fault, and the flow is marked ``uncertain`` and a
            revoke queued, when the provider's answer was in fact fully known and
            refused for its size. Every other bound in this method raises
            ``OAuthError`` with a specific reason; these two did not, and that
            difference was invisible because the message they produced was generic.
            """
            try:
                return bounded(value, 16000)
            except ValueError:
                raise OAuthError(f'Provider {name} exceeds the accepted size') from None

        access = credential(body.get('access_token'), 'access token')
        if any(c.isspace() for c in access) or not access.isascii():
            raise OAuthError('Invalid access credential')
        refresh = body.get('refresh_token', (old or {}).get('refresh_token'))
        refresh = credential(refresh, 'refresh token')
        if any(c.isspace() for c in refresh) or not refresh.isascii(): raise OAuthError('Invalid refresh credential')
        expires = body.get('expires_in')
        if type(expires) is not int or not 60 <= expires <= 86400:
            raise OAuthError('Invalid token expiry')
        raw_scope = body.get('scope')
        if raw_scope is None and old:
            scopes = old['scopes']
        elif isinstance(raw_scope, str) and len(raw_scope) <= 10000:
            scopes = sorted(set(raw_scope.split()))
        else:
            raise OAuthError('Granted scopes missing')
        # Deliberately reject unrequested extra scopes. Operator config must
        # explicitly include canonical granted names for pre-existing grants.
        if set(scopes) != self.required_scopes:
            raise Forbidden('Granted scopes differ from configured scopes')
        return {'access_token': access, 'refresh_token': refresh, 'scopes': sorted(scopes)}, self.e.clock()+expires

    def _failure(self, tenant, connection, generation, attempt, status):
        with self.e.tx() as db:
            changed = db.execute('UPDATE p_oauth_connections SET status=?,envelope=?,attempt=?,updated=? '
                'WHERE tenant=? AND id=? AND generation=? AND attempt=?',
                (status, '', '', self.e.clock(), tenant, connection, generation, attempt))
            if changed.rowcount:
                self.e.audit(db, tenant, '', 'oauth.'+status, 'oauth', {'connection': connection, 'generation': generation})

    def complete(self, tenant, connection, actor, session, state, code, session_guard=None):
        ident(tenant); ident(connection); bounded(actor); bounded(session); bounded(code, 4096)
        attempt = secrets.token_hex(16)
        with self.e.tx() as db:
            self._owner(db, tenant, actor)
            row = self._row(db, tenant, connection); self._configured(row)
            pending = db.execute('SELECT * FROM p_oauth_states WHERE state_hash=?', (opaque_hash(state),)).fetchone()
            if not pending or pending['consumed'] or pending['expires'] <= self.e.clock():
                raise Forbidden('OAuth state invalid or expired')
            if (pending['tenant'], pending['connection'], pending['actor'], pending['session_hash'], pending['generation'], pending['config_hash']) != (
                    tenant, connection, actor, opaque_hash(session), row['generation'], self.config_hash):
                raise Forbidden('OAuth callback binding mismatch')
            if row['status'] != 'authorizing': raise Conflict('OAuth flow no longer pending')
            generation = row['generation']
            verifier = self.vault.open(pending['envelope'], self._context(tenant, connection, generation, 'pkce'))['verifier']
            db.execute("UPDATE p_oauth_states SET consumed=1,envelope='' WHERE state_hash=?", (pending['state_hash'],))
            db.execute("UPDATE p_oauth_connections SET status='exchanging',attempt=?,started=? WHERE tenant=? AND id=?",
                       (attempt, self.e.clock(), tenant, connection))
        body = None
        try:
            body = self.provider.exchange(code, verifier)
            tokens, expires = self._tokens(body)
            if self.provider.identity(tokens['access_token']) != self.account:
                raise Forbidden('Provider account mismatch')
            self._store(tenant, connection, generation, attempt, tokens, expires, actor, session_guard)
        except InvalidGrant:
            self._failure(tenant, connection, generation, attempt, 'reauth_required')
            raise OAuthError('Provider authorization rejected; authorize again') from None
        except (OAuthError, Forbidden) as error:
            # The provider's answer was READ and refused on this module's own terms:
            # a bad expiry, a scope mismatch, an over-long credential, a different
            # account. Measured before this fix, five distinct refusals all reported
            # 'Authorization outcome unavailable; authorize again', which sends an
            # operator looking for a network fault and records 'uncertain' in the
            # audit for a reply that was fully understood. The transition and the
            # revoke are unchanged, because a credential we cannot use still has to
            # be revoked.
            self._failure(tenant, connection, generation, attempt, 'uncertain')
            self._cleanup_issued(tenant, connection, generation, body)
            if isinstance(error, Forbidden):
                # `Forbidden` here is a provider-side or lifecycle refusal, not a
                # statement about the caller's rights, so it is carried across as
                # this method's own error type -- the caller's contract is
                # ``OAuthError`` -- while keeping the specific text. The messages on
                # this path are fixed strings, so nothing provider-supplied leaks.
                raise OAuthError(str(error)) from None
            raise
        except Exception:
            self._failure(tenant, connection, generation, attempt, 'uncertain')
            self._cleanup_issued(tenant, connection, generation, body)
            raise OAuthError('Authorization outcome unavailable; authorize again') from None
        return self.describe(tenant, connection)

    def _store(self, tenant, connection, generation, attempt, tokens, expires, actor, session_guard=None):
        with self.e.tx() as db:
            if session_guard: session_guard(db)
            self._owner(db, tenant, actor)
            row = self._row(db, tenant, connection); self._configured(row)
            if row['generation'] != generation or row['attempt'] != attempt or row['status'] not in {'refreshing', 'exchanging'}:
                raise Conflict('OAuth operation fenced')
            envelope = self.vault.seal(tokens, self._context(tenant, connection, generation))
            db.execute("UPDATE p_oauth_connections SET status='active',envelope=?,scopes=?,expires=?,attempt='',updated=? WHERE tenant=? AND id=?",
                       (envelope, encode(tokens['scopes']), expires, self.e.clock(), tenant, connection))
            self.e.audit(db, tenant, '', 'oauth.active', actor, {'connection': connection, 'generation': generation})

    def _agent(self, db, tenant, connection, agent, required):
        self.e.require_active(db, tenant)
        policy = self.e.policy(tenant, agent)
        allowed = policy.get('allowed_connections', [])
        if connection not in allowed: raise Forbidden('Agent connection access denied')
        row = self._row(db, tenant, connection); self._configured(row)
        # Credential owner must remain an active owner; stale credentials do not
        # survive membership removal just because a different task actor exists.
        self._owner(db, tenant, row['owner'])
        if not set(required) <= set(json.loads(row['scopes'])):
            raise Forbidden('Operation scope denied')
        return row

    def access(self, tenant, connection, agent, required):
        ident(tenant); ident(connection); bounded(agent)
        if not required or not set(required) <= self.required_scopes:
            raise Forbidden('Explicit configured operation scopes required')
        attempt = secrets.token_hex(16)
        with self.e.tx() as db:
            row = self._agent(db, tenant, connection, agent, required)
            if row['status'] == 'refreshing':
                raise RefreshInProgress('Refresh in progress; retry only the safe read later')
            if row['status'] != 'active': raise Forbidden('OAuth connection requires authorization')
            tokens = self.vault.open(row['envelope'], self._context(tenant, connection, row['generation']))
            if row['expires'] > self.e.clock()+60:
                return AccessGrant(tokens['access_token'], row['generation'], row['expires'], row['account'])
            generation = row['generation']
            db.execute("UPDATE p_oauth_connections SET status='refreshing',attempt=?,started=?,updated=? WHERE tenant=? AND id=?",
                       (attempt, self.e.clock(), self.e.clock(), tenant, connection))
        body = None
        try:
            body = self.provider.refresh(tokens['refresh_token'])
            renewed, expires = self._tokens(body, tokens)
            if self.provider.identity(renewed['access_token']) != self.account:
                raise Forbidden('Provider account mismatch after refresh')
            self._store(tenant, connection, generation, attempt, renewed, expires, row['owner'])
        except InvalidGrant:
            self._failure(tenant, connection, generation, attempt, 'reauth_required')
            raise OAuthError('Provider refresh rejected; authorize again') from None
        except (OAuthError, Forbidden) as error:
            # Same reasoning as `complete`: a reply we read and refused keeps its
            # own reason instead of becoming an unknown outcome, while the caller
            # still sees this method's contract.
            self._failure(tenant, connection, generation, attempt, 'uncertain')
            self._cleanup_issued(tenant, connection, generation, body, tokens)
            if isinstance(error, Forbidden):
                raise OAuthError(str(error)) from None
            raise
        except Exception:
            self._failure(tenant, connection, generation, attempt, 'uncertain')
            self._cleanup_issued(tenant, connection, generation, body, tokens)
            raise OAuthError('Refresh outcome unavailable; authorize again') from None
        return AccessGrant(renewed['access_token'], generation, expires, self.account)

    def fence(self, tenant, connection, agent, required, grant):
        with self.e.read() as db:
            row = self._agent(db, tenant, connection, agent, required)
            if row['status'] != 'active' or row['generation'] != grant.generation or row['expires'] <= self.e.clock():
                raise Forbidden('OAuth grant no longer valid')

    def _queue_revoke(self, db, tenant, connection, generation, token):
        bounded(token, 16000)
        if not token.isascii() or any(c.isspace() for c in token):
            raise OAuthError('Invalid revocation credential')
        rid = secrets.token_hex(16)
        context = self._context(tenant, connection, generation, 'revoke:'+rid)
        envelope = self.vault.seal({'refresh_token': token}, context)
        db.execute('INSERT INTO p_oauth_revocations VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (rid, tenant, connection, generation, self.config_hash, envelope, 'pending', '', 0, self.e.clock()))
        return rid

    def _cleanup_issued(self, tenant, connection, generation, body, old=None):
        if not isinstance(body, dict): return
        token = body.get('refresh_token', (old or {}).get('refresh_token'))
        try:
            with self.e.tx() as db:
                rid = self._queue_revoke(db, tenant, connection, generation, token)
            self._drain_revoke(tenant, rid)
        except Exception:
            # A provider could issue tokens while the encryption service itself
            # is failing. Do not hide this irrecoverable remote-cleanup gap.
            with self.e.tx() as db:
                self.e.audit(db, tenant, '', 'oauth.cleanup_unavailable', 'oauth', {'connection': connection})

    def _drain_revoke(self, tenant, rid):
        attempt = secrets.token_hex(16)
        with self.e.tx() as db:
            row = db.execute('SELECT * FROM p_oauth_revocations WHERE tenant=? AND id=?', (tenant, rid)).fetchone()
            if not row: raise NotFound('Revocation not found')
            if row['status'] == 'confirmed': return 'confirmed'
            if row['config_hash'] != self.config_hash: raise Forbidden('Revocation requires original provider configuration')
            if row['status'] == 'inflight' and row['started']+120 > self.e.clock():
                raise Conflict('Revocation already in flight')
            context = self._context(tenant, row['connection'], row['generation'], 'revoke:'+rid)
            token = self.vault.open(row['envelope'], context)['refresh_token']
            db.execute("UPDATE p_oauth_revocations SET status='inflight',attempt=?,started=?,updated=? WHERE id=?",
                       (attempt, self.e.clock(), self.e.clock(), rid))
        try:
            self.provider.revoke(token); status = 'confirmed'
        except Exception:
            status = 'uncertain'
        with self.e.tx() as db:
            changed = db.execute('UPDATE p_oauth_revocations SET status=?,envelope=?,attempt=?,updated=? WHERE id=? AND attempt=?',
                       (status, '' if status == 'confirmed' else row['envelope'], '', self.e.clock(), rid, attempt))
            if changed.rowcount:
                self.e.audit(db, tenant, '', 'oauth.remote_revoke', 'oauth', {'connection': row['connection'], 'id': rid, 'status': status})
        return status

    def retry_revocations(self, tenant, connection, actor):
        with self.e.read() as db:
            self._owner(db, tenant, actor, active=False)
            rows = db.execute("SELECT id FROM p_oauth_revocations WHERE tenant=? AND connection=? AND status!='confirmed' ORDER BY updated LIMIT 20",
                              (tenant, connection)).fetchall()
        return [{'id': row['id'], 'status': self._drain_revoke(tenant, row['id'])} for row in rows]

    def revoke(self, tenant, connection, actor, remote=True):
        if type(remote) is not bool: raise ValueError('Remote revoke flag must be boolean')
        rid = None; unavailable = False
        with self.e.tx() as db:
            self._owner(db, tenant, actor, active=False)
            row = self._row(db, tenant, connection)
            if row['status'] == 'revoked':
                return {'local_revoked': True, 'remote_status': 'see_revocation_outbox'}
            if remote and row['envelope']:
                try:
                    self._configured(row)
                    tokens = self.vault.open(row['envelope'], self._context(tenant, connection, row['generation']))
                    rid = self._queue_revoke(db, tenant, connection, row['generation'], tokens['refresh_token'])
                except Exception:
                    unavailable = True
            db.execute("UPDATE p_oauth_connections SET status='revoked',generation=generation+1,envelope='',attempt='',expires=0,updated=? WHERE tenant=? AND id=?",
                       (self.e.clock(), tenant, connection))
            db.execute('DELETE FROM p_oauth_states WHERE tenant=? AND connection=?', (tenant, connection))
            self.e.audit(db, tenant, '', 'oauth.revoked', actor, {'connection': connection, 'remote_requested': remote, 'remote_unavailable': unavailable})
        status = 'not_requested' if not remote else ('unavailable' if unavailable else 'no_credential')
        if rid:
            try: status = self._drain_revoke(tenant, rid)
            except Exception: status = 'uncertain'
        return {'local_revoked': True, 'remote_status': status}

    def recover_stale(self, tenant, connection, actor):
        # Never steal a refresh lease: the provider may already have rotated it.
        with self.e.tx() as db:
            self._owner(db, tenant, actor, active=False)
            row = self._row(db, tenant, connection)
            if row['status'] not in {'refreshing', 'exchanging'} or row['started']+120 > self.e.clock():
                raise Conflict('No stale OAuth attempt')
            db.execute("UPDATE p_oauth_connections SET status='uncertain',generation=generation+1,envelope='',attempt='',updated=? WHERE tenant=? AND id=?",
                       (self.e.clock(), tenant, connection))
            self.e.audit(db, tenant, '', 'oauth.stale_fenced', actor, {'connection': connection})
        return {'status': 'uncertain', 'reauthorization_required': True}

    def rewrap(self, tenant, connection, actor):
        with self.e.tx() as db:
            self._owner(db, tenant, actor)
            row = self._row(db, tenant, connection); self._configured(row)
            if row['status'] != 'active': raise Conflict('Only idle active credentials may be rewrapped')
            envelope = self.vault.rewrap(row['envelope'], self._context(tenant, connection, row['generation']))
            db.execute('UPDATE p_oauth_connections SET envelope=?,updated=? WHERE tenant=? AND id=?',
                       (envelope, self.e.clock(), tenant, connection))
            self.e.audit(db, tenant, '', 'oauth.rewrapped', actor, {'connection': connection, 'kid': self.vault.active})

    def describe(self, tenant, connection):
        with self.e.read() as db:
            row = self._row(db, tenant, connection)
            return {k: row[k] for k in ('id', 'provider', 'account', 'generation', 'status', 'expires', 'updated')}
