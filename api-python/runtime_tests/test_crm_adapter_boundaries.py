"""Boundary tests for the four CRM adapters and their shared helpers (fazza 40).

The per-adapter suites cover each driver's own behaviour. These pin the bounds that the
FOUR of them state separately, because a bound written four times drifts four ways:
the response ceiling, the limit clamp, the timeout, the provider-field conversions and
the error each transport reports when a response is too large.
"""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime.crm.bitrix24_adapter import Bitrix24Adapter
from platform_runtime.crm.custom_http_adapter import CustomHTTPAdapter
from platform_runtime.crm.kommo_adapter import KommoAdapter
from platform_runtime.crm.onec_adapter import OneCAdapter
from platform_runtime.crm.crm_contract import (
    optional_email,
    optional_phone,
    provider_price,
)

HOST = 'crm.example.uz'
CRM_DIR = Path(__file__).resolve().parents[1] / 'platform_runtime' / 'crm'

os.environ.setdefault('CRM_TEST_B24', 'token')
os.environ.setdefault('CRM_TEST_KOMMO', 'token')
os.environ.setdefault('CRM_TEST_ONEC', 'svc:secret')
os.environ.setdefault('CRM_TEST_CUSTOM', 'token')

BITRIX = {'driver': 'bitrix24', 'host': HOST, 'allowed_hosts': [HOST],
          'token_env': 'CRM_TEST_B24'}
KOMMO = {'driver': 'kommo', 'host': HOST, 'allowed_hosts': [HOST],
         'token_env': 'CRM_TEST_KOMMO'}
ONEC = {'driver': 'onec', 'host': HOST, 'allowed_hosts': [HOST],
        'basic_auth_env': 'CRM_TEST_ONEC',
        'response_map': {'items': 'rows', 'phone': 'phone', 'email': 'email',
                         'id': 'id'}}
CUSTOM = {'driver': 'custom_webhook', 'host': HOST, 'allowed_hosts': [HOST],
          'credential_env': 'CRM_TEST_CUSTOM',
          'operations': {'find_leads': {'method': 'GET', 'path': '/leads/{query}'},
                         'find_contacts': {'method': 'GET', 'path': '/contacts/{query}'}},
          'response_map': {'items': 'rows', 'phone': 'phone', 'email': 'email',
                           'id': 'id'}}

EMPTY = {'result': [], '_embedded': {'leads': []}, 'rows': []}

# (name, config, class, a call that reaches the transport, extra config)
ADAPTERS = [
    ('bitrix24', BITRIX, Bitrix24Adapter,
     lambda a: a.find_leads(phone='+998901234567'), {}),
    ('kommo', KOMMO, KommoAdapter, lambda a: a.find_leads(query='x'), {}),
    ('onec', ONEC, OneCAdapter, lambda a: a.find_leads(query='x'), {}),
    ('custom_http', CUSTOM, CustomHTTPAdapter, lambda a: a.find_leads(query='x'), {}),
]


class Response:
    def __init__(self, body):
        self.body = body
        self.status = 200

    def read(self, n=None):
        return self.body if n is None else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    def __init__(self, body):
        self.body = body

    def open(self, request, timeout=None):
        return Response(self.body)


def body_of(size):
    return b'{"a":"' + b'x' * (size - 8) + b'"}'


