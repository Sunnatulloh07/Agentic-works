"""Declared bounds and behaviour of the provider retry layer.

``retry.py`` is new, so it starts where the audited modules ended: every number is a
named constant and every one is asserted by its own value, not by its name. The
behavioural half is here too, because the numbers only mean something together with the
rule that reads them -- a correct ``MAX_ATTEMPTS`` is worthless if ``run`` also retries
a POST.
"""
import datetime
import unittest

from platform_runtime.retry import (BACKOFF_FACTOR, BASE_DELAY_SECONDS, MAX_ATTEMPTS,
                                    MAX_DELAY_SECONDS, MAX_JITTER_FRACTION,
                                    MAX_RETRY_AFTER_SECONDS, RETRYABLE_STATUS,
                                    ProviderBusy, RetriesExhausted, delay_for, delays,
                                    is_retryable, parse_retry_after, run)


class FakeClock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


def failing(status, retry_after=None, times=1):
    """An operation that fails ``times`` times and then returns ``'ok'``."""
    state = {'n': 0}

    def operation():
        state['n'] += 1
        if state['n'] <= times:
            raise ProviderBusy(status, retry_after)
        return 'ok'
    operation.calls = state
    return operation


class DeclaredValuesTests(unittest.TestCase):
    def test_declared_values_are_the_audited_ones(self):
        self.assertEqual(RETRYABLE_STATUS, frozenset({429, 500, 502, 503, 504}))
        self.assertEqual(MAX_ATTEMPTS, 4)
        self.assertEqual(BASE_DELAY_SECONDS, 0.5)
        self.assertEqual(BACKOFF_FACTOR, 2.0)
        self.assertEqual(MAX_DELAY_SECONDS, 30.0)
        self.assertEqual(MAX_RETRY_AFTER_SECONDS, 120.0)
        self.assertEqual(MAX_JITTER_FRACTION, 0.25)

    def test_only_429_and_5xx_are_retryable(self):
        for status in sorted(RETRYABLE_STATUS):
            with self.subTest(status=status):
                self.assertTrue(is_retryable(status))
        for status in (200, 201, 301, 400, 401, 403, 404, 409, 422, 501, 505):
            with self.subTest(status=status):
                self.assertFalse(is_retryable(status))

    def test_the_retryable_set_is_exactly_the_audited_five(self):
        self.assertEqual(5, len(RETRYABLE_STATUS))


