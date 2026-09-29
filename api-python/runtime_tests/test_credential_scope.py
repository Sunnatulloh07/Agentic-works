"""A tenant's configuration may name only the environment variables that are its own.

Measured before the fix: ``secret(cfg, name)`` read ``os.environ[cfg[name]]``
after one check -- that the reference is spelled like a variable name. A tenant
that authors its own ``packs/<tenant>/integrations.yaml`` could therefore point
``telegram.token_env`` at JWT_SECRET, ADMIN_TOKEN or another tenant's bot token,
and with ``telegram.base_url`` on its own HTTPS host receive the value in a
request path.

The rules pinned here:

* platform secrets (JWT_SECRET, ADMIN_TOKEN, TELEGRAM_WEBHOOK_SECRET,
  TENANT_SECRETS, META_SECRETS, REDIS_URL, PLATFORM_VAULT_*) are refused for
  every configuration source, in ``config()`` and again in ``secret()``;
* a pack-local file may name only ``TENANT_<TENANT>__*`` variables, or names the
  operator lists in PLATFORM_SHARED_SECRET_NAMES -- never a platform secret nor
  the deployment webhook secrets META_APP_SECRET / META_VERIFY_TOKEN;
* the operator JSON (PLATFORM_INTEGRATIONS_FILE) stays trusted for every other
  name: it is operator-authored;
* every refusal names the key and the rule, never a value.
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import ConfigError, validate_runtime_config
from platform_runtime.tools import (CredentialScopeError, check_credential_scope, config,
                                    credential_references, integration_status, secret,
                                    tenant_credential_prefix)

NEEDS_YAML = unittest.skipUnless(importlib.util.find_spec('yaml') is not None, 'PyYAML is not installed')

TENANT = 'demo-retail'
OWN = 'TENANT_DEMO_RETAIL__TELEGRAM_TOKEN'
VALUE = 'sk-live-ValueNeverShown-0123456789'
PLATFORM = ('JWT_SECRET', 'ADMIN_TOKEN', 'TELEGRAM_WEBHOOK_SECRET', 'TENANT_SECRETS', 'META_SECRETS',
            'REDIS_URL', 'PLATFORM_VAULT_KEYS_JSON', 'PLATFORM_VAULT_ACTIVE_KEY')
DEPLOYMENT = ('META_APP_SECRET', 'META_VERIFY_TOKEN')
# Where a reference can sit: the four shapes the runtime's readers use.
PLACES = ('telegram:\n  token_env: {}\n',
          'whatsapp_webhook:\n  app_secret_env: {}\n',
          'whatsapp_tokens:\n  main:\n    access: {}\n',
          'crm:\n  headers:\n    Authorization:\n      env: {}\n')


class ScopeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packs = Path(self.tmp.name) / 'packs'
        self.packs.mkdir()

    def write_pack(self, text, tenant=TENANT):
        directory = self.packs / tenant
        directory.mkdir(exist_ok=True)
        (directory / 'integrations.yaml').write_text(text, encoding='utf-8')

    def write_json(self, data):
        target = Path(self.tmp.name) / 'integrations.json'
        target.write_text(json.dumps(data), encoding='utf-8')
        return str(target)

    def env(self, **values):
        """Exact environment: every variable under test is absent unless given."""
        drop = ('PACKS_DIR', 'PLATFORM_INTEGRATIONS_FILE', 'PLATFORM_SHARED_SECRET_NAMES', OWN) + PLATFORM + DEPLOYMENT
        base = {k: v for k, v in os.environ.items() if k not in drop}
        base.update(PACKS_DIR=str(self.packs), **values)
        return mock.patch.dict(os.environ, base, clear=True)

    def refused(self, tenant=TENANT):
        with self.assertRaises(CredentialScopeError) as caught:
            config(tenant)
        return str(caught.exception)


class NamespaceTests(unittest.TestCase):
    def test_the_prefix_is_the_tenant_name_in_upper_snake_then_a_double_underscore(self):
        self.assertEqual('TENANT_DEMO_RETAIL__', tenant_credential_prefix('demo-retail'))
        self.assertEqual('TENANT_T_PACK__', tenant_credential_prefix('t_pack'))
        self.assertEqual('TENANT_ACME2__', tenant_credential_prefix('Acme2'))

    def test_a_name_that_could_overlap_another_namespace_has_none(self):
        # With no doubled or edge separator in the tenant part and none in the
        # suffix, a reference has exactly one '__' and so one owner.
        for tenant in ('a--b', 'a_-b', '-a', 'a_', 'a__b'):
            with self.subTest(tenant=tenant):
                self.assertIsNone(tenant_credential_prefix(tenant))


class ReferenceTests(unittest.TestCase):
    def test_every_reader_shape_is_found_and_ordinary_values_are_not(self):
        data = {'telegram': {'token_env': 'A_TOKEN', 'base_url': 'https://x.example', 'chat_id': '42'},
                'crm': {'headers': {'Authorization': {'env': 'B_TOKEN', 'prefix': 'Bearer '}}},
                'whatsapp_tokens': {'main': {'access': 'C_TOKEN'}},
                'db': [{'password_env': 'D_PASSWORD'}],
                'sheets': {'range': 'Sheet1!A:D', 'majorDimension': 'ROWS', 'currency': 'USD'},
                'llm': {'model': 'SET_YOUR_AVAILABLE_MODEL_ID', 'key_env': 'lowercase-not-a-name'}}
        with mock.patch.dict(os.environ, {'E_SET_ELSEWHERE': 'x'}):
            data['custom'] = {'header_value': 'E_SET_ELSEWHERE'}
            found = sorted(credential_references(data))
        self.assertEqual([('crm.headers.Authorization.env', 'B_TOKEN'), ('custom.header_value', 'E_SET_ELSEWHERE'),
                          ('db.0.password_env', 'D_PASSWORD'), ('telegram.token_env', 'A_TOKEN'),
                          ('whatsapp_tokens.main.access', 'C_TOKEN')], found)

    def test_nesting_is_bounded_and_refused_as_configuration(self):
        data = value = {}
        for _ in range(200):
            value['x'] = {}
            value = value['x']
        with self.assertRaises(CredentialScopeError):
            list(credential_references(data))


@NEEDS_YAML
class PackLocalTests(ScopeCase):
    def test_a_pack_may_name_its_own_namespace(self):
        self.write_pack(f'telegram:\n  token_env: {OWN}\n')
        with self.env(**{OWN: VALUE}):
            self.assertEqual(VALUE, secret(config(TENANT)['telegram'], 'token_env'))

    def test_a_platform_or_deployment_secret_is_refused_wherever_it_sits(self):
        for name in PLATFORM + DEPLOYMENT:
            for place in PLACES:
                with self.subTest(name=name, place=place.split(':')[0]):
                    self.write_pack(place.format(name))
                    with self.env(**{name: VALUE}):
                        message = self.refused()
                    self.assertIn('platform secret', message)
                    self.assertIn('integrations.yaml', message)
                    self.assertNotIn(VALUE, message)

    def test_a_name_outside_the_tenant_namespace_is_refused(self):
        for name in ('OTHER_SHOP_TOKEN', 'TENANT_ACME__TELEGRAM_TOKEN', 'TENANT_DEMO__TOKEN',
                     'TENANT_DEMO_RETAIL___TOKEN', 'TENANT_DEMO_RETAIL__A__B', 'TENANT_DEMO_RETAIL__'):
            with self.subTest(name=name):
                self.write_pack(f'telegram:\n  token_env: {name}\n')
                with self.env(**{name: VALUE}):
                    message = self.refused()
                self.assertIn('telegram.token_env', message)
                self.assertIn('TENANT_DEMO_RETAIL__', message)
                self.assertIn('PLATFORM_SHARED_SECRET_NAMES', message)
                self.assertNotIn(VALUE, message)

    def test_another_tenants_namespace_is_not_this_tenants(self):
        # 'demo' must not reach 'demo-retail': TENANT_DEMO__ is not a prefix of TENANT_DEMO_RETAIL__.
        self.write_pack(f'telegram:\n  token_env: {OWN}\n', tenant='demo')
        with self.env(**{OWN: VALUE}):
            self.assertIn('TENANT_DEMO__', self.refused('demo'))

    def test_the_operator_may_share_names_with_every_pack(self):
        self.write_pack('llm:\n  key_env: PLATFORM_LLM_KEY\ntelegram:\n  token_env: DEMO_TELEGRAM_TOKEN\n')
        with self.env(PLATFORM_SHARED_SECRET_NAMES=' PLATFORM_LLM_KEY , DEMO_TELEGRAM_TOKEN',
                      PLATFORM_LLM_KEY=VALUE):
            self.assertEqual(VALUE, secret(config(TENANT)['llm'], 'key_env'))

    def test_the_shared_list_can_never_carry_a_platform_secret(self):
        self.write_pack('telegram:\n  token_env: JWT_SECRET\n')
        for listed in ('JWT_SECRET', 'META_APP_SECRET', 'PLATFORM_VAULT_KEYS_JSON', VALUE):
            with self.subTest(listed=listed):
                with self.env(PLATFORM_SHARED_SECRET_NAMES='PLATFORM_LLM_KEY,' + listed, JWT_SECRET=VALUE):
                    message = self.refused()
                self.assertIn('PLATFORM_SHARED_SECRET_NAMES', message)
                self.assertNotIn(VALUE, message)

    def test_a_tenant_without_a_namespace_may_use_shared_names_only(self):
        self.write_pack('telegram:\n  token_env: TENANT_A___TOKEN\n', tenant='a_')
        with self.env(TENANT_A___TOKEN=VALUE):
            self.assertIn('namespace', self.refused('a_'))
        self.write_pack('telegram:\n  token_env: DEMO_TELEGRAM_TOKEN\n', tenant='a_')
        with self.env(PLATFORM_SHARED_SECRET_NAMES='DEMO_TELEGRAM_TOKEN'):
            self.assertEqual('DEMO_TELEGRAM_TOKEN', config('a_')['telegram']['token_env'])

    def test_the_shipped_pack_template_follows_the_rule_it_teaches(self):
        import yaml
        template = Path(__file__).resolve().parents[2] / 'packs' / '_template' / 'integrations.example.yaml'
        data = yaml.safe_load(template.read_text(encoding='utf-8'))
        with self.env():
            check_credential_scope(TENANT, data, pack=True)
        self.assertTrue(all(ref.startswith('TENANT_DEMO_RETAIL__') for _, ref in credential_references(data)))

    def test_a_set_variable_under_an_unusual_key_is_still_a_reference(self):
        # A reader added later under a new key convention cannot slip past.
        self.write_pack('crm:\n  auth_header_from: ADMIN_TOKEN\n')
        with self.env(ADMIN_TOKEN=VALUE):
            self.assertIn('crm.auth_header_from', self.refused())
        self.write_pack('sheets:\n  currency: USD\n  majorDimension: ROWS\n')
        with self.env():
            self.assertEqual('USD', config(TENANT)['sheets']['currency'])


@NEEDS_YAML
class StatusTests(ScopeCase):
    """A status page reports every violation by key; config() still refuses the file."""

    def test_every_violation_is_listed_by_key_and_name_never_by_value(self):
        self.write_pack(f'telegram:\n  token_env: JWT_SECRET\ninstagram:\n  token_env: {OWN}\n'
                        'llm:\n  key_env: OTHER_KEY\n')
        with self.env(JWT_SECRET=VALUE, **{OWN: VALUE}):
            cfg, violations = integration_status(TENANT)
            self.refused()
        self.assertEqual(OWN, cfg['instagram']['token_env'])
        self.assertEqual([('llm.key_env', 'OTHER_KEY'), ('telegram.token_env', 'JWT_SECRET')],
                         sorted((path, name) for path, name, _ in violations))
        self.assertNotIn(VALUE, repr(violations))

    def test_a_clean_file_has_no_violations(self):
        self.write_pack(f'telegram:\n  token_env: {OWN}\n')
        with self.env():
            self.assertEqual([], integration_status(TENANT)[1])


class OperatorJsonTests(ScopeCase):
    def test_the_operator_json_is_trusted_for_unprefixed_names(self):
        path = self.write_json({TENANT: {'telegram': {'token_env': 'DEMO_TELEGRAM_TOKEN'},
                                         'whatsapp_webhook': {'app_secret_env': 'META_APP_SECRET'}}})
        with self.env(PLATFORM_INTEGRATIONS_FILE=path, DEMO_TELEGRAM_TOKEN=VALUE, META_APP_SECRET=VALUE):
            cfg = config(TENANT)
            self.assertEqual(VALUE, secret(cfg['telegram'], 'token_env'))
            self.assertEqual(VALUE, secret(cfg['whatsapp_webhook'], 'app_secret_env'))

    def test_the_operator_json_still_may_not_name_a_platform_secret(self):
        path = self.write_json({TENANT: {'telegram': {'token_env': 'ADMIN_TOKEN'}}})
        with self.env(PLATFORM_INTEGRATIONS_FILE=path, ADMIN_TOKEN=VALUE):
            message = self.refused()
        self.assertIn('telegram.token_env', message)
        self.assertNotIn(VALUE, message)


class SecretFloorTests(unittest.TestCase):
    def test_secret_refuses_a_platform_secret_whoever_built_the_block(self):
        # Several readers build the block themselves: secret({'value': ref}, 'value').
        for name in PLATFORM:
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: VALUE}):
                with self.assertRaises(CredentialScopeError) as caught:
                    secret({'value': name}, 'value')
                self.assertNotIn(VALUE, str(caught.exception))
                self.assertIn('platform secret', str(caught.exception))

    def test_secret_still_reads_an_integration_credential(self):
        with mock.patch.dict(os.environ, {'DEMO_TELEGRAM_TOKEN': VALUE, 'META_APP_SECRET': VALUE}):
            self.assertEqual(VALUE, secret({'token_env': 'DEMO_TELEGRAM_TOKEN'}, 'token_env'))
            self.assertEqual(VALUE, secret({'app_secret_env': 'META_APP_SECRET'}, 'app_secret_env'))


class StartupTests(unittest.TestCase):
    ENV = {'ENV': 'test', 'ALLOW_INSECURE_DEV': 'true'}

    def test_startup_refuses_a_shared_list_that_names_a_platform_secret(self):
        for listed in ('JWT_SECRET', 'PLATFORM_LLM_KEY,ADMIN_TOKEN', 'META_VERIFY_TOKEN', 'not a name'):
            with self.subTest(listed=listed), self.assertRaises(ConfigError) as caught:
                validate_runtime_config({**self.ENV, 'PLATFORM_SHARED_SECRET_NAMES': listed})
            self.assertIn('PLATFORM_SHARED_SECRET_NAMES', str(caught.exception))

    def test_startup_accepts_integration_names(self):
        validate_runtime_config({**self.ENV, 'PLATFORM_SHARED_SECRET_NAMES': 'PLATFORM_LLM_KEY, DEMO_TELEGRAM_TOKEN'})
        validate_runtime_config(dict(self.ENV))


if __name__ == '__main__':
    unittest.main()
