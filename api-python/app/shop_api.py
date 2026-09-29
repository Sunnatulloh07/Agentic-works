"""Shop-wide read surfaces for the operator dashboard.

New routes only (OCP): nothing here changes an existing platform route. The
product list lives at ``/products`` because ``/catalog`` already answers with
the agent/tool catalogue. Authentication and role checks are
``platform_api.identity`` -- the same function every platform route uses -- so
the role matrix below mirrors the routes these views sit next to:

* approvals, inbox messages, handoffs, conversations: owner/operator (as
  ``/inbox`` and step approval -- they carry customer text);
* products, orders: any member (as ``/catalog`` and ``/customers``);
* channels: owner/integrator (as ``/connections``).

Every query is tenant-scoped and bounded.
"""
import json
import os
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .packs import PackError, load_pack
from .platform_api import engine, identity

router = APIRouter(prefix='/platform', tags=['shop'])

MAX_SHOP_ROWS = 100
MAX_MESSAGE_CHARS = 1000
# The product list is capped by its own ceiling, not by MAX_SHOP_ROWS: a catalogue
# page and a message line answer different questions, and 100 products would be a
# truncated shop rather than a bounded one.
MAX_PRODUCTS = 1000
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
    return {'products': [p.model_dump() for p in pack.products[:MAX_PRODUCTS]]}


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


# ---- conversations (R5) ------------------------------------------------------
# Customer text, so owner/operator only, like /inbox/messages.

MAX_CONVERSATION_ID_CHARS = 256


@router.get('/{tenant}/handoffs')
def shop_handoffs(tenant: str, request: Request, status: Literal['open', 'resolved', 'all'] = 'open'):
    """What the bot passed to a human (conversation.handoff records), newest first.

    ``resolved`` is an operator's mark (POST .../resolve), kept beside the record; a
    later handoff on the same conversation is its own record and is open again.
    Default ``open``: the ones still waiting for a person."""
    from platform_runtime.conversation import HANDOFF_KIND
    identity(request, tenant, OPERATORS)
    where = {'open': ' AND x.handoff_id IS NULL', 'resolved': ' AND x.handoff_id IS NOT NULL', 'all': ''}[status]
    with engine().read() as c:
        rows = c.execute('SELECT r.id,r.body,r.created,x.actor resolved_by,x.created resolved_at '
                         'FROM p_records r LEFT JOIN p_handoff_resolutions x '
                         'ON x.tenant=r.tenant AND x.handoff_id=r.id '
                         'WHERE r.tenant=? AND r.kind=?' + where + ' ORDER BY r.created DESC,r.id DESC LIMIT ?',
                         (tenant, HANDOFF_KIND, MAX_SHOP_ROWS)).fetchall()
    out = []
    for r in rows:
        body = _json(r['body'], {})
        out.append({'id': r['id'], 'created': r['created'], 'text': _text(body.get('text'))[0],
                    **{k: str(body.get(k, ''))[:MAX_CONVERSATION_ID_CHARS]
                       for k in ('channel', 'event_key', 'conversation_id', 'reason', 'agent')},
                    'resolved': r['resolved_at'] is not None,
                    'resolved_by': str(r['resolved_by'] or '')[:MAX_CONVERSATION_ID_CHARS],
                    'resolved_at': r['resolved_at']})
    return {'handoffs': out, 'status': status}


class Resolution(BaseModel):
    note: str = Field('', max_length=500)


@router.post('/{tenant}/handoffs/{handoff_id}/resolve')
def shop_handoff_resolve(tenant: str, handoff_id: str, request: Request, req: Resolution | None = None):
    """A person handled this handoff. Idempotent; also closes older open ones of its chat."""
    from platform_runtime.conversation import resolve_handoff
    from platform_runtime.engine import Forbidden
    who = identity(request, tenant, OPERATORS)
    if not request.headers.get('Idempotency-Key', ''):
        raise HTTPException(422, 'Idempotency-Key header required')
    try:
        done = resolve_handoff(engine(), tenant, handoff_id, who['sub'], req.note if req else '')
    except Forbidden:
        raise HTTPException(403, 'Policy denied') from None
    if done is None:
        raise HTTPException(404, 'Handoff not found')
    return done


