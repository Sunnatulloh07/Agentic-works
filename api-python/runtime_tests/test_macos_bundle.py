import importlib.util
from pathlib import Path
import plistlib
import shutil
import sys
import tempfile
import unittest

script=Path(__file__).resolve().parents[2]/'scripts'/'prepare_runner_macos.py'
spec=importlib.util.spec_from_file_location('macos_bundle',script);bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.allowed=self.root/'allowed';self.allowed.mkdir()
        self.output=self.root/'bundle';self.node=shutil.which('node')
    def prepare(self,**overrides):
        args={'output':self.output,'folders':[self.allowed],'server':'wss://example.invalid/platform/runner/ws','node':self.node,'python':sys.executable}
        return bundle.prepare(**{**args,**overrides})
    def test_bundle_has_no_embedded_token_or_install_action(self):
        result=self.prepare();self.assertFalse(result['installed']);self.assertFalse(result['signed'])
        plist=plistlib.loads(Path(result['plist']).read_bytes());env=plist['EnvironmentVariables']
        self.assertNotIn('RUNNER_TOKEN',env);self.assertFalse(Path(env['RUNNER_TOKEN_FILE']).exists())
        self.assertEqual([str(Path(self.node).resolve()),str(self.output/'app'/'runner.js')],plist['ProgramArguments'])
    def test_existing_bundle_not_overwritten(self):
        self.prepare()
        with self.assertRaises(ValueError):self.prepare()
    def test_insecure_endpoint_denied(self):
        for server in ['ws://remote.example','wss://user:secret@host','wss://host?token=x']:
            with self.subTest(server=server),self.assertRaises(ValueError):self.prepare(server=server)
        self.assertFalse(self.output.exists())
    def test_all_files_private(self):
        self.prepare()
        for path in self.output.rglob('*'):
            if path.name=='README-UZ.md':continue
            self.assertEqual(0,path.stat().st_mode & 0o077,str(path))
    def test_whole_filesystem_root_denied(self):
        with self.assertRaises(ValueError):self.prepare(folders=['/'])
