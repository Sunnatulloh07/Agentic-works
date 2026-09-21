"""Real FastAPI/Pydantic tests, run in dependency-backed CI, not stubbed locally."""
import os
os.environ.setdefault('ENV','test')
os.environ.setdefault('ALLOW_INSECURE_DEV','true')
from fastapi.testclient import TestClient
import pytest
from app.main import app
from app import identity_store as s
from app.storage import reset,db
from app.auth import issue_token

PW='identity test password'


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('APP_DB',str(tmp_path/'http.db'));monkeypatch.setenv('IDENTITY_DIRECTORY','true')
    monkeypatch.setenv('PIPELINE_MODE','platform');reset()
    with TestClient(app) as c:yield c
    reset()


def setup_user():
    me=s.register_user('owner@example.com',PW,'Owner');s.create_workspace(me['id'],'demo-retail','Retail');return me


def login(c,wid=None):
    body={'email':'owner@example.com','password':PW}
    if wid:body['workspace_id']=wid
    return c.post('/identity/login',json=body)


def headers(tokens):return {'Authorization':'Bearer '+tokens['access_token']}


def test_login_route_not_retired_and_no_store(client):
    setup_user();r=login(client)
    assert r.status_code==200 and r.headers['cache-control']=='no-store'
    assert r.json()['tokens']['expires_in']<=900


def test_account_session_can_select_workspace(client):
    setup_user();token=login(client).json()['tokens']
    assert client.get('/identity/workspaces',headers=headers(token)).status_code==200
    assert client.get('/platform/demo-retail/tasks',headers=headers(token)).status_code==403
    selected=client.post('/identity/workspaces/demo-retail/select',headers=headers(token),json={})
    assert selected.status_code==200
    assert client.get('/platform/demo-retail/tasks',headers=headers(selected.json())).status_code==200


def test_bootstrap_default_closed(client):
    r=client.post('/identity/bootstrap',json={'email':'x@example.com','password':PW,'display_name':'X','workspace_id':'demo-retail','workspace_name':'Retail'})
    assert r.status_code==403
    assert db().execute('SELECT count(*) FROM p_users').fetchone()[0]==0


def test_authenticated_provisioning_cannot_set_plan(client,monkeypatch):
    setup_user();token=login(client).json()['tokens']
    r=client.post('/identity/workspaces',headers=headers(token),json={'user_id':'x','workspace_id':'demo-retail','name':'Hijack'})
    assert r.status_code==403
    r=client.post('/identity/workspaces',json={'user_id':'x','workspace_id':'other','name':'X','plan':'enterprise'})
    assert r.status_code==422


def test_public_registration_requires_invitation(client):
    r=client.post('/identity/register',json={'email':'x@example.com','password':PW,'display_name':'X'})
    assert r.status_code==422


def test_invited_user_can_register_and_use_account_session(client):
    me=setup_user();_,raw=s.create_invitation(me['id'],'demo-retail','member@example.com','viewer')
    r=client.post('/identity/register',json={'email':'member@example.com','password':PW,'display_name':'M','invitation_token':raw})
    assert r.status_code==200
    assert client.get('/identity/workspaces',headers=headers(r.json()['tokens'])).status_code==200


def test_logout_revokes_access_on_modern_and_legacy_routes(client):
    setup_user();token=login(client,'demo-retail').json()['tokens']
    assert client.post('/identity/logout',json={'refresh_token':token['refresh_token']}).status_code==200
    for path in ['/me','/platform/demo-retail/tasks','/identity/sessions']:
        assert client.get(path,headers=headers(token)).status_code==401


def test_refresh_replay_revokes_replacement(client):
    setup_user();token=login(client,'demo-retail').json()['tokens']
    new=client.post('/identity/refresh',json={'refresh_token':token['refresh_token']})
    assert new.status_code==200
    assert client.post('/identity/refresh',json={'refresh_token':token['refresh_token']}).status_code==401
    assert client.get('/platform/demo-retail/tasks',headers=headers(new.json())).status_code==401


def test_directory_rejects_old_unsigned_membership_cache(client):
    setup_user();old=issue_token('demo-retail',subject='operator',role='owner')
    assert client.get('/platform/demo-retail/tasks',headers={'Authorization':'Bearer '+old}).status_code==401
    assert client.post('/auth/token',json={'tenant_id':'demo-retail','role':'owner'}).status_code==410


def test_validation_never_echoes_password_or_invite(client):
    secret='sensitive-test-value'
    r=client.post('/identity/register',json={'email':True,'password':secret,'display_name':[], 'invitation_token':secret})
    assert r.status_code==422
    assert secret not in r.text and '"input"' not in r.text


def test_rate_limited_login_returns_429(client):
    setup_user()
    for i in range(20):assert login(client).status_code==200
    assert login(client).status_code==429


def test_cross_tenant_and_frozen_customer_mutation(client):
    me=setup_user();token=login(client,'demo-retail').json()['tokens']
    assert client.get('/platform/other/customers',headers=headers(token)).status_code==403
    assert client.post('/platform/demo-retail/freeze',headers=headers(token),json={'stopped':True}).status_code==200
    assert client.post('/platform/demo-retail/customers',headers=headers(token),json={'display_name':'A'}).status_code==403
