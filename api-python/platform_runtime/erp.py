"""ERP posting: the first write that leaves the platform for a financial system.

``documents.posting_plan`` ends at a proposal. This module is the step after it,
and it is the most dangerous code in the repository, because unlike a CRM note a
posting is a financial record that another system will act on. Three rules hold
it together, and each one is enforced rather than documented.

**One: the platform still does not move money.** A posting creates an accounting
document in the customer's ERP. There is no payment, transfer, settlement or
disbursement anywhere in this module, and no vocabulary for one: the payable is
recorded, and paying it remains a human act in the ERP the customer already
trusts. That is a narrower claim than the document block's, so it is measured
separately — see ``probe_erp_posting_boundary.py``, which checks every public
callable and every request body this module can build.

**Two: an incomplete document is refused, never completed.** The user's rule,
stated plainly: write only when the instruction is exact, and never fill a gap
with a guess. So a posting requires the fields the ERP needs to identify the
counterparty, the document and the amount, and if any of them is missing the
attempt is refused *by name*. The model cannot supply a counterparty, an account
or a date that the document does not carry: those fields are read from the
extracted document and from the operator's register, and a model-supplied value
for any of them is an error rather than an override. A guessed counterparty is
worse than a refused posting, because it silently attributes a real debt to the
wrong company.

**Three: a duplicate posting is prevented twice over.** The document block's
duplicate key — ``(kind, supplier, number)`` — is a *detection*; posting needs a
*prevention*, because a second posting pays an invoice twice. So the check runs in
two places: the ERP is asked whether it already holds this document
(``find_posted``), and the platform records its own ledger row keyed by the same
identity under a UNIQUE index. The ERP lookup is authoritative when it answers,
and the local ledger is the backstop for a provider that cannot search by
document number. Both must agree the document is new before anything is sent.

A failure of the ERP search is not treated as "not found". An unreachable ERP
means the platform cannot know whether the invoice is already posted, and sending
in that state is exactly how a double payment happens. The precondition fails
closed.

**Four: the ledger row exists before the POST does.** A row written *after* the
POST cannot survive a process that dies between the two, and the next submit would
post the invoice again. So a claiming ``posting`` row is reserved first, under a
deterministic idempotency key that is also sent to the ERP as a header. A POST
whose outcome never came back -- a timeout, a reset, a 5xx, a crash -- leaves that
row ``uncertain``, and an ``uncertain`` row blocks every retry until the owner
reconciles it (``reconcile_posting``), exactly as the engine treats an external
write that raised. Only a definitive provider refusal (a 4xx other than
408/409/425/429) releases the identity for a retry.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional

from . import cells
from .engine import Conflict, Forbidden, encode
from .tools import NoRedirect, config, secret

ERP_TOOLS = ('erp.posting_prepare', 'erp.posting_submit', 'erp.posting_status')
POSTING_TOOLS = frozenset({'erp.posting_submit'})

MAX_RESPONSE_BYTES = 80_000
MAX_NOTE_CHARS = 500
MAX_AMOUNT_MINOR = 10 ** 15

# The accounting fields a posting needs, in the order an error should name them.
# A posting without these is not a posting we are willing to sign: it would create
# a record in someone's ledger that cannot be reconciled.
REQUIRED_FIELDS = ('supplier', 'number', 'doc_date', 'currency', 'total_minor',
                   'account', 'counterparty')

# Fields the MODEL may never supply, because supplying them is how a wrong debt is
# created. They arrive from the extracted document or from the operator's register,
# and a step argument naming one is refused rather than merged.
OPERATOR_FIELDS = frozenset({'supplier', 'account', 'counterparty'})

ENV_REF_RE = re.compile(r'^[A-Z][A-Z0-9_]*$')
HOST_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?'
                     r'(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$')


def _clean_path(value, name):
    """A relative request path, or a refusal.

    Delegates to the CRM contract's ``safe_relative_path`` rather than re-deriving
    the rules here. That function already exists, is already tested, and encodes
    decisions this module would otherwise have to make again and could make
    differently: no scheme, no authority, no protocol-relative ``//``, no traversal,
    no backslash, no control characters, no placeholders outside the published
    allowlist. A second implementation of a path gate is a second chance to get it
    wrong -- and the first draft of this module proved that, by accepting ``/a/../b``
    because its own regex allowed dots.
    """
    if value == '':
        return ''
    from .crm.crm_contract import safe_relative_path
    return safe_relative_path(value, name, 256)

DRIVERS = ('onec_http', 'custom_http')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS p_erp_postings(
 tenant TEXT NOT NULL, id TEXT NOT NULL, document TEXT NOT NULL,
 driver TEXT NOT NULL, kind TEXT NOT NULL, supplier TEXT NOT NULL,
 number TEXT NOT NULL, currency TEXT NOT NULL, total_minor INTEGER NOT NULL,
 external_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
 request TEXT NOT NULL DEFAULT '', response TEXT NOT NULL DEFAULT '',
 plan TEXT NOT NULL DEFAULT '', created REAL NOT NULL, settled REAL,
 claim_key TEXT,
 PRIMARY KEY(tenant,id));
CREATE INDEX IF NOT EXISTS p_erp_postings_identity
 ON p_erp_postings(tenant,driver,kind,supplier,number);
CREATE UNIQUE INDEX IF NOT EXISTS p_erp_postings_claim
 ON p_erp_postings(tenant,claim_key);
CREATE INDEX IF NOT EXISTS p_erp_postings_document ON p_erp_postings(tenant,document);
'''

# Every outcome except a definitive `failed` occupies the unique claim index.
# `posted`/`skipped_existing` assert the ERP holds the document; `posting` is a
# reservation whose POST is in flight; `uncertain` and `unconfirmed` are POSTs whose
# result is unknown. The last three must block a retry until the owner reconciles,
# because a second POST may create a second document. Only `failed` -- the ERP said
# "not created" -- stays retryable.
CLAIMING_STATUSES = frozenset({'posted', 'skipped_existing', 'posting', 'uncertain',
                               'unconfirmed'})
UNKNOWN_STATUSES = frozenset({'uncertain', 'unconfirmed'})

# A reservation older than this has outlived any POST it could be waiting for
# (timeout_seconds is capped at 60), so its process is gone and the outcome is
# unknown: it becomes `uncertain`, never re-sent.
RESERVATION_STALE_SECONDS = 180

# 4xx answers that do NOT prove the document was not created: a timeout, a
# conflict (possibly "this key is in progress"), too-early and rate limiting.
NOT_DEFINITIVE_4XX = frozenset({408, 409, 425, 429})

HEADER_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9-]{0,63}$')
RESERVED_HEADERS = frozenset({'authorization', 'content-type', 'content-length',
                              'host', 'cookie', 'transfer-encoding', 'connection'})


class ErpError(RuntimeError):
    """A deterministic refusal about a posting, not a transport failure.

    ``status`` carries the provider's HTTP status when there was one, so the POST
    path can tell a definitive refusal from an answer that proves nothing.
    """

    def __init__(self, message='', *, status=None):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------- transport


def default_erp_transport(url: str, body: Optional[Any] = None,
                          headers: Optional[dict] = None, method: str = 'GET',
                          timeout: int = 15) -> Any:
    """HTTPS-only, redirect-refusing transport. Identical contract to the CRM one.

    Kept separate rather than imported so this module's provider surface cannot be
    widened by an edit made for a CRM adapter's benefit: a posting endpoint and a
    lead-search endpoint do not deserve the same trust.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('Explicit HTTPS ERP endpoint required')
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError('ERP endpoint must not embed credentials or a fragment')
    data = encode(body).encode('utf-8') if body is not None else None
    request = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/json', **(headers or {})},
        method=method)
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status == 204:
                return {}
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ErpError('ERP response exceeded maximum allowed bytes (80KB)')
            if not raw:
                return {}
            return json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as error:
        # The provider body is never echoed: an ERP error page routinely contains a
        # stack trace, an internal hostname or a credential fragment.
        raise ErpError(f'ERP HTTP status {error.code}', status=error.code) from None
    except ErpError:
        raise
    except Exception:
        raise ErpError('ERP transport failure') from None


