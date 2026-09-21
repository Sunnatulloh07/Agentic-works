"""Universal CRM connector contract: typed schemas, status mapping and write plan fingerprinting.

Supports Bitrix24 and Kommo (amoCRM) with bounded request/response limits,
deterministic plan fingerprinting and zero blind write retries.
"""
from __future__ import annotations

import math
import re
import json
from dataclasses import dataclass
from typing import Any, Optional
from ..engine import Forbidden, Conflict, digest, encode

CRM_DRIVERS = frozenset({
    'bitrix24', 'amocrm', 'kommo',
    'modme', 'billz', 'moysklad',
    'retailcrm', 'yclients', 'jowi',
    'poster', 'custom_webhook',
    'onec',
})
CANONICAL_STATUSES = frozenset({'new', 'in_progress', 'won', 'lost'})

# Drivers with an executable typed adapter. Everything else in CRM_DRIVERS is a
# declarative placeholder: it stays visible to operators but must never be
# reported as a working integration until an adapter exists for it.
IMPLEMENTED_CRM_DRIVERS = frozenset({'bitrix24', 'amocrm', 'kommo', 'onec', 'custom_webhook'})

# Drivers whose find_leads/find_contacts take a free-text query rather than
# structured phone/email equality filters.
QUERY_SEARCH_DRIVERS = frozenset({'amocrm', 'kommo', 'onec', 'custom_webhook'})


BITRIX24_STATUS_MAP = {
    'NEW': 'new',
    'IN_PROCESS': 'in_progress',
    'PROCESSED': 'in_progress',
    'WON': 'won',
    'CONVERTED': 'won',
    'LOSE': 'lost',
    'JUNK': 'lost',
}

REVERSE_BITRIX24_STATUS_MAP = {
    'new': 'NEW',
    'in_progress': 'IN_PROCESS',
    'won': 'WON',
    'lost': 'LOSE',
}

KOMMO_STATUS_MAP = {
    'new': 'new',
    'in_progress': 'in_progress',
    'won': 'won',
    'lost': 'lost',
}

# 1C:CRM / 1C:УТ document states are Russian strings. Only the states this
# platform can prove it understands are mapped; anything else is left verbatim so
# a downstream write is never silently reinterpreted as a won deal.
ONEC_STATUS_MAP = {
    'Новый': 'new',
    'НеОбработан': 'new',
    'ВРаботе': 'in_progress',
    'Обрабатывается': 'in_progress',
    'ВзятВРаботу': 'in_progress',
    'Выигран': 'won',
    'Завершен': 'won',
    'УспешноЗакрыт': 'won',
    'Проигран': 'lost',
    'Отменен': 'lost',
    'Закрыт': 'lost',
}

REVERSE_ONEC_STATUS_MAP = {
    'new': 'Новый',
    'in_progress': 'ВРаботе',
    'won': 'Выигран',
    'lost': 'Проигран',
}

# Operations a CustomHTTPAdapter connection must declare to be usable, and the
# HTTP methods it may use. Anything outside this set is rejected at validation
# time, before any request is built.
CUSTOM_HTTP_OPERATIONS = frozenset({
    'find_leads', 'find_contacts', 'create_lead', 'create_deal',
    'attach_call_record', 'attach_message', 'find_stalled_leads',
})
CUSTOM_HTTP_METHODS = frozenset({'GET', 'POST', 'PUT', 'PATCH'})
# Placeholders a path template may contain. Values are always URL-encoded and
# length-bounded, so operator configuration cannot splice a destination.
CUSTOM_HTTP_PLACEHOLDERS = frozenset({
    'query', 'phone', 'email', 'lead_id', 'contact_id', 'limit', 'minutes', 'since',
})

PHONE_CHARS = re.compile(r'[^0-9+]')
# Response-map pointer segment. Unicode word characters plus hyphen so regional ERP
# field names (Cyrillic, accented Latin, hyphenated) remain addressable, while
# structural characters cannot appear.
POINTER_SEGMENT_RE = re.compile(r'[\w-]{1,64}')
EMAIL_RE = re.compile(r'^[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$')


