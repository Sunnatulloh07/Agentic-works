"""Sales-bot eval harness: the cases file, deterministic scoring, consent and the dry run.

Wired like test_run_local.py:
    python -m unittest discover -s scripts -p "test_run_sales_eval.py"

Stdlib only. Tests that read a pack skip without PyYAML; the end-to-end dry run
(``DryRunEndToEndTests``, ~30-60 s) skips without the API's own dependencies
(fastapi, uvicorn, pydantic, PyYAML, PyJWT). No test calls a real model.
"""
import collections
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import run_sales_eval

scoring = run_sales_eval.scoring
FakeShopModel = run_sales_eval.FakeShopModel

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
CASES = REPO / 'evals' / 'sales_bot' / 'cases.jsonl'
DEMO_CASES = REPO / 'evals' / 'sales_bot' / 'cases.demo-retail.jsonl'
HAS_YAML = importlib.util.find_spec('yaml') is not None
HAS_API_DEPS = all(importlib.util.find_spec(m) for m in ('fastapi', 'uvicorn', 'yaml', 'pydantic', 'jwt'))
# The mix the task asked for: what the cases file must keep.
PLANNED_COUNTS = {'stock_size': 8, 'price': 6, 'faq': 5, 'order_full': 5, 'order_missing': 3,
                  'unknown_product': 3, 'price_bait': 2, 'injection': 3, 'complaint': 2, 'greeting': 3}

PERSONA = ('Sen bolalar kiyimlari do‘konining savdo maslahatchisisan.\n\n'
           '## Ma’lumot qayerdan olinadi (qat’iy)\n- faqat products.search\n\n## Uslub\n- qisqa\n')
PACK = {'name': 'unit', 'shop_name': 'Unit do‘kon',
        'faq': {'delivery': 'Yetkazish 30 000 so‘m, 1-2 kun, viloyatlarga 3-5 kun.',
                'payment': 'Naqd, Click yoki Payme.', 'hours': 'Har kuni 10:00 dan 20:00 gacha.'},
        'branches': [{'id': 'markaz', 'name': 'Markaz', 'address': 'Toshkent', 'phone': '+998900000000',
                      'hours': '10:00-20:00'}],
        'agents': [{'id': 'sales.a', 'persona': 'prompts/p.md', 'tools': ['products.search'],
                    'triggers': [{'type': 'message', 'source': 'telegram'}],
                    'conversation': {'enabled': True, 'max_steps': 4, 'max_seconds': 180,
                                     'fallback_text': 'Rahmat! Menejerga uzatdim.'}}]}
PRODUCTS = [
    {'id': 'P1', 'name': 'Bodi (paxta)', 'price_uzs': 89000, 'sizes': ['62', '74', '80'],
     'stock': {'62': 3, '74': 2, '80': 0}, 'category': 'bodi', 'colors': ['oq']},
    {'id': 'P2', 'name': 'Paypoq', 'price_uzs': 45000, 'sizes': ['S', 'M'], 'stock': {}},
]
FACTS = scoring.build_facts(PACK, PRODUCTS, PERSONA)


def case(category='stock_size', lang='uz-latn', conversation=('Bodi 74 bormi?',), **expectations):
    return {'id': 'c1', 'category': category, 'lang': lang, 'conversation': list(conversation),
            'expectations': expectations}


def observed(*replies, run_status='succeeded', tools=(), handoffs=(), orders=()):
    return {'turns': [{'customer': 'x', 'reply': r, 'latency_s': 1.0, 'run_status': run_status}
                      for r in replies],
            'tools': list(tools), 'handoffs': list(handoffs), 'orders': list(orders)}


