"""Declared bounds of the outbound provider transport layer (§151).

Four modules, one question: **is every limit on bytes this platform did not author
pinned by a test?** ``google_oauth``, ``model_transport``, ``model_response`` and
``mcp`` all consume data chosen by a peer -- a provider, a local inference endpoint, a
model, an MCP server -- and all four enforced their limits with inline literals.

The measured spec is four files, because a bound is only as pinned as its most distant
consumer: ``test_oauth``, ``test_local_model_transport``, ``test_model_response`` and
``test_adapters``. A single-file spec would report a bound GREEN merely because the
test that would have caught it lives next door.

Every assertion states the bound's own **number**, never the constant's name, so
widening a constant cannot move its test along with it. Two bounds are recorded rather
than pinned, because measurement showed they cannot be reached through the real
surface; the reason is written at each site.
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from platform_runtime import mcp as mcp_module
from platform_runtime import model_transport as transport_module
from platform_runtime.google_oauth import (
    BASE_SCOPES, CONFIG_SCHEMA_VERSION, MAX_CLIENT_ID_CHARS, MAX_CREDENTIAL_CHARS,
    MAX_ERROR_BODY_BYTES, MAX_PROVIDER_RESPONSE_BYTES, MAX_REDIRECT_URI_CHARS,
    MAX_REQUEST_BODY_BYTES, MAX_SUBJECT_CHARS, TOKEN, TRANSPORT_TIMEOUT_SECONDS,
    USERINFO, GoogleOAuth, strict_json)
from platform_runtime.mcp import (INITIAL_PROTOCOL_VERSION, MAX_RESPONSE_BYTES,
                                  SUPPORTED_PROTOCOL_VERSIONS, MCPClient)
from platform_runtime.model_response import (MAX_DECISION_BYTES, MAX_JSON_DEPTH,
                                             MAX_JSON_NODES, REQUIRED_CHOICES,
                                             parse_decision)
from platform_runtime.model_transport import (LOCAL_TIMEOUT_SECONDS,
                                              MAX_LOCAL_REQUEST_BYTES,
                                              MAX_LOCAL_RESPONSE_BYTES,
                                              MIN_LOCAL_PORT, validate_url)
from platform_runtime.oauth import OAuthError

SUFFIX = '.apps.googleusercontent.com'
CALLBACK = 'https://example.com/'
LOCAL_URL = 'http://127.0.0.1:11434/v1/chat/completions'


def padded_json(size):
    """A JSON object of exactly ``size`` bytes: ``{"a":"xxx..."}``."""
    return ('{"a":"' + 'x' * (size - 8) + '"}').encode()


def google_config(**overrides):
    base = {'enabled': True, 'client_id': 'fake' + SUFFIX,
            'client_secret_env': 'FAKE_GOOGLE_SECRET',
            'redirect_uri': CALLBACK, 'expected_subject': 'subject',
            'scopes': sorted(BASE_SCOPES)}
    return {**base, **overrides}


class DeclaredValuesTests(unittest.TestCase):
    """The numbers themselves, so a widened constant is a failing test."""

    def test_google_oauth_declared_values_are_the_audited_ones(self):
        self.assertEqual(MAX_PROVIDER_RESPONSE_BYTES, 1000000)
        self.assertEqual(MAX_REQUEST_BODY_BYTES, 64000)
        self.assertEqual(MAX_CREDENTIAL_CHARS, 16000)
        self.assertEqual(MAX_ERROR_BODY_BYTES, 16000)
        self.assertEqual(MAX_CLIENT_ID_CHARS, 512)
        self.assertEqual(MAX_REDIRECT_URI_CHARS, 2000)
        self.assertEqual(MAX_SUBJECT_CHARS, 256)
        self.assertEqual(TRANSPORT_TIMEOUT_SECONDS, 25)
        self.assertEqual(CONFIG_SCHEMA_VERSION, 1)

    def test_model_transport_declared_values_are_the_audited_ones(self):
        self.assertEqual(MIN_LOCAL_PORT, 1024)
        self.assertEqual(MAX_LOCAL_REQUEST_BYTES, 128000)
        self.assertEqual(MAX_LOCAL_RESPONSE_BYTES, 128000)
        self.assertEqual(LOCAL_TIMEOUT_SECONDS, 30)

    def test_model_response_declared_values_are_the_audited_ones(self):
        self.assertEqual(MAX_DECISION_BYTES, 20000)
        self.assertEqual(MAX_JSON_DEPTH, 32)
        self.assertEqual(MAX_JSON_NODES, 5000)
        self.assertEqual(REQUIRED_CHOICES, 1)

    def test_mcp_declared_values_are_the_audited_ones(self):
        self.assertEqual(MAX_RESPONSE_BYTES, 1000000)
        self.assertEqual(mcp_module.TRANSPORT_TIMEOUT_SECONDS, 25)
        self.assertEqual(INITIAL_PROTOCOL_VERSION, '2025-03-26')
        self.assertEqual(SUPPORTED_PROTOCOL_VERSIONS, frozenset({'2025-03-26', '2025-06-18'}))


class GoogleOAuthBoundTests(unittest.TestCase):
    def opener(self, payload=b'{"sub":"s"}'):
        """A transport that answers 200 without touching a socket.

        Both sides of a bound are measured through the same fake: the accepted call
        must REACH the socket, and the refused call must not. Asserting only the
        refusal would leave a guard that fires one byte early looking correct.
        """
        response = MagicMock()
        response.status = 200
        response.read.return_value = payload
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        return opener

    def test_provider_response_ceiling_is_one_megabyte(self):
        self.assertEqual({'a': 'x' * (MAX_PROVIDER_RESPONSE_BYTES - 8)},
                         strict_json(padded_json(MAX_PROVIDER_RESPONSE_BYTES)))
        with self.assertRaises(OAuthError):
            strict_json(padded_json(MAX_PROVIDER_RESPONSE_BYTES + 1))

    def test_request_body_ceiling_is_sixty_four_kilobytes(self):
        from platform_runtime.google_oauth import google_transport
        from urllib.parse import urlencode
        prefix = len('client_id=')
        at_limit = {'client_id': 'x' * (MAX_REQUEST_BODY_BYTES - prefix)}
        self.assertEqual(MAX_REQUEST_BODY_BYTES, len(urlencode(at_limit)))
        opener = self.opener()
        with patch('urllib.request.build_opener', return_value=opener):
            google_transport('POST', TOKEN, at_limit)
        self.assertEqual(1, opener.open.call_count)
        opener.reset_mock()
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(OAuthError) as caught:
                google_transport('POST', TOKEN, {'client_id': 'x' * (MAX_REQUEST_BODY_BYTES - prefix + 1)})
        self.assertIn('exceeds limit', str(caught.exception))
        opener.open.assert_not_called()

    def test_credential_ceiling_is_sixteen_thousand_characters(self):
        from platform_runtime.google_oauth import google_transport
        opener = self.opener()
        with patch('urllib.request.build_opener', return_value=opener):
            google_transport('GET', USERINFO, bearer='b' * MAX_CREDENTIAL_CHARS)
        self.assertEqual(1, opener.open.call_count)
        opener.reset_mock()
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ValueError):
                google_transport('GET', USERINFO, bearer='b' * (MAX_CREDENTIAL_CHARS + 1))
        opener.open.assert_not_called()

    def test_client_id_ceiling_is_five_hundred_and_twelve(self):
        longest = 'a' * (MAX_CLIENT_ID_CHARS - len(SUFFIX)) + SUFFIX
        self.assertEqual(MAX_CLIENT_ID_CHARS, len(longest))
        GoogleOAuth(google_config(client_id=longest), lambda _: 'synthetic')
        with self.assertRaises(ValueError):
            GoogleOAuth(google_config(client_id='a' + longest), lambda _: 'synthetic')

    def test_client_id_must_be_a_google_client_id(self):
        """Found by the first matrix, not by reading the code: the suffix rule could be
        deleted and every test stayed green. The ceiling test above only ever fed a
        well-formed id, so it pinned the length and left the shape unmeasured."""
        for value in ('fake', 'fake.apps.example.com',
                      'fake.apps.googleusercontent.com.evil.example',
                      'fake.appsgoogleusercontent.com', 'fake.apps.googleusercontent.com.evil'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                GoogleOAuth(google_config(client_id=value), lambda _: 'synthetic')
        GoogleOAuth(google_config(client_id='123-abc_def' + SUFFIX), lambda _: 'synthetic')

    def test_redirect_uri_ceiling_is_two_thousand(self):
        longest = CALLBACK + 'a' * (MAX_REDIRECT_URI_CHARS - len(CALLBACK))
        self.assertEqual(MAX_REDIRECT_URI_CHARS, len(longest))
        GoogleOAuth(google_config(redirect_uri=longest), lambda _: 'synthetic')
        with self.assertRaises(ValueError):
            GoogleOAuth(google_config(redirect_uri=longest + 'a'), lambda _: 'synthetic')

    def test_expected_subject_ceiling_is_two_hundred_and_fifty_six(self):
        GoogleOAuth(google_config(expected_subject='s' * MAX_SUBJECT_CHARS), lambda _: 'synthetic')
        with self.assertRaises(ValueError):
            GoogleOAuth(google_config(expected_subject='s' * (MAX_SUBJECT_CHARS + 1)), lambda _: 'synthetic')

    def test_identity_subject_ceiling_is_the_same_two_hundred_and_fifty_six(self):
        """The bound appears twice -- once on configuration, once on provider output.
        A single pin would let the two drift apart."""
        seen = []

        def transport(method, url, fields=None, bearer=None):
            seen.append((method, url))
            return {'email_verified': True, 'sub': 's' * 257}

        provider = GoogleOAuth(google_config(), lambda _: 'synthetic', transport)
        with self.assertRaises(ValueError):
            provider.identity('token')
        self.assertEqual([('GET', USERINFO)], seen)

    def test_secret_reference_pattern_is_two_to_one_hundred_and_twenty_eight(self):
        for value in ('A', 'A' * 129, 'lower', 'A-B'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                GoogleOAuth(google_config(client_secret_env=value), lambda _: 'synthetic')
        for value in ('AB', 'A' * 128, 'A_1'):
            with self.subTest(value=value):
                GoogleOAuth(google_config(client_secret_env=value), lambda _: 'synthetic')

    def test_public_config_schema_version_is_pinned(self):
        provider = GoogleOAuth(google_config(), lambda _: 'synthetic')
        self.assertEqual(1, provider.public_config()['schema_version'])

    def test_error_body_ceiling_is_sixteen_kilobytes(self):
        """A provider's *failure* body is still provider-chosen bytes, and it is read
        before it is parsed -- so the read size, not the parser, is the bound."""
        import urllib.error

        from platform_runtime.google_oauth import google_transport
        body = MagicMock()
        body.read.return_value = b'{"error":"invalid_grant"}'
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError(TOKEN, 400, 'Bad Request', {}, body)
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(OAuthError):
                google_transport('POST', TOKEN, {})
        body.read.assert_called_once_with(16001)


class ModelTransportBoundTests(unittest.TestCase):
    def test_local_port_floor_is_one_thousand_and_twenty_four(self):
        cfg = {'provider_mode': 'local_loopback'}
        validate_url(cfg, 'http://127.0.0.1:%d/v1' % MIN_LOCAL_PORT)
        with self.assertRaises(ValueError):
            validate_url(cfg, 'http://127.0.0.1:%d/v1' % (MIN_LOCAL_PORT - 1))

    def test_local_port_ceiling_is_recorded_not_pinned(self):
        """65535 cannot be exceeded through this surface: ``urlsplit`` refuses any
        port above 65535 before the comparison runs, so widening the constant would
        not change an outcome and no test could tell. Recorded as a measurement."""
        cfg = {'provider_mode': 'local_loopback'}
        validate_url(cfg, 'http://127.0.0.1:65535/v1')
        with self.assertRaises(ValueError):
            validate_url(cfg, 'http://127.0.0.1:65536/v1')

    def test_local_request_ceiling_is_one_hundred_and_twenty_eight_kilobytes(self):
        from platform_runtime.model_transport import transport_for
        send = transport_for({'provider_mode': 'local_loopback'})
        with self.assertRaises(ValueError):
            send(LOCAL_URL, {'prompt': 'x' * MAX_LOCAL_REQUEST_BYTES}, {})

    def test_local_response_ceiling_and_timeout_are_pinned_at_the_call(self):
        from platform_runtime.model_transport import transport_for
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'x' * (MAX_LOCAL_RESPONSE_BYTES + 1)
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(RuntimeError):
                transport_for({'provider_mode': 'local_loopback'})(LOCAL_URL, {}, {})
        self.assertEqual(128001, response.read.call_args.args[0])
        self.assertEqual(30, opener.open.call_args.kwargs['timeout'])

    def test_cloud_mode_keeps_https(self):
        with self.assertRaises(ValueError):
            validate_url({}, 'http://127.0.0.1:11434/v1')
        validate_url({}, 'https://api.example/v1')


class ModelResponseBoundTests(unittest.TestCase):
    @staticmethod
    def envelope(text):
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]}

    def test_decision_ceiling_is_twenty_thousand_bytes(self):
        head, tail = '{"action":"ask","question":"', '"}'
        room = MAX_DECISION_BYTES - len(head) - len(tail)
        self.assertEqual(MAX_DECISION_BYTES, len((head + 'x' * room + tail).encode('utf-8')))
        parse_decision(self.envelope(head + 'x' * room + tail))
        with self.assertRaises(ValueError):
            parse_decision(self.envelope(head + 'x' * (room + 1) + tail))

    def test_json_depth_ceiling_is_thirty_two(self):
        parse_decision(self.envelope('{"a":' * MAX_JSON_DEPTH + '1' + '}' * MAX_JSON_DEPTH))
        with self.assertRaises(ValueError):
            parse_decision(self.envelope('{"a":' * (MAX_JSON_DEPTH + 1) + '1' + '}' * (MAX_JSON_DEPTH + 1)))

    def test_json_node_ceiling_is_five_thousand(self):
        # root object + array + (n + 1) scalars == n + 3 visited nodes.
        def wide(n):
            return '{"a":[' + '0,' * n + '0]}'

        parse_decision(self.envelope(wide(MAX_JSON_NODES - 3)))     # exactly 5000 nodes
        with self.assertRaises(ValueError):
            parse_decision(self.envelope(wide(MAX_JSON_NODES - 2)))  # 5001 nodes

    def test_node_and_depth_ceilings_do_not_collide(self):
        """The wide fixture must be refused for width, not for depth."""
        wide = '{"a":[' + '0,' * (MAX_JSON_NODES - 2) + '0]}'
        self.assertEqual(1, wide.count('{'))
        self.assertEqual(1, wide.count('['))
        self.assertLess(len(wide.encode('utf-8')), MAX_DECISION_BYTES)

    def test_exactly_one_choice_is_required(self):
        self.assertEqual(1, REQUIRED_CHOICES)
        for count in (0, 2):
            with self.assertRaises(ValueError):
                parse_decision({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}] * count})


class FakeMCP:
    def __init__(self, version, is_error=False):
        self.version, self.is_error, self.bodies = version, is_error, []

    def __call__(self, body, headers):
        self.bodies.append(body)
        if body.get('method') == 'initialize':
            return {'jsonrpc': '2.0', 'id': body.get('id'), 'result': {'protocolVersion': self.version}}
        if body.get('method') == 'tools/call':
            return {'jsonrpc': '2.0', 'id': body.get('id'), 'result': {'isError': self.is_error}}
        return None


class MCPBoundTests(unittest.TestCase):
    def test_initial_protocol_version_is_pinned(self):
        client = MCPClient('https://example.invalid/mcp', 'fake')
        self.assertEqual('2025-03-26', client.protocol)

    def test_supported_versions_are_exactly_two(self):
        for version in sorted(SUPPORTED_PROTOCOL_VERSIONS):
            with self.subTest(version=version):
                self.assertEqual({'isError': False},
                                 MCPClient('https://example.invalid/mcp', 'fake', FakeMCP(version)).call('crm.lookup', {}))
        for version in ('2025-06-19', '2024-11-05', '', None):
            with self.subTest(version=version), self.assertRaises(RuntimeError):
                MCPClient('https://example.invalid/mcp', 'fake', FakeMCP(version)).call('crm.lookup', {})

    def test_negotiated_version_is_adopted(self):
        client = MCPClient('https://example.invalid/mcp', 'fake', FakeMCP('2025-06-18'))
        client.call('crm.lookup', {})
        self.assertEqual('2025-06-18', client.protocol)

    def test_initialize_carries_the_initial_version(self):
        transport = FakeMCP('2025-03-26')
        MCPClient('https://example.invalid/mcp', 'fake', transport).call('crm.lookup', {})
        self.assertEqual('2025-03-26', transport.bodies[0]['params']['protocolVersion'])

    def test_response_ceiling_and_timeout_are_pinned_at_the_call(self):
        response = MagicMock()
        response.status = 200
        response.headers = {'Content-Type': 'application/json'}
        response.read.return_value = b'x' * (MAX_RESPONSE_BYTES + 1)
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ValueError):
                MCPClient('https://example.invalid/mcp', 'fake')._http({'id': 1}, {})
        self.assertEqual(1000001, response.read.call_args.args[0])
        self.assertEqual(25, opener.open.call_args.kwargs['timeout'])

    def test_a_response_at_the_ceiling_is_accepted(self):
        payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {}}).encode()
        response = MagicMock()
        response.status = 200
        response.headers = {'Content-Type': 'application/json'}
        response.read.return_value = payload
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch('urllib.request.build_opener', return_value=opener):
            self.assertEqual({'jsonrpc': '2.0', 'id': 1, 'result': {}},
                             MCPClient('https://example.invalid/mcp', 'fake')._http({'id': 1}, {}))
        self.assertEqual(MAX_RESPONSE_BYTES + 1, response.read.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
