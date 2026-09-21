"""The WhatsApp inbound path: signature first, then a decision, never a guess.

A real ``Engine`` and a real SQLite database are used throughout, because the
interesting properties are about what does and does not reach the durable inbox:
signature verification that happens before parsing, a status callback that is
dropped rather than planned, an undeclared number that cannot open a window, and
Meta's redelivery absorbed by the engine's own dedup key rather than a second one
invented here.
"""
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from platform_runtime.engine import Conflict, Engine, Forbidden
from platform_runtime.tools import build_registry
from platform_runtime.whatsapp_inbound import (
    CHANNEL,
    INGEST_TOOLS,
    MAX_BODY_BYTES,
    MAX_CHANGES,
    MAX_ENTRIES,
    MESSAGE_ID_RE,
    SIGNATURE_HEADER,
    WEBHOOK_OBJECT,
    WINDOW_SECONDS,
    WebhookError,
    declared_contacts,
    ingest,
    register_whatsapp_inbound_tools,
    unwrap,
    verify_challenge,
    verify_signature,
    webhook_status,
)

TENANT = 't_wa'
AGENT = 'ops.wa'
SECRET = 'unit-app-secret'
VERIFY_TOKEN = 'unit-verify-token'
ALI = '998901234567'
DILNOZA = '998907654321'


def policy(tenant, agent):
    return {'tools': list(INGEST_TOOLS), 'ladder': 'human_assisted'}


def sign(body, secret=SECRET):
    """What Meta puts in X-Hub-Signature-256: 'sha256=' + hex HMAC of the bytes."""
    return 'sha256=' + hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()


def webhook(messages=None, statuses=None, *, object_name='whatsapp_business_account',
            field='messages', wa_id=ALI, extra_changes=None):
    changes = []
    value = {'messaging_product': 'whatsapp',
             'metadata': {'display_phone_number': '15550001111',
                          'phone_number_id': '123456789012345'}}
    if messages or statuses:
        value['contacts'] = [{'profile': {'name': 'Ali'}, 'wa_id': wa_id}]
    if messages:
        value['messages'] = messages
    if statuses:
        value['statuses'] = statuses
    changes.append({'field': field, 'value': value})
    if extra_changes:
        changes.extend(extra_changes)
    return {'object': object_name,
            'entry': [{'id': '987654321098765', 'changes': changes}]}