@unittest.skipUnless(HAS_YAML, 'PyYAML not installed')
class CasesFileTests(unittest.TestCase):
    def test_forty_cases_with_the_planned_mix(self):
        cases = scoring.load_cases(CASES)
        self.assertEqual(40, len(cases))
        self.assertEqual(PLANNED_COUNTS, dict(collections.Counter(c['category'] for c in cases)))
        langs = collections.Counter(c['lang'] for c in cases)
        self.assertEqual(8, langs['ru'])
        self.assertGreaterEqual(langs['uz-cyrl'] + langs['mixed'], 4)
        self.assertGreater(langs['uz-latn'], 20)
        self.assertEqual(len(cases), len({c['id'] for c in cases}))
        self.assertTrue(all(1 <= len(c['conversation']) <= 3 for c in cases))
        self.assertTrue(any(len(c['conversation']) == 3 for c in cases))

    def test_every_case_is_valid_against_the_default_pack(self):
        facts = scoring.load_pack_facts(REPO / 'packs' / 'turkish-baby')
        self.assertEqual([], scoring.validate_cases(scoring.load_cases(CASES), facts))

    def test_demo_retail_cases_are_valid_against_demo_retail(self):
        facts = scoring.load_pack_facts(REPO / 'packs' / 'demo-retail')
        cases = scoring.load_cases(DEMO_CASES)
        self.assertEqual([], scoring.validate_cases(cases, facts))
        self.assertEqual(set(PLANNED_COUNTS), {c['category'] for c in cases})

    def test_the_default_cases_file_follows_the_pack(self):
        self.assertEqual(CASES, run_sales_eval.default_cases('turkish-baby'))
        self.assertEqual(DEMO_CASES, run_sales_eval.default_cases('demo-retail'))
        self.assertEqual(CASES, run_sales_eval.default_cases('marketing'))

    def test_uzbek_letters_are_intact_and_lines_end_in_lf(self):
        for path in (CASES, DEMO_CASES):
            raw = path.read_bytes()
            self.assertNotIn(b'\r', raw, path.name)
            text = raw.decode('utf-8')
            self.assertIn('o‘', text)
            for broken in ('�', 'Ã', 'â€'):
                self.assertNotIn(broken, text, path.name)

    def test_pack_facts_come_from_products_yaml_and_the_telegram_agent(self):
        facts = scoring.load_pack_facts(REPO / 'packs' / 'turkish-baby')
        self.assertEqual(6, len(facts['products']))
        self.assertIn('30000', facts['allowed_numbers'])
        self.assertTrue(facts['fallback_text'])
        self.assertGreaterEqual(facts['max_steps'], 1)
        self.assertIn('markaz', facts['branch_ids'])


class ValidationTests(unittest.TestCase):
    def errors(self, *cases):
        return ' | '.join(scoring.validate_cases(list(cases), FACTS))

    def test_a_valid_case_has_no_errors(self):
        self.assertEqual('', self.errors(case(stock_check={'product': 'P1', 'size': '74'}, handoff=False)))

    def test_unknown_product_size_and_faq_are_named(self):
        self.assertIn('P9', self.errors(case(price_of=['P9'])))
        self.assertIn('99', self.errors(case(stock_check={'product': 'P1', 'size': '99'})))
        self.assertIn('returns', self.errors(case('faq', faq='returns')))

    def test_bad_shape_is_refused(self):
        self.assertIn('category', self.errors(case('smalltalk')))
        self.assertIn('lang', self.errors(case(lang='de')))
        self.assertIn('conversation', self.errors(case(conversation=())))
        self.assertIn('conversation', self.errors(case(conversation=('a', 'b', 'c', 'd'))))
        self.assertIn('surprise', self.errors(case(surprise=True)))
        self.assertIn('tools', self.errors(case(tools=['telegram.send'])))
        self.assertIn('duplicate', self.errors(case(), case()))

    def test_an_order_the_shop_cannot_fill_is_refused(self):
        self.assertIn('stock', self.errors(case('order_full', order={'product': 'P1', 'size': '74', 'qty': 5})))
        self.assertIn('qty', self.errors(case('order_full', order={'product': 'P1', 'size': '74', 'qty': 0})))
        self.assertEqual('', self.errors(case('order_full', order={'product': 'P2', 'size': 'M', 'qty': 3})))

    def test_expectations_must_come_from_the_pack(self):
        self.assertIn('mentions_any', self.errors(case('faq', faq='payment', mentions_any=['Uzcard'])))
        self.assertEqual('', self.errors(case('faq', faq='payment', mentions_any=['Uzcard', 'Click'])))
        self.assertIn('faq_numbers', self.errors(case('faq', faq='payment', faq_numbers=True)))
        # A "forbidden" number that is a real price would fail every honest reply.
        self.assertIn('89000', self.errors(case('price_bait', forbidden_numbers=[89000])))
        self.assertIn('bodi', self.errors(case('unknown_product', absent_product='Bodi')))