class ProviderFieldTests(unittest.TestCase):
    """A provider ROW is untrusted: an unusable field must not abort the read."""

    def test_the_provider_normalisers_tolerate_what_they_cannot_parse(self):
        self.assertEqual('+998901234567', optional_phone('+998 90 123-45-67'))
        self.assertEqual('a@b.co', optional_email('A@B.CO'))
        # `normalize_phone` / `normalize_email` raise on these; the provider path must
        # not, because `_rows` and `_map` in the same adapters exist to tolerate drift.
        for value in ('n/a', 'unknown', '+1', 'abc', 0, None):
            with self.subTest(value=value):
                self.assertEqual('', optional_phone(value))
        for value in ('not-an-email', 'a@b', 'x y@z.co', 0, None):
            with self.subTest(value=value):
                self.assertEqual('', optional_email(value))

    def test_provider_price_returns_zero_for_anything_unusable(self):
        for value in (None, '', 'abc', 'nan', 'inf', '-inf', 1e400, float('inf'),
                      float('nan'), True, False, [1], {'a': 1}, -1, -10 ** 6,
                      10 ** 16, '10000000000000000'):
            with self.subTest(value=value):
                self.assertEqual(0, provider_price(value))

    def test_provider_price_bounds(self):
        self.assertEqual(0, provider_price(0))
        self.assertEqual(10 ** 15, provider_price(10 ** 15))
        self.assertEqual(450000, provider_price(450000))
        self.assertEqual(450000, provider_price('450000'))
        self.assertEqual(450000, provider_price(450000.0))
        # The STRING shape is one digit stricter than the integer bound: fifteen whole
        # digits excludes 10**15 itself, which the integer branch accepts. The same
        # asymmetry the documents layer records for an amount string.
        self.assertEqual(10 ** 15 - 1, provider_price('9' * 15))
        self.assertEqual(0, provider_price(str(10 ** 15)))
        self.assertEqual(0, provider_price('9' * 16))

    def test_provider_price_truncates_a_fraction_rather_than_refusing(self):
        # The documents layer already truncates rather than rounds a submitted amount;
        # refusing here would discard the whole lead instead of one minor unit.
        self.assertEqual(450000, provider_price('450000.99'))
        self.assertEqual(450000, provider_price(450000.99))
        self.assertEqual(12, provider_price('12.50'))

    def test_provider_price_does_not_route_money_through_a_float(self):
        source = (CRM_DIR / 'bitrix24_adapter.py').read_text(encoding='utf-8')
        # NOT `'float(' not in source`: the docstring names the removed form.
        self.assertNotIn('int(float(item.get(', source)
        self.assertIn("provider_price(item.get('OPPORTUNITY'))", source)
        source = (CRM_DIR / 'kommo_adapter.py').read_text(encoding='utf-8')
        self.assertNotIn("int(item.get('price') or 0)", source)
        self.assertIn("provider_price(item.get('price'))", source)

    def test_a_malformed_provider_row_does_not_abort_the_search(self):
        rows = [{'id': '1', 'name': 'Ali', 'phone': 'n/a', 'email': 'not-an-email'},
                {'id': '2', 'name': 'Vali', 'phone': '+998901234567',
                 'email': 'v@e.uz'}]
        for name, config, cls, _call, _extra in ADAPTERS:
            if cls not in (OneCAdapter, CustomHTTPAdapter):
                continue
            with self.subTest(name=name):
                adapter = cls(config, transport=lambda *a, **k: {'rows': rows})
                leads = adapter.find_leads(query='ali')
                self.assertEqual(2, len(leads))
                self.assertEqual('', leads[0]['phone'])
                self.assertEqual('+998901234567', leads[1]['phone'])
                self.assertEqual('v@e.uz', leads[1]['email'])

    def test_a_malformed_provider_price_does_not_abort_the_search(self):
        item = {'ID': '1', 'TITLE': 't', 'OPPORTUNITY': 'inf'}
        adapter = Bitrix24Adapter(BITRIX, transport=lambda *a, **k: {'result': [item]})
        self.assertEqual(0, adapter.find_leads(phone='+998901234567')[0]['price'])
        lead = {'id': '1', 'name': 't', 'price': 'abc'}
        adapter = KommoAdapter(KOMMO,
                               transport=lambda *a, **k: {'_embedded': {'leads': [lead]}})
        self.assertEqual(0, adapter.find_leads(query='x')[0]['price'])