def text_message(message_id='wamid.UNIT1', body='Salom', sender=ALI, timestamp='1789794000',
                 kind='text', **extra):
    payload = {'id': message_id, 'from': sender, 'timestamp': timestamp, 'type': kind}
    if kind == 'text':
        payload['text'] = {'body': body}
    payload.update(extra)
    return payload


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.cfg = self.root / 'integrations.json'
        self.write_config()
        self.env = mock.patch.dict(os.environ, {
            'PLATFORM_INTEGRATIONS_FILE': str(self.cfg),
            'META_APP_SECRET': SECRET,
            'META_VERIFY_TOKEN': VERIFY_TOKEN,
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.engine = Engine(self.root / 'wa.db', build_registry(), policy)

    def write_config(self, block=None, registers=None):
        payload = {
            'whatsapp': {'registers': registers if registers is not None else {
                'support': {
                    'connection': 'sales', 'phone_number_id': '123456789012345',
                    'contacts': {'ali': ALI, 'dilnoza': DILNOZA},
                }}},
            'whatsapp_webhook': block if block is not None else {
                'app_secret_env': 'META_APP_SECRET',
                'verify_token_env': 'META_VERIFY_TOKEN',
            },
        }
        self.cfg.write_text(json.dumps({TENANT: payload}), encoding='utf-8')

    def deliver(self, payload, *, secret=SECRET, headers=None):
        body = json.dumps(payload).encode('utf-8')
        sent = {SIGNATURE_HEADER: sign(body, secret)}
        if headers is not None:
            sent = headers
        return ingest(self.engine, TENANT, body, sent)

    def events(self):
        # p_events has no 'created' column: its columns are
        # (tenant, channel, event_key, fingerprint, payload, status, claim, lease,
        # result, error). An earlier version of this helper selected 'created' and
        # every test that read an event failed with "no such column" -- the helper was
        # wrong, not the module, so the fix belongs here.
        with self.engine.read() as c:
            return [dict(r) for r in c.execute(
                'SELECT event_key, status, payload FROM p_events WHERE tenant=? '
                'AND channel=? ORDER BY rowid', (TENANT, CHANNEL))]


# ------------------------------------------------------------------ signature


class SignatureTests(Base):
    """The signature is the only thing standing between the URL and a fake customer."""

    def test_the_signature_covers_the_exact_bytes(self):
        body = json.dumps(webhook([text_message()])).encode('utf-8')
        self.assertTrue(verify_signature(body, sign(body), SECRET))

    def test_a_reordered_body_is_a_different_signature(self):
        """The defect this guards: re-serialising to "tidy up" breaks the check.

        A developer who parses, then re-dumps, gets a byte string Meta never signed.
        Asserted explicitly because "it works when I test it with my own signer"
        hides it: signing the re-serialisation also round-trips, so the only way to
        see it is to sign one string and send another.
        """
        original = json.dumps(webhook([text_message()]), sort_keys=True).encode('utf-8')
        reordered = json.dumps(json.loads(original)).encode('utf-8')
        signature = sign(original)
        if original == reordered:
            self.skipTest('the two serialisations coincide on this runtime')
        self.assertFalse(verify_signature(reordered, signature, SECRET))

    def test_a_parsed_body_is_refused_outright(self):
        """Passing a dict is not a convenience we accept; it is the bug."""
        with self.assertRaises(WebhookError):
            verify_signature({'object': 'x'}, 'sha256=' + 'a' * 64, SECRET)

    def test_a_wrong_secret_fails(self):
        body = b'{"object":"whatsapp_business_account","entry":[]}'
        self.assertFalse(verify_signature(body, sign(body, 'not-the-secret'), SECRET))

    def test_a_tampered_body_fails(self):
        body = json.dumps(webhook([text_message(body='Salom')])).encode('utf-8')
        signature = sign(body)
        tampered = json.dumps(webhook([text_message(body='Transfer')])).encode('utf-8')
        self.assertFalse(verify_signature(tampered, signature, SECRET))

    def test_a_missing_signature_fails(self):
        self.assertFalse(verify_signature(b'{}', None, SECRET))
        self.assertFalse(verify_signature(b'{}', '', SECRET))

    def test_a_missing_prefix_fails(self):
        """Meta always says which algorithm. A bare digest is something else's."""
        body = b'{}'
        bare = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        self.assertFalse(verify_signature(body, bare, SECRET))

    def test_a_missing_secret_fails_closed(self):
        """No secret configured must never mean "everything is valid"."""
        body = b'{}'
        self.assertFalse(verify_signature(body, sign(body), ''))

    def test_a_truncated_digest_fails(self):
        body = b'{}'
        self.assertFalse(verify_signature(body, sign(body)[:20], SECRET))

    def test_an_uppercase_digest_is_accepted(self):
        """Hex case is not a security property, so it is not a reason to refuse."""
        body = b'{}'
        signature = sign(body).lower()
        self.assertTrue(verify_signature(body, signature.upper(), SECRET))

    def test_the_comparison_is_constant_time(self):
        """An early-exit compare leaks how many leading characters were right."""
        body = b'{}'
        good = sign(body)
        near = 'sha256=' + 'a' * 63 + good[-1]
        with mock.patch('hmac.compare_digest', wraps=hmac.compare_digest) as spy:
            verify_signature(body, good, SECRET)
            verify_signature(body, near, SECRET)
        self.assertEqual(2, spy.call_count)
        # And the near-miss is genuinely rejected, so the test is not vacuous.
        self.assertFalse(verify_signature(body, near, SECRET))

    def test_an_oversized_body_is_refused_before_it_is_hashed(self):
        """The bound is a guard, not a comment.

        MAX_BODY_BYTES was declared and documented as enforced while nothing read it,
        so an arbitrarily large body was hashed, decoded and walked. The refusal has to
        happen before the HMAC for the allocation it protects against to be avoided,
        which is why the check lives in verify_signature rather than in ingest.
        """
        body = b'x' * (MAX_BODY_BYTES + 1)
        signature = sign(body)
        with mock.patch('hmac.new', wraps=hmac.new) as spy:
            with self.assertRaises(WebhookError):
                verify_signature(body, signature, SECRET)
        self.assertEqual(0, spy.call_count)

    def test_a_body_at_the_bound_is_still_accepted(self):
        """An off-by-one that refuses a legal body is its own outage."""
        body = b'x' * MAX_BODY_BYTES
        self.assertTrue(verify_signature(body, sign(body), SECRET))


# ------------------------------------------------------------------ handshake


class ChallengeTests(Base):
    """The GET handshake: one unauthenticated value gets reflected, so gate it."""

    QUERY = {'hub.mode': 'subscribe', 'hub.verify_token': VERIFY_TOKEN,
             'hub.challenge': 'challenge-value-123'}

    def test_the_challenge_is_echoed_on_a_matching_token(self):
        self.assertEqual('challenge-value-123',
                         verify_challenge(self.QUERY, VERIFY_TOKEN))

    def test_a_wrong_token_echoes_nothing(self):
        query = dict(self.QUERY, **{'hub.verify_token': 'guess'})
        self.assertIsNone(verify_challenge(query, VERIFY_TOKEN))

    def test_a_wrong_mode_echoes_nothing(self):
        query = dict(self.QUERY, **{'hub.mode': 'unsubscribe'})
        self.assertIsNone(verify_challenge(query, VERIFY_TOKEN))

    def test_an_absent_challenge_echoes_nothing(self):
        query = {'hub.mode': 'subscribe', 'hub.verify_token': VERIFY_TOKEN}
        self.assertIsNone(verify_challenge(query, VERIFY_TOKEN))

    def test_no_configured_token_refuses_rather_than_echoing(self):
        with self.assertRaises(WebhookError):
            verify_challenge(self.QUERY, '')

    def test_the_shipped_tool_uses_the_same_comparison(self):
        """A route that wrote its own comparison would not be covered by these."""
        out = build_registry().get('whatsapp.verify').handler(
            self.engine, TENANT, AGENT, {'query': json.dumps(self.QUERY)}, 's1')
        self.assertEqual('challenge-value-123', out['challenge'])


# ------------------------------------------------------------------ envelope


class EnvelopeTests(Base):
    def test_a_text_message_becomes_one_event(self):
        report = self.deliver(webhook([text_message()]))
        self.assertEqual(['wamid.UNIT1'], report['accepted'])
        self.assertEqual([], report['dropped'])

    def test_another_products_event_is_refused(self):
        """A Messenger event read as a WhatsApp message attributes words nobody said."""
        with self.assertRaises(WebhookError):
            self.deliver(webhook([text_message()], object_name='page'))

    def test_multiple_messages_in_one_delivery_all_arrive(self):
        report = self.deliver(webhook([
            text_message('wamid.A', 'bir'), text_message('wamid.B', 'ikki')]))
        self.assertEqual(['wamid.A', 'wamid.B'], sorted(report['accepted']))

    def test_multiple_entries_are_all_walked(self):
        payload = webhook([text_message('wamid.A')])
        payload['entry'].append({'id': '2', 'changes': [
            {'field': 'messages', 'value': {'messaging_product': 'whatsapp',
                                            'contacts': [{'profile': {'name': 'D'},
                                                          'wa_id': DILNOZA}],
                                            'messages': [text_message('wamid.B',
                                                                      sender=DILNOZA)]}}]})
        report = self.deliver(payload)
        self.assertEqual(['wamid.A', 'wamid.B'], sorted(report['accepted']))

    def test_a_status_callback_is_dropped_not_planned(self):
        """'delivered' is Meta talking to us, not a customer talking to us."""
        report = self.deliver(webhook(statuses=[
            {'id': 'wamid.UNIT1', 'status': 'delivered', 'timestamp': '1789794000',
             'recipient_id': ALI}]))
        self.assertEqual([], report['accepted'])
        self.assertEqual(['status_callback'], [d['reason'] for d in report['dropped']])
        self.assertEqual([], self.events())

    def test_a_status_and_a_message_together_split_correctly(self):
        report = self.deliver(webhook([text_message()], statuses=[
            {'id': 'wamid.OLD', 'status': 'read', 'timestamp': '1789794000',
             'recipient_id': ALI}]))
        self.assertEqual(['wamid.UNIT1'], report['accepted'])
        self.assertIn('status_callback', [d['reason'] for d in report['dropped']])

    def test_an_undeclared_number_is_dropped(self):
        """The contact list is the definition of who this tenant's customers are."""
        report = self.deliver(webhook([text_message(sender='998900000000')]))
        self.assertEqual([], report['accepted'])
        self.assertEqual(['undeclared_contact'], [d['reason'] for d in report['dropped']])
        self.assertEqual([], self.events())

    def test_the_dropped_stranger_is_masked(self):
        report = self.deliver(webhook([text_message(sender='998900000000')]))
        recorded = json.dumps(report['dropped'])
        self.assertNotIn('998900000000', recorded)
        self.assertIn('*', recorded)

    def test_an_unsupported_type_is_dropped_with_its_type_named(self):
        for kind in ('image', 'audio', 'document', 'sticker'):
            with self.subTest(kind=kind):
                report = self.deliver(webhook([text_message(kind=kind)]))
                self.assertEqual([], report['accepted'])
                self.assertEqual('unsupported_type',
                                 report['dropped'][0]['reason'])
                self.assertEqual(kind, report['dropped'][0]['type'])

    def test_empty_text_is_dropped(self):
        report = self.deliver(webhook([text_message(body='   ')]))
        self.assertEqual([], report['accepted'])
        self.assertEqual('empty_text', report['dropped'][0]['reason'])

    def test_an_unsubscribed_field_is_dropped(self):
        report = self.deliver(webhook([text_message()], field='account_update'))
        self.assertEqual([], report['accepted'])
        self.assertEqual('field_not_subscribed', report['dropped'][0]['reason'])

    def test_a_message_without_an_id_is_dropped(self):
        message = text_message()
        message.pop('id')
        report = self.deliver(webhook([message]))
        self.assertEqual([], report['accepted'])

    def test_dropping_is_a_reason_not_an_exception(self):
        """Meta retries a non-2xx, so a malformed payload must not produce a raise."""
        report = self.deliver(webhook([{'type': 'text'}], statuses=[{'status': 'x'}]))
        self.assertIsInstance(report['dropped'], list)
        self.assertTrue(report['dropped'])


# ------------------------------------------------------------------ window


class WindowTests(Base):
    """The point of the block: the window stops depending on a hand-typed sheet."""

    def test_the_event_carries_the_window_it_opens(self):
        report = self.deliver(webhook([text_message(timestamp='1789794000')]))
        self.assertEqual(['wamid.UNIT1'], report['accepted'])
        stored = json.loads(self.events()[0]['payload'])
        self.assertEqual(1789794000 + WINDOW_SECONDS, stored['window_until'])
        # The CONTACT ID, not the phone number. Asserted as a contract rather than a
        # literal: the reply side addresses its destination with the contact id, so a
        # conversation_id holding the raw number could never match it.
        self.assertEqual('ali', stored['sender'])
        self.assertEqual('ali', stored['conversation_id'])
        self.assertEqual('Salom', stored['text'])

    def test_the_event_is_addressable_by_an_allowed_recipient(self):
        """The whole reason the contract matters, asserted end to end.

        allowed_recipients holds contact ids ('ali'), and whatsapp.send's destination
        field is 'contact'. So the inbound event must be keyed by the contact id for a
        reply to bind to it. Asserting the value alone would not catch a regression
        that kept the id shape but changed which list it came from.
        """
        PACK = ['ali', 'dilnoza']
        self.deliver(webhook([text_message()]))
        stored = json.loads(self.events()[0]['payload'])
        self.assertIn(stored['conversation_id'], PACK)
        self.assertIn(stored['sender'], declared_contacts(TENANT).values())

    def test_the_raw_phone_is_preserved_but_kept_out_of_the_destination(self):
        """The number is still needed (to read the window register), just not as an
        address: the outbound block resolves contact id -> phone itself."""
        self.deliver(webhook([text_message()]))
        stored = json.loads(self.events()[0]['payload'])
        self.assertEqual(ALI, stored['wa_id'])
        self.assertNotEqual(ALI, stored['conversation_id'])
        self.assertNotEqual(ALI, stored['sender'])

    def test_the_engine_payload_matches_the_canonical_shape(self):
        """The pipeline elsewhere sends {sender, conversation_id, text}. Same shape."""
        self.deliver(webhook([text_message()]))
        stored = json.loads(self.events()[0]['payload'])
        for key in ('sender', 'conversation_id', 'text'):
            self.assertIn(key, stored)

    def test_a_missing_timestamp_is_not_read_as_epoch_zero(self):
        """An unparsable timestamp must not open the window at all.

        A zero would close it silently; our own clock would open it on a fact
        about the server rather than about the customer. Neither is acceptable,
        so the window is reported as not opened and the reason is explained.
        """
        self.deliver(webhook([text_message(timestamp='not-a-number')]))
        stored = json.loads(self.events()[0]['payload'])
        self.assertIsNone(stored['received_at'])
        self.assertIsNone(stored['window_until'])

    def test_the_window_is_never_opened_from_the_host_clock(self):
        """The window must not depend on when our server happened to process it.

        ``unwrap`` takes ``now`` for the caller's convenience, so the trap is easy
        to fall into: two calls with wildly different clocks must still produce the
        identical ``window_until``, because the only fact that may open a customer
        window is the customer's own message timestamp.
        """
        payload = webhook([text_message()])
        early = unwrap(payload, {ALI: 'ali'}, now=1_000_000)
        late = unwrap(payload, {ALI: 'ali'}, now=1_999_999_999)
        self.assertEqual(early['events'][0]['window_until'],
                         late['events'][0]['window_until'])
        self.assertEqual(1789794000 + WINDOW_SECONDS,
                         early['events'][0]['window_until'])

    def test_the_declared_contact_is_the_sender_not_the_profile_name(self):
        """The profile name is customer-supplied and must never be the address."""
        self.deliver(webhook([text_message()]))
        stored = json.loads(self.events()[0]['payload'])
        self.assertEqual('ali', stored['sender'])
        self.assertEqual('Ali', stored['profile_name'])
        self.assertNotEqual(stored['sender'], stored['profile_name'])

    def test_an_unknown_whatsapp_number_has_no_declared_contact(self):
        self.assertEqual({ALI: 'ali', DILNOZA: 'dilnoza'}, declared_contacts(TENANT))

    def test_contacts_come_from_the_outbound_registers_not_a_second_list(self):
        """Two lists with the same meaning is how they end up disagreeing."""
        self.write_config(registers={'support': {
            'connection': 'sales', 'phone_number_id': '123456789012345',
            'contacts': {'ali': ALI}}})
        self.assertEqual({ALI: 'ali'}, declared_contacts(TENANT))


# ------------------------------------------------------------------ ingestion


class IngestTests(Base):
    def test_a_bad_signature_is_refused_and_nothing_is_stored(self):
        body = json.dumps(webhook([text_message()])).encode('utf-8')
        with self.assertRaises(Forbidden):
            ingest(self.engine, TENANT, body,
                   {SIGNATURE_HEADER: sign(body, 'wrong')})
        self.assertEqual([], self.events())

    def test_a_bad_signature_is_audited(self):
        body = json.dumps(webhook([text_message()])).encode('utf-8')
        with self.assertRaises(Forbidden):
            ingest(self.engine, TENANT, body, {SIGNATURE_HEADER: 'sha256=' + '0' * 64})
        with self.engine.read() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT action FROM p_audit WHERE tenant=? AND action LIKE 'webhook.%'",
                (TENANT,))]
        self.assertEqual(['webhook.signature_rejected'], [r['action'] for r in rows])

    def test_a_missing_signature_header_is_refused(self):
        body = json.dumps(webhook([text_message()])).encode('utf-8')
        with self.assertRaises(Forbidden):
            ingest(self.engine, TENANT, body, {})
        self.assertEqual([], self.events())

    def test_the_header_name_is_matched_case_insensitively(self):
        """HTTP header names are case-insensitive; a strict compare is a real bug."""
        body = json.dumps(webhook([text_message()])).encode('utf-8')
        report = ingest(self.engine, TENANT, body,
                        {'X-Hub-Signature-256': sign(body)})
        self.assertEqual(['wamid.UNIT1'], report['accepted'])

    def test_a_redelivery_is_absorbed_as_a_duplicate(self):
        """Meta is at-least-once, so the same message id WILL arrive twice."""
        payload = webhook([text_message()])
        first = self.deliver(payload)
        second = self.deliver(payload)
        self.assertEqual(['wamid.UNIT1'], first['accepted'])
        self.assertEqual([], second['accepted'])
        self.assertEqual(['wamid.UNIT1'], second['duplicates'])
        self.assertEqual(1, len(self.events()))

    def test_the_same_id_with_different_content_is_refused_not_overwritten(self):
        """Guessing which delivery is real is how a forgery replaces a message."""
        self.deliver(webhook([text_message(body='Salom')]))
        report = self.deliver(webhook([text_message(body='Boshqa narsa')]))
        self.assertEqual([], report['accepted'])
        self.assertEqual('fingerprint_conflict', report['refused'][0]['reason'])
        stored = json.loads(self.events()[0]['payload'])
        self.assertEqual('Salom', stored['text'])

    def test_the_event_key_is_the_message_id(self):
        self.deliver(webhook([text_message(message_id='wamid.KEY')]))
        self.assertEqual('wamid.KEY', self.events()[0]['event_key'])

    def test_the_channel_is_whatsapp(self):
        self.deliver(webhook([text_message()]))
        with self.engine.read() as c:
            row = c.execute('SELECT channel FROM p_events WHERE tenant=?',
                            (TENANT,)).fetchone()
        self.assertEqual('whatsapp', row['channel'])

    def test_a_frozen_tenant_takes_no_events(self):
        self.engine.freeze(TENANT, True, 'test')
        with self.assertRaises((Forbidden, Conflict)):
            self.deliver(webhook([text_message()]))

    def test_a_body_that_is_not_json_is_refused_after_the_signature(self):
        body = b'not json at all'
        with self.assertRaises(WebhookError):
            ingest(self.engine, TENANT, body, {SIGNATURE_HEADER: sign(body)})

    def test_a_missing_secret_configuration_refuses(self):
        self.write_config(block={'verify_token_env': 'META_VERIFY_TOKEN'})
        with self.assertRaises(WebhookError):
            self.deliver(webhook([text_message()]))

    def test_a_secret_that_is_not_set_refuses(self):
        self.write_config(block={'app_secret_env': 'META_ABSENT_SECRET',
                                 'verify_token_env': 'META_VERIFY_TOKEN'})
        with self.assertRaises(WebhookError):
            self.deliver(webhook([text_message()]))

    def test_the_status_report_counts_what_arrived(self):
        self.deliver(webhook([text_message()]))
        out = webhook_status(self.engine, TENANT, AGENT, None)
        self.assertEqual(1, out['count'])
        self.assertEqual('wamid.UNIT1', out['events'][0]['event_key'])


