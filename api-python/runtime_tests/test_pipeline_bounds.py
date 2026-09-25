"""Declared bounds of the shared inbound pipeline (app/pipeline.py).

``pipeline.py`` is the one door every channel walks through and it had **no runtime
test file at all** before this one: its behaviour was exercised only from the
dependency-backed suite, which the offline revert matrix cannot measure. Its bounds
were also written inline -- ``4000`` twice, ``1..99`` (a third copy of a range that
already lived in the Order model and again in the approval-time re-check), ``200``,
``500``, ``8``, ``10 ** 9``, and a phone pattern carrying a bare ``998`` -- so
nothing could address them by name and nothing would have failed if one widened.

What is pinned here, and how honestly:

* **named constants** -- asserted literally *and* behaviourally;
* **the one-fact claims** -- the quantity range is imported from ``app.orders`` and
  the inbound text ceiling equals the control plane's, so the equality itself is a
  test rather than a comment;
* **the platform bridge** (``ui`` -> ``web``, truncation, conversation default) --
  exercised through the real function with the engine seams patched, because that
  path needs no database to be decided;
* **the legacy production refusal** -- the guard runs before any legacy work, so it
  is exercised with ``is_prod`` patched;
* **``remember``** -- a real SQLite file in a temporary APP_DB, because restart-safe
  idempotency is the behaviour, not the SQL string.

Not pinned here, and named so the gap is visible: the legacy happy path
(``_legacy_handle_text_message``) needs a pack directory, an approvals store and a
quota table; it is covered by the dependency-backed suite, not by this file.
"""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

from app import orders, pipeline, platform_api
from app.orders import MAX_QUANTITY, MIN_QUANTITY
from app.packs import PackError
from app.storage import reset

PACK = SimpleNamespace(products=[SimpleNamespace(id='P1'), SimpleNamespace(id='P2')],
                       branches=[SimpleNamespace(id='chilonzor'),
                                 SimpleNamespace(id='yakkasaroy')])
GOOD_PHONE = '+998901234567'


class DeclaredBoundTests(unittest.TestCase):
    """Every promoted number, pinned to its literal value."""

    def test_text_ceilings(self):
        self.assertEqual(pipeline.MAX_TEXT_CHARS, 4000)
        self.assertEqual(pipeline.MAX_CUSTOMER_CHARS, 200)
        self.assertEqual(pipeline.MAX_LEAD_CHARS, 500)

    def test_stable_id_shape(self):
        self.assertEqual(pipeline.STABLE_ID_HEX_CHARS, 8)
        self.assertEqual(pipeline.STABLE_ID_MODULUS, 10 ** 9)

    def test_phone_constants(self):
        self.assertEqual(pipeline.UZ_COUNTRY_CODE, '998')
        self.assertEqual(pipeline.UZ_NATIONAL_DIGITS, 9)

    def test_the_commands_are_declared(self):
        self.assertEqual(pipeline.BUY_COMMAND, '/buy')
        self.assertEqual(pipeline.STOP_COMMAND, '/stop')

    def test_the_quantity_range_is_one_fact(self):
        """It was written three times (pipeline, the model, the re-check)."""
        self.assertEqual((MIN_QUANTITY, MAX_QUANTITY), (1, 99))
        self.assertEqual(orders.MIN_QUANTITY, 1)
        self.assertEqual(orders.MAX_QUANTITY, 99)
        self.assertIs(pipeline.MIN_QUANTITY, orders.MIN_QUANTITY)
        self.assertIs(pipeline.MAX_QUANTITY, orders.MAX_QUANTITY)

    def test_the_inbound_text_ceiling_is_one_fact_with_the_control_plane(self):
        self.assertEqual(pipeline.MAX_TEXT_CHARS, platform_api.MAX_TEXT_CHARS)

    def test_no_inline_literal_survives_in_the_promoted_lines(self):
        source = Path(pipeline.__file__).read_text(encoding='utf-8')
        for literal in ('MAX_TEXT_CHARS = 4000', 'MAX_CUSTOMER_CHARS = 200',
                        'MAX_LEAD_CHARS = 500', 'STABLE_ID_HEX_CHARS = 8',
                        'STABLE_ID_MODULUS = 10 ** 9', 'UZ_COUNTRY_CODE = "998"'):
            self.assertIn(literal, source)
        for inline in ('[:4000]', '[:200]', '[:500]', 'qty > 99', '[:8]',
                       '10**9', 're.fullmatch'):
            self.assertNotIn(inline, source)

    def test_the_phone_pattern_is_built_from_its_constants(self):
        source = Path(pipeline.__file__).read_text(encoding='utf-8')
        self.assertIn('UZ_PHONE.fullmatch(p)', source)
        self.assertIn('UZ_PHONE = re.compile(UZ_COUNTRY_CODE', source)