class RetryAfterTests(unittest.TestCase):
    def test_delta_seconds_is_read_as_a_number(self):
        self.assertEqual(30.0, parse_retry_after('30'))
        self.assertEqual(30.0, parse_retry_after(30))
        self.assertEqual(0.5, parse_retry_after('0.5'))

    def test_http_date_is_converted_to_a_delta(self):
        now = datetime.datetime(2026, 10, 21, 7, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertAlmostEqual(120.0, parse_retry_after('Wed, 21 Oct 2026 07:02:00 GMT', now=now))
        # The cap applies to the date form as well, not only to delta-seconds.
        self.assertEqual(MAX_RETRY_AFTER_SECONDS,
                         parse_retry_after('Wed, 21 Oct 2026 09:00:00 GMT', now=now))

    def test_a_date_in_the_past_means_retry_now_not_an_error(self):
        now = datetime.datetime(2026, 10, 21, 7, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(0.0, parse_retry_after('Wed, 21 Oct 2026 06:00:00 GMT', now=now))

    def test_unusable_hints_yield_none_rather_than_a_guess(self):
        for value in (None, '', '   ', 'soon', 'Wed, 99 Xxx 2026', -1, '-5'):
            with self.subTest(value=value):
                self.assertIn(parse_retry_after(value), (None, 0.0))
        self.assertIsNone(parse_retry_after('soon'))
        self.assertIsNone(parse_retry_after(''))

    def test_the_hint_is_capped(self):
        self.assertEqual(120.0, parse_retry_after('3600'))
        self.assertEqual(MAX_RETRY_AFTER_SECONDS, parse_retry_after(10 ** 9))


class DelayTests(unittest.TestCase):
    def test_backoff_sequence_doubles_from_the_base(self):
        self.assertEqual([0.5, 1.0, 2.0], delays(4))
        self.assertEqual(0.5, delay_for(1))
        self.assertEqual(30.0, delay_for(100))          # clamped at MAX_DELAY_SECONDS

    def test_retry_after_wins_over_the_formula(self):
        self.assertEqual(7.0, delay_for(3, retry_after=7.0))
        self.assertEqual(MAX_RETRY_AFTER_SECONDS, delay_for(1, retry_after=10 ** 6))

    def test_jitter_never_pushes_a_wait_past_the_ceiling(self):
        self.assertLessEqual(delay_for(1, jitter=MAX_JITTER_FRACTION), MAX_RETRY_AFTER_SECONDS)
        self.assertLessEqual(delay_for(1, retry_after=MAX_RETRY_AFTER_SECONDS,
                                       jitter=MAX_JITTER_FRACTION), MAX_RETRY_AFTER_SECONDS)
        self.assertEqual(0.625, delay_for(1, jitter=0.25))

    def test_zero_attempts_is_refused(self):
        with self.assertRaises(ValueError):
            delays(0)
        with self.assertRaises(ValueError):
            delay_for(0)


class RunTests(unittest.TestCase):
    def test_a_write_is_attempted_once(self):
        """The whole point of the module: an unmarked operation is never repeated."""
        operation = failing(503, times=MAX_ATTEMPTS)
        clock = FakeClock()
        with self.assertRaises(RetriesExhausted):
            run(operation, sleep=clock.sleep, clock=clock)
        self.assertEqual(1, operation.calls['n'])
        self.assertEqual(0.0, clock.now)

    def test_an_idempotent_read_is_retried_until_it_succeeds(self):
        operation = failing(503, times=2)
        clock = FakeClock()
        self.assertEqual('ok', run(operation, idempotent=True, sleep=clock.sleep, clock=clock,
                                   rand=lambda: 0.0))
        self.assertEqual(3, operation.calls['n'])
        self.assertEqual(1.5, clock.now)                 # 0.5 then 1.0

    def test_attempts_are_capped(self):
        operation = failing(429, times=99)
        clock = FakeClock()
        with self.assertRaises(RetriesExhausted) as caught:
            run(operation, idempotent=True, sleep=clock.sleep, clock=clock, rand=lambda: 0.0)
        self.assertEqual(MAX_ATTEMPTS, operation.calls['n'])
        self.assertEqual(429, caught.exception.status)
        self.assertEqual(3.5, clock.now)                 # 0.5 + 1.0 + 2.0

    def test_an_unretryable_exception_propagates_untouched(self):
        def operation(): raise ValueError('our own bug')
        with self.assertRaises(ValueError):
            run(operation, idempotent=True, sleep=FakeClock().sleep, clock=FakeClock())

    def test_the_provider_hint_is_used_instead_of_the_formula(self):
        operation = failing(429, retry_after=5.0, times=1)
        clock = FakeClock()
        run(operation, idempotent=True, sleep=clock.sleep, clock=clock, rand=lambda: 0.0)
        self.assertEqual(5.0, clock.now)

    def test_a_single_attempt_never_sleeps(self):
        operation = failing(500, times=1)
        clock = FakeClock()
        with self.assertRaises(RetriesExhausted):
            run(operation, idempotent=True, attempts=1, sleep=clock.sleep, clock=clock)
        self.assertEqual(1, operation.calls['n'])
        self.assertEqual(0.0, clock.now)

    def test_the_wall_clock_ceiling_stops_the_run_instead_of_sleeping(self):
        """A provider hint beyond the ceiling must end the operation, not extend it."""
        operation = failing(503, retry_after=MAX_RETRY_AFTER_SECONDS, times=99)
        clock = FakeClock()
        with self.assertRaises(RetriesExhausted):
            run(operation, idempotent=True, sleep=clock.sleep, clock=clock, rand=lambda: 0.0)
        self.assertEqual(2, operation.calls['n'])        # one attempt, one wait, then stop
        self.assertEqual(MAX_RETRY_AFTER_SECONDS, clock.now)

    def test_exhaustion_reports_the_attempts_it_made(self):
        operation = failing(503, times=99)
        clock = FakeClock()
        with self.assertRaises(RetriesExhausted) as caught:
            run(operation, idempotent=True, attempts=3, sleep=clock.sleep, clock=clock, rand=lambda: 0.0)
        self.assertEqual(3, caught.exception.attempts)
        self.assertEqual(3, operation.calls['n'])


if __name__ == '__main__':
    unittest.main()
