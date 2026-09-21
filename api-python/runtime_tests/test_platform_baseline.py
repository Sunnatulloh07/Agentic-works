"""The Windows-blocked surface of this suite, recorded as a number (§154).

Twelve tests in ``runtime_tests`` assert POSIX behaviour that this platform does
not implement.  The inventory recorded that surface as
``failures=0, errors=11, skipped=2``.  The measured signature is
``failures=1, errors=11, skipped=1``: the error count was right, and the other two
fields were not.  ``test_macos_bundle.test_all_files_private`` is a *failure*
rather than an error -- it asserts permission bits instead of calling a missing
primitive, so it crashes differently and fell outside the count -- and the second
skip does not exist; ``test_whatsapp_inbound`` carries the suite's only
``skipTest``, and it skips for a runtime reason, not a platform one.

A number nobody checks drifts, and this one had drifted inside the document that
was supposed to be the record.  This module makes the blocked surface a fact the
suite asserts, and it asserts it by **reason** rather than by re-running the
tests: on Windows each recorded reason must still hold, and on POSIX each must
stop holding.  So if someone later shims ``os.O_NOFOLLOW``, or the project moves
to a Linux runner, this module fails and says the record is stale -- which is the
only way the record stays true.

Skipping is not passing, and nothing here pretends otherwise.  These twelve are
*unverified* on Windows, not verified-green.  The one thing this module refuses to
let happen is for that to be discovered by reading a CI log.

Measured by full-suite discovery::

    python -m unittest discover -s runtime_tests
    # Ran 2949 tests ... FAILED (failures=1, errors=11, skipped=1)
"""
import importlib.util
import os
import unittest

# Reason keys -> the platform fact that blocks the test.  A test is blocked when
# the predicate returns True, i.e. when the primitive it needs is unavailable.
REASONS = {
    'O_NOFOLLOW': lambda: not hasattr(os, 'O_NOFOLLOW'),
    'mkfifo': lambda: not hasattr(os, 'mkfifo'),
    'fcntl': lambda: importlib.util.find_spec('fcntl') is None,
    'posix': lambda: os.name != 'posix',
}

# test id -> reason.  Seven of the twelve need the same primitive, which is the
# finding: ``os.O_NOFOLLOW`` is the single largest reason this suite cannot be
# verified here, because it is what makes an open() refuse to follow a symlink.
BLOCKED = {
    # portable_fs: the helper opens by descriptor and relies on O_NOFOLLOW to
    # make the descriptor identity check meaningful.
    'runtime_tests.test_portable_fs.PortableFSTests.test_binary_and_large_files_denied': 'O_NOFOLLOW',
    'runtime_tests.test_portable_fs.PortableFSTests.test_descriptor_identity_rechecked': 'O_NOFOLLOW',
    'runtime_tests.test_portable_fs.PortableFSTests.test_directory_bounded': 'O_NOFOLLOW',
    'runtime_tests.test_portable_fs.PortableFSTests.test_escape_symlink_denied': 'O_NOFOLLOW',
    'runtime_tests.test_portable_fs.PortableFSTests.test_hardlink_denied': 'O_NOFOLLOW',
    'runtime_tests.test_portable_fs.PortableFSTests.test_list_real_directory': 'O_NOFOLLOW',
    'runtime_tests.test_portable_fs.PortableFSTests.test_read_real_file': 'O_NOFOLLOW',
    # portable_fs: special files that Windows has no way to create.
    'runtime_tests.test_portable_fs.PortableFSTests.test_fifo_does_not_block': 'mkfifo',
    'runtime_tests.test_portable_fs.PortableFSTests.test_unsafe_entries_hidden': 'mkfifo',
    # portable_fs: the Darwin contract is asserted through the real fcntl module.
    'runtime_tests.test_portable_fs.PortableFSTests.test_darwin_getpath_contract': 'fcntl',
    # foundation: mount scoping is POSIX path semantics.
    'runtime_tests.test_foundation_v02.ConnectorTests.test_mount_scope': 'posix',
    # macos_bundle: asserts 0o077 permission bits, which Windows does not model.
    'runtime_tests.test_macos_bundle.BundleTests.test_all_files_private': 'posix',
}

