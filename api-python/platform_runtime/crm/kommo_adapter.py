"""Kommo (amoCRM) API v4 typed adapter with OAuth and atomic complex lead creation.

Enforces HTTPS, host pinning, max response size (80KB) and timeout fences.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional
from ..engine import Conflict, Forbidden, encode
from ..tools import NoRedirect
from .crm_contract import (
    provider_price,
    clean_text,
    normalize_phone,
    normalize_email,
    parse_lead_request,
    parse_deal_request,
    validate_crm_config,
)

MAX_RESPONSE_BYTES = 80_000


class KommoHTTPError(RuntimeError):
    pass


def default_kommo_transport(url: str, body: Optional[Any] = None, headers: Optional[dict] = None, method: str = 'GET', timeout: int = 15) -> Any:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('Explicit HTTPS Kommo endpoint required')
    data = encode(body).encode('utf-8') if body is not None else None
    req_headers = {'Content-Type': 'application/json', **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            if resp.status == 204:
                return {}
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise KommoHTTPError('Kommo response exceeded maximum allowed bytes (80KB)')
            return json.loads(raw.decode('utf-8'))
    except KommoHTTPError:
        # See bitrix24_adapter: without this, the byte-ceiling refusal raised INSIDE the
        # try above is caught by the broad handler below and reported as a transport
        # error, sending an operator to look for a network fault.
        raise
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return {}
        raw_err = e.read(MAX_RESPONSE_BYTES)
        try:
            err_json = json.loads(raw_err.decode('utf-8'))
            msg = err_json.get('detail') or err_json.get('title') or str(e)
        except Exception:
            msg = str(e)
        raise KommoHTTPError(f'Kommo HTTP error {e.code}: {msg}') from None
    except Exception as e:
        raise KommoHTTPError(f'Kommo transport error: {e}') from None


class KommoAdapter:
    def __init__(self, raw_config: dict, transport: Callable = default_kommo_transport):
        self.config = validate_crm_config(raw_config)
        self.transport = transport
        self.host = self.config['host']
        
        token_env = self.config.get('token_env')
        if not token_env:
            raise ValueError('token_env must be configured for Kommo / amoCRM')
        token = os.environ.get(token_env, '').strip()
        if not token:
            raise RuntimeError(f'Missing Kommo OAuth access token in environment variable {token_env}')
            
        self.base_url = f'https://{self.host}/api/v4/'
        self.headers = {'Authorization': f'Bearer {token}'}
        # `timeout_seconds` was accepted by the configuration and silently ignored: this
        # adapter neither read it, validated it, nor passed it, so the transport's own
        # default of 15 always won. See bitrix24_adapter.
        self.timeout = self.config.get('timeout_seconds', 15)
        if type(self.timeout) is not int or not 1 <= self.timeout <= 60:
            raise ValueError('timeout_seconds must be an integer 1..60')

    def _call(self, path: str, method: str = 'GET', body: Optional[Any] = None) -> Any:
        url = self.base_url + path.lstrip('/')
        return self.transport(url, body=body, headers=self.headers, method=method,
                              timeout=self.timeout)

    def create_lead(self, request: dict) -> dict:
        parsed = parse_lead_request(request)
        contact_payload: Dict[str, Any] = {
            'first_name': parsed['name'] or 'Customer',
            'custom_fields_values': [],
        }
        if parsed['phone']:
            contact_payload['custom_fields_values'].append({
                'field_code': 'PHONE',
                'values': [{'value': parsed['phone'], 'enum_code': 'WORK'}],
            })
        if parsed['email']:
            contact_payload['custom_fields_values'].append({
                'field_code': 'EMAIL',
                'values': [{'value': parsed['email'], 'enum_code': 'WORK'}],
            })

        # Use Kommo complex lead API: creates lead + contact in one atomic call
        lead_payload = [{
            'name': parsed['title'],
            'price': parsed['price'],
            '_embedded': {
                'contacts': [contact_payload],
            },
        }]
        
        result = self._call('leads/complex', method='POST', body=lead_payload)
        if not isinstance(result, list) or not result:
            raise KommoHTTPError('Unexpected Kommo complex lead response')
            
        first = result[0]
        lead_id = str(first.get('id', ''))
        contact_id = str(first.get('contact_id', ''))
        return {
            'provider': 'kommo',
            'lead_id': lead_id,
            'contact_id': contact_id,
            'title': parsed['title'],
            'created': True,
        }

    def get_lead(self, lead_id: str) -> dict:
        clean_id = clean_text(str(lead_id), 'lead_id', 64)
        result = self._call(f'leads/{clean_id}?with=contacts')
        if not isinstance(result, dict):
            raise KommoHTTPError(f'Lead {clean_id} not found in Kommo')
            
        status_id = result.get('status_id')
        price = int(result.get('price') or 0)
        
        return {
            'provider': 'kommo',
            'id': str(result.get('id', clean_id)),
            'title': result.get('name', ''),
            'price': price,
            'status_id': status_id,
            'pipeline_id': result.get('pipeline_id'),
            'created_at': result.get('created_at'),
            'updated_at': result.get('updated_at'),
        }

    def find_leads(self, *, query: Optional[str] = None, limit: int = 20) -> List[dict]:
        limit = min(max(1, limit), 50)
        params = [f'limit={limit}']
        if query:
            params.append(f'query={urllib.parse.quote(query.strip())}')
        path = f'leads?{"&".join(params)}'
        
        result = self._call(path)
        if not isinstance(result, dict) or '_embedded' not in result:
            return []
        items = result['_embedded'].get('leads', [])
        
        leads = []
        for item in items[:limit]:
            leads.append({
                'id': str(item.get('id')),
                'title': item.get('name', ''),
                'price': provider_price(item.get('price')),
                'status_id': item.get('status_id'),
                'pipeline_id': item.get('pipeline_id'),
                'created_at': item.get('created_at'),
            })
        return leads

    def find_contacts(self, *, query: Optional[str] = None, limit: int = 20) -> List[dict]:
        limit = min(max(1, limit), 50)
        params = [f'limit={limit}']
        if query:
            params.append(f'query={urllib.parse.quote(query.strip())}')
        path = f'contacts?{"&".join(params)}'
        
        result = self._call(path)
        if not isinstance(result, dict) or '_embedded' not in result:
            return []
        items = result['_embedded'].get('contacts', [])
        
        contacts = []
        for item in items[:limit]:
            contacts.append({
                'id': str(item.get('id')),
                'name': item.get('name', ''),
                'created_at': item.get('created_at'),
            })
        return contacts

    def create_deal(self, request: dict) -> dict:
        parsed = parse_deal_request(request)
        lead_payload = [{
            'name': parsed['title'],
            'price': parsed['price'],
        }]
        result = self._call('leads', method='POST', body=lead_payload)
        if not isinstance(result, dict) or '_embedded' not in result:
            raise KommoHTTPError('Unexpected Kommo deal creation response')
        items = result['_embedded'].get('leads', [])
        if not items:
            raise KommoHTTPError('No deal returned in Kommo response')
        deal_id = str(items[0].get('id'))
        return {
            'provider': 'kommo',
            'deal_id': deal_id,
            'title': parsed['title'],
            'created': True,
        }

    def attach_call_record(self, lead_id: str, call: dict) -> dict:
        clean_id = clean_text(str(lead_id), 'lead_id', 64)
        direction_label = "Kiruvchi" if call.get('direction') == 'inbound' else "Chiquvchi"
        sentiment_label = call.get('sentiment', 'neutral').upper()
        audio_link = f"\nAudio: {call['audio_url']}" if call.get('audio_url') else ""
        note_text = (
            f"[AI Call-Center] {direction_label} ({call.get('duration_seconds', 0)} sek) | Kayfiyat: {sentiment_label}\n"
            f"Xulosa: {call.get('summary', '')}"
            f"{audio_link}\n\n"
            f"Transkript:\n{call.get('transcript', '')}"
        )
        payload = [{
            'entity_id': int(clean_id) if clean_id.isdigit() else clean_id,
            'note_type': 'common',
            'params': {
                'text': note_text[:8000],
            }
        }]
        result = self._call(f'leads/{clean_id}/notes', method='POST', body=payload)
        note_id = str(result['_embedded']['notes'][0]['id']) if isinstance(result, dict) and '_embedded' in result else 'attached'
        return {'provider': 'kommo', 'lead_id': clean_id, 'attached': True, 'note_id': note_id}

    def attach_message(self, lead_id: str, msg: dict) -> dict:
        clean_id = clean_text(str(lead_id), 'lead_id', 64)
        direction_label = "Mijoz" if msg.get('direction') == 'inbound' else "AI Agent"
        note_text = f"[{msg.get('channel', 'chat').upper()}] {direction_label}:\n{msg.get('text', '')}"
        payload = [{
            'entity_id': int(clean_id) if clean_id.isdigit() else clean_id,
            'note_type': 'common',
            'params': {
                'text': note_text[:8000],
            }
        }]
        result = self._call(f'leads/{clean_id}/notes', method='POST', body=payload)
        note_id = str(result['_embedded']['notes'][0]['id']) if isinstance(result, dict) and '_embedded' in result else 'attached'
        return {'provider': 'kommo', 'lead_id': clean_id, 'attached': True, 'note_id': note_id}

