"""Local first-run tools: run_local (.env loader + supervisor), setup_local, provision_identity.

Wired like test_release_tools.py: `python -m unittest discover -s scripts -p "test_run_local.py"`.
Stdlib only; the one end-to-end provisioning test skips when the API's own
dependencies (PyYAML, pydantic) are not installed.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import run_local
import setup_local
import provision_identity

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
HAS_API_DEPS = all(importlib.util.find_spec(m) for m in ('yaml', 'pydantic'))


class ParseEnvTests(unittest.TestCase):
    def test_comments_blanks_export_and_plain_values(self):
        text = '\ufeff# izoh\n\nENV=dev\nexport JWT_SECRET=abc=def\n  CORS_ORIGINS = http://localhost:3000  \n'
        self.assertEqual({'ENV': 'dev', 'JWT_SECRET': 'abc=def', 'CORS_ORIGINS': 'http://localhost:3000'},
                         run_local.parse_env(text))

    def test_quotes_and_inline_comments(self):
        text = ("A='x # not a comment'\nB=\"y z\" # trailing\nC=v # comment\nD=v#kept\nE=\nF=''\n"
                "G=o‘g‘il\n")
        self.assertEqual({'A': 'x # not a comment', 'B': 'y z', 'C': 'v', 'D': 'v#kept', 'E': '', 'F': '',
                          'G': 'o‘g‘il'}, run_local.parse_env(text))

    def test_bad_lines_name_the_line_never_the_content(self):
        for text in ('SuperSecretValue123\n', 'BAD KEY=SuperSecretValue123\n',
                     'K="SuperSecretValue123\n', "K='SuperSecretValue123' tail\n"):
            with self.subTest(text=text), self.assertRaises(run_local.EnvFileError) as caught:
                run_local.parse_env('OK=1\n' + text)
            self.assertIn('line 2', str(caught.exception))
            self.assertNotIn('SuperSecretValue123', str(caught.exception))


class ApplyEnvTests(unittest.TestCase):
    def test_process_env_wins_and_paths_resolve_against_repo(self):
        root = Path(tempfile.gettempdir()) / 'repo-root'
        environ = {'ENV': 'test', 'APP_DB': str(root / 'already' / 'set.db')}
        run_local.apply_env({'ENV': 'dev', 'JWT_SECRET': 's', 'PACKS_DIR': 'mypacks'}, environ, root)
        self.assertEqual('test', environ['ENV'])
        self.assertEqual('s', environ['JWT_SECRET'])
        self.assertEqual(str(root / 'already' / 'set.db'), environ['APP_DB'])
        self.assertEqual(str((root / 'mypacks').resolve()), environ['PACKS_DIR'])
        self.assertEqual(str((root / 'config' / 'integrations.json').resolve()),
                         environ['PLATFORM_INTEGRATIONS_FILE'])

    def test_defaults_without_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            environ = {}
            self.assertFalse(run_local.load_environment(Path(d) / 'missing.env', environ, Path(d)))
            self.assertEqual(str((Path(d) / 'api-python' / 'data' / 'app.db').resolve()), environ['APP_DB'])
            self.assertEqual(str((Path(d) / 'packs').resolve()), environ['PACKS_DIR'])


class CheckTests(unittest.TestCase):
    def test_check_prints_names_never_values(self):
        with tempfile.TemporaryDirectory() as d:
            env_file = Path(d) / '.env'
            integ = Path(d) / 'integrations.json'
            integ.write_text(json.dumps({'demo-retail': {'telegram': {'token_env': 'UNIT_TG_TOKEN'},
                                                         'llm': {'key_env': 'UNIT_LLM_KEY', 'model': 'm'}}}),
                             encoding='utf-8')
            env_file.write_text('ENV=dev\nJWT_SECRET=ValueJwtNeverPrinted_0123456789abcdef\n'
                                'ADMIN_TOKEN=ValueAdminNeverPrinted_01\nTELEGRAM_WEBHOOK_SECRET=ValueHookNeverPrinted_01\n'
                                f'APP_DB={Path(d) / "app.db"}\nPLATFORM_INTEGRATIONS_FILE={integ}\n'
                                'UNIT_TG_TOKEN=123:ValueTokenNeverPrinted\nUNIT_LLM_KEY=\n', encoding='utf-8')
            env = {k: v for k, v in os.environ.items() if k not in
                   ('ENV', 'JWT_SECRET', 'ADMIN_TOKEN', 'TELEGRAM_WEBHOOK_SECRET', 'APP_DB', 'PACKS_DIR',
                    'PLATFORM_INTEGRATIONS_FILE', 'ALLOW_INSECURE_DEV', 'UNIT_TG_TOKEN', 'UNIT_LLM_KEY')}
            done = subprocess.run([sys.executable, str(SCRIPTS / 'run_local.py'), '--check', '--env-file', str(env_file)],
                                  capture_output=True, text=True, encoding='utf-8', env=env, timeout=120)
        out = done.stdout + done.stderr
        self.assertNotIn('NeverPrinted', out)
        self.assertIn('runtime config', out)
        # The empty LLM key is named, so the check fails and says which variable.
        self.assertIn('UNIT_LLM_KEY', out)
        self.assertNotEqual(0, done.returncode)
        if HAS_API_DEPS:
            self.assertIn('PASS  runtime config', out)
            self.assertIn('PASS  pack demo-retail', out)

    def test_missing_integration_config_names_both_places(self):
        with tempfile.TemporaryDirectory() as d:
            environ = {'PLATFORM_INTEGRATIONS_FILE': str(Path(d) / 'absent.json'), 'PACKS_DIR': str(REPO / 'packs')}
            results = run_local.integration_checks(environ, ['demo-retail'])
        self.assertEqual(('integrations demo-retail', None, 'not configured'), results[0])
        name, ok, detail = results[-1]
        self.assertFalse(ok)
        self.assertIn('PLATFORM_INTEGRATIONS_FILE', detail)
        self.assertIn('integrations.yaml', detail)


class ConversationCheckTests(unittest.TestCase):
    AGENTS = [{'id': 'sales', 'conversation': {'enabled': True}}, {'id': 'ops', 'conversation': {'enabled': False}}]

    def test_no_conversation_agent_is_skipped(self):
        agents = [{'id': 'ops', 'conversation': {'enabled': False}}]
        self.assertIsNone(run_local.conversation_check(agents, {})[0])

    def test_conversation_agent_needs_the_loop_opt_in(self):
        for cfg in ({}, {'llm': {'model': 'm'}}, {'llm': {'model': 'm', 'agent_loop_enabled': False}},
                    {'llm': {'model': 'm', 'agent_loop_enabled': 'true'}}):
            with self.subTest(cfg=cfg):
                ok, detail = run_local.conversation_check(self.AGENTS, cfg)
                self.assertIs(False, ok)
                self.assertIn('sales', detail)
                self.assertIn('agent_loop_enabled', detail)

    def test_placeholder_model_is_refused(self):
        ok, detail = run_local.conversation_check(
            self.AGENTS, {'llm': {'model': 'SET_YOUR_AVAILABLE_MODEL_ID', 'agent_loop_enabled': True}})
        self.assertIs(False, ok)
        self.assertIn('model', detail)

    def test_opted_in_loop_passes(self):
        ok, _ = run_local.conversation_check(self.AGENTS, {'llm': {'model': 'claude-opus-5', 'agent_loop_enabled': True}})
        self.assertIs(True, ok)


class SuperviseTests(unittest.TestCase):
    def test_a_dead_child_stops_the_other_and_exits_non_zero(self):
        sleeper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        dying = subprocess.Popen([sys.executable, '-c', 'import sys; sys.exit(3)'])
        started = time.monotonic()
        with contextlib.redirect_stderr(io.StringIO()):
            code = run_local.supervise([('api', sleeper), ('worker', dying)], poll_seconds=0.1)
        self.assertEqual(3, code)
        self.assertIsNotNone(sleeper.poll())
        self.assertLess(time.monotonic() - started, 30)

    def test_a_clean_exit_is_still_non_zero(self):
        sleeper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        done = subprocess.Popen([sys.executable, '-c', 'pass'])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, run_local.supervise([('api', sleeper), ('worker', done)], poll_seconds=0.1))
        self.assertIsNotNone(sleeper.poll())


class SetupLocalTests(unittest.TestCase):
    def make_root(self, d):
        root = Path(d)
        (root / 'api-python').mkdir(); (root / 'config').mkdir()
        return root

    def test_writes_paths_placeholders_and_integration_template(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.make_root(d)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, setup_local.main(root))
            env = run_local.parse_env((root / 'api-python' / '.env').read_text(encoding='utf-8'))
            for key in ('APP_DB', 'PACKS_DIR', 'PLATFORM_INTEGRATIONS_FILE'):
                self.assertIn(key, env)
            self.assertEqual('', env['DEMO_TELEGRAM_TOKEN'])
            self.assertEqual('', env['PLATFORM_LLM_KEY'])
            self.assertTrue(len(env['JWT_SECRET']) >= 32)
            config = json.loads((root / 'config' / 'integrations.json').read_text(encoding='utf-8'))
            self.assertEqual('DEMO_TELEGRAM_TOKEN', config['demo-retail']['telegram']['token_env'])
            self.assertEqual('PLATFORM_LLM_KEY', config['demo-retail']['llm']['key_env'])
            self.assertNotIn(env['JWT_SECRET'], out.getvalue())
            # The .env paths resolve, via run_local, to the files setup_local created.
            environ = {}
            run_local.apply_env(env, environ, root)
            self.assertEqual(str((root / 'config' / 'integrations.json').resolve()), environ['PLATFORM_INTEGRATIONS_FILE'])

    def test_llm_template_opts_into_conversations_with_a_current_model(self):
        # demo-retail's sales agent holds conversations; with the loop off every
        # customer would only ever receive the handoff text.
        llm = setup_local.integrations_template()['demo-retail']['llm']
        self.assertEqual('anthropic', llm['provider'])
        self.assertEqual('claude-opus-5', llm['model'])
        self.assertEqual('low', llm['effort'])
        self.assertIs(True, llm['agent_loop_enabled'])
        self.assertEqual('PLATFORM_LLM_KEY', llm['key_env'])
        self.assertNotIn('base_url', llm)

    def test_existing_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.make_root(d)
            (root / 'api-python' / '.env').write_text('MINE=1\n', encoding='utf-8')
            (root / 'config' / 'integrations.json').write_text('{"mine": {}}\n', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = setup_local.main(root)
            self.assertNotEqual(0, code)
            self.assertIn('Existing .env preserved', out.getvalue())
            self.assertEqual('MINE=1\n', (root / 'api-python' / '.env').read_text(encoding='utf-8'))
            self.assertEqual('{"mine": {}}\n', (root / 'config' / 'integrations.json').read_text(encoding='utf-8'))


class ProvisionTests(unittest.TestCase):
    def parse(self, *argv):
        return provision_identity.parser().parse_args(['--workspace', 'demo-retail', '--workspace-name', 'Demo', *argv])

    def test_password_stdin_reads_one_line_and_never_prompts(self):
        args = self.parse('--email', 'o@example.com', '--display-name', 'Ega', '--password-stdin')
        fail = unittest.mock.Mock(side_effect=AssertionError('prompted'))
        got = provision_identity.credentials(args, io.StringIO('Long-Passw0rd-123\r\nignored\n'), fail, fail)
        self.assertEqual(('o@example.com', 'Ega', 'Long-Passw0rd-123'), got)

    def test_password_stdin_requires_email_and_name(self):
        with self.assertRaises(SystemExit):
            provision_identity.credentials(self.parse('--password-stdin'), io.StringIO('x\n'), input, input)

    def test_empty_stdin_is_refused(self):
        args = self.parse('--email', 'o@example.com', '--display-name', 'Ega', '--password-stdin')
        with self.assertRaises(SystemExit):
            provision_identity.credentials(args, io.StringIO(''), input, input)

    def test_interactive_path_unchanged(self):
        answers = iter(['o@example.com', 'Ega'])
        secrets_ = iter(['Long-Passw0rd-123', 'Long-Passw0rd-123'])
        got = provision_identity.credentials(self.parse(), io.StringIO(''), lambda p: next(answers),
                                             lambda p: next(secrets_))
        self.assertEqual(('o@example.com', 'Ega', 'Long-Passw0rd-123'), got)
        mismatch = iter(['a' * 12, 'b' * 12])
        with self.assertRaises(SystemExit):
            provision_identity.credentials(self.parse('--email', 'o@example.com', '--display-name', 'Ega'),
                                           io.StringIO(''), input, lambda p: next(mismatch))

    @unittest.skipUnless(HAS_API_DEPS, 'API dependencies (PyYAML, pydantic) not installed')
    def test_end_to_end_under_a_pipe(self):
        with tempfile.TemporaryDirectory() as d:
            env_file = Path(d) / '.env'
            env_file.write_text(f'ENV=test\nALLOW_INSECURE_DEV=true\nAPP_DB={Path(d) / "app.db"}\n', encoding='utf-8')
            env = {k: v for k, v in os.environ.items() if k not in ('ENV', 'APP_DB', 'PACKS_DIR')}
            done = subprocess.run([sys.executable, str(SCRIPTS / 'provision_identity.py'), '--env-file', str(env_file),
                                   '--workspace', 'demo-retail', '--workspace-name', 'Demo',
                                   '--email', 'o@example.com', '--display-name', 'Ega', '--password-stdin'],
                                  input='Long-Passw0rd-123\n', capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(0, done.returncode, done.stderr[-2000:])
            self.assertIn('Identity provisioned', done.stdout)
            self.assertNotIn('Long-Passw0rd-123', done.stdout + done.stderr)
            self.assertTrue((Path(d) / 'app.db').exists())


if __name__ == '__main__':
    unittest.main()
