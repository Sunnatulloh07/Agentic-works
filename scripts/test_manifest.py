"""Integrity checker regressions using temporary nonsecret local files."""
import tempfile
import unittest
from pathlib import Path
from verify_manifest import digest, verify


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'example.txt').write_text('test-data')
        self.line = digest(self.root / 'example.txt') + '  example.txt\n'
        (self.root / 'MANIFEST.sha256').write_text(self.line)
    def tearDown(self): self.tmp.cleanup()
    def test_valid_manifest(self): self.assertEqual('PASS', verify(self.root)['status'])
    def test_changed_missing_and_unlisted_files_fail(self):
        (self.root / 'example.txt').write_text('changed')
        self.assertIn('Hash mismatch: example.txt', verify(self.root)['errors'])
        (self.root / 'example.txt').unlink()
        self.assertIn('Missing file: example.txt', verify(self.root)['errors'])
        (self.root / 'other.txt').write_text('extra')
        self.assertIn('Unlisted file: other.txt', verify(self.root)['errors'])
    def test_traversal_and_duplicate_entries_fail(self):
        (self.root / 'MANIFEST.sha256').write_text(self.line + self.line + '0'*64 + '  ../outside.txt\n')
        errors=verify(self.root)['errors']
        self.assertTrue(any('Unsafe' in e for e in errors));self.assertTrue(any('Duplicate' in e for e in errors))
    def test_missing_or_malformed_manifest_fails(self):
        (self.root / 'MANIFEST.sha256').write_text('not-a-hash\n')
        self.assertEqual('FAIL',verify(self.root)['status'])
        (self.root / 'MANIFEST.sha256').unlink()
        self.assertEqual('FAIL',verify(self.root)['status'])
    def test_symlink_denied(self):
        try:
            (self.root / 'link.txt').symlink_to(self.root / 'example.txt')
        except OSError as exc:
            # Windows requires SeCreateSymbolicLinkPrivilege; the symlink rule is
            # exercised wherever the platform can actually create one.
            self.skipTest('Platform cannot create symlinks: ' + type(exc).__name__)
        self.assertIn('Symlink denied: link.txt',verify(self.root)['errors'])
    def test_explicit_build_directory_exclusion(self):
        cache=self.root/'__pycache__';cache.mkdir();(cache/'a.pyc').write_bytes(b'cache')
        self.assertEqual('PASS',verify(self.root)['status'])