def normalize_phone(value: Any) -> str:
    """Normalize phone to E.164-like international digits, e.g. +998901234567."""
    if not isinstance(value, str):
        raise ValueError('Phone must be a string')
    cleaned = PHONE_CHARS.sub('', value.strip())
    if not cleaned:
        raise ValueError('Empty phone number')
    if not cleaned.startswith('+'):
        if cleaned.startswith('998') and len(cleaned) == 12:
            cleaned = '+' + cleaned
        elif cleaned.startswith('8') and len(cleaned) == 11:
            cleaned = '+7' + cleaned[1:]
        elif len(cleaned) in (9, 10, 11, 12):
            cleaned = '+' + cleaned
    if not re.fullmatch(r'\+[0-9]{7,15}', cleaned):
        raise ValueError('Invalid international phone number format')
    return cleaned


def normalize_email(value: Any) -> str:
    """Normalize email address to lowercase bounded string."""
    if not isinstance(value, str) or not EMAIL_RE.fullmatch(value.strip()) or len(value) > 254:
        raise ValueError('Invalid email address')
    return value.strip().casefold()


def optional_phone(value: Any) -> str:
    """A provider phone, or ``''`` when the provider value is unusable.

    ``normalize_phone`` raises, which is right for operator configuration and for plan
    arguments -- a bad value there is a bug worth surfacing. It is wrong for a provider
    ROW: measured, a single lead carrying ``phone: 'n/a'`` made the whole ``find_leads``
    raise ``ValueError``, discarding every well-formed lead beside it. The same adapters
    filter non-dict rows in ``_rows`` and return a default from ``_map`` precisely to
    tolerate provider shape drift, so a raising normaliser contradicted their own design.
    The row is still returned, with its id and name, so it remains identifiable.
    """
    if not value:
        return ''
    try:
        return normalize_phone(value)
    except ValueError:
        return ''


def optional_email(value: Any) -> str:
    """A provider email, or ``''`` when the provider value is unusable.

    See ``optional_phone``. Measured: one row carrying ``email: 'not-an-email'`` made
    the whole ``find_leads`` raise.
    """
    if not value:
        return ''
    try:
        return normalize_email(value)
    except ValueError:
        return ''


def provider_price(value: Any) -> int:
    """A provider price as a whole number of currency units, or 0 when unusable.

    The adapters read this field with ``int(float(item.get('OPPORTUNITY') or 0))``
    (Bitrix24) and ``int(item.get('price') or 0)`` (Kommo). Both raise on a provider
    value that is not a number, so ONE malformed row aborted the whole search; and
    ``int(float(...))`` routed money through a binary float, which this package forbids
    elsewhere, and raised ``OverflowError`` -- not ``ValueError`` -- on ``'inf'``.

    Well-formed input behaves exactly as before, including the truncation of a
    fractional price; an unusable value is 0 rather than an exception. The integer
    branch is tested BEFORE any finiteness check, because ``math.isfinite`` converts to
    float and would itself raise ``OverflowError`` on an integer past the float range.
    """
    if value is None or value == '' or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if 0 <= value <= 10 ** 15 else 0
    if isinstance(value, float):
        if not math.isfinite(value) or not 0 <= value <= 10 ** 15:
            return 0
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not re.fullmatch(r'[0-9]{1,15}(\.[0-9]{0,6})?', text):
            return 0
        return int(text.partition('.')[0])
    return 0


def clean_text(value: Any, name: str, maximum: int = 256, *, required: bool = True) -> str:
    if value is None or value == '':
        if required:
            raise ValueError(f'{name} is required')
        return ''
    if not isinstance(value, str):
        raise ValueError(f'{name} must be a string')
    trimmed = value.strip()
    if required and not trimmed:
        raise ValueError(f'{name} must not be empty')
    if len(trimmed) > maximum or any(ord(c) < 32 and c not in '\t\n\r' for c in trimmed):
        raise ValueError(f'{name} exceeds bounds or contains control characters')
    return trimmed


