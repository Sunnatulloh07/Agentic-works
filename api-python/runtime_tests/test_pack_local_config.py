"""`config(tenant)` reads the tenant's OWN pack directory before the operator JSON.

Tamoyil 1: yangi mijoz = yangi pack katalogi. Until this contract existed, every
runtime module resolved its tenant configuration through one operator-side JSON
named by PLATFORM_INTEGRATIONS_FILE, so a measured ~88% of a non-retail tenant's
configuration lived OUTSIDE its pack directory: "yangi mijoz = yangi YAML" was
true for the retail demo and false for everything else.

The precedence pinned here:
  1. the tenant name must match the pack charset [A-Za-z0-9_-]+, checked BEFORE
     any path is built, so '..' or a separator never reaches the filesystem;
  2. <PACKS_DIR>/<tenant>/integrations.yaml, when it exists and parses to a
     mapping, IS the tenant block;
  3. otherwise the JSON named by PLATFORM_INTEGRATIONS_FILE, unchanged -- same
     reads, same messages, so no existing caller's error path moved.

A pack-local file that EXISTS but cannot be read as a mapping is refused, never
skipped: a silent fallback would let one typo in a tenant's own file be ignored
and the tenant would keep running on operator config without a word.

Dependency-free except where a test writes YAML the runtime must parse; those
skip when PyYAML is absent, because platform_runtime must stay importable -- and
its offline suite runnable -- without it.
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from platform_runtime import tools
from platform_runtime.tools import config, secret, tenant_for_instagram_account

HAS_YAML = importlib.util.find_spec('yaml') is not None
NEEDS_YAML = unittest.skipUnless(HAS_YAML, 'PyYAML is not installed')

TENANT = 't_pack'
PACK_YAML = """telegram:
  token_env: DEMO_TELEGRAM_TOKEN
  chat_id: '42'
instagram:
  account_id: '17841400000000001'
  graph_version: v21.0
  token_env: DEMO_INSTAGRAM_TOKEN
"""
JSON_BLOCK = {'telegram': {'token_env': 'OPERATOR_TELEGRAM_TOKEN'},
              'instagram': {'account_id': '17841409999999999'}}


class PackConfigCase(unittest.TestCase):
    """Fixtures only. Carries no test, so no subclass re-runs another's suite."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.packs = self.root / 'packs'
        self.packs.mkdir()

    def write_pack(self, tenant, text):
        directory = self.packs / tenant
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / 'integrations.yaml'
        target.write_text(text, encoding='utf-8')
        return target

    def write_json(self, data):
        target = self.root / 'integrations.json'
        target.write_text(json.dumps(data), encoding='utf-8')
        return target

    def env(self, **values):
        """Exact environment: the two keys under test are absent unless given."""
        base = {k: v for k, v in os.environ.items()
                if k not in ('PACKS_DIR', 'PLATFORM_INTEGRATIONS_FILE')}
        base.update(values)
        return mock.patch.dict(os.environ, base, clear=True)


