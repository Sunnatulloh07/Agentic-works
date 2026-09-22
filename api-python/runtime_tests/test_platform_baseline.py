"""The Windows-blocked surface of this suite, recorded as a number (§154, §155).

Thirteen tests in ``runtime_tests`` assert POSIX behaviour that this platform does
not implement.  The inventory recorded that surface as
``failures=0, errors=11, skipped=2``.  The measured signature is
``failures=1, errors=12, skipped=1``: the other two fields were wrong, and the
error count itself was wrong by one.  ``test_macos_bundle.test_all_files_private``
is a *failure* rather than an error -- it asserts permission bits instead of
calling a missing primitive, so it crashes differently and fell outside the count.

§155: this module was written to stop exactly that kind of drift, and it drifted
anyway -- twice.  The first version recorded **twelve** tests and attributed seven
of them to ``os.O_NOFOLLOW``.  Measured against the tracebacks, the truth is
**thirteen**, with ``O_NOFOLLOW`` blocking **six**:

* ``test_root_symlink_replacement_denied`` was not in the list at all;
* ``test_escape_symlink_denied`` was listed as ``O_NOFOLLOW`` but never reaches
  that code -- it dies earlier, in its own setup, on ``os.symlink``;
* both of those die on a *different* platform fact that the record had no name
  for: the Windows symlink privilege, ``OSError`` with ``WinError 1314``.

The reason the first version could not see this is the point of the phase, and it
is worth stating plainly because it is a general trap:

    It checked that a precondition was TRUE.  It never checked that the
    precondition was WHY the test failed.

``not hasattr(os, 'O_NOFOLLOW')`` is true on this host whether or not it is the
thing that killed a given test, so a misattributed row satisfies the predicate and
passes.  ``test_each_recorded_reason_is_the_actual_cause`` closes that hole: it
**runs** every blocked test and matches the exception it actually raises against
the reason on its row.  A reason that is merely plausible now fails.

Skipping is not passing, and nothing here pretends otherwise.  These thirteen are
*unverified* on Windows, not verified-green.  The one thing this module refuses to
let happen is for that to be discovered by reading a CI log.

Measured by full-suite discovery::

    python -m unittest discover -s runtime_tests
    # Ran 3000 tests ... FAILED (failures=1, errors=12, skipped=1)
"""
import importlib
import importlib.util
import os
import re
import unittest
from pathlib import Path

# Reason keys -> the platform fact that blocks the test.  A test is blocked when
# the predicate returns True, i.e. when the primitive it needs is unavailable.
REASONS = {
    'O_NOFOLLOW': lambda: not hasattr(os, 'O_NOFOLLOW'),
    'mkfifo': lambda: not hasattr(os, 'mkfifo'),
    'fcntl': lambda: importlib.util.find_spec('fcntl') is None,
    'symlink_privilege': lambda: os.name == 'nt',
    'posix': lambda: os.name != 'posix',
}

# How to recognise each reason in the wild, applied to the exception a blocked
# test actually raises.  This is what turns "the precondition holds" into "the
# precondition is the cause"; see the module docstring.
CAUSES = {
    'O_NOFOLLOW': lambda exc: (isinstance(exc, AttributeError)
                               and 'O_NOFOLLOW' in str(exc)),
    'mkfifo': lambda exc: (isinstance(exc, AttributeError)
                           and 'mkfifo' in str(exc)),
    'fcntl': lambda exc: (isinstance(exc, ModuleNotFoundError)
                          and 'fcntl' in str(exc)),
    # Windows refuses os.symlink without Developer Mode or an elevated token.
    'symlink_privilege': lambda exc: (isinstance(exc, OSError)
                                      and getattr(exc, 'winerror', None) == 1314),
    # Path semantics and permission-bit modelling fail as ordinary errors, not as
    # missing attributes: ``/var`` is not absolute here, and st_mode has no group
    # or other bits to clear.  Both arrive as ValueError/AssertionError.
    'posix': lambda exc: not isinstance(exc, (AttributeError, ModuleNotFoundError)),
}