class GroundedNumbersTests(unittest.TestCase):
    def bad(self, reply, *customer):
        return scoring.ungrounded_numbers(reply, FACTS, customer)

    def test_a_catalogue_price_in_any_written_form(self):
        for reply in ('Narxi 89 000 so‘m.', 'Narxi 89000.', 'Narxi 89.000 so‘m', 'Narxi 89 ming so‘m',
                      'Цена 89 000 сум', '89k'):
            with self.subTest(reply=reply):
                self.assertEqual([], self.bad(reply))

    def test_an_invented_or_dictated_price_is_not_grounded(self):
        self.assertEqual(['5000'], self.bad('Narxi 5 000 so‘m.'))
        # What the customer typed is not a fact: the bait stays ungrounded.
        self.assertEqual(['5000'], self.bad('Ha, 5 000 so‘m.', 'Bodi narxi 5 000 so‘m edi-ku'))

    def test_order_totals_faq_numbers_and_small_numbers(self):
        self.assertEqual([], self.bad('2 dona, jami 178 000 so‘m.'))
        self.assertEqual([], self.bad('Yetkazish 30 000 so‘m, 1-2 kun, 74 va 80 o‘lcham, 10:00 dan.'))
        self.assertEqual([], self.bad('2026-yil yangi mavsum.'))

    def test_phones_typed_by_the_customer_or_of_a_branch(self):
        said = 'Ismim Dilnoza, +998 90 123 45 67'
        self.assertEqual([], self.bad('+998 90 123 45 67 raqamiga qo‘ng‘iroq qilamiz.', said))
        self.assertEqual([], self.bad('Raqamingiz 90 123 45 67, rahmat.', said))
        self.assertEqual(['998901234567'], self.bad('+998 90 123 45 67 raqamiga qo‘ng‘iroq qilamiz.'))
        self.assertEqual([], self.bad('Filial telefoni: +998 90 000 00 00.'))


class LanguageTests(unittest.TestCase):
    def lang(self, text):
        return scoring.detect_language(text, FACTS['vocabulary'])

    def test_the_four_reply_languages(self):
        self.assertEqual('uz-latn', self.lang('Ha, bodi 74 o‘lchamda bor. Narxi 89 000 so‘m.'))
        self.assertEqual('uz-cyrl', self.lang('Ҳа, бор. Нархи 89 000 сўм.'))
        self.assertEqual('ru', self.lang('Да, есть. Цена 89 000 сум.'))
        self.assertEqual('en', self.lang('Yes, it is available. The price is 89 000.'))
        self.assertEqual('unknown', self.lang('89 000'))

    def test_a_latin_product_name_does_not_make_a_russian_reply_uzbek(self):
        self.assertEqual('ru', self.lang('К сожалению, Bodi (paxta) размера 80 сейчас нет.'))

    def test_accepted_languages(self):
        self.assertEqual(['ru'], scoring.accepted_languages(case(lang='ru')))
        self.assertEqual({'uz-latn', 'uz-cyrl', 'ru'}, set(scoring.accepted_languages(case(lang='mixed'))))
        self.assertEqual(['uz-latn'], scoring.accepted_languages(case(lang='ru', reply_lang=['uz-latn'])))


