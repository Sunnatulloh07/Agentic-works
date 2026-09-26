"""Session-bound API. Public bootstrap and arbitrary pack ownership are prohibited."""
import hmac
import os
import time
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from .auth import MIN_TOKEN_TTL_SECONDS, SESSION_TOKEN_TTL_SECONDS, issue_token, verify_claims
from . import client_ip
from . import identity_store as store

# Declared bounds (§163).  Every ceiling this surface enforces is either read from the
# module that owns it -- the e-mail, password, token, name and workspace numbers belong
# to identity_store, the access-token lifetime and floor to auth -- or named here.  Until now the
# models restated seven of identity_store's numbers as their own literals: raising
# MAX_EMAIL_CHARS there left this file refusing at 320, and neither half noticed.  A
# restated bound is not a second pin, it is a second source.
#
# The two floors below are the shapes identity_store's regexes accept ("a@b", and a
# two-character slug).  They are named here and pinned to agree with those patterns by
# runtime_tests.test_identity_api_bounds rather than constructed from them, because §155
# deliberately declined to build a regex out of an f-string.  That is a test, not a
# construction, and it is the weaker of the two.
MIN_EMAIL_CHARS = 3
# identity_store mints ids as 'usr_' + uuid4().hex == 36 characters.  These are not
# that shape -- they bound what an admin may POST in WorkspaceRequest.user_id, which
# the store then has to look up.  128 is a sanity ceiling on a client-chosen string,
# not a claim about the store's own id format; nothing may read it as one.
MIN_USER_ID_CHARS = 1
MAX_USER_ID_CHARS = 128
# The per-client login budget.  store.throttle's third argument is the LIMIT, not the
# window: this bucket allows a fixed multiple of the per-account budget (THROTTLE_LIMIT)
# inside the same THROTTLE_WINDOW_SECONDS, so one account cannot lock out its neighbours
# behind a shared NAT, and changing address cannot unlock an account.  It was a bare
# positional 60 at the call site, which read as a window and no offline test could
# address.  Naming it was not enough: 60 here and the store's 20 are two independent
# literals, so 'three times' was a coincidence that held only while neither moved.  The
# relationship had no source in code at all -- the comment was the only place it lived.
# Deriving it makes that sentence true by construction, and keeps this bucket above the
# per-account one for any positive multiplier, which is what the shared-address guarantee
# actually requires.  A restated number would let the store tighten or loosen its own
# budget and silently invert the ordering this comment promises.
CLIENT_THROTTLE_MULTIPLIER = 3
CLIENT_THROTTLE_LIMIT = CLIENT_THROTTLE_MULTIPLIER * store.THROTTLE_LIMIT
# A configured admin token shorter than this is a configuration mistake rather than a
# secret: refuse to provision with it instead of accepting a guessable one.
MIN_ADMIN_TOKEN_CHARS = 32
# The Authorization scheme this surface accepts.  The prefix test and the slice that
# removed it were two sources for one fact ('Bearer ' and a bare 7): renaming the
# scheme would have left the slice cutting seven characters off something else.
BEARER_PREFIX = 'Bearer '

router=APIRouter(prefix='/identity',tags=['identity'])


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Credentials(StrictModel):
    email:str=Field(min_length=MIN_EMAIL_CHARS,max_length=store.MAX_EMAIL_CHARS)
    password:SecretStr=Field(min_length=store.MIN_CANDIDATE_PASSWORD_CHARS,
                             max_length=store.MAX_PASSWORD_CHARS)


class RegisterRequest(Credentials):
    display_name:str=Field(min_length=store.MIN_NAME_CHARS,max_length=store.MAX_NAME_CHARS)
    invitation_token:SecretStr=Field(min_length=store.MIN_TOKEN_CHARS,
                                     max_length=store.MAX_TOKEN_CHARS)


class BootstrapRequest(Credentials):
    display_name:str=Field(min_length=store.MIN_NAME_CHARS,max_length=store.MAX_NAME_CHARS)
    workspace_id:str=Field(min_length=store.MIN_WORKSPACE_ID_CHARS,
                           max_length=store.MAX_WORKSPACE_ID_CHARS)
    workspace_name:str=Field(min_length=store.MIN_NAME_CHARS,max_length=store.MAX_NAME_CHARS)


class LoginRequest(Credentials):
    workspace_id:str|None=Field(default=None,min_length=store.MIN_WORKSPACE_ID_CHARS,
                                max_length=store.MAX_WORKSPACE_ID_CHARS)