class StableIdTests(unittest.TestCase):
    """Telegram's own update_id is kept; everything else is hashed, deterministically."""

    def test_a_numeric_id_is_used_verbatim(self):
        self.assertEqual(pipeline._stable_id('telegram', 'ignored', 777), 777)

    def test_the_hashed_path_is_deterministic(self):
        self.assertEqual(pipeline._stable_id('instagram', 'mid-1', None),
                         pipeline._stable_id('instagram', 'mid-1', None))

    def test_the_hashed_value_is_the_eight_hex_prefix_modulo_the_declared_modulus(self):
        expected = int(hashlib.sha256(b'instagram:mid-1').hexdigest()[:8], 16) % 10 ** 9
        self.assertEqual(pipeline._stable_id('instagram', 'mid-1', None), expected)

    def test_the_hashed_value_stays_inside_the_declared_modulus(self):
        for channel, key in (('instagram', 'a'), ('instagram', 'x' * 200), ('web', 'mid-2')):
            with self.subTest(key=key):
                value = pipeline._stable_id(channel, key, None)
                self.assertGreaterEqual(value, 0)
                self.assertLess(value, pipeline.STABLE_ID_MODULUS)

    def test_both_the_channel_and_the_key_feed_the_hash(self):
        self.assertNotEqual(pipeline._stable_id('instagram', 'k', None),
                            pipeline._stable_id('telegram', 'k', None))
        self.assertNotEqual(pipeline._stable_id('instagram', 'k1', None),
                            pipeline._stable_id('instagram', 'k2', None))


class PhoneNormalizationTests(unittest.TestCase):
    def test_spaces_hyphens_and_parentheses_are_removed(self):
        for raw in ('998 90 123 45 67', '998-90-123-45-67', '998 90 123-45(67)'):
            with self.subTest(raw=raw):
                self.assertEqual(pipeline._norm_phone(raw), GOOD_PHONE)

    def test_an_already_country_coded_number_is_unchanged(self):
        self.assertEqual(pipeline._norm_phone(GOOD_PHONE), GOOD_PHONE)

    def test_other_prefixes_are_left_alone(self):
        for raw in ('999012345678', '899012345678', '901234567'):
            with self.subTest(raw=raw):
                self.assertEqual(pipeline._norm_phone(raw), raw)

    def test_the_country_code_must_be_the_whole_prefix(self):
        """``998`` inside a longer number is not a match: 998 + ten digits is 13."""
        self.assertEqual(pipeline._norm_phone('998' + '0' * 10), '998' + '0' * 10)


