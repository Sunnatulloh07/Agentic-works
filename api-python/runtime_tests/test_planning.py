"""Command routing must be pack-driven; core never names a tenant's agent id.

CLAUDE.md architecture rule: "packs/*.yaml - mijoz konfigi. Core kod pack
mazmuniga bog'lanmaydi." Regression for the defect where /report, /memory and
/record hardcoded 'ops.assistant', so every pack without that exact agent id
failed closed on the inbound channel. Dependency-free: the agent listing is
injected, no pack file, no FastAPI and no environment is required.
"""
import unittest
from pathlib import Path
from unittest.mock import patch

from app.planning import planner, route


# A retail pack that happens to contain ops.assistant.
RETAIL = [
    {'id': 'sales.responder', 'tools': ['telegram.send'], 'ladder': 'human_led'},
    {'id': 'ops.assistant', 'ladder': 'human_assisted',
     'tools': ['records.create', 'memory.search', 'reports.summary']},
]
# A different vertical (PRD 8.1 training centre). No agent is called ops.assistant.
SCHOOL = [
    {'id': 'sales.lead_responder', 'tools': ['telegram.send'], 'ladder': 'human_assisted'},
    {'id': 'intel.director_report', 'tools': ['reports.summary'], 'ladder': 'autonomous'},
    {'id': 'ops.registrar', 'tools': ['records.create', 'memory.search'],
     'ladder': 'human_assisted'},
]
PACKS = {'demo-retail': RETAIL, 'oquv-markaz': SCHOOL}


def listing(mapping=None):
    source = PACKS if mapping is None else mapping

    def read(tenant):
        if tenant not in source:
            raise LookupError('Unknown tenant pack')
        return source[tenant]
    return read


def message(text):
    return {'text': text, 'sender': 'u1', 'conversation_id': 'u1'}


class RouteTests(unittest.TestCase):
    def test_returns_first_pack_agent_holding_the_capability(self):
        self.assertEqual('ops.assistant', route(listing(), 'demo-retail', 'reports.summary'))

    def test_same_capability_resolves_to_a_different_id_per_pack(self):
        self.assertEqual('intel.director_report',
                         route(listing(), 'oquv-markaz', 'reports.summary'))

    def test_pack_order_decides_deterministically(self):
        both = [{'id': 'first', 'tools': ['reports.summary']},
                {'id': 'second', 'tools': ['reports.summary']}]
        read = listing({'t': both})
        self.assertEqual('first', route(read, 't', 'reports.summary'))
        self.assertEqual('second', route(listing({'t': both[::-1]}), 't', 'reports.summary'))

    def test_agents_without_the_capability_are_skipped(self):
        self.assertEqual('ops.registrar', route(listing(), 'oquv-markaz', 'records.create'))

    def test_agent_without_any_tools_does_not_crash_routing(self):
        read = listing({'t': [{'id': 'empty'}, {'id': 'real', 'tools': ['reports.summary']}]})
        self.assertEqual('real', route(read, 't', 'reports.summary'))

    def test_missing_capability_names_the_capability_not_an_agent(self):
        read = listing({'t': [{'id': 'only', 'tools': ['telegram.send']}]})
        with self.assertRaises(LookupError) as caught:
            route(read, 't', 'reports.summary')
        self.assertIn('reports.summary', str(caught.exception))

    def test_routing_is_tenant_scoped(self):
        with self.assertRaises(LookupError):
            route(listing(), 'other-tenant', 'reports.summary')


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.plan = planner(object(), listing())

    def test_report_uses_the_packs_own_agent(self):
        self.assertEqual({'agent': 'ops.assistant',
                          'steps': [{'tool': 'reports.summary', 'args': {}}]},
                         self.plan('demo-retail', 'telegram', message('/report')))

    def test_report_works_for_a_pack_without_ops_assistant(self):
        result = self.plan('oquv-markaz', 'telegram', message('/report'))
        self.assertEqual('intel.director_report', result['agent'])
        self.assertEqual([{'tool': 'reports.summary', 'args': {}}], result['steps'])

    def test_memory_command_routes_by_capability_and_keeps_the_query(self):
        result = self.plan('oquv-markaz', 'web', message('/memory kurs narxi'))
        self.assertEqual('ops.registrar', result['agent'])
        self.assertEqual([{'tool': 'memory.search', 'args': {'query': 'kurs narxi'}}],
                         result['steps'])

    def test_record_command_routes_by_capability_and_splits_three_fields(self):
        result = self.plan('oquv-markaz', 'web', message('/record lead|Ali|sinov darsi'))
        self.assertEqual('ops.registrar', result['agent'])
        self.assertEqual([{'tool': 'records.create',
                           'args': {'kind': 'lead', 'title': 'Ali', 'body': 'sinov darsi'}}],
                         result['steps'])

    def test_record_still_rejects_a_malformed_argument_list(self):
        with self.assertRaises(ValueError):
            self.plan('demo-retail', 'web', message('/record only-one-field'))

    def test_pack_missing_the_capability_fails_with_a_clear_reason(self):
        read = listing({'t': [{'id': 'sales.only', 'tools': ['telegram.send']}]})
        with self.assertRaises(LookupError):
            planner(object(), read)('t', 'telegram', message('/report'))

    def test_free_text_is_delegated_to_the_configured_model_planner(self):
        with patch('app.planning.Planner') as model:
            model.return_value.return_value = {'agent': 'sales.responder', 'steps': []}
            result = planner(object(), listing())('demo-retail', 'telegram', message('salom'))
        self.assertEqual({'agent': 'sales.responder', 'steps': []}, result)


class SourceRuleTests(unittest.TestCase):
    """The architecture rule itself, not only its current symptom."""

    def test_core_planning_module_names_no_pack_agent_id(self):
        source = (Path(__file__).resolve().parents[1] / 'app' / 'planning.py').read_text(encoding='utf-8')
        for hardcoded in ('ops.assistant', 'sales.responder', 'demo-retail'):
            self.assertNotIn(hardcoded, source,
                             'Core must not name pack content: ' + hardcoded)


if __name__ == '__main__':
    unittest.main()
