"""Requires FastAPI/Pydantic/HTTPX. ERP posting ledger read + owner reconcile over HTTP.

GET  /platform/{tenant}/erp/postings?status=...           owner/operator, LIMIT 100
POST /platform/{tenant}/erp/postings/{posting_id}/reconcile  owner only

The engine-level contract is runtime_tests/test_erp.py (reconcile_posting itself);
these tests pin the HTTP contract: role gating, tenant isolation, and the status
codes app.erp_api maps erp exceptions onto. Rows are seeded directly in
p_erp_postings, the same way runtime_tests/test_erp.py's own seed_posting does,
so a test is not coupled to submit()'s transport plumbing or a tenant pack.
"""
import os

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

import pytest
from fastapi.testclient import TestClient

from app import platform_api as api
from app.auth import issue_token
from app.main import app
from app.storage import reset

TENANT = 'demo-retail'
OTHER = 'marketing'


def url(path, tenant=TENANT):
    return f'/platform/{tenant}/erp/postings{path}'


def auth(role='owner', tenant=TENANT, subject='olga'):
    return {'Authorization': 'Bearer ' + issue_token(tenant, subject=subject, role=role)}


def seed(engine, posting_id, status, *, tenant=TENANT, number='INV-1', external_id=''):
    with engine.tx() as c:
        c.execute(
            '''INSERT INTO p_erp_postings(tenant,id,document,driver,kind,supplier,
               number,currency,total_minor,external_id,status,created,settled,claim_key)
               VALUES(?,?,'doc','onec_http','invoice','acme',?,'UZS',100,?,?,1000.0,NULL,?)''',
            (tenant, posting_id, number, external_id, status,
             f'onec_http|invoice|acme|{number}' if status != 'failed' else None))


@pytest.fixture
def http(tmp_path, monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'false')
    monkeypatch.setenv('PIPELINE_MODE', 'platform')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'app.db'))
    reset()
    engine = api.engine()
    with TestClient(app) as client:
        yield client, engine
    reset()


# --------------------------------------------------------------------- reconcile


def test_owner_reconciles_an_uncertain_posting_as_posted(http):
    client, engine = http
    seed(engine, 'p1', 'uncertain')
    response = client.post(url('/p1/reconcile'), headers=auth(),
                           json={'outcome': 'posted', 'evidence': 'found in 1C', 'external_id': 'DOC-9'})
    assert response.status_code == 200
    assert response.json() == {'id': 'p1', 'status': 'posted', 'external_id': 'DOC-9'}
    with engine.read() as c:
        row = c.execute('SELECT status,external_id FROM p_erp_postings WHERE tenant=? AND id=?',
                        (TENANT, 'p1')).fetchone()
    assert (row['status'], row['external_id']) == ('posted', 'DOC-9')


def test_owner_reconciles_an_unconfirmed_posting_as_failed(http):
    client, engine = http
    seed(engine, 'p1', 'unconfirmed')
    response = client.post(url('/p1/reconcile'), headers=auth(),
                           json={'outcome': 'failed', 'evidence': 'not present in ERP'})
    assert response.status_code == 200
    assert response.json()['status'] == 'failed'


@pytest.mark.parametrize('role', ['operator', 'viewer'])
def test_operator_and_viewer_are_refused(http, role):
    client, engine = http
    seed(engine, 'p1', 'uncertain')
    response = client.post(url('/p1/reconcile'), headers=auth(role),
                           json={'outcome': 'posted', 'evidence': 'seen', 'external_id': 'D'})
    assert response.status_code == 403
    with engine.read() as c:
        assert c.execute('SELECT status FROM p_erp_postings WHERE tenant=? AND id=?',
                         (TENANT, 'p1')).fetchone()['status'] == 'uncertain'


def test_another_tenants_posting_is_404(http):
    client, engine = http
    seed(engine, 'p1', 'uncertain', tenant=OTHER)
    response = client.post(url('/p1/reconcile'), headers=auth(tenant=TENANT),
                           json={'outcome': 'failed', 'evidence': 'seen'})
    assert response.status_code == 404
    with engine.read() as c:
        assert c.execute('SELECT status FROM p_erp_postings WHERE tenant=? AND id=?',
                         (OTHER, 'p1')).fetchone()['status'] == 'uncertain'


