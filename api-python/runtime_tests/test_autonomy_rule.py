"""An autonomous agent may send to a destination the tenant already authorised.

Before this rule every `write` step waited for a human, whatever the ladder said,
so an "autonomous" FAQ bot could not answer a customer and a scheduled digest had
to bypass the engine to be delivered at all. The rule is deliberately narrow:

* only ``ladder: autonomous`` earns it;
* only ``write`` risk; ``destructive`` and ``physical`` always wait (PRD F5);
* only an outbound tool whose destination is either in the pack's
  ``allowed_recipients`` or the verified inbound conversation the task answers;
* an explicit ``approval.required_for`` entry still wins.

Every case below runs a real Engine on real SQLite and observes the step through
``tick``, so the claim-time re-validation is exercised as well as submission.
"""
import tempfile
import unittest
from pathlib import Path

from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import Registry, Tool, build_registry, obj, string

CUSTOMER = '555'
MANAGER = '77'


class PreauthorizedDestinationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sent = []
        self.registry = build_registry()
        real = self.registry.get('telegram.send')

        def send(engine, tenant, agent, args, key):
            self.sent.append(dict(args))
            return {'provider': 'telegram', 'external_id': '1'}
        self.registry.items[real.name] = Tool(real.name, real.risk, real.schema, send, external=True)
        self.registry.add(Tool('erase.all', 'destructive', obj({}), lambda *a: {}))
        self.policy = {'tools': ['telegram.send', 'records.create', 'erase.all'],
                       'ladder': 'autonomous', 'approval': [],
                       'allowed_recipients': [MANAGER], 'allowed_connections': []}
        self.e = Engine(Path(self.tmp.name) / 'a.db', self.registry, lambda t, a: self.policy)

    def send_step(self, to, text='Salom'):
        return [{'tool': 'telegram.send', 'args': {'conversation_id': to, 'text': text}}]

    def step(self, task):
        return self.e.get('t', task)['steps'][0]

    # --- pack-allowlisted destination ------------------------------------------

    def test_autonomous_send_to_an_allowlisted_recipient_needs_no_approval(self):
        task = self.e.submit('t', 'cron', 'k1', 'bot', self.send_step(MANAGER), 'owner')
        self.assertEqual(0, self.step(task)['approval_needed'])
        self.assertTrue(self.e.tick('t'))
        self.assertEqual('succeeded', self.e.get('t', task)['status'])
        self.assertEqual([{'conversation_id': MANAGER, 'text': 'Salom'}], self.sent)

    def test_autonomous_send_to_an_unlisted_recipient_is_still_refused(self):
        with self.assertRaises(Forbidden):
            self.e.submit('t', 'cron', 'k1', 'bot', self.send_step('stranger'), 'owner')
        self.assertEqual([], self.sent)

    # --- verified inbound conversation -----------------------------------------

    def test_autonomous_reply_to_the_verified_inbound_conversation_needs_no_approval(self):
        self.e.accept_event('t', 'telegram', 'm1', {'text': 'Narxi?', 'sender': CUSTOMER,
                                                    'conversation_id': CUSTOMER})
        task = self.e.submit('t', 'telegram', 'm1', 'bot', self.send_step(CUSTOMER), 'bot')
        self.assertEqual(0, self.step(task)['approval_needed'])
        self.assertTrue(self.e.tick('t'))
        self.assertEqual('succeeded', self.e.get('t', task)['status'])
        self.assertEqual(CUSTOMER, self.sent[0]['conversation_id'])

    def test_autonomous_reply_to_a_different_conversation_is_refused(self):
        self.e.accept_event('t', 'telegram', 'm1', {'text': 'x', 'sender': CUSTOMER,
                                                    'conversation_id': CUSTOMER})
        with self.assertRaises(Forbidden):
            self.e.submit('t', 'telegram', 'm1', 'bot', self.send_step('attacker'), 'bot')

    # --- everything else still waits --------------------------------------------

    def test_human_assisted_send_to_an_allowlisted_recipient_still_waits(self):
        self.policy['ladder'] = 'human_assisted'
        task = self.e.submit('t', 'cron', 'k1', 'bot', self.send_step(MANAGER), 'owner')
        self.assertEqual(1, self.step(task)['approval_needed'])
        self.assertFalse(self.e.tick('t'))
        self.assertEqual('waiting_approval', self.e.get('t', task)['status'])
        self.assertEqual([], self.sent)

    def test_human_led_waits_even_for_a_verified_reply(self):
        self.policy['ladder'] = 'human_led'
        self.e.accept_event('t', 'telegram', 'm1', {'text': 'x', 'sender': CUSTOMER,
                                                    'conversation_id': CUSTOMER})
        task = self.e.submit('t', 'telegram', 'm1', 'bot', self.send_step(CUSTOMER), 'bot')
        self.assertEqual(1, self.step(task)['approval_needed'])

    def test_autonomous_internal_write_still_waits(self):
        task = self.e.submit('t', 'cron', 'k1', 'bot',
                             [{'tool': 'records.create',
                               'args': {'kind': 'lead', 'title': 'A', 'body': 'B'}}], 'owner')
        self.assertEqual(1, self.step(task)['approval_needed'])

    def test_an_explicit_required_for_entry_overrides_the_exemption(self):
        self.policy['approval'] = ['telegram.send']
        task = self.e.submit('t', 'cron', 'k1', 'bot', self.send_step(MANAGER), 'owner')
        self.assertEqual(1, self.step(task)['approval_needed'])

    def test_destructive_risk_always_waits_however_autonomous_the_agent(self):
        task = self.e.submit('t', 'cron', 'k1', 'bot', [{'tool': 'erase.all', 'args': {}}], 'owner')
        self.assertEqual(1, self.step(task)['approval_needed'])
        self.assertFalse(self.e.tick('t'))

    # --- the decision is bound into the fingerprint ----------------------------

    def test_removing_the_recipient_from_the_allowlist_after_submit_blocks_dispatch(self):
        task = self.e.submit('t', 'cron', 'k1', 'bot', self.send_step(MANAGER), 'owner')
        self.policy['allowed_recipients'] = []
        self.assertFalse(self.e.tick('t'))
        step = self.step(task)
        self.assertEqual('failed', step['status'])
        self.assertEqual('policy_changed', step['error'])
        self.assertEqual([], self.sent)

    def test_demoting_the_ladder_after_submit_blocks_the_unattended_step(self):
        task = self.e.submit('t', 'cron', 'k1', 'bot', self.send_step(MANAGER), 'owner')
        self.policy['ladder'] = 'human_assisted'
        self.assertFalse(self.e.tick('t'))
        self.assertEqual('policy_changed', self.step(task)['error'])
        self.assertEqual([], self.sent)

    def test_a_reply_re_validates_against_the_same_inbound_event_at_claim_time(self):
        self.e.accept_event('t', 'telegram', 'm1', {'text': 'x', 'sender': CUSTOMER,
                                                    'conversation_id': CUSTOMER})
        task = self.e.submit('t', 'telegram', 'm1', 'bot', self.send_step(CUSTOMER), 'bot')
        # The origin event is re-read when the worker claims the step; the
        # fingerprint must therefore be stable across submit and claim.
        self.assertTrue(self.e.tick('t'))
        self.assertEqual('succeeded', self.e.get('t', task)['status'])


if __name__ == '__main__':
    unittest.main()
