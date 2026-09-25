"""Operator takeover reply: a human answers a customer from the dashboard.

The engine's outbound rule has two authorising shapes -- the pack allowlist and
the verified inbound event a task answers -- and this adds a third, narrower one:
an authenticated owner/operator sends `<channel>.send` to a conversation that has
previously sent this tenant a verified inbound event on that channel. The human
IS the approver: the approval row is decided by that actor at submission, so the
audit trail names who sent what, and the claim path re-checks the actor's
authority and the conversation's existence the way `_origin` is re-read.

Every case runs a real Engine on real SQLite with a stubbed send adapter and an
authority hook that can revoke an actor, so revocation between submit and claim
is observed through `tick` rather than assumed.
"""
import json
import tempfile
import unittest
from pathlib import Path

from platform_runtime.engine import (OPERATOR_CHANNEL, OPERATOR_REPLY_PREFIX, Conflict, Engine,
                                     Forbidden, NotFound)
from platform_runtime.operator_reply import OPERATOR_LINE_KIND, settle_operator_replies
from platform_runtime.tools import Tool, build_registry

T = 't'
CUSTOMER = '555'
CHAT = 'chat-555'
OPERATOR = 'olga'
# The channels runtime_authority verifies a membership on; everything else is a
# provider stream whose sender is not a workspace member.
from app.runtime_authority import MEMBER_CHANNELS


class OperatorReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sent = []
        self.registry = build_registry()
        for name in ('telegram.send', 'instagram.send'):
            real = self.registry.get(name)

            def send(engine, tenant, agent, args, key, name=name):
                self.sent.append((name, dict(args)))
                return {'provider': name.split('.')[0], 'external_id': str(len(self.sent))}
            self.registry.items[name] = Tool(real.name, real.risk, real.schema, send, external=True)
        self.policies = {
            'bot': {'tools': ['telegram.send', 'instagram.send', 'products.search'], 'ladder': 'autonomous',
                    'approval': [], 'allowed_recipients': [], 'allowed_connections': []},
            'clerk': {'tools': ['records.create'], 'ladder': 'human_assisted', 'approval': [],
                      'allowed_recipients': [], 'allowed_connections': []},
        }
        self.revoked = set()
        self.now = 1000.0

        def policy(tenant, agent):
            if agent not in self.policies:
                raise Forbidden('Agent not in tenant pack')
            return self.policies[agent]

        def authority(c, tenant, channel='', actor='', roles=('owner', 'operator')):
            if channel in MEMBER_CHANNELS and actor in self.revoked:
                raise Forbidden('Actor permission revoked')
        self.e = Engine(Path(self.tmp.name) / 'o.db', self.registry, policy,
                        clock=lambda: self.now, authority=authority)

    # --- helpers ---------------------------------------------------------------

    def inbound(self, tenant=T, channel='telegram', key='m1', chat=CHAT):
        self.e.accept_event(tenant, channel, key, {'text': 'Narxi?', 'sender': CUSTOMER,
                                                   'conversation_id': chat})

    def reply(self, text='Salom, 189 000 so‘m.', *, tenant=T, channel='telegram', chat=CHAT,
              actor=OPERATOR, role='operator', key='k1', agent='bot'):
        return self.e.operator_reply(tenant, channel, chat, text, actor=actor, role=role,
                                     key=key, agent=agent)

    def task(self, tid, tenant=T):
        return self.e.get(tenant, tid)

    def rows(self, sql, *args):
        with self.e.read() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def history(self, chat=CHAT, channel='telegram'):
        return [(r['role'], r['text']) for r in self.rows(
            'SELECT role,text FROM p_conversation_history WHERE tenant=? AND channel=? '
            'AND conversation_id=? ORDER BY seq', T, channel, chat)]

    # --- the happy path, and what it records --------------------------------------

    def test_reply_to_a_known_conversation_is_sent_with_the_operator_as_approver(self):
        self.inbound()
        tid = self.reply()
        task = self.task(tid)
        self.assertEqual(OPERATOR_CHANNEL, task['channel'])
        self.assertEqual(OPERATOR_REPLY_PREFIX + 'k1', task['event_key'])
        self.assertEqual('bot', task['agent'])
        self.assertEqual(OPERATOR, task['actor'])
        self.assertEqual('queued', task['status'])
        step = task['steps'][0]
        self.assertEqual('telegram.send', step['tool'])
        self.assertEqual({'conversation_id': CHAT, 'text': 'Salom, 189 000 so‘m.'}, step['args'])
        # The operator's decision IS the approval: recorded, decided, attributed.
        self.assertEqual(1, step['approval_needed'])
        self.assertEqual('approved', step['approval_status'])
        self.assertEqual(OPERATOR, step['approver'])
        self.assertTrue(self.e.tick(T))
        self.assertEqual('succeeded', self.task(tid)['status'])
        self.assertEqual([('telegram.send', {'conversation_id': CHAT, 'text': 'Salom, 189 000 so‘m.'})],
                         self.sent)
        self.assertEqual('consumed', self.task(tid)['steps'][0]['approval_status'])
        actions = [(r['action'], r['actor']) for r in
                   self.rows('SELECT action,actor FROM p_audit WHERE task=? ORDER BY id', tid)]
        self.assertIn(('task.created', OPERATOR), actions)
        self.assertIn(('approval.approved', OPERATOR), actions)

    def test_the_ladder_does_not_hold_the_operators_own_reply(self):
        for ladder in ('human_led', 'human_assisted', 'autonomous'):
            with self.subTest(ladder=ladder):
                self.sent.clear()
                self.policies['bot']['ladder'] = ladder
                self.inbound(key='m-' + ladder)
                tid = self.reply(key='k-' + ladder)
                self.assertTrue(self.e.tick(T))
                self.assertEqual('succeeded', self.task(tid)['status'])
                self.assertEqual(1, len(self.sent))

    def test_the_approval_is_recorded_even_where_the_agent_would_be_exempt(self):
        """An allowlisted recipient and an autonomous agent: still the human's decision."""
        self.policies['bot']['allowed_recipients'] = [CHAT]
        self.inbound()
        step = self.task(self.reply())['steps'][0]
        self.assertEqual(1, step['approval_needed'])
        self.assertEqual(OPERATOR, step['approver'])

    def test_owner_may_reply_too(self):
        self.inbound()
        tid = self.reply(actor='boss', role='owner')
        self.assertEqual('boss', self.task(tid)['steps'][0]['approver'])

    def test_instagram_is_bound_the_same_way(self):
        self.inbound(channel='instagram', chat='ig-9')
        with self.assertRaises(NotFound):
            self.reply(channel='telegram', chat='ig-9')
        tid = self.reply(channel='instagram', chat='ig-9')
        self.assertTrue(self.e.tick(T))
        self.assertEqual('succeeded', self.task(tid)['status'])
        self.assertEqual(('instagram.send', {'conversation_id': 'ig-9', 'text': 'Salom, 189 000 so‘m.'}),
                         self.sent[0])

    # --- refusals at submission -------------------------------------------------------

    def test_unknown_conversation_is_refused(self):
        with self.assertRaises(NotFound):
            self.reply()
        self.inbound()
        with self.assertRaises(NotFound):
            self.reply(chat='stranger')
        self.assertEqual([], self.e.list_tasks(T))
        self.assertEqual([], self.sent)

    def test_another_tenants_conversation_is_refused(self):
        self.inbound(tenant='other')
        with self.assertRaises(NotFound):
            self.reply(tenant=T)
        self.assertEqual([], self.sent)

    def test_a_web_event_is_not_a_customer_conversation(self):
        """Only channels with a verified inbound stream can be replied to at all."""
        self.e.accept_event(T, 'web', 'w1', {'text': 'x', 'sender': OPERATOR, 'conversation_id': OPERATOR})
        for channel in ('web', 'cron', OPERATOR_CHANNEL, ''):
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                self.reply(channel=channel, chat=OPERATOR)

    def test_whatsapp_now_owns_a_stream_so_the_refusal_is_the_unknown_chat(self):
        """whatsapp joined OUTBOUND_CHANNELS when app/whatsapp_api.py gave it a
        verified webhook; a reply to a chat that never wrote is NotFound, the same
        refusal telegram gives an unknown chat -- not 'the channel cannot carry it'.
        """
        self.e.accept_event(T, 'web', 'w1', {'text': 'x', 'sender': OPERATOR, 'conversation_id': OPERATOR})
        with self.assertRaises(NotFound):
            self.reply(channel='whatsapp', chat='998901112233')
        self.assertEqual([], self.e.list_tasks(T))
        self.assertEqual([], self.sent)

    def test_viewer_and_integrator_are_refused(self):
        self.inbound()
        for role in ('viewer', 'integrator', 'device', '', None):
            with self.subTest(role=role), self.assertRaises(Forbidden):
                self.reply(role=role)
        self.assertEqual([], self.e.list_tasks(T))

    def test_a_revoked_actor_cannot_submit(self):
        self.inbound()
        self.revoked.add(OPERATOR)
        with self.assertRaises(Forbidden):
            self.reply()

    def test_owner_only_approver_policy_refuses_an_operator(self):
        self.policies['bot']['approver_role'] = 'owner'
        self.inbound()
        with self.assertRaises(Forbidden):
            self.reply(role='operator')
        tid = self.reply(actor='boss', role='owner')
        self.assertTrue(self.e.tick(T))
        self.assertEqual('succeeded', self.task(tid)['status'])

    def test_an_agent_without_the_send_tool_is_refused(self):
        self.inbound()
        with self.assertRaises(Forbidden):
            self.reply(agent='clerk')
        with self.assertRaises(Forbidden):
            self.reply(agent='ghost')

    def test_frozen_tenant_is_refused(self):
        self.inbound()
        self.e.freeze(T, True, 'boss')
        with self.assertRaises(Forbidden):
            self.reply()
        self.assertEqual([], self.e.list_tasks(T))

    def test_text_and_identity_bounds(self):
        self.inbound()
        for text in ('', '   ', 'x' * 4001, None, 5):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.reply(text=text)
        with self.assertRaises(ValueError):
            self.reply(key='')
        with self.assertRaises(ValueError):
            self.reply(key='k' * 300)
        with self.assertRaises(ValueError):
            self.reply(chat='')
        self.assertEqual([], self.e.list_tasks(T))

    def test_channel_text_ceiling_is_the_tools_own(self):
        """instagram.send takes 1000 characters; the reply is refused, not silently cut."""
        self.inbound(channel='instagram', chat='ig-9')
        with self.assertRaises(ValueError):
            self.reply(channel='instagram', chat='ig-9', text='x' * 1001)
        self.reply(channel='instagram', chat='ig-9', text='x' * 1000)

    def test_a_destructive_send_tool_cannot_use_this_path(self):
        """Even if a channel's send tool carried destructive risk, the path refuses it."""
        real = self.registry.get('instagram.send')
        self.registry.items['instagram.send'] = Tool(real.name, 'destructive', real.schema,
                                                     real.handler, external=True)
        self.inbound(channel='instagram', chat='ig-9')
        with self.assertRaises(Forbidden):
            self.reply(channel='instagram', chat='ig-9')
        self.assertEqual([], self.sent)

    def test_the_operator_channel_is_reserved_for_this_path(self):
        """A generic submit cannot mint an operator task, whatever the recipient."""
        self.policies['bot']['allowed_recipients'] = [CHAT]
        with self.assertRaises(Forbidden):
            self.e.submit(T, OPERATOR_CHANNEL, 'k1', 'bot',
                          [{'tool': 'telegram.send', 'args': {'conversation_id': CHAT, 'text': 'x'}}], OPERATOR)
        with self.assertRaises(Forbidden):
            self.e.submit(T, OPERATOR_CHANNEL, 'k2', 'bot', [{'tool': 'products.search', 'args': {'query': 'x'}}],
                          OPERATOR)

    # --- idempotency -------------------------------------------------------------------

    def test_same_key_same_body_is_the_same_task(self):
        self.inbound()
        first = self.reply()
        self.assertEqual(first, self.reply())
        self.assertEqual(1, len(self.e.list_tasks(T)))
        self.assertTrue(self.e.tick(T))
        self.assertFalse(self.e.tick(T))
        self.assertEqual(1, len(self.sent))

    def test_same_key_different_body_is_a_conflict(self):
        self.inbound()
        self.inbound(key='m2', chat='chat-2')
        self.reply()
        with self.assertRaises(Conflict):
            self.reply(text='boshqa matn')
        with self.assertRaises(Conflict):
            self.reply(chat='chat-2')
        self.assertEqual(1, len(self.e.list_tasks(T)))

    def test_keys_are_scoped_per_tenant(self):
        self.inbound()
        self.inbound(tenant='other')
        self.assertNotEqual(self.reply(), self.reply(tenant='other'))

    # --- re-validation at claim -------------------------------------------------------

    def test_authority_revoked_between_submit_and_claim_is_not_sent(self):
        self.inbound()
        tid = self.reply()
        self.revoked.add(OPERATOR)
        self.assertFalse(self.e.tick(T))
        task = self.task(tid)
        self.assertEqual('failed', task['status'])
        # The creator (the same operator) is re-checked first at claim.
        self.assertEqual('authority_revoked', task['steps'][0]['error'])
        self.assertEqual([], self.sent)

    def test_conversation_gone_between_submit_and_claim_is_not_sent(self):
        self.inbound()
        tid = self.reply()
        with self.e.tx() as c:
            c.execute('DELETE FROM p_events WHERE tenant=?', (T,))
        self.assertFalse(self.e.tick(T))
        task = self.task(tid)
        self.assertEqual('failed', task['status'])
        self.assertEqual('conversation_unknown', task['steps'][0]['error'])
        self.assertEqual([], self.sent)

    def test_tool_removed_from_the_agent_between_submit_and_claim_is_not_sent(self):
        self.inbound()
        tid = self.reply()
        self.policies['bot']['tools'] = ['products.search']
        self.assertFalse(self.e.tick(T))
        self.assertEqual('failed', self.task(tid)['status'])
        self.assertEqual([], self.sent)

    def test_frozen_between_submit_and_claim_waits(self):
        self.inbound()
        tid = self.reply()
        self.e.freeze(T, True, 'boss')
        self.assertFalse(self.e.tick(T))
        self.assertEqual('queued', self.task(tid)['status'])
        self.assertEqual([], self.sent)
        self.e.freeze(T, False, 'boss')
        self.assertTrue(self.e.tick(T))
        self.assertEqual('succeeded', self.task(tid)['status'])

    def test_a_tampered_approval_row_does_not_dispatch(self):
        self.inbound()
        tid = self.reply()
        with self.e.tx() as c:
            c.execute("UPDATE p_approvals SET fingerprint='forged'")
        self.assertFalse(self.e.tick(T))
        self.assertEqual('approval_expired_or_invalid', self.task(tid)['steps'][0]['error'])
        self.assertEqual([], self.sent)

    # --- the conversation history -------------------------------------------------------

    def test_a_delivered_reply_joins_the_history_as_the_operator_once(self):
        self.inbound()
        tid = self.reply()
        self.assertFalse(settle_operator_replies(self.e, T), 'nothing delivered yet')
        self.assertEqual([], self.history())
        self.assertTrue(self.e.tick(T))
        self.assertTrue(settle_operator_replies(self.e, T))
        self.assertEqual([('operator', 'Salom, 189 000 so‘m.')], self.history())
        self.assertFalse(settle_operator_replies(self.e, T))
        self.assertEqual([('operator', 'Salom, 189 000 so‘m.')], self.history())
        marker = self.rows('SELECT id,body FROM p_records WHERE tenant=? AND kind=?', T, OPERATOR_LINE_KIND)
        self.assertEqual(tid, marker[0]['id'])
        self.assertEqual(OPERATOR, json.loads(marker[0]['body'])['actor'])
        self.assertEqual(1, len(self.rows("SELECT 1 FROM p_audit WHERE action='conversation.operator_delivered'")))

    def test_a_rejected_or_uncertain_reply_never_joins_the_history(self):
        from platform_runtime.engine import DeliveryRejected
        self.inbound()
        real = self.registry.items['telegram.send']
        self.registry.items['telegram.send'] = Tool(real.name, real.risk, real.schema,
                                                    lambda *a: (_ for _ in ()).throw(DeliveryRejected('http_403')),
                                                    external=True)
        tid = self.reply()
        self.assertTrue(self.e.tick(T))
        self.assertEqual('failed', self.task(tid)['status'])
        self.assertFalse(settle_operator_replies(self.e, T))
        self.assertEqual([], self.history())

    def test_the_settle_only_touches_operator_tasks_of_this_tenant(self):
        self.inbound()
        self.inbound(tenant='other')
        self.reply()
        self.reply(tenant='other')
        self.e.tick(T)
        self.e.tick('other')
        self.assertTrue(settle_operator_replies(self.e, T))
        self.assertEqual([], self.rows('SELECT 1 FROM p_conversation_history WHERE tenant=?', 'other'))
        self.assertEqual(1, len(self.rows('SELECT 1 FROM p_conversation_history WHERE tenant=?', T)))

    # --- the conversation index must be total over p_events ---------------------------

    def raw_event(self, key, payload, channel='telegram'):
        with self.e.tx() as c:
            c.execute('INSERT INTO p_events(tenant,channel,event_key,fingerprint,payload) VALUES(?,?,?,?,?)',
                      (T, channel, key, 'fp', payload))

    def test_a_malformed_event_payload_can_still_be_stored(self):
        """The index expression runs on every INSERT; a non-JSON row must not make it raise."""
        self.raw_event('bad1', 'not json')
        self.raw_event('bad2', '["a","list"]')
        self.assertEqual(2, len(self.rows('SELECT 1 FROM p_events WHERE tenant=?', T)))

    def test_the_lookup_tolerates_malformed_rows_beside_the_real_conversation(self):
        self.raw_event('bad1', 'not json')
        self.inbound()
        self.raw_event('bad2', '{"conversation_id":')
        tid = self.reply()
        self.assertTrue(self.e.tick(T))
        self.assertEqual('succeeded', self.task(tid)['status'])
        with self.assertRaises(NotFound):
            self.reply(chat='not json', key='k2')

    def test_an_engine_opens_a_database_that_already_holds_a_malformed_payload(self):
        """An older database has no index yet; building it over a bad row must not fail startup."""
        path = Path(self.tmp.name) / 'o.db'
        with self.e.tx() as c:
            c.execute('DROP INDEX p_events_conversation_id')
        self.raw_event('bad1', 'not json')
        self.inbound()
        with self.e.tx() as c:
            c.execute('PRAGMA user_version=0')  # the schema is re-applied on the next open
        reopened = Engine(path, self.registry, self.e.policy, clock=lambda: self.now,
                          authority=self.e.authority)
        with reopened.read() as c:
            self.assertTrue(c.execute("SELECT 1 FROM sqlite_master WHERE name='p_events_conversation_id'").fetchone())
        tid = reopened.operator_reply(T, 'telegram', CHAT, 'Salom', actor=OPERATOR, role='operator',
                                      key='k-reopen', agent='bot')
        self.assertTrue(reopened.tick(T))
        self.assertEqual('succeeded', reopened.get(T, tid)['status'])

    def test_the_conversation_lookup_uses_the_index(self):
        from platform_runtime.engine import EVENT_CONVERSATION_ID
        with self.e.read() as c:
            plan = ' '.join(row['detail'] for row in c.execute(
                'EXPLAIN QUERY PLAN SELECT 1 FROM p_events WHERE tenant=? AND channel=? AND '
                + EVENT_CONVERSATION_ID + '=? LIMIT 1', (T, 'telegram', CHAT)))
        self.assertIn('p_events_conversation_id', plan)


class WorkerWiringTests(unittest.TestCase):
    def test_the_worker_settles_operator_replies_after_the_engine_tick(self):
        """A reply the engine just delivered joins the history in the same pass."""
        source = (Path(__file__).resolve().parents[1] / 'app' / 'worker.py').read_text(encoding='utf-8')
        self.assertIn('settle_operator_replies(e,t)', source)
        self.assertLess(source.index("('engine',"), source.index("('operator_reply',"))


if __name__ == '__main__':
    unittest.main()
