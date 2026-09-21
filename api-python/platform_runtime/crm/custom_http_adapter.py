"""Operator-declared custom HTTP adapter for CRMs without a published adapter.

This is how Modme, Billz, MoySklad, YCLients, Jowi, Poster and a client's own
in-house CRM are onboarded without writing new core Python: the operator declares
the host, base path, per-operation method, path template and response field map,
and this adapter executes exactly that.

Boundaries that make it safe to expose to an agent:

* The destination is operator configuration only. No tool argument, pack value or
  model output can supply a scheme, host, port or absolute URL.
* Path templates are validated by ``safe_relative_path`` and every placeholder is
  percent-encoded, so a caller string cannot add a path or query separator.
* Responses are read through an operator-declared ``response_map``. A field the
  map does not resolve stays absent instead of being guessed.
* No blind write retry. A transport failure after a write stays uncertain and is
  settled only by owner-initiated reconciliation.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ..engine import Conflict, Forbidden, encode
from ..tools import NoRedirect
from .crm_contract import (
    optional_email,
    optional_phone,
    build_path,
    clean_text,
    dig,
    normalize_email,
    normalize_phone,
    parse_deal_request,
    parse_lead_request,
    validate_crm_config,
    validate_custom_http_config,
)

MAX_RESPONSE_BYTES = 80_000
# Header names are operator configuration and go on the wire, so they must be
# token-clean. Values come from environment references, never from a plan.
HEADER_NAME_RE = re.compile(r'^[A-Za-z0-9-]{1,64}$')
ENV_REF_RE = re.compile(r'^[A-Z][A-Z0-9_]*$')


class CustomHTTPError(RuntimeError):
    pass


def default_custom_transport(url: str, body: Optional[Any] = None, headers: Optional[dict] = None,
                             method: str = 'GET', timeout: int = 15) -> Any:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('Explicit HTTPS custom endpoint required')
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Custom endpoint must not embed credentials or a fragment')
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
                raise CustomHTTPError('Provider response exceeded maximum allowed bytes (80KB)')
            if not raw:
                return {}
            return json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as error:
        if error.code == 204:
            return {}
        # Provider bodies can carry internal URLs, stack traces or credentials.
        # Only the sanitized status is surfaced.
        raise CustomHTTPError(f'Provider HTTP status {error.code}') from None
    except CustomHTTPError:
        raise
    except Exception:
        raise CustomHTTPError('Provider transport failure') from None


class CustomHTTPAdapter:
    """Executes only the operations the operator declared. Never retries a write.

    Destinations, methods and body shapes are operator configuration only; credential
    headers are environment references, and headers that could re-target the request
    (``Host``, ``Content-Length``, ``Connection``, ``Transfer-Encoding``) are refused.
    """

    def __init__(self, raw_config: dict, transport: Callable = default_custom_transport):
        self.config = validate_crm_config(raw_config)
        declared = validate_custom_http_config(self.config)
        self.transport = transport
        self.host = self.config['host']
        self.base_path = declared['base_path']
        self.operations = declared['operations']
        self.response_map = declared['response_map']
        self.timeout = declared['timeout_seconds']
        if type(self.timeout) is not int or not 1 <= self.timeout <= 60:
            raise ValueError('timeout_seconds must be an integer 1..60')
        # attach_message is addressed by lead_id. Without that placeholder every
        # note would be posted to one endpoint and attached to the wrong record.
        attach = self.operations.get('attach_message')
        if attach is not None and '{lead_id}' not in attach['path']:
            raise ValueError('attach_message.path must contain the {lead_id} placeholder')
        self.headers = self._declared_headers(declared['headers'])

    def _declared_headers(self, declared: Dict[str, Any]) -> Dict[str, str]:
        """Resolve static headers and credential references from the environment."""
        headers: Dict[str, str] = {}
        for name, spec in declared.items():
            if not HEADER_NAME_RE.match(str(name)):
                raise ValueError(f'Invalid header name in custom HTTP config: {name!r}')
            if str(name).lower() in {'host', 'content-length', 'connection', 'transfer-encoding'}:
                raise ValueError(f'Header {name} may not be overridden')
            if isinstance(spec, str):
                headers[str(name)] = spec
                continue
            if not isinstance(spec, dict):
                raise ValueError(f'Header {name} must be a string or an environment reference')
            env_ref = spec.get('env')
            if not isinstance(env_ref, str) or not ENV_REF_RE.match(env_ref):
                raise ValueError(f'Header {name} needs a valid environment variable reference')
            value = os.environ.get(env_ref, '').strip()
            if not value:
                raise RuntimeError(f'Missing credential in environment variable {env_ref}')
            headers[str(name)] = str(spec.get('prefix', '')) + value
        return headers

    def _operation(self, name: str) -> dict:
        spec = self.operations.get(name)
        if spec is None:
            raise Forbidden(f'Operation {name} is not declared for this connection')
        return spec

    def _call(self, operation: str, values: dict, body: Optional[Any] = None) -> Any:
        spec = self._operation(operation)
        rendered = build_path(spec['path'], values)
        url = f'https://{self.host}{self.base_path}{rendered}'
        return self.transport(url, body=body, headers=dict(self.headers),
                              method=spec['method'], timeout=self.timeout)

    def _map(self, key: str, document: Any, default: Any = None) -> Any:
        pointer = self.response_map.get(key)
        if not isinstance(pointer, str) or not pointer:
            return default
        resolved = dig(document, pointer)
        return default if resolved is None else resolved

    def _rows(self, document: Any) -> List[dict]:
        if isinstance(document, list):
            return [row for row in document if isinstance(row, dict)]
        items = self._map('items', document)
        if isinstance(items, list):
            return [row for row in items if isinstance(row, dict)]
        return []

    def _shape_lead(self, row: dict) -> dict:
        phone_source = self._map('phone', row)
        email_source = self._map('email', row)
        identifier = self._map('id', row)
        title = self._map('title', row)
        status = self._map('status', row)
        return {
            'id': str(identifier) if identifier is not None else '',
            'title': str(title) if title is not None else '',
            'name': str(self._map('name', row) or ''),
            'phone': optional_phone(phone_source),
            'email': optional_email(email_source),
            'status': str(status) if status is not None else '',
            'created_at': str(self._map('created_at', row) or ''),
        }

    def find_leads(self, query: str = '', limit: int = 20) -> List[dict]:
        limit = min(max(1, int(limit)), 50)
        document = self._call('find_leads', {
            'query': clean_text(query, 'query', 256, required=False),
            'limit': limit,
        })
        return [self._shape_lead(row) for row in self._rows(document)][:limit]

    def find_contacts(self, query: str = '', limit: int = 20) -> List[dict]:
        limit = min(max(1, int(limit)), 50)
        document = self._call('find_contacts', {
            'query': clean_text(query, 'query', 256, required=False),
            'limit': limit,
        })
        contacts = []
        for row in self._rows(document)[:limit]:
            phone_source = self._map('phone', row)
            email_source = self._map('email', row)
            identifier = self._map('id', row)
            contacts.append({
                'id': str(identifier) if identifier is not None else '',
                'name': str(self._map('name', row) or ''),
                'phone': optional_phone(phone_source),
                'email': optional_email(email_source),
            })
        return contacts

    def _created_id(self, document: Any) -> Any:
        return self._map('id', document) or self._map('lead_id', document) or self._map('deal_id', document)

    def _create_body(self, operation: str, parsed: dict) -> Any:
        """Operator-declared body template wins; otherwise a neutral default."""
        template = self._operation(operation).get('body')
        payload = {
            'title': parsed['title'],
            'name': parsed.get('name', ''),
            'phone': parsed.get('phone', ''),
            'email': parsed.get('email', ''),
            'price': parsed.get('price', 0),
            'currency': parsed.get('currency', 'UZS'),
            'source': parsed.get('source', ''),
            'comments': parsed.get('comments', ''),
            'stage': parsed.get('stage', ''),
            'contact_id': parsed.get('contact_id', ''),
            'lead_id': parsed.get('lead_id', ''),
        }
        if template is None:
            return payload
        # Only declared keys may appear in the outbound body, and each value must
        # name a validated parsed field. A plan cannot inject an unexpected field.
        body = {}
        for key, pointer in template.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f'{operation}.body keys must be non-empty strings')
            if not isinstance(pointer, str) or pointer not in payload:
                raise ValueError(f'{operation}.body.{key} must reference a supported field')
            body[key] = payload[pointer]
        return body

    def create_lead(self, request: dict) -> dict:
        parsed = parse_lead_request(request)
        document = self._call('create_lead', {}, body=self._create_body('create_lead', parsed))
        identifier = self._created_id(document)
        if identifier is None:
            raise Conflict('Provider accepted the request but returned no record identifier')
        return {'provider': 'custom_http', 'lead_id': str(identifier),
                'title': parsed['title'], 'created': True}

    def create_deal(self, request: dict) -> dict:
        parsed = parse_deal_request(request)
        document = self._call('create_deal', {}, body=self._create_body('create_deal', parsed))
        identifier = self._created_id(document)
        if identifier is None:
            raise Conflict('Provider accepted the request but returned no record identifier')
        return {'provider': 'custom_http', 'deal_id': str(identifier),
                'title': parsed['title'], 'created': True}

    def find_stalled_leads(self, inactive_minutes: int = 120, limit: int = 20) -> List[dict]:
        """Read-only feed for the re-engagement loop. No writes, no retries."""
        minutes = min(max(1, int(inactive_minutes)), 20_160)
        limit = min(max(1, int(limit)), 50)
        document = self._call('find_stalled_leads', {'minutes': minutes, 'limit': limit})
        leads = []
        for row in self._rows(document)[:limit]:
            shaped = self._shape_lead(row)
            # A lead without an identifier cannot be followed up safely: a
            # re-engagement message would have no record to attach to.
            if shaped['id']:
                leads.append(shaped)
        return leads

    def _attach_with(self, operation: str, lead_id: str, comment: str) -> dict:
        document = self._call(operation, {'lead_id': lead_id}, body={'comment': comment[:8000]})
        reference = self._map('comment_id', document) or self._map('id', document)
        return {'provider': 'custom_http', 'lead_id': lead_id, 'attached': True,
                'comment_id': str(reference) if reference is not None else lead_id}

    def attach_call_record(self, lead_id: str, call: dict) -> dict:
        clean_id = clean_text(str(lead_id), 'lead_id', 64)
        direction = 'Kiruvchi' if call.get('direction') == 'inbound' else 'Chiquvchi'
        comment = (
            f"[AI Call-Center] {direction} qo‘ng‘iroq ({call.get('duration_seconds', 0)} sek) | "
            f"Kayfiyat: {str(call.get('sentiment', 'neutral')).upper()}\n"
            f"Xulosa: {call.get('summary', '')}\n\n--- Transkript ---\n{call.get('transcript', '')}"
        )
        # Call records share the message timeline when no dedicated operation is
        # declared; refusing outright would leave the call log unreachable.
        operation = 'attach_call_record' if 'attach_call_record' in self.operations else 'attach_message'
        return self._attach_with(operation, clean_id, comment)

    def attach_message(self, lead_id: str, msg: dict) -> dict:
        clean_id = clean_text(str(lead_id), 'lead_id', 64)
        direction = 'Mijoz' if msg.get('direction') == 'inbound' else 'AI Agent'
        comment = f"[{str(msg.get('channel', 'chat')).upper()}] {direction}:\n{msg.get('text', '')}"
        return self._attach_with('attach_message', clean_id, comment)