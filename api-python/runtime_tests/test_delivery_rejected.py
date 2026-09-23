"""A definite provider rejection is `failed`, not `uncertain`.

`uncertain` exists for the case the platform cannot know: the request may have
reached the provider and the effect may have happened. A Telegram 403 (the user
blocked the bot), 400 (chat not found), 401 (bad token) or 429 (request refused
under rate limit), and a body that says `ok: false`, are not that case -- the
provider answered and said it did nothing. Reporting them as `uncertain` sends
the operator to reconcile a delivery that never happened, and marks the
conversation turn "never resend" for a message the customer definitely did not
get.

5xx, timeouts and connection failures keep the `uncertain` verdict: a 502 from a
gateway says nothing about what the upstream did, and a reset after the request
left the socket is exactly the unknowable case.

Socket-free: runtime_tests run under the offline audit hook.
"""
import io
import os
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from platform_runtime import model_transport
from platform_runtime.engine import DeliveryRejected, Engine, SAFE_REASON
from platform_runtime.tools import Tool, build_registry, instagram, obj, telegram

TOKEN = '123456:unit_test_only_token'
ARGS = {'conversation_id': '-100123', 'text': 'salom o‘g‘lim'}
URL = f'https://api.telegram.org/bot{TOKEN}/sendMessage'


def http_error(code, url=URL):
    return urllib.error.HTTPError(url, code, 'refused', {}, io.BytesIO(b'{"ok":false}'))


def run_telegram(cfg, post):
    with patch.dict(os.environ, {'UNIT_TG_TOKEN': TOKEN}), \
            patch('platform_runtime.tools.config', return_value={'telegram': cfg}), \
            patch('platform_runtime.tools.post_json', post):
        return telegram(None, 't', 'ops', dict(ARGS), 'key')


CLOUD = {'token_env': 'UNIT_TG_TOKEN'}
LOOPBACK = {'token_env': 'UNIT_TG_TOKEN', 'provider_mode': 'local_loopback',
            'base_url': 'http://127.0.0.1:18443'}


class DeliveryRejectedClassTests(unittest.TestCase):
    def test_reason_is_a_short_safe_token(self):
        self.assertEqual('http_403', DeliveryRejected('http_403').reason)
        self.assertEqual('http_403', str(DeliveryRejected('http_403')))
        self.assertTrue(SAFE_REASON.fullmatch('provider_ok_false'))

    def test_an_unsafe_reason_is_dropped_not_stored(self):
        """A future adapter that passes a provider message through must not leak it."""
        for unsafe in (URL, 'HTTP Error 403: ' + TOKEN, '', None, 'x' * 41, 'Has Spaces'):
            with self.subTest(reason=unsafe):
                error = DeliveryRejected(unsafe)
                self.assertEqual('rejected', error.reason)
                self.assertNotIn(TOKEN, str(error))

    def test_it_is_a_runtime_error_so_existing_handlers_still_catch_it(self):
        self.assertTrue(issubclass(DeliveryRejected, RuntimeError))


