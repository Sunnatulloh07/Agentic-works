"""Declared bounds of the control plane (§153).

``app/platform_api.py`` is the surface every tenant-facing write passes through,
and until §153 its limits lived as inline ``Field(ge=..., le=...)`` literals.
An inline literal is not addressable from a test, so widening one was silent:
the suite stayed green while the control plane began accepting wider autonomous
behaviour.  The bounds that matter most are the autonomy ceilings on
re-engagement, escalation and briefing, because those decide how often the
platform may contact a customer with no human in the loop.

Three layers are asserted here:

* ``DeclaredBoundTests`` pins each named constant to its literal value, so a
  rename cannot quietly move a number.
* the per-model tests drive the real Pydantic models at ``bound - 1``, ``bound``
  and ``bound + 1``, so a widened constant is caught even if the literal
  assertion is deleted along with it.
* ``NoInlineBoundTests`` asserts the structural invariant that no numeric
  literal survives inside any ``Field(...)`` in the module.  That is what makes
  the first two layers complete rather than merely representative: a new field
  added with ``le=500`` inline fails this test until it is named.
"""
import os
import re
import unittest
from pathlib import Path

# ``app.platform_api`` imports ``app.auth``, which validates the runtime config at
# import time and fails closed without an explicit environment.  setdefault (not
# assignment) so an operator running the suite under a real configuration keeps it.
os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

from pydantic import ValidationError

from app import platform_api as api


def accepts(model, **payload):
    """True when ``payload`` validates, False when it is refused."""
    try:
        model(**payload)
    except ValidationError:
        return False
    return True


