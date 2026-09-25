"""WhatsApp as a customer conversation channel, end to end, on a real Engine.

A customer writes to the tenant's business number; the verified message becomes a
``whatsapp`` inbound event keyed by Meta's message id, with the sender's wa_id as the
conversation; the same conversation turn Telegram uses answers it; and the reply goes
back through ``whatsapp.send`` to exactly that wa_id -- the engine binds the
destination to the verified event, the model never picks it.

The 24-hour service window is modelled as Meta enforces it: a free-form reply is
allowed only while the customer's last message is younger than 24 hours. Outside it
Meta answers error 131047 and delivers nothing, so 131047 -- whether our own gate
predicts it before any I/O, or Meta returns it -- is a DEFINITE non-delivery: the step
is ``failed``, never ``uncertain``.

Socket-free: the Graph POST is either a recording stand-in for ``_bounded_json`` or a
patched opener that answers with a scripted HTTP error.
"""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from platform_runtime import whatsapp, whatsapp_inbound
from platform_runtime.agent_loop import AgentLoop
from platform_runtime.conversation import ConversationTurns
from platform_runtime.engine import DeliveryRejected, Engine, Forbidden
from platform_runtime.tools import build_registry

T = 'shop'
AGENT = 'bot'
NUMBER = '106540352242922'          # the business phone_number_id customers write to
OTHER_NUMBER = '106540352242999'
WA_ID = '998901112233'              # a customer nobody declared
STRANGER = '998907778899'
TOKEN = 'EAAG-unit-messaging-token-never-logged'
SECRET = 'unit-meta-app-secret'
START = 1_790_000_000.0


def message(mid='wamid.C1', body='Salom, futbolka bormi?', sender=WA_ID, stamp=None, **fields):
    item = {'id': mid, 'from': sender, 'timestamp': str(int(START if stamp is None else stamp)),
            'type': fields.pop('type', 'text')}
    if item['type'] == 'text':
        item['text'] = {'body': body}
    item.update(fields)
    return item


