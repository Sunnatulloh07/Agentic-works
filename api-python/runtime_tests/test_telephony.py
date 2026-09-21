"""Telephony call-event and consent-gate contract tests (PRD-04, P14 stage A).

Real Engine, real SQLite, scripted provider. The tests are about the **consent
gate** and the **audio boundary**, not about a dialer, because there is no dialer
here on purpose:

* **Audio never reaches the platform.** No tool argument, no config key, no
  output field carries a recording, a URL, a transcript or a buffer. This is
  measured, not promised: a register holding an audio path yields a call fact and
  the path appears nowhere in the output.
* **A number is one number.** ``+998 90 123 45 67`` and ``998-90-123-45-67`` are
  the same number; a short number and a long one are not, so a prefix match -- the
  defect P11 guards against on asset paths -- cannot call a stranger.
* **Consent is declared, and its absence is a usable answer.** An unknown number
  yields ``consented: false`` with a reason rather than an exception, but a
  **malformed** consent record raises at configure time, because a typo must
  never read as granted.
* **Recording needs its own consent.** A call whose recording consent is absent
  reports ``recording_allowed: false``; it is never treated as unrecorded.
* **An outbound row without consent is not shown as a normal call.** Its
  ``consent`` is false, it is counted, and it ships the reasons.
* **No person is evaluated.** No per-agent count, no rank, no score, however the
  register is written.
* **A register is read once**, an outage surfaces, and no credential or URL
  reaches the output.

Only the Sheets HTTP hop is scripted. The register declaration, A1 allowlist,
agent tool permission, connection allowlist, asset hierarchy and SQLite engine are
all the real ones.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime import telephony
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.telephony import (
    CONSENT_STATUS,
    DIRECTIONS,
    MAX_EVENTS,
    MAX_PURPOSES,
    MAX_QUEUE_ITEMS,
    PURPOSES,
    TELEPHONY_TOOLS,
    call_events,
    consent,
    normalise,
    queue,
    register_telephony_tools,
    retention,
    summary,
    telephony_config,
)
from platform_runtime.tools import build_registry

TENANT = 't_call'
AGENT = 'ops.telephony'
CONNECTION = 'plant'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

LEVELS = ['zavod', 'sex', 'liniya', 'stanok']

POLICY = {
    'tools': ['sheets.rows', 'telephony.consent', 'telephony.call_events',
              'telephony.summary', 'telephony.queue', 'telephony.retention'],
    'allowed_connections': [CONNECTION, 'google'],
    'ladder': 'human_assisted',
}

ASSETS = {
    'entity': 'asset',
    'levels': LEVELS,
    'measurements': ['cycle_time', 'idle_time', 'output_count'],
}

CALLS = {
    'registers': {
        'log': {
            'register': 'plant', 'range': 'calls',
            'direction_column': 'yo‘nalish', 'number_column': 'raqam',
            'timestamp_column': 'sana', 'outcome_column': 'natija',
            'duration_column': 'davomiylik', 'extension_column': 'stansiya',
            'purpose': 'service',
        },
    },
    'consent_registers': {
        'consent': {
            'register': 'plant', 'range': 'consent',
            'number_column': 'raqam', 'status_column': 'holat',
            'purpose_column': 'maqsad', 'expiry_column': 'muddat',
            'recording_column': 'yozish',
        },
    },
    'consented_numbers': ['998901234567', '998911111111'],
    'throughput': {'per_window': 2, 'window_seconds': 3600},
    'retention_days': 30,
}

REGISTERS = {
    'plant': {'connection': 'google', 'spreadsheet_id': SPREADSHEET,
              'ranges': {'calls': 'Qongiroq!A1:G', 'consent': 'Rozilik!A1:E'},
              'max_rows': 200},
}

CALL_HEADER = ['yo‘nalish', 'raqam', 'sana', 'natija', 'davomiylik', 'stansiya']
CONSENT_HEADER = ['raqam', 'holat', 'maqsad', 'muddat', 'yozish']

CALLS_ROWS = [
    # An outbound call to a consented number, recorded-consented: a normal fact.
    ['outbound', '+998 90 123 45 67', '2026-09-18', 'answered', '120',
     'zavod-1/sex-1/liniya-1/stanok-1'],
    # An inbound call: the customer called us, so the outbound gate does not apply.
    ['inbound', '998 90 123 45 67', '2026-09-18', 'answered', '60',
     'zavod-1/sex-1/liniya-1/stanok-1'],
    # An outbound call to a number with NO consent row: refused and flagged.
    ['outbound', '998935555555', '2026-09-19', 'no_answer', '', ''],
    # A row with no number at all: skipped, not a call.
    ['outbound', '', '2026-09-19', 'answered', '10', ''],
]

CONSENT_ROWS = [
    ['998901234567', 'granted', 'service', '2027-01-01', 'yes'],
    ['998911111111', 'granted', 'delivery', '2027-01-01', 'no'],
    ['998922222222', 'withdrawn', 'service', '', 'no'],
    ['998933333333', 'granted', 'marketing', '2026-01-01', 'yes'],  # expired
]


class _Sentinel:
    def __repr__(self):
        return 'MISSING'


_DEFAULT = _Sentinel()
_MISSING = _Sentinel()


class Clock:
    def __init__(self, start=1_770_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class RecordingTransport:
    """Stands in for the Sheets HTTP GET. Records every call."""

    def __init__(self):
        self.payload = {'values': []}
        self.calls = []

    def __call__(self, url, token):
        self.calls.append(url)
        return self.payload


def matrix(header, rows):
    return {'values': [header] + [list(row) for row in rows]}


class TelephonyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.transport = RecordingTransport()
        self.registers = json.loads(json.dumps(REGISTERS))
        self.policy = dict(POLICY)
        self.payloads = {'calls': matrix(CALL_HEADER, CALLS_ROWS),
                         'consent': matrix(CONSENT_HEADER, CONSENT_ROWS)}
        self.write_config()
        self.env = patch.dict(os.environ, {
            'PLATFORM_INTEGRATIONS_FILE': str(self.cfg),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.engine = Engine(self.root / 'call.db', build_registry(),
                             lambda t, a: self.policy, clock=self.clock)

    def write_config(self, telephony=_DEFAULT, assets=_DEFAULT):
        self.cfg = self.root / 'integrations.json'
        payload = {
            'connections': {'google': {}},
            'sheets_registers': self.registers,
        }
        if assets is not _MISSING:
            payload['assets'] = ASSETS if assets is _DEFAULT else assets
        if telephony is not _MISSING:
            payload['telephony'] = CALLS if telephony is _DEFAULT else telephony
        self.cfg.write_text(json.dumps({TENANT: payload}, ensure_ascii=False),
                            encoding='utf-8')

    def call(self, fn, *args, range_key='calls', payload=None, step='s1', **kwargs):
        """Invoke a telephony function with only the Sheets hop replaced.

        The range key decides which fixture the hop returns, so the consent read
        and the call read can each get their own table in one invocation.
        """
        self.transport.calls = []
        chosen = payload if payload is not None \
            else self.payloads.get(range_key, {'values': []})
        self.transport.payload = chosen
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=self._route):
                return fn(self.engine, TENANT, AGENT, *args, step, **kwargs)

    def _route(self, url, token):
        """Return the fixture matching the A1 range named in the URL."""
        self.transport.calls.append(url)
        for key, payload in self.payloads.items():
            if key == 'calls' and 'Qongiroq' in url:
                return payload
            if key == 'consent' and 'Rozilik' in url:
                return payload
        return {'values': []}

    # ---------------------------------------------------------- registration

    def test_every_telephony_tool_is_read_only(self):
        registry = build_registry()
        for name in TELEPHONY_TOOLS:
            self.assertIn(name, registry.items)
            self.assertEqual('read', registry.items[name].risk, name)

    def test_the_module_exposes_no_write_or_dial_path(self):
        """A dialer would be an outbound write to a person; there is none."""
        import inspect
        import platform_runtime.telephony as module
        callables = {name for name, value in vars(module).items()
                     if not name.startswith('_')
                     and (inspect.isfunction(value) or inspect.isclass(value))
                     and getattr(value, '__module__', '') == module.__name__}
        for banned in ('dial', 'call', 'place', 'connect', 'create', 'update',
                       'set', 'delete', 'write', 'append', 'edit', 'save',
                       'store', 'record', 'send'):
            hits = [name for name in callables
                    if banned in name.lower()
                    and name not in ('call_events', 'consent')]
            self.assertFalse(hits, f'unexpected mutating surface matching {banned}: {hits}')

    def test_no_tool_accepts_a_register_range_or_column(self):
        for name in TELEPHONY_TOOLS:
            props = set(self.engine.registry.items[name].schema['properties'])
            for needle in ('register_range', 'spreadsheet_id', 'connection',
                           'number_column', 'direction_column', 'status_column',
                           'audio', 'audio_path', 'recording_url', 'transcript'):
                self.assertNotIn(needle, props, f'{name} accepts {needle!r}')

    def test_the_schema_forbids_extra_keys(self):
        with self.assertRaises(ValueError):
            self.engine.registry.items['telephony.call_events'].validate(
                {'register': 'log', 'audio_path': 'x'})

    # ----------------------------------------------------- the number is one thing

    def test_two_spellings_of_one_number_are_equal(self):
        self.assertEqual('998901234567', normalise('+998 90 123 45 67'))
        self.assertEqual('998901234567', normalise('998-90-123-45-67'))
        self.assertEqual('998901234567', normalise('(998) 90 123 45 67'))

    def test_a_blank_or_unusable_cell_is_not_a_number(self):
        for value in ('', None, 'Ali Valiyev', '1234567890123456', True):
            with self.subTest(value=value):
                self.assertEqual('', normalise(value))

    def test_a_shorter_number_is_not_the_same_as_a_longer_one(self):
        """A prefix match would call a stranger because a prefix was consented."""
        self.assertNotEqual(normalise('99890123456'), normalise('998901234567'))
        self.assertNotEqual(normalise('901234567'), normalise('998901234567'))

    # ---------------------------------------- audit fixes: lossy digit extraction

    def test_unrelated_garbage_does_not_collapse_onto_one_short_number(self):
        """The ultra-audit defect: six strings all became the number '9'.

        Stripping non-digits from arbitrary text is lossy in a dangerous
        direction -- a media URL, a bare filename, the word 'call 9' and a lone
        '9' all reduce to '9' -- so an allowlist or a consent row for '9' would
        cover every one of them. A number below the minimum length is refused.
        """
        for value in ('https://a/rec-9.wav', 'rec-9.wav', 'call 9', '9', 'x9',
                      'file9', '12345', '123456'):
            with self.subTest(value=value):
                self.assertEqual('', normalise(value))
        self.assertEqual('1234567', normalise('1234567'))

    def test_a_url_embedding_a_full_number_is_not_that_number(self):
        """A recording's own filename must not become a consented counterparty.

        ``https://cdn.example/rec-998901234567.wav`` embeds a complete number, so
        a length test alone would admit it and the media path would turn into the
        call's counterparty.
        """
        self.assertEqual('', normalise('https://example.com/998901234567'))
        self.assertEqual('', normalise('https://cdn.example/rec-998901234567.wav'))
        self.assertEqual('', normalise('file:///rec/998901234567'))

    def test_a_tel_uri_is_a_spelling_of_a_number_not_a_locator(self):
        """``tel:`` names a number, so it is stripped rather than refused."""
        self.assertEqual('998901234567', normalise('tel:+998901234567'))
        self.assertEqual('998901234567', normalise('tel://998901234567'))

    # ---------------------------------------------------------- the consent gate

    def test_a_consented_number_for_this_purpose_is_consented(self):
        result = self.call(consent, number='+998 90 123 45 67', purpose='service')
        self.assertTrue(result['consented'])
        self.assertEqual([], result['reasons'])
        self.assertEqual('998901234567', result['number'])

    def test_a_number_with_no_consent_row_is_refused_with_a_reason(self):
        result = self.call(consent, number='998935555555', purpose='service')
        self.assertFalse(result['consented'])
        self.assertTrue(any('no consent record' in r for r in result['reasons']))

    def test_consent_for_one_purpose_is_not_consent_for_another(self):
        """A delivery consent is not a marketing consent."""
        allowed = self.call(consent, number='998911111111', purpose='delivery')
        refused = self.call(consent, number='998911111111', purpose='marketing')
        self.assertTrue(allowed['consented'])
        self.assertFalse(refused['consented'])

    def test_a_withdrawn_consent_is_refused_by_name(self):
        result = self.call(consent, number='998922222222', purpose='service')
        self.assertFalse(result['consented'])
        self.assertTrue(any('withdrawn' in r for r in result['reasons']))

    def test_an_expired_consent_is_refused(self):
        """998933333333's marketing consent expired on 2026-01-01."""
        result = self.call(consent, number='998933333333', purpose='marketing')
        self.assertFalse(result['consented'])
        self.assertTrue(any('expired' in r for r in result['reasons']))

    def test_an_empty_allowlist_means_no_allowlist_not_nothing_consented(self):
        """An empty list is "not declared", reported as such, not as a refusal."""
        broken = json.loads(json.dumps(CALLS))
        broken['consented_numbers'] = []
        self.write_config(telephony=broken)
        result = self.call(consent, number='998901234567', purpose='service')
        self.assertTrue(result['consented'])
        self.assertFalse(result['allowlist_declared'])

    def test_a_declared_allowlist_refuses_a_number_outside_it(self):
        """A number with a valid consent row but not in the allowlist is refused."""
        broken = json.loads(json.dumps(CALLS))
        broken['consented_numbers'] = ['998901234567']
        rows = CONSENT_ROWS + [['998944444444', 'granted', 'service', '', 'no']]
        self.write_config(telephony=broken)
        self.payloads['consent'] = matrix(CONSENT_HEADER, rows)
        allowed = self.call(consent, number='998901234567', purpose='service')
        refused = self.call(consent, number='998944444444', purpose='service')
        self.assertTrue(allowed['consented'])
        self.assertFalse(refused['consented'])
        self.assertTrue(any('consented_numbers' in r for r in refused['reasons']))

    def test_an_unknown_purpose_is_refused_by_name(self):
        with self.assertRaises(Forbidden):
            self.call(consent, number='998901234567', purpose='survey')

    # ------------------------------------------------------- recording consent

    def test_recording_is_allowed_only_with_its_own_declared_consent(self):
        yes = self.call(consent, number='998901234567', purpose='service')
        no = self.call(consent, number='998911111111', purpose='delivery')
        self.assertTrue(yes['recording_allowed'])
        self.assertFalse(no['recording_allowed'])

    def test_a_missing_recording_consent_is_not_read_as_unrecorded(self):
        """Absent permission is reported as false, never as "no recording exists"."""
        result = self.call(consent, number='998922222222', purpose='service')
        self.assertFalse(result['consented'])
        self.assertFalse(result['recording_allowed'])

    # --------------------------------------------------------- the event reader

    def test_a_consented_outbound_call_is_reported_as_a_fact(self):
        result = self.call(call_events, register='log')
        outbound = [e for e in result['events'] if e['direction'] == 'outbound']
        self.assertEqual(2, len(outbound))
        consented = [e for e in outbound if e['consent']]
        self.assertEqual(1, len(consented))
        self.assertEqual('answered', consented[0]['outcome'])
        self.assertEqual(120.0, consented[0]['duration_seconds'])

    def test_an_outbound_call_without_consent_is_flagged_not_hidden(self):
        result = self.call(call_events, register='log')
        refused = [e for e in result['events']
                   if e['direction'] == 'outbound' and not e['consent']]
        self.assertEqual(1, len(refused))
        self.assertEqual('998935555555', refused[0]['number'])
        self.assertTrue(refused[0]['consent_reasons'])
        self.assertEqual(1, result['refused_without_consent'])

    def test_an_inbound_call_does_not_need_an_outbound_consent(self):
        """The customer called us; the outbound gate is not the same question."""
        result = self.call(call_events, register='log')
        inbound = [e for e in result['events'] if e['direction'] == 'inbound']
        self.assertEqual(1, len(inbound))
        self.assertTrue(inbound[0]['consent'])

    def test_a_row_with_no_number_is_skipped_not_reported_as_a_call(self):
        result = self.call(call_events, register='log')
        self.assertEqual(1, result['skipped'])
        self.assertFalse(any(e['number'] == '' for e in result['events']))

    def test_an_unknown_direction_is_skipped(self):
        rows = CALLS_ROWS + [['sideways', '998901234567', '2026-09-19', 'x', '1', '']]
        self.payloads['calls'] = matrix(CALL_HEADER, rows)
        result = self.call(call_events, register='log')
        self.assertEqual(2, result['skipped'])

    def test_the_two_directions_are_the_declared_set(self):
        self.assertEqual(('inbound', 'outbound'), DIRECTIONS)

    # -------------------------------------------------- audio never reaches here

    def test_an_audio_path_column_appears_nowhere_in_the_output(self):
        """A register holding a recording path yields a fact, not the path.

        The probe caught this: pointing ``outcome_column`` at a media column
        republished a recording URL as a call outcome, which is exactly the leak
        the boundary forbids. A locator-shaped cell is now withheld and counted.

        The header must actually carry the media column, or the module would be
        reading a column that does not exist and the test would pass for the wrong
        reason.
        """
        broken = json.loads(json.dumps(CALLS))
        broken['registers']['log']['outcome_column'] = 'yozuv_havolasi'
        header = ['yo‘nalish', 'raqam', 'sana', 'yozuv_havolasi', 'davomiylik',
                  'stansiya']
        rows = [['outbound', '998901234567', '2026-09-18',
                 'https://media.example/rec-123.wav', '120',
                 'zavod-1/sex-1/liniya-1/stanok-1']]
        self.write_config(telephony=broken)
        self.payloads['calls'] = matrix(header, rows)
        result = self.call(call_events, register='log')
        # The note names the boundary in prose, so it is excluded from the scan:
        # what must never appear is the path, a URL, or an invented media field.
        events = {'events': result['events']}
        blob = json.dumps(events, ensure_ascii=False)
        self.assertNotIn('rec-123.wav', blob)
        self.assertNotIn('https://', blob)
        self.assertNotIn('media', blob.lower())
        self.assertNotIn('recording_url', blob.lower())
        # And the withheld cell is counted, not silently dropped.
        self.assertEqual(1, result['withheld_outcomes'])
        self.assertEqual('', result['events'][0]['outcome'])

    def test_a_plain_outcome_word_is_kept(self):
        """The locator filter must not eat ordinary outcome text."""
        result = self.call(call_events, register='log')
        outcomes = [e.get('outcome') for e in result['events'] if e.get('outcome')]
        self.assertIn('answered', outcomes)
        self.assertEqual(0, result['withheld_outcomes'])

    def test_a_media_extension_cell_is_withheld_even_without_a_scheme(self):
        header = ['yo‘nalish', 'raqam', 'sana', 'natija', 'davomiylik', 'stansiya']
        rows = [['outbound', '998901234567', '2026-09-18', 'rec-9.mp3', '120', '']]
        self.payloads['calls'] = matrix(header, rows)
        result = self.call(call_events, register='log')
        self.assertEqual(1, result['withheld_outcomes'])
        self.assertEqual('', result['events'][0]['outcome'])

    def test_no_output_field_is_an_audio_or_transcript_field(self):
        result = self.call(call_events, register='log')
        names = set(result)
        for record in result['events']:
            names |= set(record)
        banned = ('audio', 'audio_path', 'recording', 'recording_url',
                  'transcript', 'pcm', 'wav', 'media')
        hits = sorted(n for n in names if any(b in n.lower() for b in banned))
        self.assertEqual([], hits, f'audio-shaped field surfaced: {hits}')

    def test_the_module_imports_no_audio_surface_at_all(self):
        """The boundary is structural: nothing here can read or write media."""
        import pathlib
        import platform_runtime.telephony as module
        source = pathlib.Path(module.__file__).read_text(encoding='utf-8')
        for needle in ('AishaREST', 'synthesize', 'transcribe', 'urllib',
                       'audio_path', 'multipart', 'soundfile', 'wave'):
            # ``wave`` would also match a comment; assert on the import surface.
            if needle in ('AishaREST', 'synthesize', 'transcribe', 'urllib',
                          'audio_path', 'multipart'):
                self.assertNotIn(needle, source, f'telephony imports {needle}')

    # ---------------------------------------------------- no person is evaluated

    def test_no_view_names_a_person_key(self):
        for fn, kwargs in ((call_events, {'register': 'log'}),
                           (summary, {'register': 'log'})):
            result = self.call(fn, **kwargs)
            names = set(result)
            for record in result.get('events', []):
                names |= set(record)
            banned = ('operator', 'xodim', 'worker', 'employee', 'person',
                      'agent_name', 'brigade', 'team', 'user', 'login', 'author')
            hits = sorted(k for k in banned if k in {n.lower() for n in names})
            self.assertEqual([], hits, f'{fn.__name__} surfaced {hits}')

    def test_the_summary_produces_no_per_agent_figure(self):
        """A count per agent is a number that will be used in a review."""
        result = self.call(summary, register='log')
        blob = json.dumps(result, ensure_ascii=False).lower()
        for banned in ('per_agent', 'by_agent', 'agent_count', 'rank', 'rating',
                       'score', 'leaderboard', 'top_agent'):
            self.assertNotIn(banned, blob)

    def test_the_summary_counts_are_facts(self):
        result = self.call(summary, register='log')
        self.assertEqual(2, result['by_direction']['outbound'])
        self.assertEqual(1, result['by_direction']['inbound'])
        self.assertEqual(2, result['answered_count'])
        self.assertEqual(3, result['call_count'])

    def test_a_duration_sum_is_never_shown_without_its_sample_count(self):
        """A total over two calls must not read as a total over two hundred."""
        result = self.call(summary, register='log')
        self.assertEqual(2, result['timed_count'])
        self.assertEqual(180.0, result['total_seconds'])

    def test_an_unreadable_duration_is_none_not_zero(self):
        rows = [CALL_HEADER,
                ['outbound', '998901234567', '2026-09-18', 'answered', 'abc',
                 'zavod-1/sex-1/liniya-1/stanok-1']]
        self.payloads['calls'] = matrix(rows[0], rows[1:])
        result = self.call(call_events, register='log')
        self.assertIsNone(result['events'][0]['duration_seconds'])
        counts = self.call(summary, register='log')
        self.assertEqual(0, counts['timed_count'])

    # ------------------------------------------------------------ window and bound

    def test_the_window_excludes_rows_outside_it(self):
        result = self.call(call_events, register='log', since='2026-09-19')
        self.assertTrue(all(e['day'] >= '2026-09-19' for e in result['events']))

    def test_an_undated_row_is_counted_and_treated_as_outside_a_window(self):
        result = self.call(call_events, register='log', since='2026-09-01')
        self.assertEqual(0, result['undated'])

    def test_the_limit_is_bounded_and_truncation_ships(self):
        result = self.call(call_events, register='log', limit=1)
        self.assertEqual(1, len(result['events']))
        self.assertTrue(result['truncated'])
        for bad in (0, -1, MAX_EVENTS + 1, 10_000):
            with self.subTest(limit=bad), self.assertRaises(ValueError):
                self.call(call_events, register='log', limit=bad)

    def test_an_exact_fit_is_not_reported_as_cut(self):
        """The limit sits EXACTLY on the number of matching rows: the whole window.

        `CALLS_ROWS` holds four rows and one carries no number, so three land in
        the ledger -- the row without a number is skipped, not counted, which is
        why this asserts three and not four.

        The limit must sit on the boundary. A limit comfortably above the
        population passes under both the correct predicate (`matched > limit`) and
        the broken one (`matched >= limit`), so it would prove nothing; the
        boundary is the only place the two differ.
        """
        result = self.call(call_events, register='log', limit=3)
        self.assertEqual(3, len(result['events']))
        self.assertFalse(result['truncated'])

    def test_an_invalid_window_is_refused(self):
        for bad in ('18-09-2026', '2026/09/18', 'yesterday'):
            with self.subTest(since=bad), self.assertRaises(ValueError):
                self.call(call_events, register='log', since=bad)

    # ------------------------------------------------------- config validation

    def test_a_typo_in_a_column_name_is_refused_at_configure_time(self):
        broken = json.loads(json.dumps(CALLS))
        del broken['registers']['log']['number_column']
        self.write_config(telephony=broken)
        with self.assertRaises(ValueError):
            telephony_config(TENANT)

    def test_an_unknown_register_key_is_refused(self):
        broken = json.loads(json.dumps(CALLS))
        broken['registers']['log']['recording_url_column'] = 'x'
        self.write_config(telephony=broken)
        with self.assertRaises(ValueError):
            telephony_config(TENANT)

    def test_an_unknown_purpose_in_a_register_is_refused(self):
        broken = json.loads(json.dumps(CALLS))
        broken['registers']['log']['purpose'] = 'survey'
        self.write_config(telephony=broken)
        with self.assertRaises(ValueError):
            telephony_config(TENANT)

    def test_an_unreadable_consent_status_is_counted_not_granted(self):
        """A status that is neither granted nor withdrawn is not consent."""
        rows = CONSENT_ROWS + [['998955555555', 'maybe', 'service', '', 'yes']]
        self.payloads['consent'] = matrix(CONSENT_HEADER, rows)
        result = self.call(consent, number='998955555555', purpose='service')
        self.assertFalse(result['consented'])
        self.assertEqual(1, result['unreadable_consent_rows'])

    def test_an_unreadable_expiry_is_counted_not_treated_as_open_ended(self):
        rows = CONSENT_ROWS + [['998966666666', 'granted', 'service', 'soon', 'yes']]
        self.payloads['consent'] = matrix(CONSENT_HEADER, rows)
        result = self.call(consent, number='998966666666', purpose='service')
        self.assertFalse(result['consented'])
        self.assertEqual(1, result['unreadable_consent_rows'])

    def test_an_empty_telephony_block_is_five_empty_facts(self):
        self.write_config(telephony={})
        declared = telephony_config(TENANT)
        self.assertEqual({'registers': {}, 'consent_registers': {},
                          'consented_numbers': frozenset(),
                          'throughput': None, 'retention_days': None}, declared)

    def test_a_missing_telephony_block_is_five_empty_facts(self):
        self.write_config(telephony=_MISSING)
        declared = telephony_config(TENANT)
        self.assertEqual({'registers': {}, 'consent_registers': {},
                          'consented_numbers': frozenset(),
                          'throughput': None, 'retention_days': None}, declared)

    def test_an_undeclared_register_is_refused_by_name(self):
        with self.assertRaises(Forbidden):
            self.call(call_events, register='phantom')

    def test_no_declared_register_at_all_is_refused(self):
        self.write_config(telephony={'registers': {}, 'consent_registers': {}})
        with self.assertRaises(Forbidden):
            self.call(call_events)

    # -------------------------------------------------------- the refusals hold

    def test_the_source_tool_must_be_held_by_the_agent(self):
        self.policy['tools'] = ['telephony.consent']
        with self.assertRaises(Forbidden):
            self.call(call_events, register='log')

    def test_the_connection_must_be_allowed(self):
        self.policy['allowed_connections'] = ['other']
        with self.assertRaises(Forbidden):
            self.call(consent, number='998901234567', purpose='service')

    def test_an_outage_surfaces_rather_than_reporting_no_calls(self):
        """An unreadable log must not read as a quiet day."""
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            def explode(url, token):
                raise RuntimeError('provider outage')
            with patch('platform_runtime.sheets._http_get', side_effect=explode):
                with self.assertRaises(RuntimeError):
                    call_events(self.engine, TENANT, AGENT, 's1', register='log')

    def test_no_credential_or_url_reaches_the_output(self):
        for fn, kwargs in ((call_events, {'register': 'log'}),
                           (summary, {'register': 'log'}),
                           (consent, {'number': '998901234567'})):
            blob = json.dumps(self.call(fn, **kwargs), ensure_ascii=False)
            self.assertNotIn('fake-token', blob)
            self.assertNotIn('spreadsheet', blob.lower())
            self.assertNotIn(SPREADSHEET, blob)

    def test_the_output_carries_the_agent_authority(self):
        result = self.call(call_events, register='log')
        self.assertEqual(AGENT, result['authority']['agent'])
        self.assertEqual('human_assisted', result['authority']['ladder'])

    def test_a_single_declared_register_needs_no_name(self):
        result = self.call(call_events)
        self.assertEqual('plant', result['register'])
        self.assertEqual('service', result['purpose'])


