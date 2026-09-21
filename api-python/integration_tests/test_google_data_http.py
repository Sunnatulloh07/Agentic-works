"""Requires FastAPI/Pydantic/pytest. NOT_RUN in the constrained Computer.

These tests must be run in dependency-backed CI; source inspection is not a pass.
"""
import os
os.environ.setdefault('ENV','test')
os.environ.setdefault('ALLOW_INSECURE_DEV','true')
import secrets
from urllib.parse import parse_qs,urlsplit
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import google_data_api as api,identity_store as identity
from app.auth import issue_token
from app.storage import reset,tx
from platform_runtime.engine import Forbidden
from platform_runtime.oauth import OAuthManager
from platform_runtime.secret_vault import SecretVault
from platform_runtime.google_adapters import SCOPES
from platform_runtime.google_sync import GoogleSync
from runtime_tests.test_oauth import FakeProvider


@pytest.fixture
def google_http(tmp_path,monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY','true');monkeypatch.setenv('APP_DB',str(tmp_path/'db'));reset()
    user=identity.register_user('sync-owner@example.invalid','fixture password 123','Owner')
    identity.create_workspace(user['id'],'demo-retail','Demo')
    raw,session=identity.create_session(user['id'],'demo-retail')
    headers={'Authorization':'Bearer '+issue_token('demo-retail',subject=user['id'],role='owner',session_id=session['session_id'])}
    e=api.engine();e.policy=lambda t,a:{'tools':list(SCOPES),'allowed_connections':['google'],'ladder':'autonomous'}
    provider=FakeProvider();provider.scopes=set(SCOPES.values())|provider.scopes
    manager=OAuthManager(e,SecretVault({'test':secrets.token_bytes(32)},'test'),provider)
    state=parse_qs(urlsplit(manager.begin('demo-retail','google',user['id'],'test-family')['authorization_url']).query)['state'][0]
    manager.complete('demo-retail','google',user['id'],'test-family',state,'code')
    resources={'recipient_emails':[],'calendar_ids':['primary']}
    monkeypatch.setattr(api,'engine',lambda:e)
    monkeypatch.setattr(api,'configured_manager',lambda *args:manager)
    monkeypatch.setattr(api,'configured_resources',lambda *args:resources)
    monkeypatch.setattr(api,'config',lambda t:{'oauth_connections':{'google':{'configured':True}}})
    calls=[];hooks=[];responses=[]
    def transport(method,url,token,body):
        calls.append((method,url))
        if hooks:hooks.pop(0)()
        return responses.pop(0) if responses else {'historyId':'100'}
    monkeypatch.setattr(api,'GoogleSync',lambda e,m,r:GoogleSync(e,m,r,transport))
    with TestClient(app) as client:yield client,headers,e,raw,resources,calls,hooks,responses,user
    reset()


URL='/platform/demo-retail/google/google/sync/'
BODY={'agent':'ops','kind':'gmail','calendar':''}


def test_authenticated_metadata_sync(google_http):
    client,headers,_,_,_,calls,*_=google_http
    response=client.post(URL+'page',headers=headers,json=BODY)
    assert response.status_code==200 and response.json()['phase']=='snapshot'
    assert response.headers['cache-control']=='no-store'
    assert 'fake-access' not in response.text and 'anchor' not in response.text
    assert len(calls)==1


def test_anonymous_sync_is_denied(google_http):
    client,*_=google_http
    assert client.post(URL+'page',json=BODY).status_code==401


def test_role_removed_blocks_read(google_http):
    client,headers,_,_,_,calls,_,_,user=google_http
    with tx() as db:db.execute("UPDATE p_memberships SET role='viewer' WHERE user_id=?",(user['id'],))
    assert client.post(URL+'page',headers=headers,json=BODY).status_code==403
    assert not calls


@pytest.mark.parametrize('extra',[{'url':'https://evil.invalid'},{'cursor':'injected'},{'actor':'other'},{'access_token':'secret'}])
def test_no_client_control_of_authority_or_cursor(google_http,extra):
    client,headers,*_=google_http
    assert client.post(URL+'page',headers=headers,json={**BODY,**extra}).status_code==422


def test_reset_requires_explicit_confirmation(google_http):
    client,headers,*_=google_http
    for extra in ({},{'confirm_reset':False}):
        assert client.post(URL+'reset',headers=headers,json={**BODY,**extra}).status_code==422


def test_session_revoked_during_sync_cannot_commit(google_http):
    client,headers,e,raw,_,calls,hooks,*_=google_http
    hooks.append(lambda:identity.revoke_session(raw))
    assert client.post(URL+'page',headers=headers,json=BODY).status_code==403
    with e.read() as db:assert db.execute('SELECT cursor FROM p_sync_streams').fetchone()[0]==''


def test_reset_does_not_delete_google_records(google_http):
    client,headers,_,_,_,calls,*_=google_http
    assert client.post(URL+'page',headers=headers,json=BODY).status_code==200
    n=len(calls)
    r=client.post(URL+'reset',headers=headers,json={**BODY,'confirm_reset':True})
    assert r.status_code==200 and not r.json()['provider_records_deleted'] and len(calls)==n


def test_config_change_mid_request_is_fenced(google_http):
    client,headers,e,_,resources,_,hooks,*_=google_http
    hooks.append(lambda:resources.update(calendar_ids=['other']))
    assert client.post(URL+'page',headers=headers,json=BODY).status_code==403
    with e.read() as db:assert db.execute('SELECT cursor FROM p_sync_streams').fetchone()[0]==''