class DeclaredBoundTests(unittest.TestCase):
    """Each constant is pinned to its literal value."""

    def test_text_ceilings(self):
        self.assertEqual(api.MIN_NON_EMPTY, 1)
        self.assertEqual(api.MAX_IDENTIFIER_CHARS, 128)
        self.assertEqual(api.MAX_KEY_CHARS, 256)
        self.assertEqual(api.MAX_EXTERNAL_ID_CHARS, 256)
        self.assertEqual(api.MAX_EVIDENCE_CHARS, 500)
        self.assertEqual(api.MAX_TEXT_CHARS, 4000)
        self.assertEqual(api.MAX_URL_CHARS, 2000)
        self.assertEqual(api.MAX_TITLE_CHARS, 200)
        self.assertEqual(api.MAX_MODEL_CHARS, 256)
        self.assertEqual(api.MAX_CONTENT_CHARS, 100_000)
        self.assertEqual(api.MAX_QUERY_CHARS, 500)
        self.assertEqual(api.MAX_QUESTION_CHARS, 2000)
        self.assertEqual(api.MAX_DISPLAY_NAME_CHARS, 256)
        self.assertEqual(api.MAX_CONTACT_VALUE_CHARS, 512)
        self.assertEqual(api.MAX_CHANNEL_CHARS, 32)
        self.assertEqual(api.MAX_BRIEFING_TITLE_CHARS, 120)
        self.assertEqual(api.MAX_SECTION_REF_CHARS, 64)
        self.assertEqual(api.MAX_KEYWORDS_PER_SECTION, 20)

    def test_vector_and_knowledge_ceilings(self):
        self.assertEqual(api.MAX_VECTOR_DIMENSION, 1024)
        self.assertEqual(api.MAX_VECTORS_PER_DOCUMENT, 224)
        self.assertEqual(api.MIN_KNOWLEDGE_RESULTS, 1)
        self.assertEqual(api.MAX_KNOWLEDGE_RESULTS, 5)
        self.assertEqual(api.DEFAULT_KNOWLEDGE_RESULTS, 4)
        self.assertEqual(api.MIN_DIMENSION, 0)
        self.assertEqual(api.MAX_DIMENSION, 1024)
        self.assertEqual(api.MIN_VERSION, 0)
        self.assertEqual(api.MIN_DELETE_VERSION, 1)
        self.assertEqual(api.MAX_VERSION, 2 ** 31)

    def test_money_ceilings(self):
        self.assertEqual(api.MIN_MONEY_MINOR, 0)
        self.assertEqual(api.MAX_MONEY_MINOR, 10 ** 15)
        self.assertEqual(api.MIN_LIMIT_MICRO, 1)
        self.assertEqual(api.MAX_LIMIT_MICRO, 10 ** 15)
        self.assertEqual(api.MIN_ACTUAL_MICRO, 0)
        self.assertEqual(api.MIN_MAX_INFLIGHT, 1)
        self.assertEqual(api.MAX_MAX_INFLIGHT, 100)
        self.assertEqual(api.DEFAULT_MAX_INFLIGHT, 4)

    def test_step_ceilings(self):
        self.assertEqual(api.MIN_SUBMIT_STEPS, 1)
        self.assertEqual(api.MAX_SUBMIT_STEPS, 20)
        self.assertEqual(api.MIN_SECTIONS, 1)
        self.assertEqual(api.MAX_SECTIONS, 12)
        self.assertEqual(api.DEFAULT_MAX_SECTIONS, 6)
        self.assertEqual(api.MIN_AGENT_RUN_STEPS, 1)
        self.assertEqual(api.MAX_AGENT_RUN_STEPS, 12)
        self.assertEqual(api.DEFAULT_AGENT_RUN_STEPS, 6)
        self.assertEqual(api.DEFAULT_REENGAGEMENT_STEPS, 4)
        self.assertEqual(api.DEFAULT_SUPERVISOR_STEPS, 6)
        self.assertEqual(api.MIN_SUPERVISOR_HOPS, 1)
        self.assertEqual(api.MAX_SUPERVISOR_HOPS, 3)

    def test_wall_clock_ceilings(self):
        self.assertEqual(api.MIN_MAX_SECONDS, 60)
        self.assertEqual(api.MAX_MAX_SECONDS, 86_400)
        self.assertEqual(api.DEFAULT_MAX_SECONDS, 1800)
        self.assertEqual(api.MIN_SCHEDULE_INTERVAL_SECONDS, 60)
        self.assertEqual(api.MAX_SCHEDULE_INTERVAL_SECONDS, 31_536_000)

    def test_reengagement_autonomy_ceilings(self):
        self.assertEqual(api.MIN_INACTIVE_MINUTES, 1)
        self.assertEqual(api.MAX_INACTIVE_MINUTES, 20_160)
        self.assertEqual(api.DEFAULT_INACTIVE_MINUTES, 120)
        self.assertEqual(api.MIN_COOLDOWN_SECONDS, 300)
        self.assertEqual(api.MAX_COOLDOWN_SECONDS, 2_592_000)
        self.assertEqual(api.DEFAULT_COOLDOWN_SECONDS, 86_400)
        self.assertEqual(api.MIN_REENGAGEMENT_ATTEMPTS, 1)
        self.assertEqual(api.MAX_REENGAGEMENT_ATTEMPTS, 10)
        self.assertEqual(api.DEFAULT_REENGAGEMENT_ATTEMPTS, 2)
        self.assertEqual(api.MIN_REENGAGEMENT_PER_CYCLE, 1)
        self.assertEqual(api.MAX_REENGAGEMENT_PER_CYCLE, 20)
        self.assertEqual(api.DEFAULT_REENGAGEMENT_PER_CYCLE, 5)
        self.assertEqual(api.MIN_CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(api.MAX_CYCLE_INTERVAL_SECONDS, 604_800)
        self.assertEqual(api.DEFAULT_CYCLE_INTERVAL_SECONDS, 3600)

    def test_escalation_autonomy_ceilings(self):
        self.assertEqual(api.MIN_ESCALATION_PER_CYCLE, 1)
        self.assertEqual(api.MAX_ESCALATION_PER_CYCLE, 50)
        self.assertEqual(api.DEFAULT_ESCALATION_PER_CYCLE, 10)
        self.assertEqual(api.MIN_MAX_AGE_DAYS, 1)
        self.assertEqual(api.MAX_MAX_AGE_DAYS, 365)
        self.assertEqual(api.DEFAULT_MAX_AGE_DAYS, 30)

    def test_briefing_ceilings(self):
        self.assertEqual(api.MIN_HOUR, 0)
        self.assertEqual(api.MAX_HOUR, 23)
        self.assertEqual(api.DEFAULT_HOUR, 8)
        self.assertEqual(api.MIN_MINUTE, 0)
        self.assertEqual(api.MAX_MINUTE, 59)
        self.assertEqual(api.DEFAULT_MINUTE, 0)
        self.assertEqual(api.MIN_TIMEZONE_OFFSET_MINUTES, -1440)
        self.assertEqual(api.MAX_TIMEZONE_OFFSET_MINUTES, 1440)
        self.assertEqual(api.DEFAULT_TIMEZONE_OFFSET_MINUTES, 300)
        self.assertEqual(api.MIN_BRIEFING_ROWS, 1)
        self.assertEqual(api.MAX_BRIEFING_ROWS, 50)
        self.assertEqual(api.DEFAULT_BRIEFING_ROWS, 5)


class SubmitBoundsTests(unittest.TestCase):
    def payload(self, **over):
        return {'agent': 'a', 'key': 'k', 'steps': [{}], **over}

    def test_step_count_is_one_to_twenty(self):
        self.assertFalse(accepts(api.Submit, **self.payload(steps=[])))
        self.assertTrue(accepts(api.Submit, **self.payload(
            steps=[{}] * api.MIN_SUBMIT_STEPS)))
        self.assertTrue(accepts(api.Submit, **self.payload(
            steps=[{}] * api.MAX_SUBMIT_STEPS)))
        self.assertFalse(accepts(api.Submit, **self.payload(
            steps=[{}] * (api.MAX_SUBMIT_STEPS + 1))))

    def test_agent_and_key_are_non_empty(self):
        self.assertFalse(accepts(api.Submit, **self.payload(agent='')))
        self.assertFalse(accepts(api.Submit, **self.payload(key='')))

    def test_agent_ceiling(self):
        self.assertTrue(accepts(api.Submit, **self.payload(
            agent='a' * api.MAX_IDENTIFIER_CHARS)))
        self.assertFalse(accepts(api.Submit, **self.payload(
            agent='a' * (api.MAX_IDENTIFIER_CHARS + 1))))

    def test_schedule_interval_floor_and_ceiling(self):
        base = self.payload()
        self.assertFalse(accepts(api.ScheduleRequest, **base,
                                 interval_seconds=api.MIN_SCHEDULE_INTERVAL_SECONDS - 1))
        self.assertTrue(accepts(api.ScheduleRequest, **base,
                                interval_seconds=api.MIN_SCHEDULE_INTERVAL_SECONDS))
        self.assertTrue(accepts(api.ScheduleRequest, **base,
                                interval_seconds=api.MAX_SCHEDULE_INTERVAL_SECONDS))
        self.assertFalse(accepts(api.ScheduleRequest, **base,
                                 interval_seconds=api.MAX_SCHEDULE_INTERVAL_SECONDS + 1))


class ReengagementPolicyBoundsTests(unittest.TestCase):
    """The ceilings that decide how hard automation may chase a lead."""

    def payload(self, **over):
        return {'agent': 'a', 'connection': 'c', **over}

    def test_inactive_minutes(self):
        for value, ok in [(api.MIN_INACTIVE_MINUTES - 1, False),
                          (api.MIN_INACTIVE_MINUTES, True),
                          (api.MAX_INACTIVE_MINUTES, True),
                          (api.MAX_INACTIVE_MINUTES + 1, False)]:
            with self.subTest(value=value):
                self.assertEqual(ok, accepts(api.ReengagementPolicy,
                                             **self.payload(inactive_minutes=value)))

    def test_cooldown_seconds(self):
        for value, ok in [(api.MIN_COOLDOWN_SECONDS - 1, False),
                          (api.MIN_COOLDOWN_SECONDS, True),
                          (api.MAX_COOLDOWN_SECONDS, True),
                          (api.MAX_COOLDOWN_SECONDS + 1, False)]:
            with self.subTest(value=value):
                self.assertEqual(ok, accepts(api.ReengagementPolicy,
                                             **self.payload(cooldown_seconds=value)))

    def test_max_attempts(self):
        for value, ok in [(api.MIN_REENGAGEMENT_ATTEMPTS - 1, False),
                          (api.MIN_REENGAGEMENT_ATTEMPTS, True),
                          (api.MAX_REENGAGEMENT_ATTEMPTS, True),
                          (api.MAX_REENGAGEMENT_ATTEMPTS + 1, False)]:
            with self.subTest(value=value):
                self.assertEqual(ok, accepts(api.ReengagementPolicy,
                                             **self.payload(max_attempts=value)))

    def test_max_per_cycle(self):
        for value, ok in [(api.MIN_REENGAGEMENT_PER_CYCLE - 1, False),
                          (api.MIN_REENGAGEMENT_PER_CYCLE, True),
                          (api.MAX_REENGAGEMENT_PER_CYCLE, True),
                          (api.MAX_REENGAGEMENT_PER_CYCLE + 1, False)]:
            with self.subTest(value=value):
                self.assertEqual(ok, accepts(api.ReengagementPolicy,
                                             **self.payload(max_per_cycle=value)))

    def test_cycle_interval_seconds(self):
        for value, ok in [(api.MIN_CYCLE_INTERVAL_SECONDS - 1, False),
                          (api.MIN_CYCLE_INTERVAL_SECONDS, True),
                          (api.MAX_CYCLE_INTERVAL_SECONDS, True),
                          (api.MAX_CYCLE_INTERVAL_SECONDS + 1, False)]:
            with self.subTest(value=value):
                self.assertEqual(ok, accepts(api.ReengagementPolicy,
                                             **self.payload(interval_seconds=value)))

    def test_steps_and_seconds(self):
        self.assertFalse(accepts(api.ReengagementPolicy,
                                 **self.payload(max_steps=api.MIN_AGENT_RUN_STEPS - 1)))
        self.assertTrue(accepts(api.ReengagementPolicy,
                                **self.payload(max_steps=api.MAX_AGENT_RUN_STEPS)))
        self.assertFalse(accepts(api.ReengagementPolicy,
                                 **self.payload(max_steps=api.MAX_AGENT_RUN_STEPS + 1)))
        self.assertFalse(accepts(api.ReengagementPolicy,
                                 **self.payload(max_seconds=api.MIN_MAX_SECONDS - 1)))
        self.assertTrue(accepts(api.ReengagementPolicy,
                                **self.payload(max_seconds=api.MAX_MAX_SECONDS)))
        self.assertFalse(accepts(api.ReengagementPolicy,
                                 **self.payload(max_seconds=api.MAX_MAX_SECONDS + 1)))

    def test_the_defaults_are_the_documented_ones(self):
        policy = api.ReengagementPolicy(**self.payload())
        self.assertEqual(policy.inactive_minutes, api.DEFAULT_INACTIVE_MINUTES)
        self.assertEqual(policy.cooldown_seconds, api.DEFAULT_COOLDOWN_SECONDS)
        self.assertEqual(policy.max_attempts, api.DEFAULT_REENGAGEMENT_ATTEMPTS)
        self.assertEqual(policy.max_per_cycle, api.DEFAULT_REENGAGEMENT_PER_CYCLE)
        self.assertEqual(policy.interval_seconds, api.DEFAULT_CYCLE_INTERVAL_SECONDS)
        self.assertEqual(policy.max_steps, api.DEFAULT_REENGAGEMENT_STEPS)
        self.assertEqual(policy.max_seconds, api.DEFAULT_MAX_SECONDS)

    def test_strict_ints_reject_floats_and_numeric_strings(self):
        self.assertFalse(accepts(api.ReengagementPolicy,
                                 **self.payload(max_attempts='2')))
        self.assertFalse(accepts(api.ReengagementPolicy,
                                 **self.payload(max_attempts=2.5)))


class EscalationScheduleBoundsTests(unittest.TestCase):
    def payload(self, **over):
        return {'agent': 'a', 'recipient': 'r', **over}

    def test_max_per_cycle(self):
        for value, ok in [(api.MIN_ESCALATION_PER_CYCLE - 1, False),
                          (api.MIN_ESCALATION_PER_CYCLE, True),
                          (api.MAX_ESCALATION_PER_CYCLE, True),
                          (api.MAX_ESCALATION_PER_CYCLE + 1, False)]:
            with self.subTest(value=value):
                self.assertEqual(ok, accepts(api.EscalationSchedule,
                                             **self.payload(max_per_cycle=value)))

    def test_max_age_days(self):
        for value, ok in [(api.MIN_MAX_AGE_DAYS - 1, False),
                          (api.MIN_MAX_AGE_DAYS, True),
                          (api.MAX_MAX_AGE_DAYS, True),
                          (api.MAX_MAX_AGE_DAYS + 1, False)]:
            with self.subTest(value=value):
                self.assertEqual(ok, accepts(api.EscalationSchedule,
                                             **self.payload(max_age_days=value)))

    def test_cooldown_and_interval(self):
        self.assertFalse(accepts(api.EscalationSchedule,
                                 **self.payload(cooldown_seconds=api.MIN_COOLDOWN_SECONDS - 1)))
        self.assertTrue(accepts(api.EscalationSchedule,
                                **self.payload(cooldown_seconds=api.MAX_COOLDOWN_SECONDS)))
        self.assertFalse(accepts(api.EscalationSchedule,
                                 **self.payload(cooldown_seconds=api.MAX_COOLDOWN_SECONDS + 1)))
        self.assertFalse(accepts(api.EscalationSchedule,
                                 **self.payload(interval_seconds=api.MIN_CYCLE_INTERVAL_SECONDS - 1)))
        self.assertTrue(accepts(api.EscalationSchedule,
                                **self.payload(interval_seconds=api.MAX_CYCLE_INTERVAL_SECONDS)))

    def test_title_ceiling(self):
        self.assertTrue(accepts(api.EscalationSchedule,
                                **self.payload(title='t' * api.MAX_BRIEFING_TITLE_CHARS)))
        self.assertFalse(accepts(api.EscalationSchedule,
                                 **self.payload(title='t' * (api.MAX_BRIEFING_TITLE_CHARS + 1))))


class BriefingScheduleBoundsTests(unittest.TestCase):
    def payload(self, **over):
        return {'agent': 'a', 'recipient': 'r', 'connection': 'c',
                'sections': [{}], **over}

    def test_section_count(self):
        base = {'agent': 'a', 'recipient': 'r', 'connection': 'c'}
        self.assertFalse(accepts(api.BriefingSchedule, **base, sections=[]))
        self.assertTrue(accepts(api.BriefingSchedule, **base,
                                sections=[{}] * api.MAX_SECTIONS))
        self.assertFalse(accepts(api.BriefingSchedule, **base,
                                 sections=[{}] * (api.MAX_SECTIONS + 1)))

    def test_hour_and_minute(self):
        for value, ok in [(api.MIN_HOUR - 1, False), (api.MIN_HOUR, True),
                          (api.MAX_HOUR, True), (api.MAX_HOUR + 1, False)]:
            with self.subTest(field='hour', value=value):
                self.assertEqual(ok, accepts(api.BriefingSchedule,
                                             **self.payload(hour=value)))
        for value, ok in [(api.MIN_MINUTE - 1, False), (api.MIN_MINUTE, True),
                          (api.MAX_MINUTE, True), (api.MAX_MINUTE + 1, False)]:
            with self.subTest(field='minute', value=value):
                self.assertEqual(ok, accepts(api.BriefingSchedule,
                                             **self.payload(minute=value)))

    def test_timezone_offset_is_symmetric(self):
        for value, ok in [(api.MIN_TIMEZONE_OFFSET_MINUTES - 1, False),
                          (api.MIN_TIMEZONE_OFFSET_MINUTES, True),
                          (api.MAX_TIMEZONE_OFFSET_MINUTES, True),
                          (api.MAX_TIMEZONE_OFFSET_MINUTES + 1, False)]:
            with self.subTest(value=value):
                self.assertEqual(ok, accepts(api.BriefingSchedule,
                                             **self.payload(timezone_offset_minutes=value)))

    def test_max_sections_and_max_rows(self):
        self.assertFalse(accepts(api.BriefingSchedule,
                                 **self.payload(max_sections=api.MIN_SECTIONS - 1)))
        self.assertTrue(accepts(api.BriefingSchedule,
                                **self.payload(max_sections=api.MAX_SECTIONS)))
        self.assertFalse(accepts(api.BriefingSchedule,
                                 **self.payload(max_sections=api.MAX_SECTIONS + 1)))
        self.assertFalse(accepts(api.BriefingSchedule,
                                 **self.payload(max_rows=api.MIN_BRIEFING_ROWS - 1)))
        self.assertTrue(accepts(api.BriefingSchedule,
                                **self.payload(max_rows=api.MAX_BRIEFING_ROWS)))
        self.assertFalse(accepts(api.BriefingSchedule,
                                 **self.payload(max_rows=api.MAX_BRIEFING_ROWS + 1)))

    def test_defaults(self):
        schedule = api.BriefingSchedule(**self.payload())
        self.assertEqual(schedule.hour, api.DEFAULT_HOUR)
        self.assertEqual(schedule.minute, api.DEFAULT_MINUTE)
        self.assertEqual(schedule.timezone_offset_minutes,
                         api.DEFAULT_TIMEZONE_OFFSET_MINUTES)
        self.assertEqual(schedule.max_sections, api.DEFAULT_MAX_SECTIONS)
        self.assertEqual(schedule.max_rows, api.DEFAULT_BRIEFING_ROWS)
        self.assertEqual(schedule.interval_seconds, api.DEFAULT_COOLDOWN_SECONDS)


class SupervisorRouteBoundsTests(unittest.TestCase):
    def test_hops_and_steps(self):
        self.assertFalse(accepts(api.SupervisorRoute, question='q',
                                 max_hops=api.MIN_SUPERVISOR_HOPS - 1))
        self.assertTrue(accepts(api.SupervisorRoute, question='q',
                                max_hops=api.MAX_SUPERVISOR_HOPS))
        self.assertFalse(accepts(api.SupervisorRoute, question='q',
                                 max_hops=api.MAX_SUPERVISOR_HOPS + 1))
        self.assertFalse(accepts(api.SupervisorRoute, question='q',
                                 max_steps=api.MIN_AGENT_RUN_STEPS - 1))
        self.assertTrue(accepts(api.SupervisorRoute, question='q',
                                max_steps=api.MAX_AGENT_RUN_STEPS))
        self.assertFalse(accepts(api.SupervisorRoute, question='q',
                                 max_steps=api.MAX_AGENT_RUN_STEPS + 1))

    def test_question_and_section_ceilings(self):
        self.assertFalse(accepts(api.SupervisorRoute, question=''))
        self.assertTrue(accepts(api.SupervisorRoute,
                                question='q' * api.MAX_QUESTION_CHARS))
        self.assertFalse(accepts(api.SupervisorRoute,
                                 question='q' * (api.MAX_QUESTION_CHARS + 1)))
        self.assertTrue(accepts(api.SupervisorRoute, question='q',
                                section='s' * api.MAX_SECTION_REF_CHARS))
        self.assertFalse(accepts(api.SupervisorRoute, question='q',
                                 section='s' * (api.MAX_SECTION_REF_CHARS + 1)))

    def test_keyword_ceiling(self):
        self.assertTrue(accepts(api.SupervisorSection, agent='a',
                                keywords=['k'] * api.MAX_KEYWORDS_PER_SECTION))
        self.assertFalse(accepts(api.SupervisorSection, agent='a',
                                 keywords=['k'] * (api.MAX_KEYWORDS_PER_SECTION + 1)))


class KnowledgeBoundsTests(unittest.TestCase):
    def test_result_limit(self):
        self.assertFalse(accepts(api.KnowledgeQuery, agent='a', query='q',
                                 limit=api.MIN_KNOWLEDGE_RESULTS - 1))
        self.assertTrue(accepts(api.KnowledgeQuery, agent='a', query='q',
                                limit=api.MAX_KNOWLEDGE_RESULTS))
        self.assertFalse(accepts(api.KnowledgeQuery, agent='a', query='q',
                                 limit=api.MAX_KNOWLEDGE_RESULTS + 1))

    def test_default_result_limit(self):
        self.assertEqual(api.DEFAULT_KNOWLEDGE_RESULTS,
                         api.KnowledgeQuery(agent='a', query='q').limit)

    def test_dimension_ceiling(self):
        self.assertFalse(accepts(api.KnowledgeCollection, id='c',
                                 dimension=api.MIN_DIMENSION - 1))
        self.assertTrue(accepts(api.KnowledgeCollection, id='c',
                                dimension=api.MAX_DIMENSION))
        self.assertFalse(accepts(api.KnowledgeCollection, id='c',
                                 dimension=api.MAX_DIMENSION + 1))

    def test_vector_count_ceiling(self):
        document = {'id': 'd', 'title': 't', 'content': 'c'}
        self.assertTrue(accepts(api.KnowledgeDocument, **document,
                                vectors=[[]] * api.MAX_VECTORS_PER_DOCUMENT))
        self.assertFalse(accepts(api.KnowledgeDocument, **document,
                                 vectors=[[]] * (api.MAX_VECTORS_PER_DOCUMENT + 1)))

    def test_query_vector_dimension_ceiling(self):
        self.assertTrue(accepts(api.KnowledgeQuery, agent='a', query='q',
                                query_vector=[0.0] * api.MAX_VECTOR_DIMENSION))
        self.assertFalse(accepts(api.KnowledgeQuery, agent='a', query='q',
                                 query_vector=[0.0] * (api.MAX_VECTOR_DIMENSION + 1)))

    def test_version_ceiling_is_exclusive(self):
        document = {'id': 'd', 'title': 't', 'content': 'c'}
        self.assertTrue(accepts(api.KnowledgeDocument, **document,
                                expected_version=api.MAX_VERSION - 1))
        self.assertFalse(accepts(api.KnowledgeDocument, **document,
                                 expected_version=api.MAX_VERSION))
        self.assertFalse(accepts(api.KnowledgeDelete,
                                 expected_version=api.MIN_DELETE_VERSION - 1))
        self.assertTrue(accepts(api.KnowledgeDelete,
                                expected_version=api.MIN_DELETE_VERSION))

    def test_content_and_url_ceilings(self):
        self.assertFalse(accepts(api.KnowledgeDocument, id='d', title='t',
                                 content='c' * (api.MAX_CONTENT_CHARS + 1)))
        self.assertTrue(accepts(api.KnowledgeDocument, id='d', title='t',
                                content='c' * api.MAX_CONTENT_CHARS))
        self.assertFalse(accepts(api.KnowledgeDocument, id='d', title='t',
                                 content='c',
                                 source_url='u' * (api.MAX_URL_CHARS + 1)))


class MoneyAndBudgetBoundsTests(unittest.TestCase):
    def test_order_total_ceiling(self):
        order = {'external_id': 'e'}
        self.assertFalse(accepts(api.CustomerOrder, **order,
                                 total_minor=api.MIN_MONEY_MINOR - 1))
        self.assertTrue(accepts(api.CustomerOrder, **order,
                                total_minor=api.MAX_MONEY_MINOR))
        self.assertFalse(accepts(api.CustomerOrder, **order,
                                 total_minor=api.MAX_MONEY_MINOR + 1))

    def test_budget_limit_floor_and_ceiling(self):
        self.assertFalse(accepts(api.BudgetSettings, currency='UZS',
                                 limit_micro=api.MIN_LIMIT_MICRO - 1))
        self.assertTrue(accepts(api.BudgetSettings, currency='UZS',
                                limit_micro=api.MAX_LIMIT_MICRO))
        self.assertFalse(accepts(api.BudgetSettings, currency='UZS',
                                 limit_micro=api.MAX_LIMIT_MICRO + 1))

    def test_max_inflight(self):
        for value, ok in [(api.MIN_MAX_INFLIGHT - 1, False),
                          (api.MIN_MAX_INFLIGHT, True),
                          (api.MAX_MAX_INFLIGHT, True),
                          (api.MAX_MAX_INFLIGHT + 1, False)]:
            with self.subTest(value=value):
                self.assertEqual(ok, accepts(api.BudgetSettings, currency='UZS',
                                             limit_micro=1, max_inflight=value))

    def test_settlement_may_be_zero_but_not_negative(self):
        self.assertTrue(accepts(api.BudgetSettlement, actual_micro=api.MIN_ACTUAL_MICRO,
                                evidence='e'))
        self.assertFalse(accepts(api.BudgetSettlement,
                                 actual_micro=api.MIN_ACTUAL_MICRO - 1, evidence='e'))

    def test_currency_must_be_three_uppercase_letters(self):
        for currency in ['UZS', 'USD']:
            with self.subTest(currency=currency):
                self.assertTrue(accepts(api.BudgetSettings, currency=currency,
                                        limit_micro=1))
        for currency in ['uzs', 'UZ', 'UZSX', '']:
            with self.subTest(currency=currency):
                self.assertFalse(accepts(api.BudgetSettings, currency=currency,
                                         limit_micro=1))


class AgentRunBoundsTests(unittest.TestCase):
    def payload(self, **over):
        return {'agent': 'a', 'key': 'k', 'text': 't', **over}

    def test_max_steps(self):
        for value, ok in [(api.MIN_AGENT_RUN_STEPS - 1, False),
                          (api.MIN_AGENT_RUN_STEPS, True),
                          (api.MAX_AGENT_RUN_STEPS, True),
                          (api.MAX_AGENT_RUN_STEPS + 1, False)]:
            with self.subTest(value=value):
                self.assertEqual(ok, accepts(api.AgentRunRequest,
                                             **self.payload(max_steps=value)))

    def test_max_seconds(self):
        for value, ok in [(api.MIN_MAX_SECONDS - 1, False),
                          (api.MIN_MAX_SECONDS, True),
                          (api.MAX_MAX_SECONDS, True),
                          (api.MAX_MAX_SECONDS + 1, False)]:
            with self.subTest(value=value):
                self.assertEqual(ok, accepts(api.AgentRunRequest,
                                             **self.payload(max_seconds=value)))

    def test_defaults(self):
        request = api.AgentRunRequest(**self.payload())
        self.assertEqual(request.max_steps, api.DEFAULT_AGENT_RUN_STEPS)
        self.assertEqual(request.max_seconds, api.DEFAULT_MAX_SECONDS)

    def test_text_ceiling(self):
        self.assertTrue(accepts(api.AgentRunRequest,
                                **self.payload(text='t' * api.MAX_TEXT_CHARS)))
        self.assertFalse(accepts(api.AgentRunRequest,
                                 **self.payload(text='t' * (api.MAX_TEXT_CHARS + 1))))


class NoInlineBoundTests(unittest.TestCase):
    """The invariant that makes the other classes complete, not representative."""

    MODULE = Path(api.__file__).resolve()

    def test_no_numeric_literal_survives_inside_a_field_call(self):
        with self.MODULE.open(encoding='utf-8', newline='') as handle:
            source = handle.read()
        offenders = [args for args in re.findall(r'Field\(([^)]*)\)', source)
                     if re.search(r'=\s*-?\d', args)]
        self.assertEqual(
            [], offenders,
            'name these bounds as module constants: ' + repr(offenders))

    def test_every_named_bound_is_referenced_by_the_module(self):
        """A constant nothing reads is documentation pretending to be a limit."""
        with self.MODULE.open(encoding='utf-8', newline='') as handle:
            source = handle.read()
        body = source.split('Declared bounds', 1)[1]
        for name, value in vars(api).items():
            if not name.startswith(('MAX_', 'MIN_', 'DEFAULT_', 'DEFAULT')):
                continue
            if not isinstance(value, int) or isinstance(value, bool):
                continue
            with self.subTest(constant=name):
                uses = len(re.findall(r'\b%s\b' % re.escape(name), body))
                self.assertGreater(uses, 1, f'{name} is declared but never used')


if __name__ == '__main__':
    unittest.main()
