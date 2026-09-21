import unittest
from unittest.mock import MagicMock,patch
from platform_runtime.model_transport import headers,validate_url,transport_for,NoRedirect


class LocalModelTests(unittest.TestCase):
    def setUp(self):self.cfg={'provider_mode':'local_loopback'}
    def test_numeric_loopback_allowed(self):
        for url in ['http://127.0.0.1:11434/v1/chat/completions','http://[::1]:1234/v1/chat/completions']:
            validate_url(self.cfg,url)
    def test_localhost_dns_remote_and_credentials_denied(self):
        for url in ['http://localhost:11434/v1','http://192.168.1.1:11434/v1','http://127.0.0.1/v1',
                    'http://127.0.0.1:80/v1','http://user:pw@127.0.0.1:11434/v1','http://127.0.0.1:11434/v1?key=x',
                    'https://remote.example/v1','file:///tmp/model']:
            with self.subTest(url=url),self.assertRaises(ValueError):validate_url(self.cfg,url)
    def test_cloud_http_still_denied(self):
        with self.assertRaises(ValueError):validate_url({},'http://127.0.0.1:11434/v1')
    def test_keyless_only_explicit_local(self):
        secret=MagicMock(return_value='test-secret')
        self.assertEqual({},headers(self.cfg,secret));secret.assert_not_called()
        self.assertEqual({'Authorization':'Bearer test-secret'},headers({},secret))
    def test_local_optional_key(self):
        self.assertEqual({'Authorization':'Bearer unit'},headers({**self.cfg,'key_env':'TEST_KEY'},lambda *a:'unit'))
    def test_injected_transport_still_validates_endpoint(self):
        fake=MagicMock(return_value={'choices':[]});send=transport_for(self.cfg,fake)
        send('http://127.0.0.1:11434/v1/chat/completions',{},{});fake.assert_called_once()
        with self.assertRaises(ValueError):send('http://evil.example:11434/v1',{}, {})
        fake.assert_called_once()
    def test_default_local_transport_is_bounded_no_redirect_no_proxy(self):
        response=MagicMock();response.status=200;response.read.return_value=b'{"choices":[]}'
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        with patch('urllib.request.build_opener',return_value=opener) as build:
            self.assertEqual({'choices':[]},transport_for(self.cfg)('http://127.0.0.1:11434/v1/chat/completions',{},{}))
            handlers=build.call_args.args;self.assertEqual({},handlers[0].proxies);self.assertIsInstance(handlers[1],NoRedirect)
            response.read.assert_called_once_with(128001)
    def test_large_response_denied(self):
        response=MagicMock();response.status=200;response.read.return_value=b'x'*128001
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        with patch('urllib.request.build_opener',return_value=opener),self.assertRaises(RuntimeError):
            transport_for(self.cfg)('http://127.0.0.1:11434/v1',{}, {})
    def test_redirect_denied(self):
        with self.assertRaises(RuntimeError):NoRedirect().redirect_request(None,None,302,'',{},'https://evil.example')
    def test_unsupported_mode_denied(self):
        with self.assertRaises(ValueError):validate_url({'provider_mode':'auto'},'https://example.invalid')