# test id -> reason.  Six of the thirteen need the same primitive, which is the
# finding: ``os.O_NOFOLLOW`` is the single largest reason this suite cannot be
# verified here, because it is what makes an open() refuse to follow a symlink.
BLOCKED = {
    # portable_fs: the helper opens by descriptor and relies on O_NOFOLLOW to
    # make the descriptor identity check meaningful.
    'runtime_tests.test_portable_fs.PortableFSTests.test_binary_and_large_files_denied': 'O_NOFOLLOW',
    'runtime_tests.test_portable_fs.PortableFSTests.test_descriptor_identity_rechecked': 'O_NOFOLLOW',
    'runtime_tests.test_portable_fs.PortableFSTests.test_directory_bounded': 'O_NOFOLLOW',
    'runtime_tests.test_portable_fs.PortableFSTests.test_hardlink_denied': 'O_NOFOLLOW',
    'runtime_tests.test_portable_fs.PortableFSTests.test_list_real_directory': 'O_NOFOLLOW',
    'runtime_tests.test_portable_fs.PortableFSTests.test_read_real_file': 'O_NOFOLLOW',
    # portable_fs: special files that Windows has no way to create.
    'runtime_tests.test_portable_fs.PortableFSTests.test_fifo_does_not_block': 'mkfifo',
    'runtime_tests.test_portable_fs.PortableFSTests.test_unsafe_entries_hidden': 'mkfifo',
    # portable_fs: the Darwin contract is asserted through the real fcntl module.
    'runtime_tests.test_portable_fs.PortableFSTests.test_darwin_getpath_contract': 'fcntl',
    # portable_fs: these two build a symlink in their OWN setup, so they die before
    # any descriptor is opened.  On a host that permits symlink creation they would
    # instead reach O_NOFOLLOW -- at which point this module fails and asks to be
    # re-measured, which is the intended behaviour, not a flake.
    'runtime_tests.test_portable_fs.PortableFSTests.test_escape_symlink_denied': 'symlink_privilege',
    'runtime_tests.test_portable_fs.PortableFSTests.test_root_symlink_replacement_denied': 'symlink_privilege',
    # foundation: mount scoping is POSIX path semantics -- PLATFORM_DB_ROOTS='/var'
    # is an absolute root on POSIX and a relative one here.
    'runtime_tests.test_foundation_v02.ConnectorTests.test_mount_scope': 'posix',
    # macos_bundle: asserts 0o077 permission bits, which Windows does not model.
    'runtime_tests.test_macos_bundle.BundleTests.test_all_files_private': 'posix',
}

# The signature this list was measured against.  Recorded so the inventory can be
# corrected rather than re-derived.
RECORDED_SIGNATURE = {'tests': 3000, 'failures': 1, 'errors': 12, 'skipped': 1}

# What the inventory claimed, kept here so the correction is visible as a
# correction rather than as an unexplained difference between two documents.
INVENTORY_CLAIMED = {'failures': 0, 'errors': 11, 'skipped': 2}

# §154 recorded this; §155 corrected it.  Kept so the second correction is
# auditable rather than being a silent edit to a number.
FIRST_RECORD = {'blocked': 12, 'by_reason': {'O_NOFOLLOW': 7, 'mkfifo': 2,
                                             'fcntl': 1, 'posix': 2},
                'signature': {'tests': 2949, 'failures': 1, 'errors': 11,
                              'skipped': 1}}


class _Capture(unittest.TestResult):
    """A result that keeps the exception OBJECT.

    ``TestResult.errors`` holds ``(test, formatted_traceback)`` -- strings, not
    exceptions -- so the object has to be taken in ``addError``. Reading it back
    out of the list gives you a character of the traceback, which is exactly the
    kind of plausible-looking wrong answer this module exists to prevent.
    """

    def __init__(self):
        super().__init__()
        self.raised = None

    def addError(self, test, err):
        super().addError(test, err)
        self.raised = err[1]

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.raised = err[1]


def run_one(test_id):
    """Run a single test by id and return the exception it raised, or None."""
    try:
        suite = unittest.TestLoader().loadTestsFromName(test_id)
    except Exception as exc:  # noqa: BLE001 - a load failure is itself a finding
        return exc
    result = _Capture()
    suite.run(result)
    return result.raised


def tally():
    by_reason = {}
    for reason in BLOCKED.values():
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return by_reason