# ---------------------------------------------------------------------- config


def _credential(reference, name):
    if not isinstance(reference, str) or not ENV_REF_RE.match(reference):
        raise ValueError(f'{name} must be an environment variable reference')
    value = os.environ.get(reference, '')
    if not value:
        raise RuntimeError(f'Missing ERP credential in environment variable {reference}')
    return value


def erp_config(tenant) -> dict:
    """The tenant's ERP posting configuration, validated.

    Deliberately separate from ``connections``: a CRM connection is a read-mostly
    integration, and a posting target is a system that will act on what we send.
    Requiring its own declaration means an operator cannot accidentally post to a
    CRM sandbox because it happened to be the only thing configured.
    """
    raw = config(tenant).get('erp_posting')
    if raw is None:
        return {'driver': '', 'enabled': False}
    if not isinstance(raw, dict):
        raise ValueError('erp_posting must be an object')
    unknown = set(raw) - {'driver', 'enabled', 'host', 'base_path', 'timeout_seconds',
                          'auth', 'credential_env', 'token_env', 'basic_auth_env',
                          'post_path', 'search_path', 'response_map', 'accounts',
                          'counterparties', 'allow_auto_submit', 'idempotency_header'}
    if unknown:
        raise ValueError(f'erp_posting has unsupported keys: {sorted(unknown)}')
    driver = raw.get('driver')
    if driver not in DRIVERS:
        raise ValueError(f'erp_posting driver must be one of {list(DRIVERS)}')
    enabled = raw.get('enabled', False)
    if not isinstance(enabled, bool):
        raise ValueError('erp_posting.enabled must be a boolean')

    host = raw.get('host')
    if not isinstance(host, str) or not HOST_RE.match(host):
        raise ValueError('erp_posting.host must be a bare lowercase hostname')
    base_path = raw.get('base_path', '/')
    if not isinstance(base_path, str):
        raise ValueError('erp_posting.base_path must be a string')
    base_path = _clean_path(base_path.rstrip('/') or '/', 'erp_posting.base_path')
    timeout = raw.get('timeout_seconds', 15)
    if type(timeout) is not int or not 1 <= timeout <= 60:
        raise ValueError('erp_posting.timeout_seconds must be an integer 1..60')

    auth = raw.get('auth', 'bearer')
    if auth not in {'bearer', 'basic'}:
        raise ValueError('erp_posting.auth must be bearer or basic')
    if auth == 'basic':
        reference = raw.get('basic_auth_env') or raw.get('credential_env')
        key = 'basic_auth_env'
    else:
        reference = raw.get('token_env') or raw.get('credential_env')
        key = 'token_env'
    if not isinstance(reference, str) or not ENV_REF_RE.match(reference):
        raise ValueError(f'erp_posting.{key} must be an environment variable reference')

    for name in ('post_path', 'search_path'):
        value = raw.get(name, '')
        if not isinstance(value, str):
            raise ValueError(f'erp_posting.{name} must be a string')
        _clean_path(value, f'erp_posting.{name}')

    response_map = raw.get('response_map', {})
    if not isinstance(response_map, dict):
        raise ValueError('erp_posting.response_map must be an object')
    accounts = _mapping(raw.get('accounts'), 'accounts')
    counterparties = _mapping(raw.get('counterparties'), 'counterparties')
    allow_auto = raw.get('allow_auto_submit', False)
    if not isinstance(allow_auto, bool):
        raise ValueError('erp_posting.allow_auto_submit must be a boolean')
    # The header that carries the reservation's idempotency key. Sent by default
    # under the IETF name; an ERP that expects another name declares it, and ''
    # turns it off for a gateway that refuses unknown headers.
    idempotency_header = raw.get('idempotency_header', 'Idempotency-Key')
    if idempotency_header != '' and (
            not isinstance(idempotency_header, str)
            or not HEADER_NAME_RE.match(idempotency_header)
            or idempotency_header.lower() in RESERVED_HEADERS):
        raise ValueError('erp_posting.idempotency_header must be a plain header name '
                         'other than Authorization/Content-Type/Host, or empty')
    return {'driver': driver, 'enabled': enabled, 'host': host,
            'base_path': base_path.rstrip('/') or '/', 'timeout_seconds': timeout,
            'auth': auth, 'credential_env': reference, 'post_path': raw.get('post_path', ''),
            'search_path': raw.get('search_path', ''), 'response_map': response_map,
            'accounts': accounts, 'counterparties': counterparties,
            'allow_auto_submit': allow_auto, 'idempotency_header': idempotency_header}


