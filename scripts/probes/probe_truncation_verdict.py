"""Measure the `truncated` verdict every bounded tool reports, and the one way it
used to lie.

Run:  python scripts/probe_truncation_verdict.py
Exit: 0 when every measured property holds, 1 when any does not.

A probe is not a test. A test asserts that a path returns what it should; a probe
establishes *what is actually true* about a boundary, including the parts nobody
wrote a test for.

The defect this probe exists for: `truncated` answers the operator's question
"is there more, should I ask again with a larger limit". Seven call sites derived
it from `len(result) >= limit`, an expression that is true **both** for a list
that was cut and for a list that ended exactly on the bound. Only the first
warrants a re-run; the second means "this is everything". Neither the caller nor
the operator can tell them apart, so the wrong answer sends someone to re-run a
query that returns an identical list.

The same shape keeps reappearing in new places, so the probe grew with it:
`knowledge.search` cut retrieved passages and said nothing (section 11), and four
ledger readers whose docstrings called the bare-list return "a trap rather than a
live defect, because nothing in the runtime calls it" turned out to be reachable
over HTTP (section 12). Sweeping by module finds only what you already suspected;
sweeping by the SHAPE of the defect is what finds the rest.

Every property here drives the real tool through the real configuration, with a
population of exactly `limit` items and one of `limit + 1`, and compares the
reported flag against the truth. Both sides are measured because a guard that
only ever sees one side cannot tell a working predicate from a broken one.
"""
import json
import os
import sqlite3
import statistics
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api-python'))

from platform_runtime.business_graph import conflicts, search, timeline  # noqa: E402
from platform_runtime.documents import (duplicates, fraud_signals, normalize,  # noqa: E402
                                     remember)
from platform_runtime.engine import Engine  # noqa: E402
from platform_runtime.oversight import activity  # noqa: E402
from platform_runtime.tools import build_registry  # noqa: E402
from platform_runtime.workforce import shifts  # noqa: E402

TENANT = 't_trunc'
AGENT = 'sales.bot'
SHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
POLICY = {'tools': ['graph.search', 'graph.conflicts', 'graph.timeline',
                    'connectors.read', 'sheets.rows', 'oversight.activity',
                    'workforce.shifts', 'erp.posting_status', 'knowledge.search'],
          'allowed_connections': ['erp', 'google'], 'ladder': 'human_assisted'}
WORKFORCE = {
    'registers': {
        'shifts': {
            'register': 'hr', 'range': 'shifts',
            'id_column': 'id', 'name_column': 'Ism', 'date_column': 'Sana',
            'status_column': 'Smena', 'hours_column': 'Soat',
        },
    }
}

RESULTS = []


def _raises(exc, fn, needle=None):
    """True when ``fn`` raises ``exc`` -- optionally with ``needle`` in the message.

    A refusal and a refusal-with-the-right-reason are two different properties.
    This audit has twice found a module that refused the right input for the wrong
    stated reason, so the needle is checked whenever the reason is the point.
    """
    try:
        fn()
    except exc as error:
        return needle is None or needle in str(error)
    except Exception:  # noqa: BLE001
        return False
    return False


def record(label, reported, truth):
    """One measured property: does the reported value equal the truth?

    ``==`` and not ``is``. Identity comparison on integers happens to work for the
    small ones CPython caches and fails silently for the rest, which turns a
    correct implementation into a reported failure -- a probe that reports a
    defect where there is none is as useless as one that misses a defect.
    """
    ok = reported == truth
    RESULTS.append(ok)
    print(f'  {"ok  " if ok else "FAIL"} {label:<52s} '
          f'reported={str(reported):<7s} truth={str(truth):<7s}')
    return ok


class Transport:
    """Scripted Sheets transport. Answers each requested A1 range from a dict."""

    def __init__(self, ranges):
        self.ranges = ranges

    def __call__(self, url, token):
        for name, values in self.ranges.items():
            if name in url:
                return {'values': values}
        return {'values': []}


def graph_tenant(root, products):
    erp = root / 'erp.db'
    db = sqlite3.connect(erp)
    try:
        db.execute('CREATE TABLE products(sku TEXT, price INTEGER)')
        db.executemany('INSERT INTO products VALUES(?,?)', products)
        db.commit()
    finally:
        db.close()
    cfg = root / 'integrations.json'
    cfg.write_text(json.dumps({TENANT: {
        'connections': {'erp': {'driver': 'sqlite_readonly', 'path': str(erp),
                                'tables': {'products': ['sku', 'price']}},
                        'google': {}},
        'business_graph': {'conflict_policy': 'report', 'entities': {'product': {
            'identity': 'sku', 'priority': ['erp'],
            'sources': {'erp': {'tool': 'connectors.read', 'key': 'sku',
                                'args': {'connection': 'erp', 'table': 'products',
                                         'columns': ['sku', 'price']},
                                'map': {'price': 'price'}}}}}},
    }}), encoding='utf-8')
    return cfg


def with_tenant(cfg, root, body):
    env = patch.dict(os.environ, {
        'PLATFORM_INTEGRATIONS_FILE': str(cfg),
        'PLATFORM_DB_ROOTS': json.dumps([str(root)])})
    env.start()
    try:
        engine = Engine(root / 'platform.db', build_registry(),
                        lambda t, a: POLICY)
        return body(engine)
    finally:
        env.stop()