class BlockedSurfaceTests(unittest.TestCase):
    def test_the_blocked_surface_is_thirteen_tests(self):
        self.assertEqual(13, len(BLOCKED))

    def test_the_recorded_signature_matches_the_blocked_surface(self):
        """errors + failures must equal the blocked list, or the record is wrong."""
        self.assertEqual(len(BLOCKED),
                         RECORDED_SIGNATURE['failures'] + RECORDED_SIGNATURE['errors'])

    def test_the_inventory_undercounted_and_the_correction_is_deliberate(self):
        """The inventory is wrong in two fields; this pins what it got wrong."""
        self.assertNotEqual(INVENTORY_CLAIMED, RECORDED_SIGNATURE)
        self.assertEqual(1, RECORDED_SIGNATURE['failures'])
        self.assertEqual(0, INVENTORY_CLAIMED['failures'])
        self.assertEqual(1, RECORDED_SIGNATURE['skipped'])
        self.assertEqual(2, INVENTORY_CLAIMED['skipped'])
        # The error count was the one field the inventory got right, and §154 said
        # so. §155 measured it and it was wrong too: 11, not 12.
        self.assertEqual(11, INVENTORY_CLAIMED['errors'])
        self.assertEqual(12, RECORDED_SIGNATURE['errors'])

    def test_the_second_correction_moved_the_count_and_the_reason_tally(self):
        """§155 changed both the size and the shape of the list. Pin both moves."""
        self.assertEqual(12, FIRST_RECORD['blocked'])
        self.assertEqual(13, len(BLOCKED))
        self.assertEqual({'O_NOFOLLOW': 7, 'mkfifo': 2, 'fcntl': 1, 'posix': 2},
                         FIRST_RECORD['by_reason'])
        self.assertEqual({'O_NOFOLLOW': 6, 'mkfifo': 2, 'fcntl': 1,
                          'symlink_privilege': 2, 'posix': 2}, tally())
        # The primitive that used to account for seven tests accounts for six, and
        # the seventh did not disappear -- it moved to a reason that had no name.
        self.assertEqual(FIRST_RECORD['by_reason']['O_NOFOLLOW'] - 1,
                         tally()['O_NOFOLLOW'])

    def test_every_reason_is_a_known_platform_fact(self):
        for test_id, reason in BLOCKED.items():
            with self.subTest(test=test_id):
                self.assertIn(reason, REASONS)

    def test_one_missing_primitive_dominates(self):
        """Documents the finding rather than leaving it to be re-measured."""
        self.assertEqual({'O_NOFOLLOW': 6, 'mkfifo': 2, 'fcntl': 1,
                          'symlink_privilege': 2, 'posix': 2}, tally())

    def test_each_recorded_reason_holds_exactly_on_the_platforms_that_lack_it(self):
        """On Windows every reason applies; on POSIX none may.

        A reason that stops applying on Windows means the primitive was shimmed and
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
        """A typo would make the list document nothing while still counting to 13."""
        for test_id in BLOCKED:
            with self.subTest(test=test_id):
                module_name, class_name, method = test_id.rsplit('.', 2)
                self.assertTrue(importlib.util.find_spec(module_name),
                                f'{module_name} is not importable')
                module = importlib.import_module(module_name)
                self.assertTrue(hasattr(module, class_name), f'{class_name} missing')
                self.assertTrue(hasattr(getattr(module, class_name), method),
                                f'{method} missing from {class_name}')

    def test_each_recorded_reason_is_the_actual_cause(self):
        """The predicate being true is not the same as the predicate being why.

        This is the assertion whose absence let §154 be wrong twice. Every other
        test in this module checks that a precondition *holds*; none of them checks
        that the precondition is what actually killed the test. So each blocked
        test is run here and the exception it raises is matched against the reason
        on its row -- ``O_NOFOLLOW`` has to fail with an ``O_NOFOLLOW``
        ``AttributeError``, and a symlink-privilege row has to fail with
        ``WinError 1314``, not merely on a host where both are true.
        """
        if os.name != 'nt':
            self.skipTest('the blocked surface is a Windows measurement')
        for test_id, reason in sorted(BLOCKED.items()):
            with self.subTest(test=test_id, reason=reason):
                raised = run_one(test_id)
                self.assertIsNotNone(
                    raised,
                    f'{test_id} no longer fails; it can be removed from the blocked '
                    f'surface and verified for real')
                self.assertTrue(
                    CAUSES[reason](raised),
                    f'{test_id} is recorded as {reason!r} but actually raised '
                    f'{type(raised).__name__}: {raised}')


class OfflineSuiteTests(unittest.TestCase):
    """``runtime_tests`` is run behind a network-denying audit hook. Keep it so.

    §155 added three tests that reached the Telegram webhook through
    ``starlette.testclient.TestClient``.  ``scripts/verify_offline.py`` runs this
    suite with an audit hook that raises on ``socket.connect``,
    ``socket.getaddrinfo`` and ``socket.sendto``, so the gate went red with three
    ``RuntimeError: Offline verification: network disabled`` -- an error that names
    the harness rather than the test, which is why it is worth a guard.

    The suite was not socket-free by construction.  It was socket-free because
    nobody had needed a socket yet, and the moment one test did, the gate broke in
    a place nobody was looking.  This is the assertion that keeps it true, and it
    is deliberately STATIC: it fails at the import line, not at the first network
    call, so the message points at the offending statement.

    ``urllib.request`` is NOT on the list.  ``test_custom_http_adapter`` and
    ``test_onec_adapter`` import it solely to patch ``OpenerDirector.open`` with a
    synthetic ``HTTPError`` and assert the message is sanitized -- no connection is
    made, and forbidding the import would be a false positive on a real test.
    ``TestClient`` and ``httpx`` carry no such second meaning: importing either
    says the test intends to speak HTTP.
    """

    FORBIDDEN = ('TestClient', 'httpx')

    def test_no_runtime_test_imports_an_http_client(self):
        offenders = []
        for path in sorted(Path(__file__).resolve().parent.glob('*.py')):
            text = path.read_text(encoding='utf-8', errors='replace')
            for token in self.FORBIDDEN:
                if re.search(r'^\s*(?:from|import)\s+.*\b' + re.escape(token) + r'\b',
                             text, re.M):
                    offenders.append(f'{path.name}: {token}')
        self.assertEqual([], offenders,
                         'runtime_tests runs behind a network-denying audit hook '
                         '(scripts/verify_offline.py); HTTP belongs in '
                         'integration_tests, which that gate excludes')


if __name__ == '__main__':
    unittest.main()
