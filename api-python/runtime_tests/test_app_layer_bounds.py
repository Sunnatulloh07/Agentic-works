"""Declared bounds of the application layer (§155).

The runtime modules were audited first (§151–§153) because they hold the business
logic.  ``app/`` was left for last and turned out to hold a different kind of gap:
its bounds were not merely *unnamed*, several were pinned only in ``tests/`` — a
legacy suite that **no gate runs** and that is 35 tests red because the API it
exercises was deliberately retired (the routes return ``410 Gone``).  A pin that
lives in a suite nobody runs is not a pin.

So this module is where the app layer's limits are actually addressed by value.
It covers three shapes, and the split is deliberate:

* **named constants** (``MAX_PERSONA_CHARS``, ``MAX_BODY``, ``MAX_BYTES``,
  ``MAX_QUEUE``, ``MAX_RESULTS``, ``TTL_SECONDS``) — asserted literally *and*
  behaviourally.
* **inline literals promoted in this phase** (the session token lifetime ``900``,
  the throttle's ``20``/``900``, the invitation window's ``300``/``604800``, the
  daily limit's ``1000``/``86400``/``0.8``) — these had no name at all, so nothing
  could reach them.
* **regular-expression ceilings** (the e-mail local part, the workspace slug) —
  pinned behaviourally only.  Naming a bound inside a regex would mean building
  the pattern from an f-string, which trades readability for a name nobody reads;
  feeding the pattern one character past its ceiling proves the same thing.

Two guards could not be reached from a unit test without standing up a WebSocket
or a signed admin request, and they are pinned by their comparison site instead.
That is weaker than a behavioural test and it is labelled as such rather than
quietly counted as equivalent.
"""

import importlib
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

from app import auth, identity_store, limits, packs, runner_ws, telegram, trace
from app.identity_store import AuthRateLimited, IdentityError
from app.storage import reset

APP = Path(auth.__file__).resolve().parent


def source(module):
    with Path(module.__file__).resolve().open(encoding='utf-8', newline='') as handle:
        return handle.read()