def _linked_name(c, tenant, channel, sender):
    """Customer 360 name for a sender the shop has linked on this channel, else ''.

    Only the channel identity decides; verified links win over unverified ones."""
    if not sender:
        return ''
    row = c.execute(
        '''SELECT p.display_name FROM p_channel_identities i
           JOIN p_customers p ON p.id=i.customer_id AND p.tenant=i.tenant
           WHERE i.tenant=? AND i.channel=? AND i.external_id=? AND p.status!='deleted'
           ORDER BY i.verified DESC,i.created LIMIT 1''', (tenant, channel, sender)).fetchone()
    return str(row['display_name'])[:MAX_CONVERSATION_ID_CHARS] if row else ''


def _sender_name(c, tenant, channel, conversation_id):
    """The name the customer gave the channel (Telegram profile, WhatsApp profile name).

    The latest turn whose inbound event carried one. Customer-chosen text: it is
    cleaned again here, shown as text, and never used to identify anyone."""
    from platform_runtime.display_name import clean_display_name
    row = c.execute(
        """SELECT COALESCE(NULLIF(json_extract(e.payload,'$.sender_name'),''),
                           json_extract(e.payload,'$.profile_name')) name
           FROM p_conversation_turns t JOIN p_events e
             ON e.tenant=t.tenant AND e.channel=t.channel AND e.event_key=t.event_key
           WHERE t.tenant=? AND t.channel=? AND t.conversation_id=?
             AND COALESCE(NULLIF(json_extract(e.payload,'$.sender_name'),''),
                          NULLIF(json_extract(e.payload,'$.profile_name'),'')) IS NOT NULL
           ORDER BY t.created DESC,t.rowid DESC LIMIT 1""", (tenant, channel, conversation_id)).fetchone()
    return clean_display_name(row['name']) if row else ''


@router.get('/{tenant}/conversations')
def shop_conversations(tenant: str, request: Request):
    """Latest line and latest turn of each conversation, most recent first."""
    from platform_runtime.conversation import takeover_state
    identity(request, tenant, OPERATORS)
    e = engine()
    with e.read() as c:
        now = e.clock()
        rows = c.execute(
            '''SELECT h.channel,h.conversation_id,h.role,h.text,h.created FROM p_conversation_history h
               JOIN (SELECT channel,conversation_id,MAX(seq) seq FROM p_conversation_history
                     WHERE tenant=? GROUP BY channel,conversation_id) m
                 ON m.channel=h.channel AND m.conversation_id=h.conversation_id AND m.seq=h.seq
               WHERE h.tenant=? ORDER BY h.created DESC,h.rowid DESC LIMIT ?''',
            (tenant, tenant, MAX_SHOP_ROWS)).fetchall()
        out = []
        for r in rows:
            turn = c.execute(
                'SELECT status,error,sender FROM p_conversation_turns WHERE tenant=? AND channel=? '
                'AND conversation_id=? ORDER BY created DESC,rowid DESC LIMIT 1',
                (tenant, r['channel'], r['conversation_id'])).fetchone()
            sender = turn['sender'] if turn else ''
            out.append({'channel': r['channel'], 'conversation_id': r['conversation_id'],
                        'last_role': r['role'], 'last_text': _text(r['text'])[0], 'last_at': r['created'],
                        'turn_status': turn['status'] if turn else '', 'turn_error': turn['error'] if turn else '',
                        'sender': sender, 'customer_name': _linked_name(c, tenant, r['channel'], sender),
                        'sender_name': _sender_name(c, tenant, r['channel'], r['conversation_id']),
                        'takeover': takeover_state(c, tenant, r['channel'], r['conversation_id'], now)})
    return {'conversations': out}


