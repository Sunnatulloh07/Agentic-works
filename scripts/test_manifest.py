"""Integrity checker regressions using temporary nonsecret local files."""
import tempfile
import unittest
from pathlib import Path

from generate_manifest import render, write
from verify_manifest import digest, ignored_paths, verify


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
        # A successful call is not proof a symlink exists. On a Windows host without the
        # privilege the call can return without raising and leave nothing behind, and the
        # assertion then failed with an empty error list -- which reads as "the guard is
        # broken" when the truth is "the fixture was never built". Verified, not assumed.
        if not (self.root / 'link.txt').is_symlink():
            self.skipTest('Platform silently did not create a symlink')
        self.assertIn('Symlink denied: link.txt',verify(self.root)['errors'])
    def test_explicit_build_directory_exclusion(self):
        cache=self.root/'__pycache__';cache.mkdir();(cache/'a.pyc').write_bytes(b'cache')
        self.assertEqual('PASS',verify(self.root)['status'])


class ManifestGeneratorTests(unittest.TestCase):
    """The generator exists so the gate can be satisfied inside the working tree.

    Before it, the only way to produce a manifest was from outside the repository, so
    the gate could not be run where the code is written and sat red through every phase.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'a.txt').write_text('alpha')
        (self.root / 'sub').mkdir()
        (self.root / 'sub' / 'b.txt').write_text('beta')
        (self.root / '__pycache__').mkdir()
        (self.root / '__pycache__' / 'x.pyc').write_bytes(b'cache')
    def tearDown(self): self.tmp.cleanup()
    def test_generate_then_verify_passes(self):
        write(self.root, render(self.root))
        result = verify(self.root)
        self.assertEqual('PASS', result['status'], result['errors'])
        self.assertEqual(2, result['checked'])
    def test_render_is_sorted_and_excludes_build_output(self):
        names = [line.split('  ', 1)[1] for line in render(self.root)]
        self.assertEqual(['a.txt', 'sub/b.txt'], names)
    def test_render_is_deterministic(self):
        self.assertEqual(render(self.root), render(self.root))
    def test_render_skips_symlinks_the_checker_would_deny(self):
        try:
            (self.root / 'link.txt').symlink_to(self.root / 'a.txt')
        except OSError as exc:
            self.skipTest('Platform cannot create symlinks: ' + type(exc).__name__)
        if not (self.root / 'link.txt').is_symlink():
            self.skipTest('Platform silently did not create a symlink')
        self.assertEqual(['a.txt', 'sub/b.txt'], [line.split('  ', 1)[1] for line in render(self.root)])
    def test_check_mode_reports_staleness_without_writing(self):
        write(self.root, render(self.root))
        before = (self.root / 'MANIFEST.sha256').read_bytes()
        (self.root / 'new.txt').write_text('new')
        self.assertNotEqual(render(self.root),
                            (self.root / 'MANIFEST.sha256').read_text(encoding='utf-8').splitlines())
        self.assertEqual(before, (self.root / 'MANIFEST.sha256').read_bytes())
    def test_ignored_paths_is_empty_outside_a_repository(self):
        self.assertEqual(set(), ignored_paths(self.root))
    def test_ignored_paths_does_not_inherit_an_outer_repository(self):
        """``git -C`` walks upward, so a directory inside another checkout must not
        inherit that checkout's ignore rules -- it would silently shrink the file set."""
        outer = Path(__file__).resolve().parents[1]
        if not (outer / '.git').exists():
            self.skipTest('not a git checkout')
        with tempfile.TemporaryDirectory(dir=outer) as nested:
            self.assertEqual(set(), ignored_paths(Path(nested)))
    def test_gitignore_is_honoured_inside_the_repository(self):
        outer = Path(__file__).resolve().parents[1]
        if not (outer / '.git').exists() or ignored_paths(outer) == set():
            self.skipTest('no ignored files present in this checkout')
        self.assertTrue(any(name.endswith('.pyc') or '.pytest_cache' in name
                            for name in ignored_paths(outer)))