# ------------------------------------------------------------------ surface


class SurfaceTests(Base):
    def test_two_tools_are_exposed(self):
        registry = build_registry()
        for name in INGEST_TOOLS:
            self.assertIn(name, registry.items, name)

    def test_both_are_read_only(self):
        """Ingest is not a tool: a model that could accept a webhook could fake one."""
        registry = build_registry()
        for name in INGEST_TOOLS:
            self.assertEqual('read', registry.get(name).risk, name)

    def test_no_tool_accepts_a_raw_message_body(self):
        """A tool taking a signed body would be a way to manufacture an inbound."""
        registry = build_registry()
        for name in INGEST_TOOLS:
            props = set(registry.get(name).schema['properties'])
            for banned in ('body', 'payload', 'signature', 'message', 'text',
                           'from', 'wa_id'):
                self.assertNotIn(banned, props, f'{name} accepts {banned!r}')

    def test_the_query_argument_is_json_text_not_a_nested_object(self):
        """The shape that made the document block unsubmittable, asserted here too."""
        schema = build_registry().get('whatsapp.verify').schema
        self.assertEqual('string', schema['properties']['query']['type'])

    def test_every_tool_accepts_a_wellformed_call(self):
        registry = build_registry()
        registry.get('whatsapp.webhook').validate({'limit': 10})
        registry.get('whatsapp.webhook').validate({})
        registry.get('whatsapp.verify').validate({'query': json.dumps({'hub.mode': 'x'})})

    def test_a_none_limit_does_not_reach_int(self):
        """Direct callers get the documented default, not a bare TypeError.

        The registry rejects limit=None, so this is not reachable through the tool
        path -- but webhook_status is also a plain function a route calls directly, and
        int(None) raising TypeError is a worse answer than the default it documents.
        """
        self.assertEqual(0, webhook_status(self.engine, TENANT, AGENT, None,
                                           limit=None)['count'])

    def test_an_ambiguous_number_is_refused_not_guessed(self):
        """The same number under two ids must not resolve by dict ordering.

        Before this guard the mapping was last-one-wins over ``_registers`` insertion
        order, so reordering a config file silently changed which customer a customer
        was -- and the words of one person got attributed to another.
        """
        self.write_config(registers={
            'support': {'connection': 'sales',
                        'phone_number_id': '123456789012345',
                        'contacts': {'ali': ALI}},
            'sales': {'connection': 'sales',
                      'phone_number_id': '123456789012345',
                      'contacts': {'bek': ALI}},
        })
        with self.assertRaises(WebhookError):
            declared_contacts(TENANT)

    def test_the_same_number_under_the_same_id_is_not_a_conflict(self):
        """Two registers naming one contact consistently is redundancy, not ambiguity."""
        self.write_config(registers={
            'support': {'connection': 'sales',
                        'phone_number_id': '123456789012345',
                        'contacts': {'ali': ALI}},
            'sales': {'connection': 'sales',
                      'phone_number_id': '123456789012345',
                      'contacts': {'ali': ALI}},
        })
        self.assertEqual({ALI: 'ali'}, declared_contacts(TENANT))

    def test_no_ingest_function_is_registered_as_a_tool(self):
        names = ' '.join(build_registry().items)
        for banned in ('ingest', 'accept_event', 'webhook.ingest'):
            self.assertNotIn(banned, names, banned)

    def test_the_module_never_opens_a_socket(self):
        """A verifier that fetches is a verifier with an extra attack surface.

        Checked by importing the module with the network libraries poisoned, rather
        than by grepping its source: an earlier version of this test searched for the
        word 'socket.' and matched the docstring of the very function it was meant to
        protect. A source scan answers "does this word appear"; the question is "does
        this module reach the network", and only the second one is worth asserting.
        """
        import importlib
        import platform_runtime.whatsapp_inbound as module

        def refuse(*args, **kwargs):
            raise AssertionError('the inbound verifier reached the network')

        for name in ('socket', 'http', 'urllib'):
            module.__dict__.pop(name, None)
        with mock.patch.dict('sys.modules', {
                'socket': mock.MagicMock(create_connection=refuse),
                'urllib.request': mock.MagicMock(urlopen=refuse)}):
            reloaded = importlib.reload(module)
        # The module imported cleanly and its pure functions still work.
        self.assertTrue(reloaded.verify_signature(
            b'{}', 'sha256=' + hmac.new(SECRET.encode(), b'{}',
                                        hashlib.sha256).hexdigest(), SECRET))
        # And it genuinely has no network import of its own.
        source_names = set(reloaded.__dict__)
        for banned in ('socket', 'urlopen', 'requests'):
            self.assertNotIn(banned, source_names, banned)


