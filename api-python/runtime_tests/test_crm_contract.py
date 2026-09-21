import unittest
from platform_runtime.crm.crm_contract import (
    CUSTOM_HTTP_METHODS,
    CUSTOM_HTTP_OPERATIONS,
    CUSTOM_HTTP_PLACEHOLDERS,
    IMPLEMENTED_CRM_DRIVERS,
    ONEC_STATUS_MAP,
    QUERY_SEARCH_DRIVERS,
    REVERSE_BITRIX24_STATUS_MAP,
    REVERSE_ONEC_STATUS_MAP,
    bounded_int,
    dig,
    parse_call_record,
    parse_followup_request,
    safe_relative_path,
    search_mode,
    validate_custom_http_config,
)
from platform_runtime.crm.crm_contract import (
    CRM_DRIVERS,
    CANONICAL_STATUSES,
    BITRIX24_STATUS_MAP,
    normalize_phone,
    normalize_email,
    clean_text,
    bounded_price,
    clean_currency,
    parse_lead_request,
    parse_deal_request,
    plan_crm_fingerprint,
    validate_crm_config,
)
from platform_runtime.engine import Forbidden


class CRMContractTests(unittest.TestCase):
    def test_phone_normalization_uzbek(self):
        self.assertEqual('+998901234567', normalize_phone('+998 90 123-45-67'))
        self.assertEqual('+998901234567', normalize_phone('998901234567'))
        self.assertEqual('+998901234567', normalize_phone('(998) 90 123 45 67'))

    def test_phone_normalization_international(self):
        self.assertEqual('+14155552671', normalize_phone('+1 (415) 555-2671'))
        self.assertEqual('+79991234567', normalize_phone('8 (999) 123-45-67'))

    def test_invalid_phone_rejected(self):
        with self.assertRaises(ValueError):
            normalize_phone('')
        with self.assertRaises(ValueError):
            normalize_phone('123')
        with self.assertRaises(ValueError):
            normalize_phone('not-a-phone')
        with self.assertRaises(ValueError):
            normalize_phone('+12345678901234567890')

    def test_email_normalization(self):
        self.assertEqual('client@example.com', normalize_email('Client@Example.COM '))
        self.assertEqual('alisher.navoi@domain.uz', normalize_email('Alisher.Navoi@domain.uz'))

    def test_invalid_email_rejected(self):
        with self.assertRaises(ValueError):
            normalize_email('')
        with self.assertRaises(ValueError):
            normalize_email('plainaddress')
        with self.assertRaises(ValueError):
            normalize_email('@missingusername.com')
        with self.assertRaises(ValueError):
            normalize_email('spaces in@email.com')

    def test_clean_text_bounds_and_control_chars(self):
        self.assertEqual('Valid Title', clean_text('  Valid Title  ', 'title', 50))
        with self.assertRaises(ValueError):
            clean_text('A' * 51, 'title', 50)
        with self.assertRaises(ValueError):
            clean_text('Title with \x00 NUL', 'title', 50)

    def test_bounded_price(self):
        self.assertEqual(50000, bounded_price(50000))
        self.assertEqual(0, bounded_price(None))
        self.assertEqual(0, bounded_price(0))
        with self.assertRaises(ValueError):
            bounded_price(-1)
        with self.assertRaises(ValueError):
            bounded_price(10**13)
        with self.assertRaises(ValueError):
            bounded_price(True)  # boolean rejected

    def test_clean_currency(self):
        self.assertEqual('UZS', clean_currency(None))
        self.assertEqual('USD', clean_currency('usd'))
        self.assertEqual('RUB', clean_currency('RUB'))
        with self.assertRaises(ValueError):
            clean_currency('DOLLAR')
        with self.assertRaises(ValueError):
            clean_currency('12')

    def test_parse_lead_request_success(self):
        raw = {
            'title': 'Yangi Mijoz: Telefon zakaz',
            'name': 'Bobur Mirzo',
            'phone': '+998 90 999 88 77',
            'email': 'bobur@example.uz',
            'price': 1500000,
            'currency': 'UZS',
            'source': 'telegram',
            'comments': 'Kiyim-kechak katalogi bo‘yicha so‘rov',
        }
        parsed = parse_lead_request(raw)
        self.assertEqual('Yangi Mijoz: Telefon zakaz', parsed['title'])
        self.assertEqual('Bobur Mirzo', parsed['name'])
        self.assertEqual('+998909998877', parsed['phone'])
        self.assertEqual('bobur@example.uz', parsed['email'])
        self.assertEqual(1500000, parsed['price'])
        self.assertEqual('UZS', parsed['currency'])

    def test_parse_lead_requires_phone_or_email(self):
        with self.assertRaises(ValueError):
            parse_lead_request({'title': 'No Contact Lead'})

    def test_parse_deal_request(self):
        raw = {
            'title': 'Katta Shartnoma',
            'price': 25000000,
            'currency': 'UZS',
            'stage': 'in_progress',
            'contact_id': 'c_123',
            'lead_id': 'l_456',
        }
        parsed = parse_deal_request(raw)
        self.assertEqual('Katta Shartnoma', parsed['title'])
        self.assertEqual(25000000, parsed['price'])
        self.assertEqual('c_123', parsed['contact_id'])

    def test_plan_crm_fingerprint_deterministic(self):
        req = {'title': 'Zakaz', 'phone': '+998901112233', 'price': 100000, 'currency': 'UZS'}
        cfg = {'driver': 'bitrix24', 'host': 'portal.bitrix24.uz'}
        fp1 = plan_crm_fingerprint('t1', 'agent1', 'crm_main', cfg, req)
        fp2 = plan_crm_fingerprint('t1', 'agent1', 'crm_main', cfg, req)
        self.assertEqual(fp1, fp2)
        self.assertEqual(64, len(fp1))

        # Changing argument changes fingerprint
        req_tampered = {**req, 'price': 200000}
        fp3 = plan_crm_fingerprint('t1', 'agent1', 'crm_main', cfg, req_tampered)
        self.assertNotEqual(fp1, fp3)

    def test_validate_crm_config(self):
        valid = {
            'driver': 'bitrix24',
            'host': 'mycompany.bitrix24.com',
            'allowed_hosts': ['mycompany.bitrix24.com'],
            'webhook_url_env': 'BITRIX24_WEBHOOK',
            'capabilities': ['discover', 'validate', 'read', 'plan_write', 'execute_write'],
        }
        validated = validate_crm_config(valid, tenant='t1', capability='read')
        self.assertEqual('bitrix24', validated['driver'])

        # Disabled config rejected
        with self.assertRaises(Forbidden):
            validate_crm_config({**valid, 'enabled': False})

        # Host not allowed rejected
        with self.assertRaises(Forbidden):
            validate_crm_config({**valid, 'host': 'evil.com'})

        # Unsupported driver rejected
        with self.assertRaises(ValueError):
            validate_crm_config({**valid, 'driver': 'unknown_crm'})

        # Agent not in allowed list rejected
        with self.assertRaises(Forbidden):
            validate_crm_config({**valid, 'agent_ids': ['agent_a']}, agent='agent_b')


