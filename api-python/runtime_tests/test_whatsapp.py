"""WhatsApp channel contract tests. Real Engine, real SQLite, scripted provider.

The property this suite exists to protect is one sentence from the PRD:

    Error 131047 must be **impossible to reach by design**.

131047 is Meta's answer to free-form text sent outside the 24-hour service
window. It is not an exception to be caught and retried — by the time it arrives
the customer has already been left waiting while the agent believed it replied.
So the tests here are mostly about what the code *refuses*, before any provider
I/O:

* a free-form send with a closed window is refused, and the transport is never
  called — measured, not asserted from the outside;
* a missing last-inbound timestamp means *closed*, not "probably open";
* a timestamp without a timezone is refused rather than read as UTC, because
  Uzbekistan is UTC+5 and a five-hour error places a send outside the window;
* a template the operator did not declare cannot be sent, and the model cannot
  invent a language or reclassify a category;
* exactly one of text/template must be given — never both, never neither;
* a contact must be operator-declared: a model-supplied number is an unbounded
  send surface;
* the two Meta credentials stay separate, and a send credential is never used to
  inspect the account.

The send tool is ``write``, so the engine's approval gate is exercised for real
alongside the window rule the module adds on top.
"""
import datetime
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime.engine import Conflict, Engine, Forbidden
from platform_runtime.tools import build_registry
from platform_runtime.whatsapp import (
    ERROR_OUTSIDE_WINDOW,
    INBOUND_CHANNEL,
    MAX_BODY_PARAMS,
    MAX_EVENTS_SCANNED,
    MAX_PARAM_CHARS,
    MAX_RECIPIENTS,
    MAX_TEMPLATES,
    MAX_TEXT_CHARS,
    PHONE_RE,
    WINDOW_SECONDS,
    WHATSAPP_TOOLS,
    _parse_timestamp,
    register_whatsapp_tools,
    send,
    templates,
    whatsapp_config,
    window,
    window_sources,
    window_state,
)

TENANT = 't_sales'
AGENT = 'sales.outreach'
CONNECTION = 'sales'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

# The same instant, written three ways. All three must resolve identically; the
# naive fourth form must not resolve at all.
#
# These are PARSING fixtures and are deliberately fixed: the question they answer is
# "does the parser read this string correctly", and the answer does not change with
# the date. They must NOT be used to assert that a window is open.
UTC = 1_789_794_000.0            # 2026-09-19T05:00:00Z == 10:00 in Tashkent
TASHKENT = '+05:00'              # UTC+5: 10:00 local
ISO_Z = '2026-09-19T05:00:00Z'
ISO_OFFSET = '2026-09-19T10:00:00+05:00'
ISO_NAIVE = '2026-09-19 10:00:00'

# The window fixtures, deliberately RELATIVE to the clock at run time.
#
# This is the fix for a real defect found by an audit, not a stylistic preference: the
# window tests used to reuse ISO_OFFSET above, which is 2026-09-19T10:00+05:00. A
# 24-hour window opened then closes at 2026-09-20T10:00+05:00, so every "the window is
# open" assertion passed on the day it was written and then failed forever after
# 10:00 the next morning -- a test suite that rots. The parsing fixtures cannot move
# (they are about string handling), so the window fixtures are separated from them.
RECENT = time.time() - 60        # a customer wrote one minute ago: inside the window
STALE = time.time() - (WINDOW_SECONDS + 3600)   # closed for an hour


def _iso_offset(epoch):
    """Render an epoch as a Tashkent (+05:00) ISO-8601 string.

    The window register holds human-typed timestamps, so the fixtures have to be
    strings in the operator's timezone rather than epoch numbers: the point of the
    parsing tests is that the offset is honoured, and writing a UTC string here would
    quietly test a different thing.
    """
    stamp = datetime.datetime.fromtimestamp(epoch,
                                            datetime.timezone(datetime.timedelta(hours=5)))
    return stamp.isoformat()

POLICY = {
    'tools': ['sheets.rows', 'whatsapp.window', 'whatsapp.send', 'whatsapp.templates'],
    'allowed_connections': [CONNECTION, 'google'],
    'ladder': 'human_assisted',
}

REGISTERS = {
    'sales': {'connection': 'google', 'spreadsheet_id': SPREADSHEET,
              'ranges': {'windows': 'Oyna!A1:D'}, 'max_rows': 200},
}

WHATSAPP = {
    'registers': {
        'support': {
            'connection': 'sales',
            'phone_number_id': '123456789012345',
            'contacts': {'ali': '998901234567', 'dilnoza': '998907654321'},
            'templates': {
                'order_update': {'name': 'order_update_uz', 'language': 'uz',
                                 'category': 'utility'},
                'promo': {'name': 'promo_uz', 'language': 'uz', 'category': 'marketing'},
            },
            'window_register': 'sales',
            'window_range': 'windows',
            'last_inbound_column': 'oxirgi_xabar',
        },
    }
}


class _Sentinel:
    def __repr__(self):
        return 'MISSING'


_DEFAULT = _Sentinel()
_MISSING = _Sentinel()


class Clock:
    """The engine's injected clock, tied to the same "now" the window fixtures use.

    It used to default to a fixed 2026-09-19 epoch. Nothing compared it against the
    window (that reads the real clock through ``window_state``), so the tests passed --
    but an engine whose clock says September 19 running beside a window that says
    "today" is precisely the inconsistency a later test would trip on. Both now derive
    from the same RECENT base.
    """

    def __init__(self, start=None):
        self.now = RECENT if start is None else start

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


