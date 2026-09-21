"""CRM Gateway: tool dispatch, plan verification and tool registration."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional
from ..engine import Conflict, Forbidden, encode
from ..tools import Tool, obj, string, config
from .crm_contract import (
    CRM_DRIVERS,
    IMPLEMENTED_CRM_DRIVERS,
    clean_text,
    find_contacts_by_mode,
    find_leads_by_mode,
    normalize_phone,
    normalize_email,
    parse_lead_request,
    parse_deal_request,
    parse_call_record,
    parse_chat_message,
    parse_followup_request,
    plan_crm_fingerprint,
    validate_crm_config,
)
from .bitrix24_adapter import Bitrix24Adapter
from .kommo_adapter import KommoAdapter
from .onec_adapter import OneCAdapter
from .custom_http_adapter import CustomHTTPAdapter

# Only drivers with an executable adapter are reachable from a tool. A driver
# declared in CRM_DRIVERS without an entry here fails closed instead of being
# routed to a guess.
ADAPTERS = {
    'bitrix24': Bitrix24Adapter,
    'amocrm': KommoAdapter,
    'kommo': KommoAdapter,
    'onec': OneCAdapter,
    'custom_webhook': CustomHTTPAdapter,
}


def get_adapter(raw_config: dict, transport: Optional[Callable] = None) -> Any:
    driver = raw_config.get('driver')
    if driver not in ADAPTERS:
        if driver in CRM_DRIVERS:
            raise Forbidden(f'CRM driver {driver} has no executable adapter yet')
        raise Forbidden(f'Unsupported CRM driver: {driver}')
    cls = ADAPTERS[driver]
    return cls(raw_config, transport=transport) if transport else cls(raw_config)


def _check_agent_allowed(engine, tenant: str, agent: str, tool_name: str, connection: str):
    """The strict reading of ``allowed_connections``, matching every other module.

    An empty (or absent) allowlist permits **nothing**. The lenient
    ``if allowed_conns and connection not in allowed_conns`` form made one policy value
    mean "all forbidden" in ``engine.py``, ``connectors.py``, ``database/gateway.py``,
    ``google_adapters.py``, ``oauth.py``, ``business_graph.py`` and ``sheets.py``, and
    "all permitted" here -- the same disagreement ``sheets._authorize`` was fixed for.

    It matters more here than anywhere else, because this is the ONLY place a CRM
    connection is checked at all. The engine's own allowlist check covers
    ``{'connectors.read', 'database.read', 'database.plan_write', 'database.write'}``
    and no ``crm.*`` tool is in that set, so the lenient form was not a redundant second
    opinion -- it was the single gate, and it stood open. Measured: with
    ``allowed_connections: []`` an agent reached the adapter and issued the read.
    """
    policy = engine.policy(tenant, agent)
    if tool_name not in policy.get('tools', []):
        raise Forbidden(f'Tool {tool_name} not permitted for agent {agent}')
    allowed_conns = policy.get('allowed_connections') or []
    if connection not in allowed_conns:
        raise Forbidden(f'Connection {connection} not permitted for agent {agent}')


def tool_crm_lead_search(engine, tenant: str, agent: str, args: dict, step: str) -> dict:
    conn_name = clean_text(args.get('connection'), 'connection', 128)
    _check_agent_allowed(engine, tenant, agent, 'crm.lead.search', conn_name)
    
    tenant_cfg = config(tenant)
    raw_conn = tenant_cfg.get('connections', {}).get(conn_name)
    validate_crm_config(raw_conn, tenant=tenant, agent=agent, capability='read')
    adapter = get_adapter(raw_conn)
    
    phone = args.get('phone')
    email = args.get('email')
    query = args.get('query')
    limit = int(args.get('limit', 20))
    
    leads = find_leads_by_mode(adapter, raw_conn['driver'],
                               query=query, phone=phone, email=email, limit=limit)
    return {'connection': conn_name, 'leads': leads, 'count': len(leads)}


def tool_crm_lead_plan(engine, tenant: str, agent: str, args: dict, step: str) -> dict:
    conn_name = clean_text(args.get('connection'), 'connection', 128)
    _check_agent_allowed(engine, tenant, agent, 'crm.lead.plan', conn_name)
    
    tenant_cfg = config(tenant)
    raw_conn = tenant_cfg.get('connections', {}).get(conn_name)
    validate_crm_config(raw_conn, tenant=tenant, agent=agent, capability='plan_write')
    
    request = parse_lead_request(args.get('request', {}))
    fingerprint = plan_crm_fingerprint(tenant, agent, conn_name, raw_conn, request)
    return {
        'connection': conn_name,
        'driver': raw_conn['driver'],
        'request': request,
        'plan_fingerprint': fingerprint,
    }


def tool_crm_lead_create(engine, tenant: str, agent: str, args: dict, step: str) -> dict:
    conn_name = clean_text(args.get('connection'), 'connection', 128)
    _check_agent_allowed(engine, tenant, agent, 'crm.lead.create', conn_name)
    
    tenant_cfg = config(tenant)
    raw_conn = tenant_cfg.get('connections', {}).get(conn_name)
    validate_crm_config(raw_conn, tenant=tenant, agent=agent, capability='execute_write')
    
    request = parse_lead_request(args.get('request', {}))
    expected_fp = plan_crm_fingerprint(tenant, agent, conn_name, raw_conn, request)
    given_fp = args.get('plan_fingerprint')
    if not given_fp or given_fp != expected_fp:
        raise Conflict('CRM lead plan changed or fingerprint mismatch; re-plan and re-approve')
        
    adapter = get_adapter(raw_conn)
    result = adapter.create_lead(request)
    return {
        'connection': conn_name,
        'result': result,
        'idempotency_reference': step,
    }


def tool_crm_deal_create(engine, tenant: str, agent: str, args: dict, step: str) -> dict:
    conn_name = clean_text(args.get('connection'), 'connection', 128)
    _check_agent_allowed(engine, tenant, agent, 'crm.deal.create', conn_name)
    
    tenant_cfg = config(tenant)
    raw_conn = tenant_cfg.get('connections', {}).get(conn_name)
    validate_crm_config(raw_conn, tenant=tenant, agent=agent, capability='execute_write')
    
    request = parse_deal_request(args.get('request', {}))
    expected_fp = plan_crm_fingerprint(tenant, agent, conn_name, raw_conn, request)
    given_fp = args.get('plan_fingerprint')
    if not given_fp or given_fp != expected_fp:
        raise Conflict('CRM deal plan changed or fingerprint mismatch; re-plan and re-approve')
        
    adapter = get_adapter(raw_conn)
    result = adapter.create_deal(request)
    return {
        'connection': conn_name,
        'result': result,
        'idempotency_reference': step,
    }


def tool_crm_contact_lookup(engine, tenant: str, agent: str, args: dict, step: str) -> dict:
    conn_name = clean_text(args.get('connection'), 'connection', 128)
    _check_agent_allowed(engine, tenant, agent, 'crm.contact.lookup', conn_name)
    
    tenant_cfg = config(tenant)
    raw_conn = tenant_cfg.get('connections', {}).get(conn_name)
    validate_crm_config(raw_conn, tenant=tenant, agent=agent, capability='read')
    
    adapter = get_adapter(raw_conn)
    phone = args.get('phone')
    email = args.get('email')
    query = args.get('query')
    limit = int(args.get('limit', 20))
    
    contacts = find_contacts_by_mode(adapter, raw_conn['driver'],
                                     query=query, phone=phone, email=email, limit=limit)
    return {'connection': conn_name, 'contacts': contacts, 'count': len(contacts)}


def tool_crm_attach_call(engine, tenant: str, agent: str, args: dict, step: str) -> dict:
    conn_name = clean_text(args.get('connection'), 'connection', 128)
    _check_agent_allowed(engine, tenant, agent, 'crm.timeline.attach_call', conn_name)
    
    tenant_cfg = config(tenant)
    raw_conn = tenant_cfg.get('connections', {}).get(conn_name)
    validate_crm_config(raw_conn, tenant=tenant, agent=agent, capability='execute_write')
    
    call_record = parse_call_record(args.get('call', {}))
    adapter = get_adapter(raw_conn)
    result = adapter.attach_call_record(call_record['lead_id'], call_record)
    return {'connection': conn_name, 'result': result}


def tool_crm_attach_message(engine, tenant: str, agent: str, args: dict, step: str) -> dict:
    conn_name = clean_text(args.get('connection'), 'connection', 128)
    _check_agent_allowed(engine, tenant, agent, 'crm.timeline.attach_message', conn_name)
    
    tenant_cfg = config(tenant)
    raw_conn = tenant_cfg.get('connections', {}).get(conn_name)
    validate_crm_config(raw_conn, tenant=tenant, agent=agent, capability='execute_write')
    
    msg = parse_chat_message(args.get('message', {}))
    adapter = get_adapter(raw_conn)
    result = adapter.attach_message(msg['lead_id'], msg)
    return {'connection': conn_name, 'result': result}


def tool_crm_followup(engine, tenant: str, agent: str, args: dict, step: str) -> dict:
    conn_name = clean_text(args.get('connection'), 'connection', 128)
    _check_agent_allowed(engine, tenant, agent, 'crm.lead.followup', conn_name)
    
    tenant_cfg = config(tenant)
    raw_conn = tenant_cfg.get('connections', {}).get(conn_name)
    validate_crm_config(raw_conn, tenant=tenant, agent=agent, capability='execute_write')
    
    followup = parse_followup_request(args.get('followup', {}))
    with engine.tx() as db:
        engine.audit(db, tenant, step, 'crm.lead.followup_scheduled', agent, followup)
    return {'connection': conn_name, 'status': 'scheduled', 'followup': followup}


def tool_crm_lead_stalled(engine, tenant: str, agent: str, args: dict, step: str) -> dict:
    """Read-only stalled-lead feed for the autonomous re-engagement loop.

    Covers the two scenarios from the CRM ecosystem design: a lead nobody has
    answered and a lead whose follow-up is already due. No write happens here; the
    loop turns a returned lead into an ordinary approval-gated step.
    """
    conn_name = clean_text(args.get('connection'), 'connection', 128)
    _check_agent_allowed(engine, tenant, agent, 'crm.lead.stalled', conn_name)

    tenant_cfg = config(tenant)
    raw_conn = tenant_cfg.get('connections', {}).get(conn_name)
    validate_crm_config(raw_conn, tenant=tenant, agent=agent, capability='read')

    adapter = get_adapter(raw_conn)
    if not hasattr(adapter, 'find_stalled_leads'):
        raise Forbidden(f'Driver {raw_conn["driver"]} declares no stalled-lead feed')

    inactive_minutes = int(args.get('inactive_minutes', 120))
    limit = int(args.get('limit', 20))
    try:
        leads = adapter.find_stalled_leads(inactive_minutes=inactive_minutes, limit=limit)
    except Forbidden:
        raise
    except Exception as error:
        # A provider read failure must not look like "there are no stalled leads":
        # an empty result would silently retire the follow-up cycle.
        raise Conflict(f'Stalled-lead feed unavailable: {type(error).__name__}')
    return {
        'connection': conn_name,
        'driver': raw_conn['driver'],
        'inactive_minutes': inactive_minutes,
        'leads': leads,
        'count': len(leads),
    }


def register_crm_tools(registry) -> None:
    lead_request_schema = obj({
        'title': string(256),
        'name': string(128),
        'phone': string(32),
        'email': string(256),
        'price': {'type': 'integer', 'minimum': 0, 'maximum': 10**12},
        'currency': string(3),
        'source': string(64),
        'comments': string(4000),
    }, required=['title'])

    deal_request_schema = obj({
        'title': string(256),
        'price': {'type': 'integer', 'minimum': 0, 'maximum': 10**12},
        'currency': string(3),
        'stage': string(64),
        'contact_id': string(128),
        'lead_id': string(128),
    }, required=['title'])

    call_schema = obj({
        'lead_id': string(128),
        'audio_url': string(1000),
        'transcript': string(50000),
        'duration_seconds': {'type': 'integer', 'minimum': 0, 'maximum': 86400},
        'direction': {'type': 'string', 'enum': ['inbound', 'outbound']},
        'sentiment': {'type': 'string', 'enum': ['positive', 'neutral', 'negative', 'urgent']},
        'summary': string(2000),
    }, required=['lead_id', 'transcript'])

    message_schema = obj({
        'lead_id': string(128),
        'channel': {'type': 'string', 'enum': ['telegram', 'whatsapp', 'instagram', 'web', 'sms']},
        'direction': {'type': 'string', 'enum': ['inbound', 'outbound']},
        'text': string(4096),
        'external_id': string(128),
    }, required=['lead_id', 'text'])

    followup_schema = obj({
        'lead_id': string(128),
        'action': {'type': 'string', 'enum': ['message', 'call', 'escalate']},
        'channel': string(32),
        'reason': string(1000),
        'scheduled_at': {'type': 'integer', 'minimum': 0},
    }, required=['lead_id'])

    registry.add(Tool('crm.lead.search', 'read', obj({
        'connection': string(128),
        'query': string(256),
        'phone': string(32),
        'email': string(256),
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
    }, required=['connection']), tool_crm_lead_search, external=True))

    registry.add(Tool('crm.lead.plan', 'read', obj({
        'connection': string(128),
        'request': lead_request_schema,
    }, required=['connection', 'request']), tool_crm_lead_plan))

    registry.add(Tool('crm.lead.create', 'write', obj({
        'connection': string(128),
        'request': lead_request_schema,
        'plan_fingerprint': string(64),
    }, required=['connection', 'request', 'plan_fingerprint']), tool_crm_lead_create, external=True))

    registry.add(Tool('crm.deal.create', 'write', obj({
        'connection': string(128),
        'request': deal_request_schema,
        'plan_fingerprint': string(64),
    }, required=['connection', 'request', 'plan_fingerprint']), tool_crm_deal_create, external=True))

    registry.add(Tool('crm.contact.lookup', 'read', obj({
        'connection': string(128),
        'phone': string(32),
        'email': string(256),
        'query': string(256),
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
    }, required=['connection']), tool_crm_contact_lookup, external=True))

    registry.add(Tool('crm.timeline.attach_call', 'write', obj({
        'connection': string(128),
        'call': call_schema,
    }, required=['connection', 'call']), tool_crm_attach_call, external=True))

    registry.add(Tool('crm.timeline.attach_message', 'write', obj({
        'connection': string(128),
        'message': message_schema,
    }, required=['connection', 'message']), tool_crm_attach_message, external=True))

    registry.add(Tool('crm.lead.followup', 'write', obj({
        'connection': string(128),
        'followup': followup_schema,
    }, required=['connection', 'followup']), tool_crm_followup))

    registry.add(Tool('crm.lead.stalled', 'read', obj({
        'connection': string(128),
        'inactive_minutes': {'type': 'integer', 'minimum': 1, 'maximum': 20160},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
    }, required=['connection']), tool_crm_lead_stalled, external=True))



def describe_crm(connection: str, raw: dict) -> dict:
    driver = raw.get('driver')
    caps = raw.get('capabilities', ['discover', 'validate', 'read', 'plan_write', 'execute_write', 'reconcile'])
    implemented = driver in IMPLEMENTED_CRM_DRIVERS
    if raw.get('lifecycle') == 'revoked':
        status = 'revoked'
    elif implemented:
        # An adapter exists, but no live provider acceptance has run for this
        # tenant. The status must not read as a verified integration.
        status = 'configured_not_live_verified'
    else:
        status = 'adapter_required'
    return {
        'id': connection,
        'driver': driver,
        'contract_version': raw.get('contract_version', '1.0'),
        'lifecycle': raw.get('lifecycle', 'configured'),
        'capabilities': caps,
        'status': status,
        'mode': 'crm_typed_operations',
        'agent_ids': raw.get('agent_ids', []),
        'transport_implemented': implemented,
        'live_verified': False,
    }

