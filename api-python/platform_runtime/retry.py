"""Bounded retry for provider rate limits and transient failures.

The inventory recorded this as absent: a provider answering 429 or 503 ended the step,
even though the same request would have succeeded a second later. This module adds the
retry, and the shape of it matters more than the presence of it.

Four decisions, each made because the alternative is a worse system:

**Reads may repeat; writes may not.** A retry is a second request. For a GET that is
free; for a POST that sends a message or posts a ledger entry it is a second effect, and
this platform already treats "the write may have landed" as an ``uncertain`` outcome
that requires a human. So ``run`` refuses to retry unless the caller passes
``idempotent=True``, and the default is the safe one. A caller that wants a retry for a
write must first make the write idempotent at the provider (an idempotency key), which
is a decision this module cannot make for it.

**Only 429 and 5xx are transient.** A 400, 401, 403 or 404 will answer the same way
forever; retrying it burns quota and delays the error. Retrying a 401 would also hide
the credential failure the operator needs to see.

**``Retry-After`` is honoured but capped.** A provider that says "come back in an hour"
must not turn a worker into an hour-long sleep, and a hostile or broken provider must
not be able to stall the platform by asking for an enormous delay. The header is parsed
in both forms the specification allows (delta-seconds and HTTP-date) and clamped.

**Every bound is named and pinned.** This module is new, so it starts where the audited
modules ended: constants rather than literals, with a test that asserts each number.
"""
from __future__ import annotations

import datetime
import email.utils
import random

# Provider answers that mean "the same request may work later". Everything else is
# treated as final, including every other 4xx.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

MAX_ATTEMPTS = 4
BASE_DELAY_SECONDS = 0.5
BACKOFF_FACTOR = 2.0
MAX_DELAY_SECONDS = 30.0
MAX_RETRY_AFTER_SECONDS = 120.0
# Fraction of a delay that may be added as jitter, so a fleet of workers retrying the
# same provider does not synchronise into a thundering herd.
MAX_JITTER_FRACTION = 0.25


class ProviderBusy(RuntimeError):
    """A provider answer that ``run`` may retry, carrying its own retry hint."""

    def __init__(self, status: int, retry_after: float | None = None, detail: str = ''):
        super().__init__(detail or ('provider status %d' % status))
        self.status = status
        self.retry_after = retry_after


class RetriesExhausted(RuntimeError):
    """Every attempt returned a retryable answer. Carries the last one for the caller."""

    def __init__(self, attempts: int, last: ProviderBusy):
        super().__init__('provider still busy after %d attempts' % attempts)
        self.attempts = attempts
        self.status = last.status
        self.retry_after = last.retry_after


def is_retryable(status: int) -> bool:
    return status in RETRYABLE_STATUS


def parse_retry_after(value, now=None) -> float | None:
    """Return a bounded delay in seconds, or ``None`` when there is no usable hint.

    Both forms in RFC 9110 are accepted: delta-seconds (``'30'``) and an HTTP-date
    (``'Wed, 21 Oct 2026 07:28:00 GMT'``). Anything else -- including a negative or
    non-numeric value -- yields ``None`` so the caller falls back to exponential
    backoff rather than trusting a number it could not parse.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            seconds = float(text)
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(text)
            except (TypeError, ValueError):
                return None
            if when is None:
                return None
            if when.tzinfo is None:
                when = when.replace(tzinfo=datetime.timezone.utc)
            reference = datetime.datetime.now(datetime.timezone.utc) if now is None else now
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=datetime.timezone.utc)
            seconds = (when - reference).total_seconds()
    if seconds < 0:
        # A date already in the past means "retry now", which is 0, not an error.
        return 0.0
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def delay_for(attempt: int, retry_after: float | None = None, jitter: float = 0.0) -> float:
    """Seconds to wait before ``attempt`` (1-based: the wait AFTER the first failure).

    ``retry_after`` wins over exponential backoff when present, because the provider
    knows its own recovery time and the formula does not. Both paths are clamped, so
    no single wait can exceed ``MAX_RETRY_AFTER_SECONDS``.
    """
    if attempt < 1:
        raise ValueError('attempt is 1-based')
    if retry_after is not None:
        base = min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
    else:
        base = min(BASE_DELAY_SECONDS * (BACKOFF_FACTOR ** (attempt - 1)), MAX_DELAY_SECONDS)
    if jitter <= 0:
        return base
    return min(base + base * jitter, MAX_RETRY_AFTER_SECONDS)


def delays(attempts: int = MAX_ATTEMPTS) -> list:
    """The deterministic backoff sequence, for tests and for documentation."""
    if attempts < 1:
        raise ValueError('attempts must be at least 1')
    return [delay_for(n) for n in range(1, attempts)]


def run(operation, *, idempotent: bool = False, attempts: int = MAX_ATTEMPTS,
        sleep=None, clock=None, rand=None, retry_after_of=None):
    """Call ``operation`` until it succeeds or the attempts run out.

    ``operation`` is called with no arguments. It signals a retryable condition by
    raising :class:`ProviderBusy`; any other exception propagates immediately, because
    this module retries a *provider's* answer, not a bug in our own code.

    ``idempotent=False`` means one attempt, full stop. That is not a conservative
    default to be worked around: it is the correct behaviour for a request whose repeat
    would be a second side effect, and the parameter exists so the decision is visible
    at each call site rather than hidden in a decorator.
    """
    if attempts < 1:
        raise ValueError('attempts must be at least 1')
    if not idempotent:
        attempts = 1
    if sleep is None:
        import time
        sleep = time.sleep
    if clock is None:
        import time
        clock = time.monotonic
    if rand is None:
        rand = random.random
    hint = retry_after_of
    started = clock()
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except ProviderBusy as busy:
            last = busy
            if attempt == attempts:
                break
            hint_value = busy.retry_after
            if hint_value is None and hint is not None:
                hint_value = hint(busy)
            wait = delay_for(attempt, parse_retry_after(hint_value),
                             jitter=rand() * MAX_JITTER_FRACTION)
            if clock() - started + wait > MAX_RETRY_AFTER_SECONDS:
                # The next wait would push the whole operation past the wall-clock
                # ceiling. Stop and report, rather than sleeping for a while and then
                # failing anyway -- a worker that blocks for two minutes to deliver an
                # error is worse than one that delivers it now.
                break
            sleep(wait)
    raise RetriesExhausted(attempts, last)