class CRMBoundaryTests(unittest.TestCase):
    """The exact caps, pinned by VALUE.

    The rest of the suite asserts behaviour; a bound read back out of the module moves
    with the mutation that widens it, so these pin the literal and walk both ends.
    """

    # ------------------------------------------- the bounded conversions

    def test_bounded_int_refuses_everything_that_is_not_an_int(self):
        self.assertEqual(5, bounded_int(5, 'x', 0, 10))
        self.assertEqual(0, bounded_int(None, 'x', 0, 10))
        self.assertEqual(0, bounded_int('', 'x', 0, 10))
        # A bare int() accepted True as 1, truncated 1.9 to 1 and raised TypeError for
        # None and OverflowError for inf -- two types outside the ValueError contract.
        for value in (True, False, 1.9, 0.0, '5', 'abc', float('inf'),
                      float('nan'), [1], {'a': 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bounded_int(value, 'x', 0, 10)

    def test_bounded_int_walks_both_ends(self):
        self.assertEqual(0, bounded_int(0, 'x', 0, 86400))
        self.assertEqual(86400, bounded_int(86400, 'x', 0, 86400))
        for value in (-1, 86401, 10 ** 30):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bounded_int(value, 'x', 0, 86400)

    def test_the_price_is_an_integer_amount(self):
        self.assertEqual(10 ** 12, bounded_price(10 ** 12))
        with self.assertRaises(ValueError):
            bounded_price(10 ** 12 + 1)
        # A float WAS truncated -- 1.9 became 1 -- and inf raised OverflowError.
        for value in (1.9, 0.0, float('inf'), float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bounded_price(value)

    def test_the_call_duration_ceiling(self):
        self.assertEqual(86400, parse_call_record(
            {'lead_id': 'L', 'transcript': 't',
             'duration_seconds': 86400})['duration_seconds'])
        with self.assertRaises(ValueError):
            parse_call_record({'lead_id': 'L', 'transcript': 't',
                               'duration_seconds': 86401})
        # None raised TypeError before; it is now the documented default.
        self.assertEqual(0, parse_call_record(
            {'lead_id': 'L', 'transcript': 't',
             'duration_seconds': None})['duration_seconds'])
        for value in (1.9, True, '60', float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_call_record({'lead_id': 'L', 'transcript': 't',
                                   'duration_seconds': value})

    def test_the_schedule_is_a_bounded_integer(self):
        # A bare, unbounded float accepted 'nan', 'inf' and any negative value, and
        # nothing downstream rejected them.
        self.assertEqual(0, parse_followup_request({'lead_id': 'L'})['scheduled_at'])
        self.assertEqual(10 ** 12, parse_followup_request(
            {'lead_id': 'L', 'scheduled_at': 10 ** 12})['scheduled_at'])
        for value in (-1, 10 ** 12 + 1, 'nan', 'inf', '-inf', '1e400', 'abc',
                      1.9, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_followup_request({'lead_id': 'L', 'scheduled_at': value})
        self.assertIsInstance(parse_followup_request(
            {'lead_id': 'L', 'scheduled_at': 1_789_000_000})['scheduled_at'], int)

    # ------------------------------------------------------ phone and email

    def test_the_phone_digit_floor_is_seven(self):
        self.assertEqual('+1234567', normalize_phone('+1234567'))
        with self.assertRaises(ValueError):
            normalize_phone('+123456')

    def test_the_phone_digit_ceiling_is_fifteen(self):
        self.assertEqual('+' + '1' * 15, normalize_phone('+' + '1' * 15))
        with self.assertRaises(ValueError):
            normalize_phone('+' + '1' * 16)

    def test_the_998_and_leading_8_branches_are_length_exact(self):
        # 998 followed by nine digits is twelve characters: the branch fires.
        self.assertEqual('+998901234567', normalize_phone('998901234567'))
        # Thirteen characters does not, and the bare form has no plus, so it is refused
        # rather than silently gaining a country code.
        with self.assertRaises(ValueError):
            normalize_phone('9989012345678')
        # 8 followed by ten digits is eleven characters: +7 then the last ten.
        self.assertEqual('+79012345678', normalize_phone('89012345678'))
        # Twelve characters starting with 8 skips that branch and falls to the generic
        # 9..12 rule, which prepends a plus and leaves the 8 as the country code.
        self.assertEqual('+899012345678', normalize_phone('899012345678'))

    def test_the_email_ceiling_is_two_hundred_and_fifty_four(self):
        at_limit = 'a' * (254 - len('@example.com')) + '@example.com'
        self.assertEqual(254, len(at_limit))
        self.assertEqual(at_limit, normalize_email(at_limit))
        with self.assertRaises(ValueError):
            normalize_email('a' * (255 - len('@example.com')) + '@example.com')

    def test_the_currency_is_exactly_three_letters(self):
        self.assertEqual('UZS', clean_currency('uzs'))
        self.assertEqual('USD', clean_currency('  usd  '))
        self.assertEqual('UZS', clean_currency(''))
        self.assertEqual('UZS', clean_currency(None))
        for value in ('US', 'UZSS', '12A', 'UZ1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                clean_currency(value)

    # ------------------------------------------------------- the request path

    def test_the_path_ceiling_is_five_hundred_and_twelve(self):
        at_limit = '/' + 'a' * 511
        self.assertEqual(512, len(at_limit))
        self.assertEqual(at_limit, safe_relative_path(at_limit))
        with self.assertRaises(ValueError):
            safe_relative_path('/' + 'a' * 512)

    def test_the_path_rejects_every_retargeting_form(self):
        for value in ('//evil.com', '/a/../b', '/..', '/a\\b', '/a b', '/a\tb',
                      '/a:b', '/a#b', 'http://e.uz/x', 'ftp://e.uz', '/a"b', "/a'b",
                      'relative', '', '/a\x01b'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_relative_path(value)

    def test_the_placeholder_allowlist_is_exact(self):
        self.assertEqual(
            frozenset({'query', 'phone', 'email', 'lead_id', 'contact_id', 'limit',
                       'minutes', 'since'}),
            CUSTOM_HTTP_PLACEHOLDERS)
        self.assertNotIn('host', CUSTOM_HTTP_PLACEHOLDERS)
        for token in sorted(CUSTOM_HTTP_PLACEHOLDERS):
            with self.subTest(token=token):
                self.assertEqual('/x/{%s}' % token,
                                 safe_relative_path('/x/{%s}' % token))
        for value in ('/leads/{host}', '/leads/{url}', '/leads/{nope}',
                      '/leads/{query', '/leads/query}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_relative_path(value)

    def test_the_http_method_allowlist_is_exact(self):
        self.assertEqual(frozenset({'GET', 'POST', 'PUT', 'PATCH'}), CUSTOM_HTTP_METHODS)
        self.assertNotIn('DELETE', CUSTOM_HTTP_METHODS)
        for method in ('DELETE', 'HEAD', 'OPTIONS', 'TRACE', 'CONNECT'):
            with self.subTest(method=method), self.assertRaises(ValueError):
                validate_custom_http_config({
                    'base_path': '/api',
                    'operations': {'find_leads': {'method': method, 'path': '/leads'}}})

    def test_a_custom_http_config_is_normalised(self):
        config = validate_custom_http_config({
            'base_path': '/api/',
            'operations': {'find_leads': {'method': 'get', 'path': '/leads'}}})
        self.assertEqual('/api', config['base_path'])
        self.assertEqual('GET', config['operations']['find_leads']['method'])
        self.assertEqual(
            {'find_leads', 'find_contacts', 'create_lead', 'create_deal',
             'attach_call_record', 'attach_message', 'find_stalled_leads'},
            set(CUSTOM_HTTP_OPERATIONS))
        for block in ({'base_path': '/api', 'operations': {}},
                      {'base_path': '/api', 'operations': {'nope': {'path': '/x'}}}):
            with self.subTest(block=block), self.assertRaises(ValueError):
                validate_custom_http_config(block)

    # ------------------------------------------------------------- the pointer

    def test_the_pointer_segment_ceiling_is_sixty_four(self):
        self.assertEqual(1, dig({'a' * 64: 1}, 'a' * 64))
        with self.assertRaises(ValueError):
            dig({'a' * 65: 1}, 'a' * 65)

    def test_the_pointer_depth_ceiling_is_four(self):
        # Depth four lands on the scalar; depth five is refused before it is read.
        document = {'a': {'a': {'a': {'a': 1}}}}
        self.assertEqual(1, dig(document, 'a.a.a.a'))
        with self.assertRaises(ValueError):
            dig(document, 'a.a.a.a.a')
        with self.assertRaises(ValueError):
            dig(document, '')
        # No index syntax, no wildcards, no whitespace, no quotes. A hyphen IS allowed,
        # because regional ERP field names are hyphenated.
        for pointer in ('a[0]', 'a.*', 'a b', 'a"b', "a'b", 'a\tb'):
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                dig(document, pointer)
        self.assertEqual(1, dig({'a-b': 1}, 'a-b'))
        self.assertIsNone(dig({'a': 1}, 'b.c'))

    # ------------------------------------------- the driver and status tables

    def test_the_driver_sets_are_exact(self):
        self.assertEqual(
            {'bitrix24', 'amocrm', 'kommo', 'onec', 'custom_webhook'},
            set(IMPLEMENTED_CRM_DRIVERS))
        self.assertEqual(
            {'amocrm', 'kommo', 'onec', 'custom_webhook'}, set(QUERY_SEARCH_DRIVERS))
        # Declared but with no executable adapter: visible to operators, never reported
        # as a working integration.
        self.assertEqual(
            {'modme', 'billz', 'moysklad', 'retailcrm', 'yclients', 'jowi', 'poster'},
            set(CRM_DRIVERS) - set(IMPLEMENTED_CRM_DRIVERS))
        self.assertNotIn('bitrix24', QUERY_SEARCH_DRIVERS)
        self.assertEqual('structured', search_mode('bitrix24'))
        self.assertEqual('query', search_mode('kommo'))

    def test_every_status_map_lands_on_a_canonical_status(self):
        self.assertEqual({'new', 'in_progress', 'won', 'lost'}, set(CANONICAL_STATUSES))
        for name, mapping in (('bitrix24', BITRIX24_STATUS_MAP),
                              ('onec', ONEC_STATUS_MAP)):
            with self.subTest(name=name):
                self.assertEqual(set(), set(mapping.values()) - CANONICAL_STATUSES)

    def test_every_reverse_map_round_trips(self):
        # A reverse map that points at a remote status the forward map sends elsewhere
        # would write a deal into a state we did not intend.
        for name, forward, reverse in (
                ('bitrix24', BITRIX24_STATUS_MAP, REVERSE_BITRIX24_STATUS_MAP),
                ('onec', ONEC_STATUS_MAP, REVERSE_ONEC_STATUS_MAP)):
            for canonical, remote in reverse.items():
                with self.subTest(name=name, canonical=canonical):
                    self.assertIn(canonical, CANONICAL_STATUSES)
                    self.assertEqual(canonical, forward[remote])


if __name__ == '__main__':
    unittest.main()
