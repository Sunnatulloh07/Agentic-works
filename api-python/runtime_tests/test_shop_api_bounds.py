"""Declared bounds and pure helpers of the shop read surfaces (app/shop_api.py).

The dashboard's shop routes carried named constants that nothing pinned (a row
ceiling, a message ceiling, the channel list, two role tuples, the credential-name
shape) plus three pure helpers that decide what an operator is shown. The HTTP
behaviour is ``integration_tests/test_shop_api.py``'s; this file owns the numbers
and the helpers, which need no server, no database and no socket.

The credential helper is the one worth staring at: it returns environment variable
**names** from an integration block and never values, and a scoped block (a token
map, where every string IS a name) is the documented exception. Both rules are
exercised below.
"""
import os
import unittest
from pathlib import Path

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

from app import shop_api


def source():
    return Path(shop_api.__file__).read_text(encoding='utf-8')


class DeclaredBoundTests(unittest.TestCase):
    def test_the_ceilings(self):
        self.assertEqual(shop_api.MAX_SHOP_ROWS, 100)
        self.assertEqual(shop_api.MAX_MESSAGE_CHARS, 1000)
        self.assertEqual(shop_api.MAX_PRODUCTS, 1000)

    def test_the_channel_list_and_their_config_blocks(self):
        self.assertEqual(shop_api.CHANNELS, ('telegram', 'instagram', 'whatsapp'))
        self.assertEqual(shop_api.CHANNEL_BLOCKS['telegram'], ('telegram',))
        self.assertEqual(shop_api.CHANNEL_BLOCKS['instagram'], ('instagram',))
        self.assertEqual(shop_api.CHANNEL_BLOCKS['whatsapp'],
                         ('whatsapp', 'whatsapp_webhook', 'whatsapp_tokens'))

    def test_the_role_surfaces(self):
        self.assertEqual(shop_api.OPERATORS, ('owner', 'operator'))
        self.assertEqual(shop_api.INTEGRATORS, ('owner', 'integrator'))

    def test_the_credential_name_shape(self):
        self.assertIsNotNone(shop_api.ENV_NAME.fullmatch('WHATSAPP_TOKEN'))
        self.assertIsNotNone(shop_api.ENV_NAME.fullmatch('A1'))
        self.assertIsNone(shop_api.ENV_NAME.fullmatch('lower_case'))
        self.assertIsNone(shop_api.ENV_NAME.fullmatch('1LEADING_DIGIT'))
        self.assertIsNone(shop_api.ENV_NAME.fullmatch('WITH-DASH'))

    def test_the_ceilings_are_named_not_inline(self):
        """The queries bind the constant as a parameter; the helpers slice by it."""
        text = source()
        for literal in ('MAX_SHOP_ROWS = 100', 'MAX_MESSAGE_CHARS = 1000', 'MAX_PRODUCTS = 1000'):
            self.assertIn(literal, text)
        self.assertGreaterEqual(text.count('MAX_SHOP_ROWS'), 3)
        self.assertIn('[:MAX_MESSAGE_CHARS]', text)
        self.assertIn('pack.products[:MAX_PRODUCTS]', text)
        self.assertNotIn('[:1000]', text)


class TextHelperTests(unittest.TestCase):
    def test_the_truncation_boundary_is_flagged(self):
        text, truncated = shop_api._text('x' * shop_api.MAX_MESSAGE_CHARS)
        self.assertEqual(len(text), shop_api.MAX_MESSAGE_CHARS)
        self.assertFalse(truncated)
        text, truncated = shop_api._text('x' * (shop_api.MAX_MESSAGE_CHARS + 1))
        self.assertEqual(len(text), shop_api.MAX_MESSAGE_CHARS)
        self.assertTrue(truncated)

    def test_a_non_string_is_an_empty_line_not_a_crash(self):
        for value in (None, 5, [], {}, True):
            with self.subTest(value=value):
                self.assertEqual(shop_api._text(value), ('', False))


class JsonHelperTests(unittest.TestCase):
    def test_the_default_type_is_enforced(self):
        self.assertEqual(shop_api._json('{"a": 1}', {}), {'a': 1})
        self.assertEqual(shop_api._json('[1, 2]', []), [1, 2])
        self.assertEqual(shop_api._json('"text"', ''), 'text')

    def test_a_type_mismatch_falls_back_rather_than_leaking_through(self):
        self.assertEqual(shop_api._json('[]', {}), {})
        self.assertEqual(shop_api._json('{"a": 1}', []), [])

    def test_unreadable_input_is_the_default(self):
        for raw in ('{oops', None, '', 5, b'{}'):
            with self.subTest(raw=raw):
                self.assertEqual(shop_api._json(raw, {}), {})


class CredentialRefTests(unittest.TestCase):
    def test_only_env_suffixed_keys_are_read_by_default(self):
        block = {'token_env': 'TELEGRAM_TOKEN', 'plain': 'OTHER_NAME',
                 'nested': {'app_secret_env': 'META_APP_SECRET'}}
        self.assertEqual(shop_api._credential_refs(block),
                         ['TELEGRAM_TOKEN', 'META_APP_SECRET'])

    def test_a_scoped_block_treats_every_string_as_a_reference(self):
        block = {'messaging': 'WHATSAPP_TOKEN', 'nested': {'management': 'WA_MGMT'}}
        self.assertEqual(shop_api._credential_refs(block, scoped=True),
                         ['WHATSAPP_TOKEN', 'WA_MGMT'])

    def test_a_value_that_is_not_a_name_shape_is_never_returned(self):
        self.assertEqual(shop_api._credential_refs({'token_env': 'sk-live-123'}), [])
        self.assertEqual(shop_api._credential_refs({'token': 'sk-live-123'}), [])


if __name__ == '__main__':
    unittest.main()
