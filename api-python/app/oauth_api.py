"""Authenticated OAuth control plane. Provider tokens never cross this API.

Google redirects to the UI callback page; the original opener posts the code to
this API with its authenticated workspace session. No unauthenticated callback.
"""
import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from .platform_api import identity, engine, call, StrictRequest
from .identity_store import validate_session
from platform_runtime.google_oauth import configured_manager
from platform_runtime.oauth import OAuthError
from platform_runtime.secret_vault import VaultError
from platform_runtime.tools import config

router = APIRouter(prefix='/platform', tags=['oauth'])


def session_actor(request, tenant):
    who = identity(request, tenant, ('owner',))
    sid = who.get('sid')
    if not sid: raise HTTPException(403, 'Session-bound identity required')
    try: validate_session(sid, who['sub'], tenant)
    except Exception: raise HTTPException(401, 'Session unavailable') from None
    with engine().read() as db:
        row = db.execute('SELECT family_id FROM p_sessions WHERE id=? AND user_id=? AND workspace_id=? AND revoked_at=0 AND expires>?',
                         (sid, who['sub'], tenant, time.time())).fetchone()
    if not row: raise HTTPException(401, 'Session unavailable')
    return who['sub'], row['family_id']


def service(tenant, connection):
    try: return configured_manager(engine(), tenant, connection)
    except Exception: raise HTTPException(503, 'OAuth deployment configuration unavailable') from None


def invoke(fn, *args):
    try: return call(fn, *args)
    except (OAuthError, VaultError): raise HTTPException(503, 'OAuth provider operation unavailable') from None


class Complete(StrictRequest):
    state: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=4096)


class Revoke(StrictRequest):
    remote: bool = True


@router.get('/{tenant}/oauth/connections')
def connections(tenant: str, request: Request):
    session_actor(request, tenant)
    try:
        configs = config(tenant).get('oauth_connections', {})
        with engine().read() as db:
            rows = db.execute('SELECT id,provider,account,generation,status,expires,updated FROM p_oauth_connections WHERE tenant=?', (tenant,)).fetchall()
        saved = {r['id']:dict(r) for r in rows}
        for name in configs:
            if name not in saved: saved[name] = {'id':name,'provider':'google','status':'not_authorized'}
        return {'connections':list(saved.values())}
    except Exception: raise HTTPException(503,'OAuth configuration unavailable') from None


@router.post('/{tenant}/oauth/{connection}/begin')
def begin(tenant: str, connection: str, request: Request):
    actor, session = session_actor(request, tenant)
    return invoke(service(tenant,connection).begin, tenant, connection, actor, session)


@router.post('/{tenant}/oauth/{connection}/complete')
def complete(tenant: str, connection: str, req: Complete, request: Request):
    actor, session = session_actor(request, tenant)
    def guard(db):
        row=db.execute('SELECT 1 FROM p_sessions WHERE family_id=? AND user_id=? AND workspace_id=? AND revoked_at=0 AND expires>?',
                       (session,actor,tenant,time.time())).fetchone()
        if not row:
            from platform_runtime.engine import Forbidden
            raise Forbidden('OAuth initiating session revoked')
    return invoke(service(tenant,connection).complete, tenant, connection, actor, session, req.state, req.code, guard)


@router.post('/{tenant}/oauth/{connection}/revoke')
def revoke(tenant: str, connection: str, req: Revoke, request: Request):
    actor, _ = session_actor(request, tenant)
    if not req.remote:
        # Emergency local disconnect works even when provider config/key is gone.
        e = engine()
        with e.tx() as db:
            e.require_authority(db,tenant,'web',actor,('owner',))
            db.execute("UPDATE p_oauth_connections SET status='revoked',generation=generation+1,envelope='',attempt='',expires=0,updated=? WHERE tenant=? AND id=?",
                       (e.clock(),tenant,connection))
            db.execute('DELETE FROM p_oauth_states WHERE tenant=? AND connection=?',(tenant,connection))
            e.audit(db,tenant,'','oauth.emergency_local_revoke',actor,{'connection':connection,'remote_revoked':False})
        return {'local_revoked':True,'remote_status':'not_requested'}
    return invoke(service(tenant,connection).revoke, tenant, connection, actor, True)


@router.post('/{tenant}/oauth/{connection}/retry-revocations')
def retry_revocations(tenant: str, connection: str, request: Request):
    actor, _ = session_actor(request, tenant)
    return {'revocations':invoke(service(tenant,connection).retry_revocations,tenant,connection,actor)}


@router.post('/{tenant}/oauth/{connection}/recover-stale')
def recover(tenant: str, connection: str, request: Request):
    actor, _ = session_actor(request, tenant)
    return invoke(service(tenant,connection).recover_stale,tenant,connection,actor)


@router.post('/{tenant}/oauth/{connection}/rewrap')
def rewrap(tenant: str, connection: str, request: Request):
    actor, _ = session_actor(request, tenant)
    invoke(service(tenant,connection).rewrap,tenant,connection,actor)
    return {'ok':True}
