"""Shop-wide read surfaces for the operator UI (app/shop_api.py).

Every route is tenant-scoped and reuses platform_api.identity for the role
check, so these tests pin the role matrix as well as the payload shape.
"""
import json
import os
os.environ["ALLOW_INSECURE_DEV"] = "true"
os.environ.setdefault('ENV', 'test')
os.environ['PIPELINE_MODE'] = 'platform'
from fastapi.testclient import TestClient
from app.main import app
from app.auth import issue_token
from app.platform_api import engine
import pytest

T = 'demo-retail'


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'false')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'app.db'))
    monkeypatch.setenv('PIPELINE_MODE', 'platform')


def auth(tenant=T, role='owner'):
    return {'Authorization': 'Bearer ' + issue_token(tenant, subject='test-user', role=role)}


def telegram_draft(key='u1', text='Ha, kurtka bor — o‘lcham 5', inbound='Salom, qishki kurtka bormi?'):
    e = engine()
    e.accept_event(T, 'telegram', key, {'text': inbound, 'sender': '10', 'conversation_id': '-123'})
    # sales.order_taker (human_assisted): sales.responder is autonomous since the
    # conversation-turn pack change, so its reply to the origin needs no approval.
    tid = e.submit(T, 'telegram', key, 'sales.order_taker',
                   [{'tool': 'telegram.send', 'args': {'conversation_id': '-123', 'text': text}}], '10')
    return tid


# ---- approvals ------------------------------------------------------------

def test_approvals_lists_pending_draft_with_text():
    tid = telegram_draft()
    with TestClient(app) as c:
        r = c.get(f'/platform/{T}/approvals?status=pending', headers=auth(role='operator'))
    assert r.status_code == 200
    rows = r.json()['approvals']
    assert len(rows) == 1
    row = rows[0]
    assert row['task_id'] == tid
    assert row['agent'] == 'sales.order_taker'
    assert row['tool'] == 'telegram.send'
    assert row['args']['text'] == 'Ha, kurtka bor — o‘lcham 5'
    assert row['args']['conversation_id'] == '-123'
    assert row['channel'] == 'telegram'
    assert row['step_id'] and row['created']


def test_approvals_excludes_decided():
    tid = telegram_draft()
    step = engine().get(T, tid)['steps'][0]['id']
    with TestClient(app) as c:
        assert c.post(f'/platform/{T}/steps/{step}/approval', headers=auth(),
                      json={'decision': 'rejected'}).status_code == 200
        assert c.get(f'/platform/{T}/approvals', headers=auth()).json()['approvals'] == []


def test_approvals_role_and_tenant_gates():
    with TestClient(app) as c:
        assert c.get(f'/platform/{T}/approvals').status_code == 401
        assert c.get(f'/platform/{T}/approvals', headers=auth('other')).status_code == 403
        for role in ('viewer', 'integrator'):
            assert c.get(f'/platform/{T}/approvals', headers=auth(role=role)).status_code == 403
        assert c.get(f'/platform/{T}/approvals?status=approved', headers=auth()).status_code == 422


def test_approvals_are_tenant_isolated():
    telegram_draft()
    with TestClient(app) as c:
        r = c.get('/platform/other/approvals', headers=auth('other'))
    assert r.status_code == 200 and r.json()['approvals'] == []


# ---- inbox messages -------------------------------------------------------

def test_inbox_messages_include_customer_text():
    engine().accept_event(T, 'telegram', 'm1', {'text': 'Narxi qancha? Yo‘q bo‘lsa ayting', 'sender': '10',
                                                'conversation_id': '-123'})
    with TestClient(app) as c:
        r = c.get(f'/platform/{T}/inbox/messages', headers=auth(role='operator'))
    assert r.status_code == 200
    ev = r.json()['events'][0]
    assert ev['text'] == 'Narxi qancha? Yo‘q bo‘lsa ayting'
    assert ev['sender'] == '10' and ev['conversation_id'] == '-123'
    assert ev['channel'] == 'telegram' and ev['event_key'] == 'm1' and ev['status'] == 'pending'
    assert 'payload' not in ev


def test_inbox_messages_truncates_long_text():
    engine().accept_event(T, 'telegram', 'm2', {'text': 'a' * 3000, 'sender': '10', 'conversation_id': '-1'})
    with TestClient(app) as c:
        ev = c.get(f'/platform/{T}/inbox/messages', headers=auth()).json()['events'][0]
    assert len(ev['text']) <= 1001 and ev['truncated'] is True


def test_inbox_messages_owner_operator_only():
    with TestClient(app) as c:
        for role in ('viewer', 'integrator'):
            assert c.get(f'/platform/{T}/inbox/messages', headers=auth(role=role)).status_code == 403


def test_existing_inbox_unchanged():
    with TestClient(app) as c:
        r = c.get(f'/platform/{T}/inbox', headers=auth())
    assert r.status_code == 200 and set(r.json()) == {'events'}


# ---- products -------------------------------------------------------------

def test_products_from_pack_any_member():
    with TestClient(app) as c:
        r = c.get(f'/platform/{T}/products', headers=auth(role='viewer'))
    assert r.status_code == 200
    products = r.json()['products']
    first = next(p for p in products if p['id'] == 'KB001')
    assert first['name'] == 'Qizlar kurtkasi (qish)'
    assert first['price_uzs'] == 350000 and first['sizes'] == [3, 4, 5, 6]