class BuyParseTests(unittest.TestCase):
    """The /buy grammar and every ceiling it enforces, at the boundary."""

    def parse(self, text):
        return pipeline._parse_buy(text, PACK, 'shop', 5, 'telegram')

    def test_a_full_valid_line_becomes_an_order(self):
        order = self.parse('/buy P1 2 chilonzor +998901234567 Ali Vali')
        self.assertEqual((order.tenant, order.update_id), ('shop', 5))
        self.assertEqual((order.product_id, order.qty, order.branch_id), ('P1', 2, 'chilonzor'))
        self.assertEqual((order.phone, order.customer), (GOOD_PHONE, 'Ali Vali'))

    def test_a_bot_mention_suffix_is_ignored(self):
        self.assertEqual(self.parse('/buy@shop_bot P1 1 chilonzor 998901234567').qty, 1)

    def test_product_and_branch_match_case_insensitively(self):
        # The grammar splits on spaces, so a spaced phone must be one token:
        # hyphens are stripped by PHONE_CLEAN, spaces would end the field.
        order = self.parse('/buy p1 1 CHILONZOR 998-90-123-45-67')
        self.assertEqual((order.product_id, order.branch_id), ('P1', 'chilonzor'))
        self.assertEqual(order.phone, GOOD_PHONE)

    def test_trailing_punctuation_is_stripped_from_the_product_and_branch(self):
        order = self.parse('/buy P1, 1 chilonzor; +998901234567')
        self.assertEqual((order.product_id, order.branch_id), ('P1', 'chilonzor'))

    def test_the_quantity_boundaries_are_one_and_ninety_nine(self):
        self.assertEqual(self.parse('/buy P1 1 chilonzor +998901234567').qty, 1)
        self.assertEqual(self.parse('/buy P1 99 chilonzor +998901234567').qty, 99)
        for quantity in ('0', '100', '-1'):
            with self.subTest(quantity=quantity):
                with self.assertRaises(HTTPException) as caught:
                    self.parse(f'/buy P1 {quantity} chilonzor +998901234567')
                self.assertEqual(caught.exception.status_code, 422)
                self.assertEqual(caught.exception.detail, "Son 1-99 oralig'ida bo'lsin")

    def test_a_non_numeric_quantity_names_itself(self):
        with self.assertRaises(HTTPException) as caught:
            self.parse('/buy P1 abc chilonzor +998901234567')
        self.assertEqual(caught.exception.detail, "Son noto'g'ri: abc")

    def test_too_few_fields_refuse_with_the_grammar(self):
        for text in ('/buy', '/buy P1', '/buy P1 1 chilonzor', '/help P1 1 chilonzor +998901234567'):
            with self.subTest(text=text):
                with self.assertRaises(HTTPException) as caught:
                    self.parse(text)
                self.assertEqual(caught.exception.status_code, 422)
                self.assertEqual(caught.exception.detail,
                                 'Format: /buy KOD SON FILIAL TELEFON [ISM]')

    def test_an_unknown_product_or_branch_is_named(self):
        with self.assertRaises(HTTPException) as product:
            self.parse('/buy XX 1 chilonzor +998901234567')
        self.assertEqual(product.exception.detail, 'Tovar topilmadi: XX')
        with self.assertRaises(HTTPException) as branch:
            self.parse('/buy P1 1 unknown +998901234567')
        self.assertEqual(branch.exception.detail, 'Filial topilmadi: unknown')

    def test_a_phone_outside_the_uz_shape_is_refused_by_the_re_check(self):
        with self.assertRaises(HTTPException) as caught:
            self.parse('/buy P1 1 chilonzor 12345')
        self.assertEqual(caught.exception.detail, "telefon noto'g'ri")

    def test_the_customer_name_is_capped_and_defaults(self):
        order = self.parse('/buy P1 1 chilonzor +998901234567 ' + 'A' * 300)
        self.assertEqual(len(order.customer), pipeline.MAX_CUSTOMER_CHARS)
        self.assertEqual(self.parse('/buy P1 1 chilonzor +998901234567').customer, 'mijoz')