def bounded_int(value: Any, name: str, low: int, high: int, *, default: int = 0) -> int:
    """A bounded integer from an untrusted value, refusing everything else.

    A bare ``int(value)`` is not a validation. It accepts ``True`` as 1, TRUNCATES a
    float (``int(1.9) == 1``), raises ``TypeError`` for ``None`` and ``OverflowError``
    for ``float('inf')`` -- two exception types outside this module's ``ValueError``
    contract, so a caller that catches ``ValueError`` turns them into a 500 -- and it
    has no upper bound at all, so a value the tool schema refuses still passes a direct
    call to the parser.

    ``high`` is not invented where the schema already states one. The registry's integer
    validator defaults to ``-10**12 .. 10**12`` when a schema omits the bound, so a field
    declared ``{'type': 'integer', 'minimum': 0}`` is in practice bounded at ``10**12``;
    that is the number used here.
    """
    if value is None or value == '':
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an integer')
    if not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def bounded_price(value: Any) -> int:
    """A price in whole currency units, ``0..10**12``.

    The check used to accept a float and truncate it -- ``bounded_price(1.9)`` returned
    ``1``, so a fractional price silently became a smaller one -- and to raise
    ``OverflowError`` on ``float('inf')``, which is not the ``ValueError`` callers catch.
    Its own message already said "must be an integer amount"; the check now agrees with
    the message, and the bound is the one the tool schema declares.
    """
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError('Price must be an integer amount')
    if not 0 <= value <= 10**12:
        raise ValueError('Price outside bounds (0..10^12)')
    return value


def clean_currency(value: Any) -> str:
    if not value:
        return 'UZS'
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z]{3}', value.strip().upper()):
        raise ValueError('Currency must be 3-letter uppercase code, e.g. UZS, USD, RUB')
    return value.strip().upper()


