"""Dependency-backed API gates. Run separately from archived legacy contract tests."""
import os
os.environ["ALLOW_INSECURE_DEV"] = "true"
os.environ.setdefault('ENV','test')
os.environ['PIPELINE_MODE']='platform'
from fastapi.testclient import TestClient
from app.main import app
from app.auth import issue_token
from app.platform_api import engine
import pytest

@pytest.fixture(autouse=True)
def db(tmp_path,monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY','false')
    monkeypatch.setenv('APP_DB',str(tmp_path/'app.db'));monkeypatch.setenv('PIPELINE_MODE','platform')


def auth(tenant='demo-retail',role='owner'):
    return {'Authorization':'Bearer '+issue_token(tenant,subject='test-user',role=role)}


def test_no_anonymous_control_plane():
    with TestClient(app) as c:assert c.get('/platform/demo-retail/tasks').status_code==401

def test_cross_tenant_denied():
    with TestClient(app) as c:assert c.get('/platform/demo-retail/tasks',headers=auth('other')).status_code==403

def test_viewer_cannot_submit():
    with TestClient(app) as c:assert c.post('/platform/demo-retail/tasks',headers=auth(role='viewer'),json={'agent':'ops.assistant','key':'k','steps':[{'tool':'reports.summary','args':{}}]}).status_code==403

def test_create_run_read():
    with TestClient(app) as c:
        r=c.post('/platform/demo-retail/tasks',headers=auth(),json={'agent':'ops.assistant','key':'k','steps':[{'tool':'reports.summary','args':{}}]});assert r.status_code==200
        engine().tick('demo-retail')
        assert c.get('/platform/demo-retail/tasks/'+r.json()['task_id'],headers=auth()).json()['status']=='succeeded'

def test_legacy_runner_endpoint_retired():
    with TestClient(app) as c:assert c.post('/runner/exec',headers=auth(),json={}).status_code==410

def test_device_token_not_user():
    token=issue_token('demo-retail',subject='d',role='device',token_type='device',device_id='d',generation=1)
    with TestClient(app) as c:assert c.get('/platform/demo-retail/tasks',headers={'Authorization':'Bearer '+token}).status_code==403

def test_telegram_durable_acceptance():
    with TestClient(app) as c:
        payload={'update_id':1,'message':{'from':{'id':10},'chat':{'id':-123},'text':'/report'}}
        r=c.post('/webhooks/telegram',headers={'X-Telegram-Bot-Api-Secret-Token':'dev-webhook-secret'},json=payload)
        assert r.status_code==200;assert r.json()['status']=='accepted'
        assert c.post('/webhooks/telegram',headers={'X-Telegram-Bot-Api-Secret-Token':'dev-webhook-secret'},json=payload).json()['duplicate']


def test_verified_identity_role():
    with TestClient(app) as c:
        r=c.get('/platform/demo-retail/identity',headers=auth(role='viewer'))
        assert r.status_code==200
        assert r.json()['role']=='viewer'
        assert r.json()['tenant_id']=='demo-retail'

def test_freeze_rejects_new_task():
    with TestClient(app) as c:
        assert c.post('/platform/demo-retail/freeze',headers=auth(),json={'stopped':True}).status_code==200
        r=c.post('/platform/demo-retail/tasks',headers=auth(),json={'agent':'ops.assistant','key':'frozen','steps':[{'tool':'reports.summary','args':{}}]})
        assert r.status_code==403

def test_connector_metadata_owner_or_integrator_only():
    with TestClient(app) as c:
        for role in ('viewer','operator'):
            assert c.get('/platform/demo-retail/connections',headers=auth(role=role)).status_code==403

def test_anonymous_token_minting_closed_without_opt_in(monkeypatch):
    monkeypatch.delenv('ALLOW_INSECURE_DEV',raising=False)
    monkeypatch.delenv('ADMIN_TOKEN',raising=False)
    with TestClient(app) as c:
        assert c.post('/auth/token',json={'tenant_id':'demo-retail','role':'owner'}).status_code==410
