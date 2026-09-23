"""Atomic integer-microunit reservations, dispatch fencing and usage settlement.

This is a local spending guard, not subscription billing or a provider invoice.
Unknown outcomes retain their reservation until an owner reconciles with evidence.
"""
import datetime
import re
import uuid

from .engine import Conflict, Forbidden, NotFound, RateLimited, digest

MAX_AMOUNT = 10**15
SCHEMA = '''
CREATE TABLE IF NOT EXISTS p_budget_settings(
 tenant TEXT PRIMARY KEY, currency TEXT NOT NULL, limit_micro INTEGER NOT NULL,
 max_inflight INTEGER NOT NULL, generation INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS p_budget_accounts(
 tenant TEXT NOT NULL, period TEXT NOT NULL, spent_micro INTEGER NOT NULL DEFAULT 0,
 reserved_micro INTEGER NOT NULL DEFAULT 0, inflight INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant,period));
CREATE TABLE IF NOT EXISTS p_budget_reservations(
 tenant TEXT NOT NULL, id TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 period TEXT NOT NULL, amount_micro INTEGER NOT NULL, actual_micro INTEGER,
 status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY(tenant,id), UNIQUE(tenant,request_key));
CREATE INDEX IF NOT EXISTS p_budget_pending ON p_budget_reservations(tenant,status,created);
'''


def amount(value, *, zero=True):
    if type(value) is not int or not (0 if zero else 1) <= value <= MAX_AMOUNT:
        raise ValueError('Bounded integer microunits required')
    return value


def bounded(value, maximum=256):
    """A bounded identifier, with the same rules `database.contract.text` uses.

    The two gates guard one fact -- "a bounded identifier" -- and they disagreed.
    Measured before the fix: this one ACCEPTED a DEL character (0x7F) and a value
    with a leading space, while `contract.text` refused both. A tenant id, an actor
    or a piece of reconcile evidence that one layer accepts and another refuses is
    a value whose validity depends on which door it came through, so the rules are
    stated identically here rather than approximately.

    `value != value.strip()` is what rejects surrounding whitespace; the DEL check
    is what `ord(c) < 32` alone misses, because 0x7F is not a C0 control.
    """
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('Bounded identifier required')
    return value


