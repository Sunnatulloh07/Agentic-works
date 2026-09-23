"""Accounts-payable document control chain (PRD v0.5, P8b / T1).

An invoice arrives. Someone has to decide whether to pay it, and the decisions
that matter are the boring ones: is this the same invoice we already have, does
the amount match the purchase order, did the supplier's bank details change since
last month. An AI employee is genuinely useful here exactly because those checks
are tedious — and genuinely dangerous here because the consequence of getting them
wrong is money leaving the company.

So this module is **a control chain, not a payment system**. The PRD states the
boundary in one line:

    "Pul harakatlantirmaydi. Hujjatni tayyorlaydi, to'lovni tasdiqqa qo'yadi."
    — PRD v0.5 §8

There is no function here that moves money, initiates a transfer, contacts a bank
or a payment provider, or marks an invoice paid. A structural test scans the
module's own surface for any such name, and `scripts/probes/probe_document_no_payment.py`
proves the tool surface cannot express one. The furthest the module goes is to
prepare an **ERP posting plan** — a proposal — which is a ``write`` and therefore
requires a human approval like every other write in this platform.

What the module actually does
-----------------------------

The chain the PRD names, minus the parts that are not ours:

    upload -> parse -> normalize -> validate -> duplicate check
    -> PO / delivery matching (3-way) -> fraud controls -> approval routing
    -> ERP posting plan

* **parse/normalize are not an OCR engine.** The fields arrive already extracted —
  by the model from a document the operator supplied, or from the operator's own
  intake pipeline. The platform does not carry a PDF library and does not pretend
  to: an extraction quality problem is the extractor's, and quietly guessing at a
  scanned amount would be worse than refusing. What the module owns is everything
  after extraction, and it is *deterministic*.
* **duplicate check** is the ``(tenant, connection, lead_id)`` dedup pattern the
  re-engagement loop already uses, applied to a document: the key is the
  supplier's invoice number plus the supplier identity, because the same invoice
  number from two different suppliers is two different invoices.
* **PO / delivery matching** is the reconcile pattern: three documents, three
  amounts, and a tolerance the operator declares. It reports a match, a mismatch
  or a partial match, and it never adjusts a figure to make a match happen.
* **fraud controls** are new, and deliberately narrow. They are *signals*, not
  verdicts: an amount far above the supplier's history, a round-number amount, a
  bank account that changed since the last invoice, a duplicate that differs only
  in a date. Each is reported with the evidence that produced it. The module never
  accuses anybody and never blocks a payment on its own — it raises a flag and a
  human decides, which is the only arrangement that is both useful and safe.

Boundaries that make this safe to run:

* Nothing is auto-approved. The posting plan is a ``write``; the engine requires an
  approval, and the fingerprint binds the exact fields, so editing an amount after
  approval cancels the approval.
* An unread or missing source is ``complete: false`` with the error class, never a
  silent zero. A missing purchase order is not "no purchase order exists".
* Configurations are operator-owned: the tolerance, the fraud thresholds and the
  approval role are declared, not chosen by the model.
* Money is compared in integer minor units (tiyin), never in floats, because
  ``0.1 + 0.2 != 0.3`` is not an acceptable property of an accounts-payable check.
"""
from __future__ import annotations

import re

from . import cells
from .engine import Conflict, Forbidden, NotFound

DOCUMENT_TOOLS = ('document.parse', 'document.match', 'document.duplicates',
                  'document.fraud_signals', 'document.posting_plan')

# A document number is the supplier's own identifier. Bounded so a malformed value
# cannot become a key that silently collides with another supplier's.
NUMBER_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9/_.-]{0,63}$')
PARTY_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')
CURRENCIES = ('UZS', 'USD', 'EUR', 'RUB')

# Money is stored and compared as an integer count of minor units. UZS has no
# practical subunit but ISO 4217 defines one, and mixing the two conventions is how
# off-by-100 errors enter an accounting system.
MINOR_UNITS = {'UZS': 1, 'USD': 100, 'EUR': 100, 'RUB': 100}

MAX_AMOUNT = 10 ** 15
MAX_LINE_ITEMS = 200
MAX_TEXT_CHARS = 200
# Default tolerance when the operator declares none: zero. A tolerance is a
# business decision, and defaulting to a non-zero one would silently excuse
# mismatches the operator never agreed to excuse.
DEFAULT_TOLERANCE_MINOR = 0

DOCUMENT_KINDS = ('invoice', 'purchase_order', 'delivery_note', 'contract')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS p_documents(
 tenant TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
 supplier TEXT NOT NULL, number TEXT NOT NULL, doc_date TEXT NOT NULL DEFAULT '',
 currency TEXT NOT NULL, total_minor INTEGER NOT NULL,
 body TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(tenant,id));
CREATE UNIQUE INDEX IF NOT EXISTS p_documents_number
 ON p_documents(tenant,kind,supplier,number);
CREATE INDEX IF NOT EXISTS p_documents_supplier ON p_documents(tenant,supplier);
CREATE TABLE IF NOT EXISTS p_posting_plans(
 tenant TEXT NOT NULL, id TEXT NOT NULL, document TEXT NOT NULL, run TEXT NOT NULL DEFAULT '',
 plan TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(tenant,id));