class RememberTests(unittest.TestCase):
    """Durable idempotency: the first key wins, later copies are duplicates."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'APP_DB': os.path.join(self.tmp.name, 'app.db')})
        env.start()
        self.addCleanup(env.stop)
        reset()
        self.addCleanup(reset)

    def test_the_first_key_wins_and_the_second_is_a_duplicate(self):
        self.assertTrue(pipeline.remember('shop', 'telegram', 'k1'))
        self.assertFalse(pipeline.remember('shop', 'telegram', 'k1'))

    def test_the_key_is_scoped_by_tenant_and_by_channel(self):
        self.assertTrue(pipeline.remember('shop', 'telegram', 'k1'))
        self.assertTrue(pipeline.remember('shop', 'instagram', 'k1'))
        self.assertTrue(pipeline.remember('other', 'telegram', 'k1'))


class Recorder:
    def __init__(self):
        self.args = None

    def accept_event(self, *args):
        self.args = args
        return {'ok': True, 'status': 'accepted'}


class PlatformBridgeTests(unittest.TestCase):
    """handle_text_message's platform path, with the engine seams patched."""

    def bridge(self, channel='web', text='salom', **kwargs):
        recorder = Recorder()
        with patch.dict(os.environ, {'PIPELINE_MODE': 'platform'}), \
                patch.object(pipeline, 'load_pack', lambda tenant: PACK), \
                patch('app.platform_api.engine', lambda: recorder), \
                patch('app.platform_api.call', lambda fn, *args, **kw: fn(*args, **kw)):
            result = pipeline.handle_text_message('shop', channel, 'k1', 'sender-1', text, **kwargs)
        return recorder, result

    def test_the_dashboard_channel_is_recorded_as_web(self):
        recorder, _ = self.bridge(channel='ui')
        self.assertEqual(recorder.args[:3], ('shop', 'web', 'k1'))

    def test_real_channels_pass_through_unchanged(self):
        for channel in ('telegram', 'instagram', 'whatsapp'):
            with self.subTest(channel=channel):
                recorder, _ = self.bridge(channel=channel)
                self.assertEqual(recorder.args[1], channel)

    def test_text_is_truncated_to_the_declared_ceiling(self):
        recorder, _ = self.bridge(text='y' * (pipeline.MAX_TEXT_CHARS + 100))
        self.assertEqual(len(recorder.args[3]['text']), pipeline.MAX_TEXT_CHARS)
        recorder, _ = self.bridge(text='y' * pipeline.MAX_TEXT_CHARS)
        self.assertEqual(len(recorder.args[3]['text']), pipeline.MAX_TEXT_CHARS)
        recorder, _ = self.bridge(text='salom')
        self.assertEqual(recorder.args[3]['text'], 'salom')

    def test_an_empty_text_is_recorded_as_an_empty_string(self):
        recorder, _ = self.bridge(text=None)
        self.assertEqual(recorder.args[3]['text'], '')

    def test_the_conversation_falls_back_to_the_sender(self):
        recorder, _ = self.bridge()
        self.assertEqual(recorder.args[3]['conversation_id'], 'sender-1')
        recorder, _ = self.bridge(conversation_id='chat-9')
        self.assertEqual(recorder.args[3]['conversation_id'], 'chat-9')

    def test_a_missing_pack_is_a_404_before_any_event(self):
        with patch.dict(os.environ, {'PIPELINE_MODE': 'platform'}), \
                patch.object(pipeline, 'load_pack', side_effect=PackError('missing')):
            with self.assertRaises(HTTPException) as caught:
                pipeline.handle_text_message('shop', 'telegram', 'k1', 's', 'salom')
        self.assertEqual(caught.exception.status_code, 404)

    def test_the_legacy_pipeline_is_refused_in_production(self):
        with patch.dict(os.environ, {'PIPELINE_MODE': 'legacy'}), \
                patch('app.security.is_prod', return_value=True):
            with self.assertRaises(HTTPException) as caught:
                pipeline.handle_text_message('shop', 'telegram', 'k1', 's', 'salom')
        self.assertEqual(caught.exception.status_code, 503)


class OrderModelBoundsTests(unittest.TestCase):
    """The Order model's own ceilings, not only the pipeline's truncation before them."""

    def order(self, **overrides):
        fields = dict(tenant='t', update_id=1, customer='mijoz', phone=GOOD_PHONE,
                      product_id='P1', qty=1, branch_id='chilonzor')
        fields.update(overrides)
        return orders.Order(**fields)

    def test_the_customer_ceiling_is_two_hundred(self):
        self.order(customer='A' * 200)
        with self.assertRaises(ValidationError):
            self.order(customer='A' * 201)

    def test_the_quantity_bounds_are_one_and_ninety_nine(self):
        self.order(qty=1)
        self.order(qty=99)
        for qty in (0, 100):
            with self.subTest(qty=qty):
                with self.assertRaises(ValidationError):
                    self.order(qty=qty)

    def test_the_re_check_refuses_a_payload_the_model_would_accept(self):
        """validate_order_payload is the approval-time gate; it re-reads the pack."""
        payload = self.order(qty=1).model_dump()
        orders.validate_order_payload(payload, PACK)
        for wrong in ({'product_id': 'XX'}, {'branch_id': 'nowhere'},
                      {'phone': '123'}, {'qty': 0}):
            with self.subTest(wrong=wrong):
                with self.assertRaises(ValueError):
                    orders.validate_order_payload({**payload, **wrong}, PACK)


if __name__ == '__main__':
    unittest.main()