class DeclaredBoundTests(unittest.TestCase):
    """Every promoted number, pinned to its literal value."""

    def test_token_lifetimes(self):
        self.assertEqual(auth.TTL_SECONDS, 24 * 3600)
        self.assertEqual(auth.MIN_TOKEN_TTL_SECONDS, 1)
        self.assertEqual(auth.SESSION_TOKEN_TTL_SECONDS, 900)

    def test_text_ceilings(self):
        self.assertEqual(identity_store.MAX_EMAIL_CHARS, 320)
        self.assertEqual(identity_store.MIN_PASSWORD_CHARS, 12)
        self.assertEqual(identity_store.MAX_PASSWORD_CHARS, 256)
        self.assertEqual(identity_store.MIN_CANDIDATE_PASSWORD_CHARS, 1)
        self.assertEqual(identity_store.MIN_TOKEN_CHARS, 20)
        self.assertEqual(identity_store.MAX_TOKEN_CHARS, 256)

    def test_key_derivation_parameters(self):
        """Security bounds: lowering the cost factor weakens every stored password."""
        self.assertEqual(identity_store.SCRYPT_N, 16384)
        self.assertEqual(identity_store.SCRYPT_R, 8)
        self.assertEqual(identity_store.SCRYPT_P, 1)
        self.assertEqual(identity_store.SCRYPT_DKLEN, 32)
        self.assertEqual(identity_store.SALT_BYTES, 16)

    def test_the_cost_factor_is_a_power_of_two_and_not_trivial(self):
        """scrypt requires n to be a power of two; a low n is a brute-force gift."""
        self.assertGreaterEqual(identity_store.SCRYPT_N, 16384)
        self.assertEqual(0, identity_store.SCRYPT_N & (identity_store.SCRYPT_N - 1))

    def test_the_stored_hash_format_matches_the_declared_parameters(self):
        """The encoded string is what verification reads back, so it must agree."""
        encoded = identity_store._password_hash('correct horse battery')
        algorithm, n, r, p, salt, expected = encoded.split('$')
        self.assertEqual('scrypt', algorithm)
        self.assertEqual(str(identity_store.SCRYPT_N), n)
        self.assertEqual(str(identity_store.SCRYPT_R), r)
        self.assertEqual(str(identity_store.SCRYPT_P), p)
        self.assertEqual(identity_store.SALT_BYTES * 2, len(salt))
        self.assertEqual(identity_store.SCRYPT_DKLEN * 2, len(expected))

    def test_the_dummy_hash_uses_the_same_parameters_as_a_real_one(self):
        """It exists to make an unknown user cost the same as a known one."""
        self.assertEqual(identity_store.DUMMY.split('$')[:4],
                         identity_store._password_hash('x' * identity_store.MIN_PASSWORD_CHARS)
                         .split('$')[:4])

    def test_promoted_bounds(self):
        self.assertEqual(limits.MIN_DAILY_LIMIT, 1)
        self.assertEqual(identity_store.LAST_OWNER_GUARD, 1)

    def test_session_lifetimes(self):
        self.assertEqual(identity_store.MAX_SESSION_TTL, 30 * 86400)
        self.assertEqual(identity_store.MIN_SESSION_TTL, 60)

    def test_throttle_bounds(self):
        self.assertEqual(identity_store.THROTTLE_LIMIT, 20)
        self.assertEqual(identity_store.THROTTLE_WINDOW_SECONDS, 900)

    def test_invitation_window(self):
        self.assertEqual(identity_store.INVITATION_TTL_SECONDS, 86_400)
        self.assertEqual(identity_store.MIN_INVITATION_TTL_SECONDS, 300)
        self.assertEqual(identity_store.MAX_INVITATION_TTL_SECONDS, 604_800)

    def test_daily_limit_bounds(self):
        self.assertEqual(limits.DEFAULT_DAILY_LIMIT, 1000)
        self.assertEqual(limits.COUNTER_TTL_SECONDS, 86_400)
        self.assertEqual(limits.WARN_FRACTION, 0.8)

    def test_payload_ceilings(self):
        self.assertEqual(packs.MAX_PERSONA_CHARS, 8000)
        self.assertEqual(telegram.MAX_BODY, 1_000_000)
        self.assertEqual(trace.MAX_BYTES, 5 * 1024 * 1024)

    def test_runner_ceilings(self):
        self.assertEqual(runner_ws.MAX_QUEUE, 100)
        self.assertEqual(runner_ws.MAX_RESULTS, 1000)

    def test_the_warning_fires_before_the_ceiling(self):
        """A warning threshold above 1.0 would never fire; below 0 would always."""
        self.assertGreater(limits.WARN_FRACTION, 0.0)
        self.assertLess(limits.WARN_FRACTION, 1.0)

    def test_the_session_floor_is_below_the_session_ceiling(self):
        self.assertLess(identity_store.MIN_SESSION_TTL, identity_store.MAX_SESSION_TTL)
        self.assertLess(identity_store.MIN_INVITATION_TTL_SECONDS,
                        identity_store.MAX_INVITATION_TTL_SECONDS)
        self.assertLess(auth.MIN_TOKEN_TTL_SECONDS, auth.TTL_SECONDS)

    def test_the_session_token_lifetime_is_below_the_hard_ceiling(self):
        """A session token may not outlive the ceiling it is carved out of."""
        self.assertLess(auth.SESSION_TOKEN_TTL_SECONDS, auth.TTL_SECONDS)


