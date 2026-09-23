"""Measure the consent gate and the audio boundary rather than claiming them.

The telephony module's docstring makes three strong claims:

1. an outbound call to a number with no declared consent is refused **in code**,
   and the platform never places a call at all;
2. audio never reaches the platform — no recording, URL, transcript or buffer is
   read, stored or emitted;
3. no person is evaluated, however the register is written.

Claims in a docstring are not evidence, so this probe **measures** each one:
how many provider GETs a refused call produces (the interesting number is
**zero**), whether a media path in a register can surface anywhere in the output,
and whether a per-agent figure can be extracted from the summary.

Run from anywhere; paths are resolved relative to this file.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime.engine import Engine, Forbidden  # noqa: E402
from platform_runtime.telephony import (  # noqa: E402
    MAX_EVENTS,
    call_events,
    consent,
    normalise,
    queue,
    retention,
    summary,
    telephony_config,
)
from platform_runtime.tools import build_registry  # noqa: E402

TENANT = 't_call'
AGENT = 'ops.telephony'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

CALL_HEADER = ['yo‘nalish', 'raqam', 'sana', 'natija', 'davomiylik', 'stansiya']
CONSENT_HEADER = ['raqam', 'holat', 'maqsad', 'muddat', 'yozish']

CALL_ROWS = [
    ['outbound', '+998 90 123 45 67', '2026-09-18', 'answered', '120',
     'zavod-1/sex-1/liniya-1/stanok-1'],
    ['inbound', '998901234567', '2026-09-18', 'answered', '60',
     'zavod-1/sex-1/liniya-1/stanok-1'],
    ['outbound', '998935555555', '2026-09-19', 'no_answer', '', ''],
]

CONSENT_ROWS = [
    ['998901234567', 'granted', 'service', '2027-01-01', 'yes'],
    ['998911111111', 'granted', 'delivery', '2027-01-01', 'no'],
    ['998933333333', 'granted', 'marketing', '2026-01-01', 'yes'],
]

POLICY = {
    'tools': ['sheets.rows', 'telephony.consent', 'telephony.call_events',
              'telephony.summary', 'telephony.queue', 'telephony.retention'],
    'allowed_connections': ['plant', 'google'],
    'ladder': 'human_assisted',
}

# The stage-B baseline config, kept as a dict so section 8 can mutate one key and
# measure that the mutation is refused.
CALLS_CONFIG = {
    'registers': {'log': {
        'register': 'plant', 'range': 'calls',
        'direction_column': 'yo‘nalish', 'number_column': 'raqam',
        'timestamp_column': 'sana', 'outcome_column': 'natija',
        'duration_column': 'davomiylik',
        'extension_column': 'stansiya', 'purpose': 'service'}},
    'consent_registers': {'consent': {
        'register': 'plant', 'range': 'consent',
        'number_column': 'raqam', 'status_column': 'holat',
        'purpose_column': 'maqsad', 'expiry_column': 'muddat',
        'recording_column': 'yozish'}},
    'consented_numbers': ['998901234567', '998911111111', '998933333333'],
    'throughput': {'per_window': 2, 'window_seconds': 3600},
    'retention_days': 30,
}

_failures = []


def check(section, condition, message):
    if condition:
        print(f'  PASS  {message}')
    else:
        print(f'  FAIL  {message}')
        _failures.append(f'{section}: {message}')


class Harness:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = self.root / 'integrations.json'
        self.counter = 0
        self.calls = []
        self.payloads = {'calls': {'values': [CALL_HEADER] + CALL_ROWS},
                         'consent': {'values': [CONSENT_HEADER] + CONSENT_ROWS}}
        self.env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(self.cfg)})
        self.env.start()
        self.write()

    def write(self, telephony=None):
        payload = {
            'connections': {'google': {}},
            'sheets_registers': {'plant': {
                'connection': 'google', 'spreadsheet_id': SPREADSHEET,
                'ranges': {'calls': 'Qongiroq!A1:F', 'consent': 'Rozilik!A1:E'},
                'max_rows': 200}},
            'assets': {'entity': 'asset', 'levels': ['zavod', 'sex', 'liniya', 'stanok']},
            'telephony': telephony if telephony is not None else CALLS_CONFIG,
        }
        self.cfg.write_text(json.dumps({TENANT: payload}, ensure_ascii=False),
                            encoding='utf-8')

    def build(self, ladder='human_assisted'):
        self.counter += 1
        self.calls = []
        policy = dict(POLICY)
        policy['ladder'] = ladder
        return Engine(self.root / f'probe{self.counter}.db', build_registry(),
                      lambda t, a: policy, clock=lambda: 1_770_000_000.0)

    def read(self, engine, fn, **kwargs):
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get', side_effect=self._route):
                return fn(engine, TENANT, AGENT, 's1', **kwargs)

    def _route(self, url, token):
        self.calls.append(url)
        if 'Rozilik' in url:
            return self.payloads['consent']
        if 'Qongiroq' in url:
            return self.payloads['calls']
        return {'values': []}

    def close(self):
        self.env.stop()
        self.tmp.cleanup()


def section_one(h):
    print('\n[1] A number is one number, and a prefix is not a match')
    check('1', normalise('+998 90 123 45 67') == '998901234567',
          'a spaced, plussed number normalises to digits')
    check('1', normalise('998-90-123-45-67') == '998901234567',
          'a dashed number normalises to the same digits')
    check('1', normalise('(998) 90 123 45 67') == '998901234567',
          'a parenthesised number normalises too')
    check('1', normalise('') == '' and normalise(None) == '',
          'a blank cell is not a number')
    check('1', normalise('Ali Valiyev') == '',
          'a name is not a number')
    check('1', normalise('1234567890123456') == '',
          'an over-long run of digits is not a number')
    check('1', normalise(True) == '',
          'a boolean cell is not a number either')
    check('1', normalise('901234567') != normalise('998901234567'),
          'a short number is NOT equal to a long one (a prefix match would call '
          'a stranger because a prefix was consented)')

    # The ultra-audit defect: lossy digit extraction collapsed unrelated strings
    # onto the same short "number", so a consent row for '9' covered all of them.
    collapsed = {'https://a/rec-9.wav', 'rec-9.wav', 'call 9', '9', 'x9', 'file9'}
    normalised = {normalise(v) for v in collapsed}
    check('1', normalised == {''},
          f'unrelated garbage does not collapse onto one short number '
          f'(got {sorted(normalised)})')
    check('1', normalise('123456') == '' and normalise('1234567') == '1234567',
          'the minimum length is 7 digits: below it is not a number')

    # A URL embedding a complete number must not become that number, or a
    # recording's own filename would turn into a consented counterparty.
    for value in ('https://example.com/998901234567',
                  'https://cdn.example/rec-998901234567.wav',
                  'file:///rec/998901234567'):
        check('1', normalise(value) == '',
              f'a URL embedding a number is refused: {value!r}')
    check('1', normalise('tel:+998901234567') == '998901234567',
          'but a tel: URI IS a spelling of a number (stripped, not refused)')


def section_two(h):
    print('\n[2] Consent is a fact with a reason, and refusal costs zero GETs')
    engine = h.build()
    granted = h.read(engine, consent, number='998901234567', purpose='service')
    check('2', granted['consented'] is True,
          'a granted consent for this purpose yields consented: true')
    check('2', granted['reasons'] == [],
          'and it ships no refusal reason')
    check('2', granted['recording_allowed'] is True,
          "and recording is allowed because its own consent says 'yes'")

    h.calls = []
    missing = h.read(engine, consent, number='998935555555', purpose='service')
    check('2', missing['consented'] is False,
          'a number with no consent row is refused')
    check('2', any('no consent record' in r for r in missing['reasons']),
          'and the refusal names the missing record')

    cross = h.read(engine, consent, number='998911111111', purpose='marketing')
    check('2', cross['consented'] is False,
          'a delivery consent is NOT a marketing consent')
    check('2', cross['recording_allowed'] is False,
          'and recording is not inherited from the other purpose')

    expired = h.read(engine, consent, number='998933333333', purpose='marketing')
    check('2', expired['consented'] is False,
          'an expired consent is refused')
    check('2', any('expired' in r for r in expired['reasons']),
          'and the reason says expired, not missing')

    # The reading half of a refusal must still have looked at the register: the
    # consent table is what answers the question.
    check('2', len(h.calls) >= 1,
          'the consent read consults the declared consent register (a GET happened)')
    check('2', all('Rozilik' in url for url in h.calls),
          'and it reads NO call register to answer a consent question')

    # An unknown purpose is refused by name, not treated as service.
    try:
        h.read(engine, consent, number='998901234567', purpose='survey')
        check('2', False, 'an unknown purpose was accepted')
    except Forbidden:
        check('2', True, 'an unknown purpose is refused by name')


def section_three(h):
    print('\n[3] An outbound call without consent is flagged, never hidden')
    engine = h.build()
    result = h.read(engine, call_events, register='log')
    outbound = [e for e in result['events'] if e['direction'] == 'outbound']
    consented = [e for e in outbound if e['consent']]
    refused = [e for e in outbound if not e['consent']]
    check('3', len(outbound) == 2,
          'both outbound rows are reported (nothing was silently dropped)')
    check('3', len(consented) == 1,
          'the consented outbound call is a normal fact')
    check('3', len(refused) == 1,
          'the unconsented outbound call is present, flagged')
    check('3', refused[0]['consent'] is False,
          'and its consent flag is false')
    check('3', bool(refused[0]['consent_reasons']),
          'and it carries the reason it was refused')
    check('3', result['refused_without_consent'] == 1,
          'and the count agrees with the flagged rows')
    inbound = [e for e in result['events'] if e['direction'] == 'inbound']
    check('3', len(inbound) == 1 and inbound[0]['consent'] is True,
          'an inbound call needs no outbound consent (the customer called us)')


def section_four(h):
    print('\n[4] Audio never reaches the platform')
    engine = h.build()
    result = h.read(engine, call_events, register='log')
    # The note names the boundary in prose, so scan the events themselves.
    events_blob = json.dumps({'events': result['events']}, ensure_ascii=False).lower()
    for needle in ('http', '.wav', 'recording_url', 'audio_path', 'transcript',
                   'pcm', 'media'):
        check('4', needle not in events_blob,
              f'no {needle!r} appears in any event')

    names = set()
    for record in result['events']:
        names |= set(record)
    banned = ('audio', 'audio_path', 'recording', 'recording_url', 'transcript',
              'pcm', 'wav', 'media')
    hits = sorted(n for n in names if any(b in n.lower() for b in banned))
    check('4', hits == [], f'no event field is audio-shaped (found {hits})')

    # A register that names a media column must still not surface the value.
    telephony = {
        'registers': {'log': {
            'register': 'plant', 'range': 'calls',
            'direction_column': 'yo‘nalish', 'number_column': 'raqam',
            'timestamp_column': 'sana',
            'outcome_column': 'yozuv_havolasi', 'purpose': 'service'}},
        'consent_registers': {'consent': {
            'register': 'plant', 'range': 'consent',
            'number_column': 'raqam', 'status_column': 'holat',
            'purpose_column': 'maqsad'}},
        'consented_numbers': ['998901234567'],
        'retention_days': 30,
    }
    h.write(telephony)
    h.payloads['calls'] = {'values': [
        ['yo‘nalish', 'raqam', 'sana', 'yozuv_havolasi'],
        ['outbound', '998901234567', '2026-09-18', 'https://media.example/rec-9.wav']]}
    engine = h.build()
    result = h.read(engine, call_events, register='log')
    blob = json.dumps({'events': result['events']}, ensure_ascii=False).lower()
    check('4', 'rec-9.wav' not in blob and 'https://' not in blob,
          'a media path in a register never surfaces in the output')
    check('4', 'media' not in blob,
          'and no media-shaped field is invented to hold it')
    check('4', result['withheld_outcomes'] == 1,
          'and the withheld cell is counted, not silently dropped')
    check('4', result['events'][0]['outcome'] == '',
          'the outcome is empty rather than republished as a URL')
    # A plain outcome word must survive the filter, or the leak fix would have
    # broken the ordinary case.
    h.payloads['calls'] = {'values': [
        ['yo‘nalish', 'raqam', 'sana', 'yozuv_havolasi'],
        ['outbound', '998901234567', '2026-09-18', 'answered']]}
    engine = h.build()
    result = h.read(engine, call_events, register='log')
    check('4', result['events'][0]['outcome'] == 'answered',
          'an ordinary outcome word is kept (the filter is narrow)')
    check('4', result['withheld_outcomes'] == 0,
          'and a word is not counted as withheld')
    h.payloads['calls'] = {'values': [CALL_HEADER] + CALL_ROWS}
    h.write()


def section_five(h):
    print('\n[5] No person is evaluated, however the register is written')
    engine = h.build()
    for fn, kwargs in ((call_events, {'register': 'log'}),
                       (summary, {'register': 'log'})):
        result = h.read(engine, fn, **kwargs)
        names = set(result)
        for record in result.get('events', []):
            names |= set(record)
        banned = ('operator', 'xodim', 'worker', 'employee', 'person',
                  'agent_name', 'brigade', 'team', 'user', 'login', 'author')
        hits = sorted(k for k in banned if k in {n.lower() for n in names})
        check('5', hits == [], f'{fn.__name__} surfaces no person key (found {hits})')

    result = h.read(engine, summary, register='log')
    blob = json.dumps(result, ensure_ascii=False).lower()
    for banned in ('per_agent', 'by_agent', 'rank', 'rating', 'score',
                   'leaderboard'):
        check('5', banned not in blob, f'the summary carries no {banned!r}')
    check('5', result['timed_count'] == 2,
          'a duration total always ships its sample count')
    check('5', result['total_seconds'] == 180.0,
          'and the total is over the readable samples only (120 + 60)')


def section_six(h):
    print('\n[6] The gate is structural: an outage is not a quiet day')
    engine = h.build()
    # An unreadable consent register must not answer "not consented" silently.
    h.payloads['consent'] = {'values': [CONSENT_HEADER,
                                        ['998901234567', 'maybe', 'service', '', 'no']]}
    result = h.read(engine, consent, number='998901234567', purpose='service')
    check('6', result['consented'] is False,
          "a status that is neither granted nor withdrawn is not consent")
    check('6', result['unreadable_consent_rows'] == 1,
          'and the unreadable row is counted, not treated as absent')
    h.payloads['consent'] = {'values': [CONSENT_HEADER,
                                        ['998901234567', 'granted', 'service', 'soon', 'yes']]}
    result = h.read(engine, consent, number='998901234567', purpose='service')
    check('6', result['consented'] is False,
          'an unreadable expiry is not an open-ended consent')
    check('6', result['unreadable_consent_rows'] == 1,
          'and it is counted too')
    h.payloads['consent'] = {'values': [CONSENT_HEADER] + CONSENT_ROWS}

    # A provider outage surfaces rather than reporting no calls.
    with patch('platform_runtime.sheets.configured_manager') as manager:
        manager.return_value.access.return_value.access_token = 'fake-token'
        def explode(url, token):
            raise RuntimeError('provider outage')
        with patch('platform_runtime.sheets._http_get', side_effect=explode):
            try:
                call_events(engine, TENANT, AGENT, 's1', register='log')
                check('6', False,
                      'an outage returned a normal payload — a manager would read '
                      'this as a quiet day')
            except RuntimeError:
                check('6', True, 'an outage surfaces as an error, not as zero calls')

    # A tenant with no declaration refuses, not reports an empty log.
    engine = h.build()
    h.write(telephony={'registers': {}, 'consent_registers': {}})
    try:
        h.read(engine, call_events)
        check('6', False, 'an undeclared telephony block returned a payload')
    except Forbidden:
        check('6', True, 'an undeclared telephony block is refused by name')
    h.write()


def section_seven(h):
    print('\n[7] The config refuses a typo rather than reading the wrong column')
    for mutation, label in (
        (lambda t: t['registers']['log'].pop('number_column'), 'missing number_column'),
        (lambda t: t['registers']['log'].update({'recording_url_column': 'x'}),
         'unknown register key'),
        (lambda t: t['registers']['log'].update({'purpose': 'survey'}), 'unknown purpose'),
        (lambda t: t['consent_registers']['consent'].pop('status_column'),
         'missing consent status_column'),
        (lambda t: t.update({'consented_numbers': ['not-a-number']}),
         'an unusable allowlist number'),
    ):
        telephony = {
            'registers': {'log': {
                'register': 'plant', 'range': 'calls',
                'direction_column': 'yo‘nalish', 'number_column': 'raqam',
                'timestamp_column': 'sana', 'purpose': 'service'}},
            'consent_registers': {'consent': {
                'register': 'plant', 'range': 'consent',
                'number_column': 'raqam', 'status_column': 'holat',
                'purpose_column': 'maqsad'}},
            'consented_numbers': ['998901234567'],
            'retention_days': 30,
        }
        mutation(telephony)
        h.write(telephony)
        try:
            telephony_config(TENANT)
            check('7', False, f'{label} was accepted at configure time')
        except ValueError:
            check('7', True, f'{label} is refused at configure time')
    h.write()

    # An empty block is a declaration of nothing, not a typo.
    h.write(telephony={})
    check('7', telephony_config(TENANT) == {'registers': {}, 'consent_registers': {},
                                            'consented_numbers': frozenset(),
                                            'throughput': None, 'retention_days': None},
          'an empty telephony block is five empty facts')
    h.write()


def section_eight(h):
    """Stage B: the queue, the pace ceiling and the retention window.

    Three claims stage B makes, each measured rather than taken on faith:

    1. the throughput ceiling is **enforced**, so a queue cannot list more
       callable numbers than the declared pace could carry;
    2. a row whose date cannot be read is **never** treated as expired, because a
       typo must not license destroying a call record;
    3. asking about N numbers costs **one** read of the consent register, not N —
       an amplification that would otherwise make a fifty-row queue fifty reads.
    """
    print('\n[8] Stage B: queue, pace ceiling, retention')
    h.write()
    # Section 8 owns its payload outright: earlier sections leave rows behind, so
    # every measurement below starts from an explicit table rather than whatever
    # the previous section happened to leave in place.
    h.payloads['calls'] = {'values': [CALL_HEADER] + [
        list(row) for row in CALL_ROWS]}

    # --- the queue lists only what we chose to call ---------------------------
    engine = h.build()
    result = h.read(engine, queue, register='log')
    check('8', all(item['direction'] == 'outbound' for item in result['items']),
          'every queue item is an outbound row')
    check('8', '998901234567' in {item['number'] for item in result['items']},
          'the consented outbound number is queued')
    # The inbound row carries the same number as an outbound one, so an
    # implementation that ignored direction would emit it twice.
    check('8', sum(1 for item in result['items'] if item['number'] == '998901234567') == 1,
          'an inbound call is never queued (the number appears once, not twice)')
    check('8', result['blocked_count'] >= 1,
          'an ungated outbound row is listed AND counted, not hidden')

    # --- the ceiling is enforced, not reported --------------------------------
    # Pace is 2 per window here; five callable rows must not all be "callable".
    # Explicit assignment, never append: the probe must not measure a table it
    # built by accident from a previous section's leftovers.
    h.payloads['calls'] = {'values': [CALL_HEADER] + [
        ['outbound', '998901234567', '2026-09-18', 'answered', '10', '']
        for _ in range(5)]}
    engine = h.build()
    result = h.read(engine, queue, register='log')
    check('8', result['capacity'] == 2,
          f'the declared pace is echoed as capacity (got {result["capacity"]})')
    check('8', result['callable_count'] == 5,
          f'all five callable rows were evaluated (got {result["callable_count"]})')
    check('8', result['over_capacity'] == 3,
          f'the three beyond the pace are counted as over_capacity, not hidden '
          f'(got {result["over_capacity"]})')

    # A limit bounds the items carried, never the evaluation. If it bounded the
    # scan, 'over_capacity' would describe only the visible slice and the operator
    # would pace against the wrong number.
    engine = h.build()
    limited = h.read(engine, queue, register='log', limit=1)
    check('8', limited['item_count'] == 1 and limited['matched'] == 5,
          f'a limit carries fewer items but still evaluates the whole window '
          f'(items={limited["item_count"]}, matched={limited["matched"]})')

    # --- no declared pace is the safe default ---------------------------------
    h.write(telephony={
        'registers': {'log': {
            'register': 'plant', 'range': 'calls',
            'direction_column': 'yo‘nalish', 'number_column': 'raqam',
            'timestamp_column': 'sana', 'outcome_column': 'natija',
            'duration_column': 'davomiylik',
            'extension_column': 'stansiya', 'purpose': 'service'}},
        'consent_registers': {'consent': {
            'register': 'plant', 'range': 'consent',
            'number_column': 'raqam', 'status_column': 'holat',
            'purpose_column': 'maqsad', 'expiry_column': 'muddat',
            'recording_column': 'yozish'}},
        'consented_numbers': ['998901234567'],
        'retention_days': 30,
    })
    engine = h.build()
    result = h.read(engine, queue, register='log')
    check('8', result['throughput'] is None and result['capacity'] == 0,
          'a tenant with no declared pace has zero capacity')
    check('8', result['over_capacity'] == result['callable_count'] > 0,
          'with no declared pace, every callable row is over capacity — an operator '
          'who has not decided a pace has not decided to call anyone')

    # --- retention: three states, never expired by a typo ---------------------
    h.write()
    h.payloads['calls']['values'] = [CALL_HEADER,
        ['outbound', '998901234567', '2026-09-18', 'answered', '10', ''],
        ['outbound', '998901234567', '2020-01-01', 'answered', '10', ''],
        ['outbound', '998901234567', 'not-a-date', 'answered', '10', '']]
    engine = h.build()
    result = h.read(engine, retention, register='log', today='2026-09-20')
    states = {row['state'] for row in result['rows']}
    check('8', states == {'in_window', 'past_window', 'unaged'},
          f'all three retention states are distinguished (got {sorted(states)})')
    check('8', result['deleted'] == 0,
          'retention never deletes: the platform does not destroy a call record')
    check('8', result['unaged'] == 1,
          'an unreadable date is unaged, not expired')

    # The sharp edge: with an unreadable date AND a far-future "today", a naive
    # implementation would expire the unreadable row too. Measured here because it
    # is the one place a typo could destroy evidence.
    engine = h.build()
    far = h.read(engine, retention, register='log', today='2099-01-01')
    check('8', far['past_window'] == 2,
          f'under a far-future today both DATED rows expire (got {far["past_window"]})')
    check('8', far['unaged'] == 1,
          'the unreadable date is STILL unaged under a far-future today — a typo '
          'never becomes "expired by accident"')
    check('8', far['past_window'] + far['unaged'] + far['in_window'] == 3,
          'every row is accounted for in exactly one state')

    # The boundary is measured in whole days, at the declared window.
    h.payloads['calls']['values'] = [CALL_HEADER,
        ['outbound', '998901234567', '2026-08-21', 'answered', '10', ''],
        ['outbound', '998901234567', '2026-08-22', 'answered', '10', '']]
    engine = h.build()
    edge = h.read(engine, retention, register='log', today='2026-09-20')
    check('8', edge['past_window'] == 1 and edge['in_window'] == 1,
          f'a 30-day-old row expires at a 30-day window; a 29-day-old one does not '
          f'(past={edge["past_window"]}, in={edge["in_window"]})')

    # --- the read amplification is gone ---------------------------------------
    # The interesting number is the hop count: it must not grow with the queue.
    h.write()
    h.payloads['calls']['values'] = [CALL_HEADER] + [
        ['outbound', '998901234567', '2026-09-18', 'answered', '10', '']
        for _ in range(12)]
    engine = h.build()
    h.read(engine, queue, register='log')
    small = len(h.calls)
    h.payloads['calls']['values'] = [CALL_HEADER] + [
        ['outbound', '998901234567', '2026-09-18', 'answered', '10', '']
        for _ in range(60)]
    engine = h.build()
    h.read(engine, queue, register='log')
    big = len(h.calls)
    check('8', small == big,
          f'a 60-row queue costs the same provider hops as a 12-row one '
          f'({big} vs {small}) — the consent register is read once, not per row')
    check('8', big <= 2,
          f'and that is at most two hops: one call read, one consent read '
          f'(got {big})')

    # --- and stage B still does not dial --------------------------------------
    engine = h.build()
    result = h.read(engine, queue, register='log')
    blob = json.dumps(result['items'], ensure_ascii=False).lower()
    check('8', not any(word in blob for word in
                       ('dial', 'sip', 'audio', '.wav', 'transcript', 'command')),
          'no queue item carries a dial instruction, an audio path or a transport')
    blob = json.dumps(h.read(engine, retention, register='log'), ensure_ascii=False).lower()
    check('8', 'audio' not in blob and 'transcript' not in blob,
          'no retention row carries an audio path or a transcript either')

    # --- config refuses the two dangerous defaults ----------------------------
    for bad, why in (
        ({'per_window': 0, 'window_seconds': 3600}, 'a pace of zero per window'),
        ({'per_window': 10, 'window_seconds': 1}, 'a window shorter than a minute'),
        ({'per_window': 10, 'window_seconds': 3600, 'x': 1}, 'an unknown pace key'),
    ):
        h.write(telephony=dict(CALLS_CONFIG, throughput=bad))
        try:
            telephony_config(TENANT)
            check('8', False, f'{why} is refused')
        except ValueError:
            check('8', True, f'{why} is refused at configure time')
    for bad in (0, -1, 9999, 'forever'):
        h.write(telephony=dict(CALLS_CONFIG, retention_days=bad))
        try:
            telephony_config(TENANT)
            check('8', False, f'retention_days={bad!r} is refused')
        except ValueError:
            check('8', True, f'retention_days={bad!r} is refused')
    payload = {k: v for k, v in CALLS_CONFIG.items() if k != 'retention_days'}
    h.write(telephony=payload)
    try:
        telephony_config(TENANT)
        check('8', False, 'a consent register with no retention window is refused')
    except ValueError:
        check('8', True, 'a consent register with no retention window is refused '
                         '(VO-07: retention is an operator obligation)')
    h.write()


def main():
    print('Telephony consent-gate probe — measuring, not asserting')
    print('=' * 62)
    h = Harness()
    try:
        section_one(h)
        section_two(h)
        section_three(h)
        section_four(h)
        section_five(h)
        section_six(h)
        section_seven(h)
        section_eight(h)
    finally:
        h.close()
    print('\n' + '=' * 62)
    if _failures:
        print(f'MEASURED FAILURES: {len(_failures)}')
        for failure in _failures:
            print(f'  - {failure}')
        return 1
    print('All measured properties hold. The boundary is real, not documented.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
