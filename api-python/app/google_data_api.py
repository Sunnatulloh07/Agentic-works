"""Owner/session-bound metadata sync and positive-evidence reconciliation APIs."""
from typing import Literal
import time
from fastapi import APIRouter,HTTPException,Request
from pydantic import Field
from .platform_api import engine,call,StrictRequest
from .oauth_api import session_actor
from platform_runtime.engine import Forbidden,digest
from platform_runtime.oauth import OAuthError
from platform_runtime.secret_vault import VaultError
from platform_runtime.tools import config
from platform_runtime.google_adapters import configured_resources
from platform_runtime.google_oauth import configured_manager
from platform_runtime.google_sync import GoogleSync,stream_id
from platform_runtime.google_reconcile import GoogleReconciler

router=APIRouter(prefix='/platform',tags=['google-data'])


class SyncRequest(StrictRequest):
    agent:str=Field(min_length=1,max_length=128)
    kind:Literal['gmail','drive','calendar']
    calendar:str=Field(default='',max_length=256)


class SyncRecords(SyncRequest):
    after:str=Field(default='',max_length=256)
    limit:int=Field(default=50,ge=1,le=100)


class SyncReset(SyncRequest):
    confirm_reset:Literal[True]


def invoke(fn,*args,**kwargs):
    try:return call(fn,*args,**kwargs)
    except (OAuthError,VaultError):raise HTTPException(503,'Google operation unavailable; no automatic write retry') from None


def context(request,tenant,connection):
    actor,family=session_actor(request,tenant)
    e=engine()
    try:
        manager=configured_manager(e,tenant,connection)
        resources=configured_resources(tenant,connection)
        def binding():
            return digest({'oauth':config(tenant).get('oauth_connections',{}).get(connection),
                           'resources':configured_resources(tenant,connection)})
        original=binding()
    except Exception:raise HTTPException(503,'Google deployment configuration unavailable') from None
    def guard(db):
        if binding()!=original:raise Forbidden('Google configuration changed during request')
        row=db.execute('SELECT 1 FROM p_sessions WHERE family_id=? AND user_id=? AND workspace_id=? AND revoked_at=0 AND expires>?',
                       (family,actor,tenant,time.time())).fetchone()
        if not row:raise Forbidden('Initiating session revoked')
    return e,manager,resources,actor,guard


@router.post('/{tenant}/google/{connection}/sync/page')
def sync_page(tenant:str,connection:str,req:SyncRequest,request:Request):
    e,m,r,actor,guard=context(request,tenant,connection)
    return invoke(GoogleSync(e,m,r).page,tenant,connection,req.agent,actor,req.kind,req.calendar,session_guard=guard)


@router.post('/{tenant}/google/{connection}/sync/records')
def sync_records(tenant:str,connection:str,req:SyncRecords,request:Request):
    e,m,r,actor,guard=context(request,tenant,connection);s=GoogleSync(e,m,r)
    def read():
        stream=stream_id(req.kind,req.calendar)
        with e.read() as db:before=s._guard(db,tenant,connection,req.agent,actor,req.kind,req.calendar,session_guard=guard)
        rows=s.store.records(tenant,connection,stream,actor,req.limit,after=req.after)
        with e.read() as db:after=s._guard(db,tenant,connection,req.agent,actor,req.kind,req.calendar,session_guard=guard)
        if before['generation']!=after['generation']:raise Forbidden('Sync credential changed during read')
        return {'records':rows,'next_after':rows[-1]['id'] if len(rows)==req.limit else None,'untrusted_content':True,
                'coverage':'metadata_only','version_kind':'local_observation_sequence'}
    return invoke(read)


@router.post('/{tenant}/google/{connection}/sync/reset')
def sync_reset(tenant:str,connection:str,req:SyncReset,request:Request):
    e,m,r,actor,guard=context(request,tenant,connection);s=GoogleSync(e,m,r)
    def reset():
        stream=stream_id(req.kind,req.calendar)
        def checked(db):s._guard(db,tenant,connection,req.agent,actor,req.kind,req.calendar,session_guard=guard)
        s.store.reset(tenant,connection,stream,actor,guard=checked)
        return {'reset':True,'stream':stream,'provider_records_deleted':False}
    return invoke(reset)


@router.post('/{tenant}/google/steps/{step}/reconcile')
def reconcile_step(tenant:str,step:str,request:Request):
    session_actor(request,tenant)
    e=engine()
    with e.read() as db:row=db.execute('SELECT connection FROM p_google_dispatch WHERE tenant=? AND step=?',(tenant,step)).fetchone()
    if not row:raise HTTPException(404,'Dispatch not found')
    e,m,r,actor,guard=context(request,tenant,row['connection'])
    return invoke(GoogleReconciler(e,m,r).reconcile,tenant,step,actor,session_guard=guard)