class RefreshRequest(StrictModel):
    refresh_token:SecretStr=Field(min_length=store.MIN_TOKEN_CHARS,
                                  max_length=store.MAX_TOKEN_CHARS)


class WorkspaceRequest(StrictModel):
    user_id:str=Field(min_length=MIN_USER_ID_CHARS,max_length=MAX_USER_ID_CHARS)
    workspace_id:str=Field(min_length=store.MIN_WORKSPACE_ID_CHARS,
                           max_length=store.MAX_WORKSPACE_ID_CHARS)
    name:str=Field(min_length=store.MIN_NAME_CHARS,max_length=store.MAX_NAME_CHARS)


class InviteRequest(StrictModel):
    email:str=Field(min_length=MIN_EMAIL_CHARS,max_length=store.MAX_EMAIL_CHARS)
    # The set identity_store.create_invitation accepts (ROLES minus 'owner').  A
    # Literal has to carry its members literally -- pydantic builds the OpenAPI enum
    # from them -- so this one cannot be constructed from the set the way the numbers
    # above are.  It is pinned to agree with that set by test instead, which is the
    # weaker instrument and is labelled as such in the test that does it.
    role:Literal['operator','integrator','viewer']
    ttl_seconds:int=Field(default=store.INVITATION_TTL_SECONDS,
                          ge=store.MIN_INVITATION_TTL_SECONDS,
                          le=store.MAX_INVITATION_TTL_SECONDS,strict=True)


class AcceptInviteRequest(StrictModel):
    token:SecretStr=Field(min_length=store.MIN_TOKEN_CHARS,max_length=store.MAX_TOKEN_CHARS)


def invoke(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except store.AuthRateLimited as exc:
        # The window the client has to wait out is the store's, not a second number: a
        # Retry-After that disagreed would send the client straight into another 429.
        raise HTTPException(429,'Try again later',
                            headers={'Retry-After':str(store.THROTTLE_WINDOW_SECONDS)}) from exc
    except store.AuthenticationError as exc:raise HTTPException(401,'Identity or permission invalid') from exc
    except store.IdentityError as exc:raise HTTPException(422,str(exc)) from exc


def rate(request,kind):
    # Per-client key: X-Forwarded-For is read only when the peer is a TRUSTED_PROXIES
    # entry, and then only its right-most untrusted hop (app/client_ip.py). The
    # per-account half of login throttling lives in identity_store.authenticate.
    invoke(store.throttle,'http-'+kind,client_ip.request_client(request),CLIENT_THROTTLE_LIMIT)


def claims(request):
    auth=request.headers.get('Authorization','')
    if not auth.startswith(BEARER_PREFIX):raise HTTPException(401,'Bearer required')
    try:who=verify_claims(auth[len(BEARER_PREFIX):])
    except Exception as exc:raise HTTPException(401,'Invalid session') from exc
    if who.get('token_type') not in {'user','account'} or not who.get('sid'):raise HTTPException(401,'Identity session required')
    return who


def admin(request):
    required=os.getenv('ADMIN_TOKEN','')
    supplied=request.headers.get('X-Admin-Token','')
    if len(required)<MIN_ADMIN_TOKEN_CHARS or not supplied or not hmac.compare_digest(required.encode(),supplied.encode()):
        raise HTTPException(403,'Admin provisioning required')


def provisioned_pack(workspace_id):
    from .packs import load_pack, PackError
    if workspace_id=='template':raise HTTPException(422,'Template cannot be claimed')
    try:load_pack(workspace_id)
    except PackError as exc:raise HTTPException(422,'Provision and validate workspace pack first') from exc


def tokens(raw,m):
    # auth's session lifetime and auth's floor, not second copies of either.
    #
    # The cap is a real fix: the literal 900 could drift from SESSION_TOKEN_TTL_SECONDS
    # and nothing would notice.  The floor is NOT a behaviour change -- MIN_TOKEN_TTL_SECONDS
    # is 1, so this reads exactly as `ttl<1` did.  Its value is prospective: if auth ever
    # raises that floor, this route follows instead of handing issue_token a lifetime it
    # rejects with a ValueError, which here would surface as a 500 rather than a 401.
    ttl=min(SESSION_TOKEN_TTL_SECONDS,int(m['session_expires']-time.time()))
    if ttl<MIN_TOKEN_TTL_SECONDS:raise HTTPException(401,'Session expired')
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
