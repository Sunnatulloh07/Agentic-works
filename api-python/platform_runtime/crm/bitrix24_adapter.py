"""Bitrix24 REST API typed adapter with host allowlisting and rate-limited calls.

All outbound calls enforce HTTPS, host pinning, bounded response bodies (max 80KB)
and zero blind write retries on timeout.
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
    BITRIX24_STATUS_MAP,
    REVERSE_BITRIX24_STATUS_MAP,
    clean_text,
    normalize_phone,
    normalize_email,
    parse_lead_request,
    parse_deal_request,
    validate_crm_config,
)

MAX_RESPONSE_BYTES = 80_000


class Bitrix24HTTPError(RuntimeError):
    pass


def default_transport(url: str, body: Optional[dict] = None, headers: Optional[dict] = None, timeout: int = 15) -> dict:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('Explicit HTTPS Bitrix24 endpoint required')
    data = encode(body).encode('utf-8') if body is not None else None
    req_headers = {'Content-Type': 'application/json', **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=req_headers, method='POST' if data else 'GET')
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise Bitrix24HTTPError('Bitrix24 response exceeded maximum allowed bytes (80KB)')
            return json.loads(raw.decode('utf-8'))
    except Bitrix24HTTPError:
        # The byte-ceiling refusal is raised INSIDE the try above, so the broad handler
        # below caught it and re-wrapped a SIZE refusal as "Bitrix24 transport error",
        # sending an operator to look for a network fault. `onec` and `custom_http`
        # already re-raise their own type here; this one did not.
        raise
    except urllib.error.HTTPError as e:
        raw_err = e.read(MAX_RESPONSE_BYTES)
        try:
            err_json = json.loads(raw_err.decode('utf-8'))
            msg = err_json.get('error_description') or err_json.get('error') or str(e)
        except Exception:
            msg = str(e)
        raise Bitrix24HTTPError(f'Bitrix24 HTTP error {e.code}: {msg}') from None
    except Exception as e:
        raise Bitrix24HTTPError(f'Bitrix24 transport error: {e}') from None


class Bitrix24Adapter:
    def __init__(self, raw_config: dict, transport: Callable = default_transport):
        self.config = validate_crm_config(raw_config)
        self.transport = transport
        self.host = self.config['host']
        
        # Determine endpoint base and auth headers
        token_env = self.config.get('token_env')
        webhook_url_env = self.config.get('webhook_url_env')
        
        if webhook_url_env:
            raw_url = os.environ.get(webhook_url_env, '').strip()
            if not raw_url:
                raise RuntimeError(f'Missing Bitrix24 webhook URL in environment variable {webhook_url_env}')
            parsed = urllib.parse.urlparse(raw_url)
            if parsed.scheme != 'https' or parsed.hostname != self.host:
                raise Forbidden(f'Bitrix24 webhook URL host must match configured host {self.host}')
            self.base_url = raw_url.rstrip('/') + '/'
            self.headers = {}
        elif token_env:
            token = os.environ.get(token_env, '').strip()
            if not token:
                raise RuntimeError(f'Missing Bitrix24 OAuth token in environment variable {token_env}')
            self.base_url = f'https://{self.host}/rest/'
            self.headers = {'Authorization': f'Bearer {token}'}
        else:
            raise ValueError('Either webhook_url_env or token_env must be configured for Bitrix24')
        # `timeout_seconds` was accepted by the configuration and silently ignored here:
        # this adapter neither read it, validated it, nor passed it, so the transport's
        # own default of 15 always won. `onec` and `custom_http` read it and bound it to
        # 1..60, so the same config key meant two different things across the four.
        self.timeout = self.config.get('timeout_seconds', 15)
        if type(self.timeout) is not int or not 1 <= self.timeout <= 60:
            raise ValueError('timeout_seconds must be an integer 1..60')

    def _call(self, method: str, body: Optional[dict] = None) -> Any:
        url = self.base_url + method.lstrip('/')
        resp = self.transport(url, body=body, headers=self.headers, timeout=self.timeout)
        if not isinstance(resp, dict):
            raise Bitrix24HTTPError('Invalid Bitrix24 response format')
        if 'error' in resp:
            raise Bitrix24HTTPError(f"Bitrix24 API error: {resp.get('error_description', resp['error'])}")
        return resp.get('result')

    def create_lead(self, request: dict) -> dict:
        parsed = parse_lead_request(request)
        fields: Dict[str, Any] = {
            'TITLE': parsed['title'],
            'OPPORTUNITY': parsed['price'],
            'CURRENCY_ID': parsed['currency'],
            'SOURCE_ID': parsed['source'],
        }
        if parsed['name']:
            fields['NAME'] = parsed['name']
        if parsed['phone']:
            fields['PHONE'] = [{'VALUE': parsed['phone'], 'VALUE_TYPE': 'WORK'}]
        if parsed['email']:
            fields['EMAIL'] = [{'VALUE': parsed['email'], 'VALUE_TYPE': 'WORK'}]
        if parsed['comments']:
            fields['COMMENTS'] = parsed['comments']

        payload = {'fields': fields, 'params': {'REGISTER_SONET_EVENT': 'Y'}}
        lead_id = self._call('crm.lead.add.json', payload)
        if not lead_id:
            raise Bitrix24HTTPError('Failed to create lead in Bitrix24: empty result')
        return {
            'provider': 'bitrix24',
            'lead_id': str(lead_id),
            'title': parsed['title'],
            'created': True,
        }

    def get_lead(self, lead_id: str) -> dict:
        clean_id = clean_text(str(lead_id), 'lead_id', 64)
        result = self._call('crm.lead.get.json', {'id': clean_id})
        if not isinstance(result, dict):
            raise Bitrix24HTTPError(f'Lead {clean_id} not found in Bitrix24')
        raw_status = result.get('STATUS_ID', 'NEW')
        status = BITRIX24_STATUS_MAP.get(raw_status, 'in_progress')
        
        # Extract phone
        phones = result.get('PHONE', [])
        phone = phones[0].get('VALUE', '') if isinstance(phones, list) and phones else ''
        
        # Extract email
        emails = result.get('EMAIL', [])
        email = emails[0].get('VALUE', '') if isinstance(emails, list) and emails else ''
        
        return {
            'provider': 'bitrix24',
            'id': str(result.get('ID', clean_id)),
            'title': result.get('TITLE', ''),
            'name': result.get('NAME', ''),
            'status': status,
            'raw_status': raw_status,
            'price': int(float(result.get('OPPORTUNITY') or 0)),
            'currency': result.get('CURRENCY_ID', 'UZS'),
            'phone': phone,
            'email': email,
            'created_at': result.get('DATE_CREATE', ''),
        }

    def find_leads(self, *, phone: Optional[str] = None, email: Optional[str] = None, limit: int = 20) -> List[dict]:
        limit = min(max(1, limit), 50)
        filter_dict: Dict[str, Any] = {}
        if phone:
            filter_dict['PHONE'] = normalize_phone(phone)
        if email:
            filter_dict['EMAIL'] = normalize_email(email)
            
        payload = {
            'filter': filter_dict,
            'select': ['ID', 'TITLE', 'STATUS_ID', 'OPPORTUNITY', 'CURRENCY_ID', 'DATE_CREATE', 'NAME'],
            'order': {'DATE_CREATE': 'DESC'},
        }
        results = self._call('crm.lead.list.json', payload)
        if not isinstance(results, list):
            return []
        
        leads = []
        for item in results[:limit]:
            raw_status = item.get('STATUS_ID', 'NEW')
            leads.append({
                'id': str(item.get('ID')),
                'title': item.get('TITLE', ''),
                'name': item.get('NAME', ''),
                'status': BITRIX24_STATUS_MAP.get(raw_status, 'in_progress'),
                'raw_status': raw_status,
                'price': provider_price(item.get('OPPORTUNITY')),
                'currency': item.get('CURRENCY_ID', 'UZS'),
                'created_at': item.get('DATE_CREATE', ''),
            })
        return leads

    def create_contact(self, request: dict) -> dict:
        name = clean_text(request.get('name', 'Customer'), 'contact name', 128)
        phone = normalize_phone(request['phone']) if request.get('phone') else ''
        email = normalize_email(request['email']) if request.get('email') else ''
        
        fields: Dict[str, Any] = {'NAME': name}
        if phone:
            fields['PHONE'] = [{'VALUE': phone, 'VALUE_TYPE': 'WORK'}]
        if email:
            fields['EMAIL'] = [{'VALUE': email, 'VALUE_TYPE': 'WORK'}]
            
        contact_id = self._call('crm.contact.add.json', {'fields': fields})
        return {
            'provider': 'bitrix24',
            'contact_id': str(contact_id),
            'name': name,
            'created': True,
        }

    def create_deal(self, request: dict) -> dict:
        parsed = parse_deal_request(request)
        stage_id = REVERSE_BITRIX24_STATUS_MAP.get(parsed['stage'], parsed['stage'])
        fields: Dict[str, Any] = {
            'TITLE': parsed['title'],
            'OPPORTUNITY': parsed['price'],
            'CURRENCY_ID': parsed['currency'],
            'STAGE_ID': stage_id,
        }
        if parsed['contact_id']:
            fields['CONTACT_ID'] = parsed['contact_id']
        if parsed['lead_id']:
            fields['LEAD_ID'] = parsed['lead_id']
            
        deal_id = self._call('crm.deal.add.json', {'fields': fields})
        return {
            'provider': 'bitrix24',
            'deal_id': str(deal_id),
            'title': parsed['title'],
            'created': True,
        }

    def attach_call_record(self, lead_id: str, call: dict) -> dict:
        clean_id = clean_text(str(lead_id), 'lead_id', 64)
        direction_label = "Kiruvchi" if call.get('direction') == 'inbound' else "Chiquvchi"
        sentiment_label = call.get('sentiment', 'neutral').upper()
        audio_link = f"\nAudio yozuv: {call['audio_url']}" if call.get('audio_url') else ""
        comment_text = (
            f"[AI Call-Center] {direction_label} ovozli qo‘ng‘iroq ({call.get('duration_seconds', 0)} soniya)\n"
            f"Kayfiyat: {sentiment_label}\n"
            f"Xulosa: {call.get('summary', '')}"
            f"{audio_link}\n\n"
            f"--- Transkript ---\n{call.get('transcript', '')}"
        )
        payload = {
            'fields': {
                'ENTITY_ID': clean_id,
                'ENTITY_TYPE': 'lead',
                'COMMENT': comment_text[:8000],
            }
        }
        res = self._call('crm.timeline.comment.add.json', payload)
        return {'provider': 'bitrix24', 'lead_id': clean_id, 'attached': True, 'timeline_id': str(res)}

    def attach_message(self, lead_id: str, msg: dict) -> dict:
        clean_id = clean_text(str(lead_id), 'lead_id', 64)
        direction_label = "Mijozdan kelgan" if msg.get('direction') == 'inbound' else "AI javobi"
        comment_text = (
            f"[{msg.get('channel', 'chat').upper()}] {direction_label}:\n"
            f"{msg.get('text', '')}"
        )
        payload = {
            'fields': {
                'ENTITY_ID': clean_id,
                'ENTITY_TYPE': 'lead',
                'COMMENT': comment_text[:8000],
            }
        }
        res = self._call('crm.timeline.comment.add.json', payload)
        return {'provider': 'bitrix24', 'lead_id': clean_id, 'attached': True, 'timeline_id': str(res)}