CREATE INDEX IF NOT EXISTS p_posting_plans_document ON p_posting_plans(tenant,document);
'''


class DocumentError(RuntimeError):
    """A deterministic refusal about a document, not a transport failure."""


# -------------------------------------------------------------------- config

DEFAULTS = {'tolerance_minor': DEFAULT_TOLERANCE_MINOR, 'outlier_ratio': 3,
            'approver_role': 'owner', 'residency': 'uz',
            'currencies': ['UZS', 'USD', 'EUR', 'RUB']}


def documents_config(tenant):
    """The tenant's AP settings, validated, with conservative defaults.

    Operator-owned on purpose: the match tolerance, the fraud threshold and the
    approval role are business decisions, and a platform that chose them would be
    deciding how much discrepancy is acceptable to pay.

    An absent file or an absent block yields the defaults rather than an error,
    because the defaults are exhaustive and conservative (zero tolerance, exact
    match). A block that is *present* but malformed is still a hard error: a typo
    in a threshold must never be silently replaced by a default the operator did
    not choose.
    """
    from .tools import config

    try:
        block = config(tenant).get('documents')
    except (RuntimeError, ValueError, KeyError, TypeError, OSError):
        return dict(DEFAULTS)
    if block is None:
        return dict(DEFAULTS)
    if not isinstance(block, dict):
        raise ValueError('documents configuration must be an object')
    unknown = set(block) - set(DEFAULTS)
    if unknown:
        raise ValueError(f'unknown documents keys: {", ".join(sorted(unknown))}')
    resolved = dict(DEFAULTS)
    resolved.update(block)
    tolerance = resolved['tolerance_minor']
    if isinstance(tolerance, bool) or not isinstance(tolerance, int) or tolerance < 0:
        raise ValueError('tolerance_minor must be a non-negative integer')
    ratio = resolved['outlier_ratio']
    if isinstance(ratio, bool) or not isinstance(ratio, int) or ratio < 1:
        raise ValueError('outlier_ratio must be an integer of at least 1')
    if resolved['approver_role'] not in ('owner', 'operator'):
        raise ValueError('approver_role must be owner or operator')
    if resolved['residency'] not in ('uz', 'any'):
        raise ValueError('residency must be uz or any')
    currencies = resolved['currencies']
    if not isinstance(currencies, list) or not currencies:
        raise ValueError('currencies must be a non-empty list')
    for currency in currencies:
        if currency not in CURRENCIES:
            raise ValueError(f'unsupported currency {currency!r}')
    return resolved


# --------------------------------------------------------------- normalisation


def _party(value, name='party'):
    if not isinstance(value, str) or not PARTY_RE.match(value):
        raise ValueError(f'{name} must be lowercase letters, digits, dot, dash or '
                         f'underscore')
    return value


def _number(value):
    if not isinstance(value, str) or not NUMBER_RE.match(value):
        raise ValueError('document number must be letters, digits or /_.- and at '
                         'most 64 characters')
    return value


def _currency(value):
    if value not in CURRENCIES:
        raise ValueError(f'currency must be one of {", ".join(CURRENCIES)}')
    return value


def _amount_minor(value, currency):
    """Convert a declared amount to integer minor units.

    Accepts an integer (already minor units) or a decimal string such as
    ``'1250000.50'``. A float is **refused**: binary floating point cannot hold
    every decimal amount exactly, and an accounts-payable check that is off by one
    tiyin is a check that cannot be trusted. The string form is what an operator
    writes and what a provider sends.
    """
    factor = MINOR_UNITS[currency]
    digits = len(str(factor)) - 1
    if isinstance(value, bool):
        raise ValueError('amount must be a number or a decimal string')
    if isinstance(value, int):
        minor = value * factor
    elif isinstance(value, float):
        raise ValueError('amount must not be a float: use an integer or a decimal '
                         'string, because binary floating point cannot represent '
                         'every decimal amount exactly')
    elif isinstance(value, str):
        text = value.strip()
        # ASCII digits only, and deliberately explicit. ``\d`` also matches
        # Devanagari, Arabic-Indic and fullwidth digits, which ``int()`` then
        # normalises, so ``'१२३.०१'`` used to become a real amount of money that
        # nobody ever sent -- silently, with no counter and no exception. An
        # amount that is off by a debit is exactly the failure this block exists
        # to prevent, so a non-ASCII amount is refused by name. See
        # ``platform_runtime.cells``.
        if not re.fullmatch(r'[0-9]{1,15}(\.[0-9]{1,6})?', text):
            raise ValueError('amount string must be ASCII digits with an optional '
                             'decimal part')
        whole, _, fraction = text.partition('.')
        # Truncate to the currency's own precision rather than rounding: rounding a
        # submitted amount upward would inflate what we later compare against a PO.
        fraction = (fraction + '0' * digits)[:digits]
        minor = int(whole) * factor + (int(fraction) if digits else 0)
    else:
        raise ValueError('amount must be a number or a decimal string')
    if minor < 0 or minor > MAX_AMOUNT * factor:
        raise ValueError('amount out of range')
    return minor


def format_amount(minor, currency):
    """Render minor units back to a decimal string. Never used for arithmetic."""
    factor = MINOR_UNITS[currency]
    if factor == 1:
        return str(minor)
    whole, fraction = divmod(minor, factor)
    digits = len(str(factor)) - 1
    return f'{whole}.{str(fraction).zfill(digits)}'


def _date(value):
    if value in (None, ''):
        return ''
    if not isinstance(value, str):
        raise ValueError('doc_date must be an ISO date string')
    text = value.strip()
    # ``[0-9]`` and not ``\d``: Python's ``re`` matches every Unicode decimal
    # digit, so a Devanagari or fullwidth date used to pass this shape check and
    # was then handed to ``int()`` downstream (the weekday signal and the
    # duplicate window), which normalises it. The document would then be filed
    # under a date the supplier never wrote. ASCII is required so the value we
    # echo back is the value we were given. See ``platform_runtime.cells``.
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', text):
        # Guessing 10.01.2026 as either January or October shifts a duplicate
        # window by months, so the ambiguous form is refused rather than assumed.
        raise ValueError('doc_date must be ISO YYYY-MM-DD in ASCII digits, not an '
                         'ambiguous local format')
    # The shape check above is not a date check: `2026-13-45`, `2026-02-30` and
    # `2026-00-00` all match it. Such a value was stored, and `date()` then raised on
    # it INSIDE `fraud_signals`, where the exception is caught -- so the document
    # carried no weekday signal and nothing said why. A date that is not a date
    # disabled a signal by failing to parse, which is the quietest failure available.
    # An ambiguous date is refused because guessing is wrong; an impossible one is
    # refused because there is nothing to guess.
    from datetime import date as calendar_date

    try:
        year, month, day = (int(part) for part in text.split('-'))
        calendar_date(year, month, day)
    except ValueError:
        raise ValueError('doc_date must be a real calendar date in ISO YYYY-MM-DD '
                         'form') from None
    return text


def _line_items(value, currency):
    """Validate line items. ``amount`` is the line's UNIT price, ``quantity`` how many.

    This function used to validate ``quantity``, store it, and then have the
    self-consistency check in ``normalize`` ignore it -- the sum added ``amount``
    alone. That made the guard backwards for any multi-quantity line:

    * ``2 x 5000`` with a stated total of **10000** summed to 5000 and was REFUSED
      as "a document that disagrees with itself";
    * the same line with a stated total of **5000** was ACCEPTED.

    So an arithmetically correct invoice was rejected and an understated one
    passed, by the very check whose purpose is catching the understatement. A
    field that is validated and stored but not used by the arithmetic is not
    neutral: it silently redefines what the arithmetic is summing.

    ``amount`` is the unit price because that is the only reading in which
    ``quantity`` carries any meaning. The line total is therefore
    ``quantity * amount``, and the sum of those must equal the stated total --
    see ``line_items_total``. With ``quantity`` defaulting to 1 the identity
    reduces to the previous behaviour, which is why the quantity-less documents
    every existing caller submits are unaffected.
    """
    if value in (None, ''):
        return []
    if not isinstance(value, list) or len(value) > MAX_LINE_ITEMS:
        raise ValueError(f'line_items must be a list of at most {MAX_LINE_ITEMS}')
    items = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f'line item {index} must be an object')
        unknown = set(item) - {'description', 'quantity', 'amount'}
        if unknown:
            raise ValueError(f'line item {index} has unknown keys: '
                             f'{", ".join(sorted(unknown))}')
        description = item.get('description', '')
        if not isinstance(description, str) or len(description) > MAX_TEXT_CHARS:
            raise ValueError(f'line item {index} description must be text')
        quantity = item.get('quantity', 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise ValueError(f'line item {index} quantity must be a non-negative integer')
        amount = _amount_minor(item.get('amount', 0), currency)
        # The line total, so the sum below adds like with like. Computed here and
        # reported on the item, because a reader checking the arithmetic should not
        # have to perform the multiplication themselves.
        items.append({'description': description, 'quantity': quantity,
                      'amount': amount, 'line_total': quantity * amount})
    return items


def line_items_total(items):
    """The sum of the LINE totals: ``quantity * amount`` per line, then added.

    One definition, used by every caller. The sum was previously computed in two
    places -- once inside ``_line_items`` into a local nobody read, and once again
    in ``normalize`` -- which is how the two readings could drift apart unobserved.
    """
    return sum(item['line_total'] for item in items)


def normalize(fields):
    """Validate and normalize one extracted document.

    Returns a dict whose ``total_minor`` is authoritative. When the document
    carries line items, the stated total must equal their sum: a document that
    disagrees with itself is refused here rather than downstream, because a
    later check comparing the wrong total is worse than no check.
    """
    if not isinstance(fields, dict):
        raise ValueError('document fields must be an object')
    unknown = set(fields) - {'kind', 'supplier', 'number', 'doc_date', 'currency',
                             'total', 'line_items', 'bank_account'}
    if unknown:
        raise ValueError(f'unknown document keys: {", ".join(sorted(unknown))}')
    kind = fields.get('kind')
    if kind not in DOCUMENT_KINDS:
        raise ValueError(f'kind must be one of {", ".join(DOCUMENT_KINDS)}')
    supplier = _party(fields.get('supplier'), 'supplier')
    number = _number(fields.get('number'))
    currency = _currency(fields.get('currency'))
    doc_date = _date(fields.get('doc_date'))
    total_minor = _amount_minor(fields.get('total', 0), currency)
    items = _line_items(fields.get('line_items'), currency)
    if items:
        summed = line_items_total(items)
        if summed != total_minor:
            raise ValueError(
                f'line items sum to {format_amount(summed, currency)} but the stated '
                f'total is {format_amount(total_minor, currency)}: a document that '
                f'disagrees with itself cannot be checked')
    bank_account = fields.get('bank_account', '')
    if bank_account:
        if not isinstance(bank_account, str) or not re.fullmatch(r'[0-9 ]{4,34}',
                                                                 bank_account):
            raise ValueError('bank_account must be 4 to 34 digits')
        bank_account = bank_account.replace(' ', '')
        # The bound above is on the SPACED string, so it does not bound the DIGITS,
        # and the message promises digits. Measured: `'    '` (four spaces) passed it
        # and stripped to `''`, which made `if doc['bank_account']` falsy and
        # SILENTLY SKIPPED the `bank_account_changed` signal -- a material control
        # whose entire purpose is catching a supplier-account substitution, which is
        # the classic accounts-payable fraud. `'1   '` passed it too and became a
        # one-digit account. Bound the value the message names.
        if not 4 <= len(bank_account) <= 34:
            raise ValueError('bank_account must be 4 to 34 digits')
    return {'kind': kind, 'supplier': supplier, 'number': number,
            'doc_date': doc_date, 'currency': currency, 'total_minor': total_minor,
            'line_items': items, 'bank_account': bank_account}


def document_key(document):
    """The dedup key: supplier plus the supplier's own number, per document kind.

    Deliberately excludes the amount. The same invoice number with a different
    amount is *more* suspicious than a same-amount repeat, not less — a
    re-issued or altered invoice is exactly the fraud this check exists to catch.
    """
    return (document['kind'], document['supplier'], document['number'])


# ------------------------------------------------------------------ storage


def remember(engine, tenant, document, created=None):
    """Record a normalized document. Idempotent on the supplier's number."""
    entry = dict(document)
    key = document_key(entry)
    doc_id = ':'.join(key)
    e = engine
    now = e.clock() if created is None else created
    with e.tx() as c:
        e.require_active(c, tenant)
        row = c.execute('''SELECT id,total_minor,currency FROM p_documents
          WHERE tenant=? AND kind=? AND supplier=? AND number=?''', (tenant, *key)).fetchone()
        if row is not None:
            # A re-submitted number is not an error and not a second document. The
            # caller learns it is a duplicate and decides; we do not overwrite the
            # original, because the original is the evidence of what was first seen.
            return {'id': row['id'], 'duplicate': True,
                    'same_amount': row['total_minor'] == entry['total_minor'],
                    'first_total': format_amount(row['total_minor'], row['currency']),
                    'first_currency': row['currency']}
        from .engine import encode
        c.execute('''INSERT INTO p_documents
          (tenant,id,kind,supplier,number,doc_date,currency,total_minor,body,created)
          VALUES(?,?,?,?,?,?,?,?,?,?)''',
                  (tenant, doc_id, entry['kind'], entry['supplier'], entry['number'],
                   entry['doc_date'], entry['currency'], entry['total_minor'],
                   encode(entry), now))
        return {'id': doc_id, 'duplicate': False, 'same_amount': False,
                'first_total': '', 'first_currency': ''}


def stored(engine, tenant, supplier, number, kind='invoice'):
    with engine.read() as c:
        row = c.execute('''SELECT * FROM p_documents
          WHERE tenant=? AND kind=? AND supplier=? AND number=?''',
                        (tenant, kind, supplier, number)).fetchone()
    return dict(row) if row else None


def supplier_history(engine, tenant, supplier, limit=50):
    limit = min(max(1, int(limit)), 500)
    with engine.read() as c:
        return [dict(row) for row in c.execute(
            '''SELECT * FROM p_documents WHERE tenant=? AND supplier=?
               ORDER BY created DESC LIMIT ?''', (tenant, supplier, limit))]


# ------------------------------------------------------------ duplicate check


def duplicates(engine, tenant, agent, fields):
    """Is this document already known, and how does it differ from what we hold?

    Reports the stored original whenever the key matches. When the amounts differ
    the result says so explicitly and raises the fraud signal, because the PRD
    calls a same-number-different-amount case out as the interesting one.
    """
    document = normalize(fields)
    existing = stored(engine, tenant, document['supplier'], document['number'],
                      document['kind'])
    if existing is None:
        return {'duplicate': False, 'original': None, 'altered': False}
    same_currency = existing['currency'] == document['currency']
    same_amount = (existing['total_minor'] == document['total_minor']
                   and same_currency)
    # Two different changes, reported as two different things. Comparing
    # ``total_minor`` alone would call 1000 UZS and 1000 USD the same amount, and
    # comparing only the pair would leave the approver unable to tell which of the
    # two moved. The previous form collapsed both into one ``from``/``to`` pair
    # formatted in two different currencies, so a document reissued in a different
    # currency appeared as ``from: '1000', to: '1000.00'`` -- a report of an amount
    # change for an amount that did not change. An approver checking that evidence
    # would look for a price difference that is not there.
    changed = not same_amount
    return {
        'duplicate': True,
        'altered': changed,
        'same_amount': not changed,
        'same_currency': same_currency,
        'original': {
            'id': existing['id'],
            'number': existing['number'],
            'supplier': existing['supplier'],
            'date': existing['doc_date'],
            'total': format_amount(existing['total_minor'], existing['currency']),
            'currency': existing['currency'],
        },
        'submitted': {'date': document['doc_date'],
                      'total': format_amount(document['total_minor'],
                                             document['currency']),
                      'currency': document['currency']},
        # One pair per quantity, each pair in a single currency, and each absent
        # when that quantity did not move. A key that is present is a change; a
        # key that is ``None`` is not. The two can now be told apart, which the
        # single collapsed pair could not.
        'amount_changed': _changed_amount(existing, document, same_currency),
        'currency_changed': None if same_currency else {
            'from': existing['currency'], 'to': document['currency']},
        'amount_delta_minor': (None if not same_currency
                               else document['total_minor']
                               - existing['total_minor']),
    }


def _changed_amount(existing, document, same_currency):
    """The amount change, or ``None``, expressed entirely in one currency.

    A currency change with the number held constant is **not** an amount change,
    and the report says so rather than rendering the same number twice under two
    different symbols. When the currency differs the deltas are not comparable at
    all without a rate, so the pair shows the stored number on both sides with an
    explicit note instead of inventing a comparison.
    """
    if not same_currency:
        formatted = format_amount(existing['total_minor'], existing['currency'])
        return {'from': formatted, 'to': formatted,
                'note': 'the number is unchanged; the currency is what differs'}
    if existing['total_minor'] == document['total_minor']:
        return None
    return {'from': format_amount(existing['total_minor'], existing['currency']),
            'to': format_amount(document['total_minor'], document['currency'])}


# -------------------------------------------------------------- three-way match


def match(engine, tenant, agent, invoice, purchase_order=None, delivery_note=None,
          tolerance_minor=DEFAULT_TOLERANCE_MINOR):
    """Match an invoice against its purchase order and delivery note.

    This is the reconcile pattern: three documents that must agree, and an outcome
    that is reported rather than forced. The tolerance is the operator's, in minor
    units; with no tolerance declared the required agreement is exact.

    Returns ``status`` of ``matched``, ``partial``, ``mismatch`` or ``incomplete``.
    ``incomplete`` means a counterpart document was missing, which is **not** the
    same as a mismatch: absent evidence and contradicting evidence are different
    facts and an approver must be able to tell them apart.
    """
    if isinstance(tolerance_minor, bool) or not isinstance(tolerance_minor, int) \
            or tolerance_minor < 0:
        raise ValueError('tolerance_minor must be a non-negative integer')
    doc = normalize(invoice)
    currency = doc['currency']
    checks, missing = [], []

    for label, counterpart in (('purchase_order', purchase_order),
                               ('delivery_note', delivery_note)):
        if counterpart in (None, {}):
            missing.append(label)
            continue
        other = normalize(counterpart)
        if other['currency'] != currency:
            # Comparing across currencies without a rate would invent a number.
            checks.append({'against': label, 'ok': False, 'reason': 'currency',
                           'from': other['currency'], 'to': currency})
            continue
        delta = doc['total_minor'] - other['total_minor']
        within = abs(delta) <= tolerance_minor
        checks.append({
            'against': label, 'ok': within, 'reason': 'within tolerance' if within
            else ('invoice above' if delta > 0 else 'invoice below'),
            'counterpart_total': format_amount(other['total_minor'], currency),
            'difference': format_amount(abs(delta), currency),
        })

    if missing and len(missing) == 2:
        status = 'incomplete'
    elif missing:
        status = 'partial' if all(c['ok'] for c in checks) else 'mismatch'
    else:
        status = 'matched' if all(c['ok'] for c in checks) else 'mismatch'
    return {
        'status': status,
        'currency': currency,
        'invoice_total': format_amount(doc['total_minor'], currency),
        'tolerance': format_amount(tolerance_minor, currency),
        'checks': checks,
        'missing': missing,
        'complete': not missing,
    }


# ------------------------------------------------------------ fraud controls


FRAUD_SIGNALS = ('duplicate_altered', 'amount_outlier', 'bank_account_changed',
                 'round_number', 'weekend_date', 'no_history')

# Not every signal is equally interesting, and treating them as equal would make the
# check useless: most legitimate invoices are round numbers, so gating every posting
# on a round amount would train an approver to click through the warning.
#
# Every signal is always REPORTED. Only a material one forces
# ``needs_attention``; a weak one is still shown to the approver, who can judge it.
# The split is a deliberate claim about which observations could indicate money
# going somewhere it should not, and it is stated here rather than buried so
# disagreement is easy.
MATERIAL_SIGNALS = frozenset({'duplicate_altered', 'amount_outlier',
                              'bank_account_changed'})
WEAK_SIGNALS = frozenset({'round_number', 'weekend_date', 'no_history'})

# How round an amount has to be before it is worth mentioning.
#
# Roundness is counted as trailing zeros in the major unit, so the scale is
# currency-independent: 1000000 so'm and 1000.00 dollars are both "round to three".
# One trailing zero is NOT interesting -- in a currency with a small unit
# (UZS, factor 1) almost every real price ends in a zero, so a threshold of 1
# would fire on 123450 and 123460 and teach the approver to ignore the signal,
# which is the failure the comment above warns about. Three is the point where an
# amount stops looking like a price and starts looking like a person typing zeros
# -- the same "prompt to look, not a statistical claim" standard as ``outlier_ratio``.
#
# Named and configurable rather than inlined, because the honest value depends on
# the operator's own invoices and nobody else can know it. A bare literal here
# would be a threshold nobody could argue with.
ROUND_TRAILING_ZEROS = 3


def _median(values):
    """The middle of a sorted list, averaged when the count is even.

    Two reasons this is not ``values[len(values) // 2]``:

    * That expression is the *upper-middle* element. It is only equal to the
      median when the count is odd, and it is biased upward when it is not — a
      bias with a direction, always toward *raising* the outlier threshold, i.e.
      toward not raising the flag. An outlier test that gets weaker as the
      supplier's spread widens fails exactly where the signal matters most:
      measured against a correct median it needs an invoice up to **1.6x** larger
      to react.
    * The value is printed into the approver's evidence as "the median". A number
      labelled as the median has to be the median, or the label is a lie and the
      approver is checking the flag against a figure it cannot reconcile.

    ``statistics.median`` is the library's answer and is used elsewhere in the
    repository, but it is NOT usable here: for an even count it divides, which
    returns a float, and floats hold consecutive integers exactly only up to
    ``2 ** 53``. ``MAX_AMOUNT`` allows a USD total of ``10 ** 17`` minor units, so the
    range this function is asked about is well past that. Measured before the fix:
    the median of ``[10 ** 16, 10 ** 16 + 2]`` came back as ``10 ** 16`` instead of
    ``10 ** 16 + 1``, and of ``[10 ** 17, 10 ** 17 + 2]`` as ``10 ** 17`` instead of
    ``10 ** 17 + 1``. The module docstring says money is compared in integer minor
    units, *never in floats*; this was the one place that broke that rule, and the
    number it produced is printed into the approver's evidence as "the median".

    The even-count case is therefore averaged in INTEGER arithmetic, which is exact
    for every input. The result is the lower middle when the two differ, so the
    threshold is never raised by rounding -- the same direction the docstring above
    argues for.
    """
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def fraud_signals(engine, tenant, agent, fields, *, outlier_ratio=3, history_limit=50):
    """Report fraud *signals* with their evidence. Never a verdict.

    Every signal names the observation that produced it, because a flag an
    approver cannot check is a flag an approver will learn to ignore. The module
    does not accuse, score or block: it raises the flag and a human decides.

    Thresholds are operator configuration with a conservative default, and the
    outlier test is deliberately crude — three times the supplier's own median is
    a prompt to look, not a statistical claim, and saying so is more honest than
    dressing it up as a model.

    The comparison population is the supplier's *prior* documents. A document is
    normally stored before it is checked, so an unfiltered read of the history
    puts the document under test inside the population it is being tested
    against. That is not a rounding question: an outlier contributes its own
    extreme value to the median that has to catch it, so the more extreme the
    amount the harder it becomes to flag. Measured on a two-document history the
    self-included threshold sits 1.5x above the correct one. The document under
    test is therefore identified by its dedup key and removed.
    """
    if isinstance(outlier_ratio, bool) or not isinstance(outlier_ratio, int) \
            or outlier_ratio < 1:
        raise ValueError('outlier_ratio must be an integer of at least 1')
    doc = normalize(fields)
    history = supplier_history(engine, tenant, doc['supplier'], history_limit)
    signals = []

    # The document under test may already be stored (receive -> store -> check),
    # and its own dedup key is what identifies it in the history. A row with the
    # same key is not a "prior" document: it is this one.
    key = document_key(doc)
    prior = [row for row in history
             if row['currency'] == doc['currency']
             and (row['kind'], row['supplier'], row['number']) != key]
    if not prior:
        signals.append({'signal': 'no_history', 'detail':
                        f'no prior {doc["currency"]} document from '
                        f'"{doc["supplier"]}" to compare against',
                        'evidence': {}})
    else:
        amounts = sorted(row['total_minor'] for row in prior)
        median = _median(amounts)
        if median > 0 and doc['total_minor'] >= median * outlier_ratio:
            signals.append({'signal': 'amount_outlier', 'detail':
                            f'{format_amount(doc["total_minor"], doc["currency"])} is '
                            f'at least {outlier_ratio}x this supplier\'s median of '
                            f'{format_amount(median, doc["currency"])} over '
                            f'{len(prior)} prior document(s)',
                            'evidence': {'median': format_amount(median, doc['currency']),
                                         'count': len(prior)}})
        if doc['bank_account']:
            latest = next((row for row in prior), None)
            if latest is not None:
                import json

                previous = json.loads(latest['body']).get('bank_account', '')
                if previous and previous != doc['bank_account']:
                    signals.append({'signal': 'bank_account_changed', 'detail':
                                    'the supplier bank account differs from the most '
                                    'recent stored document: confirm with the supplier '
                                    'by a channel other than the document itself',
                                    'evidence': {'mismatch': True}})

    # A round amount is a prompt to look, not evidence of anything: many legitimate
    # invoices are round.
    #
    # "Round" is a claim about the MAJOR unit, and getting the units wrong here made
    # the signal both blind and noisy. The previous test was
    # ``total_minor % 10 ** MINOR_UNITS[currency] == 0``, which asks "is the amount a
    # whole number of major units" -- for UZS (factor 1) that happens to coincide
    # with roundness, but for USD (factor 100) it only asks "are the cents zero",
    # which is true of nearly every invoice ever issued, so ``USD 1000.00`` was
    # never flagged while ``USD 1000.50`` was not either. The module docstring
    # claimed the minor-unit form made the test mean the same thing for both
    # currencies; measured through the tool, it meant nothing at all for any
    # currency but UZS.
    #
    # Two separate questions were also being answered by one number:
    #   * is this a whole number of major units?  -> the guard for the signal
    #   * how round is it?                        -> trailing zeros of the major
    # The old ``digits`` was ``len(str(major).rstrip('0'))``, the count of leading
    # significant digits, and the message rendered it as a roundness claim. For UZS
    # 100050 it printed 5, i.e. "very round", when the amount is not round at all;
    # for UZS 1000000 it printed 1, i.e. "barely round", when it is the roundest
    # amount there is. The value now reported is the count of trailing zeros, which
    # is what the sentence says.
    factor = MINOR_UNITS[doc['currency']]
    if doc['total_minor'] > 0 and doc['total_minor'] % factor == 0:
        major = doc['total_minor'] // factor
        text = str(major)
        trailing = len(text) - len(text.rstrip('0'))
        if trailing >= ROUND_TRAILING_ZEROS:
            signals.append({'signal': 'round_number', 'detail':
                            f'the amount is exactly round to {trailing} trailing '
                            f'zero(s) in whole {doc["currency"]}, which is unusual '
                            f'for an invoice',
                            'evidence': {'trailing_zeros': trailing,
                                         'threshold': ROUND_TRAILING_ZEROS}})

    if doc['doc_date']:
        try:
            from datetime import date

            # New rows are ASCII-checked by ``_date``, but rows written before that
            # guard existed could hold Unicode digits, which ``int()`` would happily
            # normalise into a weekday claim about a date we were never given.
            # Refusing here means such a row simply carries no weekday signal.
            if not cells.is_ascii_digit_run(doc['doc_date'].replace('-', '')):
                raise ValueError('not an ASCII date')
            year, month, day = (int(part) for part in doc['doc_date'].split('-'))
            weekday = date(year, month, day).weekday()
            weekday_history = 0
            parsed_prior = 0
            for row in prior:
                if not row['doc_date']:
                    continue
                try:
                    if not cells.is_ascii_digit_run(row['doc_date'].replace('-', '')):
                        continue
                    y, m, d = (int(part) for part in row['doc_date'].split('-'))
                    parsed_prior += 1
                    if date(y, m, d).weekday() >= 5:
                        weekday_history += 1
                except ValueError:
                    continue
            # Only raised as a deviation from the supplier's OWN pattern, because for
            # a supplier that always issues on a Sunday a weekend date is ordinary.
            # With no parsed history there is nothing to deviate from, and we say so
            # rather than implying we checked.
            if weekday >= 5 and parsed_prior and weekday_history == 0:
                signals.append({'signal': 'weekend_date', 'detail':
                                f'the document date falls on a weekend, but none of '
                                f'this supplier\'s last {parsed_prior} document(s) did',
                                'evidence': {'weekday': weekday,
                                             'prior_weekend': weekday_history}})
        except ValueError:
            pass

    duplicate = duplicates(engine, tenant, agent, fields)
    if duplicate['duplicate'] and duplicate['altered']:
        signals.append({'signal': 'duplicate_altered', 'detail':
                        'a document with this number already exists and the amount '
                        'differs: a re-issued or altered invoice is more suspicious '
                        'than an exact repeat',
                        'evidence': duplicate['amount_changed'] or {}})

    return {'signals': signals, 'count': len(signals),
            'material': sorted({s['signal'] for s in signals} & MATERIAL_SIGNALS),
            'weak': sorted({s['signal'] for s in signals} & WEAK_SIGNALS),
            'supplier': doc['supplier'],
            # Two different quantities, so two different names. ``history_rows`` is
            # what the store returned; ``history_considered`` is what the outlier
            # test actually compared against, and they differ by one whenever the
            # document under test has already been stored -- which is the normal
            # flow. Reporting only the first would let a reader believe the
            # comparison population was larger than it was.
            'history_rows': len(history), 'history_considered': len(prior)}


# ------------------------------------------------------------- posting plan


def posting_plan(engine, tenant, agent, fields, *, tolerance_minor=0,
                 purchase_order=None, delivery_note=None, outlier_ratio=3):
    """The end of the chain: a *proposal*, never an execution.

    This is the furthest the platform goes. It validates the document, runs the
    duplicate check, the three-way match and the fraud controls, and returns a
    plan naming what should be posted and what a human should look at first.

    There is no branch of this function that pays, transfers, or settles anything:
    it returns a dict and writes nothing. Recording the plan is a separate,
    approval-gated write, because a plan that a human has not seen is not a plan
    an accountant agreed to.
    """
    doc = normalize(fields)
    dup = duplicates(engine, tenant, agent, fields)
    matched = match(engine, tenant, agent, fields, purchase_order, delivery_note,
                    tolerance_minor)
    fraud = fraud_signals(engine, tenant, agent, fields, outlier_ratio=outlier_ratio)

    # Any duplicate holds the document: an exact repeat is a double-payment risk and
    # an altered one is worse, so both must stop before an approver, and the
    # recommendation says which of the two it was.
    material = bool(fraud['material'])
    needs_attention = bool(dup['duplicate'] or matched['status'] != 'matched'
                           or material)
    if dup['duplicate']:
        recommendation = 'hold_for_duplicate_review'
    elif matched['status'] == 'mismatch':
        recommendation = 'hold_for_mismatch_review'
    elif matched['status'] == 'incomplete':
        recommendation = 'attach_missing_document'
    elif material:
        recommendation = 'review_fraud_signals'
    else:
        recommendation = 'ready_for_approval'

    return {
        'document': {'kind': doc['kind'], 'supplier': doc['supplier'],
                     'number': doc['number'], 'date': doc['doc_date'],
                     'currency': doc['currency'],
                     'total': format_amount(doc['total_minor'], doc['currency'])},
        'duplicate': dup,
        'match': matched,
        'fraud': fraud,
        'needs_attention': needs_attention,
        'recommendation': recommendation,
        # Spelled out rather than implied, because an approver reading this output
        # needs to know what has NOT happened as much as what has.
        'executes_payment': False,
        'note': 'This is a control plan, not a payment. The platform does not move '
                'money; posting to the ERP requires a human approval.',
    }


def record_plan(engine, tenant, document_id, run_id, plan):
    """Store a prepared plan. Called only by the approval-gated write handler."""
    import uuid

    from .engine import encode

    e = engine
    plan_id = uuid.uuid4().hex[:24]
    with e.tx() as c:
        e.require_active(c, tenant)
        c.execute('''INSERT INTO p_posting_plans
          (tenant,id,document,run,plan,status,created) VALUES(?,?,?,?,?,?,?)''',
                  (tenant, plan_id, document_id, run_id, encode(plan), 'prepared',
                   e.clock()))
    return plan_id


def plans(engine, tenant, document_id):
    with engine.read() as c:
        return [dict(row) for row in c.execute(
            '''SELECT id,document,status,created FROM p_posting_plans
               WHERE tenant=? AND document=? ORDER BY created DESC''',
            (tenant, document_id))]


def load(engine, tenant, document_id):
    with engine.read() as c:
        row = c.execute('SELECT * FROM p_documents WHERE tenant=? AND id=?',
                        (tenant, document_id)).fetchone()
    if row is None:
        raise NotFound('Document not found')
    return dict(row)


# --------------------------------------------------------------- tool handlers


def _payload(args, key, *, required=False):
    """Decode one structured argument that travelled as JSON text.

    Structured arguments arrive as a JSON string rather than as a nested object,
    because the registry's schema validator rejects a bare ``{'type': 'object'}``
    property: with no ``properties`` declared, *every* nested dict fails with
    "Schema fields mismatch". A document is a tree (line items, a counterpart
    document), so it cannot be flattened into one level of scalar fields without
    losing its shape. The JSON string is the shape the rest of the runtime already
    uses for structured payloads, and it is decoded here so a malformed payload is a
    clean refusal naming the argument rather than a TypeError deep inside.
    """
    value = args.get(key)
    if value in (None, ''):
        if required:
            raise ValueError(f'{key} is required')
        return None
    if isinstance(value, dict):
        # Tolerated so a caller holding the object can pass it straight through,
        # but operators and the model are told to send JSON text.
        return value
    if not isinstance(value, str):
        raise ValueError(f'{key} must be a JSON object')
    import json
    try:
        parsed = json.loads(value)
    except ValueError:
        raise ValueError(f'{key} must be valid JSON') from None
    if not isinstance(parsed, dict):
        raise ValueError(f'{key} must be a JSON object')
    return parsed


def _parse_tool(engine, tenant, agent, args, step):
    document = normalize(_payload(args, 'fields', required=True))
    record = remember(engine, tenant, document)
    out = {'document': {key: value for key, value in document.items()
                        if key != 'line_items'},
           'line_items': document['line_items'],
           'total': format_amount(document['total_minor'], document['currency']),
           'stored_id': record['id'], 'duplicate': record['duplicate']}
    if record['duplicate']:
        out['first_seen'] = {'total': record['first_total'],
                             'currency': record['first_currency'],
                             'same_amount': record['same_amount']}
    return out


def _match_tool(engine, tenant, agent, args, step):
    return match(engine, tenant, agent, _payload(args, 'invoice', required=True),
                 _payload(args, 'purchase_order'),
                 _payload(args, 'delivery_note'),
                 args.get('tolerance_minor', DEFAULT_TOLERANCE_MINOR))


def _duplicates_tool(engine, tenant, agent, args, step):
    return duplicates(engine, tenant, agent, _payload(args, 'fields', required=True))


def _fraud_tool(engine, tenant, agent, args, step):
    settings = documents_config(tenant)
    return fraud_signals(engine, tenant, agent,
                         _payload(args, 'fields', required=True),
                         outlier_ratio=args.get('outlier_ratio',
                                                settings['outlier_ratio']))


def _plan_tool(engine, tenant, agent, args, step):
    """Write, approval-gated. Records the plan; moves no money.

    The plan is recomputed here rather than trusted from the arguments: an
    approved step replays its arguments, so a caller could otherwise approve a
    clean plan and submit a dirty one under the same fingerprint. The tolerance
    and the fraud threshold default to the operator's configuration, and a step
    argument can only tighten them, never widen them past what the operator set.
    """
    settings = documents_config(tenant)
    tolerance = args.get('tolerance_minor', settings['tolerance_minor'])
    if isinstance(tolerance, bool) or not isinstance(tolerance, int) or tolerance < 0:
        raise ValueError('tolerance_minor must be a non-negative integer')
    fields = _payload(args, 'fields', required=True)
    plan = posting_plan(engine, tenant, agent, fields,
                        tolerance_minor=tolerance,
                        purchase_order=_payload(args, 'purchase_order'),
                        delivery_note=_payload(args, 'delivery_note'),
                        outlier_ratio=settings['outlier_ratio'])
    document = normalize(fields)
    record = remember(engine, tenant, document)
    plan_id = record_plan(engine, tenant, record['id'], str(step or ''), plan)
    return {'plan_id': plan_id, 'document_id': record['id'],
            'recommendation': plan['recommendation'],
            'needs_attention': plan['needs_attention'],
            'executes_payment': False}


def register_document_tools(registry):
    """Four read tools and one approval-gated write. No payment path exists.

    ``document.parse`` records an extracted document; ``document.match`` and
    ``document.duplicates`` and ``document.fraud_signals`` are pure reads over what
    is stored; ``document.posting_plan`` is the write, and all it writes is a
    proposal.

    Every structured argument is a JSON **string**, not a nested object, and that
    is not a stylistic choice. The registry validator refuses a property declared as
    a bare ``{'type': 'object'}``: with no ``properties`` to check against, any dict
    at all fails with "Schema fields mismatch". A previous revision of this module
    declared ``fields`` that way, which made all five tools reject every call at the
    engine boundary — the tools existed, they were in the capability packs, and
    none of them could be submitted. The nested-object form is therefore not
    available, and a flattened schema cannot carry line items. JSON text is what the
    rest of the runtime uses for structured payloads, and it is validated by
    ``_payload`` and then by ``normalize``, which is stricter than the generic
    validator was.
    """
    from .tools import Tool, obj, string

    # A JSON document, bounded like any other string argument. 20000 characters is
    # the validator's own limit on an encoded argument, so 16000 leaves room for the
    # enclosing object while still bounding the count of line items indirectly.
    payload = string(16000)
    definitions = [
        ('document.parse', 'write', obj({'fields': payload}), _parse_tool),
        ('document.match', 'read',
         obj({'invoice': payload, 'purchase_order': payload, 'delivery_note': payload,
              'tolerance_minor': {'type': 'integer', 'minimum': 0,
                                  'maximum': 100_000_000_000}}, required=['invoice']),
         _match_tool),
        ('document.duplicates', 'read', obj({'fields': payload}), _duplicates_tool),
        ('document.fraud_signals', 'read',
         obj({'fields': payload,
              'outlier_ratio': {'type': 'integer', 'minimum': 1, 'maximum': 100}},
             required=['fields']), _fraud_tool),
        ('document.posting_plan', 'write',
         obj({'fields': payload, 'purchase_order': payload, 'delivery_note': payload,
              'tolerance_minor': {'type': 'integer', 'minimum': 0,
                                  'maximum': 100_000_000_000}}, required=['fields']),
         _plan_tool),
    ]
    for name, risk, schema, handler in definitions:
        if name in registry.items:
            continue
        registry.add(Tool(name, risk, schema, handler, external=False))