class DegenerateTests(unittest.TestCase):
    """Inputs that are not payloads at all."""

    def test_unwrap_refuses_a_non_object(self):
        with self.assertRaises(WebhookError):
            unwrap([], {}, now=1)

    def test_unwrap_refuses_a_missing_entry(self):
        with self.assertRaises(WebhookError):
            unwrap({'object': 'whatsapp_business_account'}, {}, now=1)

    def test_unwrap_of_an_empty_delivery_is_empty_not_an_error(self):
        out = unwrap({'object': 'whatsapp_business_account', 'entry': []}, {}, now=1)
        self.assertEqual([], out['events'])
        self.assertEqual([], out['dropped'])

    def test_a_message_limit_bounds_a_hostile_delivery(self):
        messages = [text_message(f'wamid.{i}') for i in range(200)]
        out = unwrap(webhook(messages), {ALI: 'ali'}, now=1)
        self.assertLessEqual(len(out['events']), 50)

    def test_an_epoch_is_an_int_not_a_string(self):
        """String-compared against our clock, every window would report open."""
        out = unwrap(webhook([text_message(timestamp='1789794000')]), {ALI: 'ali'}, now=1)
        self.assertIsInstance(out['events'][0]['timestamp'], int)
        self.assertIsInstance(out['events'][0]['window_until'], int)