# The signature this list was measured against.  Recorded so the inventory can be
# corrected rather than re-derived.
RECORDED_SIGNATURE = {'tests': 2949, 'failures': 1, 'errors': 11, 'skipped': 1}

# What the inventory claimed, kept here so the correction is visible as a
# correction rather than as an unexplained difference between two documents.
INVENTORY_CLAIMED = {'failures': 0, 'errors': 11, 'skipped': 2}


class BlockedSurfaceTests(unittest.TestCase):
    def test_the_blocked_surface_is_twelve_tests(self):
        self.assertEqual(12, len(BLOCKED))

    def test_the_recorded_signature_matches_the_blocked_surface(self):
        """errors + failures must equal the blocked list, or the record is wrong."""
        self.assertEqual(len(BLOCKED),
                         RECORDED_SIGNATURE['failures'] + RECORDED_SIGNATURE['errors'])

    def test_the_inventory_undercounted_and_the_correction_is_deliberate(self):
        """The inventory is wrong in two fields; this pins what it got wrong."""
        self.assertNotEqual(INVENTORY_CLAIMED, RECORDED_SIGNATURE)
        self.assertEqual(INVENTORY_CLAIMED['errors'], RECORDED_SIGNATURE['errors'],
                         'the error count was the one field the inventory got right')
        self.assertEqual(1, RECORDED_SIGNATURE['failures'])
        self.assertEqual(0, INVENTORY_CLAIMED['failures'])
        self.assertEqual(1, RECORDED_SIGNATURE['skipped'])
        self.assertEqual(2, INVENTORY_CLAIMED['skipped'])

    def test_every_reason_is_a_known_platform_fact(self):
        for test_id, reason in BLOCKED.items():
            with self.subTest(test=test_id):
                self.assertIn(reason, REASONS)

    def test_one_missing_primitive_dominates(self):
        """Documents the finding rather than leaving it to be re-measured."""
        by_reason = {}
        for reason in BLOCKED.values():
            by_reason[reason] = by_reason.get(reason, 0) + 1
        self.assertEqual({'O_NOFOLLOW': 7, 'mkfifo': 2, 'fcntl': 1, 'posix': 2}, by_reason)

    def test_each_recorded_reason_holds_exactly_on_the_platforms_that_lack_it(self):
        """On Windows every reason applies; on POSIX none may.

        This is the assertion that keeps the record honest in both directions.  A
        reason that stops applying on Windows means the primitive was shimmed and
        the test can run; a reason that *still* applies on POSIX means the test is
        blocked for a non-platform reason and the inventory is misattributing it.
        """
        windows = os.name == 'nt'
        for reason, blocked in REASONS.items():
            with self.subTest(reason=reason):
                if windows:
                    self.assertTrue(blocked(),
                                    f'{reason} is available on Windows; '
                                    f'the record is stale and this test can run')
                else:
                    self.assertFalse(blocked(),
                                     f'{reason} is unavailable on POSIX; '
                                     f'this is not a platform limitation')

    def test_the_ids_are_real_importable_test_ids(self):
        """A typo would make the list document nothing while still counting to 12."""
        for test_id in BLOCKED:
            with self.subTest(test=test_id):
                module_name, class_name, method = test_id.rsplit('.', 2)
                self.assertTrue(importlib.util.find_spec(module_name),
                                f'{module_name} is not importable')
                module = importlib.import_module(module_name)
                self.assertTrue(hasattr(module, class_name), f'{class_name} missing')
                self.assertTrue(hasattr(getattr(module, class_name), method),
                                f'{method} missing from {class_name}')


if __name__ == '__main__':
    unittest.main()