class TelegramCloudTests(unittest.TestCase):
    def test_4xx_is_a_definite_rejection(self):
        for code in (400, 401, 403, 404, 429):
            with self.subTest(code=code):
                with self.assertRaises(DeliveryRejected) as caught:
                    run_telegram(CLOUD, MagicMock(side_effect=http_error(code)))
                self.assertEqual(f'http_{code}', caught.exception.reason)
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertTrue(caught.exception.__suppress_context__)

    def test_5xx_stays_uncertain(self):
        for code in (500, 502, 503, 504):
            with self.subTest(code=code):
                with self.assertRaises(RuntimeError) as caught:
                    run_telegram(CLOUD, MagicMock(side_effect=http_error(code)))
                self.assertNotIsInstance(caught.exception, DeliveryRejected)
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)

    def test_transport_failures_stay_uncertain(self):
        """Refused, reset, timed out: none of these proves the request never left."""
        for failure in (urllib.error.URLError(ConnectionRefusedError(111, 'refused ' + URL)),
                        urllib.error.URLError(socket.timeout('timed out')),
                        ConnectionResetError(104, 'reset'), TimeoutError('t'),
                        OSError('network ' + URL)):
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(Exception) as caught:
                    run_telegram(CLOUD, MagicMock(side_effect=failure))
                self.assertNotIsInstance(caught.exception, DeliveryRejected)
                self.assertNotIn(TOKEN, str(caught.exception))

    def test_ok_false_body_is_a_definite_rejection(self):
        with self.assertRaises(DeliveryRejected) as caught:
            run_telegram(CLOUD, MagicMock(return_value={'ok': False, 'error_code': 403,
                                                          'description': 'Forbidden: ' + TOKEN}))
        self.assertEqual('provider_ok_false', caught.exception.reason)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_a_body_that_says_nothing_stays_uncertain(self):
        """A 200 whose body is not the Bot API's shape could be a gateway that forwarded."""
        for body in ({}, {'result': {'message_id': 1}}, {'ok': 'yes'}, []):
            with self.subTest(body=body):
                with self.assertRaises(Exception) as caught:
                    run_telegram(CLOUD, MagicMock(return_value=body))
                self.assertNotIsInstance(caught.exception, DeliveryRejected)

    def test_success_is_unchanged(self):
        out = run_telegram(CLOUD, MagicMock(return_value={'ok': True, 'result': {'message_id': 7}}))
        self.assertEqual({'provider': 'telegram', 'external_id': '7'}, out)


class TelegramLoopbackTests(unittest.TestCase):
    def loopback(self, side_effect=None, body=b'{"ok": true, "result": {"message_id": 42}}'):
        response = MagicMock(); response.status = 200; response.read.return_value = body
        opener = MagicMock(); opener.open.return_value.__enter__.return_value = response
        if side_effect is not None:
            opener.open.side_effect = side_effect
        with patch('urllib.request.build_opener', return_value=opener):
            return run_telegram(LOOPBACK, MagicMock())

    def test_4xx_through_the_loopback_transport_is_a_definite_rejection(self):
        local = f'http://127.0.0.1:18443/bot{TOKEN}/sendMessage'
        for code in (400, 403, 429):
            with self.subTest(code=code):
                with self.assertRaises(DeliveryRejected) as caught:
                    self.loopback(http_error(code, local))
                self.assertEqual(f'http_{code}', caught.exception.reason)
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)

    def test_5xx_and_transport_failures_through_loopback_stay_uncertain(self):
        local = f'http://127.0.0.1:18443/bot{TOKEN}/sendMessage'
        for failure in (http_error(500, local), http_error(503, local), urllib.error.URLError(local),
                        ConnectionResetError(104, 'reset')):
            with self.subTest(failure=failure):
                with self.assertRaises(RuntimeError) as caught:
                    self.loopback(failure)
                self.assertNotIsInstance(caught.exception, DeliveryRejected)
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)

    def test_ok_false_through_loopback_is_a_definite_rejection(self):
        with self.assertRaises(DeliveryRejected) as caught:
            self.loopback(body=b'{"ok": false, "error_code": 400}')
        self.assertEqual('provider_ok_false', caught.exception.reason)

    def test_local_rejection_class_carries_only_the_status(self):
        error = model_transport.LocalRequestRejected(403)
        self.assertEqual(403, error.code)
        self.assertTrue(isinstance(error, RuntimeError))
        self.assertNotIn('127.0.0.1', str(error))


