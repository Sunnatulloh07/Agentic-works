"""Regressions for the coverage probe the offline gate depends on.

``scripts/probes/measure_reachability.py`` is a REQUIRED gate job: it recomputes
the figures README and the inventory lead with, and it fails the gate when a pack
declares a tool the host cannot provide. A required job whose failure path nobody
has executed is a job that can pass while broken, and that is not hypothetical
here -- the probe's failure handling was written, reviewed and committed, and it
then died with a ``KeyError`` on the first pack that refused to load. The
defensive code failed exactly when it was the only thing worth reading, and the
negative run that found it was ad hoc, so nothing in the tree would have caught a
regression of it.

These tests make the negative cases permanent. They pin three things the positive
run cannot show:

* a pack declaring a tool no adapter provides is REPORTED -- pack and tool named,
  exit code 1 -- rather than raised as a traceback;
* a name present in ``known_tool_names()`` but absent from the registry the host
  actually builds is reported, which is the check ``load_pack`` structurally
  cannot make and the measurement error §161 committed;
* ``--json`` emits exactly one JSON document and nothing beside it, so the merged
  stdout+stderr the gate stores as evidence still parses -- the verdict travels
  inside the document as ``verdict`` and ``failures``, not as a line beside it.

The fixtures are copies of the shipped ``packs/_template``, so a fixture fails for
the one reason under test rather than also tripping some validation a hand-written
stub's author did not think of.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'probes'))

import measure_reachability as probe

TEMPLATE_TOOLS_LINE = 'tools: [telegram.send]'
BOGUS_TOOL = 'no.such_tool'


class ProbeTestCase(unittest.TestCase):
    """Restores the process state ``measure()`` touches.

    ``measure()`` sets ENV and ALLOW_INSECURE_DEV and prepends ``api-python`` to
    ``sys.path``. Both are process-global, so without restoring them the probe's
    own environment handling leaks into every other module in the discovery run --
    and a test that quietly changes the environment it asserts about is not
    measuring anything.
    """

    @classmethod
    def setUpClass(cls):
        cls._environ = dict(os.environ)
        cls._path = list(sys.path)

    @classmethod
    def tearDownClass(cls):
        os.environ.clear()
        os.environ.update(cls._environ)
        sys.path[:] = cls._path

    def setUp(self):
        # Importable from the first line of every test, so ``unittest.mock.patch``
        # can name ``app.packs.PACKS_DIR`` without each test arranging import order.
        probe.prepare_environment()
        sys.path.insert(0, str(probe.API))

    def run_main(self, argv=()):
        """Run ``main()`` and return (exit code, stdout, stderr) separately.

        Separate on purpose: the JSON contract is about which stream holds what, so
        merging them would hide the very defect it pins.
        """
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(sys, 'argv', ['measure_reachability', *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = probe.main()
        return code, out.getvalue(), err.getvalue()

    @contextlib.contextmanager
    def pack_tree(self, name='fixture', yaml_edit=None):
        """Serve the probe a temporary ``packs/`` holding a copy of the template.

        ``load_pack`` resolves against ``app.packs.PACKS_DIR`` and the probe
        iterates that same imported pointer instead of keeping its own copy, so
        patching it moves both. That is a property worth stating: had the probe
        duplicated the constant, this fixture would have silently measured the
        committed packs while looking like it measured the fixture.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        shutil.copytree(ROOT / 'packs' / '_template', root / name)
        target = root / name / 'pack.yaml'
        text = target.read_text(encoding='utf-8')
        if yaml_edit is not None:
            # A fixture that was never built reads as "the guard is broken" when the
            # truth is "the edit matched nothing". Verified, not assumed.
            self.assertIn(TEMPLATE_TOOLS_LINE, text)
            target.write_text(yaml_edit(text), encoding='utf-8')
        with unittest.mock.patch('app.packs.PACKS_DIR', root):
            yield root


