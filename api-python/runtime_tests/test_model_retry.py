"""Bounded model-call retries, a model-only timeout, and what a failed call costs.

Before this file a model call was one attempt on the general 25 s HTTP timeout
(tools.PROVIDER_TIMEOUT_SECONDS) -- tight for a thinking model -- and a 429 or a
529 ended the customer's turn on the spot. Every failed METERED call became
``uncertain`` and kept its parallel-call slot until an owner reconciled it by
hand, so after ``max_inflight`` failures every later turn escalated.

The rules pinned here:

* Retry at most twice, with exponential backoff and jitter, only for 408, 409,
  429, 5xx, a request that never left, a timeout or a reset. Every other 4xx
  and every unclassified error is raised on the first attempt.
* ``retry-after`` is honoured; a provider asking for longer than the wait
  ceiling is not retried at all.
* The whole call, attempts and waits included, fits MODEL_CALL_DEADLINE_SECONDS,
  and the agent loop's planner lease is longer than that deadline.
* Metering is per ATTEMPT. A request that never left, or that the provider
  answered with an HTTP error status, is released (settled at zero); only an
  unknown outcome -- sent, no complete answer -- stays ``uncertain``. A retry
  therefore never charges twice for one decision.

No network: urllib is patched or the transport is a fake.
"""
import datetime
import http.client
import io
import json
import socket
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

from platform_runtime import model_transport
from platform_runtime.agent_loop import PLANNER_LEASE_SECONDS
from platform_runtime.agent_planner import ResultPlanner
from platform_runtime.engine import Conflict, Engine, RateLimited
from platform_runtime.model_transport import (
    DEFAULT_MODEL_TIMEOUT_SECONDS, MAX_MODEL_ATTEMPTS, MAX_MODEL_TIMEOUT_SECONDS,
    MAX_RETRY_WAIT_SECONDS, MIN_MODEL_TIMEOUT_SECONDS, MODEL_CALL_DEADLINE_SECONDS,
    NOT_SENT, REJECTED, UNKNOWN, LocalRequestRejected, ModelRequestFailed,
    classify, model_timeout, transport_for, with_retries)
from platform_runtime.tools import PROVIDER_TIMEOUT_SECONDS, build_registry
from platform_runtime.usage_budget import UsageBudget, metered_completion

URL = 'https://model.example/v1/chat/completions?leak=unit-secret-url'
DECISION = '{"action":"ask","question":"Qaysi mahsulot?"}'


def http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers['retry-after'] = str(retry_after)
    return urllib.error.HTTPError(URL, code, 'refused', headers, io.BytesIO(b'{}'))


def reply(prompt=100, completion=20):
    return {'usage': {'prompt_tokens': prompt, 'completion_tokens': completion},
            'choices': [{'finish_reason': 'stop', 'message': {'content': DECISION}}]}


class Clock:
    """A monotonic clock the fake sleep and the fake transport both advance."""

    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class Script:
    """A transport that plays a list of outcomes: an exception or a response."""

    def __init__(self, outcomes, clock=None, seconds=0.0):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.clock, self.seconds = clock, seconds

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.clock is not None:
            self.clock.now += self.seconds
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def retry(script, clock=None, timeout=DEFAULT_MODEL_TIMEOUT_SECONDS, jitter=lambda: 0.0,
          deadline=MODEL_CALL_DEADLINE_SECONDS):
    clock = clock or Clock()
    return with_retries(lambda attempt: script(), timeout=timeout, deadline=deadline,
                        sleep=clock.sleep, clock=clock, jitter=jitter)


class ClassifyTests(unittest.TestCase):
    def test_an_http_status_is_a_rejection_that_carries_status_and_retry_after(self):
        failure = classify(http_error(429, 3))
        self.assertEqual((REJECTED, 429, 3.0), (failure.outcome, failure.status, failure.retry_after))
        for code in (400, 500, 529):
            with self.subTest(code=code):
                self.assertEqual((REJECTED, code), (classify(http_error(code)).outcome, classify(http_error(code)).status))

    def test_a_request_that_never_left_is_not_sent(self):
        for reason in (ConnectionRefusedError(111, 'refused'), socket.gaierror(-2, 'dns'),
                       socket.timeout('connect timed out')):
            with self.subTest(reason=reason):
                self.assertEqual(NOT_SENT, classify(urllib.error.URLError(reason)).outcome)

    def test_a_failure_after_the_request_left_is_unknown(self):
        for exc in (TimeoutError('read timed out'), socket.timeout('read'), ConnectionResetError(104, 'reset'),
                    http.client.RemoteDisconnected('closed'), http.client.IncompleteRead(b'')):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(UNKNOWN, classify(exc).outcome)

    def test_the_loopback_rejection_keeps_its_status(self):
        failure = classify(LocalRequestRejected(503))
        self.assertEqual((REJECTED, 503), (failure.outcome, failure.status))

    def test_anything_else_is_unclassified(self):
        for exc in (RuntimeError('x'), ValueError('bad json'), KeyError('usage'), Conflict('replay')):
            with self.subTest(exc=type(exc).__name__):
                self.assertIsNone(classify(exc))

    def test_a_classified_failure_never_quotes_the_url(self):
        for exc in (http_error(500), urllib.error.URLError(URL), TimeoutError(URL)):
            with self.subTest(exc=type(exc).__name__):
                failure = classify(exc)
                self.assertIsInstance(failure, RuntimeError)
                self.assertNotIn('unit-secret-url', str(failure))
                self.assertNotIn('model.example', str(failure))