class InstagramTests(unittest.TestCase):
    CFG = {'instagram': {'graph_version': 'v21.0', 'account_id': '1784', 'token_env': 'UNIT_IG_TOKEN'}}

    def run_instagram(self, post):
        with patch.dict(os.environ, {'UNIT_IG_TOKEN': 'IGQ_unit_secret'}), \
                patch('platform_runtime.tools.config', return_value=self.CFG), \
                patch('platform_runtime.tools.post_json', post):
            return instagram(None, 't', 'ops', {'conversation_id': '9', 'text': 'salom'}, 'key')

    def test_4xx_is_a_definite_rejection(self):
        for code in (400, 401, 403, 429):
            with self.subTest(code=code):
                with self.assertRaises(DeliveryRejected) as caught:
                    self.run_instagram(MagicMock(side_effect=http_error(code, 'https://graph.instagram.com/x')))
                self.assertEqual(f'http_{code}', caught.exception.reason)
                self.assertNotIn('IGQ_unit_secret', str(caught.exception))

    def test_5xx_stays_uncertain(self):
        with self.assertRaises(RuntimeError) as caught:
            self.run_instagram(MagicMock(side_effect=http_error(502, 'https://graph.instagram.com/x')))
        self.assertNotIsInstance(caught.exception, DeliveryRejected)
        self.assertNotIn('IGQ_unit_secret', str(caught.exception))


class EngineTickTests(unittest.TestCase):
    """The engine turns the exception into the step verdict; the adapter only raises."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = build_registry()
        self.outcome = None

        def send(engine, tenant, agent, args, key):
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return {'provider': 'telegram', 'external_id': '1'}
        real = self.registry.get('telegram.send')
        self.registry.items[real.name] = Tool(real.name, real.risk, real.schema, send, external=True)
        self.registry.add(Tool('local.write', 'write', obj({}), send, external=False))
        self.policy = {'tools': ['telegram.send', 'local.write'], 'ladder': 'autonomous', 'approval': [],
                       'allowed_recipients': ['77'], 'allowed_connections': []}
        self.e = Engine(Path(self.tmp.name) / 'a.db', self.registry, lambda t, a: self.policy)

    def send(self, tool='telegram.send', args=None):
        args = {'conversation_id': '77', 'text': 'Salom'} if args is None else args
        task = self.e.submit('t', 'cron', 'k1', 'bot', [{'tool': tool, 'args': args}], 'owner')
        step = self.e.get('t', task)['steps'][0]
        if step['approval_needed']:
            self.e.approve('t', step['id'], 'olga', 'approved', 'operator')
        self.assertTrue(self.e.tick('t'))
        return self.e.get('t', task)

    def test_definite_rejection_is_failed_with_a_safe_reason(self):
        self.outcome = DeliveryRejected('http_403')
        task = self.send()
        self.assertEqual('failed', task['status'])
        self.assertEqual('failed', task['steps'][0]['status'])
        self.assertEqual('DeliveryRejected: http_403', task['steps'][0]['error'])
        with self.e.read() as c:
            audit = c.execute("SELECT data FROM p_audit WHERE action='step.failed'").fetchone()[0]
        self.assertIn('http_403', audit)
        self.assertNotIn(TOKEN, audit)

    def test_unknown_provider_failure_is_still_uncertain(self):
        self.outcome = RuntimeError('HTTP Error 502 ' + URL)
        task = self.send()
        self.assertEqual('uncertain', task['status'])
        self.assertEqual('RuntimeError', task['steps'][0]['error'])

    def test_rejection_reason_cannot_carry_a_secret(self):
        self.outcome = DeliveryRejected('Forbidden: ' + TOKEN)
        task = self.send()
        self.assertEqual('failed', task['status'])
        self.assertEqual('DeliveryRejected: rejected', task['steps'][0]['error'])

    def test_a_failed_step_is_terminal_and_never_re_claimed(self):
        self.outcome = DeliveryRejected('http_400')
        self.send()
        self.assertFalse(self.e.tick('t'))

    def test_rejection_on_a_local_write_is_also_failed(self):
        """The verdict does not depend on `external`: a definite no is a definite no."""
        self.outcome = DeliveryRejected('http_409')
        task = self.send('local.write', {})
        self.assertEqual('failed', task['status'])
        self.assertEqual('DeliveryRejected: http_409', task['steps'][0]['error'])


if __name__ == '__main__':
    unittest.main()
