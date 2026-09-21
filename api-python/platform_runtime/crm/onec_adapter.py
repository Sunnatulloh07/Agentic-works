"""1C:Enterprise (1C:CRM / 1C:УТ / 1C:ERP) typed adapter.

1C exposes an operator-published HTTP service or OData endpoint. Only HTTPS with
pinned host, pinned base path, bounded bodies and zero blind write retries are
supported. Credentials are read from an environment reference; Basic auth is the
1C default and Bearer is accepted when the published service uses a token.

1C answers with a site-specific shape, so responses are parsed through the
operator-declared ``response_map`` rather than guessed field names. A field the
map does not resolve stays absent instead of being invented.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ..engine import Conflict, encode
from ..tools import NoRedirect
from .crm_contract import (
    optional_email,
    optional_phone,
    ONEC_STATUS_MAP,
    REVERSE_ONEC_STATUS_MAP,
    build_path,
    clean_text,
    dig,
    normalize_email,
    normalize_phone,
    parse_deal_request,
    parse_lead_request,
    safe_relative_path,
    validate_crm_config,
)

MAX_RESPONSE_BYTES = 80_000
DEFAULT_BASE_PATH = '/agent-platform/hs/leads'


class OneCHTTPError(RuntimeError):
    pass


def default_onec_transport(url: str, body: Optional[Any] = None, headers: Optional[dict] = None,
                           method: str = 'GET', timeout: int = 15) -> Any:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('Explicit HTTPS 1C endpoint required')
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError('1C endpoint must not embed credentials or a fragment')
    data = encode(body).encode('utf-8') if body is not None else None
    request = urllib.request.Request(
        url, data=data, headers={'Content-Type': 'application/json', **(headers or {})}, method=method)
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status == 204:
                return {}
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise OneCHTTPError('1C response exceeded maximum allowed bytes (80KB)')
            if not raw:
                return {}
            return json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as error:
        if error.code == 204:
            return {}
        # The provider body is never echoed: it can contain a stack trace, an
        # internal URL or a credential fragment. Only the status is reported.
        raise OneCHTTPError(f'1C HTTP status {error.code}') from None
    except OneCHTTPError:
        raise
    except Exception:
        raise OneCHTTPError('1C transport failure') from None


class OneCAdapter:
    """Typed, host-pinned 1C adapter. Never retries a write."""

    def __init__(self, raw_config: dict, transport: Callable = default_onec_transport):
        self.config = validate_crm_config(raw_config)
        self.transport = transport
        self.host = self.config['host']
        self.base_path = safe_relative_path(
            self.config.get('base_path', DEFAULT_BASE_PATH), 'base_path', 200).rstrip('/') or '/'
        self.timeout = self.config.get('timeout_seconds', 15)
        if type(self.timeout) is not int or not 1 <= self.timeout <= 60:
            raise ValueError('timeout_seconds must be an integer 1..60')
        self.headers = self._auth_headers()
        raw_map = self.config.get('response_map')
        self.response_map = raw_map if isinstance(raw_map, dict) else {}
        # Path options are operator configuration, exactly like base_path, so they
        # pass the same traversal/placeholder gate instead of reaching build_path raw.
        defaults = {
            'find_path': '/search?q={query}&limit={limit}',
            'contacts_path': '/contacts?q={query}&limit={limit}',
            'stalled_path': '/leads/stalled?minutes={minutes}&limit={limit}',
            'comment_path': '/leads/{lead_id}/comment',
        }
        self.paths = {}
        for key, default in defaults.items():
            candidate = self.config.get(key, default)
            if not isinstance(candidate, str):
                raise ValueError(f'{key} must be a string')
            self.paths[key] = safe_relative_path(candidate, key)
        # A comment path without {lead_id} would post every comment to one endpoint,
        # attaching notes to the wrong record. Refuse it at construction time.
        if '{lead_id}' not in self.paths['comment_path']:
            raise ValueError('comment_path must contain the {lead_id} placeholder')

    def _auth_headers(self) -> Dict[str, str]:
        """Resolve the credential from the environment. Nothing is read from a plan.

        ``auth`` selects the scheme: 1C publishes either a Basic-authenticated HTTP
        service (default) or a token-protected one. The generic contract requires a
        credential reference, so ``credential_env`` is accepted as the shared name
        when the scheme-specific reference is absent.
        """
        mode = str(self.config.get('auth', 'basic')).strip().lower()
        if mode not in {'basic', 'bearer'}:
            raise ValueError('1C auth must be basic or bearer')
        if mode == 'basic':
            ref = self.config.get('basic_auth_env') or self.config.get('credential_env')
        else:
            ref = self.config.get('token_env') or self.config.get('credential_env')
        if not isinstance(ref, str) or not ref:
            raise ValueError(f'1C {mode} auth requires an environment variable reference')
        raw = os.environ.get(ref, '')
        if not raw:
            raise RuntimeError(f'Missing 1C credential in environment variable {ref}')
        if mode == 'basic':
            # 1C expects user:password. A malformed value would otherwise produce a
            # header the server rejects as an opaque 401, hiding an operator typo.
            user, separator, password = raw.partition(':')
            if not separator or not user or not password:
                raise ValueError('1C basic credential must be user:password')
            return {'Authorization': 'Basic ' + base64.b64encode(raw.encode('utf-8')).decode('ascii')}
        return {'Authorization': 'Bearer ' + raw.strip()}

    def _map(self, key: str, document: Any, default: Any = None) -> Any:
        pointer = self.response_map.get(key)
        if not isinstance(pointer, str) or not pointer:
            return default
        resolved = dig(document, pointer)
        return default if resolved is None else resolved

    def _call(self, path: str, method: str = 'GET', body: Optional[Any] = None) -> Any:
        url = f'https://{self.host}{self.base_path}{path}'
        return self.transport(url, body=body, headers=dict(self.headers),
                              method=method, timeout=self.timeout)

    def _rows(self, document: Any) -> List[dict]:
        """Provider rows from either a bare array or the mapped ``items`` field."""
        if isinstance(document, list):
            return [row for row in document if isinstance(row, dict)]
        items = self._map('items', document)
        if isinstance(items, list):
            return [row for row in items if isinstance(row, dict)]
        return []

    def _shape_lead(self, row: dict) -> dict:
        phone_source = self._map('phone', row)
        email_source = self._map('email', row)
        identifier = self._map('id', row) or row.get('Ref_Key') or row.get('Number')
        title = self._map('title', row) or row.get('Description')
        raw_status = str(self._map('status', row) or '').strip()
        return {
            'id': str(identifier) if identifier is not None else '',
            'title': str(title) if title else '',
            'name': str(self._map('name', row) or row.get('Наименование') or ''),
            'phone': optional_phone(phone_source),
            'email': optional_email(email_source),
            'status': ONEC_STATUS_MAP.get(raw_status, ''),
            'provider_status': raw_status,
            'created_at': str(self._map('created_at', row) or row.get('Дата') or ''),
        }

    def find_leads(self, query: str = '', limit: int = 20) -> List[dict]:
        limit = min(max(1, int(limit)), 50)
        path = build_path(self.paths['find_path'],
                          {'query': clean_text(query, 'query', 256, required=False),
                           'limit': limit})
        document = self._call(path)
        return [self._shape_lead(row) for row in self._rows(document)][:limit]

    def find_contacts(self, query: str = '', limit: int = 20) -> List[dict]:
        limit = min(max(1, int(limit)), 50)
        path = build_path(self.paths['contacts_path'],
                          {'query': clean_text(query, 'query', 256, required=False),
                           'limit': limit})
        document = self._call(path)
        contacts = []
        for row in self._rows(document)[:limit]:
            identifier = self._map('id', row) or row.get('Ref_Key')
            phone_source = self._map('phone', row)
            email_source = self._map('email', row)
            contacts.append({
                'id': str(identifier) if identifier is not None else '',
                'name': str(self._map('name', row) or row.get('Наименование') or ''),
                'phone': optional_phone(phone_source),
                'email': optional_email(email_source),
            })
        return contacts

    def _document_id(self, document: Any) -> Any:
        """Identifier of a just-created document.

        ``created_id`` lets an operator point at a provider envelope such as
        ``result.Ref_Key`` without disturbing the ``id`` pointer used for list
        rows, where the same field sits at a different depth.
        """
        for key in ('created_id', 'id', 'lead_id', 'deal_id'):
            resolved = self._map(key, document)
            if resolved is not None:
                return resolved
        return None

    def create_lead(self, request: dict) -> dict:
        parsed = parse_lead_request(request)
        payload = {
            'Description': parsed['title'],
            'Наименование': parsed['name'] or parsed['title'],
            'phone': parsed['phone'],
            'email': parsed['email'],
            'Сумма': parsed['price'],
            'Валюта': parsed['currency'],
            'Источник': parsed['source'],
            'Комментарий': parsed['comments'],
            'Статус': REVERSE_ONEC_STATUS_MAP['new'],
        }
        identifier = self._document_id(self._call('/leads', method='POST', body=payload))
        if identifier is None:
            # A 2xx without an identifier cannot prove which record was created.
            # Reporting success would make an unverifiable write look settled.
            raise Conflict('1C accepted the request but returned no document identifier')
        return {'provider': 'onec', 'lead_id': str(identifier),
                'title': parsed['title'], 'created': True}

    def create_deal(self, request: dict) -> dict:
        parsed = parse_deal_request(request)
        stage = parsed['stage'] if parsed['stage'] in REVERSE_ONEC_STATUS_MAP else 'new'
        payload = {
            'Description': parsed['title'],
            'Сумма': parsed['price'],
            'Валюта': parsed['currency'],
            'Статус': REVERSE_ONEC_STATUS_MAP.get(stage, 'Новый'),
        }
        if parsed['contact_id']:
            payload['Контакт'] = parsed['contact_id']
        if parsed['lead_id']:
            payload['Основание'] = parsed['lead_id']
        identifier = self._document_id(self._call('/deals', method='POST', body=payload))
        if identifier is None:
            raise Conflict('1C accepted the request but returned no document identifier')
        return {'provider': 'onec', 'deal_id': str(identifier),
                'title': parsed['title'], 'created': True}

    def find_stalled_leads(self, inactive_minutes: int = 120, limit: int = 20) -> List[dict]:
        """Read-only feed for the re-engagement loop. No writes, no retries."""
        minutes = min(max(1, int(inactive_minutes)), 20_160)
        limit = min(max(1, int(limit)), 50)
        path = build_path(self.paths['stalled_path'], {'minutes': minutes, 'limit': limit})
        document = self._call(path)
        leads = []
        for row in self._rows(document)[:limit]:
            shaped = self._shape_lead(row)
            # A stalled lead without an identifier cannot be acted on safely:
            # a re-engagement message would have no record to attach to.
            if shaped['id']:
                leads.append(shaped)
        return leads

    def _attach(self, lead_id: str, comment: str) -> dict:
        path = build_path(self.paths['comment_path'], {'lead_id': lead_id})
        document = self._call(path, method='POST', body={'Комментарий': comment[:8000]})
        reference = self._map('comment_id', document) or self._map('id', document)
        return {'provider': 'onec', 'lead_id': lead_id, 'attached': True,
                'comment_id': str(reference) if reference is not None else lead_id}

    def attach_call_record(self, lead_id: str, call: dict) -> dict:
        clean_id = clean_text(str(lead_id), 'lead_id', 64)
        direction = 'Kiruvchi' if call.get('direction') == 'inbound' else 'Chiquvchi'
        comment = (
            f"[AI Call-Center] {direction} qo‘ng‘iroq ({call.get('duration_seconds', 0)} sek) | "
            f"Kayfiyat: {str(call.get('sentiment', 'neutral')).upper()}\n"
            f"Xulosa: {call.get('summary', '')}\n\n--- Transkript ---\n{call.get('transcript', '')}"
        )
        return self._attach(clean_id, comment)

    def attach_message(self, lead_id: str, msg: dict) -> dict:
        clean_id = clean_text(str(lead_id), 'lead_id', 64)
        direction = 'Mijoz' if msg.get('direction') == 'inbound' else 'AI Agent'
        comment = f"[{str(msg.get('channel', 'chat')).upper()}] {direction}:\n{msg.get('text', '')}"
        return self._attach(clean_id, comment)