def test_unknown_posting_id_is_404(http):
    client, _ = http
    response = client.post(url('/does-not-exist/reconcile'), headers=auth(),
                           json={'outcome': 'failed', 'evidence': 'seen'})
    assert response.status_code == 404


def test_a_non_uncertain_posting_is_409(http):
    client, engine = http
    seed(engine, 'p1', 'posted', external_id='DOC-1')
    response = client.post(url('/p1/reconcile'), headers=auth(),
                           json={'outcome': 'failed', 'evidence': 'seen'})
    assert response.status_code == 409


def test_bad_outcome_is_422(http):
    client, engine = http
    seed(engine, 'p1', 'uncertain')
    response = client.post(url('/p1/reconcile'), headers=auth(),
                           json={'outcome': 'maybe', 'evidence': 'seen'})
    assert response.status_code == 422
    with engine.read() as c:
        assert c.execute('SELECT status FROM p_erp_postings WHERE tenant=? AND id=?',
                         (TENANT, 'p1')).fetchone()['status'] == 'uncertain'


@pytest.mark.parametrize('body', [
    {'outcome': 'failed', 'evidence': ''},
    {'outcome': 'failed', 'evidence': 'x' * 1001},
    {'outcome': 'posted', 'evidence': 'seen'},
    {'outcome': 'posted', 'evidence': 'seen', 'external_id': 'x' * 129},
    {'outcome': 'failed', 'evidence': 'seen', 'role': 'owner'},
])
def test_reconcile_rejects_malformed_bodies(http, body):
    client, engine = http
    seed(engine, 'p1', 'uncertain')
    response = client.post(url('/p1/reconcile'), headers=auth(), json=body)
    assert response.status_code == 422


def test_reconciling_the_same_posting_twice_is_409_on_the_second_call(http):
    client, engine = http
    seed(engine, 'p1', 'uncertain')
    first = client.post(url('/p1/reconcile'), headers=auth(),
                        json={'outcome': 'failed', 'evidence': 'first'})
    assert first.status_code == 200
    second = client.post(url('/p1/reconcile'), headers=auth(),
                         json={'outcome': 'failed', 'evidence': 'second'})
    assert second.status_code == 409


def test_anonymous_and_cross_tenant_tokens_are_refused(http):
    client, engine = http
    seed(engine, 'p1', 'uncertain')
    assert client.post(url('/p1/reconcile'),
                       json={'outcome': 'failed', 'evidence': 'x'}).status_code == 401
    assert client.post(url('/p1/reconcile'), headers=auth(tenant=OTHER),
                       json={'outcome': 'failed', 'evidence': 'x'}).status_code == 403


# --------------------------------------------------------------------------- list


def test_owner_and_operator_list_postings_by_status(http):
    client, engine = http
    seed(engine, 'p1', 'uncertain', number='INV-1')
    seed(engine, 'p2', 'posted', number='INV-2', external_id='DOC-2')
    for role in ('owner', 'operator'):
        response = client.get(url('?status=uncertain'), headers=auth(role))
        assert response.status_code == 200
        ids = [row['id'] for row in response.json()['postings']]
        assert ids == ['p1']


def test_viewer_cannot_list_postings(http):
    client, engine = http
    seed(engine, 'p1', 'uncertain')
    assert client.get(url('?status=uncertain'), headers=auth('viewer')).status_code == 403


def test_list_is_scoped_to_the_tenant(http):
    client, engine = http
    seed(engine, 'p1', 'uncertain', tenant=OTHER)
    response = client.get(url('?status=uncertain'), headers=auth(tenant=TENANT))
    assert response.status_code == 200
    assert response.json()['postings'] == []


def test_an_unknown_status_value_is_422(http):
    client, engine = http
    seed(engine, 'p1', 'uncertain')
    assert client.get(url('?status=archived'), headers=auth()).status_code == 422
    assert client.get(url(''), headers=auth()).status_code == 422


def test_the_list_is_bounded_to_one_hundred_rows(http):
    client, engine = http
    for index in range(105):
        seed(engine, f'q{index:03d}', 'uncertain', number=f'INV-{index}')
    response = client.get(url('?status=uncertain'), headers=auth())
    assert response.status_code == 200
    assert len(response.json()['postings']) == 100