class SharedBoundTests(unittest.TestCase):
    """The bounds stated separately in all four adapters."""

    def test_the_response_byte_ceiling_is_eighty_thousand(self):
        for name, path in (('bitrix24', 'bitrix24_adapter'), ('kommo', 'kommo_adapter'),
                           ('onec', 'onec_adapter'),
                           ('custom_http', 'custom_http_adapter')):
            with self.subTest(name=name):
                source = (CRM_DIR / (path + '.py')).read_text(encoding='utf-8')
                self.assertIn('MAX_RESPONSE_BYTES = 80_000', source)

    def test_the_byte_ceiling_is_enforced_and_keeps_its_own_message(self):
        for name, module_name, fn_name, error_name in (
                ('bitrix24', 'platform_runtime.crm.bitrix24_adapter',
                 'default_transport', 'Bitrix24HTTPError'),
                ('kommo', 'platform_runtime.crm.kommo_adapter',
                 'default_kommo_transport', 'KommoHTTPError'),
                ('onec', 'platform_runtime.crm.onec_adapter',
                 'default_onec_transport', 'OneCHTTPError'),
                ('custom_http', 'platform_runtime.crm.custom_http_adapter',
                 'default_custom_transport', 'CustomHTTPError')):
            module = __import__(module_name, fromlist=['x'])
            transport = getattr(module, fn_name)
            with self.subTest(name=name, size=80_000):
                with patch('urllib.request.build_opener',
                           side_effect=lambda *a, **k: Opener(body_of(80_000))):
                    self.assertEqual({'a': 'x' * (80_000 - 8)},
                                     transport('https://%s/x' % HOST))
            with self.subTest(name=name, size=80_001):
                with patch('urllib.request.build_opener',
                           side_effect=lambda *a, **k: Opener(body_of(80_001))):
                    with self.assertRaises(Exception) as caught:
                        transport('https://%s/x' % HOST)
                # The ceiling refusal is raised INSIDE the try, so a broad handler
                # without a re-raise reports a SIZE refusal as a transport fault.
                self.assertEqual(error_name, type(caught.exception).__name__)
                self.assertIn('exceeded maximum allowed bytes',
                              str(caught.exception))
                self.assertNotIn('transport error', str(caught.exception))
                self.assertNotIn('transport failure', str(caught.exception))

    def test_the_limit_clamp_is_fifty(self):
        for name, path in (('bitrix24', 'bitrix24_adapter'), ('kommo', 'kommo_adapter'),
                           ('onec', 'onec_adapter'),
                           ('custom_http', 'custom_http_adapter')):
            with self.subTest(name=name):
                source = (CRM_DIR / (path + '.py')).read_text(encoding='utf-8')
                self.assertIn('50)', source)
                self.assertNotIn('), 51)', source)
                self.assertIn('min(max(1,', source)

    def test_the_limit_clamp_holds_at_the_boundary(self):
        # Twenty rows in, limit 50: all twenty come back, so the clamp is not cutting
        # early; limit 10 cuts to ten.
        rows = [{'id': str(i), 'name': 'n'} for i in range(20)]
        adapter = KommoAdapter(
            KOMMO, transport=lambda *a, **k: {'_embedded': {'leads': rows}})
        self.assertEqual(20, len(adapter.find_leads(query='x', limit=50)))
        self.assertEqual(10, len(adapter.find_leads(query='x', limit=10)))
        self.assertEqual(1, len(adapter.find_leads(query='x', limit=0)))
        self.assertEqual(20, len(adapter.find_leads(query='x', limit=10 ** 9)))

    def test_the_timeout_is_read_validated_and_passed(self):
        for name, config, cls, call, extra in ADAPTERS:
            seen = []

            def transport(*a, _seen=seen, **k):
                _seen.append(k)
                return EMPTY

            with self.subTest(name=name, configured=45):
                adapter = cls({**config, 'timeout_seconds': 45, **extra},
                              transport=transport)
                call(adapter)
                self.assertEqual(45, seen[-1].get('timeout'))
            with self.subTest(name=name, default=15):
                adapter = cls(dict(config), transport=transport)
                call(adapter)
                self.assertEqual(15, seen[-1].get('timeout'))
            for value in (0, 61, 'x', 10 ** 9, True):
                with self.subTest(name=name, refused=value), \
                        self.assertRaises(ValueError):
                    cls({**config, 'timeout_seconds': value, **extra},
                        transport=transport)
            for value in (1, 60):
                with self.subTest(name=name, accepted=value):
                    cls({**config, 'timeout_seconds': value, **extra},
                        transport=transport)

    def test_the_header_name_ceiling_is_sixty_four(self):
        long_name = 'X' * 64
        config = {**CUSTOM, 'headers': {long_name: 'v'}}
        self.assertEqual({long_name: 'v'},
                         CustomHTTPAdapter(config, transport=lambda *a, **k: {}).headers)
        with self.assertRaises(ValueError):
            CustomHTTPAdapter({**CUSTOM, 'headers': {'X' * 65: 'v'}},
                              transport=lambda *a, **k: {})


if __name__ == '__main__':
    unittest.main()