def _mapping(value, name):
    """An operator-declared alias table: keys and values are bounded strings.

    Used for the account and counterparty registers. A model names an alias that
    the operator declared; it never names a raw account code, because a one-digit
    slip in an account code is a misposting nobody notices until an audit.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f'erp_posting.{name} must be an object')
    out = {}
    for alias, target in value.items():
        if not isinstance(alias, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}', alias):
            raise ValueError(f'erp_posting.{name} has an invalid alias: {alias!r}')
        if not isinstance(target, str) or not target or len(target) > 64:
            raise ValueError(f'erp_posting.{name}.{alias} must be a bounded string')
        out[alias] = target
    return out


# ------------------------------------------------------------- field resolution


def _text(value, name, maximum=200, *, required=True):
    if value is None or value == '':
        if required:
            raise ErpError(f'{name} is required for a posting and the document did not carry it')
        return ''
    if not isinstance(value, str):
        value = str(value)
    text = value.strip()
    if not text:
        if required:
            raise ErpError(f'{name} is required for a posting and the document did not carry it')
        return ''
    if len(text) > maximum:
        raise ErpError(f'{name} exceeds {maximum} characters')
    return text


def _date_text(value):
    """An ERP date the platform is willing to sign.

    Ambiguity is refused here for the same reason the document block refuses
    ``10.01.2026``: guessing shifts a posted document into the wrong period, and a
    posted document in the wrong period is a tax problem.

    Two forms are accepted, and both are validated rather than pattern-matched: an
    ISO date must be a **real** calendar date (``2026-13-01`` matches the shape and
    is not a day), and a numeric triple is accepted only when **exactly one** part
    is greater than 12, which is what makes the reading unique. Two parts greater
    than 12 name no month at all, and two parts of 12 or less are genuinely
    ambiguous; both are refused, and they are refused with different reasons.

    Both forms are additionally required to be written in **ASCII digits**. This
    is not pedantry: ``\\d`` in Python matches every Unicode decimal digit, and
    ``int()`` normalises them, so ``'१३.01.2026'`` (Devanagari) used to pass the
    uniqueness test and be returned as the string ``'2026-01-13'`` -- a date this
    platform was never given. Returning a value that was not in the source is the
    one thing this function must never do, because the posting, the duplicate
    ledger and the ERP all key off it. A non-ASCII date is refused by name
    instead. See ``platform_runtime.cells``.
    """
    text = _text(value, 'doc_date', 32)
    if not cells.is_ascii_digit_run(text.replace('-', '').replace('.', '')
                                    .replace('/', '')):
        raise ErpError(
            f'doc_date {text!r} is not written in ASCII digits. The platform will '
            f'not read a date it cannot echo back exactly: a document posted into '
            f'the wrong period is a tax problem')
    iso = re.fullmatch(r'([0-9]{4})-([0-9]{2})-([0-9]{2})', text)
    if iso:
        year, month, day = (int(part) for part in iso.groups())
        if not _real_date(year, month, day):
            raise ErpError(f'doc_date {text!r} is not a real calendar date')
        return text
    match = re.fullmatch(r'([0-9]{1,2})[./-]([0-9]{1,2})[./-]([0-9]{4})', text)
    if match:
        first, second, year = (int(part) for part in match.groups())
        if first > 12 and second <= 12:
            day, month = first, second
        elif second > 12 and first <= 12:
            day, month = second, first
        elif first > 12:
            # Both parts are greater than 12, so NEITHER can be a month: the reading
            # is not ambiguous, it is impossible. Reporting "ambiguous" here would
            # prescribe a remedy -- "send a form where one part is greater than 12"
            # -- that this very input already satisfies, sending the operator in a
            # circle. Measured, not assumed: see the ultra-audit, fazza 25.
            raise ErpError(
                f'doc_date {text!r} names no month: both parts are greater than 12. '
                f'The platform will not guess a posting date; send an ISO date '
                f'(YYYY-MM-DD)')
        else:
            # Both parts are <= 12, so the reading is genuinely ambiguous and the
            # platform declines to pick one. Refusing is cheap; a misposted period
            # is discovered by an auditor.
            raise ErpError(
                f'doc_date {text!r} is ambiguous: both parts could be a month. The '
                f'platform will not guess a posting date. Send an ISO date '
                f'(YYYY-MM-DD) or a form where one part is greater than 12')
        if not _real_date(year, month, day):
            raise ErpError(f'doc_date {text!r} is not a real calendar date')
        return f'{year:04d}-{month:02d}-{day:02d}'
    raise ErpError(
        f'doc_date {text!r} is unsupported. The platform will not guess a posting '
        f'date: a document posted into the wrong period is a tax problem')


def _real_date(year, month, day):
    import calendar
    if not 1 <= month <= 12 or not 1 <= day <= 31 or not 1900 <= year <= 2999:
        return False
    return day <= calendar.monthrange(year, month)[1]


def _amount(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ErpError('total_minor must be an integer count of minor units')
    if not 0 <= value <= MAX_AMOUNT_MINOR:
        raise ErpError(f'total_minor must be between 0 and {MAX_AMOUNT_MINOR}')
    return value


def resolve_posting(tenant, document, *, account='', counterparty=''):
    """Turn an extracted document into the exact fields a posting will carry.

    Fails closed and names what is missing. **No field is inferred**: not the
    account, not the counterparty, not the date, not the currency. The two values
    that do not live in the document -- the account and the counterparty -- must be
    operator-declared aliases, and an alias the operator never declared is refused
    rather than passed through to the ERP.
    """
    settings = erp_config(tenant)
    if not settings['driver']:
        raise Forbidden('ERP posting is not configured for this tenant')
    if not settings['enabled']:
        raise Forbidden('ERP posting is disabled for this tenant')

    if not isinstance(document, dict):
        raise ErpError('document must be an object')

    # A missing field and a malformed one are collected separately, and that split is
    # deliberate. "We could not read the amount" and "the amount you sent is not a
    # number" send an operator to two different places, and flattening them into one
    # "incomplete" message hides a typo behind a shrug. A malformed value is raised
    # immediately with its own reason; only genuine absence joins the missing list.
    missing = []
    fields = {}
    for name in ('supplier', 'number'):
        if document.get(name) in (None, ''):
            missing.append(name)
        else:
            fields[name] = _text(document.get(name), name, 200)
    if document.get('doc_date') in (None, ''):
        missing.append('doc_date')
    else:
        fields['doc_date'] = _date_text(document.get('doc_date'))
    if document.get('currency') in (None, ''):
        missing.append('currency')
    else:
        currency = _text(document.get('currency'), 'currency', 8)
        if not re.fullmatch(r'[A-Z]{3}', currency):
            raise ErpError('currency must be a three-letter code such as UZS')
        fields['currency'] = currency
    if document.get('total_minor') in (None, ''):
        missing.append('total_minor')
    else:
        fields['total_minor'] = _amount(document.get('total_minor'))

    # The account and the counterparty come from the operator's registers, keyed by
    # an alias. They are never read from the document, because the document is
    # attacker-influenced input: a supplier invoice that names its own ledger
    # account is a supplier choosing where its own debt lands.
    alias = _text(account, 'account', 64, required=False)
    if not alias:
        missing.append('account')
    elif alias not in settings['accounts']:
        raise ErpError(f'account alias {alias!r} is not declared by the operator')
    else:
        fields['account'] = settings['accounts'][alias]

    party_alias = _text(counterparty, 'counterparty', 64, required=False)
    if not party_alias:
        missing.append('counterparty')
    elif party_alias not in settings['counterparties']:
        raise ErpError(f'counterparty alias {party_alias!r} is not declared by the operator')
    else:
        fields['counterparty'] = settings['counterparties'][party_alias]

    if missing:
        raise ErpError(
            'refusing to post an incomplete document: ' + ', '.join(sorted(missing))
            + '. The platform will not guess a field a posting needs; supply the '
              'missing values from the document or the operator register.')
    return {'driver': settings['driver'], **{k: fields[k] for k in REQUIRED_FIELDS}}


# ----------------------------------------------------------------------- ledger


def identity(posting):
    """The key a duplicate posting must collide on.

    Same triple the document block dedups on, so the two agree by construction
    rather than by coincidence. The amount is deliberately excluded, exactly as the
    document block excludes it: the same invoice number for a different amount is
    *more* suspicious, and a key that changed with the amount would let a re-issued
    total slip past as a new document.
    """
    return (posting['driver'], 'invoice', posting['supplier'], posting['number'])


def posted(engine, tenant, posting):
    """The ledger row that *claims* this document, or ``None``.

    Every status in ``CLAIMING_STATUSES`` counts: a posted document, an in-flight
    reservation, and a POST whose outcome is unknown. Only a ``failed`` row -- the
    ERP refused the document -- is left out, so a refusal stays retryable while an
    unknown outcome waits for the owner (``reconcile_posting``). The failed row is
    still stored and still visible through ``ledger``.
    """
    driver, kind, supplier, number = identity(posting)
    with engine.read() as cursor:
        row = cursor.execute(
            '''SELECT id,external_id,status,created,settled FROM p_erp_postings
               WHERE tenant=? AND driver=? AND kind=? AND supplier=? AND number=?
                 AND status IN ({})'''.format(
                ','.join('?' * len(CLAIMING_STATUSES))),
            (tenant, driver, kind, supplier, number,
             *sorted(CLAIMING_STATUSES))).fetchone()
    return dict(row) if row is not None else None


def ledger(engine, tenant, limit=50, document=''):
    rows = []
    with engine.read() as cursor:
        if document:
            cursor_rows = cursor.execute(
                '''SELECT id,document,driver,kind,supplier,number,currency,total_minor,
                          external_id,status,created,settled FROM p_erp_postings
                   WHERE tenant=? AND document=? ORDER BY created DESC LIMIT ?''',
                (tenant, document, limit)).fetchall()
        else:
            cursor_rows = cursor.execute(
                '''SELECT id,document,driver,kind,supplier,number,currency,total_minor,
                          external_id,status,created,settled FROM p_erp_postings
                   WHERE tenant=? ORDER BY created DESC LIMIT ?''',
                (tenant, limit)).fetchall()
    return [dict(row) for row in cursor_rows]


def ledger_page(engine, tenant, limit=50, document=''):
    """One page of the posting ledger, plus the population it was taken from.

    Returns ``(rows, total, truncated)``. ``ledger()`` above returns the rows
    alone, which is the right shape for a caller that has already decided how many
    it wants; it is the wrong shape for a tool whose answer is read as an
    inventory, because a bare list cannot say whether it is the whole ledger. The
    count is taken in the same read as the page so the two cannot disagree, and
    ``truncated`` is `total > len(rows)` -- a comparison against the population
    rather than against the limit, so a ledger holding exactly `limit` rows is
    reported as complete instead of as cut.
    """
    with engine.read() as cursor:
        if document:
            where, params = 'tenant=? AND document=?', (tenant, document)
        else:
            where, params = 'tenant=?', (tenant,)
        total = cursor.execute(
            f'SELECT count(*) n FROM p_erp_postings WHERE {where}', params).fetchone()['n']
        cursor_rows = cursor.execute(
            f'''SELECT id,document,driver,kind,supplier,number,currency,total_minor,
                       external_id,status,created,settled FROM p_erp_postings
                WHERE {where} ORDER BY created DESC LIMIT ?''',
            (*params, limit)).fetchall()
    rows = [dict(row) for row in cursor_rows]
    return rows, total, total > len(rows)


def _record(engine, tenant, posting, document_id, plan, status, external_id,
            request_body, response, *, settled=None):
    """Write the ledger row. The UNIQUE identity index is the second guard.

    If the index fires, a concurrent submit already recorded this document, and
    ``Conflict`` is raised so the caller reports a duplicate rather than a second
    posting. That is the difference between a check and a guarantee: the ERP lookup
    can be raced, an index cannot.

    **Not every row claims the identity**, and the distinction is load-bearing. A row
    whose outcome is ``failed`` or ``unconfirmed`` must NOT occupy the unique index,
    because a failed attempt has to remain retryable and an unconfirmed one may or
    may not have posted. Only an outcome that asserts "the ERP holds this document"
    -- ``posted``, or ``skipped_existing`` because the ERP already had it -- is
    allowed to block the identity. Otherwise a single transport blip would
    permanently prevent a legitimate invoice from ever being posted, and the
    operator's only recourse would be editing the database.
    """
    import uuid
    e = engine
    row_id = uuid.uuid4().hex[:24]
    driver, kind, supplier, number = identity(posting)
    # A blank key is stored as NULL, and SQLite treats NULLs as distinct, so several
    # non-claiming rows can coexist while exactly one claiming row can exist.
    claiming = status in CLAIMING_STATUSES
    with e.tx() as cursor:
        e.require_active(cursor, tenant)
        try:
            cursor.execute(
                '''INSERT INTO p_erp_postings
                   (tenant,id,document,driver,kind,supplier,number,currency,
                    total_minor,external_id,status,request,response,plan,created,settled,
                    claim_key)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (tenant, row_id, document_id, driver, kind, supplier, number,
                 posting['currency'], posting['total_minor'], external_id, status,
                 encode(request_body), encode(response), encode(plan), e.clock(), settled,
                 f'{driver}|{kind}|{supplier}|{number}' if claiming else None))
        except Exception as error:
            if 'UNIQUE' in str(error).upper():
                raise Conflict(
                    f'document {supplier}/{number} has already been posted to '
                    f'{driver}; refusing a second posting') from None
            raise
    return row_id


def _claim_key(posting):
    driver, kind, supplier, number = identity(posting)
    return f'{driver}|{kind}|{supplier}|{number}'


def idempotency_key(tenant, posting, attempt):
    """The deterministic key of one posting attempt: the reservation's row id.

    Derived from the tenant, the document identity and the attempt number, so the
    same attempt always carries the same key -- to the ERP, in the ledger and to an
    owner reconciling by hand -- and needs no new column. The attempt number moves
    only after a DEFINITIVE refusal, because an idempotency-aware ERP replays its
    stored answer for a reused key and would refuse a corrected retry forever.
    """
    import hashlib
    driver, kind, supplier, number = identity(posting)
    material = '\x1f'.join((tenant, driver, kind, supplier, number, str(attempt)))
    return hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]


def recover_postings(engine, tenant):
    """Turn every reservation that outlived its POST into ``uncertain``.

    A ``posting`` row older than ``RESERVATION_STALE_SECONDS`` belongs to a process
    that died between the POST and the ledger update. Its outcome is unknown, so it
    is never re-sent: it becomes ``uncertain`` and keeps the claim until the owner
    reconciles it. Run lazily by every path that reads or writes the ledger, which
    is the only way a retry could reach the ERP. Returns the number of rows moved.
    """
    with engine.tx() as cursor:
        moved = cursor.execute(
            '''UPDATE p_erp_postings SET status='uncertain', response=?
               WHERE tenant=? AND status='posting' AND created<=?''',
            (encode({'error': 'reservation_expired'}), tenant,
             engine.clock() - RESERVATION_STALE_SECONDS)).rowcount
    return moved


def _reserve(engine, tenant, posting, document_id, plan, request_body):
    """Write the claiming ``posting`` row BEFORE any byte leaves for the ERP.

    The unique claim index and the primary key both collide for a concurrent
    reservation of the same document, so two submits cannot both reach the POST.
    """
    driver, kind, supplier, number = identity(posting)
    with engine.tx() as cursor:
        engine.require_active(cursor, tenant)
        attempt = cursor.execute(
            '''SELECT count(*) n FROM p_erp_postings
               WHERE tenant=? AND driver=? AND kind=? AND supplier=? AND number=?''',
            (tenant, driver, kind, supplier, number)).fetchone()['n']
        row_id = idempotency_key(tenant, posting, attempt)
        try:
            cursor.execute(
                '''INSERT INTO p_erp_postings
                   (tenant,id,document,driver,kind,supplier,number,currency,
                    total_minor,external_id,status,request,response,plan,created,settled,
                    claim_key)
                   VALUES(?,?,?,?,?,?,?,?,?,'','posting',?,'',?,?,NULL,?)''',
                (tenant, row_id, document_id, driver, kind, supplier, number,
                 posting['currency'], posting['total_minor'], encode(request_body),
                 encode(plan), engine.clock(), _claim_key(posting)))
        except Exception as error:
            if 'UNIQUE' in str(error).upper():
                raise Conflict(
                    f'document {supplier}/{number} is already being posted to '
                    f'{driver}; refusing a concurrent posting') from None
            raise
    return row_id


def _finish(engine, tenant, row_id, status, external_id, response):
    """Record the POST's outcome on its reservation.

    Deliberately not gated on the tenant being active: the POST already happened,
    and losing its answer is worse than recording it during a freeze. A late answer
    may also land on a reservation that recovery already made ``uncertain`` -- the
    provider's own answer is better evidence than "unknown". A ``failed`` outcome
    releases the claim so the document can be retried.
    """
    with engine.tx() as cursor:
        cursor.execute(
            '''UPDATE p_erp_postings SET status=?, external_id=?, response=?, settled=?,
                      claim_key=CASE WHEN ?='failed' THEN NULL ELSE claim_key END
               WHERE tenant=? AND id=? AND status IN ('posting','uncertain')''',
            (status, external_id, encode(response), engine.clock(), status,
             tenant, row_id))


def _definitive_refusal(error):
    """True only when the ERP answered and the answer proves nothing was created."""
    status = getattr(error, 'status', None)
    return (isinstance(error, ErpError) and type(status) is int
            and 400 <= status < 500 and status not in NOT_DEFINITIVE_4XX)


def reconcile_posting(engine, tenant, posting_id, actor, role, outcome, evidence, *,
                      external_id=''):
    """The owner's verdict on a posting whose outcome is unknown.

    Mirrors ``Engine.reconcile``: owner only, evidence required, and only a row in
    an unknown state (``uncertain`` or ``unconfirmed``) can be decided. ``posted``
    records the ERP's document id and keeps the claim; ``failed`` says the ERP does
    not hold the document and releases the claim so it can be posted again.
    """
    if role not in {'owner', 'super-admin'}:
        raise Forbidden('Owner required')
    if outcome not in {'posted', 'failed'}:
        raise ValueError('outcome must be posted or failed')
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 500:
        raise ValueError('Evidence required, maximum 500 characters')
    if outcome == 'posted' and (not isinstance(external_id, str)
                                or not external_id.strip() or len(external_id) > 128):
        raise ValueError('a posted verdict needs the ERP document id (max 128 characters)')
    recover_postings(engine, tenant)
    with engine.tx() as cursor:
        engine.require_authority(cursor, tenant, 'web', actor, ('owner',))
        row = cursor.execute(
            '''SELECT id,driver,kind,supplier,number,status FROM p_erp_postings
               WHERE tenant=? AND id=? AND status IN ('uncertain','unconfirmed')''',
            (tenant, posting_id)).fetchone()
        if row is None:
            raise Conflict('Posting is not in an unknown state')
        claim = f'{row["driver"]}|{row["kind"]}|{row["supplier"]}|{row["number"]}'
        try:
            cursor.execute(
                '''UPDATE p_erp_postings SET status=?, external_id=?, settled=?, claim_key=?
                   WHERE tenant=? AND id=?''',
                (outcome, external_id.strip() if outcome == 'posted' else '',
                 engine.clock(), claim if outcome == 'posted' else None,
                 tenant, posting_id))
        except Exception as error:
            if 'UNIQUE' in str(error).upper():
                raise Conflict('another row already claims this document') from None
            raise
        engine.audit(cursor, tenant, '', 'erp.posting_reconciled', actor,
                     {'posting': posting_id, 'from': row['status'], 'outcome': outcome,
                      'evidence': evidence[:500]})
    return {'id': posting_id, 'status': outcome,
            'external_id': external_id.strip() if outcome == 'posted' else ''}


# ------------------------------------------------------------------- adapters


def _auth_headers(settings):
    mode = settings['auth']
    reference = settings['credential_env']
    raw = _credential(reference, 'credential_env')
    if mode == 'basic':
        user, separator, password = raw.partition(':')
        if not separator or not user or not password:
            raise ValueError('ERP basic credential must be user:password')
        import base64
        token = base64.b64encode(raw.encode('utf-8')).decode('ascii')
        return {'Authorization': 'Basic ' + token}
    return {'Authorization': 'Bearer ' + raw.strip()}


def _dig(document, pointer, default=None):
    """Resolve one dotted pointer, depth-bounded."""
    if not isinstance(pointer, str) or not pointer:
        return default
    current = document
    for depth, segment in enumerate(pointer.split('.')):
        if depth > 3:
            return default
        if not isinstance(current, dict) or segment not in current:
            return default
        current = current[segment]
    return default if current is None else current


def posting_body(settings, posting, note=''):
    """The exact JSON a posting will carry.

    Built here, from resolved fields only, so the boundary probe can inspect the
    complete set of values this module is capable of sending. Nothing is added
    later: no caller-supplied extra, no passthrough of a model field.

    ``total_minor`` is converted to the ERP's own minor-unit expectation rather
    than sent as a float. A float posted to a ledger is a rounding error waiting to
    be found during reconciliation.
    """
    body = {
        'document_type': 'invoice',
        'counterparty': posting['counterparty'],
        'account': posting['account'],
        'document_number': posting['number'],
        'document_date': posting['doc_date'],
        'amount': posting['total_minor'],
        'currency': posting['currency'],
        'counterparty_name': posting['supplier'],
        'comment': _text(note, 'note', MAX_NOTE_CHARS, required=False),
    }
    return {key: value for key, value in body.items() if value != ''}


def find_posted(settings, posting, transport):
    """Ask the ERP whether it already holds this document.

    Fails **closed**: an unreadable search raises rather than returning "not
    found". Not being able to ask is not the same as the answer being no, and the
    difference is a double payment.

    A **credential** failure is re-raised as itself rather than folded into the
    generic message, because it is not a provider problem at all. Wrapping a missing
    ``ERP_TOKEN`` into "could not ask the ERP" sends an operator to investigate the
    ERP when the fix is an environment variable, and the message they need is the
    name of the variable.
    """
    headers = _auth_headers(settings)
    path = settings['search_path']
    if not path:
        return None
    query = urllib.parse.urlencode({
        'number': posting['number'],
        'counterparty': posting['counterparty'],
    })
    url = f'https://{settings["host"]}{settings["base_path"]}{path}?{query}'
    try:
        document = transport(url, headers=headers, method='GET',
                             timeout=settings['timeout_seconds'])
    except (RuntimeError, ValueError) as error:
        if type(error).__name__ in ('RuntimeError', 'ValueError'):
            # Configuration and credential problems are already the precise,
            # actionable answer; re-raising keeps the variable name.
            raise
        raise ErpError(str(error)) from None
    except Exception as error:
        raise ErpError(
            f'could not ask the ERP whether {posting["number"]!r} is already posted '
            f'({type(error).__name__}). Sending without that answer is how a document '
            f'is posted twice, so the posting is refused') from None
    if not isinstance(document, dict):
        raise ErpError('ERP search response was not an object')
    pointer = settings['response_map'].get('existing_id', 'id')
    existing = _dig(document, pointer)
    if existing:
        return str(existing)
    items = settings['response_map'].get('items', 'items')
    found = _dig(document, items)
    if isinstance(found, list) and found:
        return str(_dig(found[0], 'id', found[0]) or 'found')
    return None


def submit(engine, tenant, agent, posting, *, document_id='', plan=None, note='',
           transport=None, actor='system'):
    """Post one document to the ERP. The only write this module performs.

    Order matters and is the whole design:

    1. resolve — a field the document does not carry ends it here, before any I/O;
    2. ledger — our own record of the identity; a hit ends it here, and so does an
       in-flight reservation or an unknown outcome awaiting the owner;
    3. ERP search — the provider's own answer; a hit ends it here;
    4. reserve — a claiming ``posting`` row under a deterministic idempotency key;
    5. only then POST (carrying that key), and record the result on the reservation.

    Steps 2 and 3 are both present on purpose. The ledger cannot see a posting made
    by another system, and the ERP search cannot be trusted to be atomic with the
    POST. Each covers what the other misses. Step 4 is what makes "once" survive a
    crash: the row exists before the POST, so a POST whose answer is lost leaves an
    ``uncertain`` claim behind instead of nothing.
    """
    settings = erp_config(tenant)
    if not settings['driver']:
        raise Forbidden('ERP posting is not configured for this tenant')
    if not settings['enabled']:
        raise Forbidden('ERP posting is disabled for this tenant')

    recover_postings(engine, tenant)
    existing = posted(engine, tenant, posting)
    if existing is not None:
        who = f'document {posting["supplier"]}/{posting["number"]}'
        if existing['status'] == 'posting':
            raise Conflict(
                f'{who} is in flight: posting {existing["id"]} was reserved and its ERP '
                f'answer has not arrived; refusing a concurrent posting')
        if existing['status'] in UNKNOWN_STATUSES:
            raise Conflict(
                f'{who} has a posting whose outcome is unknown (status '
                f'{existing["status"]!r}, idempotency key {existing["id"]}). The owner '
                f'must reconcile it against the ERP before any retry, because a second '
                f'POST may post the invoice twice')
        raise Conflict(
            f'{who} was already posted '
            f'by this platform (status {existing["status"]!r}); refusing a second posting')

    transport = transport or default_erp_transport
    remote = find_posted(settings, posting, transport)
    if remote:
        # Recorded, not just refused: the operator must be able to see that the ERP
        # held this document and the platform declined to duplicate it.
        row_id = _record(engine, tenant, posting, document_id, plan or {},
                         'skipped_existing', remote, {}, {'existing_id': remote})
        return {'posted': False, 'duplicate': True, 'reason': 'already_in_erp',
                'external_id': remote, 'posting_id': row_id}

    body = posting_body(settings, posting, note)
    path = settings['post_path']
    if not path:
        raise Forbidden('erp_posting.post_path is not declared')
    url = f'https://{settings["host"]}{settings["base_path"]}{path}'
    # Everything that can fail without I/O fails here, before the reservation, so a
    # missing credential never leaves a claim behind.
    headers = _auth_headers(settings)
    row_id = _reserve(engine, tenant, posting, document_id, plan or {}, body)
    if settings['idempotency_header']:
        headers[settings['idempotency_header']] = row_id
    try:
        response = transport(url, body=body, headers=headers,
                             method='POST', timeout=settings['timeout_seconds'])
    except Exception as error:
        # The outcome is recorded BEFORE the exception propagates. Only a definitive
        # refusal is `failed` (and retryable); anything else -- a timeout, a reset,
        # a 5xx, an unreadable body -- may have created the document, so it is
        # `uncertain` and blocks a retry until the owner reconciles it.
        definitive = _definitive_refusal(error)
        _finish(engine, tenant, row_id, 'failed' if definitive else 'uncertain', '',
                {'error': type(error).__name__,
                 'status': getattr(error, 'status', None)})
        raise

    pointer = settings['response_map'].get('created_id', '')
    external = _dig(response, pointer) if pointer else None
    if not external and isinstance(response, dict):
        for key in ('id', 'Ref_Key', 'Number', 'document_id'):
            external = response.get(key)
            if external:
                break
    if not external:
        # A 2xx without an identifier cannot prove what was created, and an
        # unverifiable financial write must not be reported as settled. The row is
        # recorded as unconfirmed -- still claiming -- so the owner reconciles it.
        _finish(engine, tenant, row_id, 'unconfirmed', '', response)
        raise Conflict(
            'ERP accepted the posting but returned no document identifier. The attempt '
            'was recorded as unconfirmed: reconcile it in the ERP by hand rather than '
            'retrying, because a second attempt may post the invoice twice')

    _finish(engine, tenant, row_id, 'posted', str(external), response)
    return {'posted': True, 'duplicate': False, 'external_id': str(external),
            'posting_id': row_id, 'document': {'supplier': posting['supplier'],
                                               'number': posting['number'],
                                               'date': posting['doc_date'],
                                               'currency': posting['currency'],
                                               'total_minor': posting['total_minor']},
            'moves_money': False}


# ---------------------------------------------------------------- tool handlers


def _prepare_tool(engine, tenant, agent, args, step):
    """Read-only: resolve a document into the exact posting fields, or refuse.

    Exists so an operator can see *before* approving what would be sent, including
    which fields are missing. A refusal here is the useful outcome, not a failure.
    """
    from .documents import documents_config as _docs_config
    if 'erp.posting_prepare' not in engine.policy(tenant, agent).get('tools', []):
        raise Forbidden('erp.posting_prepare not permitted for agent')
    fields = _json_arg(args, 'document', required=True)
    try:
        posting = resolve_posting(tenant, fields, account=args.get('account', ''),
                                  counterparty=args.get('counterparty', ''))
    except ErpError as error:
        return {'ready': False, 'reason': str(error), 'driver': '',
                'would_send': None, 'ledger': None, 'in_erp': None}
    recover_postings(engine, tenant)
    existing = posted(engine, tenant, posting)
    settings = erp_config(tenant)
    if existing is None:
        reason = ''
    elif existing['status'] in ('posted', 'skipped_existing'):
        reason = 'already posted by this platform'
    else:
        reason = (f'a posting of this document is {existing["status"]}; nothing may be '
                  f'sent until it is answered or the owner reconciles it')
    return {'ready': existing is None, 'reason': reason,
            'driver': settings['driver'],
            'would_send': posting_body(settings, posting, args.get('note', '')),
            'ledger': existing,
            'in_erp': None if existing is not None else 'not checked (use '
                                                        'erp.posting_submit to check)',
            'approver_role': _docs_config(tenant).get('approver_role', 'owner')}


def _submit_tool(engine, tenant, agent, args, step):
    """Write, approval-gated. Posts one document, or refuses.

    The posting is recomputed from the arguments rather than trusted from a stored
    plan, for the same reason ``document.posting_plan`` recomputes: an approved step
    replays its arguments, and a caller who could approve a clean plan and then
    submit a different one under the same fingerprint would have found the hole in
    the approval pattern.
    """
    if 'erp.posting_submit' not in engine.policy(tenant, agent).get('tools', []):
        raise Forbidden('erp.posting_submit not permitted for agent')
    fields = _json_arg(args, 'document', required=True)
    posting = resolve_posting(tenant, fields, account=args.get('account', ''),
                              counterparty=args.get('counterparty', ''))
    plan = _json_arg(args, 'plan')
    return submit(engine, tenant, agent, posting,
                  document_id=str(args.get('document_id', '')),
                  plan=plan, note=args.get('note', ''), actor=agent)


def _status_tool(engine, tenant, agent, args, step):
    if 'erp.posting_status' not in engine.policy(tenant, agent).get('tools', []):
        raise Forbidden('erp.posting_status not permitted for agent')
    limit = args.get('limit', 20)
    if limit is None:
        limit = 20
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError('limit must be an integer')
    limit = min(max(1, limit), 200)
    settings = erp_config(tenant)
    recover_postings(engine, tenant)
    rows, total, truncated = ledger_page(engine, tenant, limit,
                                         str(args.get('document_id', '')))
    return {'driver': settings['driver'], 'enabled': settings['enabled'],
            # `count` is the size of the ledgers, not the size of this page. It
            # used to be `len(rows)`, which reported 20 for a ledger of twenty and
            # 20 for a ledger of four thousand: an answer that reads as an
            # inventory and is really a page size. `returned` now carries the page
            # length so the two can never be confused, and `truncated` says whether
            # anything was left out.
            'postings': rows, 'count': total, 'returned': len(rows),
            'truncated': truncated}


def _json_arg(args, key, *, required=False):
    """Decode a structured argument that travelled as JSON text.

    Same contract and same reason as ``documents._payload``: the registry refuses a
    bare ``{'type': 'object'}`` property, so a tree-shaped argument cannot be
    declared as a nested object.
    """
    value = args.get(key)
    if value in (None, ''):
        if required:
            raise ValueError(f'{key} is required')
        return None
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ValueError(f'{key} must be a JSON object')
    try:
        parsed = json.loads(value)
    except ValueError:
        raise ValueError(f'{key} must be valid JSON') from None
    if not isinstance(parsed, dict):
        raise ValueError(f'{key} must be a JSON object')
    return parsed


def register_erp_tools(registry):
    """Two reads and one approval-gated write. There is no payment tool.

    ``erp.posting_prepare`` and ``erp.posting_status`` are reads: the first resolves
    a document into the fields a posting would carry so an operator can see the
    refusal before it matters, the second reads the platform's own posting ledger.

    ``erp.posting_submit`` is the write and it is a plain ``write``, so the engine
    requires a human approval for it. Note what is NOT here: no tool retries, no tool
    cancels, no tool reverses, and no tool that settles, pays or transfers. A wrong
    posting is corrected in the ERP by the accountant who owns that ledger, because
    a reversal issued by an agent is a second financial act with the same risk as the
    first.
    """
    from .tools import Tool, obj, string

    payload = string(16000)
    definitions = [
        ('erp.posting_prepare', 'read',
         obj({'document': payload, 'account': string(64), 'counterparty': string(64),
              'note': string(MAX_NOTE_CHARS)}, required=['document']),
         _prepare_tool),
        ('erp.posting_submit', 'write',
         obj({'document': payload, 'account': string(64), 'counterparty': string(64),
              'document_id': string(64), 'plan': payload, 'note': string(MAX_NOTE_CHARS)},
             required=['document']),
         _submit_tool),
        ('erp.posting_status', 'read',
         obj({'document_id': string(64),
              'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200}},
             required=[]),
         _status_tool),
    ]
    for name, risk, schema, handler in definitions:
        if name in registry.items:
            continue
        registry.add(Tool(name, risk, schema, handler, external=(risk != 'read')))
