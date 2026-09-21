import unittest
from platform_runtime.model_response import parse_decision


def response(text='{"action":"ask","question":"Aniqlashtir"}',**kwargs):
    return {'choices':[{'finish_reason':'stop','message':{'content':text},**kwargs}]}


class ModelResponseTests(unittest.TestCase):
    def test_complete_json(self):self.assertEqual('ask',parse_decision(response())['action'])
    def test_duplicate_keys(self):
        with self.assertRaises(ValueError):parse_decision(response('{"action":"ask","action":"tool"}'))
    def test_nonfinite(self):
        for value in ['NaN','Infinity','-Infinity']:
            with self.subTest(value=value),self.assertRaises(ValueError):parse_decision(response('{"number":'+value+'}'))
    def test_truncated_denied(self):
        for finish in ['length','content_filter',None,'tool_calls']:
            with self.subTest(finish=finish),self.assertRaises(ValueError):parse_decision(response(finish_reason=finish))
    def test_multiple_choices_denied(self):
        r=response();r['choices']*=2
        with self.assertRaises(ValueError):parse_decision(r)
    def test_nonobject_json_denied(self):
        for text in ['[]','null','1','true','"hello"']:
            with self.subTest(text=text),self.assertRaises(ValueError):parse_decision(response(text))
    def test_refusal_denied(self):
        r=response();r['choices'][0]['message']['refusal']='policy refusal'
        with self.assertRaises(ValueError):parse_decision(r)
    def test_wrong_envelope_denied(self):
        for r in [None,[],{}, {'choices':[]},{'choices':[None]}]:
            with self.subTest(r=r),self.assertRaises(ValueError):parse_decision(r)
    def test_deep_json_bounded_exception(self):
        with self.assertRaises(ValueError):parse_decision(response('{"a":'+'['*1500+'0'+']'*1500+'}'))
    def test_byte_not_character_limit(self):
        with self.assertRaises(ValueError):parse_decision(response('{"value":"'+'ў'*11000+'"}'))