class UsageBudget:
    def __init__(self, engine): self.engine = engine

    def _period(self):
        return datetime.datetime.fromtimestamp(self.engine.clock(), datetime.timezone.utc).strftime('%Y-%m')

    def configure(self, tenant, actor, currency, limit_micro, max_inflight=4):
        bounded(tenant); bounded(actor); amount(limit_micro, zero=False)
        if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
            raise ValueError('Three-letter currency required')
        if type(max_inflight) is not int or not 1 <= max_inflight <= 100:
            raise ValueError('Parallel call limit must be 1..100')
        e = self.engine
        with e.tx() as db:
            e.require_authority(db, tenant, 'web', actor, ('owner',))
            old = db.execute('SELECT * FROM p_budget_settings WHERE tenant=?', (tenant,)).fetchone()
            if old and old['currency'] != currency:
                raise Conflict('Currency cannot change while historical ledger exists')
            generation = old['generation'] + 1 if old else 1
            db.execute('INSERT INTO p_budget_settings VALUES(?,?,?,?,?) ON CONFLICT(tenant) DO UPDATE SET '
                       'limit_micro=excluded.limit_micro,max_inflight=excluded.max_inflight,generation=excluded.generation',
                       (tenant, currency, limit_micro, max_inflight, generation))
            e.audit(db, tenant, '', 'budget.configured', actor, {'currency': currency, 'limit_micro': limit_micro, 'generation': generation})
        return self.summary(tenant)

    def summary(self, tenant):
        bounded(tenant)
        with self.engine.read() as db:
            settings = db.execute('SELECT * FROM p_budget_settings WHERE tenant=?', (tenant,)).fetchone()
            if not settings: raise NotFound('Budget not configured')
            account = db.execute('SELECT * FROM p_budget_accounts WHERE tenant=? AND period=?', (tenant, self._period())).fetchone()
            # Reservations from prior months still count against concurrent calls.
            inflight = db.execute("SELECT count(*) FROM p_budget_reservations WHERE tenant=? AND status IN ('reserved','dispatching','uncertain')", (tenant,)).fetchone()[0]
            out = dict(settings)
            out.update(period=self._period(), spent_micro=account['spent_micro'] if account else 0,
                       reserved_micro=account['reserved_micro'] if account else 0, inflight=inflight)
            out['available_micro'] = max(0, out['limit_micro'] - out['spent_micro'] - out['reserved_micro'])
            out['limit_exceeded'] = out['spent_micro'] + out['reserved_micro'] > out['limit_micro']
            out['warning_80_percent'] = (out['spent_micro'] + out['reserved_micro']) * 5 >= out['limit_micro'] * 4
            return out

    def pending(self, tenant, limit=100):
        bounded(tenant)
        if type(limit) is not int or not 1 <= limit <= 100: raise ValueError('Invalid limit')
        with self.engine.read() as db:
            return [dict(row) for row in db.execute('SELECT id,request_key,period,amount_micro,status,created,updated '
                "FROM p_budget_reservations WHERE tenant=? AND status IN ('reserved','dispatching','uncertain') ORDER BY created,id LIMIT ?", (tenant, limit))]

    def _row(self, db, tenant, reservation):
        row = db.execute('SELECT * FROM p_budget_reservations WHERE tenant=? AND id=?', (tenant, reservation)).fetchone()
        if not row: raise NotFound('Budget reservation not found')
        return row

    def reserve(self, tenant, request_key, fingerprint, estimate_micro, currency):
        bounded(tenant); bounded(request_key); amount(estimate_micro)
        if not isinstance(fingerprint, str) or not re.fullmatch('[a-f0-9]{64}', fingerprint):
            raise ValueError('Request fingerprint required')
        e = self.engine
        with e.tx() as db:
            e.require_active(db, tenant)
            settings = db.execute('SELECT * FROM p_budget_settings WHERE tenant=?', (tenant,)).fetchone()
            if not settings or settings['currency'] != currency:
                raise Forbidden('Configured budget with matching currency required')
            full = digest({'request': fingerprint, 'estimate': estimate_micro, 'currency': currency})
            old = db.execute('SELECT * FROM p_budget_reservations WHERE tenant=? AND request_key=?', (tenant, request_key)).fetchone()
            if old:
                if old['fingerprint'] != full: raise Conflict('Budget key reused with different request')
                return old['id']
            count = db.execute("SELECT count(*) FROM p_budget_reservations WHERE tenant=? AND status IN ('reserved','dispatching','uncertain')", (tenant,)).fetchone()[0]
            if count >= settings['max_inflight']: raise RateLimited('Budget parallel call limit reached')
            period = self._period()
            db.execute('INSERT OR IGNORE INTO p_budget_accounts(tenant,period) VALUES(?,?)', (tenant, period))
            updated = db.execute('UPDATE p_budget_accounts SET reserved_micro=reserved_micro+?,inflight=inflight+1 '
                'WHERE tenant=? AND period=? AND spent_micro+reserved_micro+?<=?',
                (estimate_micro, tenant, period, estimate_micro, settings['limit_micro']))
            if updated.rowcount != 1: raise RateLimited('Budget exhausted')
            rid = uuid.uuid4().hex
            db.execute('INSERT INTO p_budget_reservations VALUES(?,?,?,?,?,?,NULL,?,?,?)',
                (tenant, rid, request_key, full, period, estimate_micro, 'reserved', e.clock(), e.clock()))
            e.audit(db, tenant, '', 'budget.reserved', 'budget', {'id': rid, 'amount_micro': estimate_micro, 'period': period})
            return rid

    def dispatch(self, tenant, reservation):
        with self.engine.tx() as db:
            self.engine.require_active(db, tenant)
            row = self._row(db, tenant, reservation)
            if row['status'] != 'reserved': raise Conflict('Budget call already dispatched or retired')
            if row['period'] != self._period():
                raise Conflict('Undispatched reservation expired at period boundary; cancel and reserve again')
            settings = db.execute('SELECT * FROM p_budget_settings WHERE tenant=?', (tenant,)).fetchone()
            account = db.execute('SELECT * FROM p_budget_accounts WHERE tenant=? AND period=?', (tenant, row['period'])).fetchone()
            count = db.execute("SELECT count(*) FROM p_budget_reservations WHERE tenant=? AND status IN ('reserved','dispatching','uncertain')", (tenant,)).fetchone()[0]
            if not settings or account['spent_micro'] + account['reserved_micro'] > settings['limit_micro'] or count > settings['max_inflight']:
                raise RateLimited('Budget policy changed before dispatch')
            db.execute("UPDATE p_budget_reservations SET status='dispatching',updated=? WHERE tenant=? AND id=?", (self.engine.clock(), tenant, reservation))
            self.engine.audit(db, tenant, '', 'budget.dispatched', 'budget', {'id': reservation})

    def uncertain(self, tenant, reservation):
        with self.engine.tx() as db:
            row = self._row(db, tenant, reservation)
            if row['status'] == 'uncertain': return
            if row['status'] != 'dispatching': raise Conflict('Only a dispatched call can be uncertain')
            db.execute("UPDATE p_budget_reservations SET status='uncertain',updated=? WHERE tenant=? AND id=?", (self.engine.clock(), tenant, reservation))
            self.engine.audit(db, tenant, '', 'budget.uncertain', 'budget', {'id': reservation})

    def cancel(self, tenant, reservation):
        with self.engine.tx() as db:
            row = self._row(db, tenant, reservation)
            if row['status'] == 'cancelled': return
            if row['status'] != 'reserved': raise Conflict('A dispatched charge cannot be cancelled')
            self._settle(db, tenant, row, 0, 'cancelled', 'budget')

    def _settle(self, db, tenant, row, actual, status, actor):
        db.execute('UPDATE p_budget_accounts SET reserved_micro=reserved_micro-?,spent_micro=spent_micro+?,inflight=inflight-1 '
                   'WHERE tenant=? AND period=?', (row['amount_micro'], actual, tenant, row['period']))
        db.execute('UPDATE p_budget_reservations SET status=?,actual_micro=?,updated=? WHERE tenant=? AND id=?',
                   (status, actual, self.engine.clock(), tenant, row['id']))
        self.engine.audit(db, tenant, '', 'budget.' + status, actor,
                          {'id': row['id'], 'actual_micro': actual, 'reservation_exceeded': actual > row['amount_micro']})

    def settle(self, tenant, reservation, actual_micro):
        amount(actual_micro)
        with self.engine.tx() as db:
            row = self._row(db, tenant, reservation)
            if row['status'] == 'settled':
                if row['actual_micro'] != actual_micro: raise Conflict('Settlement amount changed')
                return
            if row['status'] != 'dispatching': raise Conflict('Call is not awaiting automatic settlement')
            self._settle(db, tenant, row, actual_micro, 'settled', 'budget')

    def reconcile(self, tenant, reservation, actor, actual_micro, evidence):
        bounded(actor); bounded(evidence, 500); amount(actual_micro)
        with self.engine.tx() as db:
            self.engine.require_authority(db, tenant, 'web', actor, ('owner',))
            row = self._row(db, tenant, reservation)
            if row['status'] not in {'reserved', 'dispatching', 'uncertain'}:
                raise Conflict('Reservation has already been reconciled')
            self._settle(db, tenant, row, actual_micro, 'reconciled', actor)
            self.engine.audit(db, tenant, '', 'budget.reconciliation_evidence', actor, {'id': reservation, 'evidence': evidence})