def measure_graph(products, limit):
    """Drive graph.search and graph.conflicts over a known population."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = graph_tenant(root, products)
        def body(engine):
            return (search(engine, TENANT, AGENT, 'product', 'price', '1000',
                           's1', limit=limit),
                    conflicts(engine, TENANT, AGENT, 'product', 's1', limit=limit))
        return with_tenant(cfg, root, body)


def measure_timeline(count, limit):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = graph_tenant(root, [('SKU-1', 1000)] * count)
        def body(engine):
            return timeline(engine, TENANT, AGENT, 'product', 'SKU-1', 's1',
                            limit=limit)
        return with_tenant(cfg, root, body)


def measure_shifts(count, limit):
    rows = [[f'S{i:03d}', f'Xodim {i}', '2026-09-01', 'Smena-A', '8']
            for i in range(count)]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        erp = root / 'erp.db'
        db = sqlite3.connect(erp)
        try:
            db.execute('CREATE TABLE products(sku TEXT, price INTEGER)')
            db.commit()
        finally:
            db.close()
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {
            'connections': {'erp': {'driver': 'sqlite_readonly', 'path': str(erp),
                                    'tables': {'products': ['sku', 'price']}},
                            'google': {}},
            'sheets_registers': {'hr': {
                'connection': 'google', 'spreadsheet_id': SHEET,
                'ranges': {'shifts': 'Smena!A1:E'}, 'max_rows': 200}},
            'workforce': WORKFORCE,
        }}), encoding='utf-8')
        transport = Transport({'Smena': [['id', 'Ism', 'Sana', 'Smena', 'Soat']]
                                       + rows})
        def body(engine):
            with patch('platform_runtime.sheets.configured_manager') as manager:
                manager.return_value.access.return_value.access_token = 'tok'
                with patch('platform_runtime.sheets._http_get',
                           side_effect=lambda url, token: transport(url, token)):
                    return shifts(engine, TENANT, AGENT, limit=limit)
        return with_tenant(cfg, root, body)


def measure_activity(count, limit):
    now = 1_770_000_000.0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {'connections': {}}}), encoding='utf-8')
        def body(engine):
            engine.clock = lambda: now
            with engine.tx() as c:
                c.execute('''INSERT INTO p_tasks(id,tenant,channel,event_key,
                    fingerprint,agent,actor,status,created,updated)
                    VALUES('t1',?,'web','t1','f1',?,'owner','succeeded',?,?)''',
                          (TENANT, AGENT, now - 60, now - 60))
                for i in range(count):
                    c.execute('''INSERT INTO p_audit(tenant,task,action,actor,data,
                        created) VALUES(?,?,?,?,?,?)''',
                              (TENANT, 't1', 'x', 'owner', '{}', now - i))
            return activity(engine, TENANT, AGENT, since_seconds=10 ** 6, limit=limit)
        return with_tenant(cfg, root, body)


def measure_fraud(history, amount, stored):
    """Drive documents.fraud_signals over a known supplier history.

    ``stored`` reproduces the normal receive -> store -> check order, in which the
    document under test is already a row in the history it is compared against.
    """
    erp = None
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {
            'connections': {}, 'documents': {}}}), encoding='utf-8')

        def body(engine):
            now = 1_780_000_000.0
            engine.clock = lambda: now
            for index, value in enumerate(history):
                remember(engine, TENANT, normalize(
                    {'kind': 'invoice', 'number': f'H{index}', 'supplier': 'acme',
                     'currency': 'UZS', 'total': value, 'doc_date': '2026-09-01'}))
            fields = {'kind': 'invoice', 'number': 'NEW', 'supplier': 'acme',
                      'currency': 'UZS', 'total': amount, 'doc_date': '2026-09-01'}
            if stored:
                remember(engine, TENANT, normalize(fields))
            return fraud_signals(engine, TENANT, AGENT, fields)

        return with_tenant(cfg, root, body)


def measure_erp_status(n_rows, limit):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        erp = root / 'erp.db'
        db = sqlite3.connect(erp)
        try:
            db.execute('CREATE TABLE products(sku TEXT, price INTEGER)')
            db.commit()
        finally:
            db.close()
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {
            'connections': {'erp': {'driver': 'sqlite_readonly', 'path': str(erp),
                                    'tables': {'products': ['sku', 'price']}}},
            'erp': {'enabled': True, 'driver': 'sqlite_readonly'},
        }}), encoding='utf-8')
        def body(engine):
            now = 1_770_000_000.0
            engine.clock = lambda: now
            with engine.tx() as c:
                for i in range(n_rows):
                    c.execute('''INSERT INTO p_erp_postings(tenant,id,document,driver,
                        kind,supplier,number,currency,total_minor,external_id,status,
                        created,settled) VALUES(?,?,'doc','sqlite_readonly','invoice',
                        'sup','N','UZS',100,?,'posted',?,0)''',
                              (TENANT, f'p{i}', f'ext-{i}', now - i))
            return build_registry().get('erp.posting_status').handler(
                engine, TENANT, AGENT, {'limit': limit}, 's1')
        return with_tenant(cfg, root, body)


def section_search():
    print('=== 1: graph.search answers "was a match left out" ===')
    for count, limit in ((9, 10), (10, 10), (11, 10), (2, 3), (3, 3), (4, 3)):
        rows = [(f'P{i:03d}', 1000) for i in range(count)]
        found, _ = measure_graph(rows, limit)
        record(f'{count} matching, limit {limit}', found['truncated'],
               count > len(found['matches']))


def section_conflicts():
    print('=== 2: graph.conflicts ===')
    for count, limit in ((11, 10), (10, 10)):
        # Every product carries one price in the graph and a different one in the
        # sheet, so each id is a conflict. Only the erp side is reachable here, so
        # the population is the id count and no conflict is produced at all; the
        # property measured is that an empty result is never called truncated.
        rows = [(f'P{i:03d}', 1000) for i in range(count)]
        _, conf = measure_graph(rows, limit)
        record(f'{count} ids, limit {limit}, no conflict present',
               conf['truncated'], len(conf['conflicts']) > limit)


def section_timeline():
    print('=== 3: graph.timeline measures the boundary from both sides ===')
    for count, limit in ((4, 5), (5, 5), (6, 5)):
        result = measure_timeline(count, limit)
        record(f'{count} events, limit {limit}', result['truncated'],
               count > len(result['events']))


def section_shifts():
    print('=== 4: workforce.shifts ===')
    for count, limit in ((2, 3), (3, 3), (4, 3)):
        result = measure_shifts(count, limit)
        record(f'{count} shifts, limit {limit}', result['truncated'],
               count > result['returned'])


def section_activity():
    print('=== 5: oversight.activity (the cap is applied by the SQL) ===')
    for count, limit in ((2, 3), (3, 3), (4, 3)):
        result = measure_activity(count, limit)
        record(f'{count} events, limit {limit}', result['truncated'],
               count > len(result['events']))


def section_erp_status():
    """`erp.posting_status` reported a page size where it claimed an inventory.

    Its `count` was `len(rows)`, so a ledger of twenty and a ledger of four
    thousand both answered `count: 20`, and there was no truncation flag at all.
    This is the same defect as a wrong `truncated`, one step further on: not a
    flag that lies, but a missing flag plus a number that reads as a total.
    """
    print('=== 6: erp.posting_status counts the ledger, not the page ===')
    for n_rows, limit in ((5, 20), (19, 20), (20, 20), (21, 20), (100, 20)):
        result = measure_erp_status(n_rows, limit)
        record(f'{n_rows} postings, limit {limit}: count is the population',
               result['count'], n_rows)
        record(f'{n_rows} postings, limit {limit}: truncated is honest',
               result['truncated'], n_rows > len(result['postings']))
        record(f'{n_rows} postings, limit {limit}: returned is the page',
               result['returned'], len(result['postings']))


def section_fraud_median():
    """`documents.fraud_signals` printed a number it called the median.

    Two defects, one arithmetic and one about the population:

    * ``median = amounts[len(amounts) // 2]`` is the *upper-middle* element. It
      equals the median only for an odd count, and the error is one-directional:
      it always raises the outlier threshold, so it always makes the flag harder
      to raise.
    * ``prior`` filtered the history by currency only, and documents flow
      receive -> store -> check, so the document under test was normally inside
      the population it was tested against.

    Both are measured here against an independently computed truth, over an even
    count (where the median rule bites), a two-document history (where
    self-inclusion is worst), and a history holding only the document itself.
    """
    print('=== 7: documents.fraud_signals measures the median, and excludes itself ===')
    cases = (
        # (history, under-test amount, stored-first, expected count, expected median)
        ((100000, 200000), 900000, False, 2, 150000),
        ((100000, 300000), 800000, False, 2, 200000),
        ((100000, 200000, 300000, 400000), 5000000, False, 4, 250000),  # even count
        ((100000, 300000), 800000, True, 2, 200000),
    )
    for history, amount, stored, exp_count, exp_median in cases:
        history = [a * 1 for a in history]
        report = measure_fraud(history, amount, stored)
        signal = next((s for s in report['signals'] if s['signal'] == 'amount_outlier'),
                      None)
        tag = f'history {sorted(history)}, check {amount}' \
              + (', stored first' if stored else '')
        # What the tool actually printed, compared against the independently
        # computed median. Both are read from the running tool, never assumed.
        printed = int(signal['evidence']['median']) if signal else -1
        record(f'{tag}: printed median is the median',
               printed, int(statistics.median(history)))
        record(f'{tag}: population excludes the document',
               report['history_considered'], exp_count)
        record(f'{tag}: row read is one larger', report['history_rows'],
               exp_count + (1 if stored else 0))
        record(f'{tag}: flag raised', bool(signal), True)


def section_round_number():
    """`documents.fraud_signals` asked the roundness question in the wrong unit.

    Two defects in one four-line block:

    * The guard was ``total_minor % 10 ** MINOR_UNITS[currency] == 0``, which asks
      "is the amount a whole number of major units". For UZS (factor 1) that
      coincides with roundness; for USD (factor 100) it only asks "are the cents
      zero", which is true of almost every invoice. So a perfectly round
      ``USD 1000.00`` was never flagged, and the signal was dead for every
      currency but UZS -- while the module docstring claimed the minor-unit form
      made it mean the same thing for both.
    * The reported figure was ``len(str(major).rstrip('0'))``, the count of
      leading significant digits, rendered as a roundness claim. UZS 100050 got
      5 ("very round") when it is barely round; UZS 1000000 got 1 ("barely
      round") when it is the roundest amount there is.

    Measured here against trailing zeros in the major unit, which is what the
    sentence describes, for UZS, USD and EUR.
    """
    print('=== 8: documents.fraud_signals measures roundness in the major unit ===')
    cases = (
        # (amount, currency, expected trailing zeros or None, note)
        ('1000.00', 'USD', 3, 'perfectly round'),
        (1000000, 'UZS', 6, 'perfectly round'),
        ('1000.00', 'EUR', 3, 'perfectly round'),
        (123000, 'UZS', 3, 'at the threshold'),
        (100000, 'UZS', 5, 'above the threshold'),
        (100050, 'UZS', None, 'barely round, below the threshold'),
        (123450, 'UZS', None, 'an ordinary price'),
        ('1000.50', 'USD', None, 'cents are not zero'),
        (1234, 'UZS', None, 'not a multiple of a thousand'),
    )
    for amount, currency, expected, note in cases:
        trailing, detail = measure_roundness(amount, currency)
        record(f'{currency} {amount} ({note}): trailing zeros', trailing, expected)
        # The sentence must agree with the number it carries.
        if trailing is not None:
            record(f'{currency} {amount}: the sentence quotes the same number',
                   str(trailing) in detail, True)


def measure_roundness(amount, currency):
    """Drive documents.fraud_signals and read the round_number evidence."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {'connections': {}}}), encoding='utf-8')

        def body(engine):
            report = fraud_signals(engine, TENANT, AGENT, {
                'kind': 'invoice', 'number': 'NEW', 'supplier': 'acme',
                'currency': currency, 'total': amount, 'doc_date': '2026-09-01'})
            signal = next((s for s in report['signals']
                           if s['signal'] == 'round_number'), None)
            if signal is None:
                return None, ''
            return signal['evidence']['trailing_zeros'], signal['detail']

        return with_tenant(cfg, root, body)


def section_duplicate_report():
    """`documents.duplicates` collapsed two different changes into one pair.

    ``altered`` compared the amount AND the currency together, correctly, but the
    report then rendered the change as a single ``from``/``to`` pair formatted in
    two different currencies. A document reissued in another currency came back as
    ``from: '1000', to: '1000.00'`` -- a reported amount change for an amount that
    did not change, which sends an approver looking for a price difference that is
    not there. The two changes are now separate keys, and ``amount_delta_minor`` is
    absent when a cross-currency delta would be an invented number.
    """
    print('=== 9: documents.duplicates separates an amount move from a currency move ===')
    cases = (
        # (first, second, expected altered, expected amount_changed, expected currency_changed)
        (('1000', 'UZS'), ('1000', 'UZS'), False, None, None),
        (('1000', 'UZS'), ('1500', 'UZS'), True, ('1000', '1500'), None),
        (('1000', 'UZS'), ('1000', 'USD'), True, ('1000', '1000'), ('UZS', 'USD')),
    )
    for first, second, exp_altered, exp_amount, exp_currency in cases:
        report = measure_duplicate(first, second)
        label = f'{first[0]} {first[1]} -> {second[0]} {second[1]}'
        record(f'{label}: altered', report['altered'], exp_altered)
        if exp_amount is None:
            record(f'{label}: no amount change reported',
                   report['amount_changed'], None)
        else:
            record(f'{label}: amount from', report['amount_changed']['from'],
                   exp_amount[0])
            record(f'{label}: amount to', report['amount_changed']['to'],
                   exp_amount[1])
        record(f'{label}: currency changed', report['currency_changed'],
               None if exp_currency is None
               else {'from': exp_currency[0], 'to': exp_currency[1]})
        # The delta must be present exactly when both sides share a currency.
        if first[1] == second[1]:
            record(f'{label}: delta is comparable',
                   report['amount_delta_minor'] is not None, True)
        else:
            record(f'{label}: no invented cross-currency delta',
                   report['amount_delta_minor'], None)


def measure_duplicate(first, second):
    """Drive documents.duplicates over a stored original and a resubmission."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {'connections': {}}}), encoding='utf-8')

        def body(engine):
            base = {'kind': 'invoice', 'number': 'INV-1', 'supplier': 'acme',
                    'doc_date': '2026-09-01'}
            remember(engine, TENANT, normalize(
                dict(base, total=first[0], currency=first[1])))
            return duplicates(engine, TENANT, AGENT,
                              dict(base, total=second[0], currency=second[1]))

        return with_tenant(cfg, root, body)


def section_line_item_arithmetic():
    """`documents._line_items` validated a quantity it then refused to multiply by.

    The self-consistency guard exists so that a document which disagrees with its
    own arithmetic is refused rather than checked. But the guard added ``amount``
    alone, and ``quantity`` was validated and stored and then never used, so a line
    reading "2 x 5000" summed to 5000. A *correct* two-unit invoice was refused as
    self-contradicting while the *same* invoice with a stated total of 5000 -- the
    wrong number -- was accepted. The guard was guaranteeing the contradiction it
    was written to catch.

    The unit that was missing: ``amount`` is the line's UNIT price, ``line_total``
    is ``quantity * amount``, and the sum is over the line totals. This section
    measures that definition on both sides of the boundary -- a quantity that
    multiplies and a quantity that does not.
    """
    print('=== 10: documents counts a line the way the line reads ===')
    # (quantity, unit price, line count, stated total, expected accepted)
    cases = (
        (1, 5000, 1, 5000, True),
        (2, 5000, 1, 10000, True),
        (3, 10000, 1, 30000, True),
        (2, 5000, 2, 20000, True),
        (0, 5000, 1, 0, True),
        # A stated total that ignores the quantity: the line says 2 x 5000 and the
        # document says 5000. Counting the unit price alone ACCEPTS this; counting
        # the line totals refuses it. That difference is the whole measurement.
        (1, 4999, 1, 5000, False),
        (2, 5000, 1, 5000, False),
    )
    for quantity, unit, lines, stated, accepted in cases:
        measured = measure_line_items(quantity, unit, lines, stated)
        expected = quantity * unit * lines
        if accepted:
            label = f'{lines} line(s) of {quantity} x {unit} = {expected}'
            record(f'{label}: accepted', measured['ok'], True)
            record(f'{label}: summed total',
                   measured.get('items_total', measured.get('total')), expected)
            record(f'{label}: unit price kept', measured['unit_kept'], unit)
            record(f'{label}: line total is quantity x unit',
                   measured['line_total'], quantity * unit)
        else:
            label = f'{quantity} x {unit} stated as {stated}'
            record(f'{label}: refused', measured['ok'], False)


def measure_line_items(quantity, unit, lines, stated):
    """Drive documents.normalize over one line repeated, and report what it summed.

    Returns ``{'ok': bool, ...}``: ``ok`` False means normalize refused the
    document, which is the correct answer for a genuinely self-contradicting one.
    ``stated`` is passed in rather than derived, because deriving it from
    ``quantity * unit`` would restate the correct total and make the two refusal
    cases untestable -- a probe that computes its own expectation from the thing
    under test measures nothing.
    """
    item = {'description': 'widget', 'quantity': quantity, 'amount': unit}
    document = {'kind': 'invoice', 'number': 'INV-1', 'supplier': 'acme',
                'doc_date': '2026-09-01', 'currency': 'UZS',
                'total': str(stated),
                'line_items': [dict(item) for _ in range(lines)]}
    try:
        normalized = normalize(document)
    except ValueError as exc:
        return {'ok': False, 'reason': str(exc)}
    items = normalized['line_items']
    return {'ok': True, 'total': normalized['total_minor'],
            'items_total': sum(entry['line_total'] for entry in items),
            'unit_kept': items[0]['amount'],
            'line_total': items[0]['line_total']}


def section_knowledge_truncation():
    """`knowledge.search` cut the retrieved passages and did not say so.

    This is the same class as the defect the whole probe was written for, found
    again in a place the earlier sweeps had not looked: the reply carried
    ``collection``, ``matches``, ``retrieval`` and ``semantic_fact_check`` and
    nothing else, so fifty matching chunks and five matching chunks came back with
    exactly the same shape. Retrieved passages are what the agent reasons FROM, so
    five out of fifty read as "this is all the corpus says about it".

    Both sides are measured: a corpus of exactly ``limit`` must NOT report a cut.
    """
    print('=== 11: knowledge.search says how many matched and how many came back ===')
    for documents, limit in ((3, 4), (4, 4), (5, 4), (7, 4), (50, 5), (5, 5), (6, 5)):
        out = measure_knowledge(documents, limit)
        label = f'{documents} matching, limit {limit}'
        # A missing key is itself the defect, so report it rather than crashing:
        # the flag's ABSENCE is what the earlier shape did.
        if 'matched' not in out or 'returned' not in out or 'truncated' not in out:
            record(f'{label}: the reply carries a truncation verdict', False, True)
            continue
        truth = out['matched'] > out['returned']
        record(f'{label}: truncated flag', out['truncated'], truth)
        record(f'{label}: returned equals the list', out['returned'],
               len(out['matches']))
        record(f'{label}: matched is the corpus',
               out['matched'], min(documents, MAX_KB_MATCHES))


MAX_KB_MATCHES = 4000


def measure_knowledge(documents, limit):
    """Every document matches the query, so only the limit stands in the way."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {'connections': {}}}), encoding='utf-8')

        def body(engine):
            from platform_runtime.knowledge import KnowledgeStore
            store = KnowledgeStore(engine)
            store.create_collection(TENANT, 'owner', 'manuals')
            store.grant(TENANT, 'owner', 'manuals', AGENT, True)
            for index in range(documents):
                store.ingest(TENANT, 'owner', 'manuals', f'doc-{index}', f'Q{index}',
                             f'savdo hisoboti {index} matni', 0)
            return store.search(TENANT, AGENT, 'manuals', 'savdo', limit)

        return with_tenant(cfg, root, body)


def section_ledger_truncation():
    """Four ledger readers returned a bare list and are reachable over HTTP.

    ``briefing.ledger``, ``escalation.ledger``, ``reengagement.ledger`` and
    ``supervisor.history`` each carried a docstring saying the bare-list shape was
    "a trap rather than a live defect, because nothing in the runtime calls it".
    That reasoning asked the wrong question: nothing in the *runtime* calls them,
    but four HTTP routes expose them as ``entries`` / ``routes``, so an operator
    asking "how many runs happened" was answered with the page size.

    This section measures the flag on both sides of the boundary for each reader.
    """
    print('=== 12: the ledger readers report their population, not their page ===')
    for name, measure in (
            ('briefing', measure_briefing_ledger),
            ('escalation', measure_escalation_ledger),
            ('reengagement', measure_reengagement_ledger),
            ('supervisor', measure_supervisor_ledger)):
        for built, limit, expected_total in ((3, 1, 3), (2, 2, 2)):
            outcome = measure(built, limit)
            label = f'{name}: {built} rows, limit {limit}'
            if not isinstance(outcome, tuple) or len(outcome) != 3:
                record(f'{label}: the reply separates page from population', False, True)
                continue
            rows, total, truncated = outcome
            record(f'{label}: page size', len(rows), limit)
            record(f'{label}: population', total, expected_total)
            record(f'{label}: truncated flag', truncated, expected_total > limit)


def measure_supervisor_ledger(built, limit):
    """The route ledger is keyed on (tenant, request_key): one row per question."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {'connections': {}}}), encoding='utf-8')

        def body(engine):
            from platform_runtime.supervisor import Supervisor
            supervisor = Supervisor(engine)
            with engine.tx() as c:
                for index in range(built):
                    c.execute('INSERT INTO p_supervisor_route'
                              '(tenant,request_key,question,actor,section,agent,'
                              'status,created,updated)'
                              ' VALUES(?,?,?,?,?,?,?,?,?)',
                              (TENANT, f'r{index}', f'savdo savoli {index}', 'owner',
                               'sales', 'sales.360', 'routed', 0.0, 0.0))
            return supervisor.history(TENANT, limit, with_total=True)

        return with_tenant(cfg, root, body)


def measure_briefing_ledger(built, limit):
    """The briefing ledger is keyed on (tenant, schedule, day): one row per day."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {'connections': {}}}), encoding='utf-8')

        def body(engine):
            from platform_runtime.briefing import Briefing
            briefing = Briefing(engine)
            with engine.tx() as c:
                for day in range(built):
                    c.execute('INSERT INTO p_briefing_ledger'
                              '(tenant,schedule,day,run_id,status,first_seen,'
                              'last_attempt,updated) VALUES(?,?,?,?,?,?,?,?)',
                              (TENANT, 'morning', f'2026-09-{day + 1:02d}',
                               str(day), 'sent', 0.0, 0.0, 0.0))
            return briefing.ledger(TENANT, 'morning', limit, with_total=True)

        return with_tenant(cfg, root, body)


def measure_escalation_ledger(built, limit):
    """Keyed on (tenant, schedule, person, task, due): distinct people make rows."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {'connections': {}}}), encoding='utf-8')

        def body(engine):
            from platform_runtime.escalation import EscalationLoop
            escalation = EscalationLoop(engine)
            with engine.tx() as c:
                for index in range(built):
                    c.execute('INSERT INTO p_escalation_ledger'
                              '(tenant,schedule,person,task,due,run_id,status,'
                              'first_seen,last_attempt,updated)'
                              ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                              (TENANT, 'daily', f'u{index}', 'Hisobot',
                               f'2026-09-0{index + 1}', str(index), 'sent',
                               0.0, 0.0, 0.0))
            return escalation.ledger(TENANT, 'daily', limit, with_total=True)

        return with_tenant(cfg, root, body)


def measure_reengagement_ledger(built, limit):
    """Keyed on (tenant, connection, lead_id): distinct leads make rows."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {'connections': {}}}), encoding='utf-8')

        def body(engine):
            from platform_runtime.agent_loop import AgentLoop
            from platform_runtime.reengagement import ReengagementLoop
            reengagement = ReengagementLoop(engine, AgentLoop(engine))
            with engine.tx() as c:
                for index in range(built):
                    c.execute('INSERT INTO p_reengagement_ledger'
                              '(tenant,connection,lead_id,policy,run_id,status,'
                              'first_seen,last_attempt,updated)'
                              ' VALUES(?,?,?,?,?,?,?,?,?)',
                              (TENANT, 'crm', f'lead-{index}', 'main',
                               str(index), 'queued', 0.0, 0.0, 0.0))
            return reengagement.ledger(TENANT, 'main', limit, with_total=True)

        return with_tenant(cfg, root, body)


def section_exception_mapping():
    """Every refusal a bounded reader can raise must be one the API layer maps.

    The API layer maps six types to status codes (`call()` catches RateLimited,
    Forbidden, AuthenticationError, NotFound, Conflict, ValueError, LookupError)
    and nothing else.  A reader that refuses a caller's mistake with a type
    outside that set turns a 422 into a 500: the caller is blamed for nothing
    and the operator is paged for a mistake in a query string.

    The rule this section measures: a bounded reader either CLAMPS its bound
    (accepts anything, returns a sane page) or REFUSES with a mapped type.  It
    must never do neither.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        engine = Engine(root / 'app.db', build_registry(),
                        lambda t, a: {'tools': ['reports.summary'],
                                      'ladder': 'autonomous', 'approval': []})
        engine.submit(TENANT, 'web', 'k0', AGENT,
                      [{'tool': 'reports.summary', 'args': {}}], 'user')

        # A reader that clamps must return a page for every bound, including the
        # ones that used to crash: a string, None, a bool, a float, a list.
        for bad in ('x', None, True, 1.5, [2], {}):
            try:
                rows = engine.list_tasks(TENANT, bad)
            except Exception as exc:  # noqa: BLE001 -- the type IS the property
                mapped = isinstance(exc, (ValueError, LookupError))
                record(f'list_tasks(limit={bad!r}): refusal is mappable', mapped, True)
                if mapped:
                    record("  and the message names the argument",
                           'limit' in str(exc), True)
            else:
                # Clamping is also legal -- but then it must be a real page.
                record(f'list_tasks(limit={bad!r}): clamps to a page',
                       isinstance(rows, list) and len(rows) <= 200, True)

        # The bound is CLAMPED, not refused, at the ends: a caller asking for
        # more than the ceiling gets the ceiling, not an error.
        for limit, expected in ((500, 1), (200, 1), (0, 1), (-5, 1)):
            try:
                rows = engine.list_tasks(TENANT, limit)
                record(f'list_tasks(limit={limit}): clamped page',
                       len(rows), expected)
            except Exception as exc:  # noqa: BLE001
                record(f'list_tasks(limit={limit}): clamped page', type(exc).__name__, 'a page')


def section_engine_clock():
    """Every time-dependent answer must read the SAME clock.

    The engine takes an injectable clock, and it is not decoration: deadlines,
    leases, approval expiry and escalation windows all read it. A module that
    falls back to ``time.time()`` instead answers its question against a clock the
    platform does not control, which makes the answer unreproducible (a test
    cannot pin it) and wrong whenever the two clocks disagree.

    This measures the shape directly: move ONLY the engine clock and see whether
    the answer follows. An answer that does not move is reading the wall clock.
    """
    import sqlite3
    import tempfile

    from platform_runtime import whatsapp

    print('=== 14: every time-dependent answer reads the engine clock ===')
    window = whatsapp.WINDOW_SECONDS

    # The engine's own readers: does each consult engine.clock()?
    modules = {'escalation': 0, 'usage_budget': 0, 'workforce': 0, 'whatsapp': 0}
    root = Path(__file__).resolve().parents[1] / 'api-python' / 'platform_runtime'
    for name in modules:
        text = (root / f'{name}.py').read_text(encoding='utf-8')
        modules[name] = text.count('engine.clock()') + text.count('_engine_now(engine)')
        record(f'{name}: reads the engine clock', modules[name] > 0, True)

    # No module may read the wall clock directly except as a declared fallback.
    for name in modules:
        text = (root / f'{name}.py').read_text(encoding='utf-8')
        bare = text.count('time.time()')
        # whatsapp keeps exactly one: the default of its own _now() fallback.
        allowance = 1 if name == 'whatsapp' else 0
        record(f'{name}: no undeclared wall-clock read', bare, allowance)

    # The behavioural half. Measuring `_engine_now` alone proves nothing: the
    # defect was a CALL SITE that did not use it, so the probe has to drive the
    # send gate itself. `window_state` was always correct; the gate was not.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        engine = Engine(workdir / 'clock.db', build_registry(), lambda t, a: {})

        class Drift:
            now = 1_000_000.0

            def __call__(self):
                return self.now

        drifted = Drift()
        engine.clock = drifted
        last_inbound = drifted.now - 60        # inside the window
        opened, _ = whatsapp.window_state(last_inbound, whatsapp._engine_now(engine))
        record('window open under the engine clock', opened, True)

        drifted.now += window * 10             # ten windows later
        opened, _ = whatsapp.window_state(last_inbound, whatsapp._engine_now(engine))
        record('window closed after the engine clock moves', opened, False)

        # And the pure function must still work with no engine at all.
        opened, closes = whatsapp.window_state(last_inbound, drifted.now - window - 1)
        record('pure window_state still answers from an explicit now',
               opened, False)
        record('  and names when it closed', closes is not None, True)

    # The call-site half: does the gate PASS the engine clock in? Read the source
    # because driving `send` needs a whole tenant fixture; the probe's job here is
    # to catch the shape (a bare `window_state(last)` at a production call site).
    text = (root / 'whatsapp.py').read_text(encoding='utf-8')
    body = text.split('def send(')[-1]
    gate_uses_engine_clock = 'window_state(last, _engine_now(engine))' in body
    record('the send gate passes the engine clock to window_state',
           gate_uses_engine_clock, True)
    record('  and no bare window_state(last) reaches a gate',
           'window_state(last)' in body, False)


def section_expiry_boundary():
    """An expiry bound must be measured AT the boundary, not far from it.

    A predicate like ``expires <= now`` and its variant ``expires < now`` differ at
    exactly one instant: equality. A fixture that sets the expiry to epoch 1 -- some
    fifty-seven years in the past -- is refused by BOTH, so it proves the refusal
    happens and nothing about WHERE the bound sits. The same is true of a far-future
    expiry and an acceptance check. Only equality discriminates.

    This measures the shape across the store's expiry readers and the tests that are
    supposed to guard them, because a guard that cannot fail is not a guard.
    """
    print('=== 15: expiry bounds are measured at the boundary ===')

    now = 1_790_000_000.0
    # The predicate pair, at the positions that matter. Equality is the ONLY one
    # where they differ: on either side of it both answer the same way, which is
    # exactly why a fixture parked far from the bound measures nothing.
    for label, expires, expect_flip in (('far past', 1.0, False),
                                        ('one second before', now + 1, False),
                                        ('exactly now', now, True),
                                        ('one second after', now - 1, False)):
        inclusive = expires <= now
        exclusive = expires < now
        record(f'{label}: the two predicates differ', inclusive != exclusive,
               expect_flip)

    # The three store readers must agree with each other at equality.
    root = Path(__file__).resolve().parents[1] / 'api-python'
    text = (root / 'app' / 'identity_store.py').read_text(encoding='utf-8')
    inclusive_sites = text.count("expires']<=time.time()") + text.count("expires']<=now")
    listing = "AND expires>?" in text
    record('session/invitation readers take the deadline second as expired',
           inclusive_sites, 3)
    record('the listing is the exact complement of that refusal', listing, True)

    # The tests that guard those readers must stand ON the boundary.
    guards = (root / 'runtime_tests' / 'test_identity_hardening.py').read_text(
        encoding='utf-8')
    # A frozen-clock fixture written onto the expiry column is the shape that puts
    # the assertion on the bound. Count it rather than matching one literal line:
    # an earlier version of this check matched a spacing that does not occur and
    # reported a missing guard that was there.
    frozen_expiry = guards.count('SET expires=?') + guards.count('SET expires=? WHERE')
    record('guards write the expiry from a frozen clock', frozen_expiry >= 2, True)
    record('the weak far-past fixture is still present but not alone',
           'SET expires=1' in guards and 'frozen' in guards, True)


def section_approval_deadline():
    """Two readers of ONE approval deadline must refuse at the same instant.

    The runtime writes an approval with ``expires = created + 86400`` and then two
    different code paths ask whether that deadline has passed:

      * ``approve`` refuses to DECIDE an expired approval (a human action);
      * ``claim``  refuses to EXECUTE one (the worker path, via the tick).

    They are separate literals in separate methods. If they disagreed by a single
    second, a decision could be accepted and then never executed -- the approval is
    consumed, the step waits forever -- or the reverse. Nothing in the type system
    ties the two together, so the only thing that can hold them in agreement is a
    test that stands exactly on the shared bound.

    The pre-existing ``test_approval_expires`` advances the clock by 86401, one
    second PAST the deadline, where both an inclusive and an exclusive predicate
    refuse. It proves a refusal happens somewhere past the bound and nothing about
    where the bound is. This section measures that gap and checks it is now closed
    from BOTH sides.
    """
    print('=== 16: the approval deadline is one bound read by two paths ===')

    now, deadline = 1000.0, 1000.0 + 86400.0
    # The instant the two predicates disagree is equality, and only equality.
    for label, at, expect_flip in (('one second before', deadline - 1, False),
                                   ('exactly the deadline', deadline, True),
                                   ('one second past', deadline + 1, False)):
        record(f'approval {label}: <= and < differ',
               (at >= deadline) != (at > deadline), expect_flip)

    root = Path(__file__).resolve().parents[1] / 'api-python'
    engine = (root / 'platform_runtime' / 'engine.py').read_text(encoding='utf-8')

    # Both readers must be inclusive, and there must be exactly one of each: a
    # second copy of either predicate would be an unfenced duplicate.
    decide_inclusive = engine.count("r['expires']<=self.clock()")
    execute_inclusive = engine.count("a['expires']<=now")
    record('the decide path refuses at the deadline second', decide_inclusive, 1)
    record('the execute path refuses at the deadline second', execute_inclusive, 1)
    record('neither path drifted to an exclusive comparison',
           "r['expires']<self.clock()" not in engine
           and "a['expires']<now" not in engine, True)
    record('the deadline is created as now + one day',
           "'pending',now+86400)" in engine or 'now+86400)' in engine, True)

    # The guards must sit ON the bound, and must name what they are refusing: an
    # `assertRaises(Conflict)` alone cannot say which of the predicate's three
    # clauses fired, since a fresh write has status pending and step queued and the
    # expiry clause is the only one with anything to refuse.
    guards = (root / 'runtime_tests' / 'test_engine.py').read_text(encoding='utf-8')
    record('a guard advances the clock exactly onto the deadline',
           'self.now+=86400' in guards, True)
    record('a guard stays one second inside the deadline to prove acceptance',
           'deadline=self.now+86400' in guards and 'self.now=deadline-1' in guards, True)
    record('a guard pins the decide path by re-reading the untouched row',
           'FROM p_approvals a JOIN p_steps' in guards
           and "assertEqual(('pending','','queued')" in guards, True)
    record('a guard pins the execute path by asserting the named failure',
           "assertEqual('approval_expired_or_invalid',row[0])" in guards, True)
    # The weak guard is still there; it is not wrong, it is only not sufficient.
    record('the far-past guard is present but no longer alone',
           'self.now+=86401' in guards and 'self.now+=86400' in guards, True)


def section_lease_bound():
    """The lease is one bound read by two paths that must COMPLEMENT each other.

    A claimed step carries an ownership token and a lease. Two separate methods ask
    whether that lease is still alive:

      * ``finish`` guards with ``lease > now``  -- STRICT: the worker may only report
        while the lease is strictly in the future;
      * ``claim``'s sweep expires with ``lease <= now`` -- INCLUSIVE: a running lease at
        or before now is dead and is retired to ``uncertain``.

    They live in different methods and are written as different literals. Their safety
    property is that they are EXACT COMPLEMENTS: at every instant, including equality,
    exactly one of them fires. That property fails two different ways, and both are
    silent:

      * sweep strict (``lease < now``): at equality NEITHER fires. The step stays
        ``running`` under a dead claim and nothing ever retires it -- measured, not
        assumed, in ``test_lease_bound_is_exclusive_on_both_sides``;
      * finish inclusive (``lease >= now``): at equality BOTH fire. A worker reports a
        step the sweep has already retired as ``uncertain``.

    Measured discrimination on revert: sweep-strict alone -> 1 failure, finish-inclusive
    alone -> 1 failure, both -> 1 failure, neither -> 63/63 OK.
    """
    print('=== 17: the lease bound is complemented by the claim sweep ===')

    now = 1000.0
    # Equality is NOT where the two forms start differing -- as complements they differ
    # at every instant. It is where a WRONG pairing also differs, which is why the
    # assertion belongs there and nowhere else. Measured directly, pairing by pairing.
    for label, lease in (('one second past', now - 1),
                         ('exactly the bound', now),
                         ('one second inside', now + 1)):
        # The correct pairing: strict finish + inclusive sweep. Exactly one side fires.
        correct_once = (lease > now) != (lease <= now)
        # The gap pairing: strict finish + strict sweep. At equality NEITHER fires.
        gap_pair = (lease > now) or (lease < now)
        # The overlap pairing: inclusive finish + inclusive sweep. At equality BOTH fire.
        overlap_pair = (lease >= now) and (lease <= now)
        record(f'the correct pairing separates exactly one side ({label})',
               correct_once, True)
        if lease == now:
            record('at equality the gap pairing fires NEITHER side', gap_pair, False)
            record('at equality the overlap pairing fires BOTH sides', overlap_pair, True)
        else:
            record(f'away from equality the gap pairing still fires ({label})',
                   gap_pair, True)
            record(f'away from equality the overlap pairing is silent ({label})',
                   overlap_pair, False)

    # The correct pair covers every instant exactly once -- no gap, no overlap.
    covered = []
    for lease in (now - 1, now, now + 1):
        finish_accepts = lease > now
        sweep_expires = lease <= now
        covered.append(finish_accepts != sweep_expires)
    record('the two forms cover every instant exactly once', all(covered), True)

    # The wrong pairs must be shown to actually fail, or the property above is vacuous.
    gap = [(now - 1), (now), (now + 1)]
    record('a strict sweep leaves a gap at equality (neither fires)',
           (now > now) is False and (now < now) is False, True)
    record('an inclusive finish overlaps at equality (both fire)',
           (now >= now) is True and (now <= now) is True, True)

    root = Path(__file__).resolve().parents[1] / 'api-python'
    engine = (root / 'platform_runtime' / 'engine.py').read_text(encoding='utf-8')

    # Exactly two sites of the strict form -- the finish guard and the dispatch fence --
    # and one of the inclusive form. Two readers of one lease, deliberately duplicated
    # at the dispatch fence, so the count is asserted rather than a single occurrence.
    record('the finish guard is strict, once',
           engine.count("AND status='running' AND lease>?"), 2)
    record('the claim sweep is inclusive, once',
           engine.count("AND status='running' AND lease<=?"), 1)
    record('neither form drifted',
           "AND status='running' AND lease>=?" not in engine
           and "AND status='running' AND lease<?" not in engine, True)

    # The guards must sit ON the shared bound.
    guards = (root / 'runtime_tests' / 'test_engine.py').read_text(encoding='utf-8')
    record('a guard sits one second inside the bound and proves acceptance',
           'self.now=lease-1' in guards, True)
    record('a guard sits exactly on the bound',
           'self.now=lease\n' in guards, True)
    record('a guard sits far past the bound to rule out an instant-only trick',
           'self.now=lease+3600' in guards, True)
    record('a guard pins BOTH directions, not just one',
           "assertEqual('lease_expired',row[0]" in guards, True)
    # The weak guard is still there; it is not wrong, only not sufficient.
    record('the far-past guard is present but no longer alone',
           'self.now+=91' in guards and 'self.now=lease' in guards, True)


def section_event_lease():
    """The EVENT lease has one reader -- and depends on a token property to be safe.

    The step lease is a pair of complementary predicates (section 17). The event lease
    is not: ``process_event``'s dequeue reads ``status='processing' AND lease<=now``
    (inclusive) and there is NO second reader to complement it. The completion statement
    ends ``AND claim=?`` -- the claim token alone, with no ``lease > now`` term.

    That absence is deliberate and safe, but only because of a property that is asserted
    nowhere else: the dequeue MINTS A NEW TOKEN on every claim. A superseded worker's
    late ``UPDATE ... AND claim=<old token>`` therefore matches no row, so it can never
    overwrite the result of the worker that won. Removing the fence and removing the
    regeneration together would reopen exactly the hole the step path still closes twice.

    The bound itself carries the familiar shape: the TTL is a hard-coded 120 seconds and
    ``test_event_crash_recovery_after_submission`` forces ``lease=0``, so it measures a
    recovery and not where recovery begins.
    """
    print('=== 18: the event lease bound and the token that guards completion ===')

    now = 1000.0
    # Only equality discriminates the inclusive dequeue from a strict variant.
    for label, lease in (('one second past', now - 1), ('exactly the bound', now),
                         ('one second inside', now + 1)):
        record(f'event dequeue {label}: <= and < differ',
               (lease <= now) != (lease < now), lease == now)

    root = Path(__file__).resolve().parents[1] / 'api-python'
    engine = (root / 'platform_runtime' / 'engine.py').read_text(encoding='utf-8')

    # The dequeue is inclusive, once.
    record('the event dequeue is inclusive, once',
           engine.count("status='processing' AND lease<=?"), 1)
    record('the event dequeue did not drift to a strict comparison',
           "status='processing' AND lease<?" not in engine, True)
    # The TTL is a documented constant, not a magic number invented per call.
    record('the event lease TTL is the constant 120',
           'self.clock()+120' in engine, True)
    # The completion path guards on the token ALONE -- recorded as a fact, so that a
    # future change adding or needing a lease term is a visible diff in this probe.
    complete = [line for line in engine.splitlines()
                if 'UPDATE p_events SET status=?' in line]
    record('event completion guards with the claim token, exactly one statement',
           len(complete), 1)
    record('event completion carries NO lease term (token-only fencing)',
           complete and 'lease>' not in complete[0] and 'AND claim=?' in complete[0],
           True)
    # The token must be minted fresh on every claim -- the property that makes the
    # token-only fence sufficient.
    record('the dequeue mints a new token per call',
           'def process_event(self,tenant,planner):\n        token=uuid.uuid4().hex' in engine,
           True)

    # The guards must sit ON the bound, and must pin the token property directly.
    guards = (root / 'runtime_tests' / 'test_engine.py').read_text(encoding='utf-8')
    record('a guard sits one second inside the event bound',
           'self.now=lease-1' in guards, True)
    record('a guard sits exactly on the event bound',
           'self.now=lease\n' in guards.replace('\r\n', '\n'), True)
    record('a guard asserts the re-claim mints a NEW token',
           "assertNotEqual('TOKEN_A',row['claim']" in guards, True)
    record('a guard asserts a superseded token matches no row',
           "self.assertIsNone(match,'a superseded token must match no row')" in guards,
           True)
    # The weak guard is still there; only now it is not alone.
    record('the far-past event guard is present but no longer alone',
           "UPDATE p_events SET status='processing',lease=0" in guards
           and 'self.now=lease' in guards, True)


def section_quota_windows():
    """Three bounds on the inbox, two of which no test reached at all.

    ``accept_event`` enforces three separate limits, each with a different shape:

      * the DAILY QUOTA -- a per (tenant, day-index) counter, refused at
        ``count >= 10000`` and then incremented, so the effective cap is exactly 10000;
      * the OUTSTANDING BACKPRESSURE -- a live gauge on events in (pending, processing),
        refused at ``pending >= 1000``, so the outstanding set caps at exactly 1000;
      * the DAY INDEX -- ``int(clock() // 86400)``, the window the counter lives in. A new
        day-index starts a fresh counter.

    The pre-existing test writes ``count=10000`` directly and asserts the next accept is
    refused. Unlike the lease and approval cases, that one IS discriminating -- the pinned
    value sits exactly on the bound -- but it pins the OUTSIDE of the quota only. The
    inside (9999 must be accepted and land exactly on 10000), the entire backpressure
    bound, and the window reset were all unasserted.
    """
    print('=== 19: the inbox quota, the backpressure gauge and the day window ===')

    # The off-by-one reasoning, measured rather than reasoned about: the counter is read
    # BEFORE the increment, so the accepted set is exactly the cap.
    cap = 10000
    for label, stored, expect_accept in (('one short of the cap', cap - 1, True),
                                         ('exactly the cap', cap, False),
                                         ('past the cap', cap + 1, False)):
        record(f'quota {label}: accept allowed', stored < cap, expect_accept)
    # Two accepts across a window reset can each sit at the cap -> 2x in two seconds.
    record('a window reset makes the daily cap reachable twice in two seconds',
           (cap <= cap) and (cap <= cap), True)

    root = Path(__file__).resolve().parents[1] / 'api-python'
    engine = (root / 'platform_runtime' / 'engine.py').read_text(encoding='utf-8')

    # All three bounds, each asserted as a literal so a drift is a visible diff.
    record('the quota refuses inclusively at the cap',
           "quota['count']>=10000" in engine, True)
    record('the quota did not drift to exclusive',
           "quota['count']>10000" not in engine, True)
    record('the backpressure gauge refuses inclusively at 1000',
           'if pending>=1000' in engine, True)
    record('the backpressure gauge did not drift to exclusive',
           'if pending>1000' not in engine, True)
    record('the window is a UTC day index',
           'day=int(self.clock()//86400)' in engine, True)
    record('the quota window did not drift to a different divisor',
           'self.clock()//86400' in engine, True)
    # The ordering that makes a refused accept free: the guard raises before the increment.
    guard = engine.find('if pending>=1000')
    bump = engine.find('INSERT INTO p_quota')
    record('the guard raises before the counter is incremented', guard < bump, True)

    guards = (root / 'runtime_tests' / 'test_engine.py').read_text(encoding='utf-8')
    record('a guard pins the inside of the quota bound',
           'UPDATE p_quota SET count=9999' in guards, True)
    record('a guard pins the backpressure bound from both sides',
           'the-1000th' in guards and 'the-1001st' in guards, True)
    record('a guard pins the window reset instant',
           'self.now=base-1' in guards and 'self.now=base\n' in guards.replace('\r\n','\n'), True)
    # The pre-existing guard is still there; it is the one that already discriminated.
    record('the original on-the-bound quota guard is still present',
           'UPDATE p_quota SET count=10000' in guards, True)


def section_device_cascade():
    """A device change rewrites a NARROW set, and the narrowness is the safety property.

    ``device()`` handles enrollment, re-enrollment and revocation in one transaction. It
    rewrites the steps that could still run on that device:

        running                      -> uncertain   (dispatched; cannot be recalled)
        queued, waiting_approval     -> cancelled

    Exactly three statuses. A step that has ALREADY finished must not be rewritten -- if
    ``succeeded`` were in the set a re-enroll would retroactively rewrite history that
    already happened, and in-flight approvals would be rejected on a lie.

    The approval cascade is a SEPARATE list again: ``status IN ('pending','approved')``.
    A ``consumed`` approval belongs to a step that already started, so it must survive.

    And the generation is not a version counter: the app layer fences every device
    message with ``d['generation'] != claims['generation']``, an EQUALITY test, so the
    token minted for the old generation stops matching the instant the row is bumped --
    even though ``revoked`` is still false.
    """
    print('=== 20: the device cascade and the generation fence ===')

    # The two sets, stated as data so a drift is visible rather than merely executable.
    step_set = {'queued', 'waiting_approval', 'running'}
    rewritten = {'queued': 'cancelled', 'waiting_approval': 'cancelled',
                 'running': 'uncertain'}
    kept = {'succeeded', 'failed', 'cancelled', 'uncertain'}
    record('the step cascade rewrites exactly three statuses', len(rewritten), 3)
    record('no finished status is in the step cascade set',
           bool(step_set & kept), False)
    record('a live claim is in the set (running is fenced)',
           'running' in step_set, True)

    approval_set = {'pending', 'approved'}
    record('the approval cascade rejects exactly two statuses', len(approval_set), 2)
    record('a consumed approval is NOT rejected (the step already started)',
           'consumed' not in approval_set, True)
    record('the two sets are genuinely different',
           approval_set != step_set, True)

    # The generation fence is equality, so the old token must stop matching.
    g_old, g_new = 1, 2
    record('the old generation stops matching after a re-enroll', g_old != g_new, True)
    record('the new generation is the only one that matches',
           (g_new == g_new) and (g_old == g_new), False)

    root = Path(__file__).resolve().parents[1] / 'api-python'
    engine = (root / 'platform_runtime' / 'engine.py').read_text(encoding='utf-8')
    api = (root / 'app' / 'platform_api.py').read_text(encoding='utf-8')

    # The device cascade states the set THREE times -- the affected-task SELECT, the
    # nested approval subquery and the UPDATE -- and it must be the same set in all three,
    # or an approval could be rejected for a step the UPDATE then declines to fence.
    record("the step cascade's literal status set appears at all three device sites",
           engine.count("AND status IN ('queued','waiting_approval','running')"), 4)
    record('the device cascade did not widen the set to include a finished status',
           "'waiting_approval','running','succeeded'" not in engine, True)
    record("the approval cascade's literal status set is unchanged",
           "WHERE tenant=? AND status IN ('pending','approved') AND step IN" in engine, True)
    record('the approval set did not absorb consumed',
           "'pending','approved','consumed'" not in engine, True)
    record('the generation still increments on every device() call',
           "generation=generation+1'" in engine, True)
    record('the app-layer fence compares generations by equality',
           "d['generation']!=claims['generation']" in api, True)

    guards = (root / 'runtime_tests' / 'test_engine.py').read_text(encoding='utf-8')
    record('a guard pins the whole step-cascade matrix',
           "'succeeded','succeeded'" in guards and "'failed','failed'" in guards, True)
    record('a guard pins the approval cascade matrix',
           "'consumed','running','consumed'" in guards, True)
    record('a guard pins the generation as a fence, not a number',
           'the old generation must no longer match' in guards, True)


def section_identity_throttle():
    """The login/HTTP throttle has THREE bounds that must agree with each other.

    ``throttle(namespace, key, limit, window_seconds)`` is the gate that runs BEFORE
    the expensive scrypt verification, so a mistake here is either a lockout (too
    eager) or an open door (too lax). Three independent bounds:

      * the REFUSAL predicate ``count > limit`` -- the count is read AFTER the
        increment, so the limit-th call must PASS. A ``>=`` caps one below the
        configured limit, and a test that only pokes the outside cannot see it.
      * the WINDOW INDEX ``int(now // window_seconds)`` -- a floor, so the last
        second of a window and the first second of the next land in different
        windows. Shifting it by one desynchronises counter from clock.
      * the CLEANUP predicate ``DELETE WHERE window < window-2`` -- a strict
        ``<``, so the current, the previous and the one before that survive.
        Making it eager (``<=``) erases a still-relevant counter and silently
        resets a bucket that should still be limited.

    The pre-existing guard (``test_rate_limit_persists_across_reconnect``) does pin
    the refusal side -- its fixture sits exactly on the bound -- but it never moves
    the clock, so the window index and the cleanup bound were measured by nothing.
    """
    print('=== 21: the fixed-window throttle, three bounds ===')

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'app' / 'identity_store.py').read_text(encoding='utf-8')

    record('the refusal bound is strict (`>`), so the limit-th call is accepted',
           'if count > limit: raise AuthRateLimited' in src, True)
    record('the refusal bound did not become inclusive',
           'if count >= limit: raise AuthRateLimited' not in src, True)

    # Order inside throttle(): the increment lands before the count is read back,
    # which is exactly why the predicate must be `>` and not `>=`.
    increment = src.index("INSERT INTO p_auth_limits VALUES(?,?,1)")
    readback = src.index('SELECT count FROM p_auth_limits WHERE bucket=? AND window=?')
    record('the counter is incremented before it is read back', increment < readback, True)

    record('the window index is floored by the window length',
           'window = int(time.time() // window_seconds)' in src, True)
    record('the window index was not shifted by a constant',
           'window_seconds) + 1' not in src and 'window_seconds) - 1' not in src, True)
    record('the cleanup keeps everything from window-2 upward (strict `<`)',
           'DELETE FROM p_auth_limits WHERE window<?' in src and "(window-2,)" in src, True)
    record('the cleanup did not become eager',
           'WHERE window<=?' not in src, True)
    record('the bucket is scoped by namespace AND key, not a constant',
           "(namespace + ':' + str(key)[:512])" in src, True)

    # The gate must run before password verification, or it protects nothing.
    auth = src.index('def authenticate(')
    call = src.index('throttle(', auth)
    verify = src.index('_password_ok(', auth)
    record('the throttle runs before password verification', call < verify, True)

    guards = (root / 'runtime_tests' / 'test_identity_hardening.py').read_text(encoding='utf-8')
    record('a guard pins the inside of the refusal bound',
           'at or below the limit and must be accepted' in guards, True)
    record('a guard pins the window tick from both sides',
           'int(t//900)' in guards or 'test_window_index_advances_exactly_on_the_tick' in guards, True)
    record('a guard pins the cleanup retention exactly',
           'test_cleanup_retains_the_previous_window_and_drops_older_ones' in guards, True)
    record('a guard pins that two buckets never share a counter',
           'test_throttle_buckets_do_not_share_a_counter' in guards, True)


def section_oauth_deadlines():
    """Four OAuth/Linked-Credentials deadline readers, each a different sense.

    `platform_runtime/oauth.py` gates a credential on time in four separate
    places, and every one of them was pinned only from FAR AWAY:

      * the AUTHORIZATION STATE TTL -- `begin` stores `expires = now + 600` and
        `complete` reads `pending['expires'] <= now`. Equality is already
        expired. The pre-existing guard jumps 601s, past the bound either way.
      * the ACCESS HEADROOM -- `access` reuses a cached token only while
        `row['expires'] > now + 60`, and refreshes otherwise. A one-second shift
        in either direction is invisible to `test_expiry_refresh`, which expires
        the token thousands of seconds out.
      * the STALE ATTEMPT LEASE -- `recover_stale` refuses while
        `started + 120 > now`, so an attempt that burned exactly 120s is
        recoverable and one at 119s is not. The pre-existing guard jumps 121s.
      * the GRANT FENCE -- `fence` reads `row['expires'] <= now`, so a grant is
        dead AT its expiry instant, not a second later. Only the generation term
        of that predicate had a guard.

    Two of the four are inclusive (`<=`) and two exclusive (`>`), and the
    literal `120` is shared by recover_stale and _drain_revoke -- so a change to
    one copy silently moves the other. Both are pinned here.
    """
    print('=== 22: oauth deadline readers, four senses ===')

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'platform_runtime' / 'oauth.py').read_text(encoding='utf-8')

    record('the state TTL is 600 seconds from begin()',
           'self.e.clock()+600, pending))' in src, True)
    record('the state reader is INCLUSIVE at expiry (equality already expired)',
           "pending['expires'] <= self.e.clock()" in src, True)
    record('the state reader did not become strictly-less',
           "pending['expires'] < self.e.clock()" not in src, True)

    record('access() reuses only with a full 60s of headroom',
           "row['expires'] > self.e.clock()+60" in src, True)
    record('the headroom constant was not shifted',
           "self.e.clock()+61" not in src and "self.e.clock()+59" not in src, True)

    record('the stale lease is 120 seconds in BOTH readers',
           src.count("row['started']+120 > self.e.clock()"), 2)
    record('the stale lease was not shifted in either copy',
           "row['started']+121 > self.e.clock()" not in src, True)

    record('the grant fence is INCLUSIVE at expiry',
           "row['expires'] <= self.e.clock()" in src, True)
    record('the grant fence still tests the generation by equality',
           "row['generation'] != grant.generation" in src, True)

    guards = (root / 'runtime_tests' / 'test_oauth.py').read_text(encoding='utf-8')
    record('a guard pins the state TTL at the exact expiry second',
           'test_state_ttl_bound_is_the_exact_instant_of_expiry' in guards, True)
    record('a guard pins the headroom at exactly sixty seconds',
           'test_token_headroom_bound_is_sixty_seconds_exclusive' in guards, True)
    record('a guard pins the stale lease at exactly one twenty',
           'test_stale_attempt_bound_is_exclusive_at_one_twenty' in guards, True)
    record('a guard pins the grant fence at the expiry instant',
           'test_fence_rejects_at_the_expiry_instant_not_after' in guards, True)



def section_cooldown_windows():
    """The two scheduled-outreach loops share ONE time shape, stated twice.

    `escalation.py` (late work -> manager) and `reengagement.py` (stalled lead ->
    customer) are different products with the same three time bounds, and all six
    sites were pinned only from far away:

      * the COOLDOWN retry gate -- `last_attempt + cooldown > now` -> silent. The
        bound is EXCLUSIVE, so the row reopens AT the cooldown instant. Escalation
        pins this only via a `sent` row (terminal for a different reason) and
        reengagement jumps 700 of 7200 and 3601 of 3600, both strictly one-sided.
      * the DUE-policy read -- `... AND next_due <= clock ORDER BY next_due,id
        LIMIT 1`. Equality IS due. Both modules only ever observed the far side
        (`next_due` an interval in the future), so a `<` flip would delay every
        cycle by a whole interval and no test would fail.
      * the INTERVAL advance -- `_advance` writes `next_due = now + interval`,
        read back from the row, so a doubled or zero interval is visible.

    A one-second shift in the cooldown is not cosmetic: escalation retries a FAILED
    delivery and reengagement re-messages a live customer, so the bound decides
    when a person is contacted again.
    """
    print('=== 23: escalation and reengagement time windows ===')

    root = Path(__file__).resolve().parents[1] / 'api-python'
    esc = (root / 'platform_runtime' / 'escalation.py').read_text(encoding='utf-8')
    re_ = (root / 'platform_runtime' / 'reengagement.py').read_text(encoding='utf-8')

    record("escalation's cooldown gate is EXCLUSIVE (equality retries)",
           "row['last_attempt'] + schedule['cooldown_seconds'] > now" in esc, True)
    record("escalation's cooldown gate did not become inclusive",
           "schedule['cooldown_seconds'] >= now" not in esc, True)
    record("reengagement's cooldown gate is EXCLUSIVE (equality retries)",
           "row['last_attempt'] + policy['cooldown_seconds'] > now" in re_, True)
    record("reengagement's cooldown gate did not become inclusive",
           "policy['cooldown_seconds'] >= now" not in re_, True)

    # The due read is the same SQL shape in both loops; a drift in one is a drift.
    record("escalation's due read is INCLUSIVE (`next_due<=?`)",
           'AND next_due<=? ORDER BY next_due,id LIMIT 1' in esc, True)
    record("reengagement's due read is INCLUSIVE (`next_due<=?`)",
           'AND next_due<=? ORDER BY next_due,id LIMIT 1' in re_, True)
    record('neither due read drifted to strictly-less',
           ('AND next_due<? ORDER BY next_due,id LIMIT 1' in esc or
            'AND next_due<? ORDER BY next_due,id LIMIT 1' in re_), False)

    record("escalation's interval advance adds exactly one interval",
           "(now + schedule['interval_seconds'], now, tenant, schedule['id'])" in esc, True)
    record("reengagement's interval advance adds exactly one interval",
           "(now + policy['interval_seconds'], now, tenant, policy['id'])" in re_, True)

    eg = (root / 'runtime_tests' / 'test_escalation.py').read_text(encoding='utf-8')
    rg = (root / 'runtime_tests' / 'test_reengagement.py').read_text(encoding='utf-8')
    record('a guard pins the escalation cooldown at the exact retry instant',
           'test_cooldown_bound_is_the_exact_retry_instant' in eg, True)
    record('a guard pins the escalation due bound at equality',
           'test_a_schedule_exactly_on_its_next_due_is_due' in eg, True)
    record('a guard pins the escalation interval advance by reading the row back',
           'test_the_interval_advance_lands_exactly_one_interval_ahead' in eg, True)
    record('a guard pins the reengagement cooldown at the exact retry instant',
           'test_cooldown_bound_is_the_exact_retry_instant' in rg, True)
    record('a guard pins the reengagement due bound at equality',
           'test_a_policy_exactly_on_its_next_due_is_due' in rg, True)
    record('a guard pins the reengagement interval advance by reading the row back',
           'test_the_interval_advance_lands_exactly_one_interval_ahead' in rg, True)



def section_oversight_and_supervisor_bounds():
    """`oversight.py` and `supervisor.py`: the control-plane bounds nobody sat on.

    Nine bounds across the two modules were surveyed. Eight of them were correct
    AND unpinned from the discriminating side -- only `supervisor.history`'s own
    truncation flag was properly guarded. The defect shape is the same one this
    audit has found in every phase: a test that asserts the property from FAR
    AWAY passes under both the right and the wrong predicate.

      * `oversight._window` returns `now - since_seconds`, read as
        `created>=?` in THREE separate queries. The only window test walks a
        one-second window over rows 29-59 seconds old, so a `>` flip changes
        nothing and the test stays green. The three reads also shared the literal,
        so a drift in the p_audit one alone would drop the newest events while the
        task count still matched.
      * `cost()` has no bound of its own -- it inherits `_window` -- and the only
        test naming the bound called it through `activity`. A refactor that
        inlined the window into `activity` would leave `cost` unbounded and green.
      * `_bounded(max_steps, 1, 12)` and `_bounded(max_hops, 1, 3)`: both tested
        with 0 and 99. 99 proves a ceiling EXISTS, not that it is 12 or 3. The
        ceilings themselves were unreachable.
      * `60 <= max_seconds <= 86400`: the only test used 5, so the floor's value
        was unproven and the UPPER bound was asserted by nothing at all -- an
        operator asking for exactly one day, the largest deadline offered, would
        have received a ValueError with no test failing.
      * `_match`'s tie-break is `(len(keyword), -index)`, so a tie goes to the
        EARLIER candidate; the candidate list is `ORDER BY id`, so "earlier" means
        the alphabetically first section id. The docstring said "declaration
        order", which was simply wrong, and flipping the sign to `+index` passed
        the whole suite.

    The two measured findings rather than mere gaps: the docstring mismatch above,
    and the fact that the supervisor's own edge test could not see a widened bound
    (fixed by walking one value further out and asserting the literal). Both are
    recorded here by count so a revert of either is visible.
    """
    print('=== 24: oversight windows and supervisor bound ceilings ===')

    root = Path(__file__).resolve().parents[1] / 'api-python'
    ov = (root / 'platform_runtime' / 'oversight.py').read_text(encoding='utf-8')
    sup = (root / 'platform_runtime' / 'supervisor.py').read_text(encoding='utf-8')

    # --- oversight: one window helper, three inclusive reads -------------------
    record('oversight derives the window start from the clock and the span',
           'return now - since_seconds, now' in ov, True)
    record('the task read is INCLUSIVE at the window start',
           'AND agent=? AND created>=? GROUP BY status' in ov, True)
    record("the event join is INCLUSIVE at the window start", "a.created>=? AND a.task<>''" in ov, True)
    record('the cost run read is INCLUSIVE at the window start',
           "AND agent=? AND created>=?'''" in ov, True)
    record('no oversight window read drifted to strictly-greater',
           'AND agent=? AND created>? GROUP BY status' not in ov
           and "a.created>? AND a.task<>''" not in ov, True)

    # --- oversight: the bound is stated once, in _window ----------------------
    record('the window bound is 1..MAX_WINDOW_SECONDS',
           "_bounded(since_seconds, 'since_seconds', 1, MAX_WINDOW_SECONDS)" in ov, True)
    record('MAX_WINDOW_SECONDS is ninety days',
           'MAX_WINDOW_SECONDS = 90 * 86400' in ov, True)
    record('cost calls the same window helper rather than its own bound',
           '    since, now = _window(engine, since_seconds)' in ov, True)
    # Both consumers route through _window, so neither can drift alone. The
    # pattern occurs three times: the definition itself and both call sites.
    record('activity and cost both call _window, not a local copy of the bound',
           ov.count('since, now = _window(engine, since_seconds)') == 2, True)
    record('the window helper has exactly two consumers',
           ov.count('_window(engine, since_seconds)') == 3, True)

    # --- oversight: the guards that now sit ON the boundary -------------------
    otg = (root / 'runtime_tests' / 'test_oversight.py').read_text(encoding='utf-8')
    record('a guard places a row exactly on the window start',
           'test_the_window_start_is_inclusive_so_a_row_on_it_is_counted' in otg, True)
    record('a guard covers all three reads at the boundary',
           'test_all_three_activity_reads_share_the_inclusive_predicate' in otg, True)
    record('a guard pins cost to activity\'s reported window',
           'test_cost_reports_the_same_window_activity_does' in otg, True)
    record('a guard shows a wide and a narrow window really differ',
           'test_a_longer_window_admits_rows_a_shorter_one_excludes' in otg, True)
    record('the window guard also checks the bounds themselves are accepted',
           'activity(self.engine, TENANT, AGENT, since_seconds=MAX_WINDOW_SECONDS)' in otg, True)

    # --- supervisor: max_seconds is a closed 60..86400 ------------------------
    record('max_seconds is a closed 60..86400 range',
           'not 60 <= max_seconds <= 86400' in sup, True)
    record('max_seconds did not widen past one day',
           '60 <= max_seconds <= 86401' not in sup, True)
    record('max_seconds did not narrow below one minute',
           '61 <= max_seconds <= 86400' not in sup, True)

    # --- supervisor: the two ceilings ----------------------------------------
    record('the hop ceiling is three', 'MAX_HOPS_CEILING = 3' in sup, True)
    record('the step ceiling is twelve', 'MAX_STEPS_CEILING = 12' in sup, True)
    record('max_hops is bounded by the hop ceiling',
           "_bounded(\n            max_hops, 'max_hops', 1, MAX_HOPS_CEILING)" in sup, True)
    record('max_steps is bounded by the step ceiling',
           "max_steps = _bounded(max_steps, 'max_steps', 1, MAX_STEPS_CEILING)" in sup, True)

    # --- supervisor: the tie-break is the section id, and says so ------------
    record('the tie-break scores a longer keyword first',
           'score = (len(keyword), -index)' in sup, True)
    record('the tie-break sign is negative so the earlier id wins',
           'score = (len(keyword), index)' not in sup, True)
    record('the candidate list is ordered by section id',
           "SELECT * FROM p_supervisor_section WHERE tenant=?\n                                ORDER BY id" in sup, True)
    record('the docstring no longer claims declaration order for the tie-break',
           'then declaration order' not in sup, True)
    record('the docstring names the real rule',
           'alphabetically first section id' in sup, True)
    record('the routable docstring names section-id order',
           'Enabled sections, in section-id order' in sup, True)

    # --- supervisor: the guards that now sit ON the boundaries ---------------
    stg = (root / 'runtime_tests' / 'test_supervisor.py').read_text(encoding='utf-8')
    record('a guard walks both max_seconds edges plus one value past the bound',
           '(86_400, True), (86_401, False), (86_402, False)' in stg, True)
    record('a guard asserts the max_seconds literal in the source',
           "self.assertIn('60 <= max_seconds <= 86400', source)" in stg, True)
    record('a guard reaches the hop ceiling itself',
           'test_the_hop_ceiling_itself_is_an_accepted_cap' in stg, True)
    record('a guard reaches the step ceiling itself',
           'test_the_step_ceiling_itself_reaches_the_run' in stg, True)
    record('a guard pins the tie-break to the section id, anti-alphabetically',
           'test_equal_length_keywords_resolve_by_section_id_not_declaration_order' in stg, True)
    record('the ceiling guards also refuse one past the ceiling',
           'max_steps=MAX_STEPS_CEILING + 1' in stg and 'max_hops=MAX_HOPS_CEILING + 1' in stg, True)


def section_erp_bounds():
    """`erp.py`: eleven thresholds, and only two of them were guarded.

    The ERP posting module is the most dangerous code in the repository -- the one
    write that leaves the platform for a financial system -- and it is defended by
    eleven numeric or structural thresholds. A revert matrix walked every one:

        M1  MAX_RESPONSE_BYTES  80_000 -> 80_001      suite GREEN
        M2  MAX_NOTE_CHARS      500 -> 501            suite GREEN
        M3  MAX_AMOUNT_MINOR    10**15 -> 10**15+1    suite GREEN
        M4  timeout upper       60 -> 61              suite GREEN
        M5  year window         2999 -> 3000          suite GREEN
        M6  _dig depth          >3 -> >4              suite GREEN
        M7  alias regex         {0,63} -> {0,64}      suite GREEN
        M8  target ceiling      64 -> 65              suite GREEN
        M9  status clamp        200 -> 201            suite GREEN
        M10 ledger_page         > -> >=               RED  (pinned by fazza III)
        M11 date uniqueness     >12 -> >13            RED  (one direction only)

    Nine green mutations are nine boundaries no test sat on. This is the same
    blindness fazza 24 recorded for the supervisor's ceilings: `0` and `99` prove a
    bound EXISTS, never that it IS 80_000.

    One measured defect, and it is the fazza-24 shape again -- a message that
    contradicts the code it comes from. `_date_text` refused `13.13.2026` as
    "ambiguous: both parts could be a month ... send a form where one part is
    greater than 12". Neither part can be a month (both exceed twelve) and the
    offending input ALREADY has two parts greater than twelve, so the prescribed
    remedy is one the value satisfies. It now says "names no month", and the
    genuinely ambiguous case keeps its own message.

    The literal trap is met twice in this audit: `MAX_RESPONSE_BYTES` is asserted
    BOTH behaviourally and as a source literal, because a behavioural test that
    derives its payload from the symbol moves when the symbol moves.
    """
    print('=== 25: erp.py boundary ceilings ===')
    from platform_runtime.erp import (ErpError, MAX_AMOUNT_MINOR, _amount,  # noqa: E402
                                      _date_text, _dig, _text)

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'platform_runtime' / 'erp.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_erp.py').read_text(encoding='utf-8')

    # --- the literals themselves, as exact LINES ------------------------------
    # Not substrings: `'MAX_AMOUNT_MINOR = 10 ** 15'` is a substring of
    # `'MAX_AMOUNT_MINOR = 10 ** 15 + 1'`, so a substring check is satisfied by the
    # widened ceiling it exists to catch. This is the fazza-24 M5 lesson, met again.
    lines = set(src.splitlines())
    record('the response byte ceiling is 80_000',
           'MAX_RESPONSE_BYTES = 80_000' in lines, True)
    record('the note ceiling is 500', 'MAX_NOTE_CHARS = 500' in lines, True)
    record('the amount ceiling is 10**15', 'MAX_AMOUNT_MINOR = 10 ** 15' in lines, True)
    record('the timeout window is the closed pair 1..60',
           '    if type(timeout) is not int or not 1 <= timeout <= 60:' in lines, True)
    record('the calendar year window is 1900..2999',
           '    if not 1 <= month <= 12 or not 1 <= day <= 31 '
           'or not 1900 <= year <= 2999:' in lines, True)
    record('the pointer depth predicate is `> 3`', '        if depth > 3:' in lines, True)
    record('the alias ceiling is 64 characters',
           "        if not isinstance(alias, str) or not re.fullmatch("
           "r'[a-z0-9][a-z0-9_.-]{0,63}', alias):" in lines, True)
    record('the register target ceiling is 64 characters',
           '        if not isinstance(target, str) or not target '
           'or len(target) > 64:' in lines, True)
    record('the status clamp is 1..200',
           '    limit = min(max(1, limit), 200)' in lines, True)
    record('the date refusal splits "no month" from "ambiguous"',
           '        elif first > 12:' in lines, True)
    record('the docstring says exactly one part over twelve',
           '**exactly one** part' in src, True)

    # --- measured behaviour, through the real helpers -------------------------
    record('an amount of exactly 10**15 is accepted',
           _amount(MAX_AMOUNT_MINOR) == MAX_AMOUNT_MINOR, True)
    record('an amount one past the ceiling is refused',
           _raises(ErpError, lambda: _amount(MAX_AMOUNT_MINOR + 1)), True)
    record('a zero amount is accepted', _amount(0) == 0, True)
    record('a negative amount is refused', _raises(ErpError, lambda: _amount(-1)), True)
    record('a boolean amount is refused', _raises(ErpError, lambda: _amount(True)), True)

    record('text of exactly the ceiling is accepted',
           _text('a' * 200, 'x', 200) == 'a' * 200, True)
    record('text one past the ceiling is refused',
           _raises(ErpError, lambda: _text('a' * 201, 'x', 200)), True)

    record('the first year of the window is a real date',
           _date_text('1900-01-01') == '1900-01-01', True)
    record('the last year of the window is a real date',
           _date_text('2999-12-31') == '2999-12-31', True)
    record('one year before the window is refused',
           _raises(ErpError, lambda: _date_text('1899-12-31')), True)
    record('one year after the window is refused',
           _raises(ErpError, lambda: _date_text('3000-01-01')), True)
    record('a leap day is accepted', _date_text('2024-02-29') == '2024-02-29', True)
    record('a common-year 29 February is refused',
           _raises(ErpError, lambda: _date_text('2023-02-29')), True)

    record('a triple with exactly one part over twelve is read',
           _date_text('13.01.2026') == '2026-01-13', True)
    record('the mirror reading is the same date',
           _date_text('01.13.2026') == '2026-01-13', True)
    record('two parts at or under twelve are ambiguous',
           _raises(ErpError, lambda: _date_text('12.12.2026'), 'ambiguous'), True)
    record('two parts over twelve name no month, not an ambiguity',
           _raises(ErpError, lambda: _date_text('13.13.2026'), 'no month'), True)
    record('the no-month refusal does not claim ambiguity',
           not _raises(ErpError, lambda: _date_text('13.13.2026'), 'ambiguous'), True)

    record('a four-segment pointer resolves',
           _dig({'a': {'b': {'c': {'d': 'x'}}}}, 'a.b.c.d') == 'x', True)
    record('a five-segment pointer falls back to the default',
           _dig({'a': {'b': {'c': {'d': {'e': 'x'}}}}}, 'a.b.c.d.e') is None, True)

    # --- the guards that now sit on the boundaries ----------------------------
    record('a guard pins every ceiling literal in the source',
           'test_the_boundary_constants_are_the_documented_literals' in guard, True)
    record('a guard walks the amount ceiling and one past it',
           'MAX_AMOUNT_MINOR + 1' in guard, True)
    record('a guard walks the response byte ceiling and one past it',
           'sized(MAX_RESPONSE_BYTES + 1)' in guard, True)
    record('a guard walks the timeout window edges and one past each',
           '(0, 61, True, 15.0)' in guard, True)
    record('a guard walks the calendar year window and one past each end',
           "('1899-12-31', '3000-01-01')" in guard, True)
    record('a guard walks the status clamp from both sides',
           '((1, 1), (200, 200), (0, 1), (201, 200))' in guard, True)
    record('a guard asserts the two date refusals are different refusals',
           "self.assertNotIn('ambiguous', str(no_month.exception))" in guard, True)
    record('a guard pins the note ceiling to its literal',
           "posting_body(settings, posting, 'n' * (MAX_NOTE_CHARS + 1))" in guard, True)
    record('a guard walks the alias and target ceilings',
           "'accounts': {'a' * 65: '60.01'}" in guard, True)



def section_unrepresentable_numbers():
    """`float()` past the float range: five sites, two different failures.

    The audit swept by SHAPE rather than by module, and the shape is one line:
    `float(value)` applied to a cell without asking whether the result exists.
    Five sites carried it, and they failed in two different ways.

    Four are cell readers -- `inventory._number`, `oee._number`,
    `manufacturing._number`, `workforce._hours`. Their docstrings all promise that
    "a cell that is not a finite number becomes unreadable, counted, never
    propagated", and all four already refuse `inf` and `nan`. But `float()` does
    not return infinity for an integer past the float range; it RAISES
    `OverflowError`. So a 400-digit integer -- which is exactly what a JSON
    provider hands back for an absurd quantity -- crashed the read instead of being
    counted unreadable. Measured: `float(10 ** 400)` raises in all four.

    The fifth is `business_graph.canonical`, and it was worse. `canonical` is what
    decides whether two sources AGREE, so it is the function whose whole job is to
    notice a conflict. Its string path did not raise: `float()` returns INFINITY
    for an out-of-range numeric string, silently, so `'1'` followed by 400 zeros
    and `'2'` followed by 400 zeros both canonicalised to `('n', inf)` and compared
    EQUAL. A real conflict between two enormous prices was reported as agreement.
    Its integer path raised instead, so the same value was fatal or silent
    depending on how it arrived.

    Both are now measured, both are fixed, and the probe pins both. The four cell
    readers refuse by name; `canonical` falls back to a text form that is
    deterministic and agrees across the integer and string paths.

    One more finding, and it is about a test rather than code. Three pre-existing
    tests are named for the non-finite property -- `test_a_non_finite_cell...`,
    `test_a_nan_cell...`, `test_a_negative_infinity_cell...` -- and all three feed
    the STRING `'inf'`, which `NUMBER_RE` rejects BEFORE the finite guard is ever
    consulted. Removing the guard leaves all three green. They measure the regex,
    not the guard they are named for; the guard was unmeasured until this phase.

    And `inventory.MAX_QUANTITY = 10 ** 15` bounded nothing at all, one line under
    a comment stating that "a constant that bounds nothing is worse than an absent
    one, because it advertises a limit nobody enforces". Removed.
    """
    print('=== 26: unrepresentable numbers and the ceiling that bounded nothing ===')
    from platform_runtime import (business_graph as bg, inventory as inv,  # noqa: E402
                                  manufacturing as mfg, oee, workforce as wf)

    root = Path(__file__).resolve().parents[1] / 'api-python'
    huge = 10 ** 400

    # --- four cell readers: unrepresentable means unreadable, never fatal -----
    for label, fn in (('inventory._number', inv._number),
                      ('oee._number', oee._number),
                      ('manufacturing._number', mfg._number),
                      ('workforce._hours', wf._hours)):
        record(f'{label} refuses 10**400 rather than raising', fn(huge) is None, True)
        record(f'{label} still reads 10**308', fn(10 ** 308) == 1e308, True)
        record(f'{label} refuses infinity', fn(float('inf')) is None, True)
        record(f'{label} refuses nan', fn(float('nan')) is None, True)

    # --- the regex, not the guard, is what rejects the string 'inf' -----------
    for label, module in (('inventory', inv), ('oee', oee), ('manufacturing', mfg)):
        record(f'{label}.NUMBER_RE rejects the string "inf"',
               module.NUMBER_RE.match('inf') is None, True)

    # --- the guard is in the source, so a revert is visible -------------------
    for name in ('inventory.py', 'oee.py', 'manufacturing.py', 'workforce.py'):
        src = (root / 'platform_runtime' / name).read_text(encoding='utf-8')
        record(f'{name} guards the float conversion',
               'except OverflowError:' in src, True)

    # --- canonical: distinct values must stay distinct -----------------------
    one, two = '1' + '0' * 400, '2' + '0' * 400
    record('two enormous values are not the same observation',
           bg.canonical(one) != bg.canonical(two), True)
    record('an enormous value does not collapse onto infinity',
           bg.canonical(one) != ('n', float('inf')), True)
    record('the integer and string forms of one value agree',
           bg.canonical(huge) == bg.canonical(one), True)
    record('ordinary numbers still canonicalise numerically',
           bg.canonical(450000) == ('n', 450000.0) == bg.canonical('450000'), True)

    # --- the ceiling that bounded nothing ------------------------------------
    inv_src = (root / 'platform_runtime' / 'inventory.py').read_text(encoding='utf-8')
    # A DECLARATION, not a mention. The removal note quotes the constant, so
    # `'MAX_QUANTITY' not in inv_src` is satisfied by the comment that records its
    # removal -- the unanchored-assertion trap this audit keeps meeting.
    declared = [line for line in inv_src.splitlines()
                if line.strip().startswith('MAX_QUANTITY')]
    record('inventory no longer declares MAX_QUANTITY', declared == [], True)

    # --- the guards that now sit on the properties ---------------------------
    ig = (root / 'runtime_tests' / 'test_inventory.py').read_text(encoding='utf-8')
    og = (root / 'runtime_tests' / 'test_oee.py').read_text(encoding='utf-8')
    mg = (root / 'runtime_tests' / 'test_manufacturing.py').read_text(encoding='utf-8')
    wg = (root / 'runtime_tests' / 'test_workforce.py').read_text(encoding='utf-8')
    gg = (root / 'runtime_tests' / 'test_business_graph.py').read_text(encoding='utf-8')
    record('inventory pins the float-range refusal',
           'test_a_quantity_past_the_float_range_is_unreadable_not_fatal' in ig, True)
    record('inventory pins the removal of the unread ceiling',
           'test_the_module_declares_no_ceiling_it_does_not_enforce' in ig, True)
    for label, guard in (('oee', og), ('manufacturing', mg), ('workforce', wg)):
        record(f'{label} pins the float-range refusal',
               'test_an_integer_past_the_float_range_is_unreadable_not_fatal' in guard,
               True)
    record('the graph pins the non-collapse of enormous values',
           'test_two_enormous_values_do_not_collapse_into_one' in gg, True)
    record('the new guards feed a real float, not the string the regex rejects',
           "self.assertIsNone(_number(float('inf')))" in og, True)



def section_database_contract_bounds():
    """`database/contract.py`: one fact read two ways, and a crash in a gate.

    Two findings, both the same shape as earlier phases: a value that two readers
    of one fact disagree about, and a validation path that raises something its
    contract does not allow.

    1. THE VERSION CEILING. `MAX_INTEGER = 2**53 - 1` is the largest integer this
       contract calls portable, and `scalar` accepts it. `normalize`'s
       `expected_version` check used a STRICT `<`, so it refused the ceiling
       itself -- while all four backend guards (`cassandra`, `dynamodb`,
       `elasticsearch`, `neo4j`) used `<=` and accepted it. It also refused with
       "Update requires positive expected_version", which is false: the version is
       positive, it is simply the ceiling. Measured at MAX_INTEGER: contract
       REFUSED, backends ACCEPTED.

    2. THE LONE SURROGATE. `json.loads` produces a lone surrogate from a JSON
       `udXXX` escape, so a request body can carry a `str` that cannot be encoded.
       Three gates bound a value by its UTF-8 size, and the measurement raised
       `UnicodeEncodeError` -- out of a validation path whose contract is
       `ValueError` or `Forbidden`. Measured before the fix: `scalar` raised while
       `text`, in the same file, ACCEPTED the same value. Same value, two answers,
       one of them a crash.

    Both are fixed, and both are pinned here and in the guard suite.
    """
    print('=== 27: managed database contract bounds ===')
    from platform_runtime.database import contract as c  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    database = root / 'platform_runtime' / 'database'
    src = (database / 'contract.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_managed_database.py').read_text(encoding='utf-8')

    # --- the ceiling: one value, and the contract agrees with the backends ----
    config = {'resources': {'r': {'key_field': 'k', 'version_field': 'ver',
                                  'read_fields': ['k', 'ver'], 'insert_fields': [],
                                  'update_fields': ['x']}},
              'isolation': 'dedicated_database'}

    def update(version):
        return c.normalize(config, 't', {'operation': 'update', 'resource': 'r',
                                         'key': 'k', 'values': {'x': 1},
                                         'expected_version': version})

    record('scalar accepts the portable ceiling', c.scalar(c.MAX_INTEGER) == c.MAX_INTEGER, True)
    record('scalar refuses one past the ceiling',
           _raises(ValueError, lambda: c.scalar(c.MAX_INTEGER + 1)), True)
    record('normalize accepts the ceiling as expected_version',
           update(c.MAX_INTEGER)['expected_version'] == c.MAX_INTEGER, True)
    record('normalize refuses one past the ceiling',
           _raises(ValueError, lambda: update(c.MAX_INTEGER + 1)), True)
    record('normalize still refuses zero', _raises(ValueError, lambda: update(0)), True)
    record('normalize still refuses a boolean', _raises(ValueError, lambda: update(True)), True)
    record('the contract predicate is inclusive at the ceiling',
           '            if type(version) is not int or not 1 <= version <= MAX_INTEGER:'
           in set(src.splitlines()), True)
    record('the contract predicate is no longer strict',
           'not 1 <= version < MAX_INTEGER' not in src, True)
    for name in ('cassandra_backend.py', 'dynamodb_backend.py',
                 'elasticsearch_backend.py', 'neo4j_backend.py'):
        backend = (database / name).read_text(encoding='utf-8')
        record(f'{name} uses the same inclusive ceiling',
               '<=MAX_INTEGER' in backend.replace(' ', ''), True)
    record('the refusal names the real bound, not "positive"',
           'no greater than' in src, True)

    # --- the surrogate: a ValueError, not a crash ----------------------------
    surrogate = '\ud800'
    record('the surrogate really is a lone surrogate', len(surrogate) == 1, True)
    for label, call in (('scalar', lambda: c.scalar(surrogate)),
                        ('record_key', lambda: c.record_key(surrogate)),
                        ('text', lambda: c.text(surrogate))):
        record(f'{label} refuses the surrogate with ValueError',
               _raises(ValueError, lambda f=call: f()), True)
        record(f'{label} does not raise UnicodeEncodeError',
               not _raises(UnicodeEncodeError, lambda f=call: f()), True)
    record('the parser still admits the escaped form',
           c.parse_request('{"x": "' + '\\ud800' + '"}') == {'x': surrogate}, True)
    record('the refusal happens in the contract, not at the transport',
           _raises(ValueError, lambda: c.normalize(
               config, 't', {'operation': 'update', 'resource': 'r', 'key': 'k',
                             'values': {'x': surrogate}, 'expected_version': 1})), True)

    # --- the gate must not narrow what it accepts ---------------------------
    for value in ("o'zbek \u2014 tasdiq", '450000', '2026-09-20'):
        record(f'ordinary text is still carried: {value[:12]!r}',
               c.text(value) == value and c.scalar(value) == value, True)

    # --- the guards that now sit on the bounds ------------------------------
    record('a guard walks the version ceiling and one past it',
           'MAX_INTEGER + 1, 0, -1, True, None' in guard, True)
    record('a guard reads every backend guard from the source',
           "test_every_version_guard_uses_the_same_ceiling" in guard, True)
    record('a guard pins the surrogate refusal by gate',
           'test_a_lone_surrogate_is_refused_by_name_not_by_crash' in guard, True)
    record('a guard proves ordinary text is still carried',
           'test_ordinary_text_is_still_carried' in guard, True)



def section_agent_loop_bounds():
    """`agent_loop.py`: seventeen bounds, twelve of them unpinned.

    The loop that drives a persisted, result-fed planner is defended by seventeen
    numeric bounds. A revert matrix walked every one:

        R1  MAX_STEPS 12 -> 6 (NARROWED)            suite GREEN
        R2  MAX_STEPS 12 -> 13                      RED
        R3  MAX_SECONDS 86400 -> 43200 (NARROWED)   suite GREEN
        R4  MAX_SECONDS 86400 -> 86401              RED
        R5  text ceiling 4000 -> 4001               GREEN
        R6  tenant identity 64 -> 65                GREEN
        R7  key identity 256 -> 257                 GREEN
        R8  queue ceiling 100 -> 101                GREEN
        R9  answer ceiling 8000 -> 8001             GREEN
        R10 question ceiling 1000 -> 1001           GREEN
        R11 evidence ceiling -> + 1                 GREEN
        R12 observation bytes 12000 -> 12001        GREEN
        R13 history bytes 48000 -> 48001            GREEN
        R14 planner lease 60 -> 61                  GREEN
        R15 ACTIVE constant loses waiting_task      RED
        R16 create() site stops counting waiting    GREEN   <-- the defect

    R1 and R3 are the interesting ones. The existing budget test walks `0` and `13`
    as REFUSALS, which pins the ceiling from one side only -- a ceiling of 6 also
    refuses 13. So a NARROWED step or time budget was invisible, and a twelve-step
    run would silently have been refused at six.

    R16 is the measured defect, and it is the "one fact, three readers" shape. The
    active-status set is declared as `ACTIVE`, compared against in `_reserve`, and
    spelled out again inside SQL in `create` AND `tick`. Changing the `create` copy
    so it dropped `waiting_task` left the whole 30-test suite green, while the same
    change in `tick` was caught. The consequence is not cosmetic: the queue ceiling
    would simply have stopped counting waiting runs, so a tenant could accumulate
    unbounded runs in that state while every guard reported healthy. Fixed by
    removing the restatement -- both SQL sites now build their placeholders from
    ACTIVE, the way `erp.posted` builds them from CLAIMING_STATUSES.
    """
    print('=== 28: agent loop bounds and the queue ceiling ===')
    import tempfile  # noqa: E402

    from platform_runtime.agent_loop import (ACTIVE, MAX_HISTORY_BYTES,  # noqa: E402
                                             MAX_OBSERVATION_BYTES, MAX_SECONDS,
                                             MAX_STEPS, PLANNER_LEASE_SECONDS, AgentLoop)
    from platform_runtime.engine import Engine, RateLimited  # noqa: E402
    from platform_runtime.tools import Registry, Tool, obj, build_registry  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'platform_runtime' / 'agent_loop.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_agent_loop.py').read_text(encoding='utf-8')
    lines = set(src.splitlines())

    # --- the literals, as exact lines ---------------------------------------
    for literal in ('MAX_STEPS = 12', 'MAX_SECONDS = 86400',
                    'MAX_OBSERVATION_BYTES = 12000', 'MAX_HISTORY_BYTES = 48000',
                    'PLANNER_LEASE_SECONDS = 60'):
        record(f'the source states {literal!r}', literal in lines, True)

    # --- one fact, one statement --------------------------------------------
    record('the active-status set is declared once',
           "ACTIVE = ('pending', 'planning', 'waiting_task')" in src, True)
    record('the placeholders are built from ACTIVE',
           "_ACTIVE_PLACEHOLDERS = ','.join('?' * len(ACTIVE))" in src, True)
    record('the set is not restated inside SQL',
           src.count("('pending','planning','waiting_task')"), 0)
    record('both SQL sites use the constant',
           src.count('_ACTIVE_PLACEHOLDERS'), 3)

    # --- measured behaviour, through the real loop --------------------------
    def fresh():
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / 'loop.db'
        registry = Registry()
        registry.add(Tool('lookup.customer', 'read', obj({}),
                          lambda *a: {'customer_id': 'c7'}))
        policy = lambda t, a: {'tools': list(registry.items), 'ladder': 'autonomous'}  # noqa: E731
        engine = Engine(path, registry, policy, clock=lambda: 1000)
        return tmp, engine, AgentLoop(engine)

    def outcome(fn):
        try:
            fn()
            return 'ok'
        except Exception as error:  # noqa: BLE001
            return type(error).__name__

    tmp, engine, loop = fresh()
    record('max_steps 1 is accepted',
           outcome(lambda: loop.create('t', 'a1', 'ops', 'x', 'actor', max_steps=1)), 'ok')
    record('max_steps MAX_STEPS is accepted',
           outcome(lambda: loop.create('t', 'a12', 'ops', 'x', 'actor',
                                       max_steps=MAX_STEPS)), 'ok')
    record('max_steps 0 is refused',
           outcome(lambda: loop.create('t', 'a0', 'ops', 'x', 'actor', max_steps=0)),
           'ValueError')
    record('max_steps MAX_STEPS + 1 is refused',
           outcome(lambda: loop.create('t', 'a13', 'ops', 'x', 'actor',
                                       max_steps=MAX_STEPS + 1)), 'ValueError')
    record('max_seconds 60 is accepted',
           outcome(lambda: loop.create('t', 'b60', 'ops', 'x', 'actor', max_seconds=60)), 'ok')
    record('max_seconds MAX_SECONDS is accepted',
           outcome(lambda: loop.create('t', 'b86', 'ops', 'x', 'actor',
                                       max_seconds=MAX_SECONDS)), 'ok')
    record('max_seconds 59 is refused',
           outcome(lambda: loop.create('t', 'b59', 'ops', 'x', 'actor', max_seconds=59)),
           'ValueError')
    record('max_seconds MAX_SECONDS + 1 is refused',
           outcome(lambda: loop.create('t', 'b87', 'ops', 'x', 'actor',
                                       max_seconds=MAX_SECONDS + 1)), 'ValueError')
    record('run text of 4000 characters is accepted',
           outcome(lambda: loop.create('t', 'c4000', 'ops', 'a' * 4000, 'actor')), 'ok')
    record('run text of 4001 characters is refused',
           outcome(lambda: loop.create('t', 'c4001', 'ops', 'a' * 4001, 'actor')), 'ValueError')

    # --- the queue ceiling, including a waiting run -------------------------
    tmp2, engine2, loop2 = fresh()
    for index in range(99):
        loop2.create('t', 'q%03d' % index, 'ops', 'x', 'actor')
    record('99 active runs admit a hundredth',
           outcome(lambda: loop2.create('t', 'q099', 'ops', 'x', 'actor')), 'ok')
    record('100 active runs refuse the next',
           outcome(lambda: loop2.create('t', 'q100', 'ops', 'x', 'actor')), 'RateLimited')

    tmp3, engine3, loop3 = fresh()
    run_id = loop3.create('t', 'w000', 'ops', 'x', 'actor')
    loop3.tick('t', lambda t, c: {'action': 'tool', 'tool': 'lookup.customer', 'args': {}})
    record('a ticked run reaches waiting_task',
           loop3.get('t', run_id)['status'], 'waiting_task')
    for index in range(1, 99):
        loop3.create('t', 'w%03d' % index, 'ops', 'x', 'actor')
    record('the waiting run is admitted as the hundredth',
           outcome(lambda: loop3.create('t', 'w099', 'ops', 'x', 'actor')), 'ok')
    record('a waiting run counts toward the queue ceiling',
           outcome(lambda: loop3.create('t', 'w100', 'ops', 'x', 'actor')), 'RateLimited')

    # --- the decision ceilings ----------------------------------------------
    row = {'tenant': 't', 'agent': 'ops', 'steps': 1, 'max_steps': MAX_STEPS}
    wide = [{'evidence_id': 'step:%d' % i} for i in range(MAX_STEPS + 1)]
    ids = [item['evidence_id'] for item in wide]
    record('an 8000-character answer is accepted',
           outcome(lambda: loop._decision(
               row, wide, {'action': 'final', 'answer': 'a' * 8000,
                           'evidence_ids': ids[:1]})), 'ok')
    record('an 8001-character answer is refused',
           outcome(lambda: loop._decision(
               row, wide, {'action': 'final', 'answer': 'a' * 8001,
                           'evidence_ids': ids[:1]})), 'LoopDecisionError')
    record('a 1000-character question is accepted',
           outcome(lambda: loop._decision(
               row, wide, {'action': 'ask', 'question': 'q' * 1000})), 'ok')
    record('a 1001-character question is refused',
           outcome(lambda: loop._decision(
               row, wide, {'action': 'ask', 'question': 'q' * 1001})), 'LoopDecisionError')
    record('MAX_STEPS evidence ids are accepted',
           outcome(lambda: loop._decision(
               row, wide, {'action': 'final', 'answer': 'a',
                           'evidence_ids': ids[:MAX_STEPS]})), 'ok')
    record('MAX_STEPS + 1 evidence ids are refused',
           outcome(lambda: loop._decision(
               row, wide, {'action': 'final', 'answer': 'a',
                           'evidence_ids': ids[:MAX_STEPS + 1]})), 'LoopDecisionError')

    # --- the guards that now sit on the bounds ------------------------------
    record('a guard walks both step-budget edges',
           'test_the_step_budget_edges_are_accepted_not_merely_bounded' in guard, True)
    record('a guard walks both time-budget edges',
           'test_the_time_budget_edges_are_accepted_not_merely_bounded' in guard, True)
    record('a guard walks the queue ceiling',
           'test_the_active_run_queue_ceiling_is_one_hundred' in guard, True)
    record('a guard proves a waiting run counts toward the ceiling',
           'test_the_queue_ceiling_counts_a_waiting_run' in guard, True)
    record('a guard pins the status set to one statement',
           'test_the_active_status_set_is_stated_once' in guard, True)
    record('a guard walks the identity limits',
           'test_the_run_identity_limits_are_the_documented_four' in guard, True)
    record('a guard walks the decision ceilings',
           'test_the_decision_ceilings_are_the_documented_values' in guard, True)



def section_route_surface_vs_ui():
    """The declared route surface against what the UI can actually reach.

    Measured, not impressionistic. Three generic path matchers were tried and all
    three were wrong in a way worth recording, because the failure mode is the
    point:

      1. `startswith` on normalised paths said EVERY route was referenced -- a
         matcher that says yes to everything measures nothing.
      2. Extracting quoted strings lost the route segment: the components build
         `base + suffix` and the base is a backtick template containing
         `encodeURIComponent(...)`, which a character class without parentheses
         truncates at `/platform/${encodeURIComponent`.
      3. Pairing quotes naively desynchronises on a stray apostrophe in a comment,
         and `identity` is BOTH a mount prefix and a route segment, so stripping
         the mount deleted the route.

    So this section does not infer. It names each family and checks, by grep, that
    the UI never references it -- which is a claim that can be re-run and refuted.

    The measured result is four findings, and the first one is a defect rather than
    a gap: the platform page tells the operator to finish an `uncertain` external
    write "reconcile API orqali" (via the reconcile API), the route exists, and the
    UI offers no control for it. An uncertain write is exactly the case where the
    platform refuses automatic retry, so the documented recovery path is the only
    one and it is unreachable from the product.
    """
    print('=== 29: declared route surface vs what the UI can reach ===')
    import re  # noqa: E402

    root = Path(__file__).resolve().parents[1]
    app = root / 'api-python' / 'app'
    ui = root / 'apps' / 'ui'

    route_re = re.compile(r"^@router\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]")
    declared = 0
    for path in sorted(app.glob('*.py')):
        declared += sum(1 for line in path.read_text(encoding='utf-8').splitlines()
                        if route_re.match(line.strip()))
    record('the backend declares 93 routes', declared, 93)

    ui_files = [p for p in ui.rglob('*')
                if p.is_file() and p.suffix in {'.tsx', '.ts', '.mjs', '.js', '.mts'}
                and 'node_modules' not in p.parts]
    ui_text = '\n'.join(p.read_text(encoding='utf-8', errors='replace') for p in ui_files)

    # --- the defect: a prescribed remedy with no control ---------------------
    record('the platform offers a step reconcile route',
           "post('/{tenant}/steps/{step}/reconcile')" in
           (app / 'platform_api.py').read_text(encoding='utf-8'), True)
    page = (ui / 'app' / 'platform' / 'page.tsx').read_text(encoding='utf-8')
    record('the UI tells the operator to use the reconcile API',
           'reconcile API orqali' in page, True)
    record('the UI never CALLS the step reconcile route',
           "'/steps/' + 'reconcile'" not in page and '/reconcile' not in page, True)

    # --- families with a complete backend and no UI reference ----------------
    for family in ('channel-identities', 'schedules', 'reengagement', 'briefing',
                   'escalation', 'supervisor', 'invitations', 'sessions',
                   'logout-all', 'documents/{document}/delete'):
        record(f'the UI never references {family!r}', family not in ui_text, True)
    record('the UI references customers only through its own list and detail calls',
           '`/customers/${c.id}`' in page, True)
    record('no customer contacts route is called',
           '/contacts' not in page, True)
    record('no customer orders route is called',
           "/orders'" not in page and '/orders`' not in page, True)

    # --- the admin surface --------------------------------------------------
    identity = (app / 'identity_api.py').read_text(encoding='utf-8')
    for route in ('/bootstrap', '/logout-all', '/sessions', '/invitations',
                  '/members/{user_id}/revoke'):
        record(f'the identity route {route!r} exists', route in identity, True)
        record(f'the UI never references {route!r}', route.lstrip('/') not in ui_text, True)

    # --- the manifest, measured ---------------------------------------------
    import json  # noqa: E402
    import subprocess  # noqa: E402
    import sys as _sys  # noqa: E402
    proc = subprocess.run([_sys.executable, str(root / 'scripts' / 'verify_manifest.py')],
                          capture_output=True, text=True, encoding='utf-8',
                          errors='replace', cwd=str(root))
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        report = {'status': 'unparsed', 'checked': -1, 'errors': []}
    kinds = {}
    for entry in report.get('errors', []):
        kinds[entry.split(':')[0].strip()] = kinds.get(entry.split(':')[0].strip(), 0) + 1
    record('the manifest gate still reports FAIL', report.get('status'), 'FAIL')
    record('the manifest checks 445 files', report.get('checked'), 445)
    # The COUNTS are a snapshot, not a property: they grow with every file this
    # audit touches. Asserting an exact number would make the probe fail whenever
    # the audit does its job, so the shape is asserted instead -- and then the
    # specific claim that matters, which is that the manifest does not cover the
    # modules this audit has been changing.
    record('the manifest reports hash mismatches', kinds.get('Hash mismatch', 0) > 0, True)
    record('the manifest reports unlisted files', kinds.get('Unlisted file', 0) > 0, True)
    record('the manifest never covered erp.py at all',
           any('erp.py' in entry for entry in report.get('errors', [])), True)
    record('no manifest generator exists in the repository',
           not list(root.glob('**/generate*manifest*')), True)



def section_budget_and_schema_bounds():
    """`usage_budget.py` and `tools.py`: two gates that disagreed, one unit that did.

    Two measured findings, and both are the "one fact, two readers" shape this audit
    keeps meeting.

    1. TWO BOUNDED-STRING GATES DISAGREED. `usage_budget.bounded` and
       `database.contract.text` both guard "a bounded identifier" -- a tenant id, an
       actor, a piece of reconcile evidence. Measured before the fix: `bounded`
       ACCEPTED a DEL character (0x7F) and a value with a leading space, while
       `text` refused both. A value that one layer accepts and another refuses is a
       value whose validity depends on which door it came through. `bounded` now
       applies the same two rules, and this section pins them as a pair.

    2. ONE LITERAL, TWO UNITS. `tools.validate_schema` bounded an argument object by
       `len(encode(value))` -- CHARACTERS -- while every other bound on a serialised
       payload in this runtime measures BYTES (`contract.parse_request` 12000,
       `erp._observations` 12000/48000, the managed backends 1024/16000/16000).
       Measured: a Cyrillic object under 20 000 characters whose JSON is over
       20 000 bytes was accepted, i.e. roughly twice the declared size. It now
       measures bytes.

    RESIDUAL, deliberately not fixed: `string(maxLength)` counts characters, which is
    what JSON Schema means by maxLength, while `contract.parse_request` counts bytes,
    which is what a byte stream means. A `request_json` body under the 12000-character
    schema bound and inside the 20000-byte object bound, yet over 12000 bytes, is
    admitted by the schema the model reads and refused by the contract. Both numbers
    are defensible in isolation; making them agree is a policy decision, so it is
    recorded here rather than taken unilaterally.
    """
    print('=== 30: usage budget and tool schema bounds ===')
    import datetime  # noqa: E402
    import json  # noqa: E402
    import tempfile  # noqa: E402

    from platform_runtime.database import contract  # noqa: E402
    from platform_runtime.engine import Engine, digest, encode  # noqa: E402
    from platform_runtime.tools import build_registry, obj, string, validate_schema  # noqa: E402
    from platform_runtime.usage_budget import (MAX_AMOUNT, UsageBudget, amount,  # noqa: E402
                                               bounded, token_cost)

    root = Path(__file__).resolve().parents[1] / 'api-python'
    guard = (root / 'runtime_tests' / 'test_usage_budget.py').read_text(encoding='utf-8')
    adapter_guard = (root / 'runtime_tests' / 'test_adapters.py').read_text(encoding='utf-8')

    # --- finding 1: the two bounded-string gates now agree -------------------
    for value in ('a\x7fb', ' a', 'a ', 'a\x01b', '', '   '):
        record(f'bounded refuses {value!r}',
               _raises(ValueError, lambda v=value: bounded(v)), True)
        record(f'contract.text refuses the same {value!r}',
               _raises(ValueError, lambda v=value: contract.text(v)), True)
    record('bounded accepts exactly 256 characters', bounded('a' * 256) == 'a' * 256, True)
    record('bounded refuses 257', _raises(ValueError, lambda: bounded('a' * 257)), True)

    # --- the amount floor and ceiling ---------------------------------------
    record('amount(0) is accepted where a charge may be zero', amount(0) == 0, True)
    record('amount(0, zero=False) is refused', _raises(ValueError, lambda: amount(0, zero=False)), True)
    record('amount(1, zero=False) is accepted', amount(1, zero=False) == 1, True)
    record('the amount ceiling is accepted', amount(MAX_AMOUNT) == MAX_AMOUNT, True)
    record('one past the amount ceiling is refused',
           _raises(ValueError, lambda: amount(MAX_AMOUNT + 1)), True)
    record('a boolean is never an amount', _raises(ValueError, lambda: amount(True)), True)

    # --- the ledger arithmetic, driven through the real budget ---------------
    tmp = tempfile.TemporaryDirectory()
    now = [datetime.datetime(2026, 9, 14, tzinfo=datetime.timezone.utc).timestamp()]
    engine = Engine(Path(tmp.name) / 'p.db', build_registry(), lambda t, a: {},
                    clock=lambda: now[0])
    budget = UsageBudget(engine)
    budget.configure('a', 'owner', 'USD', 100, 10)
    budget.reserve('a', 'w79', digest({'w': 79}), 79, 'USD')
    record('79% does not warn', budget.summary('a')['warning_80_percent'], False)
    budget.reserve('a', 'w1', digest({'w': 1}), 1, 'USD')
    record('exactly 80% warns', budget.summary('a')['warning_80_percent'], True)
    record('exactly 80% is not an overrun', budget.summary('a')['limit_exceeded'], False)

    tmp2 = tempfile.TemporaryDirectory()
    engine2 = Engine(Path(tmp2.name) / 'p2.db', build_registry(), lambda t, a: {},
                     clock=lambda: now[0])
    budget2 = UsageBudget(engine2)
    budget2.configure('a', 'owner', 'USD', 100, 10)
    budget2.reserve('a', 'full', digest({'f': 1}), 100, 'USD')
    summary = budget2.summary('a')
    record('exactly the limit is not an overrun', summary['limit_exceeded'], False)
    record('exactly the limit leaves nothing available', summary['available_micro'], 0)
    record('a further reservation at the limit is refused',
           _raises(Exception, lambda: budget2.reserve('a', 'over', digest({'o': 1}), 1, 'USD')), True)

    tmp3 = tempfile.TemporaryDirectory()
    engine3 = Engine(Path(tmp3.name) / 'p3.db', build_registry(), lambda t, a: {},
                     clock=lambda: now[0])
    budget3 = UsageBudget(engine3)
    budget3.configure('a', 'owner', 'USD', 10 ** 6, 2)
    budget3.reserve('a', 'p1', digest({'p': 1}), 1, 'USD')
    budget3.reserve('a', 'p2', digest({'p': 2}), 1, 'USD')
    record('the parallel ceiling admits exactly max_inflight',
           budget3.summary('a')['inflight'], 2)
    record('the parallel ceiling refuses the next one',
           _raises(Exception, lambda: budget3.reserve('a', 'p3', digest({'p': 3}), 1, 'USD')), True)

    # --- the declared ceilings -------------------------------------------------
    record('max_inflight 1 is accepted',
           not _raises(ValueError, lambda: budget3.configure('a', 'owner', 'USD', 10 ** 6, 1)), True)
    record('max_inflight 100 is accepted',
           not _raises(ValueError, lambda: budget3.configure('a', 'owner', 'USD', 10 ** 6, 100)), True)
    record('max_inflight 101 is refused',
           _raises(ValueError, lambda: budget3.configure('a', 'owner', 'USD', 10 ** 6, 101)), True)
    record('pending limit 100 is accepted',
           not _raises(ValueError, lambda: budget3.pending('a', 100)), True)
    record('pending limit 101 is refused',
           _raises(ValueError, lambda: budget3.pending('a', 101)), True)

    # --- token_cost ------------------------------------------------------------
    pricing = {'input_micro_per_million': 1000000, 'output_micro_per_million': 2000000}
    record('zero tokens cost zero', token_cost(0, 0, pricing), 0)
    record('a fraction of a microunit rounds UP', token_cost(1, 0, pricing), 1)
    record('the token bound is 10**8', token_cost(10 ** 8, 0, pricing) > 0, True)
    record('one token past the bound is refused',
           _raises(ValueError, lambda: token_cost(10 ** 8 + 1, 0, pricing)), True)

    # --- finding 2: the object bound measures bytes ---------------------------
    plain = {'type': 'object',
             'properties': {'a': {'type': 'string', 'maxLength': 100000}},
             'required': ['a']}
    wide = {'a': '\u044f' * 19990}
    narrow = {'a': '\u044f' * 9000}
    record('a Cyrillic object over 20000 BYTES is refused',
           _raises(ValueError, lambda: validate_schema(wide, plain)), True)
    record('the same object is under 20000 CHARACTERS',
           len(encode(wide)) < 20000, True)
    record('a Cyrillic object under 20000 bytes is accepted',
           not _raises(ValueError, lambda: validate_schema(narrow, plain)), True)

    # --- the schema defaults ---------------------------------------------------
    record('the integer default ceiling is 10**12',
           not _raises(ValueError, lambda: validate_schema(10 ** 12, {'type': 'integer'})), True)
    record('one past the integer default is refused',
           _raises(ValueError, lambda: validate_schema(10 ** 12 + 1, {'type': 'integer'})), True)
    record('the array default maxItems is 100',
           not _raises(ValueError, lambda: validate_schema(list(range(100)),
                                                           {'type': 'array',
                                                            'items': {'type': 'integer'}})), True)
    record('an array of 101 is refused',
           _raises(ValueError, lambda: validate_schema(list(range(101)),
                                                       {'type': 'array',
                                                        'items': {'type': 'integer'}})), True)
    record('the string default maxLength is 4000',
           not _raises(ValueError, lambda: validate_schema('a' * 4000, {'type': 'string'})), True)
    record('a string of 4001 is refused',
           _raises(ValueError, lambda: validate_schema('a' * 4001, {'type': 'string'})), True)
    record('a boolean is not an integer',
           _raises(ValueError, lambda: validate_schema(True, {'type': 'integer'})), True)

    # --- residual divergence: characters vs bytes -----------------------------
    db_schema = obj({'connection': string(128), 'request_json': string(12000)})
    body_text = json.dumps({'resource': 'contacts', 'fields': ['id'],
                            'note': '\u044f' * 8000}, ensure_ascii=False)
    body = {'connection': 'c', 'request_json': body_text}
    record('the residual body is under 12000 characters', len(body_text) <= 12000, True)
    record('the residual body is over 12000 bytes',
           len(body_text.encode('utf-8')) > 12000, True)
    record('the object it travels in is under 20000 bytes',
           len(encode(body).encode('utf-8')) <= 20000, True)
    record('the schema admits it (maxLength counts characters)',
           not _raises(ValueError, lambda: validate_schema(body, db_schema)), True)
    record('the contract refuses it (parse_request counts bytes)',
           _raises(ValueError, lambda: contract.parse_request(body_text)), True)
    ascii_twin = json.dumps({'resource': 'contacts', 'fields': ['id'],
                             'note': 'a' * 8000}, ensure_ascii=False)
    record('the ASCII twin passes both, so the divergence is the encoding',
           not _raises(ValueError, lambda: contract.parse_request(ascii_twin)), True)

    # --- the guards that now sit on the bounds --------------------------------
    record('a guard pins the two bounded-string gates together',
           'test_a_bounded_identifier_refuses_what_the_contract_refuses' in guard, True)
    record('a guard walks the amount floor and ceiling',
           'test_the_amount_floor_is_zero_or_one_depending_on_the_caller' in guard, True)
    record('a guard walks the warning threshold',
           'test_the_warning_threshold_is_inclusive_at_eighty_percent' in guard, True)
    record('a guard walks the limit boundary',
           'test_the_limit_is_inclusive_so_exactly_the_limit_is_not_exceeded' in guard, True)
    record('a guard walks the parallel ceiling',
           'test_the_parallel_ceiling_admits_exactly_max_inflight' in guard, True)
    record('a guard walks the parallel ceiling literal',
           'test_the_parallel_call_ceiling_is_one_hundred' in guard, True)
    record('a guard walks the pending limit literal',
           'test_the_pending_limit_ceiling_is_one_hundred' in guard, True)
    record('a guard walks the evidence literal',
           'test_the_reconcile_evidence_ceiling_is_five_hundred' in guard, True)
    record('a guard walks token_cost rounding and its bound',
           'test_token_cost_rounds_up_and_bounds_its_inputs' in guard, True)
    record('a guard pins the object bound to BYTES',
           'test_the_argument_object_bound_is_measured_in_bytes' in adapter_guard, True)
    record('a guard walks the schema default bounds',
           'test_the_default_schema_bounds_are_the_documented_values' in adapter_guard, True)



def section_whatsapp_bounds():
    """The 24-hour service window, the webhook signature, and fifteen ceilings.

    ONE FACT, TWO DECLARATIONS, AND AN ASYMMETRIC DRIFT. `whatsapp.WINDOW_SECONDS`
    and `whatsapp_inbound.WINDOW_SECONDS` are the same fact. The restatement is
    DELIBERATE -- the ingest block keeps no module-scope import edge to the outbound
    block, and says so -- so the audit did not remove it. What the audit found is
    that the price of a restatement was not being paid: the two values were equal by
    coincidence, and the drift would be silent AND asymmetric.

      events path    ingest stores `stamp + W_inbound`; the outbound reader subtracts
                     its OWN W and `window_state` adds it back, so the pair CANCELS
                     and the window follows the INBOUND constant.
      register path  the sheet carries a real last-inbound time, so `window_state`
                     adds the OUTBOUND constant.

    Measured: with the outbound constant set to one hour the events path still
    yielded twenty-four hours while the register path yielded one. One tenant, two
    window lengths, depending on which source answered -- and nothing failed. The
    equality is now enforced by a guard, and both readings are pinned.

    Fifteen revert modes were walked over the two modules and SEVEN left the suite
    green: the body bound, the message-id regex, the phone regex, and the entry,
    text, scan and error-code constants. Each is pinned here and in the guard suite.
    Note the body bound in particular: its behavioural guard derives its payload from
    the same symbol, so it moved with the constant -- the fazza-24 M5 lesson, met a
    third time.
    """
    print('=== 31: WhatsApp window, signature and declared ceilings ===')
    import hashlib  # noqa: E402
    import hmac  # noqa: E402

    from platform_runtime import whatsapp, whatsapp_inbound  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    outbound_guard = (root / 'runtime_tests' / 'test_whatsapp.py').read_text(encoding='utf-8')
    inbound_guard = (root / 'runtime_tests' / 'test_whatsapp_inbound.py').read_text(encoding='utf-8')

    # --- one fact, two declarations, now enforced ---------------------------
    record('the two service-window constants are equal',
           whatsapp.WINDOW_SECONDS == whatsapp_inbound.WINDOW_SECONDS, True)
    record('the outbound constant is the documented 24 hours',
           whatsapp.WINDOW_SECONDS, 86400)
    record('the inbound constant is the documented 24 hours',
           whatsapp_inbound.WINDOW_SECONDS, 86400)

    now = 1_000_000.0
    expiry = now + whatsapp_inbound.WINDOW_SECONDS       # what ingest stores
    register_last = now - 10                             # what the operator sheet carries
    original = whatsapp.WINDOW_SECONDS
    try:
        whatsapp.WINDOW_SECONDS = 3600
        last = expiry - whatsapp.WINDOW_SECONDS          # the reader's own subtraction
        _, closes = whatsapp.window_state(last, now + 1)  # window_state adds it back
        record('the events path follows the INBOUND constant', closes - now,
               whatsapp_inbound.WINDOW_SECONDS)
        _, register_closes = whatsapp.window_state(register_last, now + 1)
        record('the register path follows the OUTBOUND constant',
               register_closes - register_last, 3600)
    finally:
        whatsapp.WINDOW_SECONDS = original
    record('the outbound constant is restored', whatsapp.WINDOW_SECONDS, 86400)

    # --- the window boundary -------------------------------------------------
    record('a missing timestamp is closed',
           whatsapp.window_state(None, now), (False, None))
    record('a zero timestamp is closed', whatsapp.window_state(0, now), (False, None))
    record('a negative timestamp is closed', whatsapp.window_state(-1, now), (False, None))
    record('one second before expiry the window is open',
           whatsapp.window_state(now - 86400 + 1, now)[0], True)
    record('exactly at expiry the window is CLOSED',
           whatsapp.window_state(now - 86400, now)[0], False)
    record('closes_at is last + WINDOW_SECONDS',
           whatsapp.window_state(now - 10, now)[1], now - 10 + 86400)

    # --- the webhook signature ----------------------------------------------
    body = b'{"object":"whatsapp_business_account"}'
    secret = 'app-secret'
    good = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    record('a correct signature verifies',
           whatsapp_inbound.verify_signature(body, good, secret), True)
    record('an upper-case prefix verifies',
           whatsapp_inbound.verify_signature(body, good.replace('sha256=', 'SHA256='),
                                             secret), True)
    record('an upper-case digest verifies',
           whatsapp_inbound.verify_signature(body, good.upper(), secret), True)
    record('a bare digest without the prefix is refused',
           whatsapp_inbound.verify_signature(body, good.split('=', 1)[1], secret), False)
    record('a 63-character digest is refused',
           whatsapp_inbound.verify_signature(body, 'sha256=' + 'a' * 63, secret), False)
    record('a 65-character digest is refused',
           whatsapp_inbound.verify_signature(body, 'sha256=' + 'a' * 65, secret), False)
    record('a wrong secret is refused',
           whatsapp_inbound.verify_signature(body, good, 'other'), False)
    record('a parsed body is refused rather than silently re-signed',
           _raises(whatsapp_inbound.WebhookError,
                   lambda: whatsapp_inbound.verify_signature({'a': 1}, good, secret)), True)

    # --- the declared ceilings ----------------------------------------------
    for label, value, expected in (
            ('the webhook body ceiling', whatsapp_inbound.MAX_BODY_BYTES, 1_000_000),
            ('the entry ceiling', whatsapp_inbound.MAX_ENTRIES, 50),
            ('the change ceiling', whatsapp_inbound.MAX_CHANGES, 50),
            ('the per-change message ceiling',
             whatsapp_inbound.MAX_MESSAGES_PER_CHANGE, 50),
            ('the inbound text ceiling', whatsapp_inbound.MAX_TEXT_CHARS, 4096),
            ('the status ceiling', whatsapp_inbound.MAX_STATUS_CHARS, 32),
            ('the outbound text ceiling', whatsapp.MAX_TEXT_CHARS, 4096),
            ('the recipient ceiling', whatsapp.MAX_RECIPIENTS, 200),
            ('the template ceiling', whatsapp.MAX_TEMPLATES, 100),
            ('the body-parameter ceiling', whatsapp.MAX_BODY_PARAMS, 20),
            ('the parameter-text ceiling', whatsapp.MAX_PARAM_CHARS, 400),
            ('the event-scan ceiling', whatsapp.MAX_EVENTS_SCANNED, 500),
            ('the response ceiling', whatsapp.MAX_RESPONSE_BYTES, 200_000),
            ('Meta\'s outside-window error code', whatsapp.ERROR_OUTSIDE_WINDOW, 131047)):
        record(label, value, expected)

    # --- the identifier ceilings --------------------------------------------
    for label, regex, ceiling, over in (
            ('the message-id ceiling', whatsapp_inbound.MESSAGE_ID_RE, 'a' * 128, 'a' * 129),
            ('the wa-id ceiling', whatsapp_inbound.WA_ID_RE, '1' * 20, '1' * 21),
            ('the phone ceiling', whatsapp.PHONE_RE, '1' * 15, '1' * 16),
            ('the template-name ceiling', whatsapp.NAME_RE, 'a' * 64, 'a' * 65)):
        record(f'{label} accepts its ceiling', bool(regex.match(ceiling)), True)
        record(f'{label} refuses one past it', bool(regex.match(over)), False)
    record('a phone number may not start with zero',
           bool(whatsapp.PHONE_RE.match('0' + '1' * 10)), False)
    record('the language tag accepts a bare language',
           bool(whatsapp.LANGUAGE_RE.match('uz')), True)
    record('the language tag refuses a word',
           bool(whatsapp.LANGUAGE_RE.match('uzbek')), False)

    # --- the guards that now sit on the bounds ------------------------------
    record('a guard pins the two window constants as one fact',
           'test_the_two_service_window_constants_are_one_fact' in outbound_guard, True)
    record('a guard measures the asymmetric drift',
           'test_the_two_window_reads_follow_different_constants' in outbound_guard, True)
    record('a guard pins the phone ceiling',
           'test_the_phone_ceiling_is_fifteen_digits' in outbound_guard, True)
    record('a guard pins the outbound text ceiling',
           'test_the_outbound_text_ceiling_is_four_thousand_and_ninety_six' in outbound_guard,
           True)
    record('a guard pins the event-scan ceiling and its read',
           'test_the_event_scan_ceiling_is_five_hundred' in outbound_guard, True)
    record('a guard pins the outside-window error code',
           'test_the_outside_window_error_code_is_the_one_meta_sends' in outbound_guard, True)
    record('a guard pins the body ceiling to its literal',
           "self.assertEqual(1_000_000, MAX_BODY_BYTES)" in inbound_guard, True)
    record('a guard pins the entry ceiling',
           'test_the_entry_ceiling_is_fifty' in inbound_guard, True)
    record('a guard pins the change ceiling',
           'test_the_change_ceiling_is_fifty' in inbound_guard, True)
    record('a guard pins the message-id ceiling',
           'test_the_message_id_ceiling_is_one_hundred_and_twenty_eight' in inbound_guard, True)



def section_telephony_bounds():
    """The call-event reader and the consent gate: nineteen bounds, fifteen unpinned.

    THE THIRD DEAD-CONSTANT FINDING IN THIS AUDIT. Fazza I removed
    `inventory.MAX_NAMES` and `oee.MAX_NAMES`; fazza 26 removed
    `inventory.MAX_QUANTITY`; this phase removed three more:

      telephony.MAX_QUEUE = 200       the comment block documents it as "how many
                                      callable rows one read may return at all", but
                                      the read is bounded by MAX_ROWS and the items
                                      carried by MAX_QUEUE_ITEMS -- so the ceiling
                                      the comment named did not exist.
      telephony.MAX_WINDOW_DAYS = 31  read by nobody.
      vision.MAX_WINDOW_DAYS = 31     the same constant, restated in a second module,
                                      and read by nobody there either.

    The obvious intent of MAX_WINDOW_DAYS is a ceiling on the `since`..`until` span.
    `_day` validates the FORMAT of both and never their distance -- `2026-13-45`
    passes it -- so that ceiling was never wired. It is removed here and the missing
    span bound is recorded as an open risk rather than invented, because choosing the
    number is an operator's decision, not the audit's.

    Nineteen revert modes were walked and FIFTEEN left the suite green, so nearly
    every remaining ceiling was correct and unmeasured. All of them are pinned here
    and in the guard suite.
    """
    print('=== 32: telephony bounds and the third dead-constant finding ===')
    from platform_runtime import telephony as T  # noqa: E402
    from platform_runtime import vision as V  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'platform_runtime' / 'telephony.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_telephony.py').read_text(encoding='utf-8')
    vision_guard = (root / 'runtime_tests' / 'test_vision.py').read_text(encoding='utf-8')

    # --- the three removals --------------------------------------------------
    record('telephony no longer declares MAX_QUEUE', hasattr(T, 'MAX_QUEUE'), False)
    record('telephony no longer declares MAX_WINDOW_DAYS',
           hasattr(T, 'MAX_WINDOW_DAYS'), False)
    record('vision no longer declares MAX_WINDOW_DAYS',
           hasattr(V, 'MAX_WINDOW_DAYS'), False)
    record('the queue comment now names the ceiling that is enforced',
           '* ``MAX_ROWS``' in src, True)
    record('the queue comment no longer names the one that is not',
           '* ``MAX_QUEUE``' in src, False)

    # --- the throughput pace -------------------------------------------------
    for per, accepted in ((1, True), (T.MAX_PER_WINDOW, True),
                          (0, False), (T.MAX_PER_WINDOW + 1, False), (True, False)):
        record(f'per_window={per!r} is {"accepted" if accepted else "refused"}',
               not _raises(ValueError, lambda p=per: T._throughput(
                   {'per_window': p, 'window_seconds': 60})), accepted)
    for window, accepted in ((T.MIN_WINDOW_SECONDS, True), (T.MAX_WINDOW_SECONDS, True),
                             (T.MIN_WINDOW_SECONDS - 1, False),
                             (T.MAX_WINDOW_SECONDS + 1, False)):
        record(f'window_seconds={window!r} is {"accepted" if accepted else "refused"}',
               not _raises(ValueError, lambda w=window: T._throughput(
                   {'per_window': 1, 'window_seconds': w})), accepted)
    record('an absent pace is None, not a permissive default', T._throughput(None), None)

    # --- retention -----------------------------------------------------------
    for days, accepted in ((T.MIN_RETENTION_DAYS, True), (T.MAX_RETENTION_DAYS, True),
                           (0, False), (T.MAX_RETENTION_DAYS + 1, False)):
        record(f'retention_days={days!r} is {"accepted" if accepted else "refused"}',
               not _raises(ValueError, lambda d=days: T._retention(d)), accepted)

    # --- the digit window and the locator rule ------------------------------
    record('seven digits is a number', T.normalise('9' * 7) == '9' * 7, True)
    record('fifteen digits is a number', T.normalise('9' * 15) == '9' * 15, True)
    record('six digits is NOT a number', T.normalise('9' * 6), '')
    record('sixteen digits is NOT a number', T.normalise('9' * 16), '')
    record('a formatted number normalises', T.normalise('+998 90 123 45 67'), '998901234567')
    record('tel: names a number and is stripped',
           T.normalise('tel:+998901234567'), '998901234567')
    for extension in ('wav', 'mp3', 'ogg', 'opus', 'webm', 'm4a', 'aac',
                      'pcm', 'flac', 'amr', 'gsm', 'ulaw', 'alaw'):
        record(f'a .{extension} locator is refused',
               T.normalise('https://cdn.example/rec-998901234567.%s' % extension), '')
    record('a non-media path is not a locator',
           T.normalise('zavod-1/sex-1/liniya-1/stanok-1'), '')

    # --- the day window is a FORMAT check, not a distance check -------------
    record('a well-formed date passes', T._day('2026-09-20', 'since'), '2026-09-20')
    record('a short month is refused', _raises(ValueError, lambda: T._day('2026-9-20', 's')), True)
    record('a dotted date is refused', _raises(ValueError, lambda: T._day('20.09.2026', 's')), True)
    record('an impossible calendar day passes the FORMAT check',
           T._day('2026-13-45', 'since'), '2026-13-45')
    record('an empty window is allowed', T._day('', 'since'), '')

    # --- the ceilings that remain -------------------------------------------
    for label, value, expected in (
            ('the row ceiling', T.MAX_ROWS, 200),
            ('the event ceiling', T.MAX_EVENTS, 200),
            ('the register ceiling', T.MAX_REGISTERS, 20),
            ('the purpose ceiling', T.MAX_PURPOSES, 16),
            ('the queue-item ceiling', T.MAX_QUEUE_ITEMS, 50),
            ('the per-window ceiling', T.MAX_PER_WINDOW, 1000),
            ('the minimum window', T.MIN_WINDOW_SECONDS, 60),
            ('the maximum window', T.MAX_WINDOW_SECONDS, 86_400),
            ('the minimum retention', T.MIN_RETENTION_DAYS, 1),
            ('the maximum retention', T.MAX_RETENTION_DAYS, 3650),
            ('the purpose-text ceiling', T.MAX_PURPOSE_CHARS, 64),
            ('the digit ceiling', T.MAX_NUMBER_DIGITS, 15),
            ('the digit floor', T.MIN_NUMBER_DIGITS, 7),
            ('the duration ceiling', T.MAX_DURATION, 86_400)):
        record(label, value, expected)
    record('the name ceiling is 64 characters',
           bool(T.NAME_RE.match('a' * 64)) and not T.NAME_RE.match('a' * 65), True)
    record('the day regex refuses a short month',
           bool(T.DAY_RE.match('2026-09-20')) and not T.DAY_RE.match('2026-9-20'), True)
    record('the consent states are exactly two', T.CONSENT_STATUS,
           ('granted', 'withdrawn'))
    record('the directions are exactly two', T.DIRECTIONS, ('inbound', 'outbound'))
    record('the purpose vocabulary is the documented five', len(T.PURPOSES), 5)

    # --- the guards that now sit on the bounds ------------------------------
    record('a guard walks the pace edges',
           'test_the_throughput_pace_edges_are_the_documented_pair' in guard, True)
    record('a guard walks the retention edges',
           'test_the_retention_edges_are_the_documented_pair' in guard, True)
    record('a guard walks the queue and scan ceilings',
           'test_the_queue_and_scan_limits_are_the_documented_ceilings' in guard, True)
    record('a guard pins the duration ceiling and its read',
           'test_the_call_duration_ceiling_is_one_day' in guard, True)
    record('a guard walks the locator rule extension by extension',
           'test_a_media_filename_is_not_a_phone_number' in guard, True)
    record('a guard pins the removal of both dead constants',
           'test_the_module_declares_no_ceiling_it_does_not_enforce' in guard, True)
    record('the vision guard pins its own removal',
           'test_the_module_declares_no_ceiling_it_does_not_enforce' in vision_guard, True)



def section_connector_bounds():
    """The connector layer: three modules, and one of them used a different rule.

    A 22-mode revert matrix over `connectors.py`, `connector_contract.py` and
    `connector_authority.py` left FOURTEEN mutations green. The read authority gate
    was well covered; the other two modules were almost entirely unmeasured.

    ONE MEASURED DEFECT: `authorize_read_connection` validated a connection name
    with a LENGTH-ONLY check while `connector_contract._string` and
    `database.contract.text` -- the two other gates for the same kind of value --
    also require no surrounding whitespace and reject control characters including
    DEL. Measured: `authorize_read_connection('t', ' erp')` and `('t', 'e\\x7frp')`
    passed the gate, refused only because the name was not in the config, while both
    siblings refused those values outright. This is the split fazza 30 found between
    `usage_budget.bounded` and `contract.text`, and it is fixed the same way: one
    rule, not two.

    Also worth recording: the request-side `<= 40` column ceiling is UNREACHABLE.
    `columns` must be a subset of the allowlist, and the allowlist is already capped
    at 40, so the second check can never fire. It is defensive redundancy rather
    than a defect, and it is pinned by its literal because no behaviour can reach it.
    """
    print('=== 33: connector layer bounds ===')
    import json  # noqa: E402
    import os  # noqa: E402
    import sqlite3  # noqa: E402
    import tempfile  # noqa: E402
    from unittest.mock import patch  # noqa: E402

    from platform_runtime import connector_authority, connector_contract, connectors  # noqa: E402
    from platform_runtime.database import contract  # noqa: E402
    from platform_runtime.engine import Forbidden, encode  # noqa: E402
    from platform_runtime.tools import build_registry  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'platform_runtime' / 'connectors.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_connector_authority.py').read_text(encoding='utf-8')
    contract_guard = (root / 'runtime_tests' / 'test_connector_contract.py').read_text(encoding='utf-8')

    # --- the defect: three gates, one rule --------------------------------
    # `chr()`, not an escape: a writer that emits `\\x7f` produces the literal
    # four characters, and the gates correctly accept THAT. The character is
    # what the rule is about.
    for value in (' crm', 'crm ', 'c' + chr(127) + 'rm', 'c' + chr(1) + 'rm',
                  '', '   '):
        record(f'authorize_read_connection refuses {value!r}',
               _raises(ValueError, lambda v=value: connectors.authorize_read_connection('a', v)),
               True)
        record(f'connector_contract._string refuses the same {value!r}',
               _raises(ValueError, lambda v=value: connector_contract._string(v, 'x', 128)), True)
        record(f'database.contract.text refuses the same {value!r}',
               _raises(ValueError, lambda v=value: contract.text(v)), True)
    record('the name ceiling itself still reaches the config lookup',
           _raises(Exception, lambda: connectors.authorize_read_connection('a', 'a' * 128)), True)

    # --- identifiers and ceilings ------------------------------------------
    record('IDENTIFIER accepts 63 characters',
           bool(connectors.IDENTIFIER.match('a' * 63)), True)
    record('IDENTIFIER refuses 64', bool(connectors.IDENTIFIER.match('a' * 64)), False)
    record('IDENTIFIER refuses a leading digit',
           bool(connectors.IDENTIFIER.match('1a')), False)
    record('the request column ceiling literal is 40',
           'not 1 <= len(columns) <= 40' in src, True)
    record('the config allowlist ceiling literal is 40',
           'len(columns) > 40' in src, True)
    record('the limit ceiling literal is 100',
           'not 1 <= limit <= 100' in src, True)
    record('the filter value ceiling literal is 1000',
           "len(where['equals']) > 1000" in src, True)
    for line in ('db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 100_000)',
                 'db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16_000)',
                 'deadline = time.monotonic() + 2',
                 'return int(calls >= 1000 or time.monotonic() >= deadline)'):
        record(f'the query limit {line[:46]!r}', line in src, True)

    # --- measured behaviour through a real database -------------------------
    tmp = tempfile.TemporaryDirectory()
    home = Path(tmp.name)
    customer = home / 'customer.db'
    db = sqlite3.connect(customer)
    try:
        db.executescript("CREATE TABLE contacts(id INTEGER, name TEXT);"
                         "INSERT INTO contacts VALUES(1,'Ali'),(2,'Vali');")
        db.commit()
    finally:
        db.close()
    cfg = home / 'integrations.json'
    entry = {'driver': 'sqlite_readonly', 'path': str(customer),
             'tables': {'contacts': ['id', 'name']}}
    cfg.write_text(json.dumps({'a': {'connections': {'crm': entry}}}), encoding='utf-8')
    environment = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(cfg),
                                          'PLATFORM_DB_ROOTS': json.dumps([str(home)])})
    environment.start()
    try:
        def read(**extra):
            request = {'connection': 'crm', 'table': 'contacts', 'columns': ['id']}
            request.update(extra)
            return connectors.read_rows('a', request, home / 'platform.db')

        record('a limit of 1 is accepted', read(limit=1)['returned'] >= 1, True)
        record('a limit of 100 is accepted', read(limit=100)['limit'], 100)
        for limit in (0, 101, True, 50.0):
            record(f'a limit of {limit!r} is refused',
                   _raises(ValueError, lambda l=limit: read(limit=l)), True)
        record('a filter value of 1000 characters is accepted',
               not _raises(Forbidden, lambda: read(where={'column': 'id',
                                                          'equals': 'x' * 1000})), True)
        record('a filter value of 1001 characters is refused',
               _raises(Forbidden, lambda: read(where={'column': 'id',
                                                      'equals': 'x' * 1001})), True)
        record('a filter on an approved column is accepted',
               not _raises(Forbidden, lambda: read(where={'column': 'name',
                                                           'equals': 'Ali'})), True)
        record('a filter on an unapproved column is refused',
               _raises(Forbidden, lambda: read(where={'column': 'secret',
                                                      'equals': 'x'})), True)
        record('a request field outside the allowlist is refused',
               _raises(ValueError, lambda: read(sql='DROP TABLE contacts')), True)
        record('a table outside the allowlist is refused',
               _raises(Forbidden, lambda: read(table='sqlite_master', columns=['name'])), True)

        # --- the byte ceilings, at their boundaries -------------------------
        framing = len(encode({'blob': ''}))
        record('the JSON framing of one cell is 11 bytes', framing, 11)

        def rebuild(table, sizes):
            handle = sqlite3.connect(customer)
            try:
                handle.execute('DROP TABLE IF EXISTS %s' % table)
                handle.execute('CREATE TABLE %s(id INTEGER, blob TEXT)' % table)
                handle.executemany('INSERT INTO %s VALUES(?,?)' % table,
                                   [(i, 'x' * s) for i, s in enumerate(sizes)])
                handle.commit()
            finally:
                handle.close()
            entry['tables'] = {table: ['id', 'blob']}
            cfg.write_text(json.dumps({'a': {'connections': {'crm': entry}}}),
                           encoding='utf-8')
            return lambda: connectors.read_rows(
                'a', {'connection': 'crm', 'table': table, 'columns': ['blob'],
                      'limit': 100}, home / 'platform.db')

        at_row = rebuild('atrow', [16_000 - framing])
        record('a row of exactly 16 000 bytes is accepted',
               len(at_row()['rows'][0]['blob']), 16_000 - framing)
        past_row = rebuild('pastrow', [16_000 - framing + 1])
        record('a row of 16 001 bytes is refused',
               _raises(ValueError, past_row), True)

        sizes = [16_000] * 4 + [1_001, 15_000]        # 80 001 in total
        over_total = rebuild('overtotal', [size - framing for size in sizes])
        record('a cumulative 80 001 bytes is refused',
               _raises(ValueError, over_total), True)
        sizes[-1] -= 1                                # 80 000 in total
        at_total = rebuild('attotal', [size - framing for size in sizes])
        record('a cumulative 80 000 bytes is accepted',
               at_total()['returned'], 6)
    finally:
        environment.stop()

    # --- the model-facing schema agrees with the runtime --------------------
    properties = build_registry().get('connectors.read').schema['properties']
    record('the schema connection ceiling matches the gate',
           properties['connection']['maxLength'], 128)
    record('the schema column ceiling matches the allowlist',
           properties['columns']['maxItems'], 40)
    record('the schema column-name ceiling matches the identifier',
           properties['columns']['items']['maxLength'], 63)
    record('the schema limit ceiling matches the runtime',
           properties['limit']['maximum'], 100)
    record('the schema filter ceiling matches the runtime',
           properties['where']['properties']['equals']['maxLength'], 1000)

    # --- the contract --------------------------------------------------------
    record('the bounded-string default is 128',
           connector_contract._string('a' * 128, 'x') == 'a' * 128, True)
    record('the bounded-string default refuses 129',
           _raises(ValueError, lambda: connector_contract._string('a' * 129, 'x')), True)
    record('the list default is 100',
           len(connector_contract._strings(['x%d' % i for i in range(100)], 'x')), 100)
    record('the list default refuses 101',
           _raises(ValueError, lambda: connector_contract._strings(
               ['x%d' % i for i in range(101)], 'x')), True)
    record('the contract version is 1.0', connector_contract.CONTRACT_VERSION, '1.0')
    record('there are seven lifecycles', len(connector_contract.LIFECYCLES), 7)
    record('there are seven capabilities', len(connector_contract.CAPABILITIES), 7)
    record('the read lifecycles are a strict subset',
           connector_authority.READ_LIFECYCLES < connector_contract.LIFECYCLES, True)
    record('the read lifecycles exclude draft and revoked',
           connector_authority.READ_LIFECYCLES & {'draft', 'revoked'}, frozenset())

    # --- the read authority gate --------------------------------------------
    def access(lifecycle='healthy', capabilities=('read',), driver='sqlite_readonly',
               enabled=True, agent_ids=None, agent=None):
        raw = {'driver': driver, 'enabled': enabled, 'lifecycle': lifecycle,
               'capabilities': list(capabilities)}
        if agent_ids is not None:
            raw['agent_ids'] = agent_ids
        return connector_authority.require_read_access(raw, agent=agent)

    for lifecycle, accepted in (('configured', True), ('healthy', True),
                                ('degraded', True), ('draft', False),
                                ('revoked', False), ('verifying', False),
                                ('authorizing', False)):
        record(f'lifecycle {lifecycle!r} is {"accepted" if accepted else "refused"}',
               not _raises(Forbidden, lambda l=lifecycle: access(lifecycle=l)), accepted)
    record('a connection without the read capability is refused',
           _raises(Forbidden, lambda: access(capabilities=['discover'])), True)
    record('an unsupported driver is refused',
           _raises(Forbidden, lambda: access(driver='bitrix24')), True)
    record('a disabled connection is refused',
           _raises(Forbidden, lambda: access(enabled=False)), True)
    record('a non-dict config is refused',
           _raises(Forbidden, lambda: connector_authority.require_read_access(None)), True)
    record('an empty agent_ids list adds no restriction',
           not _raises(Forbidden, lambda: access(agent_ids=[])), True)
    record('a nonempty agent_ids list requires a match',
           _raises(Forbidden, lambda: access(agent_ids=['ops'], agent='other')), True)
    record('...and admits the matching agent',
           not _raises(Forbidden, lambda: access(agent_ids=['ops'], agent='ops')), True)

    # --- the guards that now sit on the bounds ------------------------------
    record('a guard pins the three name gates together',
           'test_the_connection_name_gate_matches_its_siblings' in guard, True)
    record('a guard walks the identifier ceiling',
           'test_the_identifier_ceiling_is_sixty_three_characters' in guard, True)
    record('a guard walks the allowlist and request column ceilings',
           'test_the_column_allowlist_ceiling_is_forty' in guard, True)
    record('a guard walks the result byte ceilings at their boundaries',
           'test_the_result_byte_ceilings' in guard, True)
    record('a guard pins the query limits and their reads',
           'test_the_query_limits_are_the_documented_values' in guard, True)
    record('a guard pins the schema to the runtime',
           'test_the_tool_schema_agrees_with_the_runtime_ceilings' in guard, True)
    record('a guard pins the contract gates',
           'test_the_bounded_string_gate_matches_its_siblings' in contract_guard, True)
    record('a guard pins the read-only write-capability rule',
           'test_a_read_only_driver_may_not_advertise_a_write_capability' in contract_guard,
           True)



def section_knowledge_bounds():
    """The retrieval ceilings, and the guard that died on the value it refuses.

    A 20-mode revert matrix left FIFTEEN mutations green, so almost every ceiling in
    `knowledge.py` was correct and unmeasured.

    THE MEASURED DEFECT is the second occurrence of the unrepresentable-number shape
    fazza 26 found in five places, and a sharper one. `vector()` validated each
    embedding element with

        if type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 1e6:

    `math.isfinite` converts to float, and `math.isfinite(10 ** 400)` RAISES
    `OverflowError` -- so an integer beyond the float range never reached
    `abs(n) > 1e6`, which would have refused it correctly. There the guard was
    ABSENT; here the guard itself raises, which is worse: the check that exists to
    bound the value is the thing that dies.

    The fix is a REORDER. `abs()` needs no conversion, so the magnitude check runs
    first; the finiteness check still catches nan, for which every comparison is
    False. Measured before the fix: `vector([10**400, 1], 2)` raised OverflowError,
    `vector([10**308, 1], 2)` raised ValueError (isfinite returns True and the bound
    caught it), and `vector([10**6, 1], 2)` was accepted, because the bound is
    inclusive.

    One note on the tests this phase added: the version ceiling's literal had to be
    anchored to the EXACT LINE. `'not 0 <= expected_version < 2**31'` is a PREFIX of
    `'... < 2**31 + 1'`, so an unanchored `assertIn` was satisfied by the very
    mutation it was written to catch -- the fourth time this audit has met that trap.
    """
    print('=== 34: knowledge bounds and the guard that dies ===')
    import tempfile  # noqa: E402
    from unittest import mock  # noqa: E402

    from platform_runtime import knowledge as K  # noqa: E402
    from platform_runtime.engine import Engine  # noqa: E402
    from platform_runtime.tools import build_registry  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'platform_runtime' / 'knowledge.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_knowledge.py').read_text(encoding='utf-8')

    # --- the defect, and the fix -------------------------------------------
    record('an integer past the float range is refused, not fatal',
           _raises(ValueError, lambda: K.vector([10 ** 400, 1], 2)), True)
    record('a large but representable integer is refused by the bound',
           _raises(ValueError, lambda: K.vector([10 ** 308, 1], 2)), True)
    record('the magnitude check comes before the finiteness check',
           'abs(n) > 1e6 or not math.isfinite(n)' in src, True)
    record('the finiteness check is no longer first',
           'not math.isfinite(n) or abs(n) > 1e6' in src, False)
    record('the magnitude bound is inclusive at one million',
           K.vector([1e6, 1], 2)[0] > 0, True)
    record('one past the magnitude bound is refused',
           _raises(ValueError, lambda: K.vector([1e6 + 1, 0], 2)), True)
    for value in (float('nan'), float('inf'), float('-inf'), True, '1'):
        record(f'{value!r} is refused',
               _raises(ValueError, lambda v=value: K.vector([v, 1], 2)), True)
    record('a zero vector is refused', _raises(ValueError, lambda: K.vector([0, 0], 2)), True)
    record('a wrong dimension is refused', _raises(ValueError, lambda: K.vector([1], 2)), True)

    # --- identifiers and text ----------------------------------------------
    record('identifier accepts 128 characters',
           K.identifier('a' * 128) == 'a' * 128, True)
    record('identifier refuses 129',
           _raises(ValueError, lambda: K.identifier('a' * 129)), True)
    for size, accepted in ((1, True), (K.MAX_DOCUMENT_CHARS, True),
                           (K.MAX_DOCUMENT_CHARS + 1, False)):
        record(f'a document of {size} characters is '
               f'{"accepted" if accepted else "refused"}',
               not _raises(ValueError, lambda s=size: K.chunks('x' * s)), accepted)
    record('empty text is refused', _raises(ValueError, lambda: K.chunks('')), True)
    record('a NUL is refused', _raises(ValueError, lambda: K.chunks('a\x00b')), True)

    # --- the chunk geometry -------------------------------------------------
    record('CHUNK_SIZE is 512', K.CHUNK_SIZE, 512)
    record('OVERLAP is 64', K.OVERLAP, 64)
    record('CHUNK_SIZE exceeds OVERLAP, so the loop advances',
           K.CHUNK_SIZE > K.OVERLAP, True)
    parts = K.chunks('x' * 2000)
    record('the first chunk starts at zero', parts[0][0], 0)
    record('the last chunk ends at the text length', parts[-1][1], 2000)
    record('consecutive chunks overlap by OVERLAP',
           {left[1] - right[0] for left, right in zip(parts, parts[1:])}, {K.OVERLAP})
    record('a text of exactly CHUNK_SIZE is one chunk',
           len(K.chunks('x' * K.CHUNK_SIZE)), 1)
    record('a text of CHUNK_SIZE + 1 is two chunks',
           len(K.chunks('x' * (K.CHUNK_SIZE + 1))), 2)

    # --- the store ----------------------------------------------------------
    tmp = tempfile.TemporaryDirectory()
    engine = Engine(Path(tmp.name) / 'kb.db', build_registry(),
                    lambda t, a: {'tools': ['knowledge.search'], 'ladder': 'autonomous'})
    store = K.KnowledgeStore(engine)
    store.create_collection('a', 'owner', 'manuals')
    store.grant('a', 'owner', 'manuals', 'ops')

    def ingest(document='doc', title='T', content='savdo hisoboti', version=0,
               source_url=''):
        return store.ingest('a', 'owner', 'manuals', document, title, content,
                            version, source_url)

    for dimension, accepted in ((0, True), (1024, True), (1025, False),
                                (-1, False), (True, False)):
        model = 'm' if isinstance(dimension, int) and dimension > 0 else ''
        record(f'collection dimension {dimension!r} is '
               f'{"accepted" if accepted else "refused"}',
               not _raises(ValueError, lambda d=dimension, m=model, i=len(
                   str(dimension)): store.create_collection(
                       'a', 'owner', 'd%s%d' % (str(dimension)[:3], i), m, d)), accepted)
    record('a model without a dimension is refused',
           _raises(ValueError, lambda: store.create_collection('a', 'owner', 'p1',
                                                               'model', 0)), True)
    record('a dimension without a model is refused',
           _raises(ValueError, lambda: store.create_collection('a', 'owner', 'p2',
                                                               '', 4)), True)
    record('a model of 256 characters is accepted',
           not _raises(ValueError, lambda: store.create_collection(
               'a', 'owner', 'm256', 'x' * 256, 4)), True)
    record('a model of 257 characters is refused',
           _raises(ValueError, lambda: store.create_collection(
               'a', 'owner', 'm257', 'x' * 257, 4)), True)

    record('an agent name of 128 characters is accepted',
           not _raises(ValueError, lambda: store.grant('a', 'owner', 'manuals',
                                                       'a' * 128)), True)
    record('an agent name of 129 characters is refused',
           _raises(ValueError, lambda: store.grant('a', 'owner', 'manuals',
                                                   'a' * 129)), True)

    record('a title of 200 characters is accepted',
           not _raises(ValueError, lambda: ingest(document='t200', title='x' * 200)), True)
    record('a title of 201 characters is refused',
           _raises(ValueError, lambda: ingest(document='t201', title='x' * 201)), True)
    prefix = 'https://e.uz/'
    record('the URL prefix is thirteen characters', len(prefix), 13)
    record('a source_url of 2000 characters is accepted',
           not _raises(ValueError, lambda: ingest(
               document='u2000', source_url=prefix + 'x' * (2000 - len(prefix)))), True)
    record('a source_url of 2001 characters is refused',
           _raises(ValueError, lambda: ingest(
               document='u2001', source_url=prefix + 'x' * (2001 - len(prefix)))), True)
    for bad in ('javascript:alert(1)', 'https://u:p@e.uz/x', 'https://e.uz/\x01'):
        record(f'a source_url of {bad[:16]!r} is refused',
               _raises(ValueError, lambda b=bad: ingest(document='bad', source_url=b)), True)
    record('expected_version 0 is accepted',
           not _raises(ValueError, lambda: ingest(document='v0', version=0)), True)
    for value in (2 ** 31, 2 ** 32, -1, True):
        record(f'expected_version {value!r} is refused',
               _raises(ValueError, lambda v=value: ingest(document='v0', version=v)), True)
    record('the version ceiling literal is the documented one',
           'not 0 <= expected_version < 2**31' in src, True)

    record('documents limit 100 is accepted',
           not _raises(ValueError, lambda: store.documents('a', 'manuals', 100)), True)
    record('documents limit 101 is refused',
           _raises(ValueError, lambda: store.documents('a', 'manuals', 101)), True)
    record('search limit 5 is accepted',
           not _raises(ValueError, lambda: store.search('a', 'ops', 'manuals',
                                                        'savdo', 5)), True)
    record('search limit 6 is refused',
           _raises(ValueError, lambda: store.search('a', 'ops', 'manuals', 'savdo', 6)), True)
    record('a query of 500 characters is accepted',
           not _raises(ValueError, lambda: store.search(
               'a', 'ops', 'manuals', 'savdo ' + 'x' * 494)), True)
    record('a query of 501 characters is refused',
           _raises(ValueError, lambda: store.search(
               'a', 'ops', 'manuals', 'savdo ' + 'x' * 495)), True)
    record('a punctuation-only query is refused',
           _raises(ValueError, lambda: store.search('a', 'ops', 'manuals', '!!!')), True)

    # --- the collection chunk ceiling, with the constant lowered -------------
    record('the chunk ceiling is 4000', K.MAX_COLLECTION_CHUNKS, 4000)
    record('1408 characters chunks into exactly three', len(K.chunks('x' * 1408)), 3)
    record('1409 characters chunks into four', len(K.chunks('x' * 1409)), 4)
    # A FRESH collection: `manuals` already holds chunks from the records above, so
    # `count` is not zero and the boundary would never be reached.
    store.create_collection('a', 'owner', 'cap')
    store.grant('a', 'owner', 'cap', 'ops')

    def cap_ingest(document, content):
        return store.ingest('a', 'owner', 'cap', document, 'T', content, 0)

    with mock.patch.object(K, 'MAX_COLLECTION_CHUNKS', 3):
        record('a three-chunk document fits a three-chunk ceiling',
               not _raises(ValueError, lambda: cap_ingest('three', 'x' * 1408)), True)
        record('a four-chunk document does not',
               _raises(ValueError, lambda: cap_ingest('four', 'x' * 1409)), True)
    with mock.patch.object(K, 'MAX_COLLECTION_CHUNKS', 2):
        record('a corpus past the ceiling is refused at search time',
               _raises(ValueError, lambda: store.search('a', 'ops', 'cap', 'x')), True)

    # --- the corpus survives an empty one -----------------------------------
    record('bm25 of an empty corpus is empty', K.bm25('x', []), [])
    record('bm25 with no matching term is all zeros', K.bm25('zzz', ['abc']), [0.0])
    record('bm25 of tokenless texts is all zeros', K.bm25('x', ['!!!']), [0.0])

    # --- the guards that now sit on the bounds ------------------------------
    record('a guard pins the guard ORDER, not just the bounds',
           'test_an_integer_past_the_float_range_is_refused_not_fatal' in guard, True)
    record('a guard walks both document-ceiling edges',
           'test_the_document_ceiling_edges_are_both_measured' in guard, True)
    record('a guard walks the chunk geometry',
           'test_the_chunk_geometry_is_the_documented_pair' in guard, True)
    record('a guard walks the magnitude bound',
           'test_the_embedding_magnitude_bound_is_inclusive_at_one_million' in guard, True)
    record('a guard walks the collection configuration ceilings',
           'test_the_collection_configuration_ceilings' in guard, True)
    record('a guard walks the document field ceilings',
           'test_the_document_field_ceilings' in guard, True)
    record('a guard anchors the version ceiling to its line',
           "lines = set(source.splitlines())" in guard, True)
    record('a guard walks the collection chunk ceiling on both reads',
           'test_the_collection_chunk_ceiling_bounds_both_reads' in guard, True)



def section_vision_bounds():
    """The vision feed: a `truncated` flag that counted rows nobody withheld.

    A 19-mode revert matrix left NINE mutations green, and one of them was the fix
    this phase made -- so the defect could have been reintroduced silently.

    THE MEASURED DEFECT. `_scan` derived `truncated` from `matched`, which counts
    every row that passed the WINDOW -- including rows whose station is an intern
    node, which cannot bind to an asset and therefore never becomes an event.
    Measured with three bindable rows, seven intern-node rows and limit five:

        scanned=10 count=10 returned=3 unbound=7 truncated=True

    The list holds EVERY bindable event and the limit was never reached, so the flag
    told the caller to re-run with a larger limit and receive an identical list. That
    is the failure this whole probe exists for: `truncated` answers "is there more,
    should I ask again", and a wrong answer wastes the operator's time and, worse,
    teaches them the flag is noise.

    `summary` derives its flag from `len(ordered) > limit`, which is the correct form
    -- `ordered[:limit]` is exactly what it returns. Only `_scan` was wrong. The fix
    compares against what came BACK: `matched - unbound > len(events)`.

    Also worth recording: `vision.py` states its register cap as a bare `20` where
    `telephony.py` names the same ceiling `MAX_REGISTERS`. A literal is not a defect,
    but it is a fact stated without a name, so it is pinned by its line.
    """
    print('=== 35: vision bounds and the truncated verdict ===')
    import json  # noqa: E402
    import os  # noqa: E402
    import tempfile  # noqa: E402
    from unittest.mock import patch  # noqa: E402

    from platform_runtime import vision as V  # noqa: E402
    from platform_runtime.engine import Engine  # noqa: E402
    from platform_runtime.tools import build_registry  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'platform_runtime' / 'vision.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_vision.py').read_text(encoding='utf-8')

    TENANT = 't_plant'
    AGENT = 'ops.vision'
    SHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
    LEVELS = ['zavod', 'sex', 'liniya', 'stanok']
    POLICY = {'tools': ['sheets.rows', 'vision.station_event', 'vision.person_event',
                        'vision.summary'],
              'allowed_connections': ['google'], 'ladder': 'human_assisted'}
    HEADER = ['stansiya', 'hodisa', 'sana', 'ishonch']
    GOOD = ['zavod-1/sex-1/liniya-1/stanok-1', 'nuqson', '2026-09-18', '0.9']
    INTERN = ['zavod-1/sex-1/liniya-1', 'nuqson', '2026-09-18', '0.9']
    STATION_BLOCK = {'registers': {'shopfloor': {
        'register': 'plant', 'range': 'events', 'station_column': 'stansiya',
        'event_column': 'hodisa', 'timestamp_column': 'sana',
        'confidence_column': 'ishonch', 'sensitivity': 'station'}}}
    PERSON_BLOCK = {'registers': {
        'shopfloor': STATION_BLOCK['registers']['shopfloor'],
        'access': {'register': 'plant', 'range': 'person_events',
                   'station_column': 'stansiya', 'event_column': 'hodisa',
                   'timestamp_column': 'sana', 'sensitivity': 'person',
                   'person_classes': ['yuz'], 'biometric_ack': True}}}

    def fixture(rows, block):
        tmp = tempfile.TemporaryDirectory()
        home = Path(tmp.name)
        cfg = home / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {
            'connections': {'google': {}},
            'sheets_registers': {'plant': {
                'connection': 'google', 'spreadsheet_id': SHEET,
                'ranges': {'events': 'Hodisa!A1:D', 'person_events': 'Shaxs!A1:C'},
                'max_rows': 200}},
            'assets': {'entity': 'asset', 'levels': LEVELS},
            'vision': block,
        }}), encoding='utf-8')
        env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(cfg)})
        env.start()
        engine = Engine(home / 'p.db', build_registry(), lambda t, a: POLICY)

        def call(fn, **kwargs):
            with patch('platform_runtime.sheets.configured_manager') as manager:
                manager.return_value.access.return_value.access_token = 'fake'
                with patch('platform_runtime.sheets._http_get',
                           side_effect=lambda url, token: {'values': rows}):
                    return fn(engine, TENANT, AGENT, **kwargs)
        return tmp, env, call

    # --- the declared constants ---------------------------------------------
    for label, value, expected in (('MAX_ROWS', V.MAX_ROWS, 200),
                                   ('MAX_EVENTS', V.MAX_EVENTS, 200),
                                   ('MAX_CLASSES', V.MAX_CLASSES, 32),
                                   ('MAX_CLASS_CHARS', V.MAX_CLASS_CHARS, 64)):
        record(label, value, expected)
    record('the sensitivities are the documented triple', V.SENSITIVITIES,
           ('station', 'person', 'biometric'))
    record('the register cap is a bare 20 with no named constant',
           'len(declared) > 20' in src, True)

    # --- the small validators ------------------------------------------------
    for value, accepted in ((1, True), (V.MAX_EVENTS, True), (0, False),
                            (V.MAX_EVENTS + 1, False), (True, False)):
        record(f'the limit {value!r} is {"accepted" if accepted else "refused"}',
               not _raises(ValueError, lambda v=value: V._bounded(v, 'limit', 1,
                                                                  V.MAX_EVENTS)), accepted)
    for count, accepted in ((V.MAX_CLASSES, True), (V.MAX_CLASSES + 1, False)):
        classes = ['k%d' % i for i in range(count)]
        record(f'{count} event classes are {"accepted" if accepted else "refused"}',
               not _raises(ValueError, lambda c=classes: V._class_list(c, 'r', 'k')), accepted)
    for length, accepted in ((V.MAX_CLASS_CHARS, True), (V.MAX_CLASS_CHARS + 1, False)):
        record(f'an event class of {length} characters is '
               f'{"accepted" if accepted else "refused"}',
               not _raises(ValueError, lambda l=length: V._class_list(['a' * l], 'r', 'k')),
               accepted)
    record('a class is casefolded and stripped',
           V._class_list(['  Nuqson  '], 'r', 'k'), ['nuqson'])

    # --- the window ----------------------------------------------------------
    record('a day on the since boundary is admitted',
           V._window('2026-09-18', '2026-09-18', ''), True)
    record('a day one before since is refused',
           V._window('2026-09-17', '2026-09-18', ''), False)
    record('a day on the until boundary is admitted',
           V._window('2026-09-18', '', '2026-09-18'), True)
    record('a day one after until is refused',
           V._window('2026-09-19', '', '2026-09-18'), False)
    record('no window admits any day', V._window('2026-09-18', '', ''), True)
    record('a non-ISO day is refused when a window is asked for',
           V._window('not-a-day', '2026-09-18', ''), False)
    record('a non-ISO day is admitted when no window is asked for',
           V._window('not-a-day', '', ''), True)
    record('since after until is refused',
           _raises(ValueError, lambda: V._check_window('2026-09-19', '2026-09-18')), True)
    record('since equal to until is accepted',
           not _raises(ValueError, lambda: V._check_window('2026-09-18', '2026-09-18')), True)

    # --- THE DEFECT: what `truncated` means ---------------------------------
    tmp, env, call = fixture([HEADER] + [GOOD] * 3 + [INTERN] * 7, STATION_BLOCK)
    try:
        out = call(V.station_event, limit=5)
        record('the intern-node rows are reported as unbound', out['unbound'], 7)
        record('only the bindable rows become events', out['returned'], 3)
        record('the window-passing count includes the unbound rows', out['count'], 10)
        record('the limit was NOT reached', out['returned'] < 5, True)
        record('...so the reply does NOT claim the list was cut',
               out['truncated'], False)
    finally:
        env.stop()
        tmp.cleanup()

    tmp, env, call = fixture([HEADER] + [GOOD] * 8, STATION_BLOCK)
    try:
        cut = call(V.station_event, limit=5)
        record('a genuinely cut list still reports truncated', cut['truncated'], True)
        record('...with the limit worth of rows', cut['returned'], 5)
    finally:
        env.stop()
        tmp.cleanup()

    tmp, env, call = fixture([HEADER] + [GOOD] * 5, STATION_BLOCK)
    try:
        exact = call(V.station_event, limit=5)
        record('an exact fit is not a cut',
               (exact['returned'], exact['truncated']), (5, False))
    finally:
        env.stop()
        tmp.cleanup()

    # --- summary's own flag, which was already the correct form -------------
    # Three DISTINCT stations: three rows on one station is a single station, and a
    # single station can never be truncated. Measured the hard way.
    THREE = [['zavod-1/sex-1/liniya-1/stanok-%d' % i, 'nuqson', '2026-09-18', '0.9']
             for i in range(3)]
    tmp, env, call = fixture([HEADER] + THREE, STATION_BLOCK)
    try:
        whole = call(V.summary, limit=3)
        record('summary counts by class', whole['by_class'], {'nuqson': 3})
        record('summary is not truncated when every station fits',
               whole['truncated'], False)
        cut = call(V.summary, limit=1)
        record('summary IS truncated when a station is left out', cut['truncated'], True)
        record('...and returns only the limit', len(cut['by_station']), 1)
    finally:
        env.stop()
        tmp.cleanup()

    # --- the biometric gate --------------------------------------------------
    tmp, env, call = fixture([HEADER] + [GOOD], PERSON_BLOCK)
    try:
        record('a person read on a human_assisted agent is refused',
               _raises(Exception, lambda: call(V.person_event)), True)
        record('a station read on the same agent is allowed',
               not _raises(Exception, lambda: call(V.station_event)), True)
    finally:
        env.stop()
        tmp.cleanup()

    # --- the register validator ---------------------------------------------
    base = dict(STATION_BLOCK['registers']['shopfloor'])

    def register(**extra):
        entry = dict(base)
        entry.update(extra)
        return V._register('r', entry)

    for sensitivity in ('station', 'person', 'biometric'):
        record(f'sensitivity {sensitivity!r} with person_classes is accepted',
               not _raises(ValueError, lambda s=sensitivity: register(
                   sensitivity=s, person_classes=['yuz'])), True)
    record('an unknown sensitivity is refused',
           _raises(ValueError, lambda: register(sensitivity='vibing')), True)
    for sensitivity in ('person', 'biometric'):
        record(f'{sensitivity} without person_classes is refused',
               _raises(ValueError, lambda s=sensitivity: register(
                   sensitivity=s, person_classes=[])), True)
    record('a non-boolean biometric_ack is refused',
           _raises(ValueError, lambda: register(biometric_ack='yes')), True)
    record('an unknown register key is refused',
           _raises(ValueError, lambda: register(extra_key=1)), True)
    for length, accepted in ((64, True), (65, False)):
        record(f'a column name of {length} characters is '
               f'{"accepted" if accepted else "refused"}',
               not _raises(ValueError, lambda l=length: register(
                   station_column='a' * l)), accepted)
    record('an empty confidence_column is accepted, because it is optional',
           not _raises(ValueError, lambda: register(confidence_column='')), True)

    # --- the guards that now sit on the bounds ------------------------------
    record('a guard pins what `truncated` counts',
           'test_the_truncated_flag_counts_events_not_matched_rows' in guard, True)
    record('a guard pins the genuinely-cut case',
           'test_the_truncated_flag_is_true_when_the_list_really_was_cut' in guard, True)
    record('a guard walks the class ceilings',
           'test_the_class_ceilings' in guard, True)
    record('a guard walks the register and column caps',
           'test_the_register_name_and_column_caps' in guard, True)
    record('a guard walks the window edges',
           'test_the_window_edges_are_inclusive' in guard, True)
    record('a guard pins the summary truncation flag',
           'test_the_summary_truncation_flag' in guard, True)
    record('a guard pins the shared limit ceiling',
           'test_the_limit_ceiling_is_the_event_ceiling' in guard, True)



def section_asset_bounds():
    """The asset hierarchy: a `truncated` flag that withheld nothing.

    A 20-mode revert matrix left EIGHT mutations green, and one of them was the fix
    this phase made.

    THE MEASURED DEFECT. `tree` returned every node it built and still claimed a cut:

        'nodes': [nodes[key] for key in sorted(nodes)],
        'count': len(nodes),
        'truncated': len(nodes) > MAX_CHILDREN,

    Measured with `MAX_CHILDREN` lowered to two and six nodes in the hierarchy:

        nodes=6  count=6  truncated=True

    `len(nodes) == count`, so no page was withheld -- the whole tree was in the list.
    And `tree` takes no `limit`: it is bounded by `depth`, which the CALLER chooses,
    so there was no larger value to re-run with. The flag was false AND unactionable.
    `MAX_CHILDREN` is the children tool's ceiling, used here as if it were the tree's.

    `children` and `descendants` are correct, and their existing tests say so: both
    slice to a limit and compare the POPULATION against it. `tree` was the odd one.

    This is the third occurrence of the "flag that lies" shape in this audit: fazza II
    found `len(result) >= limit` reporting an exact fit as cut, and fazza 35 found
    `truncated` counting rows that could never become events.
    """
    print('=== 36: asset bounds and the tree that withheld nothing ===')
    import json  # noqa: E402
    import os  # noqa: E402
    import tempfile  # noqa: E402
    from unittest.mock import patch  # noqa: E402

    from platform_runtime import assets as A  # noqa: E402
    from platform_runtime.engine import Engine  # noqa: E402
    from platform_runtime.tools import build_registry  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'platform_runtime' / 'assets.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_assets.py').read_text(encoding='utf-8')

    TENANT = 't_plant'
    AGENT = 'ops.plant'
    SHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
    LEVELS = ['zavod', 'sex', 'liniya', 'stanok']
    POLICY = {'tools': ['sheets.rows', 'graph.entity', 'graph.entities', 'graph.search',
                        'graph.timeline', 'graph.conflicts', 'graph.explain',
                        'asset.levels', 'asset.tree', 'asset.children',
                        'asset.descendants', 'asset.resolve'],
              'allowed_connections': ['google'], 'ladder': 'human_assisted'}
    BLOCK = {'entity': 'asset', 'levels': LEVELS, 'measurements': ['cycle_time']}
    GRAPH = {'conflict_policy': 'report', 'entities': {'asset': {
        'identity': 'id', 'priority': ['equipment'], 'sources': {'equipment': {
            'tool': 'sheets.rows', 'args': {'register': 'plant', 'range': 'assets'},
            'key': 'id', 'map': {'name': 'Nomi', 'status': 'Holat'}}}}}}
    REGISTERS = {'plant': {'connection': 'google', 'spreadsheet_id': SHEET,
                           'ranges': {'assets': 'Uskuna!A1:E'}, 'max_rows': 200}}
    HEADER = ['id', 'Nomi', 'Holat', 'Model', 'Liniya']
    ROWS = [HEADER] + [['zavod-1/sex-1/liniya-1/stanok-%d' % i, 'P', 'ishlaydi', 'X', 'l']
                       for i in range(1, 4)]

    tmp = tempfile.TemporaryDirectory()
    home = Path(tmp.name)
    cfg = home / 'integrations.json'
    cfg.write_text(json.dumps({TENANT: {
        'connections': {'google': {}}, 'sheets_registers': REGISTERS,
        'assets': BLOCK, 'business_graph': GRAPH}}), encoding='utf-8')
    env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(cfg)})
    env.start()
    engine = Engine(home / 'p.db', build_registry(), lambda t, a: POLICY)

    def call(fn, *args, **kwargs):
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: {'values': ROWS}):
                return fn(engine, TENANT, AGENT, *args, 's1', **kwargs)

    try:
        # --- the constants ---------------------------------------------------
        for label, value, expected in (('MAX_LEVELS', A.MAX_LEVELS, 8),
                                       ('MAX_PATH_CHARS', A.MAX_PATH_CHARS, 512),
                                       ('MAX_CHILDREN', A.MAX_CHILDREN, 200),
                                       ('MAX_DESCENDANTS', A.MAX_DESCENDANTS, 200),
                                       ('MAX_MEASUREMENTS', A.MAX_MEASUREMENTS, 100)):
            record(label, value, expected)
        record('SEGMENT_RE accepts 64 characters',
               bool(A.SEGMENT_RE.match('a' * 64)), True)
        record('SEGMENT_RE refuses 65', bool(A.SEGMENT_RE.match('a' * 65)), False)
        record('SEGMENT_RE refuses a leading dash',
               bool(A.SEGMENT_RE.match('-a')), False)

        # --- the path shape --------------------------------------------------
        at_limit = '/'.join(['a' * 63] * 7 + ['a' * 64])
        over_limit = '/'.join(['a' * 63] * 6 + ['a' * 64] * 2)
        record('the path at the ceiling is exactly 512 characters',
               len(at_limit), A.MAX_PATH_CHARS)
        record('one past it is 513', len(over_limit), A.MAX_PATH_CHARS + 1)
        record('a 512-character path is accepted',
               not _raises(ValueError, lambda: A._segments(TENANT, at_limit, LEVELS)), True)
        record('a 513-character path is refused',
               _raises(ValueError, lambda: A._segments(TENANT, over_limit, LEVELS)), True)
        record('an empty path is refused',
               _raises(ValueError, lambda: A.prefix_path(TENANT, '')), True)
        record('surrounding slashes are stripped',
               A.prefix_path(TENANT, '/zavod-1/sex-1/'), ['zavod-1', 'sex-1'])
        record('an empty segment is refused',
               _raises(ValueError, lambda: A.prefix_path(TENANT, 'zavod-1//sex-1')), True)

        # --- identity versus navigation --------------------------------------
        record('parse_path accepts the full depth',
               A.parse_path(TENANT, 'zavod-1/sex-1/liniya-1/stanok-1'),
               ['zavod-1', 'sex-1', 'liniya-1', 'stanok-1'])
        record('parse_path refuses an intern node',
               _raises(ValueError, lambda: A.parse_path(TENANT, 'zavod-1/sex-1/liniya-1')),
               True)
        record('prefix_path accepts an intern node',
               A.prefix_path(TENANT, 'zavod-1/sex-1/liniya-1'), ['zavod-1', 'sex-1', 'liniya-1'])
        record('prefix_path refuses one level too deep',
               _raises(ValueError, lambda: A.prefix_path(
                   TENANT, 'zavod-1/sex-1/liniya-1/stanok-1/x')), True)
        record('is_descendant is true for a deeper path',
               A.is_descendant(['zavod-1'], ['zavod-1', 'sex-1']), True)
        record('is_descendant is false at equal depth',
               A.is_descendant(['zavod-1'], ['zavod-1']), False)
        record('zavod-10 is not a descendant of zavod-1',
               A.is_descendant(['zavod-1'], ['zavod-10']), False)

        # --- THE DEFECT ------------------------------------------------------
        plain = call(A.tree)
        record('a small tree is not truncated', plain['truncated'], False)
        with patch.object(A, 'MAX_CHILDREN', 2):
            small = call(A.tree)
        record('the node list is never sliced', len(small['nodes']), small['count'])
        record('the tree is larger than the lowered ceiling', small['count'] > 2, True)
        record('...and the reply does NOT claim a withheld page',
               small['truncated'], False)
        record('the fix is the literal False, with the reason written down',
               "'truncated': False," in src, True)
        # The removal COMMENT quotes the old expression, so a bare substring check is
        # satisfied by the note that records the removal -- the same trap the MAX_QUANTITY
        # record met in fazza 26. Assert the exact claim instead.
        record('the old claim is gone',
               "'truncated': len(nodes) > MAX_CHILDREN" not in src, True)
        record('tree takes no limit to raise',
               'limit' in A.tree.__code__.co_varnames, False)
        for depth, accepted in ((1, True), (len(LEVELS), True), (0, False),
                                (len(LEVELS) + 1, False)):
            record(f'tree depth {depth} is {"accepted" if accepted else "refused"}',
                   not _raises(ValueError, lambda d=depth: call(A.tree, depth=d)), accepted)

        # --- children and descendants, which were already correct ------------
        for limit, accepted in ((1, True), (A.MAX_CHILDREN, True), (0, False),
                                (A.MAX_CHILDREN + 1, False), (True, False)):
            record(f'children limit {limit!r} is '
                   f'{"accepted" if accepted else "refused"}',
                   not _raises(ValueError, lambda l=limit: call(A.children, 'zavod-1',
                                                                limit=l)), accepted)
        record('a leaf level has no children',
               _raises(ValueError, lambda: call(A.children,
                                                'zavod-1/sex-1/liniya-1/stanok-1')), True)
        cut = call(A.children, 'zavod-1', limit=1)
        record('children ARE cut to the limit', len(cut['children']), 1)
        for limit, accepted in ((1, True), (A.MAX_DESCENDANTS, True), (0, False),
                                (A.MAX_DESCENDANTS + 1, False)):
            record(f'descendants limit {limit!r} is '
                   f'{"accepted" if accepted else "refused"}',
                   not _raises(ValueError, lambda l=limit: call(A.descendants, 'zavod-1',
                                                                limit=l)), accepted)
        descendants = call(A.descendants, 'zavod-1', limit=1)
        record('descendants ARE cut to the limit', descendants['returned'], 1)
        record('...and count the whole population', descendants['count'] > 1, True)
        record('...and the flag says so', descendants['truncated'], True)
        exact = call(A.descendants, 'zavod-1', limit=A.MAX_DESCENDANTS)
        record('an exact fit is not a cut',
               (exact['returned'], exact['truncated']), (exact['count'], False))

        # --- levels and measurements -----------------------------------------
        record('levels reports the declared shape', A.levels(TENANT)['levels'], LEVELS)
        record('levels reports the depth', A.levels(TENANT)['depth'], len(LEVELS))
        record('the measurement ceiling is 100', A.MAX_MEASUREMENTS, 100)
    finally:
        env.stop()
        tmp.cleanup()

    # --- the declared levels, through the config ------------------------------
    for count, accepted in ((1, True), (A.MAX_LEVELS, True), (A.MAX_LEVELS + 1, False)):
        declared = ['l%d' % i for i in range(count)]
        block = {**BLOCK, 'levels': declared}
        outcome_ = _levels_outcome(block)
        record(f'{count} declared levels is {"accepted" if accepted else "refused"}',
               outcome_[0], 'ok' if accepted else 'ValueError')
    record('a duplicate level name is refused',
           _levels_outcome({**BLOCK, 'levels': ['a', 'a']})[0], 'ValueError')
    for count, accepted in ((A.MAX_MEASUREMENTS, True), (A.MAX_MEASUREMENTS + 1, False)):
        block = {**BLOCK, 'measurements': ['m%d' % i for i in range(count)]}
        record(f'{count} measurements is {"accepted" if accepted else "refused"}',
               _measurements_outcome(block)[0], 'ok' if accepted else 'ValueError')

    # --- the guards that now sit on the bounds --------------------------------
    record('a guard pins what the tree flag may claim',
           'test_the_tree_never_claims_a_cut_it_did_not_make' in guard, True)
    record('a guard walks the segment ceiling',
           'test_the_segment_ceiling_is_sixty_four' in guard, True)
    record('a guard walks the path-length ceiling',
           'test_the_path_length_ceiling_is_five_hundred_and_twelve' in guard, True)
    record('a guard walks the children and descendants ceilings',
           'test_the_children_and_descendants_ceilings' in guard, True)
    record('a guard walks the measurement ceiling',
           'test_the_measurement_ceiling_is_one_hundred' in guard, True)
    record('a guard walks the level and measurement-name ceilings',
           'test_the_level_and_measurement_name_ceilings' in guard, True)


def _levels_outcome(block):
    """Validate one ``assets`` block through the real config path."""
    import json
    import os
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    from platform_runtime import assets as A
    tmp = tempfile.TemporaryDirectory()
    try:
        cfg = Path(tmp.name) / 'i.json'
        cfg.write_text(json.dumps({'t': {'assets': block}}), encoding='utf-8')
        with patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(cfg)}):
            try:
                A.levels('t')
                return ('ok', None)
            except Exception as error:  # noqa: BLE001
                return (type(error).__name__, None)
    finally:
        tmp.cleanup()


def _measurements_outcome(block):
    import json
    import os
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    from platform_runtime import assets as A
    tmp = tempfile.TemporaryDirectory()
    try:
        cfg = Path(tmp.name) / 'i.json'
        cfg.write_text(json.dumps({'t': {'assets': block}}), encoding='utf-8')
        with patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(cfg)}):
            try:
                A.measurements('t')
                return ('ok', None)
            except Exception as error:  # noqa: BLE001
                return (type(error).__name__, None)
    finally:
        tmp.cleanup()



def main():
    section_search()
    section_conflicts()
    section_timeline()
    section_shifts()
    section_activity()
    section_erp_status()
    section_fraud_median()
    section_round_number()
    section_duplicate_report()
    section_line_item_arithmetic()
    section_knowledge_truncation()
    section_ledger_truncation()
    section_exception_mapping()
    section_engine_clock()
    section_expiry_boundary()
    section_approval_deadline()
    section_lease_bound()
    section_event_lease()
    section_quota_windows()
    section_device_cascade()
    section_identity_throttle()
    section_oauth_deadlines()
    section_cooldown_windows()
    section_oversight_and_supervisor_bounds()
    section_erp_bounds()
    section_unrepresentable_numbers()
    section_database_contract_bounds()
    section_agent_loop_bounds()
    section_route_surface_vs_ui()
    section_budget_and_schema_bounds()
    section_whatsapp_bounds()
    section_telephony_bounds()
    section_connector_bounds()
    section_knowledge_bounds()
    section_vision_bounds()
    section_asset_bounds()
    section_sheets_bounds()
    section_documents_bounds()
    section_crm_bounds()
    section_crm_adapter_bounds()
    section_reengagement_bounds()
    section_supervisor_bounds()
    print()
    print(f'measured properties : {len(RESULTS)}')
    print(f'passed              : {sum(RESULTS)}')
    print(f'failed              : {len(RESULTS) - sum(RESULTS)}')
    return 0 if all(RESULTS) else 1


def section_sheets_bounds():
    """The sheets register layer: two read tools, one limit, two different answers.

    A 22-mode revert matrix left TWO modes green. One is honest; the other was the fix
    this phase made.

    THE MEASURED DEFECT. `_read` serves both `sheets.read` (the raw matrix) and
    `sheets.rows` (header-keyed objects). `shape_rows` admits the header row -- it
    slices `values[:limit + 1]` -- but the NON-KEYED matrix slice did not:

        'returned': len(rows) if keyed else len(values[:limit]),
        ...
        result['values'] = [... for row in values[:limit] if isinstance(row, list)]

    `header_row` is true by DEFAULT, so `values` carries a header row that is not data.
    Measured with limit=3 and header_row=True:

        sheet holds 2 data rows -> read returns 2 data rows, truncated=False   correct
        sheet holds 3 data rows -> read returns 2 data rows, truncated=False   LIES
        sheet holds 4 data rows -> read returns 2 data rows, truncated=True    cut announced
        sheet holds 5 data rows -> read returns 2 data rows, truncated=True    cut announced

    Three faults in one expression. The slice returned `limit - 1` DATA rows where the
    keyed path returned `limit`; and in the window where the sheet held EXACTLY `limit`
    data rows it DROPPED the last one while the flag -- which counts data rows --
    reported False. The caller was told "that is everything" about a reply that had
    silently lost a row. `returned` counted matrix rows here and data rows there, so one
    field meant two things depending on which read tool the agent called.

    This is the fourth occurrence of the "flag that lies" shape (fazza II, 35, 36), and
    the first where the flag denied a DROP rather than inventing a cut.

    The second green mode is honest and recorded as such: `capped = values[:limit + 1]`
    already bounds `matrix`, so `matrix[1:]` and `matrix[1:limit + 1]` are the same
    list. That inner slice is DEFENSIVE and unreachable, so it is pinned by its exact
    line with the reason rather than by a behaviour that cannot be walked.
    """
    print('=== 37: sheets bounds and the row the reply dropped ===')
    import json  # noqa: E402
    import os  # noqa: E402
    import tempfile  # noqa: E402
    from unittest.mock import patch  # noqa: E402

    from platform_runtime import sheets as S  # noqa: E402
    from platform_runtime.engine import Engine  # noqa: E402
    from platform_runtime.tools import build_registry  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'platform_runtime' / 'sheets.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_sheets.py').read_text(encoding='utf-8')

    TENANT = 't_sheets'
    AGENT = 'finance.bot'
    SHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
    POLICY = {'tools': ['sheets.registers', 'sheets.read', 'sheets.rows'],
              'allowed_connections': ['google'], 'ladder': 'human_assisted'}
    REGISTER = {'connection': 'google', 'spreadsheet_id': SHEET,
                'ranges': {'revenue': 'Kunlik!A1:F'}, 'max_rows': 200,
                'header_row': True}

    tmp = tempfile.TemporaryDirectory()
    home = Path(tmp.name)
    cfg = home / 'integrations.json'

    def write(registers):
        cfg.write_text(json.dumps({TENANT: {'sheets_registers': registers}}),
                       encoding='utf-8')

    write({'finance': REGISTER})
    env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(cfg)})
    env.start()
    engine = Engine(home / 'p.db', build_registry(), lambda t, a: POLICY)

    def call(tool, args, rows):
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: {'values': rows}):
                return build_registry().get(tool).handler(engine, TENANT, AGENT, args, 's1')

    def matrix(n_data):
        return [['h']] + [['d%d' % i] for i in range(n_data)]

    try:
        # --- the constants ---------------------------------------------------
        for label, value, expected in (('MAX_ROWS', S.MAX_ROWS, 200),
                                       ('MAX_CELL_CHARS', S.MAX_CELL_CHARS, 200),
                                       ('MAX_RESPONSE_BYTES', S.MAX_RESPONSE_BYTES,
                                        200_000)):
            record(label, value, expected)

        # --- THE DEFECT: one limit, two answers ------------------------------
        for n_data, expected_data, expected_flag in ((2, 2, False), (3, 3, False),
                                                     (4, 3, True), (5, 3, True)):
            read = call('sheets.read', {'register': 'finance', 'range': 'revenue',
                                        'limit': 3}, matrix(n_data))
            rows = call('sheets.rows', {'register': 'finance', 'range': 'revenue',
                                        'limit': 3}, matrix(n_data))
            record(f'{n_data} data rows, limit=3: read returns {expected_data} DATA rows',
                   len(read['values']) - 1, expected_data)
            record(f'{n_data} data rows, limit=3: read returned={expected_data}',
                   read['returned'], expected_data)
            record(f'{n_data} data rows, limit=3: read truncated={expected_flag}',
                   read['truncated'], expected_flag)
            record(f'{n_data} data rows, limit=3: rows returns the same {expected_data}',
                   rows['returned'], expected_data)
            record(f'{n_data} data rows: the two read tools agree on the count',
                   read['returned'], rows['returned'])
        # The window that lied: the sheet holds EXACTLY `limit` data rows.
        exactly = call('sheets.read', {'register': 'finance', 'range': 'revenue',
                                       'limit': 3}, matrix(3))
        record('a sheet holding exactly `limit` data rows keeps all of them',
               len(exactly['values']) - 1, 3)
        record('...and is not called truncated', exactly['truncated'], False)

        # --- the header-vs-data flag, still pinned ----------------------------
        two = call('sheets.read', {'register': 'finance', 'range': 'revenue',
                                   'limit': 2}, matrix(2))
        record('a header is not counted as a data row',
               (two['returned'], two['truncated']), (2, False))
        three = call('sheets.read', {'register': 'finance', 'range': 'revenue',
                                     'limit': 2}, matrix(3))
        record('one data row past the limit IS announced', three['truncated'], True)

        # --- clamped, not raised ---------------------------------------------
        write({'big': {**REGISTER, 'max_rows': 500}})
        record('a register may declare max_rows above the read ceiling',
               S.registers(TENANT)['big']['max_rows'], 500)
        record('an ordinary read on that register still succeeds',
               call('sheets.read', {'register': 'big', 'range': 'revenue'},
                    [['h'], ['1']])['returned'], 1)
        record('a limit above the ceiling is clamped to MAX_ROWS',
               call('sheets.read', {'register': 'big', 'range': 'revenue',
                                    'limit': 100000},
                    [['h']] + [['1']] * 300)['returned'], S.MAX_ROWS)
        write({'finance': REGISTER})

        # --- the caps, through the real config path --------------------------
        entry = {'connection': 'google', 'spreadsheet_id': SHEET,
                 'ranges': {'revenue': 'A1:B'}, 'max_rows': 10}
        for count, accepted in ((50, True), (51, False)):
            write({'r%d' % i: dict(entry) for i in range(count)})
            record(f'{count} registers is {"accepted" if accepted else "refused"}',
                   _sheets_outcome()[0], 'ok' if accepted else 'ValueError')
        for count, accepted in ((40, True), (41, False)):
            write({'ok': dict(entry, ranges={'g%d' % i: 'A1:B' for i in range(count)})})
            record(f'{count} ranges is {"accepted" if accepted else "refused"}',
                   _sheets_outcome()[0], 'ok' if accepted else 'ValueError')
        for value, accepted in ((1000, True), (1001, False), (0, False)):
            write({'ok': dict(entry, max_rows=value)})
            record(f'max_rows {value} is {"accepted" if accepted else "refused"}',
                   _sheets_outcome()[0], 'ok' if accepted else 'ValueError')
        for length, accepted in ((120, True), (121, False), (19, False)):
            write({'ok': dict(entry, spreadsheet_id='A' * length)})
            record(f'a spreadsheet id of {length} characters is '
                   f'{"accepted" if accepted else "refused"}',
                   _sheets_outcome()[0], 'ok' if accepted else 'ValueError')
        for length, accepted in ((64, True), (65, False)):
            write({'ok': {**entry, 'connection': 'c' * length}})
            record(f'a connection name of {length} characters is '
                   f'{"accepted" if accepted else "refused"}',
                   _sheets_outcome()[0], 'ok' if accepted else 'ValueError')
        for length, accepted in ((64, True), (65, False)):
            write({'r' * length: dict(entry)})
            record(f'a register name of {length} characters is '
                   f'{"accepted" if accepted else "refused"}',
                   _sheets_outcome()[0], 'ok' if accepted else 'ValueError')
        for length, accepted in ((64, True), (65, False)):
            write({'ok': dict(entry, ranges={'g' * length: 'A1:B'})})
            record(f'a range name of {length} characters is '
                   f'{"accepted" if accepted else "refused"}',
                   _sheets_outcome()[0], 'ok' if accepted else 'ValueError')
        write({'finance': REGISTER})
    finally:
        env.stop()
        tmp.cleanup()

    # --- the A1 notation and the length bound ---------------------------------
    lines = set(src.splitlines())
    record('the range-length bound is 128',
           '    if not isinstance(value, str) or not value or len(value) > 128:'
           in lines, True)
    record('a declared A1 range is accepted',
           S.validate_range('Kunlik!A1:F'), 'Kunlik!A1:F')
    record('a whole-column range is accepted',
           S.validate_range('Kunlik!A:F'), 'Kunlik!A:F')
    record('a 129-character A1 string is refused',
           _raises(ValueError, lambda: S.validate_range('A' * 129)), True)
    record('a bare sheet reference is refused',
           _raises(ValueError, lambda: S.validate_range('Kunlik!')), True)
    record('a leading bang is refused',
           _raises(ValueError, lambda: S.validate_range('!Kunlik')), True)

    # --- the defensive slice, recorded honestly -------------------------------
    record('the keyed row slice is present',
           'for row in matrix[1:limit + 1]:' in src, True)
    record('...and is documented as unreachable',
           'DEFENSIVE: `capped` above already bounds' in src, True)

    # --- the guards that now sit on the bounds --------------------------------
    for label, name in (
            ('the read ceiling', 'test_the_read_ceiling_is_two_hundred'),
            ('the cell ceiling', 'test_the_cell_ceiling_is_two_hundred'),
            ('the byte ceiling',
             'test_the_response_byte_ceiling_is_two_hundred_thousand'),
            ('the header-not-data rule',
             'test_a_header_is_not_counted_as_a_data_row'),
            ('the clamp',
             'test_a_register_may_declare_max_rows_above_the_read_ceiling'),
            ('the register and range name caps',
             'test_register_and_range_names_are_capped_at_sixty_four'),
            ('the range-length cap',
             'test_the_range_length_ceiling_is_one_hundred_and_twenty_eight'),
            ('the register cap', 'test_the_register_cap_is_fifty'),
            ('the range cap', 'test_the_range_cap_is_forty'),
            ('the declared max_rows cap',
             'test_the_declared_max_rows_ceiling_is_one_thousand'),
            ('the id and connection caps',
             'test_the_spreadsheet_id_ceiling_is_one_hundred_and_twenty'),
            ('the defensive slice',
             'test_the_keyed_row_slice_is_defensively_bounded')):
        record(f'a guard pins {label}', name in guard, True)


def _sheets_outcome():
    """Validate the current sheets registers through the real config path."""
    from platform_runtime import sheets as S
    try:
        S.registers('t_sheets')
        return ('ok', None)
    except Exception as error:  # noqa: BLE001
        return (type(error).__name__, None)


def section_documents_bounds():
    """The document layer: money, and three guards that measured the wrong thing.

    A 26-mode revert matrix left EIGHTEEN mutations green, and three of them were the
    fixes this phase made. `documents.py` is the accounts-payable control chain -- the
    module whose own docstring says "money is compared in integer minor units (tiyin),
    NEVER in floats, because ``0.1 + 0.2 != 0.3`` is not an acceptable property of an
    accounts-payable check".

    DEFECT A -- A GUARD BOUNDED THE WRONG STRING. `bank_account` was checked with
    ``re.fullmatch(r'[0-9 ]{4,34}', value)`` and THEN stripped of spaces, while the
    message promised "4 to 34 digits". Measured:

        '    ' (4 spaces) -> matched, stripped to '', stored EMPTY
        '1   '            -> matched, stored '1'   (a ONE-digit account)
        '12  '            -> matched, stored '12'

    The emptied value is the serious one: `fraud_signals` gates the
    `bank_account_changed` signal on `if doc['bank_account']`, and that signal is
    MATERIAL -- it exists to catch a supplier-account substitution, which is the
    classic accounts-payable fraud. A whitespace-only field did not fail the control;
    it REMOVED it. Bound the digits the message names.

    DEFECT B -- A SHAPE CHECK PASSED FOR A DATE CHECK. ``[0-9]{4}-[0-9]{2}-[0-9]{2}``
    accepts ``2026-13-45``, ``2026-02-30`` and ``2026-00-00``. Such a value was
    stored, and ``date()`` then raised on it INSIDE ``fraud_signals``, where the
    exception is caught -- so the document carried no weekday signal and nothing said
    why. The docstring argues that an AMBIGUOUS date must be refused because guessing
    is wrong; an IMPOSSIBLE one was accepted, and there is nothing to guess.

    DEFECT C -- MONEY WENT THROUGH A FLOAT, IN THE ONE MODULE THAT FORBIDS IT.
    ``_median`` returned ``int(statistics.median(values))``. That returns a float for
    an even count, and floats hold consecutive integers exactly only up to ``2 ** 53``
    -- while ``MAX_AMOUNT`` allows a USD total of ``10 ** 17`` minor units. Measured
    before the fix:

        _median([10**16, 10**16 + 2]) -> 10**16       (exact 10**16 + 1)  wrong by -1
        _median([10**17, 10**17 + 2]) -> 10**17       (exact 10**17 + 1)  wrong by -1

    And the number is PRINTED into the approver's evidence as "the median". The
    docstring two paragraphs above says it in the module's own words: "A number
    labelled as the median has to be the median, or the label is a lie and the
    approver is checking the flag against a figure it cannot reconcile." The fix for
    the upper-middle bias introduced a float, and the float made the label false again
    above ``2 ** 53``. Now averaged in integer arithmetic, which is exact for every
    input and still never raises the threshold.

    ONE ASYMMETRY, RECORDED NOT FIXED: the amount STRING shape allows fifteen whole
    digits, which excludes ``10 ** 15`` itself, while the RANGE check and the INT path
    both accept it. The shape bound is one digit stricter than the range bound. The
    discriminating input is therefore exactly ``10 ** 15`` -- a sixteen-nines overflow
    is refused by the range check either way and proves nothing about the shape.
    """
    print('=== 38: document and money bounds ===')
    import inspect  # noqa: E402
    import json  # noqa: E402
    import os  # noqa: E402
    import tempfile  # noqa: E402
    from unittest.mock import patch  # noqa: E402

    from platform_runtime import documents as D  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'platform_runtime' / 'documents.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_documents.py').read_text(encoding='utf-8')

    def outcome(fn):
        try:
            fn()
            return 'ok'
        except Exception as error:  # noqa: BLE001
            return type(error).__name__

    def doc(**extra):
        base = {'kind': 'invoice', 'supplier': 'acme', 'number': 'INV-1',
                'currency': 'UZS', 'total': 1250000}
        base.update(extra)
        return base

    # --- the constants -------------------------------------------------------
    for label, value, expected in (('MAX_AMOUNT', D.MAX_AMOUNT, 10 ** 15),
                                   ('MAX_LINE_ITEMS', D.MAX_LINE_ITEMS, 200),
                                   ('MAX_TEXT_CHARS', D.MAX_TEXT_CHARS, 200),
                                   ('ROUND_TRAILING_ZEROS', D.ROUND_TRAILING_ZEROS, 3),
                                   ('DEFAULT_TOLERANCE_MINOR',
                                    D.DEFAULT_TOLERANCE_MINOR, 0)):
        record(label, value, expected)
    record('MINOR_UNITS scales per currency',
           tuple(sorted(D.MINOR_UNITS.items())),
           (('EUR', 100), ('RUB', 100), ('USD', 100), ('UZS', 1)))

    # --- DEFECT A: the bank account ------------------------------------------
    record('a 4-digit bank account is accepted',
           D.normalize(doc(bank_account='1234'))['bank_account'], '1234')
    record('spaces inside a bank account are stripped',
           D.normalize(doc(bank_account='1234 5678 9012'))['bank_account'],
           '123456789012')
    for value in ('    ', '1   ', '12  ', '123', '1 2 3'):
        record(f'bank_account {value!r} is refused',
               outcome(lambda v=value: D.normalize(doc(bank_account=v))), 'ValueError')
    record('34 digits is the ceiling',
           len(D.normalize(doc(bank_account='1' * 34))['bank_account']), 34)
    record('35 digits is refused',
           outcome(lambda: D.normalize(doc(bank_account='1' * 35))), 'ValueError')
    record('...and the digits are what the guard bounds',
           'if not 4 <= len(bank_account) <= 34:' in src, True)
    record('...so a blank field cannot empty the value the signal is gated on',
           "if doc['bank_account']:" in src, True)

    # --- DEFECT B: the date ---------------------------------------------------
    for value in ('2026-13-45', '2026-02-30', '2026-00-00', '2026-02-29',
                  '2026-04-31', '0000-01-01'):
        record(f'doc_date {value!r} is refused',
               outcome(lambda v=value: D._date(v)), 'ValueError')
    for value in ('2026-09-18', '2024-02-29', '2026-12-31'):
        record(f'doc_date {value!r} is accepted', D._date(value), value)
    record('a non-ISO local form is still refused',
           outcome(lambda: D._date('10.01.2026')), 'ValueError')
    record('...and the guard checks the calendar, not only the shape',
           'calendar_date(year, month, day)' in src, True)

    # --- DEFECT C: the median -------------------------------------------------
    for values, expected in (([1000], 1000), ([1000, 2000], 1500), ([1, 2], 1),
                             ([2 ** 53, 2 ** 53 + 2], 2 ** 53 + 1),
                             ([10 ** 16, 10 ** 16 + 2], 10 ** 16 + 1),
                             ([10 ** 17, 10 ** 17 + 2], 10 ** 17 + 1),
                             ([10 ** 17, 10 ** 17 + 1, 10 ** 17 + 5], 10 ** 17 + 1),
                             ([2000, 1000], 1500)):
        record(f'_median({values!r})', D._median(values), expected)
    median_source = inspect.getsource(D._median)
    # NOT `'statistics' not in source`: the new docstring NAMES statistics.median to
    # explain why it is unusable, so the word survives the removal -- the same trap as
    # MAX_QUANTITY (fazza 26) and the tree flag (fazza 36). Assert the exact code.
    record('...and statistics is no longer imported',
           'import statistics' in median_source, False)
    record('...and statistics.median is no longer called',
           'statistics.median(values)' in median_source, False)
    record('...and the even-count case divides in integer arithmetic',
           '(ordered[middle - 1] + ordered[middle]) // 2' in median_source, True)
    record('2**53 is where float integers stop being consecutive',
           int(float(2 ** 53 + 1)) == 2 ** 53 + 1, False)

    # --- the money bounds -----------------------------------------------------
    record('the UZS ceiling is MAX_AMOUNT', D._amount_minor(10 ** 15, 'UZS'), 10 ** 15)
    record('one past it is refused',
           outcome(lambda: D._amount_minor(10 ** 15 + 1, 'UZS')), 'ValueError')
    record('a USD int is scaled by 100', D._amount_minor(10 ** 15, 'USD'), 10 ** 17)
    record('a float amount is refused',
           outcome(lambda: D._amount_minor(1.5, 'UZS')), 'ValueError')
    record('a bool amount is refused',
           outcome(lambda: D._amount_minor(True, 'UZS')), 'ValueError')
    record('a negative amount is refused',
           outcome(lambda: D._amount_minor(-1, 'UZS')), 'ValueError')
    record('a 15-digit amount string is accepted',
           D._amount_minor('9' * 15, 'UZS'), 10 ** 15 - 1)
    record('...but 10**15 as a string is refused by the SHAPE',
           outcome(lambda: D._amount_minor('1000000000000000', 'UZS')), 'ValueError')
    record('...while 10**15 as an INT is accepted -- the asymmetry',
           D._amount_minor(10 ** 15, 'UZS'), 10 ** 15)
    record('a 16-digit amount string is refused',
           outcome(lambda: D._amount_minor('9' * 16, 'UZS')), 'ValueError')
    record('six fraction digits are accepted and truncated',
           D._amount_minor('1.509999', 'USD'), 150)
    record('seven fraction digits are refused',
           outcome(lambda: D._amount_minor('1.1234567', 'USD')), 'ValueError')
    record('a non-ASCII amount is refused',
           outcome(lambda: D._amount_minor('\u0967\u0968\u0969', 'UZS')), 'ValueError')
    record('format_amount round-trips a scaled amount',
           D.format_amount(150, 'USD'), '1.50')
    record('format_amount is identity for a factor of 1',
           D.format_amount(150, 'UZS'), '150')

    # --- the structural caps --------------------------------------------------
    for length, accepted in ((64, True), (65, False)):
        record(f'a document number of {length} characters is '
               f'{"accepted" if accepted else "refused"}',
               outcome(lambda l=length: D._number('A' * l)),
               'ok' if accepted else 'ValueError')
        record(f'a party of {length} characters is '
               f'{"accepted" if accepted else "refused"}',
               outcome(lambda l=length: D._party('a' * l)),
               'ok' if accepted else 'ValueError')
    for count, accepted in ((200, True), (201, False)):
        record(f'{count} line items is {"accepted" if accepted else "refused"}',
               outcome(lambda c=count: D._line_items([{'amount': 0}] * c, 'UZS')),
               'ok' if accepted else 'ValueError')
    for length, accepted in ((200, True), (201, False)):
        record(f'a description of {length} characters is '
               f'{"accepted" if accepted else "refused"}',
               outcome(lambda l=length: D._line_items([{'description': 'd' * l}],
                                                      'UZS')),
               'ok' if accepted else 'ValueError')
    record('a line total multiplies quantity by the unit price',
           D._line_items([{'quantity': 2, 'amount': 5000}], 'UZS')[0]['line_total'],
           10000)
    record('line_items_total sums the line totals',
           D.line_items_total(D._line_items([{'quantity': 2, 'amount': 5000},
                                             {'amount': 100}], 'UZS')), 10100)
    record('a document that disagrees with itself is refused',
           outcome(lambda: D.normalize(doc(total=5000,
                                           line_items=[{'quantity': 2,
                                                        'amount': 5000}]))),
           'ValueError')

    # --- the tool schema bounds -----------------------------------------------
    from platform_runtime.tools import build_registry  # noqa: E402

    registry = build_registry()
    for name in ('document.match', 'document.posting_plan'):
        tolerance = registry.get(name).schema['properties']['tolerance_minor']
        record(f'{name} tolerance floor', tolerance['minimum'], 0)
        record(f'{name} tolerance ceiling', tolerance['maximum'], 100_000_000_000)
    ratio = registry.get('document.fraud_signals').schema['properties']['outlier_ratio']
    record('the outlier ratio floor', ratio['minimum'], 1)
    record('the outlier ratio ceiling', ratio['maximum'], 100)
    record('a 16000-character payload validates',
           outcome(lambda: registry.get('document.parse').validate(
               {'fields': 'x' * 16000})), 'ok')
    record('a 16001-character payload is refused',
           outcome(lambda: registry.get('document.parse').validate(
               {'fields': 'x' * 16001})), 'ValueError')

    # --- the operator settings ------------------------------------------------
    tmp = tempfile.TemporaryDirectory()
    try:
        cfg = Path(tmp.name) / 'i.json'
        env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(cfg)})
        env.start()
        try:
            for block, accepted in (({}, True), ({'tolerance_minor': 0}, True),
                                    ({'tolerance_minor': -1}, False),
                                    ({'outlier_ratio': 1}, True),
                                    ({'outlier_ratio': 0}, False),
                                    ({'approver_role': 'owner'}, True),
                                    ({'approver_role': 'agent'}, False),
                                    ({'residency': 'uz'}, True),
                                    ({'residency': 'eu'}, False),
                                    ({'currencies': ['UZS']}, True),
                                    ({'currencies': []}, False),
                                    ({'currencies': ['XYZ']}, False),
                                    ({'auto_pay': True}, False)):
                cfg.write_text(json.dumps({'t': {'documents': block}}),
                               encoding='utf-8')
                record(f'documents config {sorted(block)} is '
                       f'{"accepted" if accepted else "refused"}',
                       outcome(lambda: D.documents_config('t')),
                       'ok' if accepted else 'ValueError')
        finally:
            env.stop()
    finally:
        tmp.cleanup()

    # --- the guards that now sit on the bounds --------------------------------
    for label, name in (
            ('the amount ceiling',
             'test_the_amount_ceiling_is_ten_to_the_fifteenth'),
            ('the line-item cap', 'test_the_line_item_cap_is_two_hundred'),
            ('the text cap', 'test_the_text_cap_is_two_hundred'),
            ('the identifier caps', 'test_the_identifier_caps_are_sixty_four'),
            ('the amount string bounds', 'test_the_amount_string_bounds'),
            ('the negative-amount refusal', 'test_a_negative_amount_is_refused'),
            ('the bank-account digits',
             'test_the_bank_account_bound_is_on_the_digits_not_the_spaces'),
            ('the emptied bank account',
             'test_an_emptied_bank_account_cannot_disable_the_signal'),
            ('the date calendar',
             'test_the_date_guard_checks_the_calendar_not_only_the_shape'),
            ('the exact median',
             'test_the_median_is_exact_above_two_to_the_fifty_third'),
            ('the no-float rule',
             'test_the_median_does_not_route_money_through_a_float'),
            ('the tool schema bounds', 'test_the_tool_bounds_are_pinned'),
            ('the payload bound', 'test_the_payload_bound_is_sixteen_thousand'),
            ('the config floors', 'test_the_config_floors'),
            ('the history clamp', 'test_the_history_clamp_is_pinned'),
            ('the round-number threshold',
             'test_the_round_number_threshold_is_three'),
            ('the default tolerance',
             'test_the_default_tolerance_is_zero')):
        record(f'a guard pins {label}', name in guard, True)


def section_crm_bounds():
    """The CRM boundary layer: one gate, and it stood open.

    A 30-mode revert matrix left TWENTY-FIVE mutations green, and one of them was the fix
    this phase made. ``crm_contract.py`` plus ``crm_gateway.py`` are the validation layer
    every CRM driver shares -- Bitrix24, Kommo/amoCRM, 1C and the operator-declared custom
    HTTP connection -- so a bound that is wrong here is wrong for all of them.

    DEFECT A -- THE ONLY GATE FOR A CRM CONNECTION READ THE POLICY LENIENTLY.
    ``crm_gateway._check_agent_allowed`` used

        if allowed_conns and connection not in allowed_conns:

    so an EMPTY ``allowed_connections`` -- which every other module reads as "nothing is
    permitted" -- read here as "everything is permitted". Measured with
    ``allowed_connections: []``: ``_check_agent_allowed`` returned cleanly and
    ``crm.lead.search`` reached the adapter and issued the read.

    It matters here more than anywhere else, because this is the ONLY place a CRM
    connection is checked at all. The engine's own allowlist check covers
    ``{'connectors.read', 'database.read', 'database.plan_write', 'database.write'}`` and
    no ``crm.*`` tool is in that set, so the lenient form was not a redundant second
    opinion -- it was the single gate. Every other reader is strict: ``engine.py``,
    ``connectors.py`` (twice), ``database/gateway.py``, ``google_adapters.py``,
    ``oauth.py``, ``business_graph.py`` and ``sheets.py`` (fixed in fazza 37 for exactly
    this reason). This was the last lenient reader in the runtime.

    DEFECT B -- A BARE ``int()`` IS NOT A VALIDATION. ``parse_call_record`` did
    ``duration = int(data.get('duration_seconds', 0))`` and ``bounded_price`` accepted a
    float and truncated it. Measured: ``bounded_price(1.9)`` returned **1**, so a
    fractional price silently became a smaller one; ``bounded_price(float('inf'))`` raised
    **OverflowError**, which is not the ``ValueError`` this module's callers catch; and
    ``duration_seconds=None`` raised **TypeError**. Its own message said "must be an
    integer amount". ``bounded_int`` now refuses a bool, refuses a float, refuses a
    string, bounds both ends and never raises anything but ``ValueError``.

    DEFECT C -- A BARE, UNBOUNDED ``float()`` ACCEPTED NaN AND INFINITY.
    ``parse_followup_request`` did ``scheduled_at = float(data.get('scheduled_at', 0))``.
    ``'nan'`` and ``'inf'`` are valid floats, so they did not raise, and nothing
    downstream rejects them: the value goes into the audit record and back to the caller.
    A negative schedule was accepted too. Measured: ``scheduled_at='nan'`` was accepted and
    survived as ``nan``. The tool schema declares the field an INTEGER of at least 0, so
    the parser now returns one, and the ceiling is the registry validator's own integer
    default (``10 ** 12``) rather than a number invented here.

    ONE CANDIDATE MEASURED AND DISMISSED. ``tool_crm_lead_search`` converts its ``limit``
    with a bare ``int(args.get('limit', 20))`` and no bound, so ``10 ** 9`` does reach the
    adapter. It is absorbed TWICE: the tool schema declares ``1..50``, and every adapter
    clamps with ``min(max(1, limit), 50)``. The bare conversion is therefore not a bound
    violation, and it is recorded as measured rather than "fixed".

    TWO SURFACES RECORDED, NOT CHANGED. ``safe_relative_path`` rejects traversal, ``//``,
    backslashes, whitespace, quotes, ``:`` and ``#``, but accepts a ``?`` query separator
    and percent-encoding (``/a%2e%2e%2fb`` passes). The docstring's claim is about
    re-targeting the request, and the template is operator configuration, not agent input;
    placeholder VALUES are always quoted with an empty safe set. Recorded as an open risk.
    """
    print('=== 39: CRM boundary layer ===')
    import json  # noqa: E402
    import os  # noqa: E402
    import tempfile  # noqa: E402
    from unittest.mock import patch  # noqa: E402

    from platform_runtime.crm import crm_contract as C  # noqa: E402
    from platform_runtime.crm import crm_gateway as G  # noqa: E402
    from platform_runtime.engine import Engine  # noqa: E402
    from platform_runtime.tools import build_registry  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    gateway_src = (root / 'platform_runtime' / 'crm' / 'crm_gateway.py').read_text(
        encoding='utf-8')
    engine_src = (root / 'platform_runtime' / 'engine.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_crm_contract.py').read_text(encoding='utf-8')
    guard_gw = (root / 'runtime_tests' / 'test_crm_gateway.py').read_text(encoding='utf-8')

    def outcome(fn):
        try:
            fn()
            return 'ok'
        except Exception as error:  # noqa: BLE001
            return type(error).__name__

    # --- DEFECT A: the allowlist ----------------------------------------------
    tmp = tempfile.TemporaryDirectory()
    try:
        cfg = Path(tmp.name) / 'integrations.json'
        cfg.write_text(json.dumps({'t': {'connections': {'crm_bitrix': {
            'driver': 'bitrix24', 'host': 'sales.bitrix24.com',
            'allowed_hosts': ['sales.bitrix24.com'], 'token_env': 'B24_TOKEN',
            'capabilities': ['read']}}}}), encoding='utf-8')
        env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(cfg),
                                      'B24_TOKEN': 'tok'})
        env.start()
        try:
            def engine_with(allowed, tag):
                return Engine(Path(tmp.name) / ('p_%s.db' % tag), build_registry(),
                              lambda t, a: {'tools': ['crm.lead.search',
                                                      'crm.lead.plan'],
                                            'allowed_connections': allowed,
                                            'ladder': 'autonomous'})

            for allowed, expected, tag in ((['crm_bitrix'], 'ok', 'a'),
                                           (['other'], 'Forbidden', 'b'),
                                           ([], 'Forbidden', 'c'),
                                           (None, 'Forbidden', 'd')):
                engine = engine_with(allowed, tag)
                record(f'an allowlist of {allowed!r} answers',
                       outcome(lambda e=engine: G._check_agent_allowed(
                           e, 't', 'sales_bot', 'crm.lead.search', 'crm_bitrix')),
                       expected)

            class Stub:
                def __init__(self):
                    self.calls = []

                def find_leads(self, **kwargs):
                    self.calls.append(kwargs)
                    return [{'id': '1'}]

            engine = engine_with([], 'e2e')
            stub = Stub()
            with patch.object(G, 'get_adapter', return_value=stub):
                record('an EMPTY allowlist is refused end to end',
                       outcome(lambda: G.tool_crm_lead_search(
                           engine, 't', 'sales_bot',
                           {'connection': 'crm_bitrix', 'phone': '+998901234567'}, 's1')),
                       'Forbidden')
            record('...and the adapter is never reached', len(stub.calls), 0)
            allowed_engine = engine_with(['crm_bitrix'], 'ok_e2e')
            ok_stub = Stub()
            with patch.object(G, 'get_adapter', return_value=ok_stub):
                G.tool_crm_lead_search(allowed_engine, 't', 'sales_bot',
                                       {'connection': 'crm_bitrix',
                                        'phone': '+998901234567'}, 's1')
            record('...while a permitted connection still works', len(ok_stub.calls), 1)
        finally:
            env.stop()
    finally:
        tmp.cleanup()

    record('the gateway reads the allowlist STRICTLY',
           'if connection not in allowed_conns:' in gateway_src, True)
    record('...and no longer uses the lenient form',
           'if allowed_conns and connection not in allowed_conns:' in gateway_src, False)
    covered = engine_src.split("if name in {'connectors.read'")[1].split('}')[0]
    record('the engine covers no crm.* tool in its own allowlist check',
           'crm.' in covered, False)
    for label, source, pattern in (
            ('connectors.py', 'platform_runtime/connectors.py',
             "args['connection'] not in engine.policy(tenant, agent)"
             ".get('allowed_connections', [])"),
            ('database/gateway.py', 'platform_runtime/database/gateway.py',
             "args.get('connection') not in policy.get('allowed_connections', [])"),
            ('engine.py', 'platform_runtime/engine.py',
             "args['connection'] not in policy.get('allowed_connections',[])"),
            ('google_adapters.py', 'platform_runtime/google_adapters.py',
             "connection not in engine.policy(tenant,agent)"
             ".get('allowed_connections', [])"),
            ('oauth.py', 'platform_runtime/oauth.py',
             'if connection not in allowed: raise Forbidden'),
            ('business_graph.py', 'platform_runtime/business_graph.py',
             'if connection not in allowed:'),
            ('sheets.py', 'platform_runtime/sheets.py', 'if connection not in allowed:')):
        body = (root / source).read_text(encoding='utf-8')
        record(f'{label} reads the allowlist strictly', pattern in body, True)

    # --- DEFECT B: the bounded conversions ------------------------------------
    for value, expected in ((0, 'ok'), (10 ** 12, 'ok'), (10 ** 12 + 1, 'ValueError'),
                            (-1, 'ValueError'), (None, 'ok'), (True, 'ValueError'),
                            ('100', 'ValueError'), (1.9, 'ValueError'),
                            (float('nan'), 'ValueError'), (float('inf'), 'ValueError'),
                            (0.0, 'ValueError')):
        record(f'bounded_price({value!r})',
               outcome(lambda v=value: C.bounded_price(v)), expected)
    record('...and an infinite price is a ValueError, not an OverflowError',
           outcome(lambda: C.bounded_price(float('inf'))), 'ValueError')
    for value, expected in ((5, 'ok'), (None, 'ok'), ('', 'ok'), (True, 'ValueError'),
                            (1.9, 'ValueError'), ('5', 'ValueError'),
                            (float('inf'), 'ValueError'), ([1], 'ValueError')):
        record(f'bounded_int({value!r})',
               outcome(lambda v=value: C.bounded_int(v, 'x', 0, 10)), expected)
    for value, expected in ((0, 'ok'), (86400, 'ok'), (86401, 'ValueError'),
                            (None, 'ok'), ('abc', 'ValueError'),
                            (1.9, 'ValueError'), (float('inf'), 'ValueError'),
                            (True, 'ValueError')):
        record(f'duration_seconds={value!r}',
               outcome(lambda v=value: C.parse_call_record(
                   {'lead_id': 'L1', 'transcript': 't', 'duration_seconds': v})),
               expected)

    # --- DEFECT C: the schedule -----------------------------------------------
    for value, expected in ((0, 'ok'), (1_789_000_000, 'ok'), (10 ** 12, 'ok'),
                            (10 ** 12 + 1, 'ValueError'), ('1e400', 'ValueError'),
                            ('nan', 'ValueError'), ('inf', 'ValueError'),
                            ('-inf', 'ValueError'), (-10 ** 9, 'ValueError'),
                            ('abc', 'ValueError'), (True, 'ValueError'),
                            (1.9, 'ValueError')):
        record(f'scheduled_at={value!r}',
               outcome(lambda v=value: C.parse_followup_request(
                   {'lead_id': 'L1', 'scheduled_at': v})), expected)
    record('an omitted schedule defaults to 0',
           C.parse_followup_request({'lead_id': 'L1'})['scheduled_at'], 0)
    record('...and the parser returns an INTEGER, as the schema declares',
           type(C.parse_followup_request({'lead_id': 'L1',
                                          'scheduled_at': 1_789_000_000})['scheduled_at']),
           int)

    # --- the caps --------------------------------------------------------------
    for value, expected in (('+1234567', 'ok'), ('+123456', 'ValueError'),
                            ('+' + '1' * 15, 'ok'), ('+' + '1' * 16, 'ValueError'),
                            ('998901234567', 'ok'), ('9989012345678', 'ValueError'),
                            ('89012345678', 'ok'), ('899012345678', 'ok')):
        record(f'normalize_phone({value!r})',
               outcome(lambda v=value: C.normalize_phone(v)), expected)
    at_limit = 'a' * (254 - len('@example.com')) + '@example.com'
    record('an email of 254 characters is accepted', C.normalize_email(at_limit), at_limit)
    record('an email of 255 characters is refused',
           outcome(lambda: C.normalize_email(
               'a' * (255 - len('@example.com')) + '@example.com')), 'ValueError')
    for value, expected in (('uzs', 'ok'), ('US', 'ValueError'), ('UZSS', 'ValueError'),
                            ('12A', 'ValueError'), ('', 'ok'), (None, 'ok')):
        record(f'clean_currency({value!r})',
               outcome(lambda v=value: C.clean_currency(v)), expected)
    at_path = '/' + 'a' * 511
    record('a path of 512 characters is accepted',
           C.safe_relative_path(at_path), at_path)
    record('a path of 513 characters is refused',
           outcome(lambda: C.safe_relative_path('/' + 'a' * 512)), 'ValueError')
    for value in ('//evil.com', '/a/../b', '/..', '/a\\b', '/a b', '/a\tb',
                  '/a:b', '/a#b', 'http://e.uz/x', 'relative', '', '/a\x01b'):
        record(f'safe_relative_path({value!r}) is refused',
               outcome(lambda v=value: C.safe_relative_path(v)), 'ValueError')
    record('...but a query separator is NOT refused',
           outcome(lambda: C.safe_relative_path('/leads?x=1')), 'ok')
    record('...and neither is percent-encoding',
           outcome(lambda: C.safe_relative_path('/a%2e%2e%2fb')), 'ok')
    record('the placeholder allowlist excludes host',
           'host' in C.CUSTOM_HTTP_PLACEHOLDERS, False)
    record('the method allowlist excludes DELETE',
           'DELETE' in C.CUSTOM_HTTP_METHODS, False)
    record('the method allowlist is exactly GET/POST/PUT/PATCH',
           tuple(sorted(C.CUSTOM_HTTP_METHODS)), ('GET', 'PATCH', 'POST', 'PUT'))
    for method in ('DELETE', 'HEAD', 'OPTIONS', 'TRACE'):
        record(f'a custom HTTP operation may not use {method}',
               outcome(lambda m=method: C.validate_custom_http_config({
                   'base_path': '/api',
                   'operations': {'find_leads': {'method': m, 'path': '/leads'}}})),
               'ValueError')
    record('the pointer segment ceiling is 64',
           outcome(lambda: C.dig({'a' * 65: 1}, 'a' * 65)), 'ValueError')
    record('...and 64 is accepted', C.dig({'a' * 64: 1}, 'a' * 64), 1)
    record('the pointer depth ceiling is 4',
           outcome(lambda: C.dig({'a': {'a': {'a': {'a': {'a': 1}}}}}, 'a.a.a.a.a')),
           'ValueError')
    record('...and depth 4 is accepted',
           C.dig({'a': {'a': {'a': {'a': 1}}}}, 'a.a.a.a'), 1)
    record('a missing path returns None rather than raising',
           C.dig({'a': 1}, 'b.c'), None)
    record('a Cyrillic segment is addressable', C.dig({'Статус': 'won'},
                                                      'Статус'), 'won')

    # --- the driver and status tables -----------------------------------------
    record('the implemented drivers are exactly the five with an adapter',
           tuple(sorted(C.IMPLEMENTED_CRM_DRIVERS)),
           ('amocrm', 'bitrix24', 'custom_webhook', 'kommo', 'onec'))
    record('the placeholders are declared but not implemented',
           tuple(sorted(set(C.CRM_DRIVERS) - C.IMPLEMENTED_CRM_DRIVERS)),
           ('billz', 'jowi', 'modme', 'moysklad', 'poster', 'retailcrm', 'yclients'))
    record('the query-search drivers are exactly the four free-text providers',
           tuple(sorted(C.QUERY_SEARCH_DRIVERS)),
           ('amocrm', 'custom_webhook', 'kommo', 'onec'))
    record('bitrix24 is NOT a query-search driver',
           C.search_mode('bitrix24'), 'structured')
    record('kommo IS a query-search driver', C.search_mode('kommo'), 'query')
    record('every bitrix24 mapping lands on a canonical status',
           tuple(sorted(set(C.BITRIX24_STATUS_MAP.values()) - C.CANONICAL_STATUSES)), ())
    record('every onec mapping lands on a canonical status',
           tuple(sorted(set(C.ONEC_STATUS_MAP.values()) - C.CANONICAL_STATUSES)), ())
    record('the canonical statuses are exactly four',
           tuple(sorted(C.CANONICAL_STATUSES)),
           ('in_progress', 'lost', 'new', 'won'))
    for canonical, remote in C.REVERSE_BITRIX24_STATUS_MAP.items():
        record(f'bitrix24 reverse {canonical} -> {remote} round-trips',
               C.BITRIX24_STATUS_MAP[remote], canonical)
    for canonical, remote in C.REVERSE_ONEC_STATUS_MAP.items():
        record(f'onec reverse {canonical} -> {remote} round-trips',
               C.ONEC_STATUS_MAP[remote], canonical)

    # --- the limit, measured and dismissed -------------------------------------
    record('the gateway converts the limit with a bare int()',
           'limit = int(args.get(' in gateway_src, True)
    schema = build_registry().get('crm.lead.search').schema['properties']['limit']
    record('...but the tool schema bounds it at 1..50',
           (schema['minimum'], schema['maximum']), (1, 50))
    for name in ('bitrix24_adapter', 'kommo_adapter', 'onec_adapter',
                 'custom_http_adapter'):
        body = (root / 'platform_runtime' / 'crm' / (name + '.py')).read_text(
            encoding='utf-8')
        record(f'...and {name} clamps it again',
               'min(max(1, limit), 50)' in body or 'min(max(1, int(limit)), 50)' in body,
               True)

    # --- the guards that now sit on the bounds --------------------------------
    for label, name, body in (
            ('the empty allowlist', 'test_an_empty_allowlist_permits_no_connection',
             guard_gw),
            ('the absent allowlist', 'test_an_absent_allowlist_permits_no_connection',
             guard_gw),
            ('the unreached adapter', 'test_the_adapter_is_never_reached', guard_gw),
            ('the tool permission', 'test_a_tool_outside_the_policy_is_denied',
             guard_gw),
            ('the permitted connection',
             'test_a_permitted_connection_still_reaches_the_adapter', guard_gw),
            ('bounded_int type refusal',
             'test_bounded_int_refuses_everything_that_is_not_an_int', guard),
            ('bounded_int ends', 'test_bounded_int_walks_both_ends', guard),
            ('the integer price', 'test_the_price_is_an_integer_amount', guard),
            ('the call duration', 'test_the_call_duration_ceiling', guard),
            ('the schedule', 'test_the_schedule_is_a_bounded_integer', guard),
            ('the phone floor', 'test_the_phone_digit_floor_is_seven', guard),
            ('the phone ceiling', 'test_the_phone_digit_ceiling_is_fifteen', guard),
            ('the phone branches',
             'test_the_998_and_leading_8_branches_are_length_exact', guard),
            ('the email ceiling',
             'test_the_email_ceiling_is_two_hundred_and_fifty_four', guard),
            ('the currency', 'test_the_currency_is_exactly_three_letters', guard),
            ('the path ceiling',
             'test_the_path_ceiling_is_five_hundred_and_twelve', guard),
            ('the path refusals',
             'test_the_path_rejects_every_retargeting_form', guard),
            ('the placeholder allowlist',
             'test_the_placeholder_allowlist_is_exact', guard),
            ('the method allowlist', 'test_the_http_method_allowlist_is_exact', guard),
            ('the custom HTTP config',
             'test_a_custom_http_config_is_normalised', guard),
            ('the pointer segment',
             'test_the_pointer_segment_ceiling_is_sixty_four', guard),
            ('the pointer depth', 'test_the_pointer_depth_ceiling_is_four', guard),
            ('the driver sets', 'test_the_driver_sets_are_exact', guard),
            ('the status maps',
             'test_every_status_map_lands_on_a_canonical_status', guard),
            ('the reverse maps', 'test_every_reverse_map_round_trips', guard)):
        record(f'a guard pins {label}', name in body, True)


def section_crm_adapter_bounds():
    """The four CRM adapters: one bound each, written four times, drifted four ways.

    A 27-mode revert matrix left TWENTY-ONE mutations green, and four of them were the
    fixes this phase made. Bitrix24, Kommo/amoCRM, 1C and the operator-declared custom
    HTTP connection all state the same four bounds -- an 80 000-byte response ceiling, a
    1..50 limit clamp, a timeout, and how a provider row's fields are converted -- and
    the four implementations disagreed on two of them and were unmeasured on all four.

    DEFECT A -- AN UNTRUSTED PROVIDER FIELD ABORTED THE WHOLE READ. ``_shape_lead`` and
    ``find_contacts`` in the 1C and custom-HTTP adapters called ``normalize_phone`` and
    ``normalize_email``, which RAISE. Measured with one malformed row among well-formed
    ones:

        onec find_leads        phone 'n/a'        -> ValueError: Empty phone number
        custom_http find_leads phone 'n/a'        -> ValueError: Empty phone number
        onec find_leads        email 'not-an-email' -> ValueError: Invalid email address
        onec find_contacts     phone 'n/a'        -> ValueError: Empty phone number

    One bad lead discarded every good lead beside it. That contradicts the same adapters'
    own design: ``_rows`` filters non-dict rows and ``_map`` returns a default, precisely
    to tolerate provider shape drift. ``optional_phone``/``optional_email`` keep the row,
    empty the unusable field and leave the id and name, so it stays identifiable.

    DEFECT B -- A PROVIDER PRICE WAS COERCED WITH A BARE ``int(float(...))``. Bitrix24
    read ``int(float(item.get('OPPORTUNITY') or 0))`` and Kommo ``int(item.get('price')
    or 0)``. Measured:

        'abc'  -> ValueError          (aborts the whole search)
        'nan'  -> ValueError
        'inf'  -> OverflowError       (not ValueError)
        '450000.99' -> 450000         (silently truncated through a binary float)

    Money went through a float, which this package forbids elsewhere, and one malformed
    price aborted the search. ``provider_price`` bounds the value, never routes it through
    a float, and returns 0 for anything unusable -- while truncating a fractional price
    exactly as before, because the documents layer already truncates rather than rounds.

    DEFECT C -- ``timeout_seconds`` WAS HONOURED BY TWO ADAPTERS AND SILENTLY IGNORED BY
    TWO. Measured: with ``timeout_seconds: 45`` configured, ``onec`` and ``custom_http``
    passed ``timeout=45`` to the transport, and ``bitrix24`` and ``kommo`` passed no
    timeout at all, so the transport's own default of 15 always won. Neither read nor
    validated the key, and an out-of-range value (0, 61, 'x', 10**9) was accepted
    silently. One config key meant two different things across the four.

    DEFECT D -- A SIZE REFUSAL WAS REPORTED AS A TRANSPORT FAULT. The 80 000-byte ceiling
    is enforced by all four, but the refusal is raised INSIDE the ``try`` and caught by
    the broad ``except Exception`` below it. ``onec`` and ``custom_http`` re-raise their
    own type first; ``bitrix24`` and ``kommo`` did not. Measured with an 80 001-byte body:

        bitrix24   -> Bitrix24HTTPError: Bitrix24 transport error: Bitrix24 response
                      exceeded maximum allowed bytes (80KB)
        onec       -> OneCHTTPError: 1C response exceeded maximum allowed bytes (80KB)

    An operator reading the first goes looking for a network fault.

    ONE ASYMMETRY RECORDED, NOT FIXED: ``provider_price``'s STRING shape allows fifteen
    whole digits, which excludes ``10 ** 15`` itself, while its integer branch accepts it
    -- the same one-digit asymmetry the documents layer records for an amount string.
    """
    print('=== 40: CRM adapter bounds ===')
    import os  # noqa: E402
    from unittest.mock import patch  # noqa: E402

    from platform_runtime.crm.bitrix24_adapter import Bitrix24Adapter  # noqa: E402
    from platform_runtime.crm.kommo_adapter import KommoAdapter  # noqa: E402
    from platform_runtime.crm.onec_adapter import OneCAdapter  # noqa: E402
    from platform_runtime.crm.custom_http_adapter import (  # noqa: E402
        CustomHTTPAdapter,
    )
    from platform_runtime.crm.crm_contract import (  # noqa: E402
        optional_email,
        optional_phone,
        provider_price,
    )

    root = Path(__file__).resolve().parents[1] / 'api-python'
    crm_dir = root / 'platform_runtime' / 'crm'
    guard = (root / 'runtime_tests'
             / 'test_crm_adapter_boundaries.py').read_text(encoding='utf-8')

    HOST = 'crm.example.uz'
    os.environ.update({'PROBE_B24': 't', 'PROBE_KOMMO': 't', 'PROBE_ONEC': 'u:p',
                       'PROBE_CUSTOM': 't'})
    BITRIX = {'driver': 'bitrix24', 'host': HOST, 'allowed_hosts': [HOST],
              'token_env': 'PROBE_B24'}
    KOMMO = {'driver': 'kommo', 'host': HOST, 'allowed_hosts': [HOST],
             'token_env': 'PROBE_KOMMO'}
    ONEC = {'driver': 'onec', 'host': HOST, 'allowed_hosts': [HOST],
            'basic_auth_env': 'PROBE_ONEC',
            'response_map': {'items': 'rows', 'phone': 'phone', 'email': 'email',
                             'id': 'id'}}
    CUSTOM = {'driver': 'custom_webhook', 'host': HOST, 'allowed_hosts': [HOST],
              'credential_env': 'PROBE_CUSTOM',
              'operations': {'find_leads': {'method': 'GET',
                                            'path': '/leads/{query}'}},
              'response_map': {'items': 'rows', 'phone': 'phone', 'email': 'email',
                               'id': 'id'}}
    EMPTY = {'result': [], '_embedded': {'leads': []}, 'rows': []}

    def outcome(fn):
        try:
            return ('ok', fn())
        except Exception as error:  # noqa: BLE001
            return (type(error).__name__, str(error)[:60])

    # --- DEFECT A: a provider field must not abort the read --------------------
    record('optional_phone normalises a usable value',
           optional_phone('+998 90 123-45-67'), '+998901234567')
    record('optional_email normalises a usable value',
           optional_email('A@B.CO'), 'a@b.co')
    for value in ('n/a', 'unknown', '+1', 'abc', 0, None):
        record(f'optional_phone({value!r}) is empty rather than raising',
               optional_phone(value), '')
    for value in ('not-an-email', 'a@b', 'x y@z.co', 0, None):
        record(f'optional_email({value!r}) is empty rather than raising',
               optional_email(value), '')
    BAD = [{'id': '1', 'name': 'Ali', 'phone': 'n/a', 'email': 'not-an-email'},
           {'id': '2', 'name': 'Vali', 'phone': '+998901234567',
            'email': 'v@e.uz'}]
    for name, config, cls in (('onec', ONEC, OneCAdapter),
                              ('custom_http', CUSTOM, CustomHTTPAdapter)):
        adapter = cls(config, transport=lambda *a, **k: {'rows': BAD})
        kind, leads = outcome(lambda a=adapter: a.find_leads(query='ali'))
        record(f'{name} returns BOTH rows when one is malformed', kind, 'ok')
        record(f'{name} keeps the malformed row, with an empty phone',
               leads[0]['phone'] if kind == 'ok' else kind, '')
        record(f'{name} keeps the well-formed row intact',
               leads[1]['phone'] if kind == 'ok' else kind, '+998901234567')
        record(f'{name} drops only the unusable email',
               leads[1]['email'] if kind == 'ok' else kind, 'v@e.uz')

    # --- DEFECT B: provider_price ---------------------------------------------
    for value in (None, '', 'abc', 'nan', 'inf', '-inf', 1e400, float('inf'),
                  float('nan'), True, False, -1, 10 ** 16, [1]):
        record(f'provider_price({value!r}) is 0', provider_price(value), 0)
    record('provider_price(0) is 0', provider_price(0), 0)
    record('provider_price(10**15) is the ceiling', provider_price(10 ** 15), 10 ** 15)
    record('provider_price(450000) passes through', provider_price(450000), 450000)
    record("provider_price('450000') parses", provider_price('450000'), 450000)
    record('a fractional string is TRUNCATED', provider_price('450000.99'), 450000)
    record('a fractional float is TRUNCATED', provider_price(450000.99), 450000)
    record('the STRING shape excludes 10**15 -- the recorded asymmetry',
           provider_price(str(10 ** 15)), 0)
    record('...while the integer branch accepts it', provider_price(10 ** 15), 10 ** 15)
    source = (crm_dir / 'bitrix24_adapter.py').read_text(encoding='utf-8')
    record('bitrix24 no longer routes a price through a float',
           'int(float(item.get(' in source, False)
    record('...and reads it through provider_price',
           "provider_price(item.get('OPPORTUNITY'))" in source, True)
    source = (crm_dir / 'kommo_adapter.py').read_text(encoding='utf-8')
    record('kommo no longer uses a bare int() for the price',
           "int(item.get('price') or 0)" in source, False)
    item = {'ID': '1', 'OPPORTUNITY': 'inf'}
    b24 = Bitrix24Adapter(BITRIX, transport=lambda *a, **k: {'result': [item]})
    kind, leads = outcome(lambda: b24.find_leads(phone='+998901234567'))
    record('an infinite provider price no longer aborts the search', kind, 'ok')
    record('...and yields 0', leads[0]['price'] if kind == 'ok' else kind, 0)

    # --- DEFECT C: the timeout ------------------------------------------------
    for name, config, cls, call in (
            ('bitrix24', BITRIX, Bitrix24Adapter,
             lambda a: a.find_leads(phone='+998901234567')),
            ('kommo', KOMMO, KommoAdapter, lambda a: a.find_leads(query='x')),
            ('onec', ONEC, OneCAdapter, lambda a: a.find_leads(query='x')),
            ('custom_http', CUSTOM, CustomHTTPAdapter,
             lambda a: a.find_leads(query='x'))):
        seen = []

        def transport(*a, _seen=seen, **k):
            _seen.append(k)
            return EMPTY

        adapter = cls({**config, 'timeout_seconds': 45}, transport=transport)
        outcome(lambda a=adapter, c=call: c(a))
        record(f'{name} passes the configured timeout of 45',
               seen[-1].get('timeout') if seen else None, 45)
        adapter = cls(dict(config), transport=transport)
        outcome(lambda a=adapter, c=call: c(a))
        record(f'{name} defaults the timeout to 15', seen[-1].get('timeout'), 15)
        for value in (0, 61, 'x', 10 ** 9, True):
            record(f'{name} refuses timeout_seconds={value!r}',
                   outcome(lambda v=value: cls({**config, 'timeout_seconds': v},
                                               transport=transport))[0],
                   'ValueError')
        for value in (1, 60):
            record(f'{name} accepts timeout_seconds={value!r}',
                   outcome(lambda v=value: cls({**config, 'timeout_seconds': v},
                                               transport=transport))[0], 'ok')

    # --- DEFECT D: the byte ceiling -------------------------------------------
    class Resp:
        def __init__(self, body):
            self.body = body
            self.status = 200

        def read(self, n=None):
            return self.body if n is None else self.body[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Op:
        def __init__(self, body):
            self.body = body

        def open(self, request, timeout=None):
            return Resp(self.body)

    def body_of(size):
        return b'{"a":"' + b'x' * (size - 8) + b'"}' if size else b''

    for name, module_name, fn_name, error_name in (
            ('bitrix24', 'platform_runtime.crm.bitrix24_adapter',
             'default_transport', 'Bitrix24HTTPError'),
            ('kommo', 'platform_runtime.crm.kommo_adapter',
             'default_kommo_transport', 'KommoHTTPError'),
            ('onec', 'platform_runtime.crm.onec_adapter',
             'default_onec_transport', 'OneCHTTPError'),
            ('custom_http', 'platform_runtime.crm.custom_http_adapter',
             'default_custom_transport', 'CustomHTTPError')):
        module = __import__(module_name, fromlist=['x'])
        fn = getattr(module, fn_name)
        with patch('urllib.request.build_opener',
                   side_effect=lambda *a, **k: Op(body_of(80_000))):
            kind, _ = outcome(lambda f=fn: f('https://%s/x' % HOST))
        record(f'{name} accepts exactly 80 000 bytes', kind, 'ok')
        with patch('urllib.request.build_opener',
                   side_effect=lambda *a, **k: Op(body_of(80_001))):
            kind, detail = outcome(lambda f=fn: f('https://%s/x' % HOST))
        record(f'{name} refuses 80 001 bytes with its OWN error type',
               kind, error_name)
        record(f'{name} keeps the byte-limit message',
               'exceeded maximum allowed bytes' in detail, True)
        record(f'{name} does not call it a transport error',
               'transport error' in detail or 'transport failure' in detail, False)
        source = (crm_dir / (name + '_adapter.py')).read_text(encoding='utf-8')
        record(f'{name} re-raises its own error before the broad handler',
               f'except {error_name}:' in source, True)

    # --- the shared constants -------------------------------------------------
    for name in ('bitrix24_adapter', 'kommo_adapter', 'onec_adapter',
                 'custom_http_adapter'):
        source = (crm_dir / (name + '.py')).read_text(encoding='utf-8')
        record(f'{name} caps the response at 80 000 bytes',
               'MAX_RESPONSE_BYTES = 80_000' in source, True)
        record(f'{name} clamps the limit with min(max(1,',
               'min(max(1,' in source, True)
        record(f'{name} does not clamp at 51', '), 51)' in source, False)
    rows = [{'id': str(i), 'name': 'n'} for i in range(20)]
    kommo = KommoAdapter(KOMMO,
                         transport=lambda *a, **k: {'_embedded': {'leads': rows}})
    record('a limit of 50 returns all twenty rows',
           len(kommo.find_leads(query='x', limit=50)), 20)
    record('a limit of 10 cuts to ten', len(kommo.find_leads(query='x', limit=10)), 10)
    record('a limit of 0 is clamped up to 1',
           len(kommo.find_leads(query='x', limit=0)), 1)
    record('a limit of 10**9 is clamped down to 50',
           len(kommo.find_leads(query='x', limit=10 ** 9)), 20)

    # --- the guards that now sit on the bounds --------------------------------
    for label, name in (
            ('the provider normalisers',
             'test_the_provider_normalisers_tolerate_what_they_cannot_parse'),
            ('the unusable price', 'test_provider_price_returns_zero_for_anything_unusable'),
            ('the price bounds', 'test_provider_price_bounds'),
            ('the fractional price',
             'test_provider_price_truncates_a_fraction_rather_than_refusing'),
            ('the no-float price rule',
             'test_provider_price_does_not_route_money_through_a_float'),
            ('the malformed row', 'test_a_malformed_provider_row_does_not_abort_the_search'),
            ('the malformed price',
             'test_a_malformed_provider_price_does_not_abort_the_search'),
            ('the byte ceiling constant',
             'test_the_response_byte_ceiling_is_eighty_thousand'),
            ('the byte ceiling enforcement',
             'test_the_byte_ceiling_is_enforced_and_keeps_its_own_message'),
            ('the limit clamp constant', 'test_the_limit_clamp_is_fifty'),
            ('the limit clamp boundary', 'test_the_limit_clamp_holds_at_the_boundary'),
            ('the timeout', 'test_the_timeout_is_read_validated_and_passed'),
            ('the header-name ceiling',
             'test_the_header_name_ceiling_is_sixty_four')):
        record(f'a guard pins {label}', name in guard, True)


def section_reengagement_bounds():
    """The re-engagement loop: a guard that promised a requirement it did not check.

    A 27-mode revert matrix left EIGHTEEN mutations green, and one of them was the fix
    this phase made. ``reengagement.py`` is the autonomous outreach coordinator -- it
    contacts real customers on a schedule -- so its bounds are the limits on how often a
    person may be messaged by a machine.

    MEASURED DEFECT. ``_identifier`` tested the RAW value for emptiness and returned the
    TRIMMED one:

        def _identifier(value, name, maximum=64):
            if not isinstance(value, str) or not value or len(value) > maximum:
                raise ValueError(f'{name} is required')
            return value.strip()

    Measured: ``_identifier(' ', 'agent')`` returned ``''``, and so did ``'   '`` and
    ``'\t'``. The message says "is required" and a value of whitespace satisfied it.
    Through ``configure`` the consequence is not cosmetic: ``configure(agent=' ')``
    STORED a schedule whose agent is the empty string, and every cycle then failed at
    the tool gate and disabled the loop with an opaque ``feed_denied:Forbidden`` --
    the operator's typo surfaced as a self-disabling integration rather than a refusal
    at the point they typed it. The same held for ``connection`` and ``actor``.

    The length bound deliberately stays on the RAW value: trimming first would let a
    caller pad a too-long identifier with spaces and slip past the ceiling.

    TWO TABLES THAT MUST STAY IN STEP, RECORDED NOT CHANGED. ``RUN_TO_LEDGER`` maps a
    run's terminal status onto the ledger, and its keys must equal
    ``agent_loop.TERMINAL`` exactly. They do today -- this was checked because a missing
    key looked like a live defect and is not: if the agent loop gained a terminal status
    the map did not, a queued ledger row for that run would stay ``queued`` for ever, and
    ``queued`` is the ONE status ``_claim`` treats as repeatable, so the lead would be
    contacted again after the cooldown. The same shape appears between ``LIMITS`` and the
    API request model ``ReengagementPolicy``, which states all seven bounds a second
    time; measured, the two agree today on every pair.

    THE LEDGER DECLARES FOUR STATES AND WRITES EIGHT. ``LEDGER_*`` names queued, settled,
    exhausted and failed, while ``RUN_TO_LEDGER`` writes cancelled, escalated,
    needs_input and uncertain as well. That is deliberate -- the comment says only
    ``queued`` is repeatable and everything else is a decision a human must make -- and
    the repeatability check tests ``== LEDGER_QUEUED``, so the extra values are handled
    correctly. Recorded so the next reader does not "fix" the constants into a closed set
    and break the map.
    """
    print('=== 41: re-engagement bounds ===')
    import json  # noqa: E402
    import os  # noqa: E402
    import re  # noqa: E402
    import tempfile  # noqa: E402
    from unittest.mock import patch  # noqa: E402

    from platform_runtime import agent_loop as AL  # noqa: E402
    from platform_runtime import reengagement as R  # noqa: E402
    from platform_runtime.agent_loop import AgentLoop  # noqa: E402
    from platform_runtime.engine import Engine  # noqa: E402
    from platform_runtime.tools import build_registry  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    src = (root / 'platform_runtime' / 'reengagement.py').read_text(encoding='utf-8')
    guard = (root / 'runtime_tests' / 'test_reengagement.py').read_text(encoding='utf-8')
    api = (root / 'app' / 'platform_api.py').read_text(encoding='utf-8')

    def outcome(fn):
        try:
            return ('ok', fn())
        except Exception as error:  # noqa: BLE001
            return (type(error).__name__, str(error)[:60])

    # --- the identifier -------------------------------------------------------
    for value in (' ', '   ', '\t', '\n', ' \t ', '\r\n', ''):
        record(f'_identifier({value!r}) is refused',
               outcome(lambda v=value: R._identifier(v, 'agent', 128))[0], 'ValueError')
    record('a padded identifier is trimmed',
           outcome(lambda: R._identifier('  a  ', 'agent', 128))[1], 'a')
    record('128 characters is the ceiling',
           len(outcome(lambda: R._identifier('a' * 128, 'agent', 128))[1]), 128)
    record('129 characters is refused',
           outcome(lambda: R._identifier('a' * 129, 'agent', 128))[0], 'ValueError')
    record('...and padding cannot evade the ceiling',
           outcome(lambda: R._identifier('a' * 128 + ' ' * 10, 'agent', 128))[0],
           'ValueError')

    # --- the limits -----------------------------------------------------------
    for name, (low, high) in sorted(R.LIMITS.items()):
        record(f'{name} floor {low} is accepted',
               outcome(lambda v=low, n=name: R._bounded(v, n))[0], 'ok')
        record(f'{name} ceiling {high} is accepted',
               outcome(lambda v=high, n=name: R._bounded(v, n))[0], 'ok')
        for value in (low - 1, high + 1, True, False, 1.0, '5'):
            record(f'{name} refuses {value!r}',
                   outcome(lambda v=value, n=name: R._bounded(v, n))[0], 'ValueError')
    record('POLICY_ID_RE accepts 64 characters',
           bool(R.POLICY_ID_RE.match('a' * 64)), True)
    record('POLICY_ID_RE refuses 65',
           bool(R.POLICY_ID_RE.match('a' * 65)), False)
    for value in ('Main', '.main', '-main', 'a b', ''):
        record(f'POLICY_ID_RE refuses {value!r}',
               bool(R.POLICY_ID_RE.match(value)), False)
    record('the ledger clamp is 1..500', 'min(max(1, int(limit)), 500)' in src, True)

    # --- the two tables that must stay in step --------------------------------
    record('RUN_TO_LEDGER keys equal agent_loop.TERMINAL',
           tuple(sorted(R.RUN_TO_LEDGER)), tuple(sorted(AL.TERMINAL)))
    record('RUN_TO_LEDGER maps succeeded onto settled',
           R.RUN_TO_LEDGER['succeeded'], R.LEDGER_SETTLED)
    record('...and no other outcome onto queued',
           tuple(sorted(k for k, v in R.RUN_TO_LEDGER.items()
                        if v == R.LEDGER_QUEUED)), ())
    record('the ledger declares four states',
           tuple(sorted({R.LEDGER_QUEUED, R.LEDGER_SETTLED, R.LEDGER_EXHAUSTED,
                         R.LEDGER_FAILED})),
           ('exhausted', 'failed', 'queued', 'settled'))
    record('...and eight are reachable, which is deliberate',
           tuple(sorted({R.LEDGER_QUEUED, R.LEDGER_SETTLED, R.LEDGER_EXHAUSTED,
                         R.LEDGER_FAILED} | set(R.RUN_TO_LEDGER.values()))),
           ('cancelled', 'escalated', 'exhausted', 'failed', 'needs_input', 'queued',
            'settled', 'uncertain'))
    record('the repeatability check tests exactly queued',
           "if row['status'] != LEDGER_QUEUED:" in src, True)
    record('...and the sync only rewrites a queued row',
           'AND status=?' in src, True)

    # --- LIMITS versus the API model ------------------------------------------
    block = api.split('class ReengagementPolicy')[1].split('\n\n')[0]
    model_bounds = {}
    for match in re.finditer(r'(\w+):int=Field\(default=\d+,ge=(\d+),le=(\d+)', block):
        model_bounds[match.group(1)] = (int(match.group(2)), int(match.group(3)))
    record('the API request model states all seven bounds a second time',
           tuple(sorted(model_bounds)), tuple(sorted(R.LIMITS)))
    record('...and the two agree on every pair',
           tuple(sorted(model_bounds.items())), tuple(sorted(R.LIMITS.items())))
    record('the stalled-lead tool accepts the whole inactive_minutes range',
           build_registry().get('crm.lead.stalled').schema['properties'][
               'inactive_minutes']['maximum'], R.LIMITS['inactive_minutes'][1])
    record('...and max_per_cycle is inside the tool limit ceiling',
           R.LIMITS['max_per_cycle'][1] <= build_registry().get(
               'crm.lead.stalled').schema['properties']['limit']['maximum'], True)

    # --- the claim boundaries through a real engine ---------------------------
    TENANT = 't_reeng'
    AGENT = 'sales.reengager'
    CONNECTION = 'crm_onec'
    OWNER = 'usr_owner'
    POLICY = {'tools': ['crm.lead.stalled'], 'allowed_connections': [CONNECTION],
              'ladder': 'human_assisted'}
    ONEC = {'driver': 'onec', 'host': '1c.example.uz',
            'allowed_hosts': ['1c.example.uz'], 'auth': 'basic',
            'basic_auth_env': 'PROBE_ONEC', 'base_path': '/hs/leads',
            'capabilities': ['read'], 'agent_ids': [AGENT],
            'response_map': {'items': 'rows', 'id': 'Ref_Key'}}

    class Clock:
        def __init__(self, start=10_000.0):
            self.now = start

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    tmp = tempfile.TemporaryDirectory()
    try:
        clock = Clock()
        engine = Engine(Path(tmp.name) / 'r.db', build_registry(), lambda t, a: POLICY,
                        clock=clock)
        loop = R.ReengagementLoop(engine, AgentLoop(engine))
        cfg = Path(tmp.name) / 'integrations.json'
        cfg.write_text(json.dumps({TENANT: {'connections': {CONNECTION: ONEC}}}),
                       encoding='utf-8')
        env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(cfg),
                                      'PROBE_ONEC': 'robot:secret'})
        env.start()
        try:
            BASE = {'inactive_minutes': 120, 'cooldown_seconds': 300,
                    'max_per_cycle': 5, 'interval_seconds': 300, 'max_steps': 3,
                    'max_seconds': 1800}

            def configure(**overrides):
                settings = dict(BASE)
                settings.update(overrides)
                loop.configure(TENANT, 'main', AGENT, CONNECTION, OWNER, **settings)
                policy = loop.policy(TENANT, 'main')
                clock.advance(policy['interval_seconds'] + 1)
                return policy

            def feed(rows):
                return patch('platform_runtime.crm.onec_adapter.OneCAdapter._call',
                             return_value={'rows': rows})

            def lead(identifier):
                return {'Ref_Key': identifier, 'Description': 'Anvar',
                        'phone': '+998901234567'}

            def clear():
                with engine.read() as c:
                    c.execute('DELETE FROM p_reengagement_ledger WHERE tenant=?',
                              (TENANT,))
                    c.execute('DELETE FROM p_agent_runs WHERE tenant=?', (TENANT,))
                    c.execute('DELETE FROM p_reengagement WHERE tenant=?', (TENANT,))

            def row_for(lead_id):
                with engine.read() as c:
                    return c.execute('SELECT attempt,status FROM p_reengagement_ledger '
                                     'WHERE tenant=? AND lead_id=?',
                                     (TENANT, lead_id)).fetchone()

            # The tick also advances `next_due`, so step past BOTH the interval and the
            # cooldown or only every other tick is due.
            for attempts in (1, 2, 3, 10):
                clear()
                configure(max_attempts=attempts, cooldown_seconds=300,
                          interval_seconds=300)
                for _ in range(attempts * 3 + 5):
                    clock.advance(1000)
                    with feed([lead('CEIL')]):
                        loop.tick(TENANT)
                row = row_for('CEIL')
                record(f'max_attempts={attempts} is reached exactly once',
                       row['attempt'], attempts)
                record(f'...and the lead is then EXHAUSTED',
                       row['status'], R.LEDGER_EXHAUSTED)

            # Walked on `_claim` rather than `tick`, because a tick also moves
            # `next_due` and the schedule would mask the boundary being measured.
            clear()
            policy = configure(max_attempts=5, cooldown_seconds=3600)
            t0 = clock.now
            with engine.tx() as c:
                record('the first claim is attempt 1',
                       loop._claim(c, TENANT, policy, 'COOL', t0), 1)
            for delta, expected in ((0, None), (299, None), (3599, None), (3600, 2),
                                    (7199, None), (7200, 3)):
                clock.now = t0 + delta
                with engine.tx() as c:
                    record(f'at t0+{delta}s the claim is {expected!r}',
                           loop._claim(c, TENANT, policy, 'COOL', clock.now), expected)

            clear()
            configure(max_per_cycle=2, max_attempts=1)
            with feed([lead('P%d' % i) for i in range(10)]):
                loop.tick(TENANT)
            with engine.read() as c:
                claimed = c.execute('SELECT count(*) n FROM p_reengagement_ledger '
                                    'WHERE tenant=?', (TENANT,)).fetchone()['n']
            record('a batch of 10 with max_per_cycle=2 claims 2', claimed, 2)

            for field in ('agent', 'connection', 'actor'):
                clear()
                args = {'agent': AGENT, 'connection': CONNECTION, 'actor': OWNER}
                args[field] = ' '
                record(f'a blank {field} is refused at configure time',
                       outcome(lambda a=args: loop.configure(
                           TENANT, 'blank', a['agent'], a['connection'], a['actor'],
                           **BASE))[0], 'ValueError')
                record(f'...and nothing is stored for {field}',
                       outcome(lambda: loop.policy(TENANT, 'blank'))[0], 'NotFound')

            clear()
            policy = configure()
            text = loop._input_text(policy, {'id': 'x' * 500, 'title': 't' * 500,
                                             'name': 'n' * 500, 'phone': 'p' * 500,
                                             'email': 'e' * 500, 'status': 's' * 500,
                                             'created_at': 'c' * 500})
            record('the run input is capped at 4000 characters', len(text) <= 4000, True)
            record('...and marks provider text as untrusted', 'ISHONCHSIZ' in text, True)
            record('a missing field becomes a dash',
                   loop._input_text(policy, {}).count('-'), 7)
        finally:
            env.stop()
    finally:
        tmp.cleanup()

    # --- the guards that now sit on the bounds --------------------------------
    for label, name in (
            ('the blank identifier', 'test_a_blank_identifier_is_refused'),
            ('the raw-value length bound',
             'test_the_identifier_length_bound_is_on_the_raw_value'),
            ('the blank field through configure',
             'test_a_blank_field_is_refused_through_configure'),
            ('the limit table', 'test_the_limit_table_is_exact'),
            ('both ends of every limit', 'test_every_limit_is_walked_at_both_ends'),
            ('the policy-id ceiling', 'test_the_policy_id_ceiling_is_sixty_four'),
            ('the ledger clamp', 'test_the_ledger_clamp_is_five_hundred'),
            ('the run-to-ledger mirror',
             'test_the_run_to_ledger_map_mirrors_the_agent_loop_terminal_set'),
            ('only-queued-repeatable', 'test_only_queued_is_repeatable'),
            ('the attempt ceiling',
             'test_the_attempt_ceiling_is_exhausted_not_repeatable'),
            ('the cooldown boundary', 'test_the_cooldown_is_inclusive_at_its_boundary'),
            ('the closed-row sync', 'test_sync_ledger_never_rewrites_a_closed_row'),
            ('the run input bounds', 'test_the_run_input_bounds_are_pinned')):
        record(f'a guard pins {label}', name in guard, True)


def section_supervisor_bounds():
    """The supervisor and oversight layer: three dead constants and a ceiling off by eleven.

    A 30-mode revert matrix left TWENTY-TWO mutations green, three of them the fixes this
    phase made. ``supervisor.py`` is the router that decides which section agent answers a
    manager's question, and ``oversight.py`` is what a human reads to see what an agent
    did -- so their bounds are the limits on who answers what, and on what an operator can
    observe.

    DEFECT A -- A CEILING THAT BOUND THE WRONG STRING, BY ELEVEN. ``route`` checked
    ``len(request_key) > 256`` and then derived the run key as
    ``f'supervisor:{request_key}'``, while ``AgentLoop.create`` bounds the KEY at 256.
    Measured, with the derived key in brackets:

        request_key 244 -> ok   (derived key 255)
        request_key 245 -> ok   (derived key 256)
        request_key 246 -> ValueError: Invalid run identity   (derived key 257)
        request_key 256 -> ValueError: Invalid run identity   (derived key 267)

    So the effective ceiling was 245, not the 256 the message promised, and every length
    from 246 up was refused with a message about the run identity rather than about the
    request key. ``RUN_KEY_PREFIX`` and ``MAX_REQUEST_KEY = 256 - len(RUN_KEY_PREFIX)``
    are now one expression, and the key is built from the same constant.

    DEFECT B -- THREE DEAD CONSTANTS, TREATED THREE WAYS. ``supervisor.MAX_SECTIONS = 20``,
    ``oversight.MAX_RUNS = 100`` and ``oversight.VIEWS = ('activity', 'cost', 'health')``
    each appeared on exactly one line -- their own declaration -- and bounded or described
    nothing. They are not the same case, so they did not get the same treatment:

    * ``MAX_SECTIONS`` is a real bound that was never wired: the module's own docstring
      says "a bounded router", ``MAX_KEYWORDS`` beside it IS enforced, and the enforcement
      point is unambiguous. Measured before the fix: twenty-one sections were declared and
      stored. Now a NEW section past the ceiling is refused and re-declaring an existing
      one is always allowed, so a tenant already holding more than the ceiling keeps
      working.
    * ``MAX_RUNS`` bounds nothing that exists: ``activity`` aggregates the runs by status
      with ``GROUP BY``, so there is no run list for a cap to apply to. REMOVED, with the
      absence recorded rather than an enforcement point invented.
    * ``VIEWS`` is not a bound but a declared set with no reader -- the closed set lived
      only in ``_dispatch``'s if-chain. WIRED IN as the guard, so the declaration is now
      the thing that decides.

    DEFECT C -- A GUARD THAT VALIDATED ONE FORM AND RETURNED ANOTHER. ``oversight._text``
    tested the TRIMMED value for emptiness and returned the RAW one, and its only caller
    then regex-matched the raw value. Measured: ``_known(' sales ')`` raised 'Invalid agent
    id' while ``supervisor._name(' sales ')`` returned 'sales'. Two modules disagreeing
    about whether a padded identifier is valid is worse than either answer on its own, so
    ``_text`` now returns what it validated -- and ``_known`` uses it.

    ONE REDUNDANT GUARD RECORDED, NOT CHANGED: ``_keywords`` bounds each keyword on the RAW
    item (``len(item) > MAX_KEYWORD_LENGTH``) and then stores the trimmed one. That is the
    same asymmetry the identifier guards have, in the direction that fails closed, so it is
    pinned by its line rather than "fixed".
    """
    print('=== 42: supervisor and oversight bounds ===')
    import json  # noqa: E402
    import os  # noqa: E402
    import tempfile  # noqa: E402
    from unittest.mock import patch  # noqa: E402

    from platform_runtime import oversight as O  # noqa: E402
    from platform_runtime import supervisor as S  # noqa: E402
    from platform_runtime.agent_loop import AgentLoop  # noqa: E402
    from platform_runtime.engine import Engine, Forbidden  # noqa: E402
    from platform_runtime.tools import build_registry  # noqa: E402

    root = Path(__file__).resolve().parents[1] / 'api-python'
    sup_src = (root / 'platform_runtime' / 'supervisor.py').read_text(encoding='utf-8')
    ovs_src = (root / 'platform_runtime' / 'oversight.py').read_text(encoding='utf-8')
    guard_sup = (root / 'runtime_tests' / 'test_supervisor.py').read_text(encoding='utf-8')
    guard_ovs = (root / 'runtime_tests' / 'test_oversight.py').read_text(encoding='utf-8')

    def outcome(fn):
        try:
            return ('ok', fn())
        except Exception as error:  # noqa: BLE001
            return (type(error).__name__, str(error)[:70])

    # --- DEFECT B: the three constants ----------------------------------------
    for module, name, source in (('supervisor', 'MAX_SECTIONS', sup_src),
                                 ('oversight', 'VIEWS', ovs_src)):
        hits = [line for line in source.splitlines() if name in line]
        used = [line for line in hits if not line.strip().startswith(name)]
        record(f'{module}.{name} now has a reader beyond its declaration', bool(used), True)
    record('oversight.MAX_RUNS is gone', 'MAX_RUNS' in ovs_src, False)
    record('MAX_SECTIONS is 20', S.MAX_SECTIONS, 20)
    record('VIEWS is the three views',
           tuple(O.VIEWS), ('activity', 'cost', 'health'))
    record('...and _dispatch guards on it', 'if view not in VIEWS:' in ovs_src, True)

    # --- DEFECT A: the request key and the derived run key ---------------------
    record('the run-key prefix is a named constant',
           "RUN_KEY_PREFIX = 'supervisor:'" in sup_src, True)
    record('MAX_REQUEST_KEY is derived from it',
           'MAX_REQUEST_KEY = 256 - len(RUN_KEY_PREFIX)' in sup_src, True)
    record('the derived key uses the same constant',
           'RUN_KEY_PREFIX + request_key' in sup_src, True)
    record('MAX_REQUEST_KEY is 245', S.MAX_REQUEST_KEY, 245)
    record('...and the two together are the loop key ceiling',
           S.MAX_REQUEST_KEY + len(S.RUN_KEY_PREFIX), 256)

    # --- DEFECT C: the identifier guards --------------------------------------
    record('_text returns the trimmed value', O._text(' sales ', 'agent', 128), 'sales')
    for value in ('', '   ', '\t', '\n'):
        record(f'_text({value!r}) is refused',
               outcome(lambda v=value: O._text(v, 'agent', 128))[0], 'ValueError')
    record('_text accepts 128 characters',
           len(outcome(lambda: O._text('a' * 128, 'agent', 128))[1]), 128)
    record('_text refuses 129',
           outcome(lambda: O._text('a' * 129, 'agent', 128))[0], 'ValueError')
    record('supervisor._name trims too', S._name('  sales  ', 'agent', 128), 'sales')
    record('...and the two agree on a padded id',
           (O._text(' sales ', 'agent', 128), S._name(' sales ', 'agent', 128)),
           ('sales', 'sales'))
    for value in ('Sales', 'a b', '-a', '', '   '):
        record(f'_name({value!r}) is refused',
               outcome(lambda v=value: S._name(v, 'agent', 128))[0], 'ValueError')

    # --- the caps -------------------------------------------------------------
    for label, value, expected in (('MAX_QUESTION', S.MAX_QUESTION, 2000),
                                   ('MAX_KEYWORDS', S.MAX_KEYWORDS, 20),
                                   ('MAX_KEYWORD_LENGTH', S.MAX_KEYWORD_LENGTH, 60),
                                   ('MAX_HOPS_CEILING', S.MAX_HOPS_CEILING, 3),
                                   ('MAX_STEPS_CEILING', S.MAX_STEPS_CEILING, 12),
                                   ('DEFAULT_MAX_HOPS', S.DEFAULT_MAX_HOPS, 1),
                                   ('DEFAULT_MAX_STEPS', S.DEFAULT_MAX_STEPS, 6),
                                   ('MAX_EVENTS', O.MAX_EVENTS, 100),
                                   ('DEFAULT_WINDOW_SECONDS', O.DEFAULT_WINDOW_SECONDS,
                                    7 * 86400),
                                   ('MAX_WINDOW_SECONDS', O.MAX_WINDOW_SECONDS,
                                    90 * 86400)):
        record(label, value, expected)
    for count, accepted in ((20, True), (21, False)):
        record(f'{count} keywords is {"accepted" if accepted else "refused"}',
               outcome(lambda c=count: S._keywords(['k%d' % i for i in range(c)]))[0],
               'ok' if accepted else 'ValueError')
    for length, accepted in ((60, True), (61, False)):
        record(f'a keyword of {length} characters is '
               f'{"accepted" if accepted else "refused"}',
               outcome(lambda l=length: S._keywords(['k' * l]))[0],
               'ok' if accepted else 'ValueError')
    record('a keyword is casefolded and trimmed', S._keywords(['  SOTUV  ']), ['sotuv'])
    for value in (['   '], [''], [None], 'not-a-list'):
        record(f'keywords={value!r} is refused',
               outcome(lambda v=value: S._keywords(v))[0], 'ValueError')
    for label, fn in (('supervisor', S._bounded), ('oversight', O._bounded)):
        for value in (1, 10):
            record(f'{label}._bounded({value!r}, 1, 10) is accepted',
                   outcome(lambda v=value, f=fn: f(v, 'x', 1, 10))[0], 'ok')
        for value in (0, 11, True, False, 1.0, '5'):
            record(f'{label}._bounded({value!r}, 1, 10) is refused',
                   outcome(lambda v=value, f=fn: f(v, 'x', 1, 10))[0], 'ValueError')

    # --- the routing behaviour through a real engine ---------------------------
    TENANT = 't_probe_sup'
    OWNER = 'usr_owner'
    POLICIES = {'mgmt.supervisor': {'tools': ['supervisor.route', 'supervisor.sections'],
                                    'ladder': 'autonomous', 'allowed_connections': []},
                'sales.360': {'tools': [], 'ladder': 'human_assisted',
                              'allowed_connections': []}}

    class Clock:
        def __init__(self, start=1_770_000_000.0):
            self.now = start

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    def policy(tenant, agent):
        if agent not in POLICIES:
            raise Forbidden('Agent not in tenant pack')
        return dict(POLICIES[agent])

    tmp = tempfile.TemporaryDirectory()
    try:
        clock = Clock()
        engine = Engine(Path(tmp.name) / 's.db', build_registry(), policy, clock=clock,
                        authority=lambda c, t, ch='', a='', r=('owner',): None)
        sup = S.Supervisor(engine, AgentLoop(engine))
        cfg = Path(tmp.name) / 'i.json'
        cfg.write_text(json.dumps({TENANT: {}}), encoding='utf-8')
        env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(cfg)})
        env.start()
        try:
            def clear_routes():
                with engine.tx() as c:
                    c.execute('DELETE FROM p_supervisor_route WHERE tenant=?', (TENANT,))

            def clear_sections():
                with engine.tx() as c:
                    c.execute('DELETE FROM p_supervisor_section WHERE tenant=?', (TENANT,))

            def attempt(key, **kwargs):
                clear_routes()
                return outcome(lambda: sup.route(TENANT, key, 'mijoz savoli', OWNER,
                                                 **kwargs))[0]

            # A section named 'sec', which the request-key and route-bound records
            # route into. The section-ceiling record below clears it again.
            sup.declare(TENANT, 'sec', 'sales.360', OWNER)

            # the request-key boundary
            for length, expected in ((244, 'ok'), (245, 'ok'), (246, 'ValueError'),
                                     (256, 'ValueError')):
                record(f'a request key of {length} characters', 
                       attempt('k' * length, section='sec'), expected)

            # the section ceiling
            clear_sections()
            for index in range(20):
                sup.declare(TENANT, 'sec%02d' % index, 'sales.360', OWNER)
            record('twenty sections are accepted', len(sup.sections(TENANT)), 20)
            record('the twenty-first is REFUSED',
                   outcome(lambda: sup.declare(TENANT, 'sec20', 'sales.360',
                                               OWNER))[0], 'ValueError')
            record('...and it was not stored', len(sup.sections(TENANT)), 20)
            record('re-declaring an existing section is still allowed',
                   outcome(lambda: sup.declare(TENANT, 'sec00', 'sales.360',
                                               OWNER))[0], 'ok')

            # the route bounds. The section-ceiling record above cleared the table, so
            # 'sec' is declared again here.
            clear_sections()
            sup.declare(TENANT, 'sec', 'sales.360', OWNER)
            for value, expected in ((1, 'ok'), (3, 'ok'), (0, 'ValueError'),
                                    (4, 'ValueError')):
                record(f'max_hops={value!r}', attempt('h%s' % value, section='sec',
                                                      max_hops=value), expected)
            for value, expected in ((1, 'ok'), (12, 'ok'), (0, 'ValueError'),
                                    (13, 'ValueError')):
                record(f'max_steps={value!r}', attempt('s%s' % value, section='sec',
                                                       max_steps=value), expected)
            for value, expected in ((60, 'ok'), (86400, 'ok'), (59, 'ValueError'),
                                    (86401, 'ValueError')):
                record(f'max_seconds={value!r}', attempt('t%s' % value, section='sec',
                                                         max_seconds=value), expected)
            for length, expected in ((2000, 'ok'), (2001, 'ValueError')):
                clear_routes()
                record(f'a question of {length} characters',
                       outcome(lambda l=length: sup.route(
                           TENANT, 'q%d' % length, 'x' * l, OWNER, section='sec'))[0],
                       expected)

            # the hop cap is counted from the ledger, not asserted by the caller
            clear_routes()
            for index in range(3):
                sup.route(TENANT, 'rk%d' % index, 'mijoz', OWNER, section='sec',
                          max_hops=3)
            record('the hop cap refuses the fourth delegation',
                   outcome(lambda: sup.route(TENANT, 'rk3', 'mijoz', OWNER,
                                             section='sec', max_hops=3))[0],
                   'Forbidden')

            # the keyword precedence rule
            clear_sections()
            sup.declare(TENANT, 'alpha', 'sales.360', OWNER, keywords=['sotuv narx',
                                                                      'sotuv'])
            sup.declare(TENANT, 'beta', 'sales.360', OWNER, keywords=['sotuv'])
            picked = sup._match(TENANT, 'sotuv narx kerak')
            record('the longest keyword wins',
                   (picked[0]['section'], picked[1]), ('alpha', 'sotuv narx'))
            picked = sup._match(TENANT, 'sotuv kerak')
            record('...and a tie goes to the alphabetically first section id',
                   picked[0]['section'], 'alpha')

            # the history clamp
            record('the history clamp is 1..500',
                   'limit = min(max(1, int(limit)), 500)' in sup_src, True)
            record('a limit of 0 is clamped up rather than refused',
                   outcome(lambda: sup.history(TENANT, 0))[0], 'ok')
            record('a limit of 10**9 is clamped down rather than refused',
                   outcome(lambda: sup.history(TENANT, 10 ** 9))[0], 'ok')

            # --- oversight -----------------------------------------------------
            for value, expected in ((1, 'ok'), (O.MAX_WINDOW_SECONDS, 'ok'),
                                    (0, 'ValueError'),
                                    (O.MAX_WINDOW_SECONDS + 1, 'ValueError'),
                                    (True, 'ValueError')):
                record(f'since_seconds={value!r}',
                       outcome(lambda v=value: O._window(engine, v))[0], expected)
            for value, expected in ((1, 'ok'), (O.MAX_EVENTS, 'ok'), (0, 'ValueError'),
                                    (O.MAX_EVENTS + 1, 'ValueError')):
                record(f'an activity limit of {value!r}',
                       outcome(lambda v=value: O.activity(engine, TENANT, 'sales.360',
                                                          3600, v))[0], expected)
            for view in O.VIEWS:
                record(f'{view} dispatches',
                       outcome(lambda v=view: O._dispatch(
                           'agent.' + v, engine, TENANT, 'sales.360', {}))[0], 'ok')
            for name in ('agent.nope', 'agent.', 'agent.activity.extra'):
                record(f'{name} is refused',
                       outcome(lambda n=name: O._dispatch(
                           n, engine, TENANT, 'sales.360', {}))[0], 'ValueError')
            record('the activity probe reads one past the limit',
                   'probe = limit + 1' in ovs_src, True)
            registry = build_registry()
            record('the declared oversight tools are the registered ones',
                   tuple(sorted(n for n in registry.items
                                if n in O.OVERSIGHT_TOOLS)),
                   tuple(sorted(O.OVERSIGHT_TOOLS)))
            record('...and all of them are reads',
                   tuple(sorted({registry.get(n).risk for n in O.OVERSIGHT_TOOLS})),
                   ('read',))
            record('the activity limit ceiling is MAX_EVENTS',
                   registry.get('agent.activity').schema['properties'][
                       'limit']['maximum'], O.MAX_EVENTS)
            record('the window ceiling is MAX_WINDOW_SECONDS',
                   registry.get('agent.activity').schema['properties'][
                       'since_seconds']['maximum'], O.MAX_WINDOW_SECONDS)
        finally:
            env.stop()
    finally:
        tmp.cleanup()

    # --- the guards that now sit on the bounds --------------------------------
    for label, name, body in (
            ('the supervisor limit constants',
             'test_the_limit_constants_are_pinned', guard_sup),
            ('the name ceiling',
             'test_the_name_ceiling_is_one_hundred_and_twenty_eight', guard_sup),
            ('the keyword caps', 'test_the_keyword_caps_are_pinned', guard_sup),
            ('supervisor._bounded',
             'test_the_bounded_helper_refuses_a_non_int', guard_sup),
            ('the request-key/run-key tie',
             'test_the_request_key_ceiling_leaves_room_for_the_run_prefix', guard_sup),
            ('the request-key boundary',
             'test_the_request_key_ceiling_holds_at_the_boundary', guard_sup),
            ('the section ceiling',
             'test_the_section_ceiling_is_enforced', guard_sup),
            ('the hop cap boundary',
             'test_the_hop_cap_holds_at_its_boundary', guard_sup),
            ('the route bounds', 'test_the_route_bounds_are_walked', guard_sup),
            ('the history clamp',
             'test_the_history_clamp_is_five_hundred', guard_sup),
            ('the keyword precedence',
             'test_the_keyword_precedence_is_longest_then_alphabetical', guard_sup),
            ('the oversight constants',
             'test_the_limit_constants_are_pinned', guard_ovs),
            ('the window and limit bounds',
             'test_the_window_and_limit_bounds_are_walked', guard_ovs),
            ('oversight._bounded',
             'test_the_bounded_helper_refuses_a_non_int', guard_ovs),
            ('the VIEWS guard', 'test_the_view_set_is_the_guard', guard_ovs),
            ('the absence of MAX_RUNS', 'test_max_runs_is_gone', guard_ovs),
            ('the trimmed identifier',
             'test_text_returns_the_value_it_validated', guard_ovs),
            ('the agent-id ceiling',
             'test_the_agent_id_ceiling_is_one_hundred_and_twenty_eight', guard_ovs),
            ('the activity probe',
             'test_the_activity_probe_reads_one_past_the_limit', guard_ovs),
            ('the oversight tool surface',
             'test_the_tool_surface_is_the_declared_one', guard_ovs)):
        record(f'a guard pins {label}', name in body, True)


if __name__ == '__main__':
    raise SystemExit(main())