@router.get('/{tenant}/conversations/{channel}/{conversation_id}')
def shop_conversation(tenant: str, channel: str, conversation_id: str, request: Request):
    """One thread: history lines in order, its turns, and operator replies sent to it."""
    from platform_runtime.conversation import takeover_state
    from platform_runtime.engine import DIRECT_DESTINATION_FIELD, OPERATOR_CHANNEL
    from platform_runtime.operator_reply import OPERATOR_LINE_KIND
    identity(request, tenant, OPERATORS)
    if len(channel) > 32 or len(conversation_id) > MAX_CONVERSATION_ID_CHARS:
        raise HTTPException(404, 'Conversation not found')
    key = (tenant, channel, conversation_id)
    e = engine()
    with e.read() as c:
        takeover = takeover_state(c, *key, e.clock())
        sender_name = _sender_name(c, *key)
        history = [dict(r) for r in c.execute(
            'SELECT seq,role,text,created FROM p_conversation_history WHERE tenant=? AND channel=? '
            'AND conversation_id=? ORDER BY seq', key)]
        turns = [dict(r) for r in c.execute(
            '''SELECT * FROM (SELECT event_key,seq,status,error,task,reply,run_id,sender,created,updated
               FROM p_conversation_turns WHERE tenant=? AND channel=? AND conversation_id=?
               ORDER BY created DESC,rowid DESC LIMIT ?) ORDER BY created,seq''', (*key, MAX_SHOP_ROWS))]
        replies = []
        tool = channel + '.send'
        if tool in DIRECT_DESTINATION_FIELD:
            path = '$.' + DIRECT_DESTINATION_FIELD[tool]
            replies = [{'task_id': r['id'], 'status': r['status'], 'actor': r['actor'],
                        'created': r['created'], 'text': _text(_json(r['args'], {}).get('text'))[0]}
                       for r in c.execute(
                           '''SELECT t.id,t.status,t.actor,t.created,s.args FROM p_tasks t
                              JOIN p_steps s ON s.task=t.id AND s.tenant=t.tenant
                              WHERE t.tenant=? AND t.channel=? AND s.tool=? AND json_extract(s.args,?)=?
                              AND NOT EXISTS(SELECT 1 FROM p_records r WHERE r.tenant=t.tenant
                                             AND r.kind=? AND r.id=t.id)
                              ORDER BY t.created DESC LIMIT ?''',
                           (tenant, OPERATOR_CHANNEL, tool, path, conversation_id, OPERATOR_LINE_KIND,
                            MAX_SHOP_ROWS))]
            replies.reverse()
    if not history and not turns and not replies:
        raise HTTPException(404, 'Conversation not found')
    for line in history:
        line['text'] = _text(line['text'])[0]
    for turn in turns:
        turn['reply'] = _text(turn['reply'])[0]
    return {'channel': channel, 'conversation_id': conversation_id, 'history': history,
            'turns': turns, 'operator_replies': replies, 'takeover': takeover,
            'sender_name': sender_name}


@router.post('/{tenant}/conversations/{channel}/{conversation_id}/release')
def shop_conversation_release(tenant: str, channel: str, conversation_id: str, request: Request):
    """Hand an operator-held chat back to the bot before its takeover expires."""
    from platform_runtime.conversation import release_takeover, takeover_state
    from platform_runtime.engine import Forbidden
    who = identity(request, tenant, OPERATORS)
    if not request.headers.get('Idempotency-Key', ''):
        raise HTTPException(422, 'Idempotency-Key header required')
    e = engine()
    try:
        released = release_takeover(e, tenant, channel, conversation_id, who['sub'])
    except Forbidden:
        raise HTTPException(403, 'Policy denied') from None
    with e.read() as c:
        state = takeover_state(c, tenant, channel, conversation_id, e.clock())
    return {'released': released, 'takeover': state}


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
    from platform_runtime.tools import IntegrationNotConfigured, integration_status
    try:
        # Never resolves a credential: only names, and whether each is set.
        cfg, violations = integration_status(tenant)
    except IntegrationNotConfigured:
        # "Not configured" is a status to show; an unreadable file is an error.
        cfg, violations = {}, []
    except RuntimeError:
        raise HTTPException(503, 'Integration configuration unavailable')
    except (ValueError, OSError):
        raise HTTPException(503, 'Integration configuration unavailable')
    # One name the tenant may not use makes config() refuse its whole file, so no
    # channel is ready; each says which key, never a value, and a disallowed name
    # is not probed for presence.
    denied = {name for _, name, _ in violations}
    out = []
    for channel in CHANNELS:
        blocks = [(name, cfg.get(name)) for name in CHANNEL_BLOCKS[channel]]
        present = [(name, b) for name, b in blocks if isinstance(b, dict) and b]
        refs = []
        for name, block in present:
            for ref in _credential_refs(block, scoped=name == 'whatsapp_tokens'):
                if ref not in refs and ref not in denied:
                    refs.append(ref)
        creds = [{'env': ref, 'set': bool(os.environ.get(ref))} for ref in refs]
        paths = [path for path, _, _ in violations if path.split('.', 1)[0] in CHANNEL_BLOCKS[channel]]
        paths = paths or [path for path, _, _ in violations][:1]
        problem = ('misconfigured: credential name not allowed for this tenant ('
                   + ', '.join(p[:120] for p in paths[:5]) + ')') if paths else ''
        out.append({'channel': channel, 'configured': bool(present), 'credentials': creds,
                    'ready': bool(present) and bool(creds) and all(x['set'] for x in creds) and not violations,
                    'problem': problem})
    return {'channels': out}