def validate_crm_config(raw: dict, *, tenant: str = '', agent: str = '', capability: str = 'read') -> dict:
    if not isinstance(raw, dict):
        raise Forbidden('CRM connection config must be an object')
    if raw.get('enabled', True) is not True:
        raise Forbidden('CRM connection is disabled')
    driver = raw.get('driver')
    if driver not in CRM_DRIVERS:
        raise ValueError(f'Unsupported CRM driver: {driver}')
    
    # Check agent authorization if agent specified
    if agent:
        allowed_agents = raw.get('agent_ids', [])
        if allowed_agents and agent not in allowed_agents:
            raise Forbidden('Agent not permitted to use this CRM connection')
            
    # Check capability
    declared_caps = set(raw.get('capabilities', ['discover', 'validate', 'read']))
    if capability and capability not in declared_caps:
        raise Forbidden(f'CRM connection does not advertise {capability} capability')
        
    # Check host allowlist
    host = raw.get('host', '')
    allowed_hosts = raw.get('allowed_hosts', [])
    if not host or not isinstance(host, str) or not isinstance(allowed_hosts, list):
        raise ValueError('Explicit CRM host and allowed_hosts required')
    if host not in allowed_hosts:
        raise Forbidden(f'CRM host {host} not in allowed_hosts policy')
        
    # Check credential reference. basic_auth_env covers 1C and other endpoints that
    # publish a service account rather than a token.
    cred_ref = (raw.get('credential_env') or raw.get('token_env')
                or raw.get('webhook_url_env') or raw.get('basic_auth_env'))
    if not isinstance(cred_ref, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', cred_ref):
        raise ValueError('Valid credential environment variable reference required')
        
    return raw


def parse_lead_request(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError('Lead request must be an object')
    title = clean_text(data.get('title'), 'title', 256, required=True)
    name = clean_text(data.get('name'), 'contact name', 128, required=False)
    phone = normalize_phone(data['phone']) if data.get('phone') else ''
    email = normalize_email(data['email']) if data.get('email') else ''
    if not phone and not email:
        raise ValueError('At least one of phone or email is required for a CRM lead')
    price = bounded_price(data.get('price', 0))
    currency = clean_currency(data.get('currency', 'UZS'))
    source = clean_text(data.get('source', 'agent_platform'), 'source', 64, required=False)
    comments = clean_text(data.get('comments', ''), 'comments', 4000, required=False)
    return {
        'title': title,
        'name': name,
        'phone': phone,
        'email': email,
        'price': price,
        'currency': currency,
        'source': source,
        'comments': comments,
    }


def parse_deal_request(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError('Deal request must be an object')
    title = clean_text(data.get('title'), 'deal title', 256, required=True)
    price = bounded_price(data.get('price', 0))
    currency = clean_currency(data.get('currency', 'UZS'))
    stage = clean_text(data.get('stage', 'new'), 'stage', 64, required=False)
    contact_id = clean_text(data.get('contact_id', ''), 'contact_id', 128, required=False)
    lead_id = clean_text(data.get('lead_id', ''), 'lead_id', 128, required=False)
    return {
        'title': title,
        'price': price,
        'currency': currency,
        'stage': stage,
        'contact_id': contact_id,
        'lead_id': lead_id,
    }


def safe_relative_path(value: Any, name: str = 'path', maximum: int = 512) -> str:
    """Validate an operator-declared relative request path template.

    This is the boundary that stops a custom HTTP connection from becoming a
    general-purpose request primitive. A path must be relative, single-segment
    clean and free of anything that could re-target the request: no scheme, no
    authority, no protocol-relative ``//``, no traversal, no backslash, no
    control characters and no whitespace. Placeholders are limited to the
    published allowlist; their values are encoded by ``build_path``.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f'{name} must be a non-empty string')
    path = value.strip()
    if not path.startswith('/'):
        raise ValueError(f'{name} must be an absolute path beginning with /')
    if len(path) > maximum:
        raise ValueError(f'{name} exceeds {maximum} characters')
    if path.startswith('//'):
        raise ValueError(f'{name} must not be protocol-relative (//)')
    if '\\' in path or '..' in path or '//' in path:
        raise ValueError(f'{name} contains traversal or separator characters')
    for char in path:
        if ord(char) < 32 or char.isspace() or char in '"\'':
            raise ValueError(f'{name} contains control, space or quote characters')
    if ':' in path:
        raise ValueError(f'{name} must not contain a scheme or port')
    if '#' in path:
        raise ValueError(f'{name} must not contain a fragment')
    for token in re.findall(r'\{([^}]*)\}', path):
        if token not in CUSTOM_HTTP_PLACEHOLDERS:
            raise ValueError(f'{name} uses unsupported placeholder {{{token}}}')
    if path.count('{') != path.count('}'):
        raise ValueError(f'{name} has unbalanced placeholder braces')
    return path


def build_path(template: str, values: dict, *, maximum_value: int = 512) -> str:
    """Substitute ``{placeholder}`` tokens with percent-encoded, bounded values.

    Only placeholders already accepted by ``safe_relative_path`` can appear, and
    every value is quoted with an empty safe set. A caller-supplied string can
    therefore never introduce a path separator, query separator or new authority
    into the request target.
    """
    import urllib.parse as _urlparse

    rendered = template
    for token in re.findall(r'\{([^}]*)\}', template):
        raw = values.get(token)
        if raw is None:
            raise ValueError(f'Missing value for placeholder {{{token}}}')
        text = str(raw)
        if len(text) > maximum_value:
            raise ValueError(f'Placeholder {{{token}}} exceeds {maximum_value} characters')
        rendered = rendered.replace('{' + token + '}', _urlparse.quote(text, safe=''))
    return rendered


def dig(document: Any, pointer: str, *, maximum_depth: int = 4) -> Any:
    """Read a dotted field path out of an untrusted provider document.

    Provider responses are untrusted input. Only plain ``a.b.c`` object traversal
    is supported; no index syntax, no wildcards, no expressions. A missing path
    returns ``None`` instead of raising, so a provider schema drift degrades to
    "field not found" rather than an exception that could be mistaken for a
    transport failure.

    Segments are Unicode word characters because 1C and other regional ERPs use
    Cyrillic or Latin field names (``Статус``, ``Ref_Key``). Structural characters
    (``.``, ``[``, ``]``, ``*``, quotes, whitespace, controls) remain rejected.
    """
    if not isinstance(pointer, str) or not pointer:
        raise ValueError('Field pointer must be a non-empty string')
    parts = pointer.split('.')
    if len(parts) > maximum_depth:
        raise ValueError(f'Field pointer exceeds depth {maximum_depth}')
    current = document
    for part in parts:
        if not POINTER_SEGMENT_RE.fullmatch(part):
            raise ValueError('Field pointer segments must be identifier-like')
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def optional_dotted(value: Any, name: str, maximum: int = 128) -> str:
    if value is None or value == '':
        return ''
    return clean_text(value, name, maximum, required=False)


def search_mode(driver: str) -> str:
    """'query' for free-text providers, 'structured' for phone/email equality.

    Kept in the contract so the gateway, the reconciler and any future caller
    route every driver identically instead of each re-deriving the rule.
    """
    return 'query' if driver in QUERY_SEARCH_DRIVERS else 'structured'


def find_leads_by_mode(adapter: Any, driver: str, *, query: Any = None, phone: Any = None,
                       email: Any = None, limit: int = 20) -> list:
    if search_mode(driver) == 'query':
        return adapter.find_leads(query=query or phone or email or '', limit=limit)
    return adapter.find_leads(phone=phone, email=email, limit=limit)


def find_contacts_by_mode(adapter: Any, driver: str, *, query: Any = None, phone: Any = None,
                          email: Any = None, limit: int = 20) -> list:
    if search_mode(driver) == 'query':
        return adapter.find_contacts(query=query or phone or email or '', limit=limit)
    # Bitrix24 has no standalone contact search; its lead search finds the person.
    leads = adapter.find_leads(phone=phone, email=email, limit=limit)
    return [{'id': str(lead.get('id', '')), 'name': lead.get('name', ''),
             'phone': phone or '', 'email': email or '', 'lead_id': lead.get('id')}
            for lead in leads]


def validate_custom_http_config(raw: dict) -> dict:
    """Fail-closed validation of an operator-declared custom HTTP connection.

    Everything that determines a destination (host, base path, method, path
    template, auth header name) is operator configuration. Nothing in an agent
    plan or model response is ever allowed to supply one.
    """
    if not isinstance(raw, dict):
        raise Forbidden('Custom HTTP config must be an object')
    base_path = safe_relative_path(raw.get('base_path', '/'), 'base_path', 200)
    if base_path != '/':
        base_path = base_path.rstrip('/')

    operations = raw.get('operations')
    if not isinstance(operations, dict) or not operations:
        raise ValueError('operations map is required for a custom HTTP connection')
    unknown = sorted(set(operations) - CUSTOM_HTTP_OPERATIONS)
    if unknown:
        raise ValueError(f'Unsupported custom HTTP operations: {", ".join(unknown)}')

    normalised = {}
    for op_name, spec in operations.items():
        if not isinstance(spec, dict):
            raise ValueError(f'Operation {op_name} must be an object')
        method = str(spec.get('method', 'GET')).strip().upper()
        if method not in CUSTOM_HTTP_METHODS:
            raise ValueError(f'Operation {op_name} method {method} is not allowlisted')
        path = safe_relative_path(spec.get('path'), f'{op_name}.path')
        normalised[op_name] = {
            'method': method,
            'path': path,
            'body': spec.get('body') if isinstance(spec.get('body'), dict) else None,
        }

    return {
        'base_path': base_path,
        'operations': normalised,
        'headers': raw.get('headers') if isinstance(raw.get('headers'), dict) else {},
        'response_map': raw.get('response_map') if isinstance(raw.get('response_map'), dict) else {},
        'timeout_seconds': raw.get('timeout_seconds', 15),
    }


def plan_crm_fingerprint(tenant: str, agent: str, connection: str, config: dict, request: dict) -> str:
    """Deterministic hash of the CRM mutation plan, binding arguments to connection and policy."""
    payload = {
        'tenant': tenant,
        'agent': agent,
        'connection': connection,
        'driver': config.get('driver'),
        'host': config.get('host'),
        'request': request,
    }
    return digest(payload)


def parse_call_record(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError('Call record must be an object')
    lead_id = clean_text(data.get('lead_id'), 'lead_id', 128, required=True)
    audio_url = clean_text(data.get('audio_url', ''), 'audio_url', 1000, required=False)
    if audio_url and not (audio_url.startswith('https://') or audio_url.startswith('/media/')):
        raise ValueError('audio_url must be HTTPS URL or verified media path')
    transcript = clean_text(data.get('transcript', ''), 'transcript', 50000, required=True)
    # A bare `int(...)`: `None` raised TypeError and `1.9` was truncated to 1, so the
    # refusal was a crash for one input and a silent edit for another. The bound is the
    # one the tool schema declares.
    duration = bounded_int(data.get('duration_seconds', 0), 'duration_seconds', 0, 86400)
    direction = data.get('direction', 'inbound')
    if direction not in {'inbound', 'outbound'}:
        raise ValueError('direction must be inbound or outbound')
    sentiment = data.get('sentiment', 'neutral')
    if sentiment not in {'positive', 'neutral', 'negative', 'urgent'}:
        raise ValueError('sentiment must be positive, neutral, negative, or urgent')
    summary = clean_text(data.get('summary', ''), 'summary', 2000, required=False)
    return {
        'lead_id': lead_id,
        'audio_url': audio_url,
        'transcript': transcript,
        'duration_seconds': duration,
        'direction': direction,
        'sentiment': sentiment,
        'summary': summary,
    }


def parse_chat_message(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError('Chat message must be an object')
    lead_id = clean_text(data.get('lead_id'), 'lead_id', 128, required=True)
    channel = clean_text(data.get('channel', 'telegram'), 'channel', 32, required=True)
    if channel not in {'telegram', 'whatsapp', 'instagram', 'web', 'sms'}:
        raise ValueError('Unsupported communication channel')
    direction = data.get('direction', 'inbound')
    if direction not in {'inbound', 'outbound'}:
        raise ValueError('direction must be inbound or outbound')
    text = clean_text(data.get('text', ''), 'text', 4096, required=True)
    external_id = clean_text(data.get('external_id', ''), 'external_id', 128, required=False)
    return {
        'lead_id': lead_id,
        'channel': channel,
        'direction': direction,
        'text': text,
        'external_id': external_id,
    }


def parse_followup_request(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError('Followup request must be an object')
    lead_id = clean_text(data.get('lead_id'), 'lead_id', 128, required=True)
    action = data.get('action', 'message')
    if action not in {'message', 'call', 'escalate'}:
        raise ValueError('action must be message, call or escalate')
    channel = clean_text(data.get('channel', 'telegram'), 'channel', 32, required=False)
    reason = clean_text(data.get('reason', ''), 'reason', 1000, required=False)
    # A bare, unbounded `float(...)`. It accepted `'nan'` and `'inf'` WITHOUT raising --
    # both are valid floats -- and any negative value, so a schedule could be NaN, could
    # be infinite and could be in the past. Nothing downstream rejects them: the value
    # goes straight into the audit record and back to the caller. The tool schema
    # declares this field an integer of at least 0, so the parser now returns one, and
    # the ceiling is the validator's own integer default.
    scheduled_at = bounded_int(data.get('scheduled_at', 0), 'scheduled_at', 0, 10**12)
    return {
        'lead_id': lead_id,
        'action': action,
        'channel': channel,
        'reason': reason,
        'scheduled_at': scheduled_at,
    }

