"""telegram.send: the Bot API base URL is per-tenant operator config.

The default stays https://api.telegram.org. An override must be plain HTTPS, or
numeric-loopback HTTP under the SAME explicit opt-in the model endpoint uses
(`provider_mode: local_loopback`, platform_runtime/model_transport.py). The bot
token is part of the URL path, so every refusal must be raised before the token
is read and must never carry it.

Socket-free on purpose: runtime_tests run under the offline audit hook.
"""
import os
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from platform_runtime.tools import telegram, TELEGRAM_API

TOKEN = '123456:unit_test_only_token'
ARGS = {'conversation_id': '-100123', 'text': 'salom o‘g‘lim'}


def run(cfg, post=None):
    """Call the telegram.send handler with `cfg` as the tenant's telegram block."""
    post = post or MagicMock(return_value={'ok': True, 'result': {'message_id': 7}})
    with patch.dict(os.environ, {'UNIT_TG_TOKEN': TOKEN}), \
            patch('platform_runtime.tools.config', return_value={'telegram': cfg}), \
            patch('platform_runtime.tools.post_json', post):
        return telegram(None, 't', 'ops', dict(ARGS), 'key'), post


class TelegramBaseUrlTests(unittest.TestCase):
    def test_default_is_the_public_bot_api(self):
        self.assertEqual('https://api.telegram.org', TELEGRAM_API)
        out, post = run({'token_env': 'UNIT_TG_TOKEN'})
        self.assertEqual(f'https://api.telegram.org/bot{TOKEN}/sendMessage', post.call_args.args[0])
        self.assertEqual({'chat_id': '-100123', 'text': 'salom o‘g‘lim'}, post.call_args.args[1])
        self.assertEqual({'provider': 'telegram', 'external_id': '7'}, out)

    def test_https_override_is_honoured(self):
        _, post = run({'token_env': 'UNIT_TG_TOKEN', 'base_url': 'https://tg-gw.example.uz/api/'})
        self.assertEqual(f'https://tg-gw.example.uz/api/bot{TOKEN}/sendMessage', post.call_args.args[0])

    def test_http_refused_without_the_loopback_opt_in(self):
        for base in ('http://127.0.0.1:18443', 'http://tg.example.uz', 'http://localhost:18443'):
            with self.subTest(base=base):
                post = MagicMock()
                with self.assertRaises(RuntimeError) as caught:
                    run({'token_env': 'UNIT_TG_TOKEN', 'base_url': base}, post)
                post.assert_not_called()
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertNotIn('123456', str(caught.exception))

    def test_opt_in_is_exactly_the_model_rule(self):
        """localhost (DNS), privileged ports, remote hosts, userinfo and queries stay refused."""
        for base in ('http://localhost:18443', 'http://127.0.0.1', 'http://127.0.0.1:80',
                     'http://10.0.0.5:18443', 'http://u:p@127.0.0.1:18443', 'http://127.0.0.1:18443?x=1',
                     'https://remote.example.uz'):
            with self.subTest(base=base), self.assertRaises(RuntimeError) as caught:
                run({'token_env': 'UNIT_TG_TOKEN', 'provider_mode': 'local_loopback', 'base_url': base})
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_unknown_mode_refused(self):
        with self.assertRaises(RuntimeError):
            run({'token_env': 'UNIT_TG_TOKEN', 'provider_mode': 'auto'})

    def test_loopback_opt_in_uses_the_bounded_no_proxy_transport(self):
        response = MagicMock(); response.status = 200
        response.read.return_value = b'{"ok": true, "result": {"message_id": 42}}'
        opener = MagicMock(); opener.open.return_value.__enter__.return_value = response
        post = MagicMock()
        with patch('urllib.request.build_opener', return_value=opener) as build:
            out, _ = run({'token_env': 'UNIT_TG_TOKEN', 'provider_mode': 'local_loopback',
                          'base_url': 'http://127.0.0.1:18443'}, post)
        post.assert_not_called()
        self.assertEqual('42', out['external_id'])
        self.assertEqual({}, build.call_args.args[0].proxies)
        request = opener.open.call_args.args[0]
        self.assertEqual(f'http://127.0.0.1:18443/bot{TOKEN}/sendMessage', request.full_url)

    def test_loopback_failure_never_carries_the_token(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError(f'http://127.0.0.1:18443/bot{TOKEN}/sendMessage')
        with patch('urllib.request.build_opener', return_value=opener), \
                self.assertRaises(RuntimeError) as caught:
            run({'token_env': 'UNIT_TG_TOKEN', 'provider_mode': 'local_loopback',
                 'base_url': 'http://127.0.0.1:18443'})
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_rejection_still_not_success(self):
        with self.assertRaises(RuntimeError):
            run({'token_env': 'UNIT_TG_TOKEN', 'base_url': 'https://tg-gw.example.uz'},
                MagicMock(return_value={'ok': False}))


if __name__ == '__main__':
    unittest.main()
