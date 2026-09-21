"""CRM integration package for Agent Platform."""
from .crm_contract import (
    CRM_DRIVERS,
    IMPLEMENTED_CRM_DRIVERS,
    QUERY_SEARCH_DRIVERS,
    CANONICAL_STATUSES,
    ONEC_STATUS_MAP,
    REVERSE_ONEC_STATUS_MAP,
    build_path,
    dig,
    find_contacts_by_mode,
    find_leads_by_mode,
    normalize_phone,
    normalize_email,
    plan_crm_fingerprint,
    safe_relative_path,
    search_mode,
    validate_crm_config,
    validate_custom_http_config,
    parse_lead_request,
    parse_deal_request,
)
from .bitrix24_adapter import Bitrix24Adapter
from .kommo_adapter import KommoAdapter
from .onec_adapter import OneCAdapter
from .custom_http_adapter import CustomHTTPAdapter
from .crm_reconcile import CRMReconciler
from .crm_gateway import register_crm_tools

__all__ = [
    'CRM_DRIVERS',
    'IMPLEMENTED_CRM_DRIVERS',
    'QUERY_SEARCH_DRIVERS',
    'CANONICAL_STATUSES',
    'ONEC_STATUS_MAP',
    'REVERSE_ONEC_STATUS_MAP',
    'build_path',
    'dig',
    'find_contacts_by_mode',
    'find_leads_by_mode',
    'normalize_phone',
    'normalize_email',
    'plan_crm_fingerprint',
    'safe_relative_path',
    'search_mode',
    'validate_crm_config',
    'validate_custom_http_config',
    'parse_lead_request',
    'parse_deal_request',
    'Bitrix24Adapter',
    'KommoAdapter',
    'OneCAdapter',
    'CustomHTTPAdapter',
    'CRMReconciler',
    'register_crm_tools',
]