class TokenLifetimeTests(unittest.TestCase):
    """``issue_token`` is pure, so the range is exercised directly."""

    def test_the_floor_and_the_ceiling(self):
        for ttl, ok in [(auth.MIN_TOKEN_TTL_SECONDS - 1, False),
                        (auth.MIN_TOKEN_TTL_SECONDS, True),
                        (auth.TTL_SECONDS, True),
                        (auth.TTL_SECONDS + 1, False)]:
            with self.subTest(ttl=ttl):
                try:
                    auth.issue_token('t', ttl_seconds=ttl)
                except ValueError:
                    self.assertFalse(ok, f'{ttl} should have been accepted')
                else:
                    self.assertTrue(ok, f'{ttl} should have been refused')

    def test_a_session_token_gets_the_shorter_lifetime(self):
        """The whole point of the 900: a session token must expire sooner.

        Decoded without verification: a session token's ``verify_claims`` also
        re-checks the session row, which would make this test about the database
        rather than about the lifetime.
        """
        def claims(token):
            return auth.jwt.decode(token, options={'verify_signature': False})

        session = claims(auth.issue_token('t', session_id='sid'))
        plain = claims(auth.issue_token('t'))
        self.assertEqual(auth.SESSION_TOKEN_TTL_SECONDS, session['exp'] - session['iat'])
        self.assertEqual(auth.TTL_SECONDS, plain['exp'] - plain['iat'])
        self.assertLess(session['exp'] - session['iat'], plain['exp'] - plain['iat'])

    def test_a_non_integer_lifetime_is_refused(self):
        """``True`` is an ``int`` in Python, so a naive range check would pass it."""
        for ttl in ['900', 900.0, True, [900]]:
            with self.subTest(ttl=repr(ttl)):
                with self.assertRaises(ValueError):
                    auth.issue_token('t', ttl_seconds=ttl)


class SessionAndInvitationTTLTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'APP_DB': str(Path(self.tmp.name) / 'app.db')})
        self.env.start()
        self.addCleanup(self.env.stop)
        reset()
        self.addCleanup(reset)
        self.owner = identity_store.register_user('owner@example.invalid',
                                                 'correct horse battery', 'Owner')
        identity_store.create_workspace(self.owner['id'], 'acme-main', 'Acme')

    def test_session_ttl_range(self):
        for ttl, ok in [(identity_store.MIN_SESSION_TTL - 1, False),
                        (identity_store.MIN_SESSION_TTL, True),
                        (identity_store.MAX_SESSION_TTL, True),
                        (identity_store.MAX_SESSION_TTL + 1, False)]:
            with self.subTest(ttl=ttl):
                try:
                    identity_store.create_session(self.owner['id'], ttl_seconds=ttl)
                except IdentityError:
                    self.assertFalse(ok, f'{ttl} should have been accepted')
                else:
                    self.assertTrue(ok, f'{ttl} should have been refused')

    def test_session_ttl_default_is_the_ceiling(self):
        import time
        _, details = identity_store.create_session(self.owner['id'])
        remaining = details['session_expires'] - time.time()
        self.assertLessEqual(remaining, identity_store.MAX_SESSION_TTL)
        self.assertGreater(remaining, identity_store.MAX_SESSION_TTL - 60)

    def test_invitation_ttl_range(self):
        for ttl, ok in [(identity_store.MIN_INVITATION_TTL_SECONDS - 1, False),
                        (identity_store.MIN_INVITATION_TTL_SECONDS, True),
                        (identity_store.MAX_INVITATION_TTL_SECONDS, True),
                        (identity_store.MAX_INVITATION_TTL_SECONDS + 1, False)]:
            with self.subTest(ttl=ttl):
                try:
                    identity_store.create_invitation(self.owner['id'], 'acme-main',
                                                     f'x{ttl}@example.invalid',
                                                     'operator', ttl_seconds=ttl)
                except IdentityError:
                    self.assertFalse(ok, f'{ttl} should have been accepted')
                else:
                    self.assertTrue(ok, f'{ttl} should have been refused')

    def test_a_float_session_ttl_is_refused(self):
        with self.assertRaises(IdentityError):
            identity_store.create_session(self.owner['id'], ttl_seconds=3600.0)


class ThrottleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'APP_DB': str(Path(self.tmp.name) / 'app.db')})
        self.env.start()
        self.addCleanup(self.env.stop)
        reset()
        self.addCleanup(reset)

    def test_the_limit_is_inclusive_then_exclusive(self):
        for _ in range(identity_store.THROTTLE_LIMIT):
            identity_store.throttle('login', 'k')
        with self.assertRaises(AuthRateLimited):
            identity_store.throttle('login', 'k')

    def test_a_different_key_has_its_own_bucket(self):
        for _ in range(identity_store.THROTTLE_LIMIT):
            identity_store.throttle('login', 'k1')
        identity_store.throttle('login', 'k2')

    def test_the_defaults_are_the_declared_ones(self):
        import inspect
        signature = inspect.signature(identity_store.throttle)
        self.assertEqual(signature.parameters['limit'].default,
                         identity_store.THROTTLE_LIMIT)
        self.assertEqual(signature.parameters['window_seconds'].default,
                         identity_store.THROTTLE_WINDOW_SECONDS)


class DailyLimitTests(unittest.TestCase):
    def test_the_default_is_used_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('DAILY_LIMIT_PER_TENANT', None)
            self.assertEqual(limits.DEFAULT_DAILY_LIMIT, limits._limit())

    def test_a_non_positive_override_falls_back_to_the_default(self):
        for value in ['0', '-5']:
            with self.subTest(value=value):
                with patch.dict(os.environ, {'DAILY_LIMIT_PER_TENANT': value}):
                    self.assertEqual(limits.DEFAULT_DAILY_LIMIT, limits._limit())

    def test_an_unparseable_override_falls_back_to_the_default(self):
        with patch.dict(os.environ, {'DAILY_LIMIT_PER_TENANT': 'lots'}):
            self.assertEqual(limits.DEFAULT_DAILY_LIMIT, limits._limit())

    def test_a_positive_override_is_honoured(self):
        with patch.dict(os.environ, {'DAILY_LIMIT_PER_TENANT': '7'}):
            self.assertEqual(7, limits._limit())


class PersonaCeilingTests(unittest.TestCase):
    """The same ceiling ``tests/test_pack_persona.py`` pins -- in a suite that runs.

    ``tests/`` is not wired into any gate, so a persona bound asserted only there
    is a bound nobody enforces on a machine that only runs the offline suite.
    """

    PACK = ('name: probe\nagents:\n  - id: ops.probe\n    name: Probe\n'
            '    tools: [reports.summary]\n    ladder: human_led\n'
            '    persona: p.md\nbranches: []\n')

    def build(self, size):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        folder = root / 'probe'
        folder.mkdir()
        (folder / 'pack.yaml').write_text(self.PACK, encoding='utf-8')
        (folder / 'p.md').write_text('x' * size, encoding='utf-8')
        return root

    def test_the_ceiling_is_inclusive(self):
        root = self.build(packs.MAX_PERSONA_CHARS)
        with patch.object(packs, 'PACKS_DIR', root):
            agent = packs.load_pack('probe').agents[0]
            self.assertEqual(packs.MAX_PERSONA_CHARS, len(agent.prompt))

    def test_one_character_past_the_ceiling_is_refused(self):
        root = self.build(packs.MAX_PERSONA_CHARS + 1)
        with patch.object(packs, 'PACKS_DIR', root):
            with self.assertRaises(packs.PackError):
                packs.load_pack('probe')


class _Request:
    """The two things the webhook handler asks of a request."""

    def __init__(self, headers, body):
        self.headers = headers
        self._body = body

    async def body(self):
        return self._body