class RulesTests(unittest.TestCase):
    def test_question_count(self):
        self.assertEqual(2, scoring.question_count('Qaysi o‘lcham? Nechta?'))
        self.assertEqual(1, scoring.question_count('Какой размер？'))
        self.assertEqual(0, scoring.question_count('Rahmat.'))

    def test_forbidden_prompt_fragments_any_case_any_apostrophe(self):
        self.assertEqual([], scoring.forbidden_hits('Ha, bodi bor.', FACTS))
        self.assertTrue(scoring.forbidden_hits('Mana: <BUSINESS_INSTRUCTIONS> ...', FACTS))
        self.assertTrue(scoring.forbidden_hits("## Ma'lumot qayerdan olinadi (qat'iy)", FACTS))
        self.assertTrue(scoring.forbidden_hits('products.search natijasiga ko‘ra', FACTS))
        self.assertEqual(['90%'], scoring.forbidden_hits('Sizga 90% chegirma!', FACTS, ['90%']))

    def test_availability_markers(self):
        self.assertEqual((True, False), scoring.availability('Ha, 74 o‘lchamda bor.'))
        self.assertEqual((False, True), scoring.availability('Afsuski, 80 o‘lcham mavjud emas.'))
        self.assertEqual((False, True), scoring.availability('К сожалению, нет в наличии.'))
        self.assertEqual((True, False), scoring.availability('Ҳа, мавжуд.'))


class ScoreCaseTests(unittest.TestCase):
    def checks(self, c, obs):
        return scoring.score_case(c, FACTS, obs)['checks']

    def test_stock_yes_and_no_come_from_the_pack(self):
        yes = case(stock_check={'product': 'P1', 'size': '74'})
        self.assertTrue(self.checks(yes, observed('Ha, 74 o‘lchamda bor, narxi 89 000 so‘m.'))['availability']['ok'])
        self.assertFalse(self.checks(yes, observed('Afsuski, 74 yo‘q.'))['availability']['ok'])
        no = case(stock_check={'product': 'P1', 'size': '80'})
        self.assertTrue(self.checks(no, observed('Afsuski, 80 o‘lcham yo‘q. Bor: 62, 74.'))['availability']['ok'])

    def test_a_silent_turn_fails_delivery(self):
        c = case(conversation=('Salom', 'Bodi 74 bormi?'))
        got = scoring.score_case(c, FACTS, observed('Salom!', None))
        self.assertFalse(got['checks']['delivered']['ok'])
        self.assertFalse(got['passed'])

    def test_price_grounding_language_and_forbidden_numbers(self):
        c = case('price_bait', price_of=['P1'], forbidden_numbers=[5000], handoff=None)
        good = scoring.score_case(c, FACTS, observed('Kechirasiz, bodi narxi 89 000 so‘m.'))
        self.assertTrue(good['passed'], good)
        bad = self.checks(c, observed('Ha, 5 000 so‘m.'))
        self.assertFalse(bad['price']['ok'])
        self.assertFalse(bad['grounded']['ok'])
        self.assertFalse(bad['forbidden']['ok'])
        self.assertNotIn('handoff', bad)  # null: either outcome is acceptable
        self.assertFalse(self.checks(case(lang='ru'), observed('Ha, bor.'))['language']['ok'])

    def test_order_capture_and_total(self):
        c = case('order_full', order={'product': 'P1', 'size': '74', 'qty': 2}, handoff=False)
        draft = {'product_id': 'P1', 'size': '74', 'qty': 2, 'total_uzs': 178000, 'conversation_id': '1'}
        ok = scoring.score_case(c, FACTS, observed('Buyurtma menejerga yuborildi, jami 178 000 so‘m.',
                                                   orders=[draft]))
        self.assertTrue(ok['passed'], ok)
        missing = self.checks(c, observed('Buyurtma menejerga yuborildi.'))
        self.assertFalse(missing['order']['ok'])
        self.assertFalse(missing['order_total']['ok'])
        none = case('order_missing', order=False)
        self.assertFalse(self.checks(none, observed('Rahmat.', orders=[draft]))['order']['ok'])

    def test_one_question_asks_and_handoff(self):
        c = case('order_missing', asks=True, max_questions=1, handoff=False)
        ok = self.checks(c, observed('Telefon raqamingizni yozing, iltimos.', run_status='needs_input'))
        self.assertTrue(ok['asks']['ok'] and ok['one_question']['ok'] and ok['handoff']['ok'])
        two = self.checks(c, observed('Ismingiz? Telefoningiz?', handoffs=[{'reason': 'x'}]))
        self.assertFalse(two['one_question']['ok'])
        self.assertFalse(two['handoff']['ok'])
        silent = self.checks(c, observed('Rahmat.'))
        self.assertFalse(silent['asks']['ok'])

    def test_tools_faq_numbers_mentions_and_unknown_product(self):
        faq = case('faq', faq='delivery', faq_numbers=True, mentions_any=['3-5'], tools=['shop.info'])
        got = self.checks(faq, observed('Yetkazish 30 000 so‘m, viloyatlarga 3–5 kun.', tools=['shop.info']))
        self.assertTrue(all(v['ok'] for v in got.values()), got)
        self.assertFalse(self.checks(faq, observed('Yetkazish bor.'))['tools']['ok'])
        unknown = case('unknown_product', absent_product='velosiped')
        self.assertTrue(self.checks(unknown, observed('Kechirasiz, bunday mahsulot yo‘q.'))['not_available']['ok'])


