"""Session-bound API. Public bootstrap and arbitrary pack ownership are prohibited."""
import hmac
import os
import time
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from .auth import issue_token, verify_claims
from . import identity_store as store

router=APIRouter(prefix='/identity',tags=['identity'])


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Credentials(StrictModel):
    email:str=Field(min_length=3,max_length=320)
    password:SecretStr=Field(min_length=1,max_length=256)


class RegisterRequest(Credentials):
    display_name:str=Field(min_length=1,max_length=256)
    invitation_token:SecretStr=Field(min_length=20,max_length=256)


class BootstrapRequest(Credentials):
    display_name:str=Field(min_length=1,max_length=256)
    workspace_id:str=Field(min_length=2,max_length=64)
    workspace_name:str=Field(min_length=1,max_length=256)


class LoginRequest(Credentials):
    workspace_id:str|None=Field(default=None,min_length=2,max_length=64)


class RefreshRequest(StrictModel):
    refresh_token:SecretStr=Field(min_length=20,max_length=256)


class WorkspaceRequest(StrictModel):
    user_id:str=Field(min_length=1,max_length=128)
    workspace_id:str=Field(min_length=2,max_length=64)
    name:str=Field(min_length=1,max_length=256)


class InviteRequest(StrictModel):
    email:str=Field(min_length=3,max_length=320)
    role:Literal['operator','integrator','viewer']
    ttl_seconds:int=Field(default=86400,ge=300,le=604800,strict=True)


class AcceptInviteRequest(StrictModel):
    token:SecretStr=Field(min_length=20,max_length=256)


def invoke(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except store.AuthRateLimited as exc:raise HTTPException(429,'Try again later',headers={'Retry-After':'900'}) from exc
    except store.AuthenticationError as exc:raise HTTPException(401,'Identity or permission invalid') from exc
    except store.IdentityError as exc:raise HTTPException(422,str(exc)) from exc


def rate(request,kind):
    # Trust proxy headers only at the ingress. Do not let client-supplied XFF bypass throttling.
    peer=request.client.host if request.client else 'unknown'
    invoke(store.throttle,'http-'+kind,peer,60)


def claims(request):
    auth=request.headers.get('Authorization','')
    if not auth.startswith('Bearer '):raise HTTPException(401,'Bearer required')
    try:who=verify_claims(auth[7:])
    except Exception as exc:raise HTTPException(401,'Invalid session') from exc
    if who.get('token_type') not in {'user','account'} or not who.get('sid'):raise HTTPException(401,'Identity session required')
    return who


def admin(request):
    required=os.getenv('ADMIN_TOKEN','')
    supplied=request.headers.get('X-Admin-Token','')
    if len(required)<32 or not supplied or not hmac.compare_digest(required.encode(),supplied.encode()):
        raise HTTPException(403,'Admin provisioning required')


def provisioned_pack(workspace_id):
    from .packs import load_pack, PackError
    if workspace_id=='template':raise HTTPException(422,'Template cannot be claimed')
    try:load_pack(workspace_id)
    except PackError as exc:raise HTTPException(422,'Provision and validate workspace pack first') from exc


def tokens(raw,m):
    ttl=min(900,int(m['session_expires']-time.time()))
    if ttl<1:raise HTTPException(401,'Session expired')
    return {'access_token':issue_token(m['workspace_id'],subject=m['user_id'],role=m['role'],
              token_type='account' if m['workspace_id']==store.ACCOUNT else 'user',session_id=m['session_id'],ttl_seconds=ttl),
            'refresh_token':raw,'token_type':'bearer','expires_in':ttl,
            'workspace_id':None if m['workspace_id']==store.ACCOUNT else m['workspace_id']}


@router.post('/bootstrap')
def bootstrap(req:BootstrapRequest,request:Request):
    admin(request)
    if os.getenv('IDENTITY_BOOTSTRAP_ENABLED','').lower()!='true':raise HTTPException(403,'Bootstrap disabled')
    rate(request,'bootstrap');provisioned_pack(req.workspace_id)
    me=invoke(store.bootstrap_identity,req.email,req.password.get_secret_value(),req.display_name,req.workspace_id,req.workspace_name)
    return {'user':me,'tokens':tokens(*invoke(store.create_session,me['id'],req.workspace_id))}


@router.post('/register')
def register(req:RegisterRequest,request:Request):
    rate(request,'register')
    me=invoke(store.register_with_invitation,req.email,req.password.get_secret_value(),req.display_name,req.invitation_token.get_secret_value())
    return {'user':me,'tokens':tokens(*invoke(store.create_session,me['id']))}


@router.post('/login')
def login(req:LoginRequest,request:Request):
    rate(request,'login');me=invoke(store.authenticate,req.email,req.password.get_secret_value())
    return {'user':me,'workspaces':invoke(store.list_workspaces,me['id']),
            'tokens':tokens(*invoke(store.create_session,me['id'],req.workspace_id or store.ACCOUNT))}


@router.post('/refresh')
def refresh(req:RefreshRequest,request:Request):
    rate(request,'refresh')
    return tokens(*invoke(store.rotate_session,req.refresh_token.get_secret_value()))


@router.post('/logout')
def logout(req:RefreshRequest,request:Request):
    rate(request,'logout');invoke(store.revoke_session,req.refresh_token.get_secret_value());return {'ok':True}


@router.post('/logout-all')
def logout_all(request:Request):
    invoke(store.revoke_all_sessions,claims(request)['sub']);return {'ok':True}


@router.get('/sessions')
def sessions(request:Request):
    return {'sessions':invoke(store.list_sessions,claims(request)['sub'])}


@router.get('/workspaces')
def workspaces(request:Request):
    return {'workspaces':invoke(store.list_workspaces,claims(request)['sub'])}


@router.post('/workspaces')
def workspace_create(req:WorkspaceRequest,request:Request):
    admin(request);provisioned_pack(req.workspace_id)
    return {'workspace':invoke(store.create_workspace,req.user_id,req.workspace_id,req.name)}


@router.post('/workspaces/{workspace_id}/select')
def select_workspace(workspace_id:str,request:Request):
    who=claims(request)
    return tokens(*invoke(store.create_session,who['sub'],workspace_id))


@router.post('/workspaces/{workspace_id}/invitations')
def invite(workspace_id:str,req:InviteRequest,request:Request):
    invitation,raw=invoke(store.create_invitation,claims(request)['sub'],workspace_id,req.email,req.role,req.ttl_seconds)
    return {'invitation':invitation,'token':raw}


@router.post('/invitations/accept')
def accept(req:AcceptInviteRequest,request:Request):
    return {'membership':invoke(store.accept_invitation,claims(request)['sub'],req.token.get_secret_value())}


@router.post('/workspaces/{workspace_id}/members/{user_id}/revoke')
def revoke(workspace_id:str,user_id:str,request:Request):
    invoke(store.revoke_membership,claims(request)['sub'],workspace_id,user_id);return {'ok':True}