class TelegramBodyCeilingTests(unittest.TestCase):
    """The 413 guard, called in-process rather than over a client.

    Deliberately NOT through ``starlette.testclient.TestClient``.  This module
    lives in ``runtime_tests``, which ``scripts/verify_offline.py`` runs behind an
    audit hook that raises on ``socket.connect``, ``socket.getaddrinfo`` and
    ``socket.sendto``: the offline suite is meant to be socket-free, and
    ``TestClient`` is the one thing here that opens a socket.  The first version of
    these three tests used it and turned the offline gate red with three errors
    reading ``RuntimeError: Offline verification: network disabled`` -- a test that
    reaches the network is not an offline test, however in-process the framework
    claims to be.  ``integration_tests`` is where HTTP belongs, and it is kept out
    of the offline gate for exactly this reason.

    Calling the coroutine directly is also the tighter assertion: it measures the
    route's own guard rather than a framework's round-trip through it.
    """

    SECRET = {'X-Telegram-Bot-Api-Secret-Token': 'dev-webhook-secret'}

    def call(self, body, content_length=None):
        """Drive the handler coroutine by hand; return its result or the exception.

        No event loop, and that is not an optimisation.  On Windows **an event
        loop is itself a socket**: both ``ProactorEventLoop`` and
        ``SelectorEventLoop`` build their self-pipe from ``socket.socketpair()``,
        which the offline audit hook sees as ``socket.connect``.  So
        ``asyncio.run`` and ``loop.run_until_complete`` are both refused here, and
        stepping the coroutine with ``send(None)`` is the only way to reach this
        guard offline.

        It is safe because the handler never actually suspends: its single ``await``
        is ``request.body()``, and the stub below returns without yielding.  If a
        future change makes the route await something real, ``send`` returns
        instead of raising and this fails loudly rather than hanging.
        """
        headers = dict(self.SECRET)
        if content_length is not None:
            headers['content-length'] = str(content_length)
        coro = telegram.telegram_webhook(_Request(headers, body))
        try:
            coro.send(None)
        except StopIteration as stop:
            return stop.value
        except HTTPException as exc:
            return exc
        coro.close()
        self.fail('the handler suspended before returning, so it now needs a real '
                  'event loop -- which on Windows means a socket, and the offline '
                  'suite forbids one')

    def test_a_body_at_the_ceiling_is_not_rejected_for_size(self):
        result = self.call(b'{}', content_length=telegram.MAX_BODY)
        self.assertNotIsInstance(result, HTTPException)
        self.assertEqual({'ok': True, 'ignored': True}, result)

    def test_a_declared_length_past_the_ceiling_is_rejected(self):
        result = self.call(b'{}', content_length=telegram.MAX_BODY + 1)
        self.assertIsInstance(result, HTTPException)
        self.assertEqual(413, result.status_code)

    def test_an_undeclared_length_is_measured_against_the_ceiling(self):
        """The header can lie, so the body is measured as well."""
        result = self.call(b'x' * (telegram.MAX_BODY + 1))
        self.assertIsInstance(result, HTTPException)
        self.assertEqual(413, result.status_code)

    def test_the_guard_names_the_constant(self):
        text = source(telegram)
        self.assertEqual(2, len(re.findall(r'>\s*MAX_BODY\b', text)))


class RunnerCeilingTests(unittest.TestCase):
    """These two guards sit inside a WebSocket loop and a signed admin request.

    Reaching them from a unit test would mean standing up either transport, which
    would test the transport rather than the ceiling.  They are pinned by their
    comparison site instead -- weaker than the behavioural tests above, and said
    so out loud rather than counted as equivalent.
    """

    def test_the_queue_guard_names_the_constant(self):
        self.assertIn('>= MAX_QUEUE', source(runner_ws))

    def test_the_result_trim_names_the_constant(self):
        text = source(runner_ws)
        self.assertIn('n > MAX_RESULTS', text)
        self.assertIn('n - MAX_RESULTS', text)

    def test_the_trim_keeps_the_newest_and_drops_the_oldest(self):
        """The arithmetic the mutation targets: over the ceiling, delete the excess."""
        for n, expected in [(runner_ws.MAX_RESULTS, 0),
                            (runner_ws.MAX_RESULTS + 1, 1),
                            (runner_ws.MAX_RESULTS + 50, 50)]:
            with self.subTest(n=n):
                self.assertEqual(expected, max(0, n - runner_ws.MAX_RESULTS))