class ConfigSourceTests(PackConfigCase):
    @NEEDS_YAML
    def test_pack_local_file_wins_over_the_operator_json(self):
        self.write_pack(TENANT, PACK_YAML)
        path = self.write_json({TENANT: JSON_BLOCK})
        with self.env(PACKS_DIR=str(self.packs), PLATFORM_INTEGRATIONS_FILE=str(path)):
            self.assertEqual(config(TENANT)['telegram']['token_env'], 'DEMO_TELEGRAM_TOKEN')

    def test_absent_pack_file_falls_back_to_the_json_unchanged(self):
        path = self.write_json({TENANT: JSON_BLOCK})
        with self.env(PACKS_DIR=str(self.packs), PLATFORM_INTEGRATIONS_FILE=str(path)):
            self.assertEqual(config(TENANT), JSON_BLOCK)

    def test_tenant_in_neither_source_keeps_its_message(self):
        path = self.write_json({'t_other': JSON_BLOCK})
        with self.env(PACKS_DIR=str(self.packs), PLATFORM_INTEGRATIONS_FILE=str(path)):
            with self.assertRaises(RuntimeError) as caught:
                config(TENANT)
        self.assertEqual(str(caught.exception), 'Tenant integration not configured')

    def test_no_json_env_and_no_pack_file_keeps_its_message(self):
        with self.env(PACKS_DIR=str(self.packs)):
            with self.assertRaises(RuntimeError) as caught:
                config(TENANT)
        self.assertEqual(str(caught.exception), 'Integration configuration missing')

    @NEEDS_YAML
    def test_pack_file_alone_is_enough(self):
        # The operator JSON is optional once a tenant owns its own file: this is
        # the whole point of the change, so it is asserted, not assumed.
        self.write_pack(TENANT, PACK_YAML)
        with self.env(PACKS_DIR=str(self.packs)):
            self.assertEqual(config(TENANT)['instagram']['graph_version'], 'v21.0')

    @NEEDS_YAML
    def test_pack_file_that_is_not_a_mapping_is_refused_not_skipped(self):
        path = self.write_json({TENANT: JSON_BLOCK})
        for label, text in (('list', '- telegram\n- instagram\n'),
                            ('scalar', 'just a string\n'),
                            ('empty', '')):
            with self.subTest(shape=label):
                self.write_pack(TENANT, text)
                with self.env(PACKS_DIR=str(self.packs),
                              PLATFORM_INTEGRATIONS_FILE=str(path)):
                    with self.assertRaises(RuntimeError):
                        config(TENANT)

    @NEEDS_YAML
    def test_malformed_yaml_is_refused_not_skipped(self):
        self.write_pack(TENANT, 'telegram:\n  token_env: [unclosed\n')
        path = self.write_json({TENANT: JSON_BLOCK})
        with self.env(PACKS_DIR=str(self.packs), PLATFORM_INTEGRATIONS_FILE=str(path)):
            with self.assertRaises(RuntimeError):
                config(TENANT)

    def test_traversal_tenant_is_refused_before_any_file_is_touched(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('filesystem touched for an invalid tenant name')

        path = self.write_json({TENANT: JSON_BLOCK})
        for name in ('..', '../evil', 'a/b', 'a\\b', '', 'a.b', 'demo retail'):
            with self.subTest(tenant=name):
                with self.env(PACKS_DIR=str(self.packs),
                              PLATFORM_INTEGRATIONS_FILE=str(path)):
                    with mock.patch('builtins.open', forbidden), \
                         mock.patch('pathlib.Path.is_file', forbidden):
                        with self.assertRaises(RuntimeError):
                            config(name)

    def test_missing_pyyaml_is_refused_not_skipped(self):
        # platform_runtime imports without PyYAML on purpose, but a tenant that
        # HAS a pack file must not be served stale operator config because the
        # parser is absent -- that failure is loud or it is invisible.
        self.write_pack(TENANT, PACK_YAML)
        path = self.write_json({TENANT: JSON_BLOCK})
        with self.env(PACKS_DIR=str(self.packs), PLATFORM_INTEGRATIONS_FILE=str(path)):
            with mock.patch.dict('sys.modules', {'yaml': None}):
                with self.assertRaises(RuntimeError):
                    config(TENANT)

    @NEEDS_YAML
    def test_env_name_survives_as_a_plain_dict_so_secret_still_resolves(self):
        self.write_pack(TENANT, PACK_YAML)
        with self.env(PACKS_DIR=str(self.packs), DEMO_TELEGRAM_TOKEN='7:aaa'):
            block = config(TENANT)
            self.assertIs(type(block), dict)
            self.assertIs(type(block['telegram']), dict)
            self.assertEqual(secret(block['telegram'], 'token_env'), '7:aaa')

    @NEEDS_YAML
    def test_every_call_rereads_the_file(self):
        # Nothing is cached: PACKS_DIR is environment, and tests -- as well as
        # operators reloading a pack -- change it between calls.
        self.write_pack(TENANT, PACK_YAML)
        with self.env(PACKS_DIR=str(self.packs)):
            self.assertEqual(config(TENANT)['telegram']['token_env'], 'DEMO_TELEGRAM_TOKEN')
            self.write_pack(TENANT, 'telegram:\n  token_env: ROTATED_TOKEN\n')
            self.assertEqual(config(TENANT)['telegram']['token_env'], 'ROTATED_TOKEN')

    def test_default_packs_dir_is_the_repository_packs_directory(self):
        # Mirrors app/packs.py without importing it: platform_runtime must not
        # depend on the API layer.
        expected = Path(tools.__file__).resolve().parents[2] / 'packs'
        self.assertEqual(tools.DEFAULT_PACKS_DIR, expected)
        with self.env():
            self.assertEqual(tools.pack_integrations_path(TENANT),
                             (expected / TENANT / 'integrations.yaml').resolve())


class InstagramRoutingTests(PackConfigCase):
    """Inbound Meta routing still resolves through the operator JSON only.

    Documented limitation, pinned rather than discovered: `tenant_for_instagram_
    account` maps an account id to a tenant with no tenant in hand, so it cannot
    consult <PACKS_DIR>/<tenant>/ -- it would have to enumerate and parse every
    pack on every webhook. A tenant whose integrations moved entirely into its
    pack is therefore NOT routable inbound until its instagram.account_id is
    also listed in PLATFORM_INTEGRATIONS_FILE.
    """

    def test_routing_still_reads_the_operator_json(self):
        path = self.write_json({TENANT: JSON_BLOCK})
        with self.env(PACKS_DIR=str(self.packs), PLATFORM_INTEGRATIONS_FILE=str(path)):
            self.assertEqual(tenant_for_instagram_account('17841409999999999'), TENANT)

    @NEEDS_YAML
    def test_routing_does_not_yet_see_pack_local_accounts(self):
        self.write_pack(TENANT, PACK_YAML)
        path = self.write_json({'t_other': {'instagram': {'account_id': '1'}}})
        with self.env(PACKS_DIR=str(self.packs), PLATFORM_INTEGRATIONS_FILE=str(path)):
            with self.assertRaises(RuntimeError) as caught:
                tenant_for_instagram_account('17841400000000001')
        self.assertEqual(str(caught.exception), 'Ambiguous or unknown Meta account')


if __name__ == '__main__':
    unittest.main()
