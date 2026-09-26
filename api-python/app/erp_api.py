"""HTTP surface for the ERP posting ledger: read the unknown/failed rows, and
let the owner resolve one whose outcome is unknown.

GET  /platform/{tenant}/erp/postings?status=uncertain|unconfirmed|posting|failed|posted
  owner/operator, bounded LIMIT 100. ``platform_runtime.erp`` has no
  status-filtered read (``ledger``/``ledger_page`` return the tenant's whole
  ledger, newest first, with no status argument), so this reads the table
  directly -- the same direct-SQL pattern ``app.platform_api`` already uses
  for ``/audit``, ``/inbox`` and ``/devices`` rather than growing the runtime
  module's public surface for one filtered read.

POST /platform/{tenant}/erp/postings/{posting_id}/reconcile
  body {"outcome": "posted"|"failed", "evidence": 1..1000 chars,
        "external_id": optional, <=128 chars}, owner only.
  Delegates to ``platform_runtime.erp.reconcile_posting`` -- the owner's
  audited verdict on a posting stuck ``uncertain``/``unconfirmed`` after a
  POST whose answer never came back. No Idempotency-Key: the two existing
  reconcile-shaped mutation routes in ``app.platform_api``
  (``/steps/{step}/reconcile``, ``/usage-budget/{reservation}/reconcile``)
  do not require one either, and ``reconcile_posting`` is itself a guarded
  state transition -- it only touches a row whose status is still
  ``uncertain``/``unconfirmed``, so a resubmitted request lands on the same
  Conflict a genuine duplicate would.

Both routes reuse ``app.platform_api.identity`` for authentication/role and
``app.platform_api.call`` for exception mapping, rather than reimplementing
either. ``reconcile_posting`` cannot tell "no such posting" apart from
"posting exists but is not uncertain/unconfirmed" -- both read as the same
``Conflict`` inside its own transaction -- so the existence check below is
done here, before the call, purely to give the two cases their documented
status codes (404 vs 409); a posting belonging to another tenant is
indistinguishable from an unknown one once the query is scoped by tenant,
so it is also 404.
"""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from platform_runtime import erp

from . import platform_api as api

router = APIRouter(prefix='/platform', tags=['erp'])

POSTING_STATUSES = Literal['uncertain', 'unconfirmed', 'posting', 'failed', 'posted']

POSTING_COLUMNS = ('id', 'document', 'driver', 'kind', 'supplier', 'number', 'currency',
                   'total_minor', 'external_id', 'status', 'created', 'settled')

# Declared bounds; pinned literally in runtime_tests/test_erp_api_bounds.py.
POSTING_LIMIT = 100
MAX_EVIDENCE_CHARS = 1000
MAX_EXTERNAL_ID_CHARS = 128


@router.get('/{tenant}/erp/postings')
def erp_postings(tenant: str, request: Request, status: POSTING_STATUSES):
    api.identity(request, tenant, ('owner', 'operator'))
    e = api.engine()
    erp.recover_postings(e, tenant)
    with e.read() as c:
        rows = [dict(row) for row in c.execute(
            f'''SELECT {",".join(POSTING_COLUMNS)} FROM p_erp_postings
                WHERE tenant=? AND status=? ORDER BY created DESC LIMIT {POSTING_LIMIT}''',
            (tenant, status)).fetchall()]
    return {'postings': rows}


class ErpReconcile(api.StrictRequest):
    outcome: Literal['posted', 'failed']
    evidence: str = Field(min_length=api.MIN_NON_EMPTY, max_length=MAX_EVIDENCE_CHARS)
    external_id: str = Field(default='', max_length=MAX_EXTERNAL_ID_CHARS)


@router.post('/{tenant}/erp/postings/{posting_id}/reconcile')
def erp_reconcile(tenant: str, posting_id: str, req: ErpReconcile, request: Request):
    who = api.identity(request, tenant, ('owner',))
    e = api.engine()
    with e.read() as c:
        exists = c.execute('SELECT 1 FROM p_erp_postings WHERE tenant=? AND id=?',
                           (tenant, posting_id)).fetchone()
    if exists is None:
        raise HTTPException(404, 'Posting not found')
    return api.call(erp.reconcile_posting, e, tenant, posting_id, who['sub'], who['role'],
                    req.outcome, req.evidence, external_id=req.external_id)