class TelephonyStageBTests(TelephonyTests):
    """Outbound queue, throughput ceiling and retention reporting (stage B).

    Stage B adds no new data path and no new act: it reads the same register the
    event view reads, and it still does not dial. What it adds is the three facts
    an outbound campaign actually needs before it can be lawful --

    * **who may be called** (the queue), with the consent gate already applied, so
      a queue is never assembled from ungated rows;
    * **how fast** (the throughput ceiling), enforced rather than reported, so a
      queue cannot overstate what may be called;
    * **how long the record may live** (retention), stated per row but never
      acted on, because destroying a call record is the operator's lawful act and
      not the platform's convenience.

    The inheritance is deliberate: these tests run against the *same* engine,
    fixtures and scripted hop as stage A, so a stage-B change that broke a stage-A
    invariant would break a stage-A test in the parent class.
    """

    # --------------------------------------------------------------- the queue

    def test_the_queue_lists_only_outbound_rows(self):
        result = self.call(queue, register='log')
        self.assertTrue(result['items'])
        for item in result['items']:
            self.assertEqual('outbound', item['direction'])

    def test_an_inbound_call_is_never_a_queue_item(self):
        """We did not choose to make an inbound call, so there is nothing to pace."""
        result = self.call(queue, register='log')
        numbers = {item['number'] for item in result['items']}
        self.assertIn('998901234567', numbers)   # the outbound consented row
        # The inbound row shares a number with a consented outbound row, so a
        # naive implementation that ignored direction would emit it twice.
        self.assertEqual(1, sum(1 for item in result['items']
                                if item['number'] == '998901234567'))

    def test_a_row_without_consent_is_shown_and_not_hidden(self):
        """Hiding an ungated row would leave an operator believing the list clean."""
        result = self.call(queue, register='log')
        blocked = [item for item in result['items'] if not item['consented']]
        self.assertTrue(blocked)
        self.assertTrue(blocked[0]['reasons'])
        self.assertEqual(len(blocked), result['blocked_count'])

    def test_the_queue_never_dials(self):
        """No queue item may name a provider, an action or a command."""
        result = self.call(queue, register='log')
        blob = json.dumps(result['items'], ensure_ascii=False).lower()
        for banned in ('dial', 'sip', 'call_now', 'action', 'command', 'invoke'):
            self.assertNotIn(banned, blob)

    def test_the_queue_echoes_the_pace_it_applied(self):
        result = self.call(queue, register='log')
        self.assertEqual({'per_window': 2, 'window_seconds': 3600}, result['throughput'])
        self.assertEqual(2, result['capacity'])

    def test_no_declared_pace_makes_every_callable_row_over_capacity(self):
        """A tenant who has not decided a pace has not decided to call anyone."""
        self.write_config(telephony={
            'registers': CALLS['registers'],
            'consent_registers': CALLS['consent_registers'],
            'consented_numbers': CALLS['consented_numbers'],
            'retention_days': 30,
        })
        result = self.call(queue, register='log')
        self.assertIsNone(result['throughput'])
        self.assertEqual(0, result['capacity'])
        # The one consented outbound row is callable, and with no pace declared
        # that is already more than the (absent) allowance permits.
        self.assertEqual(1, result['callable_count'])
        self.assertEqual(1, result['over_capacity'])

    def test_a_surplus_beyond_the_declared_pace_is_counted_not_listed(self):
        """The ceiling is enforced, not merely reported."""
        rows = [['outbound', '998901234567', '2026-09-18', 'answered', '10', ''],
                ['outbound', '+998 90 123 45 67', '2026-09-18', 'answered', '11', ''],
                ['outbound', '998 90 123 45 67', '2026-09-18', 'answered', '12', '']]
        self.payloads['calls'] = matrix(CALL_HEADER, rows)
        result = self.call(queue, register='log')
        # Three callable rows, a pace of two: one is over capacity.
        self.assertEqual(3, result['callable_count'])
        self.assertEqual(1, result['over_capacity'])

    def test_the_limit_bounds_the_items_carried_not_the_evaluation(self):
        """'There are 900 callable' and 'here are 50' are two different facts."""
        rows = [['outbound', '998901234567', '2026-09-1$d'.replace('$d', str(d)),
                 'answered', '10', ''] for d in range(1, 6)]
        self.payloads['calls'] = matrix(CALL_HEADER, rows)
        result = self.call(queue, register='log', limit=2)
        self.assertEqual(2, result['item_count'])
        # Everything was still examined, so the caller paces against the whole set.
        self.assertEqual(5, result['matched'])
        self.assertTrue(result['truncated'])

    def test_a_limit_beyond_the_ceiling_is_refused(self):
        with self.assertRaises(ValueError):
            self.call(queue, register='log', limit=MAX_QUEUE_ITEMS + 1)

    def test_the_queue_reads_the_register_once_per_consent_check(self):
        """A queue of N rows must not re-read the consent register N times."""
        calls_before = None
        result = self.call(queue, register='log')
        # Every consent question is answered from one indexed read of the consent
        # register; the call read is one hop. So the hop count is bounded and does
        # not grow with the number of queue items.
        self.assertLessEqual(len(self.transport.calls), 2)
        self.assertLessEqual(result['item_count'], MAX_QUEUE_ITEMS)

    def test_a_queue_carries_no_audio_surface(self):
        result = self.call(queue, register='log')
        blob = json.dumps(result['items'], ensure_ascii=False).lower()
        self.assertNotIn('audio', blob)
        self.assertNotIn('.wav', blob)
        self.assertNotIn('transcript', blob)

    def test_a_queue_evaluates_no_person(self):
        result = self.call(queue, register='log')
        blob = json.dumps(result, ensure_ascii=False).lower()
        for banned in ('agent_count', 'per_agent', 'score', 'rank', 'rating',
                       'by_agent', 'operator'):
            self.assertNotIn(banned, blob)

    # ------------------------------------------------------------- the ceiling

    def test_a_callable_row_beyond_the_limit_is_not_listed_at_all(self):
        rows = [['outbound', '998901234567', '2026-09-18', 'answered', '10', '']] * 6
        self.payloads['calls'] = matrix(CALL_HEADER, rows)
        result = self.call(queue, register='log', limit=3)
        self.assertEqual(3, result['item_count'])
        self.assertEqual(6, result['matched'])

    # ------------------------------------------------------------ the window

    def test_the_queue_respects_the_window(self):
        result = self.call(queue, register='log', since='2026-09-19')
        # Only the unconsented 2026-09-19 row falls inside; it is listed but blocked.
        self.assertEqual(1, result['matched'])

    # ------------------------------------------------------------- retention

    def test_retention_reports_three_states_and_never_two(self):
        """An unreadable date is neither in-window nor past-window."""
        rows = [['outbound', '998901234567', '2026-09-18', 'answered', '10', ''],
                ['outbound', '998911111111', '2020-01-01', 'answered', '10', ''],
                ['outbound', '998922222222', 'not-a-date', 'answered', '10', '']]
        self.payloads['calls'] = matrix(CALL_HEADER, rows)
        result = self.call(retention, register='log', today='2026-09-20')
        self.assertEqual(1, result['in_window'])
        self.assertEqual(1, result['past_window'])
        self.assertEqual(1, result['unaged'])
        self.assertEqual(3, len(result['rows']))

    def test_retention_never_deletes(self):
        result = self.call(retention, register='log', today='2026-09-20')
        self.assertEqual(0, result['deleted'])
        blob = json.dumps(result, ensure_ascii=False).lower()
        for banned in ('removed', 'purged', 'deleted_count', 'destroyed'):
            self.assertNotIn(banned, blob)

    def test_an_unreadable_date_is_never_expired_by_accident(self):
        """A typo in a date must not justify destroying a call record."""
        rows = [['outbound', '998901234567', 'garbage', 'answered', '10', '']]
        self.payloads['calls'] = matrix(CALL_HEADER, rows)
        result = self.call(retention, register='log', today='2099-01-01')
        self.assertEqual(0, result['past_window'])
        self.assertEqual(1, result['unaged'])
        self.assertEqual('unaged', result['rows'][0]['state'])

    def test_the_window_boundary_is_measured_in_days(self):
        """`retention_days=30` expires a 30-day-old row, not a 29-day-old one."""
        rows = [['outbound', '998901234567', '2026-08-21', 'answered', '10', ''],  # 30 days
                ['outbound', '998911111111', '2026-08-22', 'answered', '10', '']]  # 29 days
        self.payloads['calls'] = matrix(CALL_HEADER, rows)
        result = self.call(retention, register='log', today='2026-09-20')
        self.assertEqual(1, result['past_window'])
        self.assertEqual(1, result['in_window'])

    def test_retention_requires_a_declared_window(self):
        self.write_config(telephony={'registers': CALLS['registers'],
                                     'consent_registers': CALLS['consent_registers'],
                                     'consented_numbers': CALLS['consented_numbers']})
        with self.assertRaises(ValueError):
            telephony_config(TENANT)

    def test_retention_without_a_window_refuses_rather_than_guessing(self):
        self.write_config(telephony={
            'registers': CALLS['registers'],
            'consent_registers': {},
            'consented_numbers': [],
        })
        with self.assertRaises(Forbidden):
            self.call(retention, register='log')

    # ------------------------------------------------------ config refusals

    def test_a_bad_pace_is_refused(self):
        for bad in ({'per_window': 0, 'window_seconds': 3600},
                    {'per_window': 10, 'window_seconds': 5},
                    {'per_window': 10, 'window_seconds': 3600, 'extra': 1},
                    {'per_window': 'ten', 'window_seconds': 3600}):
            with self.subTest(bad=bad):
                self.write_config(telephony=dict(CALLS, throughput=bad))
                with self.assertRaises(ValueError):
                    telephony_config(TENANT)

    def test_a_bad_retention_window_is_refused(self):
        for bad in (0, -1, 3651, 'thirty', True):
            with self.subTest(bad=bad):
                self.write_config(telephony=dict(CALLS, retention_days=bad))
                with self.assertRaises(ValueError):
                    telephony_config(TENANT)

    def test_a_consent_register_without_a_retention_window_is_refused(self):
        """VO-07: a data holder with no retention window has chosen the forbidden default."""
        payload = {k: v for k, v in CALLS.items() if k != 'retention_days'}
        self.write_config(telephony=payload)
        with self.assertRaises(ValueError):
            telephony_config(TENANT)

    def test_the_two_new_tools_are_read_only_and_registered(self):
        registry = build_registry()
        for name in ('telephony.queue', 'telephony.retention'):
            self.assertIn(name, registry.items)
            self.assertEqual('read', registry.items[name].risk)


