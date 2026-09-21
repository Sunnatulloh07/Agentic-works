"""Owner and operator triggered read-only CRM write reconciliation.

Only positive, exact-match provider evidence settles an uncertain CRM step.
Missing matches, ambiguous multiple records and transport errors never prove
non-creation and will never trigger a blind retry.
Settlement commits step status and external ID atomically within engine.tx().
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional
from ..engine import Conflict, Forbidden, NotFound, encode
from .crm_contract import (
    CRM_DRIVERS,
    IMPLEMENTED_CRM_DRIVERS,
    find_leads_by_mode,
    validate_crm_config,
)
from .bitrix24_adapter import Bitrix24Adapter
from .kommo_adapter import KommoAdapter
from .onec_adapter import OneCAdapter
from .custom_http_adapter import CustomHTTPAdapter

# The provider lookup is read-only, but its width is still a bound: it decides how many
# records are fetched before the exact-match rule runs, and therefore whether an
# ambiguous result can be seen at all. A limit of one would make every duplicate look
# like a unique match -- the failure this whole module exists to prevent.
MAX_CANDIDATE_MATCHES = 5
EXACT_MATCHES_REQUIRED = 1


class CRMReconciler:
    def __init__(self, engine, config_resolver: Callable[[str], dict], adapter_factory: Optional[Callable] = None):
        self.engine = engine
        self.config_resolver = config_resolver
        self.adapter_factory = adapter_factory or self._default_adapter_factory

    def _default_adapter_factory(self, driver: str, raw_config: dict) -> Any:
        if driver == 'bitrix24':
            return Bitrix24Adapter(raw_config)
        elif driver in {'amocrm', 'kommo'}:
            return KommoAdapter(raw_config)
        elif driver == 'onec':
            return OneCAdapter(raw_config)
        elif driver == 'custom_webhook':
            return CustomHTTPAdapter(raw_config)
        raise ValueError(f'Unsupported CRM driver: {driver}')

    def reconcile_step(self, tenant: str, step_id: str, actor: str) -> dict:
        e = self.engine
        with e.read() as db:
            e.require_active(db, tenant)
            e.require_authority(db, tenant, 'web', actor, ('owner', 'operator'))
            
            step = db.execute(
                'SELECT s.*, t.agent FROM p_steps s JOIN p_tasks t ON t.tenant=s.tenant AND t.id=s.task WHERE s.tenant=? AND s.id=?',
                (tenant, step_id)
            ).fetchone()
            if not step:
                raise NotFound(f'Step {step_id} not found')
            if step['status'] != 'uncertain':
                raise Conflict(f'Step {step_id} status is {step["status"]}, but uncertain is required for reconciliation')
            if not step['tool'].startswith('crm.'):
                raise Conflict(f'Step tool {step["tool"]} is not a CRM tool')

        args = json.loads(step['args'])
        connection_name = args.get('connection')
        if not connection_name:
            raise ValueError('Step args missing connection')

        tenant_config = self.config_resolver(tenant)
        connections = tenant_config.get('connections', {})
        raw_conn = connections.get(connection_name)
        if not raw_conn:
            raise NotFound(f'CRM connection {connection_name} not configured')

        validate_crm_config(raw_conn, tenant=tenant, agent=step['agent'], capability='read')
        driver = raw_conn.get('driver')
        adapter = self.adapter_factory(driver, raw_conn)

        req = args.get('request', {})
        phone = req.get('phone')
        email = req.get('email')
        title = req.get('title')

        # 1. Search provider using read-only query. Routing is shared with the gateway
        # so a driver cannot be writable but unreconcilable.
        matches = []
        try:
            query = phone or email or title or ''
            matches = find_leads_by_mode(adapter, driver, query=query, phone=phone,
                                         email=email, limit=MAX_CANDIDATE_MATCHES)
        except Exception as err:
            return {
                'settled': False,
                'status': 'uncertain',
                'reason': f'Provider lookup failed: {err}',
            }

        # 2. Verify positive match
        exact_matches = []
        for m in matches:
            # Check title or exact phone/email match
            if title and m.get('title') == title:
                exact_matches.append(m)
            elif phone and m.get('phone') == phone:
                exact_matches.append(m)

        if len(exact_matches) == EXACT_MATCHES_REQUIRED:
            matched = exact_matches[0]
            matched_id = matched['id']
            receipt = {
                'provider': driver,
                'reconciled': True,
                'lead_id': matched_id,
                'matched_record': matched,
            }
            # 3. Atomically settle step in engine.tx()
            with e.tx() as tx_db:
                # Recheck step status inside tx to avoid race conditions
                cur = tx_db.execute(
                    'SELECT status FROM p_steps WHERE tenant=? AND id=?',
                    (tenant, step_id)
                ).fetchone()
                if not cur or cur['status'] != 'uncertain':
                    raise Conflict('Step status changed during reconciliation')
                
                tx_db.execute(
                    "UPDATE p_steps SET status='succeeded', result=? WHERE tenant=? AND id=?",
                    (encode(receipt), tenant, step_id)
                )
                e.audit(tx_db, tenant, step['task'], 'crm.reconcile_settled', actor, receipt)
            return {
                'settled': True,
                'status': 'succeeded',
                'lead_id': matched_id,
                'receipt': receipt,
            }
        elif len(exact_matches) > EXACT_MATCHES_REQUIRED:
            return {
                'settled': False,
                'status': 'uncertain',
                'reason': f'Ambiguous: found {len(exact_matches)} candidate matches in CRM. Manual operator review required.',
            }
        else:
            return {
                'settled': False,
                'status': 'uncertain',
                'reason': 'No positive matching record found in CRM. Step remains uncertain.',
            }
