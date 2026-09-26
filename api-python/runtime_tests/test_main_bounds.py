"""Declared bounds of the ASGI entry point (app/main.py).

``main.py`` holds the platform's front door: the CORS surface, the tenant charset,
the role set, and the legacy-authorization middleware that retires the old routes
and gates the ones that remain. Every one of those was an inline literal, so
nothing could address them and widening one was silent.

The middleware is exercised by driving the coroutine **by hand**. That is not an
optimisation: on Windows an event loop is itself a socket (both loop classes build
their self-pipe from ``socket.socketpair()``), and the offline suite forbids one.
The coroutine never actually suspends -- its single await is a stub that returns
without yielding -- and if that changes, ``send(None)`` returns instead of raising
and the test fails loudly rather than hanging.

The HTTP-shaped behaviour these bounds feed (preflight, retirement statuses) is
also pinned end to end in ``integration_tests/test_cors_and_retirement_http.py``;
this file is the offline owner of the numbers.
"""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

from app import main
from app.auth import issue_token


class _Request:
    def __init__(self, path, method='GET', headers=None):
        self.url = SimpleNamespace(path=path)
        self.method = method
        self.headers = headers or {}


class _Response:
    def __init__(self):
        self.headers = {}


class DeclaredBoundTests(unittest.TestCase):
    def test_the_cors_surface(self):
        self.assertEqual(main.DEFAULT_CORS_ORIGIN, 'http://localhost:3000')
        self.assertEqual(main.CORS_METHODS,
                         ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
        self.assertEqual(main.CORS_HEADERS,
                         ['Authorization', 'Content-Type', 'X-Admin-Token', 'Idempotency-Key'])

    def test_tenant_and_role_bounds(self):
        self.assertEqual(main.MAX_TENANT_CHARS, 64)
        self.assertEqual(main.ROLES, frozenset({'owner', 'operator', 'integrator', 'viewer'}))
        self.assertEqual(main.MUTATION_ROLES, frozenset({'owner', 'operator'}))

    def test_the_middleware_prefixes(self):
        self.assertEqual(main.LEGACY_RUNNER_PREFIX, '/runner/')
        self.assertEqual(main.OWNER_ONLY_PREFIX, '/ladder/')
        self.assertEqual(main.LEGACY_EXEMPT_PREFIXES,
                         ('/webhooks/', '/platform/', '/auth/', '/identity/'))
        self.assertEqual(main.NO_STORE_PREFIXES, ('/identity/', '/platform/'))

    def test_no_inline_literal_survives(self):
        with open(main.__file__, encoding='utf-8') as handle:
            source = handle.read()
        for literal in ("DEFAULT_CORS_ORIGIN = 'http://localhost:3000'",
                        'MAX_TENANT_CHARS = 64',
                        "LEGACY_RUNNER_PREFIX = '/runner/'"):
            self.assertIn(literal, source)
        for inline in ('len(tenant_id) > 64', 'path.startswith("/runner/")',
                       '"/webhooks/", "/platform/"', 'if path.startswith("/ladder/")'):
            self.assertNotIn(inline, source)


class MiddlewareTests(unittest.TestCase):
    TENANT = 'demo-retail'

    def call(self, path, method='GET', headers=None, pipeline_mode='platform',
             directory='true'):
        request = _Request(path, method, headers)
        response = _Response()

        async def call_next(_request):
            return response

        with patch.dict(os.environ, {'PIPELINE_MODE': pipeline_mode,
                                     'IDENTITY_DIRECTORY': directory}):
            coro = main.legacy_authorization(request, call_next)
            try:
                coro.send(None)
            except StopIteration as stop:
                return stop.value
        self.fail('the middleware suspended; it now needs a real event loop')

    def detail(self, response):
        return json.loads(response.body)['detail']

    def test_the_legacy_runner_route_is_gone_whatever_the_method(self):
        for method in ('GET', 'POST'):
            with self.subTest(method=method):
                result = self.call('/runner/ws', method=method)
                self.assertEqual(result.status_code, 410)
                self.assertIn('Legacy runner disabled', self.detail(result))

    def test_a_legacy_mutation_is_retired_in_platform_mode(self):
        result = self.call('/orders', method='POST')
        self.assertEqual(result.status_code, 410)
        self.assertIn('Legacy mutation retired', self.detail(result))

    def test_only_the_read_verbs_pass_on_a_legacy_path(self):
        for method in ('GET', 'HEAD', 'OPTIONS'):
            with self.subTest(method=method):
                result = self.call('/orders', method=method)
                self.assertIsInstance(result, _Response)

    def test_legacy_mode_still_serves_the_legacy_paths(self):
        result = self.call('/orders', method='POST', pipeline_mode='legacy')
        self.assertIsInstance(result, _Response)

    def test_an_exempt_prefix_passes_a_mutation_through(self):
        for path in ('/webhooks/telegram', '/platform/demo-retail/tasks',
                     '/auth/token', '/identity/session'):
            with self.subTest(path=path):
                result = self.call(path, method='POST')
                self.assertIsInstance(result, _Response)

    def test_an_invalid_token_on_a_legacy_path_is_401(self):
        result = self.call('/orders', headers={'Authorization': 'Bearer nonsense'})
        self.assertEqual(result.status_code, 401)
        self.assertEqual(self.detail(result), 'Invalid token')

    def test_an_operator_may_use_a_legacy_read_path(self):
        token = issue_token(self.TENANT, subject='olga', role='operator')
        result = self.call('/orders', headers={'Authorization': 'Bearer ' + token},
                           directory='false')
        self.assertIsInstance(result, _Response)

    def test_a_viewer_cannot_mutate(self):
        # The role check is reachable only where legacy mutations still run; in
        # platform mode the retirement answers first (410), which the test above pins.
        token = issue_token(self.TENANT, subject='vera', role='viewer')
        result = self.call('/ladder/step', method='POST', pipeline_mode='legacy',
                           headers={'Authorization': 'Bearer ' + token}, directory='false')
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.detail(result), 'Role not permitted')

    def test_the_owner_only_prefix_refuses_an_operator(self):
        token = issue_token(self.TENANT, subject='olga', role='operator')
        result = self.call('/ladder/step', method='POST', pipeline_mode='legacy',
                           headers={'Authorization': 'Bearer ' + token}, directory='false')
        self.assertEqual(result.status_code, 403)

    def test_platform_responses_are_marked_no_store(self):
        for path in ('/platform/demo-retail/tasks', '/identity/session'):
            with self.subTest(path=path):
                result = self.call(path)
                self.assertEqual(result.headers.get('Cache-Control'), 'no-store')
                self.assertEqual(result.headers.get('X-Content-Type-Options'), 'nosniff')

    def test_other_paths_carry_no_cache_header(self):
        result = self.call('/orders')
        self.assertNotIn('Cache-Control', result.headers)


if __name__ == '__main__':
    unittest.main()