class TraceRotationTests(unittest.TestCase):
    def test_a_file_at_the_ceiling_is_rotated_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.jsonl'
            with path.open('wb') as handle:
                handle.write(b'x' * (trace.MAX_BYTES + 1))
            with patch.dict(os.environ, {'TRACE_PATH': str(path)}):
                trace.log({'event': 'probe'})
            self.assertTrue(path.with_suffix('.old.jsonl').is_file(),
                            'a file past the ceiling must be rotated')
            self.assertLess(path.stat().st_size, trace.MAX_BYTES)

    def test_a_file_below_the_ceiling_is_appended_not_rotated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.jsonl'
            with patch.dict(os.environ, {'TRACE_PATH': str(path)}):
                trace.log({'event': 'one'})
                trace.log({'event': 'two'})
            self.assertFalse(path.with_suffix('.old.jsonl').exists())
            self.assertEqual(2, len(path.read_text(encoding='utf-8').splitlines()))

    def test_the_guard_uses_a_strict_comparison(self):
        """``> MAX_BYTES`` not ``>=``: a file exactly at the ceiling is still writable."""
        self.assertIn('> MAX_BYTES', source(trace))


class IdentifierRegexTests(unittest.TestCase):
    """The two regex ceilings, pinned behaviourally instead of by name."""

    def test_email_local_part_ceiling(self):
        local = 'a' * 128
        self.assertIsNotNone(identity_store.EMAIL_RE.match(f'{local}@example.invalid'))
        self.assertIsNone(identity_store.EMAIL_RE.match(f'{local}a@example.invalid'))

    def test_email_domain_ceiling(self):
        domain = 'a' * 255
        self.assertIsNotNone(identity_store.EMAIL_RE.match(f'x@{domain}'))
        self.assertIsNone(identity_store.EMAIL_RE.match(f'x@{domain}a'))

    def test_workspace_slug_ceiling(self):
        self.assertIsNotNone(identity_store.SLUG_RE.match('a' * 64))
        self.assertIsNone(identity_store.SLUG_RE.match('a' * 65))

    def test_workspace_slug_floor(self):
        self.assertIsNone(identity_store.SLUG_RE.match('a'))
        self.assertIsNotNone(identity_store.SLUG_RE.match('ab'))


class NoUnnamedBoundTests(unittest.TestCase):
    """The invariant that keeps the promoted names from drifting back to literals."""

    # Guard sites that must name a constant rather than carry a number.
    GUARDS = {
        'auth.py': [r'not\s+\w+\s*<=\s*ttl\s*<=\s*TTL_SECONDS'],
        'limits.py': [r'math\.ceil\(WARN_FRACTION \* _limit\(\)\)',
                      r'ex=COUNTER_TTL_SECONDS'],
        'telegram.py': [r'>\s*MAX_BODY'],
        'trace.py': [r'>\s*MAX_BYTES'],
        'runner_ws.py': [r'>=\s*MAX_QUEUE', r'>\s*MAX_RESULTS'],
        'packs.py': [r'>\s*MAX_PERSONA_CHARS'],
    }

    def test_every_guard_names_a_constant(self):
        for name, patterns in self.GUARDS.items():
            text = (APP / name).read_text(encoding='utf-8', newline='')
            for pattern in patterns:
                with self.subTest(module=name, pattern=pattern):
                    self.assertRegex(text, pattern)

    def test_the_promoted_modules_carry_no_stray_literal_in_a_guard(self):
        """A number compared against in a guard is a bound that lost its name."""
        offenders = {}
        for name in ('auth.py', 'limits.py', 'identity_store.py'):
            text = (APP / name).read_text(encoding='utf-8', newline='')
            body = text.split('Declared bounds', 1)[-1]
            # Comparisons against a bare integer, ignoring the constant block itself.
            hits = [line.strip() for line in body.splitlines()
                    if re.search(r'(?:<=|>=|<|>)\s*-?\d', line)
                    and not line.strip().startswith('#')]
            if hits:
                offenders[name] = hits
        self.assertEqual({}, offenders, f'name these bounds: {offenders}')


if __name__ == '__main__':
    unittest.main()