def token_cost(input_tokens, output_tokens, pricing):
    for n in (input_tokens, output_tokens):
        if type(n) is not int or not 0 <= n <= 10**8: raise ValueError('Invalid provider token usage')
    input_rate = amount(pricing.get('input_micro_per_million'))
    output_rate = amount(pricing.get('output_micro_per_million'))
    return amount((input_tokens * input_rate + output_tokens * output_rate + 999999) // 1000000)


def provider_tokens(usage, name):
    """One provider receipt -> (prompt_tokens, completion_tokens).

    OpenAI reports two numbers. Anthropic reports input in THREE buckets --
    ``input_tokens`` plus ``cache_creation_input_tokens`` and
    ``cache_read_input_tokens`` (skill, python/claude-api/README.md ->
    Verifying Cache Hits) -- and output as ``output_tokens``
    (curl/examples.md -> Parsing the response).

    All three input buckets are charged here at the full input rate. That is a
    deliberate upper bound, not the provider's arithmetic: a cache READ bills
    at roughly a tenth of an uncached input token and a cache WRITE at about
    1.25x, so this ledger OVERSTATES a cache-heavy call. This is a local
    spending guard, so erring towards holding money the tenant may not owe is
    the safe direction; an owner reconciles against the real invoice.

    Missing or non-integer counts raise, which keeps the reservation.
    """
    if name == 'anthropic':
        if not {'input_tokens', 'output_tokens'}.issubset(usage):
            raise ValueError('Model usage receipt missing')
        counts = [usage['input_tokens'], usage.get('cache_creation_input_tokens', 0),
                  usage.get('cache_read_input_tokens', 0), usage['output_tokens']]
        for n in counts:
            if type(n) is not int: raise ValueError('Invalid provider token usage')
        return counts[0] + counts[1] + counts[2], counts[3]
    if not {'prompt_tokens', 'completion_tokens'}.issubset(usage):
        raise ValueError('Model usage receipt missing')
    return usage['prompt_tokens'], usage['completion_tokens']


def metered_completion(engine, tenant, cfg, transport, url, body, headers, request_key=None):
    """Optional pricing configuration enables mandatory ledger reservation.

    Missing/malformed usage keeps the full reservation; no automatic refund.
    Model JSON validity is separate from whether the provider incurred a charge.
    """
    from .engine import encode
    from .model_transport import provider, validate_url
    validate_url(cfg, url)  # Configuration errors must not consume a reservation.
    name = provider(cfg)    # An unknown dialect must not reserve either.
    if 'usage_budget_required' in cfg and type(cfg['usage_budget_required']) is not bool:
        raise ValueError('usage_budget_required must be boolean')
    pricing = cfg.get('usage_budget')
    if pricing is None:
        if cfg.get('usage_budget_required') is True:
            raise Forbidden('Model usage budget configuration required')
        return transport(url, body, headers)
    if not isinstance(pricing, dict) or set(pricing) != {'currency', 'input_micro_per_million', 'output_micro_per_million'}:
        raise ValueError('Explicit model pricing required')
    budget = UsageBudget(engine)
    # Conservative byte-based input estimate plus message/token framing allowance.
    # Tokenizers/provider hidden input may differ: overruns are recorded, never hidden.
    estimate = token_cost(len(encode(body).encode('utf-8')) + 4096, body['max_tokens'], pricing)
    key = request_key or 'model:' + uuid.uuid4().hex
    rid = budget.reserve(tenant, key, digest({'url': url, 'body': body, 'pricing': pricing}), estimate, pricing['currency'])
    budget.dispatch(tenant, rid)
    try:
        response = transport(url, body, headers)
        usage = response.get('usage') if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            raise ValueError('Model usage receipt missing')
        actual = token_cost(*provider_tokens(usage, name), pricing)
    except Exception:
        budget.uncertain(tenant, rid)
        raise RuntimeError('Metered model call failed or usage unavailable; reconciliation required') from None
    budget.settle(tenant, rid, actual)
    return response