class DeclaredBoundTests(Base):
    """Fazza 31: the ingest ceilings, pinned by driving the real envelope."""

    def test_the_webhook_body_bound_is_one_megabyte(self):
        """The guard lives in ``verify_signature``, before the body is hashed.

        The literal is asserted SEPARATELY from the behaviour: the loop below derives
        its payload from the same symbol, so a widened bound moves the test with it
        and the suite stays green. Measured -- ``MAX_BODY_BYTES = 1_000_001`` did.
        """
        self.assertEqual(1_000_000, MAX_BODY_BYTES)
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'whatsapp_inbound.py').read_text(encoding='utf-8')
        self.assertIn('MAX_BODY_BYTES = 1_000_000', source)
        for size, raises in ((MAX_BODY_BYTES, False), (MAX_BODY_BYTES + 1, True)):
            payload = b'x' * size
            signature = 'sha256=' + hmac.new(SECRET.encode(), payload,
                                             hashlib.sha256).hexdigest()
            with self.subTest(size=size):
                if raises:
                    with self.assertRaises(WebhookError):
                        verify_signature(payload, signature, SECRET)
                else:
                    self.assertTrue(verify_signature(payload, signature, SECRET))

    def test_the_entry_ceiling_is_fifty(self):
        payload = webhook([text_message('wamid.E0')])
        for index in range(1, 60):
            payload['entry'].append({'id': str(index), 'changes': [
                {'field': 'messages', 'value': {
                    'messaging_product': 'whatsapp',
                    'contacts': [{'profile': {'name': 'D'}, 'wa_id': DILNOZA}],
                    'messages': [text_message('wamid.E%d' % index, sender=DILNOZA)]}}]})
        report = self.deliver(payload)
        # Sixty entries, fifty walked: the rest are not even looked at.
        self.assertEqual(MAX_ENTRIES, len(report['accepted']))
        self.assertEqual(50, MAX_ENTRIES)

    def test_the_change_ceiling_is_fifty(self):
        changes = []
        for index in range(60):
            changes.append({'field': 'messages', 'value': {
                'messaging_product': 'whatsapp',
                'contacts': [{'profile': {'name': 'D'}, 'wa_id': DILNOZA}],
                'messages': [text_message('wamid.C%d' % index, sender=DILNOZA)]}})
        payload = {'object': WEBHOOK_OBJECT, 'entry': [{'id': '1', 'changes': changes}]}
        report = self.deliver(payload)
        self.assertEqual(MAX_CHANGES, len(report['accepted']))
        self.assertEqual(50, MAX_CHANGES)

    def test_the_message_id_ceiling_is_one_hundred_and_twenty_eight(self):
        self.assertTrue(MESSAGE_ID_RE.match('a' * 128))
        self.assertFalse(MESSAGE_ID_RE.match('a' * 129))
        self.assertTrue(MESSAGE_ID_RE.match('wamid.ABC-123_x:y'))
        self.assertFalse(MESSAGE_ID_RE.match('wamid ABC'))

if __name__ == '__main__':
    unittest.main()