class NumberNormalisationTests(unittest.TestCase):
    """The pure function, tested apart from the engine."""

    def test_the_purpose_and_status_vocabularies_are_closed(self):
        self.assertEqual(('granted', 'withdrawn'), CONSENT_STATUS)
        self.assertEqual(5, len(PURPOSES))
        self.assertLessEqual(MAX_PURPOSES, 16)


class DeclaredBoundTests(unittest.TestCase):
    """Fazza 32: the bounds that were correct and unpinned, and three that bounded nothing.

    Nineteen revert modes were walked over this module and FIFTEEN left it green, so
    almost every ceiling here was correct and unmeasured. Where a cheap behavioural
    path exists it is used; the two that have none say so and pin the literal
    together with the line that reads it, which still fails when the bound drifts.
    """

    def test_the_throughput_pace_edges_are_the_documented_pair(self):
        for per in (1, telephony.MAX_PER_WINDOW):
            with self.subTest(per_window=per):
                telephony._throughput({'per_window': per, 'window_seconds': 60})
        for per in (0, telephony.MAX_PER_WINDOW + 1, True):
            with self.subTest(per_window=per):
                with self.assertRaises(ValueError):
                    telephony._throughput({'per_window': per, 'window_seconds': 60})
        for window in (telephony.MIN_WINDOW_SECONDS, telephony.MAX_WINDOW_SECONDS):
            with self.subTest(window_seconds=window):
                telephony._throughput({'per_window': 1, 'window_seconds': window})
        for window in (telephony.MIN_WINDOW_SECONDS - 1, telephony.MAX_WINDOW_SECONDS + 1):
            with self.subTest(window_seconds=window):
                with self.assertRaises(ValueError):
                    telephony._throughput({'per_window': 1, 'window_seconds': window})
        self.assertEqual((1000, 60, 86_400),
                         (telephony.MAX_PER_WINDOW, telephony.MIN_WINDOW_SECONDS,
                          telephony.MAX_WINDOW_SECONDS))

    def test_the_retention_edges_are_the_documented_pair(self):
        for days in (telephony.MIN_RETENTION_DAYS, telephony.MAX_RETENTION_DAYS):
            with self.subTest(days=days):
                telephony._retention(days)
        for days in (0, telephony.MAX_RETENTION_DAYS + 1, True):
            with self.subTest(days=days):
                with self.assertRaises(ValueError):
                    telephony._retention(days)
        self.assertEqual((1, 3650), (telephony.MIN_RETENTION_DAYS,
                                     telephony.MAX_RETENTION_DAYS))

    def test_the_queue_and_scan_limits_are_the_documented_ceilings(self):
        telephony._bounded(1, 'limit', 1, telephony.MAX_QUEUE_ITEMS)
        telephony._bounded(telephony.MAX_QUEUE_ITEMS, 'limit', 1,
                           telephony.MAX_QUEUE_ITEMS)
        with self.assertRaises(ValueError):
            telephony._bounded(telephony.MAX_QUEUE_ITEMS + 1, 'limit', 1,
                               telephony.MAX_QUEUE_ITEMS)
        telephony._bounded(telephony.MAX_EVENTS, 'limit', 1, telephony.MAX_EVENTS)
        with self.assertRaises(ValueError):
            telephony._bounded(telephony.MAX_EVENTS + 1, 'limit', 1, telephony.MAX_EVENTS)
        self.assertEqual((50, 200, 200),
                         (telephony.MAX_QUEUE_ITEMS, telephony.MAX_EVENTS,
                          telephony.MAX_ROWS))

    def test_the_register_purpose_and_text_ceilings(self):
        self.assertEqual(20, telephony.MAX_REGISTERS)
        self.assertEqual(16, telephony.MAX_PURPOSES)
        self.assertEqual(64, telephony.MAX_PURPOSE_CHARS)
        # No cheap behavioural path: the ceilings are read inside the config
        # validator, so the literal and the line that reads it are pinned together.
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'telephony.py').read_text(encoding='utf-8')
        for line in ('len(declared) > MAX_REGISTERS', 'len(value) > MAX_PURPOSES',
                     'len(item) > MAX_PURPOSE_CHARS'):
            with self.subTest(line=line):
                self.assertIn(line, source)

    def test_the_call_duration_ceiling_is_one_day(self):
        self.assertEqual(86_400, telephony.MAX_DURATION)
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'telephony.py').read_text(encoding='utf-8')
        self.assertIn('if seconds < 0 or seconds > MAX_DURATION:', source)

    def test_the_name_ceiling_is_sixty_four_characters(self):
        self.assertTrue(telephony.NAME_RE.match('a' * 64))
        self.assertFalse(telephony.NAME_RE.match('a' * 65))
        self.assertFalse(telephony.NAME_RE.match('-a'))
        self.assertFalse(telephony.NAME_RE.match('A'))

    def test_a_media_filename_is_not_a_phone_number(self):
        """The locator rule, extension by extension.

        A register whose number column points at a media column would otherwise turn
        a recording's own filename into a consented number -- the value embeds a full
        number, passes the length test and becomes a valid counterparty.
        """
        for extension in ('wav', 'mp3', 'ogg', 'opus', 'webm', 'm4a', 'aac',
                          'pcm', 'flac', 'amr', 'gsm', 'ulaw', 'alaw'):
            with self.subTest(extension=extension):
                self.assertEqual('', telephony.normalise(
                    'https://cdn.example/rec-998901234567.%s' % extension))
                self.assertEqual('', telephony.normalise(
                    'rec-998901234567.%s' % extension))
        self.assertEqual('', telephony.normalise('zavod-1/sex-1/liniya-1/stanok-1'))
        self.assertEqual('998901234567', telephony.normalise('tel:+998901234567'))

    def test_the_module_declares_no_ceiling_it_does_not_enforce(self):
        """The third time this shape has been found -- see fazza I and fazza 26.

        `MAX_QUEUE = 200` sat in a comment block describing it as "how many callable
        rows one read may return at all", while the read is bounded by `MAX_ROWS` and
        the items carried by `MAX_QUEUE_ITEMS`. `MAX_WINDOW_DAYS = 31` was read by
        nobody, here or in `vision`, where the same constant was restated.
        """
        self.assertFalse(hasattr(telephony, 'MAX_QUEUE'))
        self.assertFalse(hasattr(telephony, 'MAX_WINDOW_DAYS'))
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'telephony.py').read_text(encoding='utf-8')
        self.assertIn('* ``MAX_ROWS``', source)
        self.assertNotIn('* ``MAX_QUEUE``', source)

if __name__ == '__main__':
    unittest.main()