class RetryPolicyTests(unittest.TestCase):
    def test_a_rate_limit_is_retried_then_succeeds(self):
        script = Script([http_error(429), {'ok': 1}])
        self.assertEqual({'ok': 1}, retry(script))
        self.assertEqual(2, script.calls)

    def test_retryable_statuses_and_transport_failures(self):
        for failure in (http_error(408), http_error(409), http_error(429), http_error(500),
                        http_error(503), http_error(529), urllib.error.URLError(ConnectionRefusedError()),
                        TimeoutError('read'), ConnectionResetError(104, 'reset')):
            with self.subTest(failure=failure):
                script = Script([failure, {'ok': 1}])
                self.assertEqual({'ok': 1}, retry(script))
                self.assertEqual(2, script.calls)

    def test_other_client_errors_are_raised_on_the_first_attempt(self):
        for code in (400, 401, 402, 403, 404, 413, 422):
            with self.subTest(code=code):
                script = Script([http_error(code), {'ok': 1}])
                with self.assertRaises(ModelRequestFailed) as caught:
                    retry(script)
                self.assertEqual(1, script.calls)
                self.assertEqual(code, caught.exception.status)

    def test_at_most_two_retries(self):
        self.assertEqual(3, MAX_MODEL_ATTEMPTS)
        script = Script([http_error(503)] * 5)
        with self.assertRaises(ModelRequestFailed):
            retry(script)
        self.assertEqual(MAX_MODEL_ATTEMPTS, script.calls)

    def test_the_final_error_is_sanitized(self):
        with self.assertRaises(ModelRequestFailed) as caught:
            retry(Script([http_error(503)] * 3))
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn('unit-secret-url', str(caught.exception))

    def test_an_unclassified_error_propagates_unchanged_and_is_not_retried(self):
        script = Script([RuntimeError('unit-provider-failure'), {'ok': 1}])
        with self.assertRaises(RuntimeError) as caught:
            retry(script)
        self.assertEqual('unit-provider-failure', str(caught.exception))
        self.assertEqual(1, script.calls)

    def test_backoff_is_exponential_with_bounded_jitter(self):
        clock = Clock()
        retry(Script([http_error(503), http_error(503), {'ok': 1}]), clock=clock, jitter=lambda: 0.0)
        self.assertEqual([1.0, 2.0], clock.waits)
        clock = Clock()
        retry(Script([http_error(503), http_error(503), {'ok': 1}]), clock=clock, jitter=lambda: 1.0)
        self.assertEqual([0.75, 1.5], clock.waits)

    def test_retry_after_is_respected(self):
        clock = Clock()
        retry(Script([http_error(429, 5), {'ok': 1}]), clock=clock)
        self.assertEqual([5.0], clock.waits)

    def test_a_retry_after_beyond_the_wait_ceiling_is_not_retried(self):
        script = Script([http_error(429, MAX_RETRY_WAIT_SECONDS + 1), {'ok': 1}])
        with self.assertRaises(ModelRequestFailed):
            retry(script)
        self.assertEqual(1, script.calls)

    def test_no_attempt_starts_that_could_overrun_the_deadline(self):
        # Two 60 s timeouts plus a wait fit 150 s; a third attempt would not.
        clock = Clock()
        script = Script([TimeoutError('read')] * 3, clock=clock, seconds=60)
        with self.assertRaises(ModelRequestFailed):
            retry(script, clock=clock, timeout=60)
        self.assertEqual(2, script.calls)
        self.assertLessEqual(clock.now, MODEL_CALL_DEADLINE_SECONDS)

    def test_the_worst_case_call_fits_the_deadline_for_every_allowed_timeout(self):
        for timeout in (MIN_MODEL_TIMEOUT_SECONDS, DEFAULT_MODEL_TIMEOUT_SECONDS, MAX_MODEL_TIMEOUT_SECONDS):
            with self.subTest(timeout=timeout):
                clock = Clock()
                script = Script([TimeoutError('read')] * 3, clock=clock, seconds=timeout)
                with self.assertRaises(ModelRequestFailed):
                    retry(script, clock=clock, timeout=timeout, jitter=lambda: 0.0)
                self.assertLessEqual(clock.now, MODEL_CALL_DEADLINE_SECONDS)