class MeasurementTests(ProbeTestCase):
    """What the committed tree must measure, and the arithmetic behind it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        probe.prepare_environment()
        sys.path.insert(0, str(probe.API))
        cls.result = probe.measure()

    def test_the_published_shape_is_complete(self):
        """Every key the report and the JSON output read has to exist.

        A missing key is not a cosmetic gap: ``report()`` died on exactly that,
        because a pack that refused to load has no ``declared_per_pack`` entry.
        """
        for key in ('registry_tools', 'known_tool_names', 'packs', 'shipped_packs',
                    'template_packs', 'declared_tools', 'template_declared_tools',
                    'template_only_tools', 'reachable_tools', 'reachable',
                    'hollow_claims', 'not_in_engine_registry', 'load_failures',
                    'declared_per_pack', 'loc', 'reachable_tool_modules',
                    'unreachable_tool_modules'):
            self.assertIn(key, self.result)

    def test_the_committed_tree_has_no_defect(self):
        self.assertEqual([], self.result['load_failures'])
        self.assertEqual([], self.result['hollow_claims'])
        self.assertEqual([], self.result['not_in_engine_registry'])
        self.assertEqual([], self.result['template_only_tools'])
        self.assertEqual(0, self.run_main()[0])

    def test_the_headline_is_the_intersection_of_declared_and_registered(self):
        declared = set()
        for name in self.result['shipped_packs']:
            declared.update(self.result['declared_per_pack'][name])
        self.assertTrue(set(self.result['reachable']) <= declared)
        self.assertEqual(self.result['reachable_tools'], len(self.result['reachable']))
        self.assertLessEqual(self.result['reachable_tools'], self.result['registry_tools'])
        self.assertLessEqual(self.result['reachable_tools'], self.result['declared_tools'])

    def test_a_template_is_loaded_and_validated_but_not_counted_as_shipped(self):
        """A scaffold is what a future customer copies, not what a present one calls.

        Counting it would let the headline rise without any tenant receiving
        anything. It is still loaded -- excluding it from the numerator is not the
        same as not checking it.
        """
        self.assertNotIn('_template', self.result['shipped_packs'])
        self.assertIn('_template', self.result['template_packs'])
        self.assertGreater(self.result['template_declared_tools'], 0)
        self.assertEqual([], self.result['load_failures'])

    def test_the_loc_arithmetic_reproduces_the_published_percentage(self):
        """The figure README prints must fall out of the parts published beside it.

        A percentage nobody can re-derive is the defect this probe exists to end, so
        the test recomputes it from the line counts rather than trusting the
        rounding the probe already did.
        """
        for label in ('tool_modules', 'package'):
            row = self.result['loc'][label]
            self.assertEqual(row['reachable_modules'] + row['unreachable_modules'],
                             row['owning_modules'])
            self.assertLessEqual(row['unreachable_lines'], row['owning_lines'])
            self.assertEqual(round(100.0 * row['unreachable_lines'] / row['owning_lines'], 1),
                             row['percent'])

    def test_every_source_file_is_accounted_for_exactly_once(self):
        """§161's second error, pinned arithmetically rather than by name.

        Modules owning no tool -- ``engine.py``, ``llm.py``, ``conversation.py`` --
        are reached through routes, webhooks and the agent loop, not through a pack's
        tool surface. Scoring them as unreachable counts the most-used code in the
        system as dead and inflates the figure to 88.7%. They must be counted
        separately and never enter the denominator, and the two buckets together
        must equal the glob the probe itself walked: nothing dropped, nothing in
        both.
        """
        top = self.result['loc']['tool_modules']
        whole = self.result['loc']['package']
        self.assertEqual(top['owning_modules'] + top['core_modules_owning_no_tool'],
                         len(sorted(probe.PACKAGE.glob('*.py'))))
        self.assertEqual(whole['owning_modules'] + whole['core_modules_owning_no_tool'],
                         len(sorted(probe.PACKAGE.rglob('*.py'))))
        self.assertLessEqual(top['owning_modules'], whole['owning_modules'])
        self.assertLessEqual(top['owning_lines'], whole['owning_lines'])

    def test_the_module_lists_are_disjoint_and_match_the_counts(self):
        reached = self.result['reachable_tool_modules']
        unreached = self.result['unreachable_tool_modules']
        self.assertEqual(set(), set(reached) & set(unreached))
        self.assertEqual(len(reached), self.result['loc']['tool_modules']['reachable_modules'])
        self.assertEqual(len(unreached), self.result['loc']['tool_modules']['unreachable_modules'])
        self.assertTrue(all(name.startswith('platform_runtime.')
                            for name in reached + unreached))


class FailureReportingTests(ProbeTestCase):
    """The failure paths. None of these can be exercised by a clean tree."""

    def test_a_pack_declaring_a_tool_nobody_implements_is_reported_not_raised(self):
        """``load_pack`` refuses it, and the probe must REPORT that refusal.

        This is the path that died with a ``KeyError``: a pack that refuses to load
        has no ``declared_per_pack`` entry, and ``report()`` looked every pack
        directory up in that dict. The defensive code failed exactly when it was
        the only thing worth reading.
        """
        with self.pack_tree(yaml_edit=lambda text: text.replace(
                TEMPLATE_TOOLS_LINE,
                'tools: [telegram.send, %s]' % BOGUS_TOOL)):
            result = probe.measure()
            self.assertEqual(['fixture'], [entry['pack'] for entry in result['load_failures']])
            self.assertIn(BOGUS_TOOL, result['load_failures'][0]['error'])
            code, out, err = self.run_main()
        self.assertEqual(1, code)
        self.assertIn(BOGUS_TOOL, out)
        self.assertIn('REFUSED TO LOAD', out)
        self.assertNotIn('Traceback', out + err)

    def test_the_report_survives_a_pack_that_refused_to_load(self):
        """Regression, isolated: ``report()`` alone, on a result holding a failure.

        Separated from ``main()`` so a future crash is attributed to the report
        rather than to whatever else ``main()`` does on the way out.
        """
        with self.pack_tree(yaml_edit=lambda text: text.replace(
                TEMPLATE_TOOLS_LINE,
                'tools: [telegram.send, %s]' % BOGUS_TOOL)):
            result = probe.measure()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                probe.report(result)
        self.assertIn('REFUSED TO LOAD', out.getvalue())
        self.assertNotIn('KeyError', out.getvalue())

    def test_a_registry_gap_is_reported_while_the_pack_still_loads(self):
        """The check ``load_pack`` structurally cannot make, proven to fire.

        Dropping the host catalog removes ``products.search`` from the registry the
        engine builds while ``known_tool_names()`` still returns the full
        catalog-conditional name set -- so every pack still LOADS and the defect is
        invisible to the loader. That is exactly §161's measurement error, and it is
        the reason this probe compares against the built registry rather than
        trusting the name set. If ``load_failures`` were non-empty here the test
        would be measuring the loader's defence and proving nothing.
        """
        with unittest.mock.patch('app.platform_api.catalog', None):
            result = probe.measure()
        self.assertIn('products.search', result['not_in_engine_registry'])
        self.assertEqual([], result['load_failures'])
        self.assertEqual([], result['hollow_claims'])

    def test_a_registry_gap_fails_the_run_and_names_the_tool(self):
        with unittest.mock.patch('app.platform_api.catalog', None):
            code, out, err = self.run_main()
        self.assertEqual(1, code)
        self.assertIn('products.search', out)
        self.assertIn('engine registry', out)
        self.assertNotIn('Traceback', out + err)

    def test_a_template_only_tool_does_not_raise_the_headline(self):
        """A scaffold's tools are not a customer's tools.

        Today ``_template`` declares nothing ``demo-retail`` does not, so the two
        readings agree and the separation looks redundant. It is not: that agreement
        is a coincidence of the current YAML, and this pins the rule that keeps the
        headline honest when a scaffold grows a tool no pack ships.
        """
        with self.pack_tree(name='_fixture', yaml_edit=lambda text: text.replace(
                TEMPLATE_TOOLS_LINE, 'tools: [telegram.send, whatsapp.send]')):
            result = probe.measure()
        self.assertEqual([], result['shipped_packs'])
        self.assertEqual(['_fixture'], result['template_packs'])
        self.assertEqual(0, result['declared_tools'])
        self.assertEqual(0, result['reachable_tools'])
        self.assertEqual([], result['load_failures'])
        self.assertIn('whatsapp.send', result['template_only_tools'])
        self.assertEqual(result['template_declared_tools'], len(result['template_only_tools']))

    def test_the_same_pack_counted_as_shipped_does_move_the_headline(self):
        """The control for the test above: identical YAML, different directory name.

        Without it the exclusion could be passing because the fixture failed to load
        at all rather than because it was classified as a scaffold -- a negative
        result whose cause was never established is not evidence.
        """
        with self.pack_tree(name='fixture'):
            result = probe.measure()
        self.assertEqual(['fixture'], result['shipped_packs'])
        self.assertEqual([], result['template_packs'])
        self.assertEqual([], result['load_failures'])
        self.assertEqual(6, result['declared_tools'])
        self.assertEqual(6, result['reachable_tools'])
        self.assertEqual([], result['template_only_tools'])


class MachineReadableOutputTests(ProbeTestCase):
    """``--json`` is what the offline gate runs, so stdout is an interface.

    The gate stores the merged stdout+stderr of every job as ``<name>.log`` in its
    evidence directory and does not parse it, which makes pollution worse rather
    than better: a stray line cannot fail the gate, so it silently invalidates the
    record while every job still reports PASS.

    Two versions of this defect existed. The first put a verdict line on stdout
    after the document. The fix moved that line to stderr, which reads as correct
    in isolation and is not: the gate merges the two streams, and piped stdout is
    block-buffered while stderr is not, so the verdict landed FIRST and the
    evidence file still would not parse. Neither the probe run alone nor its unit
    tests could show that -- only running the real gate and reading the file it
    wrote did. ``--json`` now emits the document and nothing else, with the verdict
    as a field inside it.
    """

    def test_stdout_is_a_single_parseable_document(self):
        code, out, err = self.run_main(['--json'])
        self.assertEqual(0, code)
        document = json.loads(out)
        self.assertEqual(document['reachable_tools'], len(document['reachable']))

    def test_the_merged_stream_the_gate_captures_still_parses(self):
        """The regression an isolated probe run could not show.

        ``verify_offline`` runs every job with ``stderr=subprocess.STDOUT`` and
        piped stdout is block-buffered, so a verdict on stderr arrives in the
        evidence log BEFORE the document. The probe passed, the gate passed, and
        ``measure_reachability.log`` began with prose and would not parse. Found by
        running the real gate and reading the file it wrote, not by reasoning about
        the probe.
        """
        code, out, err = self.run_main(['--json'])
        self.assertEqual(0, code)
        self.assertEqual('', err)
        json.loads(err + out)

    def test_the_verdict_travels_inside_the_document(self):
        """Removing the prose line must not remove the verdict.

        It becomes a field, so a consumer reads it as data instead of pattern-matching
        a sentence that anyone is free to reword.
        """
        _, out, _ = self.run_main(['--json'])
        document = json.loads(out)
        self.assertEqual('PASS', document['verdict'])
        self.assertEqual([], document['failures'])

    def test_the_json_document_agrees_with_the_human_report(self):
        """Two renderings of one measurement must not be able to disagree.

        The gate keeps the JSON and a human reads the report; if they can drift, the
        evidence the gate stores is not the evidence the reader saw.
        """
        _, out, _ = self.run_main(['--json'])
        document = json.loads(out)
        _, report_out, _ = self.run_main()
        self.assertIn('REACHABLE: %d / %d' % (document['reachable_tools'],
                                              document['registry_tools']), report_out)
        self.assertIn(str(document['loc']['tool_modules']['percent']), report_out)

    def test_a_defect_is_still_visible_in_the_json_document(self):
        """A machine-readable mode must not soften the verdict it reports."""
        with unittest.mock.patch('app.platform_api.catalog', None):
            code, out, err = self.run_main(['--json'])
        self.assertEqual(1, code)
        document = json.loads(err + out)
        self.assertEqual('FAIL', document['verdict'])
        self.assertIn('products.search', document['not_in_engine_registry'])
        self.assertTrue(any('products.search' in failure['error']
                            for failure in document['failures']))


class GateWiringTests(unittest.TestCase):
    """A probe nobody runs is a probe that cannot fail anything.

    These read the gate's source instead of executing it: the gate spawns a dozen
    jobs and writes an evidence directory, so running it here would measure the
    runner rather than the wiring. Same convention ``test_verify_offline`` uses.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'scripts' / 'verify_offline.py').read_text(encoding='utf-8')
        cls.required = cls.source.split('required = {', 1)[1].split('}', 1)[0]

    def test_the_gate_runs_the_probe_in_json_mode(self):
        # The probe job specifically, not the probe's own regression job: the
        # ``probe_tools`` line also contains the substring, via
        # ``test_measure_reachability.py``, and matching it would assert --json
        # against a line that runs unittest rather than the probe.
        line = next(line for line in self.source.splitlines()
                    if 'measure_reachability.py' in line
                    and 'test_measure_reachability.py' not in line)
        self.assertIn("'--json'", line)

    def test_the_probe_is_in_the_required_job_set(self):
        """Required means BLOCKED fails too, not only FAIL.

        Without it the probe could be executed, recorded BLOCKED on some platform,
        and the gate could still report PASS -- the §162 shape, where a job's name
        implies a guarantee the job does not actually make.
        """
        self.assertIn('measure_reachability', self.required)

    def test_the_probe_regressions_are_themselves_a_gate_job(self):
        """Tests that nothing runs are tests that cannot fail anything.

        The probe is a required job, so its regressions have to be one as well;
        otherwise the guarantee is named and never enforced, and the failure paths
        -- which a clean tree cannot exercise -- rot silently.
        """
        line = next(line for line in self.source.splitlines()
                    if 'test_measure_reachability.py' in line)
        self.assertIn("'probe_tools'", line)
        self.assertIn('probe_tools', self.required)

    def test_every_browser_client_job_is_required(self):
        """Four browser clients are one family.

        Relaxing three and not the fourth opens a hole nobody decided to open, and it
        stays invisible while Node is installed -- which is the only condition anyone
        tests under.
        """
        for name in ('browser_session_client', 'browser_oauth_client',
                     'browser_google_data_client', 'browser_tools_client'):
            self.assertIn(name, self.required)

    def test_the_manifest_job_runs_the_real_checker(self):
        """§162, pinned.

        ``manifest_tools`` runs the manifest TOOL's unit tests against fixtures and
        stayed green while the committed ``MANIFEST.sha256`` was eleven errors out of
        date -- the gate measured something true and its name implied something else.
        The job whose name promises the manifest is intact has to run the checker
        against the manifest.
        """
        line = next(line for line in self.source.splitlines()
                    if "'manifest_integrity'" in line)
        self.assertIn('verify_manifest.py', line)


if __name__ == '__main__':
    unittest.main()
