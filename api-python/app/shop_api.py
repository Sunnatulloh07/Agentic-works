"""Shop-wide read surfaces for the operator dashboard.

New routes only (OCP): nothing here changes an existing platform route. The
product list lives at ``/products`` because ``/catalog`` already answers with
the agent/tool catalogue. Authentication and role checks are
``platform_api.identity`` -- the same function every platform route uses -- so
the role matrix below mirrors the routes these views sit next to:

* approvals, inbox messages: owner/operator (as ``/inbox`` and step approval);
* products, orders: any member (as ``/catalog`` and ``/customers``);
* channels: owner/integrator (as ``/connections``).

Every query is tenant-scoped and bounded.
"""
import json
import os
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from .packs import PackError, load_pack
from .platform_api import engine, identity

router = APIRouter(prefix='/platform', tags=['shop'])

MAX_SHOP_ROWS = 100
MAX_MESSAGE_CHARS = 1000
CHANNELS = ('telegram', 'instagram', 'whatsapp')
# Integration blocks that belong to each channel in config(tenant).
CHANNEL_BLOCKS = {'telegram': ('telegram',), 'instagram': ('instagram',),
                  'whatsapp': ('whatsapp', 'whatsapp_webhook', 'whatsapp_tokens')}
ENV_NAME = re.compile(r'[A-Z][A-Z0-9_]*')

OPERATORS = ('owner', 'operator')
INTEGRATORS = ('owner', 'integrator')


def _json(raw, default):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _text(value):
    text = value if isinstance(value, str) else ''
    return text[:MAX_MESSAGE_CHARS], len(text) > MAX_MESSAGE_CHARS


@router.get('/{tenant}/approvals')
def shop_approvals(tenant: str, request: Request, status: Literal['pending'] = 'pending'):
    identity(request, tenant, OPERATORS)
    e = engine()
    with e.read() as c:
        rows = c.execute(
            '''SELECT a.step step_id, a.expires, s.task task_id, s.tool, s.args, s.risk,
                      s.status step_status, t.agent, t.channel, t.event_key, t.created
               FROM p_approvals a JOIN p_steps s ON s.id=a.step AND s.tenant=a.tenant
               JOIN p_tasks t ON t.id=s.task AND t.tenant=s.tenant
               WHERE a.tenant=? AND a.status='pending' AND a.expires>?
                 AND s.status IN ('queued','waiting_approval')
               ORDER BY t.created DESC LIMIT ?''',
            (tenant, e.clock(), MAX_SHOP_ROWS)).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item['args'] = _json(item['args'], {})
        out.append(item)
    return {'approvals': out, 'status': status}


@router.get('/{tenant}/inbox/messages')
def shop_inbox_messages(tenant: str, request: Request):
    identity(request, tenant, OPERATORS)
    with engine().read() as c:
        rows = c.execute(
            'SELECT channel,event_key,status,result,error,payload FROM p_events '
            'WHERE tenant=? ORDER BY rowid DESC LIMIT ?', (tenant, MAX_SHOP_ROWS)).fetchall()
    out = []
    for r in rows:
        payload = _json(r['payload'], {})
        text, truncated = _text(payload.get('text'))
        out.append({'channel': r['channel'], 'event_key': r['event_key'], 'status': r['status'],
                    'result': _json(r['result'], {}), 'error': r['error'],
                    'text': text, 'truncated': truncated,
                    'sender': str(payload.get('sender', ''))[:256],
                    'conversation_id': str(payload.get('conversation_id', ''))[:256]})
    return {'events': out}


@router.get('/{tenant}/products')
def shop_products(tenant: str, request: Request):
    identity(request, tenant)
    try:
        pack = load_pack(tenant)
    except PackError:
        raise HTTPException(404, 'Pack not found')
    return {'products': [p.model_dump() for p in pack.products[:1000]]}


@router.get('/{tenant}/orders')
def shop_orders(tenant: str, request: Request):
    identity(request, tenant)
    with engine().read() as c:
        records = c.execute(
            "SELECT id,body,created FROM p_records WHERE tenant=? AND kind='order' "
            'ORDER BY created DESC LIMIT ?', (tenant, MAX_SHOP_ROWS)).fetchall()
        customer = c.execute(
            '''SELECT o.id,o.external_id,o.status,o.currency,o.total_minor,o.created,o.updated,
                      o.customer_id,p.display_name customer_name
               FROM p_customer_orders o LEFT JOIN p_customers p
                 ON p.id=o.customer_id AND p.tenant=o.tenant
               WHERE o.tenant=? ORDER BY o.updated DESC LIMIT ?''',
            (tenant, MAX_SHOP_ROWS)).fetchall()
    orders = []
    for r in records:
        body = _json(r['body'], {})
        orders.append({'id': r['id'], 'created': r['created'],
                       'title': str(body.get('title', ''))[:200],
                       'body': _text(body.get('body'))[0]})
    return {'orders': orders, 'customer_orders': [dict(r) for r in customer]}


def _credential_refs(block, scoped=False):
    """Env variable NAMES referenced by a config block; never their values."""
    refs = []
    if isinstance(block, dict):
        for key, value in block.items():
            if isinstance(value, str) and (scoped or str(key).endswith('_env')):
                if ENV_NAME.fullmatch(value):
                    refs.append(value)
            elif isinstance(value, dict):
                refs += _credential_refs(value, scoped)
    return refs


@router.get('/{tenant}/channels')
def shop_channels(tenant: str, request: Request):
    identity(request, tenant, INTEGRATORS)
    from platform_runtime.tools import IntegrationNotConfigured, config
    try:
        cfg = config(tenant)
    except IntegrationNotConfigured:
        # "Not configured" is a status to show; an unreadable file is an error.
        cfg = {}
    except RuntimeError:
        raise HTTPException(503, 'Integration configuration unavailable')
    except (ValueError, OSError):
        raise HTTPException(503, 'Integration configuration unavailable')
    out = []
    for channel in CHANNELS:
        blocks = [(name, cfg.get(name)) for name in CHANNEL_BLOCKS[channel]]
        present = [(name, b) for name, b in blocks if isinstance(b, dict) and b]
        refs = []
        for name, block in present:
            for ref in _credential_refs(block, scoped=name == 'whatsapp_tokens'):
                if ref not in refs:
                    refs.append(ref)
        creds = [{'env': ref, 'set': bool(os.environ.get(ref))} for ref in refs]
        out.append({'channel': channel, 'configured': bool(present), 'credentials': creds,
                    'ready': bool(present) and bool(creds) and all(x['set'] for x in creds)})
    return {'channels': out}
