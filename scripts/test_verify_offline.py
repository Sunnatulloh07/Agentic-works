"""The offline gate's platform-aware verdict (scripts/verify_offline.py).

Two of its jobs are red on Windows for platform reasons, and the gate used to
report them as FAIL on every Windows run -- a gate that is always red carries no
information. It now compares the failing set against the records the tree already
keeps, so these tests pin: the records are READ from those files rather than copied,
the recorded surface is accepted, and anything else -- a new failing test, an
unreadable report, a missing record -- keeps the job FAIL.
"""
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import verify_offline

ROOT = Path(__file__).resolve().parents[1]
WRAPPED_ID = 'runtime_tests.test_portable_fs.PortableFSTests.test_read_real_file'


class RecordedSurfaceTests(unittest.TestCase):
    def test_the_python_record_is_read_from_the_baseline_module(self):
        ids = verify_offline.python_blocked_ids(ROOT)
        self.assertEqual(len(ids), 13)
        self.assertTrue(all('.' in name for name in ids))

    def test_the_node_record_is_read_from_the_windows_baseline(self):
        names = verify_offline.node_blocked_names(ROOT)
        self.assertEqual(len(names), 11)
        self.assertIn('real file read', names)

    def test_the_sources_are_named_for_both_jobs(self):
        self.assertEqual(set(verify_offline.BLOCKED_SOURCES), {'python_runtime', 'node_runner'})
        self.assertEqual(verify_offline.ACCEPTED_STATUSES,
                         frozenset({'PASS', 'PASS_WITH_RECORDED_BLOCKED'}))

    def test_a_wrapped_unittest_report_is_read(self):
        text = 'FAIL: test_short\nERROR: test_other\n(' + WRAPPED_ID + ')\n'
        self.assertEqual(verify_offline.failing_tests(text), {WRAPPED_ID})

    def test_a_one_line_unittest_report_is_read(self):
        text = 'ERROR: short_name (' + WRAPPED_ID + ')\n'
        self.assertEqual(verify_offline.failing_tests(text), {WRAPPED_ID})

    def test_a_report_without_an_id_is_ignored(self):
        self.assertEqual(verify_offline.failing_tests('FAIL: unnamed\nsome output\n'), set())

    def test_a_discover_style_id_matches_the_baseline_record(self):
        """The job runs ``discover -s runtime_tests``, so its ids have no prefix;
        the record does, and the comparison must not care."""
        text = 'ERROR: x (test_portable_fs.PortableFSTests.test_read_real_file)\n'
        self.assertEqual(verify_offline.recorded_blocked('python_runtime', text, ROOT),
                         [WRAPPED_ID])

    def test_the_recorded_python_surface_is_accepted(self):
        recorded = sorted(verify_offline.python_blocked_ids(ROOT))[0]
        text = 'ERROR: name\n(' + recorded + ')\n'
        self.assertEqual(verify_offline.recorded_blocked('python_runtime', text, ROOT),
                         [recorded])

    def test_a_new_python_failure_is_not_blessed(self):
        text = 'ERROR: something_new\n(runtime_tests.test_engine.EngineTests.test_a_new_failure)\n'
        self.assertIsNone(verify_offline.recorded_blocked('python_runtime', text, ROOT))

    def test_an_unreadable_python_report_is_not_blessed(self):
        self.assertIsNone(verify_offline.recorded_blocked('python_runtime', 'Ran 5 tests\n', ROOT))

    def test_the_recorded_node_surface_is_accepted(self):
        text = verify_offline.CROSS + ' real file read (1.0ms)\n'
        self.assertEqual(verify_offline.recorded_blocked('node_runner', text, ROOT),
                         ['real file read'])

    def test_a_new_node_failure_is_not_blessed(self):
        text = verify_offline.CROSS + ' a brand new test (1.0ms)\n'
        self.assertIsNone(verify_offline.recorded_blocked('node_runner', text, ROOT))

    def test_the_node_summary_header_is_not_a_test(self):
        text = (verify_offline.CROSS + ' failing tests:\n\n'
                + verify_offline.CROSS + ' real file read (1.0ms)\n')
        self.assertEqual(verify_offline.recorded_blocked('node_runner', text, ROOT),
                         ['real file read'])

    def test_other_jobs_are_never_blessed(self):
        self.assertIsNone(verify_offline.recorded_blocked('sqlite_demo', 'ERROR: x', ROOT))

    def test_a_missing_record_is_not_a_blessing(self):
        with unittest.mock.patch.object(verify_offline, 'python_blocked_ids',
                                        side_effect=RuntimeError('record gone')):
            text = 'ERROR: x\n(' + WRAPPED_ID + ')\n'
            self.assertIsNone(verify_offline.recorded_blocked('python_runtime', text, ROOT))

    def test_ansi_decorated_logs_are_parsed_after_stripping(self):
        text = (chr(27) + '[7mERROR: name' + chr(27) + '[0m\n(' + WRAPPED_ID + ')\n')
        self.assertEqual(len(verify_offline.recorded_blocked('python_runtime', text, ROOT)), 1)

    def test_main_requires_a_required_job_to_be_accepted(self):
        source = (ROOT / 'scripts' / 'verify_offline.py').read_text(encoding='utf-8')
        self.assertIn("row['status'] not in ACCEPTED_STATUSES", source)


class ChildEnvironmentTests(unittest.TestCase):
    """The refusal must measure the environment the jobs actually run in."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.temp = Path(self.tmp.name)
        (self.temp / 'home').mkdir()
        self.env = verify_offline.child_env(self.temp, self.temp / 'home')

    def test_the_child_writes_and_reads_utf8(self):
        self.assertEqual(self.env.get('PYTHONIOENCODING'), 'utf-8')

    def test_the_child_keeps_the_user_site_pointer_on_windows(self):
        self.assertEqual('APPDATA' in self.env, bool(os.environ.get('APPDATA')))
        if os.name == 'nt':
            self.assertIn('SystemRoot', self.env)

    def test_the_child_imports_the_required_set(self):
        code, text = verify_offline.child_imports(self.env, ROOT)
        self.assertEqual(0, code, text)

    def test_the_probe_reports_a_missing_module(self):
        code, text = verify_offline.child_imports(self.env, ROOT,
                                                  names=('definitely_missing_module_xyz',))
        self.assertNotEqual(0, code)
        self.assertIn('definitely_missing_module_xyz', text)

    def test_job_output_is_decoded_as_utf8(self):
        source = (ROOT / 'scripts' / 'verify_offline.py').read_text(encoding='utf-8')
        self.assertIn("encoding='utf-8', errors='replace'", source)


if __name__ == '__main__':
    unittest.main()