class RecordingPost:
    """Stands in for the Graph API POST. Records every call for the 131047 proof."""

    def __init__(self):
        self.answer = {'messages': [{'id': 'wamid.TEST'}]}
        self.calls = []

    def __call__(self, url, token, *, method='GET', body=None, timeout=20):
        self.calls.append({'url': url, 'token': token, 'method': method, 'body': body})
        return self.answer


class WhatsAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.transport = RecordingTransport()
        self.post = RecordingPost()
        self.registers = dict(REGISTERS)
        self.policy = dict(POLICY)
        self.window_rows = [
            ['contact', 'oxirgi_xabar', 'izoh'],
            # RECENT, not ISO_OFFSET: the window answer is computed against the real
            # clock (window_state defaults ``now`` to time.time()), so a fixed date
            # made this row "open" only for the 24 hours after the fixture was written.
            ['ali', _iso_offset(RECENT), 'window open'],
            ['dilnoza', '2020-01-01T00:00:00+05:00', 'long closed'],
        ]
        self.write_config()
        self.env = patch.dict(os.environ, {
            'PLATFORM_INTEGRATIONS_FILE': str(self.cfg),
            'WHATSAPP_SUPPORT_MESSAGING_TOKEN': 'messaging-token',
            'WHATSAPP_SUPPORT_MANAGEMENT_TOKEN': 'management-token',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.engine = Engine(self.root / 'whatsapp.db', build_registry(),
                             lambda t, a: self.policy, clock=self.clock)

    def write_config(self, whatsapp=_DEFAULT, tokens=_DEFAULT):
        self.cfg = self.root / 'integrations.json'
        payload = {
            'connections': {'google': {}, 'sales': {}},
            'sheets_registers': self.registers,
        }
        if whatsapp is not _MISSING:
            payload['whatsapp'] = WHATSAPP if whatsapp is _DEFAULT else whatsapp
        if tokens is not _MISSING:
            payload['whatsapp_tokens'] = (
                {'support': {'messaging': 'WHATSAPP_SUPPORT_MESSAGING_TOKEN',
                             'management': 'WHATSAPP_SUPPORT_MANAGEMENT_TOKEN'}}
                if tokens is _DEFAULT else tokens)
        # Integration config is keyed by tenant; every block lives under the
        # tenant object, not at the top level.
        self.cfg.write_text(json.dumps({TENANT: payload}, ensure_ascii=False),
                            encoding='utf-8')

    # ------------------------------------------------------------- harness

    def rows_context(self):
        """Patch the Sheets GET so the window register answers with our fixture."""
        self.transport.calls = []
        self.transport.payload = {'values': self.window_rows}
        manager = patch('platform_runtime.sheets.configured_manager')
        get = patch('platform_runtime.sheets._http_get',
                    side_effect=lambda url, token: self.transport(url, token))
        return manager, get

    def read_window(self, contact='', rows=None):
        if rows is not None:
            self.window_rows = rows
        manager, get = self.rows_context()
        with manager as m, get:
            m.return_value.access.return_value.access_token = 'fake-token'
            return window(self.engine, TENANT, AGENT, 's1', contact=contact)

    def post_context(self):
        self.post.calls = []
        return patch('platform_runtime.whatsapp._bounded_json',
                     side_effect=self.post)

    def do_send(self, args):
        manager, get = self.rows_context()
        with manager as m, get, self.post_context():
            m.return_value.access.return_value.access_token = 'fake-token'
            result = send(self.engine, TENANT, AGENT, args, 's1')
        return result, self.post

    def expect_refusal(self, args, error=Forbidden):
        manager, get = self.rows_context()
        with manager as m, get, self.post_context():
            m.return_value.access.return_value.access_token = 'fake-token'
            with self.assertRaises(error):
                send(self.engine, TENANT, AGENT, args, 's1')
        return self.post

    def seed_inbound(self, contact='ali', *, wrote_at=_DEFAULT, message_id=None):
        """Write a verified inbound event the way whatsapp_inbound.ingest would.

        The window answer reads these rows directly, so a test can place a message
        Meta signed without standing up the whole webhook path. The *shape* matters:
        conversation_id is the contact id and window_until is an epoch, which is
        exactly what ingest stores.

        ``wrote_at`` is the time the customer wrote, because that is the fact a caller
        reasons about and the fact the resolver returns. It is converted to the stored
        ``window_until`` (wrote_at + 24h) here, mirroring ingest, so a test cannot
        accidentally encode the expiry/last-inbound confusion the resolver once had.

        Timestamps passed by callers must be relative to ``self.clock.now``, not to
        ``time.time()``. The send gate reads the engine clock (see
        ``whatsapp._engine_now``), and the engine clock starts at ``RECENT``, which is
        captured at import -- so a fixture anchored on the wall clock is offset by
        however long the process has been running. Two tests that seeded
        ``time.time() - (WINDOW + 60)`` passed only while the gate ignored the engine
        clock: at a 60-second import drift they sat exactly on the boundary and flipped
        the moment the gate started reading the injected clock. Anchor on
        ``self.clock.now`` instead, and the fixture and the answer share one now by
        construction.

        The sentinel rather than None, because None is a *meaningful* value: it is what
        ingest stores when Meta supplied no usable timestamp. An earlier version of this
        helper could not express that, so a test asking for a null window silently got a
        good one.
        """
        last = RECENT if wrote_at is _DEFAULT else wrote_at
        expiry = None if last is None else last + WINDOW_SECONDS
        key = message_id or f'wamid.{contact}.{int(last) if last else 0}'
        with self.engine.tx() as c:
            c.execute(
                'INSERT INTO p_events(tenant,channel,event_key,fingerprint,payload) '
                'VALUES(?,?,?,?,?)',
                (TENANT, INBOUND_CHANNEL, key, 'fp-' + key,
                 json.dumps({'sender': contact, 'conversation_id': contact,
                             'text': 'Salom', 'window_until': expiry})))
        return key

    # -------------------------------------------------------------- window

    def test_window_math_is_pure(self):
        now = 1_000_000.0
        self.assertEqual((False, None), window_state(None, now))
        self.assertEqual((False, None), window_state(0, now))
        opened, closes = window_state(now - 10, now)
        self.assertTrue(opened)
        self.assertEqual(now - 10 + WINDOW_SECONDS, closes)
        opened, closes = window_state(now - WINDOW_SECONDS, now)
        self.assertFalse(opened)
        self.assertEqual(now, closes)

    def test_the_window_closes_exactly_at_the_boundary(self):
        """At ``last + 24h`` the window is already closed, not still open."""
        now = 5_000_000.0
        self.assertFalse(window_state(now - WINDOW_SECONDS, now)[0])
        self.assertTrue(window_state(now - WINDOW_SECONDS + 1, now)[0])

    def test_an_iso_offset_and_a_zulu_timestamp_are_the_same_instant(self):
        self.assertEqual(_parse_timestamp(ISO_Z), _parse_timestamp(ISO_OFFSET))
        self.assertEqual(UTC, _parse_timestamp(ISO_Z))

    def test_a_naive_timestamp_is_refused_not_assumed_utc(self):
        """Uzbekistan is UTC+5: assuming UTC would be a five-hour error."""
        self.assertIsNone(_parse_timestamp(ISO_NAIVE))

    def test_fractional_seconds_are_accepted(self):
        """The forms Python, JavaScript, Postgres and Go emit by default.

        An audit found this block rejected all of them, so a customer who had just
        written was read as never having written and every free-form reply was refused
        with "wait for the customer to message first". The regex was stricter than the
        datetime.fromisoformat it guards.
        """
        base = _parse_timestamp('2026-09-20T10:24:50+05:00')
        for value in ('2026-09-20T10:24:50.859388+05:00',
                      '2026-09-20T10:24:50.123456789+05:00',
                      '2026-09-20 10:24:50.5+05:00'):
            parsed = _parse_timestamp(value)
            self.assertIsNotNone(parsed, value)
            # The instant is the same to within the sub-second part, so the comparison
            # has to be a tolerance, not a decimal-place count: places=3 would demand
            # agreement to a millisecond and .859 is a 0.86 second difference.
            self.assertLess(abs(base - parsed), 1.0, value)
        # The JS toISOString() shape spells the same instant in Zulu: 05:24:50Z is
        # 10:24:50+05:00, so the two must land on the same epoch. Asserted so the test
        # cannot pass by ignoring the timezone in the fractional branch.
        self.assertLess(abs(base - _parse_timestamp('2026-09-20T05:24:50.859Z')), 1.0)

    def test_a_fraction_does_not_soften_the_timezone_requirement(self):
        """A fraction is a precision, not an excuse to drop the offset."""
        for value in ('2026-09-20T10:24:50.859',
                      '2026-09-20 10:24:50.859'):
            self.assertIsNone(_parse_timestamp(value), value)

    def test_an_ambiguous_local_date_is_refused(self):
        for value in ('10.01.2026', '19/09/2026', '19.09.2026 10:00'):
            self.assertIsNone(_parse_timestamp(value), value)

    def test_an_epoch_number_is_accepted(self):
        self.assertEqual(UTC, _parse_timestamp(int(UTC)))

    def test_the_window_read_reports_open_and_closed_per_contact(self):
        result = self.read_window()
        block = result['registers'][0]
        by_id = {row['contact']: row for row in block['contacts']}
        self.assertTrue(by_id['ali']['open'])
        self.assertFalse(by_id['dilnoza']['open'])
        self.assertTrue(by_id['ali']['free_form'])
        self.assertFalse(by_id['dilnoza']['free_form'])

    def test_the_window_read_says_when_it_closes(self):
        result = self.read_window()
        row = {r['contact']: r for r in result['registers'][0]['contacts']}['ali']
        # Stated against the fixture's own timestamp, not a hardcoded date: the
        # arithmetic is ``last inbound + 24h`` and the fixture is what supplies the
        # inbound time.
        self.assertEqual(_parse_timestamp(_iso_offset(RECENT)) + WINDOW_SECONDS,
                         row['closes_at'])

    def test_a_missing_timestamp_is_closed_not_unknown(self):
        """The dangerous default. Unknown must never read as open."""
        result = self.read_window(rows=[['contact', 'oxirgi_xabar'], ['ali', '']])
        row = result['registers'][0]['contacts'][0]
        self.assertFalse(row['open'])
        self.assertEqual('no inbound message', row['reason'])

    def test_an_unparsable_timestamp_is_closed_and_named(self):
        result = self.read_window(rows=[['contact', 'oxirgi_xabar'],
                                        ['ali', '10.01.2026']])
        row = result['registers'][0]['contacts'][0]
        self.assertFalse(row['open'])
        self.assertEqual('unparsable timestamp', row['reason'])

    def test_the_window_read_masks_the_phone_number(self):
        result = self.read_window()
        row = {r['contact']: r for r in result['registers'][0]['contacts']}['ali']
        self.assertNotIn('998901234567', json.dumps(result))
        self.assertTrue(row['phone'].startswith('99'))

    def test_an_undeclared_contact_is_refused(self):
        with self.assertRaises(Forbidden):
            self.read_window(contact='unknown')

    def test_no_register_at_all_is_a_declared_absence(self):
        self.write_config(whatsapp=_MISSING)
        result = self.read_window()
        self.assertEqual([], result['registers'])
        self.assertTrue(result['complete'])

    # ------------------------------------------------------- 131047 proof

    def test_free_form_outside_the_window_is_refused_before_any_io(self):
        """The core property. Refused, and the provider is never called."""
        posted = self.expect_refusal({'register': 'support', 'contact': 'dilnoza',
                                      'text': 'Salom, buyurtmangiz tayyor'})
        self.assertEqual([], posted.calls)

    def test_the_refusal_names_the_provider_error(self):
        manager, get = self.rows_context()
        with manager as m, get, self.post_context():
            m.return_value.access.return_value.access_token = 'fake-token'
            with self.assertRaises(Forbidden) as caught:
                send(self.engine, TENANT, AGENT,
                     {'register': 'support', 'contact': 'dilnoza', 'text': 'Salom'}, 's1')
        self.assertIn(str(ERROR_OUTSIDE_WINDOW), str(caught.exception))

    def test_free_form_inside_the_window_is_sent(self):
        result, posted = self.do_send({'register': 'support', 'contact': 'ali',
                                       'text': 'Salom, buyurtmangiz tayyor'})
        self.assertEqual('text', result['kind'])
        self.assertEqual(1, len(posted.calls))
        self.assertEqual('POST', posted.calls[0]['method'])
        self.assertEqual('Salom, buyurtmangiz tayyor',
                         posted.calls[0]['body']['text']['body'])

    def test_a_template_is_allowed_outside_the_window(self):
        """Meta's own rule: a template is the only path when the window is closed."""
        result, posted = self.do_send({'register': 'support', 'contact': 'dilnoza',
                                       'template': 'order_update'})
        self.assertEqual('template', result['kind'])
        self.assertEqual(1, len(posted.calls))

    def test_a_template_is_allowed_inside_the_window_too(self):
        result, posted = self.do_send({'register': 'support', 'contact': 'ali',
                                       'template': 'order_update'})
        self.assertEqual('template', result['kind'])
        self.assertEqual(1, len(posted.calls))

    def test_the_send_url_carries_the_declared_phone_number_id(self):
        _, posted = self.do_send({'register': 'support', 'contact': 'ali',
                                  'template': 'order_update'})
        self.assertIn('123456789012345', posted.calls[0]['url'])
        self.assertTrue(posted.calls[0]['url'].endswith('/messages'))

    def test_the_send_uses_the_declared_recipient_number(self):
        _, posted = self.do_send({'register': 'support', 'contact': 'ali',
                                  'template': 'order_update'})
        self.assertEqual('998901234567', posted.calls[0]['body']['to'])

    def test_exactly_one_of_text_or_template_is_required(self):
        for args in ({'register': 'support', 'contact': 'ali'},
                     {'register': 'support', 'contact': 'ali', 'text': 'x',
                      'template': 'order_update'},
                     {'register': 'support', 'contact': 'ali', 'text': '   '}):
            with self.subTest(args=args):
                self.expect_refusal(args, ValueError)

    def test_text_over_the_limit_is_refused(self):
        self.expect_refusal({'register': 'support', 'contact': 'ali',
                             'text': 'x' * (MAX_TEXT_CHARS + 1)}, ValueError)

    # ----------------------------------------------------- template safety

    def test_an_undeclared_template_is_refused(self):
        """The model selects a declared template; it does not compose one."""
        self.expect_refusal({'register': 'support', 'contact': 'ali',
                             'template': 'welcome'})

    def test_the_template_category_is_operator_declared_not_model_chosen(self):
        """A marketing template cannot be sent as utility: there is no category arg."""
        schema = build_registry().get('whatsapp.send').schema
        self.assertNotIn('category', schema['properties'])
        self.assertNotIn('language', schema['properties'])

    def test_the_declared_category_travels_with_the_template(self):
        declared = whatsapp_config(TENANT)['support']['templates']
        self.assertEqual('marketing', declared['promo']['category'])
        self.assertEqual('utility', declared['order_update']['category'])

    def test_a_template_needs_a_category(self):
        broken = json.loads(json.dumps(WHATSAPP))
        del broken['registers']['support']['templates']['order_update']['category']
        self.write_config(whatsapp=broken)
        with self.assertRaises(ValueError):
            whatsapp_config(TENANT)

    def test_an_unknown_category_is_refused(self):
        broken = json.loads(json.dumps(WHATSAPP))
        broken['registers']['support']['templates']['promo']['category'] = 'transactional'
        self.write_config(whatsapp=broken)
        with self.assertRaises(ValueError):
            whatsapp_config(TENANT)

    def test_a_bad_language_tag_is_refused(self):
        broken = json.loads(json.dumps(WHATSAPP))
        broken['registers']['support']['templates']['promo']['language'] = 'uzbek'
        self.write_config(whatsapp=broken)
        with self.assertRaises(ValueError):
            whatsapp_config(TENANT)

    def test_templates_are_listed_with_their_category(self):
        listed = templates(self.engine, TENANT, AGENT, 's1')['templates']
        self.assertEqual(2, len(listed))
        self.assertEqual({'marketing', 'utility'}, {row['category'] for row in listed})

    def test_the_listing_reports_the_meta_template_name_and_language(self):
        listed = {row['template']: row
                  for row in templates(self.engine, TENANT, AGENT, 's1')['templates']}
        self.assertEqual('order_update_uz', listed['order_update']['name'])
        self.assertEqual('uz', listed['order_update']['language'])

    # ------------------------------------------------------- config safety

    def test_on_premises_is_refused_by_configuration(self):
        """The On-Premises API was sunset on 2025-10-23; only Cloud exists."""
        broken = json.loads(json.dumps(WHATSAPP))
        broken['registers']['support']['api'] = 'on_premises'
        self.write_config(whatsapp=broken)
        with self.assertRaises(ValueError):
            whatsapp_config(TENANT)

    def test_an_unknown_register_key_is_refused(self):
        broken = json.loads(json.dumps(WHATSAPP))
        broken['registers']['support']['camera_url'] = 'rtsp://x'
        self.write_config(whatsapp=broken)
        with self.assertRaises(ValueError):
            whatsapp_config(TENANT)

    def test_a_non_digit_phone_number_id_is_refused(self):
        broken = json.loads(json.dumps(WHATSAPP))
        broken['registers']['support']['phone_number_id'] = 'abc'
        self.write_config(whatsapp=broken)
        with self.assertRaises(ValueError):
            whatsapp_config(TENANT)

    def test_a_malformed_contact_phone_is_refused(self):
        for phone in ('+998901234567', '0998', '998 90 123'):
            with self.subTest(phone=phone):
                broken = json.loads(json.dumps(WHATSAPP))
                broken['registers']['support']['contacts']['ali'] = phone
                self.write_config(whatsapp=broken)
                with self.assertRaises(ValueError):
                    whatsapp_config(TENANT)

    def test_an_undeclared_register_is_refused_not_empty(self):
        with self.assertRaises(Forbidden):
            send(self.engine, TENANT, AGENT,
                 {'register': 'nope', 'contact': 'ali', 'template': 'order_update'}, 's1')

    def test_an_undeclared_contact_cannot_be_messaged(self):
        """A model-supplied number is an unbounded send surface."""
        self.expect_refusal({'register': 'support', 'contact': '998900000000',
                             'template': 'order_update'})

    # ----------------------------------------------------------- credentials

    def test_the_send_uses_the_messaging_credential_only(self):
        _, posted = self.do_send({'register': 'support', 'contact': 'ali',
                                  'template': 'order_update'})
        self.assertEqual('messaging-token', posted.calls[0]['token'])

    def test_the_two_credentials_are_separate_config_keys(self):
        """messaging sends, management inspects. Neither can do the other's job."""
        self.write_config(tokens={'support': {'messaging': 'WHATSAPP_SUPPORT_MESSAGING_TOKEN'}})
        # A missing management credential does not stop a send, and a send does
        # not fall back to the management credential: the scopes are distinct and
        # the send reads only its own key.
        result, posted = self.do_send({'register': 'support', 'contact': 'ali',
                                       'template': 'order_update'})
        self.assertEqual(1, len(posted.calls))
        self.assertEqual('messaging-token', posted.calls[0]['token'])

    def test_a_missing_messaging_credential_stops_the_send(self):
        self.write_config(tokens={'support': {'management': 'WHATSAPP_SUPPORT_MANAGEMENT_TOKEN'}})
        self.expect_refusal({'register': 'support', 'contact': 'ali',
                             'template': 'order_update'},
                            error=RuntimeError)

    def test_a_send_credential_is_not_used_for_management_scoped_work(self):
        """No registered tool performs management-scoped work at all."""
        names = set(build_registry().items)
        self.assertFalse({n for n in names if 'account' in n or 'quality' in n})

    # ------------------------------------------------------------- surface

    def test_the_tool_surface_is_exactly_three_names(self):
        self.assertEqual(('whatsapp.window', 'whatsapp.send', 'whatsapp.templates'),
                         WHATSAPP_TOOLS)

    def test_the_tool_surface_has_no_template_authoring_path(self):
        """Submitting a template is a Meta review process, not an agent action."""
        names = ' '.join(build_registry().items)
        for banned in ('template.create', 'template.submit', 'whatsapp.account'):
            self.assertNotIn(banned, names)

    def test_the_module_exposes_no_broadcast_path(self):
        """One declared contact per call: there is no list-send anywhere."""
        schema = build_registry().get('whatsapp.send').schema
        props = schema['properties']
        # 'contact' is a single bounded string naming a declared contact, not a
        # list of recipients: there is no way to express a broadcast.
        self.assertEqual('string', props['contact']['type'])
        self.assertNotIn('contacts', props)
        self.assertNotIn('recipients', props)

    def test_the_send_tool_is_write_and_the_reads_are_read(self):
        registry = build_registry()
        self.assertEqual('write', registry.get('whatsapp.send').risk)
        self.assertEqual('read', registry.get('whatsapp.window').risk)
        self.assertEqual('read', registry.get('whatsapp.templates').risk)

    def test_the_send_tool_requires_an_approval(self):
        """The engine gates every non-read: approval is structural, not advisory."""
        spec = build_registry().get('whatsapp.send')
        self.assertNotEqual('read', spec.risk)
        self.assertTrue(spec.external)

    def test_the_engine_gates_the_whatsapp_destination_too(self):
        """Defence in depth: the engine, not only the adapter, checks the recipient.

        The engine verified outbound destinations for telegram/instagram from the
        start, but the tool set was hardcoded, so a newly registered outbound tool
        silently lost the check. whatsapp.send is now covered by the same rule and
        a direct send to an unlisted contact is refused at submission, before any
        approval and before any provider I/O.
        """
        with self.assertRaises(Forbidden):
            self.engine.submit(TENANT, 'api', 'evt-1', AGENT,
                               [{'tool': 'whatsapp.send',
                                 'args': {'register': 'support', 'contact': 'ali',
                                          'template': 'order_update'}}],
                               actor='owner')

    def test_a_direct_send_to_an_allowlisted_contact_is_accepted(self):
        self.policy = dict(POLICY, allowed_recipients=['ali'])
        engine = Engine(self.root / 'wa4.db', build_registry(),
                        lambda t, a: self.policy, clock=self.clock)
        task = engine.submit(TENANT, 'api', 'evt-2', AGENT,
                             [{'tool': 'whatsapp.send',
                               'args': {'register': 'support', 'contact': 'ali',
                                        'template': 'order_update'}}],
                             actor='owner')
        self.assertTrue(task)

    def test_the_module_never_reaches_the_provider_without_a_registered_tool(self):
        """Importing the module performs no I/O."""
        import platform_runtime.whatsapp as module
        self.assertFalse(hasattr(module, 'configured_manager'))

    # ------------------------------------------------------------- authority

    def test_the_window_read_needs_the_sheets_tool(self):
        self.policy = {'tools': ['whatsapp.window'],
                       'allowed_connections': [CONNECTION], 'ladder': 'human_assisted'}
        engine = Engine(self.root / 'wa2.db', build_registry(),
                        lambda t, a: self.policy, clock=self.clock)
        with self.assertRaises((Forbidden, LookupError)):
            window(engine, TENANT, AGENT, 's1')

    def test_a_connection_refusal_on_the_window_register_is_not_empty_data(self):
        """An authority refusal is re-raised, never reported as 'no data'."""
        self.policy = {'tools': ['sheets.rows', 'whatsapp.window'],
                       'allowed_connections': ['other'], 'ladder': 'human_assisted'}
        engine = Engine(self.root / 'wa3.db', build_registry(),
                        lambda t, a: self.policy, clock=self.clock)
        with self.assertRaises(Forbidden):
            window(engine, TENANT, AGENT, 's1')

    def test_a_frozen_tenant_is_not_silently_served(self):
        with self.engine.tx() as c:
            self.engine.require_active(c, TENANT)
        self.engine.freeze(TENANT, True, 'test')
        with self.assertRaises((Forbidden, Conflict)):
            with self.engine.read() as c:
                self.engine.require_active(c, TENANT)


# ------------------------------------------------- the window's two sources


class WindowSourceTests(WhatsAppTests):
    """A verified message and a hand-typed cell disagree; which one wins?

    Before this, the window was read only from the operator's sheet, so a customer's
    real message was ignored whenever nobody had updated a cell -- the inbound block
    recorded the window and nothing consulted it. These tests pin the rule that
    replaced it: a message Meta signed wins outright, and the sheet is the fallback.
    """

    def sources(self):
        entry = whatsapp_config(TENANT)['support']
        return window_sources(self.engine, TENANT, AGENT, entry, 's1',
                              contacts=entry['contacts'])

    def test_a_verified_event_wins_over_a_register_that_says_closed(self):
        """The case that made the inbound block load-bearing.

        The register fixture says ali wrote in 2020 (closed). A signed message from
        today must open the window anyway -- otherwise the platform is refusing to
        answer a customer who demonstrably just wrote, on the strength of a cell
        nobody updated.
        """
        self.seed_inbound('ali')
        resolved, sources, _ = self.sources()
        self.assertEqual('event', sources['ali'])
        self.assertEqual('at', resolved['ali'][0])
        rows = {r['contact']: r for r in self.read_window()['registers'][0]['contacts']}
        self.assertTrue(rows['ali']['open'])
        self.assertEqual('event', rows['ali']['source'])

    def test_the_register_still_answers_for_a_contact_with_no_event(self):
        """A tenant mid-migration keeps working: no event, fall back to the sheet."""
        self.seed_inbound('ali')
        rows = {r['contact']: r for r in self.read_window()['registers'][0]['contacts']}
        self.assertEqual('register', rows['dilnoza']['source'])
        self.assertFalse(rows['dilnoza']['open'])

    def test_a_stale_register_cannot_reopen_a_window_the_events_closed(self):
        """The precedence is not "the newer of the two", and this is why.

        If the winner were chosen by recency, a sheet cell with a timestamp slightly
        in the future would re-open a window the verified events say is closed. That is
        the platform granting itself permission from a value it never verified.
        """
        self.seed_inbound('ali', wrote_at=self.clock.now - (WINDOW_SECONDS + 60))
        # The register says ali wrote a minute ago -- i.e. open -- and it must lose.
        rows = {r['contact']: r for r in self.read_window()['registers'][0]['contacts']}
        self.assertEqual('event', rows['ali']['source'])
        self.assertFalse(rows['ali']['open'])

    def test_a_null_event_window_does_not_erase_a_register_window(self):
        """An unparsable delivery must not silently close a working window.

        whatsapp_inbound records window_until as None when Meta supplied no usable
        timestamp, on purpose. Treating that None as "closed" here would let one odd
        delivery close a window a previous good message opened, so the contact is left
        to the register fallback instead.
        """
        self.seed_inbound('ali', wrote_at=None, message_id='wamid.no-stamp')
        resolved, sources, _ = self.sources()
        self.assertEqual('register', sources['ali'])
        rows = {r['contact']: r for r in self.read_window()['registers'][0]['contacts']}
        self.assertTrue(rows['ali']['open'])

    def test_the_send_gate_uses_the_verified_event(self):
        """The whole point: the gate must act on the recorded fact, not the sheet.

        The register says closed; a signed message says open. A free-form send must go
        through, and must actually reach the provider.
        """
        self.seed_inbound('ali')
        result, post = self.do_send({'register': 'support', 'contact': 'ali',
                                     'text': 'Salom, buyurtmangiz tayyor'})
        self.assertEqual(1, len(post.calls))
        self.assertEqual('ali', result['contact'])

    def test_the_send_gate_answers_using_the_engine_clock(self):
        """The window question must use the same now as the rest of the platform.

        The engine takes an injectable clock, and every other time-dependent answer
        reads it: deadlines, leases, approval expiry, escalation windows. The send
        gate used to call ``window_state(last)`` with no ``now``, so it silently fell
        back to ``time.time()`` -- a clock the runtime does not control. A runtime
        driven by an injected clock (a test, a replay, clock skew between hosts) then
        had its send gate decided by something else, and the answer could not be
        pinned: two tests in this file seeded ``time.time() - (WINDOW + 60)`` and
        passed only because the gate ignored the engine clock.

        Measured by moving ONLY the engine clock past the window and leaving the wall
        clock alone. The customer wrote one minute before the engine's now, so under
        the engine clock the window is open; ten windows later it is long closed, and
        the gate must refuse. Before the fix it sent anyway.
        """
        args = {'register': 'support', 'contact': 'ali', 'text': 'Salom'}
        result, posted = self.do_send(dict(args))
        self.assertEqual(1, len(posted.calls),
                         'the window is open under the engine clock and must send')

        self.clock.now += WINDOW_SECONDS * 10
        with self.assertRaises(Forbidden) as caught:
            self.do_send(dict(args))
        self.assertIn(str(ERROR_OUTSIDE_WINDOW), str(caught.exception))

    def test_the_send_gate_still_refuses_when_the_event_window_is_closed(self):
        """And the inversion, so the previous test cannot pass by ignoring the gate."""
        self.seed_inbound('ali', wrote_at=self.clock.now - (WINDOW_SECONDS + 60))
        post = self.expect_refusal({'register': 'support', 'contact': 'ali',
                                    'text': 'Salom'})
        self.assertEqual([], post.calls)

    def test_the_event_window_is_not_double_counted(self):
        """The expiry stored in the event must be converted back to a send time.

        The event records ``window_until`` (the *expiry*: the customer's timestamp plus
        24h), while ``window_state`` takes the *last inbound time* and adds the 24h
        itself. Passing the expiry straight through adds the window twice, which
        reports a customer who wrote 25 hours ago as still open -- the precise send
        this module exists to refuse. An earlier revision of the resolver had exactly
        that bug; this is its regression.
        """
        # A message 25 hours old: outside a 24-hour window in either reading of the
        # word, so a wrong answer cannot be blamed on a boundary.
        wrote_at = time.time() - (WINDOW_SECONDS + 3600)
        self.seed_inbound('ali', wrote_at=wrote_at)
        # Keep the register out of the way so only the event can answer.
        self.window_rows = [['contact', 'oxirgi_xabar', 'izoh'], ['ali', '', '']]
        row = {r['contact']: r for r in self.read_window()['registers'][0]['contacts']}['ali']
        self.assertEqual('event', row['source'])
        self.assertFalse(row['open'], row)
        self.assertLess(abs(row['last_inbound'] - wrote_at), 5, row)

    def test_a_message_just_inside_the_window_is_open(self):
        """The same conversion, from the other side, so the fix cannot pass by
        refusing everything."""
        wrote_at = time.time() - 60
        self.seed_inbound('ali', wrote_at=wrote_at)
        self.window_rows = [['contact', 'oxirgi_xabar', 'izoh'], ['ali', '', '']]
        row = {r['contact']: r for r in self.read_window()['registers'][0]['contacts']}['ali']
        self.assertEqual('event', row['source'])
        self.assertTrue(row['open'], row)
        self.assertEqual(wrote_at + WINDOW_SECONDS, row['closes_at'], row)

    def test_the_newest_event_for_a_contact_is_the_one_used(self):
        """Two messages from one customer: the later timestamp sets the window."""
        older = time.time() - (WINDOW_SECONDS + 100)
        self.seed_inbound('ali', wrote_at=older, message_id='wamid.old')
        self.seed_inbound('ali', wrote_at=time.time() - 30, message_id='wamid.new')
        resolved, sources, _ = self.sources()
        self.assertEqual('event', sources['ali'])
        # Within a second of "30 seconds ago", not the stale one.
        self.assertLess(abs(resolved['ali'][1] - (time.time() - 30)), 5)

    def test_another_tenants_events_do_not_open_this_tenants_window(self):
        """The event read is scoped by tenant, like every other read in the engine."""
        with self.engine.tx() as c:
            c.execute('INSERT INTO p_events(tenant,channel,event_key,fingerprint,payload) '
                      'VALUES(?,?,?,?,?)',
                      ('someone_else', INBOUND_CHANNEL, 'wamid.x', 'fp',
                       json.dumps({'conversation_id': 'ali',
                                   'window_until': time.time()})))
        rows = {r['contact']: r for r in self.read_window()['registers'][0]['contacts']}
        # The other tenant's event is invisible, so this tenant falls back to its own
        # register -- which is what the fixture says, and is therefore the point: the
        # answer comes from the register, not from a row belonging to someone else.
        self.assertEqual('register', rows['ali']['source'])

    def test_a_broken_event_payload_is_skipped_not_fatal(self):
        """A row that is not the shape we wrote must not break the window answer."""
        with self.engine.tx() as c:
            for key, payload in (('wamid.bad1', 'not json'),
                                 ('wamid.bad2', json.dumps(['a', 'list'])),
                                 ('wamid.bad3', json.dumps({'conversation_id': 'ali',
                                                            'window_until': 'soon'}))):
                c.execute('INSERT INTO p_events(tenant,channel,event_key,fingerprint,'
                          'payload) VALUES(?,?,?,?,?)',
                          (TENANT, INBOUND_CHANNEL, key, 'fp', payload))
        report = self.read_window()
        self.assertEqual('ok', report['registers'][0]['status'])
        rows = {r['contact']: r for r in report['registers'][0]['contacts']}
        # The register fixture is the only usable source, so it answers.
        self.assertEqual('register', rows['ali']['source'])

    def test_the_two_service_window_constants_are_one_fact(self):
        """The window is restated in two modules, and the restatement is enforced.

        `whatsapp_inbound` keeps no module-scope import edge to the outbound block on
        purpose, so it declares the 24 hours itself. The existing round-trip tests
        build the expiry with ONE constant and read it back with the same one, so they
        pass whatever the two values are; this is the only thing that fails when the
        restatement drifts.
        """
        from platform_runtime import whatsapp_inbound
        self.assertEqual(WINDOW_SECONDS, whatsapp_inbound.WINDOW_SECONDS)

    def test_the_two_window_reads_follow_different_constants(self):
        """The drift would be asymmetric, and this is the measurement of why.

        The stored `window_until` is written with the INBOUND constant. The outbound
        reader subtracts its own copy and `window_state` adds it back, so the pair
        cancels and the events path follows the inbound value -- while the
        operator-register path, which carries a real last-inbound time, follows the
        outbound one. Setting the outbound constant to one hour therefore leaves the
        events path at twenty-four hours and the register path at one.
        """
        from platform_runtime import whatsapp, whatsapp_inbound
        now = 1_000_000.0
        expiry = now + whatsapp_inbound.WINDOW_SECONDS      # what ingest stores
        register_last = now - 10                            # what the sheet carries
        original = whatsapp.WINDOW_SECONDS
        try:
            whatsapp.WINDOW_SECONDS = 3600
            # the events path: -W on read, +W in window_state -> the pair cancels
            last = expiry - whatsapp.WINDOW_SECONDS
            _, closes = window_state(last, now + 1)
            self.assertEqual(expiry, closes)
            self.assertEqual(whatsapp_inbound.WINDOW_SECONDS, closes - now)
            # the register path: no stored expiry, so the outbound constant IS the window
            _, register_closes = window_state(register_last, now + 1)
            self.assertEqual(3600, register_closes - register_last)
        finally:
            whatsapp.WINDOW_SECONDS = original
        self.assertEqual(86400, whatsapp.WINDOW_SECONDS)


class DeclaredBoundTests(unittest.TestCase):
    """Fazza 31: the declared ceilings, pinned at their literal values.

    Seven of these were unpinned: mutating each one left the whole suite green. A
    constant that can drift without a failure is a constant nobody is checking, and
    every one of these is read by a guard that decides whether a message is sent.
    """

    def test_the_phone_ceiling_is_fifteen_digits(self):
        self.assertTrue(PHONE_RE.match('998901234567'))
        self.assertTrue(PHONE_RE.match('1' * 15))
        self.assertFalse(PHONE_RE.match('1' * 16))
        # E.164 has no leading zero, and a local number read as international is
        # how a message reaches a stranger.
        self.assertFalse(PHONE_RE.match('0' + '1' * 10))

    def test_the_outbound_text_ceiling_is_four_thousand_and_ninety_six(self):
        self.assertEqual(4096, MAX_TEXT_CHARS)
        tool = build_registry().get('whatsapp.send')
        self.assertEqual(MAX_TEXT_CHARS, tool.schema['properties']['text']['maxLength'])

    def test_the_event_scan_ceiling_is_five_hundred(self):
        """The literal AND the read that uses it, so a widened bound is visible."""
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'whatsapp.py').read_text(encoding='utf-8')
        self.assertIn('MAX_EVENTS_SCANNED = 500', source)
        self.assertIn('(tenant, INBOUND_CHANNEL, MAX_EVENTS_SCANNED)', source)
        self.assertEqual(500, MAX_EVENTS_SCANNED)

    def test_the_outside_window_error_code_is_the_one_meta_sends(self):
        """131047 is Meta's own code. The platform echoes it rather than inventing one,
        so an operator can search Meta's documentation for the failure they saw."""
        self.assertEqual(131047, ERROR_OUTSIDE_WINDOW)

    def test_the_recipient_and_template_ceilings(self):
        self.assertEqual(200, MAX_RECIPIENTS)
        self.assertEqual(100, MAX_TEMPLATES)
        self.assertEqual(20, MAX_BODY_PARAMS)
        self.assertEqual(400, MAX_PARAM_CHARS)

if __name__ == '__main__':
    unittest.main()
