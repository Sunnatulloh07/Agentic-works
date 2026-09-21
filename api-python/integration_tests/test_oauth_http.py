"""Dependency-backed HTTP tests. Never counted as passed by syntax checks."""
import os
os.environ.setdefault('ENV','test')
os.environ.setdefault('ALLOW_INSECURE_DEV','true')
import secrets
from urllib.parse import parse_qs,urlsplit
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import identity_store as identity, oauth_api
from app.auth import issue_token
from app.storage import reset,tx
from platform_runtime.oauth import OAuthManager
from platform_runtime.secret_vault import SecretVault
from runtime_tests.test_oauth import FakeProvider


@pytest.fixture
def oauth_http(tmp_path,monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY','true');monkeypatch.setenv('APP_DB',str(tmp_path/'db'))
    reset()
    user=identity.register_user('owner@example.invalid','fixture password 123','Owner')
    identity.create_workspace(user['id'],'demo-retail','Demo')
    raw,session=identity.create_session(user['id'],'demo-retail')
    headers={'Authorization':'Bearer '+issue_token('demo-retail',subject=user['id'],role='owner',session_id=session['session_id'])}
    provider=FakeProvider();manager=OAuthManager(oauth_api.engine(),SecretVault({'test':secrets.token_bytes(32)},'test'),provider)
    monkeypatch.setattr(oauth_api,'service',lambda t,c:manager)
    monkeypatch.setattr(oauth_api,'config',lambda t:{'oauth_connections':{'mail':{}}})
    with TestClient(app) as client:yield client,headers,provider,manager,user,raw
    reset()


def start(client,headers):
    r=client.post('/platform/demo-retail/oauth/mail/begin',headers=headers,json={})
    assert r.status_code==200
    return parse_qs(urlsplit(r.json()['authorization_url']).query)['state'][0]


def test_authenticated_oauth_callback_flow(oauth_http):
    client,headers,provider,manager,user,raw=oauth_http;state=start(client,headers)
    r=client.post('/platform/demo-retail/oauth/mail/complete',headers=headers,json={'state':state,'code':'fake-code'})
    assert r.status_code==200 and r.json()['status']=='active'
    assert 'fake-access' not in r.text and 'envelope' not in r.text
    assert r.headers['cache-control']=='no-store'


def test_anonymous_cannot_start(oauth_http):
    client,*_=oauth_http
    assert client.post('/platform/demo-retail/oauth/mail/begin',json={}).status_code==401


def test_viewer_cannot_start(oauth_http):
    client,headers,_,_,user,_=oauth_http
    with tx() as db:db.execute("UPDATE p_memberships SET role='viewer' WHERE user_id=?",(user['id'],))
    assert client.post('/platform/demo-retail/oauth/mail/begin',headers=headers,json={}).status_code==403


def test_oauth_state_bound_to_session_family(oauth_http):
    client,headers,_,_,user,_=oauth_http;state=start(client,headers)
    _,other=identity.create_session(user['id'],'demo-retail')
    other_headers={'Authorization':'Bearer '+issue_token('demo-retail',subject=user['id'],role='owner',session_id=other['session_id'])}
    r=client.post('/platform/demo-retail/oauth/mail/complete',headers=other_headers,json={'state':state,'code':'fake-code'})
    assert r.status_code==403


def test_session_refresh_preserves_callback_binding(oauth_http):
    client,headers,_,_,user,raw=oauth_http;state=start(client,headers)
    _,new=identity.rotate_session(raw)
    new_headers={'Authorization':'Bearer '+issue_token('demo-retail',subject=user['id'],role='owner',session_id=new['session_id'])}
    assert client.post('/platform/demo-retail/oauth/mail/complete',headers=new_headers,json={'state':state,'code':'fake-code'}).status_code==200


def test_session_revoke_during_provider_call_fences_store(oauth_http):
    client,headers,provider,manager,user,raw=oauth_http;state=start(client,headers)
    provider.before_exchange=lambda:identity.revoke_session(raw)
    r=client.post('/platform/demo-retail/oauth/mail/complete',headers=headers,json={'state':state,'code':'fake-code'})
    assert r.status_code==503 and manager.describe('demo-retail','mail')['status']!='active'


@pytest.mark.parametrize('extra',[{'url':'https://evil.invalid'},{'access_token':'injected'},{'actor':'other'}])
def test_callback_unknown_fields_denied(oauth_http,extra):
    client,headers,*_=oauth_http
    r=client.post('/platform/demo-retail/oauth/mail/complete',headers=headers,json={'state':'x','code':'y',**extra})
    assert r.status_code==422