class TimeoutTests(unittest.TestCase):
    def test_the_model_timeout_is_its_own_setting(self):
        self.assertEqual(60, DEFAULT_MODEL_TIMEOUT_SECONDS)
        self.assertEqual((10, 120), (MIN_MODEL_TIMEOUT_SECONDS, MAX_MODEL_TIMEOUT_SECONDS))
        self.assertEqual(25, PROVIDER_TIMEOUT_SECONDS)  # sends keep the general timeout
        self.assertEqual(60, model_timeout({}))
        for value in (10, 45, 120):
            self.assertEqual(value, model_timeout({'timeout_seconds': value}))
        for value in (9, 121, 0, True, 60.0, '60', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                model_timeout({'timeout_seconds': value})

    def opener(self):
        response = MagicMock(); response.status = 200
        response.read.return_value = json.dumps(reply()).encode()
        opener = MagicMock(); opener.open.return_value.__enter__.return_value = response
        return opener

    def test_the_cloud_transport_uses_the_model_timeout(self):
        opener = self.opener()
        with patch('urllib.request.build_opener', return_value=opener):
            transport_for({}, timeout=45)('https://model.example/v1/chat/completions', {}, {})
        self.assertEqual(45, opener.open.call_args.kwargs['timeout'])

    def test_the_loopback_transport_uses_the_model_timeout(self):
        opener = self.opener()
        with patch('urllib.request.build_opener', return_value=opener):
            transport_for({'provider_mode': 'local_loopback'}, timeout=45)(
                'http://127.0.0.1:11434/v1/chat/completions', {}, {})
        self.assertEqual(45, opener.open.call_args.kwargs['timeout'])

    def test_the_planner_sends_with_the_configured_model_timeout(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        engine = Engine(Path(tmp.name) / 'p.db', build_registry(),
                        lambda t, a: {'tools': ['reports.summary'], 'ladder': 'autonomous'})
        cfg = {'llm': {'model': 'unit-model', 'key_env': 'UNIT_KEY', 'timeout_seconds': 45,
                       'base_url': 'https://model.example/v1', 'agent_loop_enabled': True}}
        opener = self.opener()
        with patch('platform_runtime.agent_planner.config', return_value=cfg), \
                patch('platform_runtime.agent_planner.secret', return_value='unit-placeholder'), \
                patch('urllib.request.build_opener', return_value=opener):
            ResultPlanner(engine)('tenant', {'run_id': 'r', 'agent': 'ops', 'input': 'x', 'call_index': 1,
                                             'remaining_steps': 1, 'remaining_calls': 1, 'observations': []})
        self.assertEqual(45, opener.open.call_args.kwargs['timeout'])

    def test_a_loopback_failure_keeps_its_billing_class(self):
        cfg = {'provider_mode': 'local_loopback'}
        for failure, outcome in ((urllib.error.URLError(ConnectionRefusedError()), NOT_SENT),
                                 (TimeoutError('read'), UNKNOWN)):
            with self.subTest(outcome=outcome):
                opener = MagicMock(); opener.open.side_effect = failure
                with patch('urllib.request.build_opener', return_value=opener):
                    with self.assertRaises(RuntimeError) as caught:
                        transport_for(cfg)('http://127.0.0.1:11434/v1/chat/completions', {}, {})
                self.assertEqual(outcome, classify(caught.exception).outcome)
                self.assertIsNone(caught.exception.__cause__)


class LeaseTests(unittest.TestCase):
    def test_the_planner_lease_outlives_the_worst_case_model_call(self):
        # A result that arrives after the lease is discarded, so the lease must
        # cover every attempt and wait, plus connect time and the commit.
        self.assertGreaterEqual(MODEL_CALL_DEADLINE_SECONDS, MAX_MODEL_TIMEOUT_SECONDS)
        self.assertGreaterEqual(PLANNER_LEASE_SECONDS, MODEL_CALL_DEADLINE_SECONDS + 30)

    def test_the_one_shot_planner_fits_the_event_lease(self):
        # Engine.process_event leases an event for 120 s while it plans.
        from platform_runtime.llm import EVENT_PLAN_DEADLINE_SECONDS
        self.assertLessEqual(EVENT_PLAN_DEADLINE_SECONDS + 20, 120)


class MeteredRetryTests(unittest.TestCase):
    """Real SQLite ledger. One decision, several attempts, one honest bill."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.now = [datetime.datetime(2026, 9, 14, tzinfo=datetime.timezone.utc).timestamp()]
        self.e = Engine(Path(tmp.name) / 'meter.db', build_registry(), lambda t, a: {},
                        clock=lambda: self.now[0])
        self.b = UsageBudget(self.e); self.b.configure('a', 'owner', 'USD', 10 ** 9, 4)
        self.cfg = {'usage_budget': {'currency': 'USD', 'input_micro_per_million': 1000000,
                                     'output_micro_per_million': 2000000}}
        self.clock = Clock()

    def invoke(self, script, key='agent:run:1'):
        return metered_completion(self.e, 'a', self.cfg, script, 'https://model.example/v1/chat/completions',
                                  {'max_tokens': 100, 'messages': []}, {'Authorization': 'test-only'}, key,
                                  sleep=self.clock.sleep, clock=self.clock)

    def ledger(self):
        with self.e.read() as db:
            return [(r['request_key'], r['status'], r['actual_micro']) for r in db.execute(
                'SELECT request_key,status,actual_micro FROM p_budget_reservations ORDER BY created,rowid')]

    def test_a_retried_rate_limit_is_charged_once(self):
        script = Script([http_error(429), reply()])
        self.invoke(script)
        self.assertEqual(2, script.calls)
        self.assertEqual(140, self.b.summary('a')['spent_micro'])
        self.assertEqual(0, self.b.summary('a')['reserved_micro'])
        self.assertEqual([], self.b.pending('a'))
        self.assertEqual([('agent:run:1', 'released', 0), ('agent:run:1#retry1', 'settled', 140)], self.ledger())

    def test_an_http_error_or_unsent_request_releases_its_reservation(self):
        for failure in (http_error(400), urllib.error.URLError(ConnectionRefusedError())):
            with self.subTest(failure=type(failure).__name__):
                key = 'k-' + type(failure).__name__
                with self.assertRaises(RuntimeError):
                    self.invoke(Script([failure] * 3), key)
        summary = self.b.summary('a')
        self.assertEqual((0, 0, 0), (summary['spent_micro'], summary['reserved_micro'], summary['inflight']))
        self.assertTrue(all(status == 'released' for _, status, _ in self.ledger()))

    def test_repeated_server_errors_release_every_attempt(self):
        script = Script([http_error(503)] * 3)
        with self.assertRaises(ModelRequestFailed):
            self.invoke(script)
        self.assertEqual(3, script.calls)
        self.assertEqual(['released'] * 3, [status for _, status, _ in self.ledger()])
        self.assertEqual(0, self.b.summary('a')['inflight'])

    def test_only_an_unknown_outcome_stays_uncertain(self):
        script = Script([TimeoutError('read'), reply()])
        self.invoke(script)
        self.assertEqual([('agent:run:1', 'uncertain', None), ('agent:run:1#retry1', 'settled', 140)],
                         self.ledger())
        self.assertGreater(self.b.summary('a')['reserved_micro'], 0)  # the lost attempt is still held

    def test_a_missing_usage_receipt_is_uncertain_and_not_retried(self):
        script = Script([{'choices': []}, reply()])
        with self.assertRaises(RuntimeError):
            self.invoke(script)
        self.assertEqual(1, script.calls)
        self.assertEqual('uncertain', self.b.pending('a')[0]['status'])

    def test_a_replayed_request_key_still_never_reaches_the_provider(self):
        self.invoke(Script([reply()]))
        script = Script([reply()])
        with self.assertRaises(Conflict):
            self.invoke(script)
        self.assertEqual(0, script.calls)

    def test_a_retry_the_budget_refuses_is_not_sent(self):
        self.b.configure('a', 'owner', 'USD', 10 ** 9, 1)
        script = Script([TimeoutError('read'), reply()])
        with self.assertRaises(RateLimited):
            self.invoke(script)
        self.assertEqual(1, script.calls)

    def test_unmetered_calls_are_retried_too(self):
        script = Script([http_error(529), {'ok': 1}])
        self.assertEqual({'ok': 1}, metered_completion(
            self.e, 'a', {}, script, 'https://model.example/v1', {'max_tokens': 1}, {},
            sleep=self.clock.sleep, clock=self.clock))
        self.assertEqual(2, script.calls)


if __name__ == '__main__':
    unittest.main()