def delivery(messages=(), statuses=(), number=NUMBER, sender=WA_ID, field='messages'):
    value = {'messaging_product': 'whatsapp',
             'metadata': {'display_phone_number': '998712000000', 'phone_number_id': number},
             'contacts': [{'profile': {'name': 'Ali'}, 'wa_id': sender}]}
    if messages:
        value['messages'] = list(messages)
    if statuses:
        value['statuses'] = list(statuses)
    return {'object': 'whatsapp_business_account',
            'entry': [{'id': 'WABA1', 'changes': [{'field': field, 'value': value}]}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.packs = self.root / 'packs'
        self.packs.mkdir()
        self.cfg = self.root / 'integrations.json'
        self.write_config()
        env = patch.dict(os.environ, {'PLATFORM_INTEGRATIONS_FILE': str(self.cfg),
                                      'PACKS_DIR': str(self.packs),
                                      'WA_SHOP_TOKEN': TOKEN, 'META_APP_SECRET': SECRET})
        env.start()
        self.addCleanup(env.stop)
        self.now = START + 60
        self.policy = {'tools': ['whatsapp.send', 'telegram.send'], 'ladder': 'autonomous',
                       'approval': [], 'allowed_recipients': [], 'allowed_connections': [],
                       'conversation': {'enabled': True, 'fallback_text': 'Operator tez orada javob beradi.'}}

        def policy(tenant, agent):
            if agent != AGENT:
                raise Forbidden('Agent not in tenant pack')
            return self.policy
        self.e = Engine(self.root / 'wa.db', build_registry(), policy, clock=lambda: self.now)
        self.posts = []
        self.answer = {'messaging_product': 'whatsapp', 'messages': [{'id': 'wamid.OUT1'}]}

    def write_config(self, registers=None, extra=None):
        registers = registers if registers is not None else {
            'shop': {'connection': 'whatsapp', 'phone_number_id': NUMBER,
                     'contacts': {'ali': '998901234567'}}}
        block = {'whatsapp': {'registers': registers},
                 'whatsapp_tokens': {name: {'messaging': 'WA_SHOP_TOKEN'} for name in registers}}
        block.update(extra or {})
        self.cfg.write_text(json.dumps({T: block, '_comment': 'not a tenant'}), encoding='utf-8')

    def post(self, url, token, *, method='GET', body=None, timeout=20):
        self.posts.append({'url': url, 'token': token, 'method': method, 'body': body})
        return self.answer

    def accept(self, *messages, number=NUMBER):
        report = whatsapp_inbound.customer_messages(delivery(messages, number=number))
        return whatsapp_inbound.accept_customer_messages(self.e, T, report['events'])

    def rows(self, sql, *args):
        with self.e.read() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def events(self):
        return [dict(r, payload=json.loads(r['payload'])) for r in self.rows(
            'SELECT event_key,payload FROM p_events WHERE tenant=? AND channel=? ORDER BY rowid',
            T, 'whatsapp')]


# ------------------------------------------------------------------ parsing


class CustomerMessageTests(unittest.TestCase):
    def parse(self, *messages, **kw):
        return whatsapp_inbound.customer_messages(delivery(messages, **kw))

    def test_any_customer_who_writes_becomes_an_event_keyed_by_wa_id(self):
        """The conversation channel: no declared-contact allowlist, like Telegram."""
        out = self.parse(message())
        self.assertEqual([], out['dropped'])
        [event] = out['events']
        self.assertEqual('wamid.C1', event['message_id'])
        self.assertEqual(NUMBER, event['phone_number_id'])
        payload = event['payload']
        self.assertEqual(WA_ID, payload['sender'])
        self.assertEqual(WA_ID, payload['conversation_id'])
        self.assertEqual('Salom, futbolka bormi?', payload['text'])
        self.assertEqual(NUMBER, payload['phone_number_id'])
        self.assertEqual(int(START), payload['received_at'])
        self.assertEqual(int(START) + whatsapp.WINDOW_SECONDS, payload['window_until'])

    def test_media_becomes_a_bracketed_placeholder_or_its_caption(self):
        cases = [
            (message(type='image', image={'id': 'm1'}), '[rasm]'),
            (message(type='image', image={'id': 'm1', 'caption': 'Shu bormi?'}), 'Shu bormi?'),
            (message(type='video', video={'id': 'm1'}), '[video]'),
            (message(type='document', document={'id': 'm1', 'filename': 'x.pdf'}), '[fayl]'),
            (message(type='audio', audio={'id': 'm1', 'voice': True}), '[ovozli xabar]'),
            (message(type='audio', audio={'id': 'm1'}), '[audio]'),
        ]
        for item, text in cases:
            with self.subTest(text=text):
                [event] = self.parse(item)['events']
                self.assertEqual(text, event['payload']['text'])

    def test_button_and_interactive_replies_arrive_as_their_text(self):
        cases = [
            (message(type='button', button={'text': 'Ha, buyurtma', 'payload': 'yes'}), 'Ha, buyurtma'),
            (message(type='interactive', interactive={
                'type': 'button_reply', 'button_reply': {'id': 'b1', 'title': 'Yetkazib berish'}}),
             'Yetkazib berish'),
            (message(type='interactive', interactive={
                'type': 'list_reply', 'list_reply': {'id': 'l1', 'title': '110 sm', 'description': 'x'}}),
             '110 sm'),
        ]
        for item, text in cases:
            with self.subTest(text=text):
                [event] = self.parse(item)['events']
                self.assertEqual(text, event['payload']['text'])

    def test_what_carries_nothing_to_answer_is_dropped_with_a_reason(self):
        for item in (message(type='sticker', sticker={'id': 's'}),
                     message(type='reaction', reaction={'message_id': 'x', 'emoji': 'ok'}),
                     message(type='interactive', interactive={'type': 'nfm_reply'}),
                     message(body='   ')):
            with self.subTest(kind=item['type']):
                out = self.parse(item)
                self.assertEqual([], out['events'])
                self.assertIn(out['dropped'][0]['reason'], ('unsupported_type', 'empty_text'))

    def test_status_updates_are_not_customer_words(self):
        out = self.parse(statuses=[
            {'id': 'wamid.OUT1', 'status': 'delivered', 'timestamp': '1', 'recipient_id': WA_ID},
            {'id': 'wamid.OUT2', 'status': 'failed', 'timestamp': '1', 'recipient_id': WA_ID,
             'errors': [{'code': 131047, 'title': 'Re-engagement message'}]}])
        self.assertEqual([], out['events'])
        self.assertEqual(['status_callback', 'status_callback'], [d['reason'] for d in out['dropped']])
        self.assertNotIn(WA_ID, json.dumps(out['dropped']))

    def test_a_dropped_senders_number_is_masked(self):
        out = self.parse(message(type='sticker', sticker={'id': 's'}))
        self.assertNotIn(WA_ID, json.dumps(out['dropped']))

    def test_the_customer_text_is_bounded_like_every_inbound_channel(self):
        [event] = self.parse(message(body='x' * 5000))['events']
        self.assertEqual(4000, len(event['payload']['text']))

    def test_another_product_or_shape_is_refused_as_a_whole(self):
        with self.assertRaises(whatsapp_inbound.WebhookError):
            whatsapp_inbound.customer_messages(dict(delivery([message()]), object='page'))
        with self.assertRaises(whatsapp_inbound.WebhookError):
            whatsapp_inbound.customer_messages([])

    def test_phone_number_ids_are_read_for_routing_only_and_bounded(self):
        payload = delivery([message()])
        payload['entry'].append({'id': 'WABA2', 'changes': [
            {'field': 'messages', 'value': {'metadata': {'phone_number_id': OTHER_NUMBER}}},
            {'field': 'messages', 'value': {'metadata': {'phone_number_id': NUMBER}}},
            {'field': 'messages', 'value': {'metadata': {'phone_number_id': '12 34'}}},
            'not a change']})
        self.assertEqual([NUMBER, OTHER_NUMBER], whatsapp_inbound.phone_number_ids(payload))
        for junk in (None, [], 'x', {'entry': 'x'}, {'entry': [{'changes': {'a': 1}}]}):
            self.assertEqual([], whatsapp_inbound.phone_number_ids(junk))


# ------------------------------------------------------------------ routing


class RoutingTests(Base):
    def test_the_business_number_names_exactly_one_tenant(self):
        self.assertEqual([T], whatsapp_inbound.tenants_for_phone_number(NUMBER))
        self.assertEqual([], whatsapp_inbound.tenants_for_phone_number(OTHER_NUMBER))

    def test_a_pack_local_integrations_file_is_scanned_too(self):
        tenant = self.packs / 'kids'
        tenant.mkdir()
        (tenant / 'integrations.yaml').write_text(
            'whatsapp:\n  registers:\n    main:\n      connection: whatsapp\n'
            f'      phone_number_id: "{OTHER_NUMBER}"\n', encoding='utf-8')
        self.assertEqual(['kids'], whatsapp_inbound.tenants_for_phone_number(OTHER_NUMBER))

    def test_a_number_two_tenants_declare_is_ambiguous_not_guessed(self):
        tenant = self.packs / 'kids'
        tenant.mkdir()
        (tenant / 'integrations.yaml').write_text(
            'whatsapp:\n  registers:\n    main:\n      connection: whatsapp\n'
            f'      phone_number_id: "{NUMBER}"\n', encoding='utf-8')
        self.assertEqual(['kids', T], whatsapp_inbound.tenants_for_phone_number(NUMBER))

    def test_the_app_secret_defaults_to_meta_app_secret_and_a_tenant_may_override(self):
        self.assertEqual(SECRET, whatsapp_inbound.app_secret_for(None))
        self.assertEqual(SECRET, whatsapp_inbound.app_secret_for(T))
        self.write_config(extra={'whatsapp_webhook': {'app_secret_env': 'SHOP_META_SECRET'}})
        with patch.dict(os.environ, {'SHOP_META_SECRET': 'shop-own-secret'}):
            self.assertEqual('shop-own-secret', whatsapp_inbound.app_secret_for(T))
        with self.assertRaises(whatsapp_inbound.WebhookError):
            whatsapp_inbound.app_secret_for(T)  # declared but unset: refused, not defaulted


# ------------------------------------------------------------------ acceptance


class AcceptTests(Base):
    def test_a_message_is_accepted_once_and_a_redelivery_is_a_duplicate(self):
        first = self.accept(message())
        again = self.accept(message())
        self.assertEqual((['wamid.C1'], []), (first['accepted'], first['duplicates']))
        self.assertEqual(([], ['wamid.C1']), (again['accepted'], again['duplicates']))
        [event] = self.events()
        self.assertEqual('wamid.C1', event['event_key'])
        self.assertEqual(WA_ID, event['payload']['conversation_id'])

    def test_the_same_id_with_other_content_is_refused_not_overwritten(self):
        self.accept(message(body='Salom'))
        out = self.accept(message(body='Boshqa'))
        self.assertEqual('fingerprint_conflict', out['refused'][0]['reason'])
        self.assertEqual('Salom', self.events()[0]['payload']['text'])

    def test_a_frozen_tenant_takes_no_events(self):
        self.e.freeze(T, True, 'owner')
        with self.assertRaises(Forbidden):
            self.accept(message())
        self.assertEqual([], self.events())


# ------------------------------------------------------------------ reply path


class ConversationReplyTests(Base):
    def setUp(self):
        super().setUp()
        self.turns = ConversationTurns(self.e, AgentLoop(self.e))
        cfg = patch('platform_runtime.conversation.config', return_value={})
        cfg.start()  # no llm block: the turn replies with the pack's fallback text
        self.addCleanup(cfg.stop)

    def planner(self, tenant, channel, payload):
        return {'agent': AGENT, 'conversation': self.policy['conversation']}

    def converse(self, *messages):
        self.accept(*messages)
        while self.e.process_event(T, self.planner):
            pass
        with patch('platform_runtime.whatsapp._bounded_json', side_effect=self.post):
            for _ in range(20):
                if not (self.turns.tick(T) or self.e.tick(T)):
                    break

    def turn(self, key='wamid.C1'):
        return self.rows('SELECT * FROM p_conversation_turns WHERE tenant=? AND channel=? AND event_key=?',
                         T, 'whatsapp', key)[0]

    def step(self):
        return self.rows("SELECT tool,args,status,error FROM p_steps WHERE tenant=? AND tool='whatsapp.send'", T)[0]

    def test_the_reply_goes_to_the_verified_wa_id_through_whatsapp_send(self):
        self.converse(message())
        self.assertEqual('delivered', self.turn()['status'])
        [sent] = self.posts
        self.assertEqual(f'{whatsapp.GRAPH_BASE}/{NUMBER}/messages', sent['url'])
        self.assertEqual(WA_ID, sent['body']['to'])
        self.assertEqual('text', sent['body']['type'])
        self.assertEqual('Operator tez orada javob beradi.', sent['body']['text']['body'])
        self.assertEqual(TOKEN, sent['token'])
        step = self.step()
        self.assertEqual({'contact': WA_ID, 'text': 'Operator tez orada javob beradi.'}, json.loads(step['args']))
        self.assertEqual('succeeded', step['status'])

    def test_a_reply_past_the_window_is_failed_before_any_io(self):
        """A reply that waited too long (an approval, a stalled worker) is outside the window."""
        self.policy['ladder'] = 'human_assisted'
        self.converse(message())
        [approval] = self.rows("SELECT step FROM p_approvals WHERE tenant=? AND status='pending'", T)
        self.e.approve(T, approval['step'], 'owner', 'approved', 'owner')
        self.now = START + whatsapp.WINDOW_SECONDS + 5
        with patch('platform_runtime.whatsapp._bounded_json', side_effect=self.post):
            self.assertTrue(self.e.tick(T))
        self.assertEqual([], self.posts)
        step = self.step()
        self.assertEqual(('failed', 'DeliveryRejected: window_closed'), (step['status'], step['error']))

    def test_meta_131047_is_a_definite_failure_and_the_turn_is_handed_off(self):
        body = json.dumps({'error': {'code': 131047, 'message': 'Re-engagement message ' + TOKEN,
                                     'fbtrace_id': 'x'}}).encode()
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError(
            'https://graph.facebook.com/v21.0/x/messages?access_token=' + TOKEN, 400, 'Bad Request',
            {}, io.BytesIO(body))
        self.accept(message())
        while self.e.process_event(T, self.planner):
            pass
        with patch('urllib.request.build_opener', return_value=opener):
            for _ in range(20):
                if not (self.turns.tick(T) or self.e.tick(T)):
                    break
        step = self.step()
        self.assertEqual(('failed', 'DeliveryRejected: meta_131047'), (step['status'], step['error']))
        self.assertEqual('failed', self.turn()['status'])
        self.assertEqual(1, len(self.rows("SELECT id FROM p_records WHERE tenant=? AND kind='conversation.handoff'", T)))
        everything = json.dumps([self.rows('SELECT * FROM p_steps'), self.rows('SELECT * FROM p_audit'),
                                 self.rows('SELECT * FROM p_conversation_turns')])
        self.assertNotIn(TOKEN, everything)

    def test_an_operator_can_answer_a_whatsapp_customer(self):
        """whatsapp now owns a verified inbound stream, so the takeover reply works too."""
        self.accept(message())
        task = self.e.operator_reply(T, 'whatsapp', WA_ID, 'Salom, men operatorman.', actor='olga',
                                     role='operator', key='k1', agent=AGENT)
        with patch('platform_runtime.whatsapp._bounded_json', side_effect=self.post):
            self.assertTrue(self.e.tick(T))
        self.assertEqual('succeeded', self.e.get(T, task)['status'])
        self.assertEqual(WA_ID, self.posts[0]['body']['to'])

    def test_a_whatsapp_task_cannot_name_another_customer(self):
        self.accept(message())
        with self.assertRaises(Forbidden):
            self.e.submit(T, 'whatsapp', 'wamid.C1', AGENT,
                          [{'tool': 'whatsapp.send', 'args': {'contact': STRANGER, 'text': 'x'}}], WA_ID)


# ------------------------------------------------------------------ send gate


class SendGateTests(Base):
    def send(self, args, step='s1'):
        with patch('platform_runtime.whatsapp._bounded_json', side_effect=self.post):
            return whatsapp.send(self.e, T, AGENT, args, step)

    def test_a_wa_id_that_never_wrote_is_refused_before_io(self):
        with self.assertRaises(Forbidden) as caught:
            self.send({'register': 'shop', 'contact': STRANGER, 'text': 'Salom'})
        self.assertIsInstance(caught.exception, DeliveryRejected)
        with self.assertRaises(Forbidden):
            self.send({'contact': STRANGER, 'text': 'Salom'})
        self.assertEqual([], self.posts)

    def test_a_customer_of_one_number_is_not_answered_from_another(self):
        self.write_config(registers={
            'shop': {'connection': 'whatsapp', 'phone_number_id': NUMBER},
            'second': {'connection': 'whatsapp', 'phone_number_id': OTHER_NUMBER}})
        self.accept(message())
        with self.assertRaises(Forbidden):
            self.send({'register': 'second', 'contact': WA_ID, 'text': 'Salom'})
        self.assertEqual([], self.posts)
        self.send({'contact': WA_ID, 'text': 'Salom'})
        self.assertIn(NUMBER, self.posts[0]['url'])

    def test_the_window_follows_the_customers_latest_message(self):
        self.accept(message())
        self.now = START + whatsapp.WINDOW_SECONDS + 5
        with self.assertRaises(Forbidden) as caught:
            self.send({'contact': WA_ID, 'text': 'Salom'})
        self.assertEqual('window_closed', caught.exception.reason)
        self.assertIn(str(whatsapp.ERROR_OUTSIDE_WINDOW), str(caught.exception))
        self.accept(message('wamid.C2', 'Yana men', stamp=self.now - 10))
        self.send({'contact': WA_ID, 'text': 'Salom'})
        self.assertEqual(1, len(self.posts))

    def test_a_template_may_reach_a_customer_outside_the_window(self):
        self.write_config(registers={'shop': {
            'connection': 'whatsapp', 'phone_number_id': NUMBER,
            'templates': {'follow_up': {'name': 'follow_up_uz', 'language': 'uz', 'category': 'utility'}}}})
        self.accept(message())
        self.now = START + 3 * whatsapp.WINDOW_SECONDS
        out = self.send({'contact': WA_ID, 'template': 'follow_up'})
        self.assertEqual('template', out['kind'])
        self.assertEqual(WA_ID, self.posts[0]['body']['to'])

    def test_a_declared_contact_window_counts_a_route_message_by_wa_id(self):
        """A declared contact who writes through the route opens their own window."""
        self.accept(message(sender='998901234567'))
        self.send({'register': 'shop', 'contact': 'ali', 'text': 'Salom Ali'})
        self.assertEqual('998901234567', self.posts[0]['body']['to'])


class ProviderVerdictTests(Base):
    def call(self, error):
        opener = MagicMock()
        opener.open.side_effect = error
        with patch('urllib.request.build_opener', return_value=opener):
            return whatsapp._bounded_json(f'{whatsapp.GRAPH_BASE}/{NUMBER}/messages', TOKEN,
                                          method='POST', body={'to': WA_ID})

    def http_error(self, code, body):
        return urllib.error.HTTPError('https://graph.facebook.com/x?access_token=' + TOKEN, code,
                                      'refused', {}, io.BytesIO(body))

    def test_131047_is_a_definite_rejection_that_carries_no_provider_text(self):
        body = json.dumps({'error': {'code': 131047, 'message': TOKEN}}).encode()
        with self.assertRaises(DeliveryRejected) as caught:
            self.call(self.http_error(400, body))
        self.assertEqual('meta_131047', caught.exception.reason)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_any_4xx_is_a_definite_rejection(self):
        for code, body, reason in ((401, b'{"error":{"code":190}}', 'meta_190'),
                                   (400, b'not json', 'http_400'),
                                   (429, b'{"error":{"code":"x"}}', 'http_429')):
            with self.subTest(code=code):
                with self.assertRaises(DeliveryRejected) as caught:
                    self.call(self.http_error(code, body))
                self.assertEqual(reason, caught.exception.reason)

    def test_5xx_and_transport_failures_stay_uncertain(self):
        for error in (self.http_error(502, b'{"error":{"code":131047}}'),
                      urllib.error.URLError('down ' + TOKEN), TimeoutError('t')):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(whatsapp.WhatsAppError) as caught:
                    self.call(error)
                self.assertNotIsInstance(caught.exception, DeliveryRejected)
                self.assertNotIn(TOKEN, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