def test_products_cross_tenant_denied_and_existing_catalog_unchanged():
    with TestClient(app) as c:
        assert c.get(f'/platform/{T}/products', headers=auth('other')).status_code == 403
        assert 'agents' in c.get(f'/platform/{T}/catalog', headers=auth()).json()


# ---- orders ---------------------------------------------------------------

def test_orders_lists_order_records_only():
    e = engine()
    with e.tx() as c:
        c.execute('INSERT INTO p_records VALUES(?,?,?,?,?)',
                  (T, 'order', 's1', json.dumps({'kind': 'order', 'title': 'KB001 x1', 'body': 'tel +998901112233'}), 10.0))
        c.execute('INSERT INTO p_records VALUES(?,?,?,?,?)',
                  (T, 'note', 's2', json.dumps({'kind': 'note', 'title': 'x', 'body': 'y'}), 11.0))
        c.execute('INSERT INTO p_records VALUES(?,?,?,?,?)',
                  ('other', 'order', 's3', json.dumps({'kind': 'order', 'title': 'leak', 'body': 'z'}), 12.0))
    with TestClient(app) as c:
        r = c.get(f'/platform/{T}/orders', headers=auth(role='viewer'))
    assert r.status_code == 200
    rows = r.json()['orders']
    assert [x['id'] for x in rows] == ['s1']
    assert rows[0]['title'] == 'KB001 x1' and rows[0]['body'] == 'tel +998901112233'
    assert 'customer_orders' in r.json()


def test_orders_include_customer_orders():
    with TestClient(app) as c:
        cust = c.post(f'/platform/{T}/customers', headers=auth(), json={'display_name': 'Go‘zal'}).json()['customer']
        assert c.post(f'/platform/{T}/customers/{cust["id"]}/orders', headers=auth(),
                      json={'external_id': 'ORD-1', 'total_minor': 350000}).status_code == 200
        rows = c.get(f'/platform/{T}/orders', headers=auth()).json()['customer_orders']
    assert rows[0]['external_id'] == 'ORD-1' and rows[0]['customer_name'] == 'Go‘zal'
    assert rows[0]['total_minor'] == 350000


def test_orders_requires_membership():
    with TestClient(app) as c:
        assert c.get(f'/platform/{T}/orders').status_code == 401
        assert c.get(f'/platform/{T}/orders', headers=auth('other')).status_code == 403


# ---- channels -------------------------------------------------------------

def test_channels_report_config_and_env_presence_never_values(tmp_path, monkeypatch):
    packs = tmp_path / 'packs'
    (packs / T).mkdir(parents=True)
    src = os.path.join(os.path.dirname(__file__), '..', '..', 'packs', T)
    for name in ('pack.yaml', 'products.yaml'):
        (packs / T / name).write_text(open(os.path.join(src, name), encoding='utf-8').read(), encoding='utf-8')
    import shutil
    shutil.copytree(os.path.join(src, 'prompts'), packs / T / 'prompts')
    (packs / T / 'integrations.yaml').write_text(
        'telegram:\n  token_env: SHOP_TEST_TG_TOKEN\ninstagram:\n  account_id: "1"\n  token_env: SHOP_TEST_IG_TOKEN\n',
        encoding='utf-8')
    monkeypatch.setenv('PACKS_DIR', str(packs))
    monkeypatch.setenv('SHOP_TEST_TG_TOKEN', '123:secret-value')
    monkeypatch.delenv('SHOP_TEST_IG_TOKEN', raising=False)
    with TestClient(app) as c:
        r = c.get(f'/platform/{T}/channels', headers=auth(role='integrator'))
    assert r.status_code == 200
    assert 'secret-value' not in r.text
    ch = {x['channel']: x for x in r.json()['channels']}
    assert set(ch) == {'telegram', 'instagram', 'whatsapp'}
    assert ch['telegram']['configured'] is True and ch['telegram']['ready'] is True
    assert ch['telegram']['credentials'] == [{'env': 'SHOP_TEST_TG_TOKEN', 'set': True}]
    assert ch['instagram']['configured'] is True and ch['instagram']['ready'] is False
    assert ch['instagram']['credentials'] == [{'env': 'SHOP_TEST_IG_TOKEN', 'set': False}]
    assert ch['whatsapp']['configured'] is False and ch['whatsapp']['ready'] is False


def test_channels_without_any_config_is_not_an_error(monkeypatch):
    monkeypatch.delenv('PLATFORM_INTEGRATIONS_FILE', raising=False)
    with TestClient(app) as c:
        r = c.get(f'/platform/{T}/channels', headers=auth())
    assert r.status_code == 200
    assert all(x['configured'] is False for x in r.json()['channels'])


def test_channels_owner_integrator_only():
    with TestClient(app) as c:
        for role in ('viewer', 'operator'):
            assert c.get(f'/platform/{T}/channels', headers=auth(role=role)).status_code == 403


def test_channels_unreadable_config_is_503_not_unconfigured(tmp_path, monkeypatch):
    packs = tmp_path / 'packs'
    (packs / T).mkdir(parents=True)
    (packs / T / 'integrations.yaml').write_text('- not a mapping\n', encoding='utf-8')
    monkeypatch.setenv('PACKS_DIR', str(packs))
    with TestClient(app) as c:
        assert c.get(f'/platform/{T}/channels', headers=auth()).status_code == 503
