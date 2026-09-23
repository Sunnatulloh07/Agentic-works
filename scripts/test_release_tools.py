import importlib.util
import os
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from sqlite_backup import backup
from check_release import check


class ReleaseToolsTests(unittest.TestCase):
    def test_wal_backup_restore_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'live.db';snapshot=Path(d)/'snapshot.db';restore=Path(d)/'restore.db'
            c=sqlite3.connect(source)
            try:
                c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE data(id INTEGER)');c.execute('INSERT INTO data VALUES(42)');c.commit()
                backup(source,snapshot);backup(snapshot,restore)
                target=sqlite3.connect(restore)
                try:self.assertEqual(42,target.execute('SELECT id FROM data').fetchone()[0])
                finally:target.close()
                if os.name == 'posix':
                    # Windows has no POSIX mode bits; chmod only toggles the read-only
                    # flag there, so the private-artifact check is a POSIX guarantee.
                    self.assertEqual(0o600,snapshot.stat().st_mode & 0o777)
                with self.assertRaises(FileExistsError):backup(source,snapshot)
                with self.assertRaises(ValueError):backup(source,source)
            finally:c.close()
    def test_release_cannot_pass_on_empty_evidence(self):
        self.assertEqual('NO_GO',check({})['release'])
    def test_status_without_evidence_is_not_pass(self):
        self.assertIn('http_integration',check({'gates':{'http_integration':{'status':'PASS'}}})['blockers'])


if __name__=='__main__':unittest.main()


class TelegramWebhookTests(unittest.TestCase):
    """The webhook registration must carry the secret the API verifies, and leak nothing."""

    def setUp(self):
        from telegram_set_webhook import build_request, masked, main, WebhookError
        self.build, self.masked, self.main, self.error = build_request, masked, main, WebhookError
        self.url = 'https://shop.example.uz/webhooks/telegram?tenant=demo-retail'
        self.token = '123456:AbC-def_GHI'

    def test_request_targets_setwebhook_with_the_secret(self):
        endpoint, body = self.build(self.url, 's3cr3t_token-1', self.token)
        self.assertEqual('https://api.telegram.org/bot123456:AbC-def_GHI/setWebhook', endpoint)
        self.assertEqual(self.url, body['url'])
        self.assertEqual('s3cr3t_token-1', body['secret_token'])

    def test_only_https_webhook_urls_are_accepted(self):
        for bad in ('http://shop.example.uz/hook', 'https://user:pw@shop.example.uz/hook',
                    'https://shop.example.uz/hook#frag', 'ftp://x'):
            with self.subTest(url=bad), self.assertRaises(self.error):
                self.build(bad, 'secret', self.token)

    def test_token_and_secret_shapes_are_checked_before_any_network(self):
        with self.assertRaises(self.error):
            self.build(self.url, 'secret', 'not-a-token')
        with self.assertRaises(self.error):
            self.build(self.url, 'has space', self.token)
        with self.assertRaises(self.error):
            self.build(self.url, 'x' * 257, self.token)

    def test_masking_removes_the_token_from_the_endpoint(self):
        endpoint, _ = self.build(self.url, 'secret', self.token)
        self.assertNotIn(self.token, self.masked(endpoint))
        self.assertIn('/bot***/setWebhook', self.masked(endpoint))

    def test_dry_run_never_prints_token_or_secret(self):
        import contextlib, io
        with unittest.mock.patch.dict(os.environ, {'T_TOKEN': self.token, 'T_SECRET': 'very-secret'}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = self.main(['--url', self.url, '--token-env', 'T_TOKEN',
                                  '--secret-env', 'T_SECRET', '--dry-run'])
        self.assertEqual(0, code)
        self.assertNotIn(self.token, out.getvalue())
        self.assertNotIn('very-secret', out.getvalue())
        self.assertIn('setWebhook', out.getvalue())

    def test_missing_environment_variable_is_a_refusal_not_a_crash(self):
        import contextlib, io
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = self.main(['--url', self.url, '--token-env', 'ABSENT_TOKEN', '--dry-run'])
        self.assertEqual(2, code)
        self.assertIn('not set', err.getvalue())