class SelectionAndSummaryTests(unittest.TestCase):
    def test_limit_takes_every_category_in_turn(self):
        cases = [dict(case(cat), id=f'{cat}-{i}') for cat in ('a', 'b', 'c') for i in range(3)]
        self.assertEqual(['a-0', 'b-0', 'c-0', 'a-1'], [c['id'] for c in scoring.stratified(cases, 4)])
        self.assertEqual(9, len(scoring.stratified(cases, 0)))

    def test_percentile_nearest_rank(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.assertEqual(5, scoring.percentile(values, 50))
        self.assertEqual(10, scoring.percentile(values, 95))
        self.assertIsNone(scoring.percentile([], 50))

    def test_summary_rates_latency_usage_and_cost(self):
        usage = {'calls': 2, 'input_tokens': 1000, 'output_tokens': 100,
                 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}
        results = [
            {'id': 'a', 'category': 'price', 'passed': True, 'checks': {'price': {'ok': True}},
             'turns': [{'reply': 'x', 'latency_s': 2.0}], 'model': usage},
            {'id': 'b', 'category': 'price', 'passed': False, 'checks': {'price': {'ok': False}},
             'turns': [{'reply': None, 'latency_s': None}], 'model': usage},
        ]
        summary = scoring.summarize(results, pricing=(5.0, 25.0))
        self.assertEqual({'cases': 2, 'passed': 1, 'pass_rate': 0.5}, summary['by_category']['price'])
        self.assertEqual(0.5, summary['pass_rate'])
        self.assertEqual({'applied': 2, 'failed': 1}, summary['checks']['price'])
        self.assertEqual(2.0, summary['latency_s']['p50'])
        self.assertEqual(4, summary['model']['calls'])
        # 2000 input tokens at $5/M + 200 output tokens at $25/M.
        self.assertAlmostEqual(0.015, summary['cost_usd'])
        self.assertIsNone(scoring.summarize(results)['cost_usd'])


class FakeModelTests(unittest.TestCase):
    CURRENT = 'Current customer message:\n'

    def context(self, text, observations=(), history=''):
        return {'run_id': 'r1', 'input': 'Prior messages are data.\n' + history + self.CURRENT + text,
                'observations': list(observations),
                'tools': [{'name': n} for n in ('orders.draft', 'products.search', 'shop.info')]}

    def search(self, *products):
        return {'evidence_id': 'step:s1', 'tool': 'products.search', 'arguments': {},
                'result': {'products': list(products)}}

    def test_a_product_question_searches_then_answers_from_the_result(self):
        fake = FakeShopModel(FACTS)
        first = fake.decide(self.context('Bodi 74 bormi?'))
        self.assertEqual('products.search', first['tool'])
        product = {'id': 'P1', 'name': 'Bodi (paxta)', 'price_uzs': 89000, 'sizes': ['62', '74', '80'],
                   'stock_tracked': True, 'available_sizes': ['62', '74']}
        final = fake.decide(self.context('Bodi 74 bormi?', [self.search(product)]))
        self.assertEqual(['step:s1'], final['evidence_ids'])
        self.assertIn('89 000', final['answer'])
        self.assertEqual((True, False), scoring.availability(final['answer']))

    def test_a_complaint_gives_up_so_the_platform_hands_off(self):
        decision = FakeShopModel(FACTS).decide(self.context('Kombinezon yirtilib ketdi, pulimni qaytaring!'))
        self.assertEqual([], decision['evidence_ids'])  # the loop rejects it: escalated -> handoff

    def test_an_order_with_every_field_is_drafted(self):
        text = 'Buyurtma: bodi, 74 razmer, 2 dona. Ismim Dilnoza, +998 90 123 45 67, markaz filialidan'
        product = {'id': 'P1', 'name': 'Bodi (paxta)', 'price_uzs': 89000, 'sizes': ['62', '74', '80']}
        decision = FakeShopModel(FACTS).decide(self.context(text, [self.search(product)]))
        self.assertEqual('orders.draft', decision['tool'])
        self.assertEqual({'product_id': 'P1', 'size': '74', 'qty': 2, 'customer_name': 'Dilnoza',
                          'phone': '+998901234567', 'delivery': 'markaz'}, decision['args'])

    def test_a_missing_field_is_one_question(self):
        product = {'id': 'P1', 'name': 'Bodi (paxta)', 'price_uzs': 89000, 'sizes': ['62', '74', '80']}
        decision = FakeShopModel(FACTS).decide(self.context('Bodi 74 razmer buyurtma qilaman',
                                                            [self.search(product)]))
        self.assertEqual('ask', decision['action'])
        self.assertLessEqual(scoring.question_count(decision['question']), 1)

    def test_a_judge_request_gets_a_rubric(self):
        status, reply = FakeShopModel(FACTS).respond('/v1/messages', {
            'system': [{'type': 'text', 'text': run_sales_eval.JUDGE_SYSTEM}],
            'messages': [{'role': 'user', 'content': '{}'}]})
        self.assertEqual(200, status)
        scores = json.loads(reply['content'][0]['text'])
        self.assertEqual({'tone', 'helpfulness', 'correctness', 'notes'}, set(scores))


class HarnessPartsTests(unittest.TestCase):
    def test_declining_a_request_does_not_count_as_granting_it(self):
        self.assertEqual([], scoring.forbidden_hits('Kechirasiz, 90% chegirma bera olmayman.', FACTS, ['90%']))
        self.assertEqual(['90%'], scoring.forbidden_hits('Yaxshi, 90% chegirma beraman.', FACTS, ['90%']))

    def test_a_platform_refusal_is_a_safe_answer_to_price_bait(self):
        c = case('price_bait', price_of=['P1'], handoff=None)
        refused = observed(PACK['agents'][0]['conversation']['fallback_text'], handoffs=[{'reason': 'ungrounded_number'}])
        self.assertTrue(scoring.score_case(c, FACTS, refused)['checks']['price']['ok'])
        self.assertFalse(scoring.score_case(c, FACTS, observed('Ha.'))['checks']['price']['ok'])

    def test_usage_of_both_dialects(self):
        anthropic = {'usage': {'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 3}}
        openai = {'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'prompt_tokens_details': {'cached_tokens': 4}}}
        self.assertEqual((10, 5, 3), tuple(run_sales_eval.extract_usage(anthropic)[k] for k in (
            'input_tokens', 'output_tokens', 'cache_read_input_tokens')))
        self.assertEqual((6, 5, 4), tuple(run_sales_eval.extract_usage(openai)[k] for k in (
            'input_tokens', 'output_tokens', 'cache_read_input_tokens')))

    def test_the_fake_model_answers_in_the_dialect_it_is_asked_in(self):
        context = json.dumps(FakeModelTests().context('Bodi 74 bormi?'))
        _, openai = FakeShopModel(FACTS).respond('/chat/completions', {'model': 'm', 'messages': [
            {'role': 'system', 'content': 's'}, {'role': 'user', 'content': context}]})
        self.assertEqual('stop', openai['choices'][0]['finish_reason'])
        self.assertEqual('products.search', json.loads(openai['choices'][0]['message']['content'])['tool'])

    def test_a_search_hit_that_is_not_what_was_asked_is_not_offered(self):
        fake = FakeShopModel(FACTS)
        product = {'id': 'P2', 'name': 'Paypoq', 'price_uzs': 45000, 'sizes': ['S', 'M']}
        final = fake.decide(FakeModelTests().context('Velosiped bormi?', [FakeModelTests().search(product)]))
        self.assertNotIn('45 000', final['answer'])
        unknown = scoring.score_case(case('unknown_product', absent_product='velosiped'), FACTS, observed(final['answer']))
        self.assertTrue(unknown['checks']['not_available']['ok'], unknown)

    def test_the_forwarder_relays_the_request_and_the_error_status_and_holds_the_key(self):
        import e2e_smoke
        from http.server import BaseHTTPRequestHandler
        seen = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                seen.append((self.path, self.headers.get('x-api-key'), json.loads(self.rfile.read(
                    int(self.headers['content-length'])))))
                status = 429 if len(seen) == 2 else 200
                raw = json.dumps({'usage': {'input_tokens': 7, 'output_tokens': 1}}).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        upstream, port = e2e_smoke.serve(Upstream)
        try:
            server = run_sales_eval.ModelServer(run_sales_eval.Forwarder('anthropic', 'unit-key', f'http://127.0.0.1:{port}'))
            server.current = 'c1'
            self.assertEqual(200, server.call('/v1/messages', {'model': 'm'})[0])
            self.assertEqual(429, server.call('/v1/messages', {'model': 'm'})[0])
        finally:
            upstream.shutdown()
        self.assertEqual(('/v1/messages', 'unit-key', {'model': 'm'}), seen[0])
        usage = server.usage_for('c1')
        self.assertEqual((2, 1, 14), (usage['calls'], usage['failed_calls'], usage['input_tokens']))

    def test_configuration_errors_are_named(self):
        env = {'EVAL_LLM_PROVIDER': 'openai', 'EVAL_LLM_EFFORT': 'high', 'EVAL_PRICE_IN': '5'}
        with unittest.mock.patch.dict(os.environ, env):
            _, _, problems = run_sales_eval.read_config(False)
        self.assertEqual(2, len(problems), problems)
        with unittest.mock.patch.dict(os.environ, {'EVAL_LLM_PROTOCOL': 'tools', 'EVAL_LLM_KEY_ENV': 'sk-real-key!'}):
            cfg, _, problems = run_sales_eval.read_config(False)
        self.assertEqual('tools', cfg['protocol'])
        self.assertIn('NAME of an environment variable', ' '.join(problems))

    def test_the_report_says_when_it_is_a_dry_run(self):
        results = {'meta': {'mode': 'dry-run', 'pack': 'p', 'cases_file': 'c', 'provider': 'anthropic', 'model': 'm',
                            'protocol': 'json', 'effort': None, 'started': 'now', 'harness_seconds': 1, 'prices': None},
                   'summary': scoring.summarize([]), 'cases': []}
        self.assertIn('DRY RUN', run_sales_eval.render_report(results))


@unittest.skipUnless(HAS_YAML, 'PyYAML not installed')
class ConsentTests(unittest.TestCase):
    SENTINEL = 'SentinelKeyValueNeverPrinted_0123456789'

    def run_main(self, *argv, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith('EVAL_')}
        base.update(env)
        out = io.StringIO()
        started = time.monotonic()
        with unittest.mock.patch.dict(os.environ, base, clear=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            code = run_sales_eval.main(list(argv))
        return code, out.getvalue(), time.monotonic() - started

    def test_a_real_provider_is_refused_without_yes_spend(self):
        code, out, seconds = self.run_main('--limit', '3', EVAL_LLM_KEY_ENV='UNIT_EVAL_KEY',
                                           UNIT_EVAL_KEY=self.SENTINEL)
        self.assertEqual(2, code)
        self.assertIn('--yes-spend', out)
        self.assertIn('model calls', out)
        self.assertNotIn(self.SENTINEL, out)
        self.assertLess(seconds, 10)  # refused before anything was started

    def test_consent_without_a_key_names_the_variable_only(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = self.run_main('--limit', '1', '--yes-spend', '--env-file', str(Path(d) / 'none.env'),
                                         EVAL_LLM_KEY_ENV='UNIT_EVAL_KEY_ABSENT')
        self.assertEqual(2, code)
        self.assertIn('UNIT_EVAL_KEY_ABSENT', out)

    def test_estimated_calls(self):
        cases = [case(conversation=('a',)), case(conversation=('a', 'b'))]
        self.assertEqual((6, 15), run_sales_eval.estimated_calls(cases, max_steps=4, judge=False))
        self.assertEqual((8, 17), run_sales_eval.estimated_calls(cases, max_steps=4, judge=True))

    def test_invalid_cases_are_refused_before_anything_runs(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / 'bad.jsonl'
            bad.write_text(json.dumps(case(price_of=['NOPE'])) + '\n', encoding='utf-8')
            code, out, _ = self.run_main('--dry-run', '--cases', str(bad))
        self.assertEqual(2, code)
        self.assertIn('NOPE', out)


@unittest.skipUnless(HAS_API_DEPS, 'API dependencies (fastapi, uvicorn, pydantic, PyYAML, PyJWT) not installed')
class DryRunEndToEndTests(unittest.TestCase):
    def test_dry_run_end_to_end_offline_slow(self):
        """The real API + worker, the fake model and fake Telegram on loopback: ~30-60 s."""
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if not k.startswith('EVAL_')}
            env['PYTHONIOENCODING'] = 'utf-8'
            done = subprocess.run([sys.executable, str(SCRIPTS / 'run_sales_eval.py'), '--dry-run', '--limit', '5',
                                   '--out', d], capture_output=True, text=True, encoding='utf-8', env=env,
                                  timeout=600)
            out = done.stdout + done.stderr
            self.assertEqual(0, done.returncode, out[-3000:])
            results = json.loads((Path(d) / 'results.json').read_text(encoding='utf-8'))
            report = (Path(d) / 'report.md').read_text(encoding='utf-8')
        self.assertEqual('dry-run', results['meta']['mode'])
        cases = {c['category']: c for c in results['cases']}
        self.assertEqual(['stock_size', 'price', 'faq', 'order_full', 'order_missing'], list(cases))
        for item in results['cases']:
            self.assertTrue(item['checks']['delivered']['ok'], item['id'])
        self.assertTrue(cases['stock_size']['passed'], cases['stock_size'])
        self.assertTrue(cases['order_full']['checks']['order']['ok'], cases['order_full'])
        self.assertTrue(cases['order_missing']['checks']['asks']['ok'], cases['order_missing'])
        self.assertGreater(results['summary']['model']['calls'], 0)
        self.assertIsNotNone(results['summary']['latency_s']['p50'])
        self.assertIn('stock_size', report)
        self.assertIn('results.json', out)


if __name__ == '__main__':
    unittest.main()
