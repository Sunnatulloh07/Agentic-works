"""One misconfigured coordinator must not starve a tenant's whole engine.

app/worker.py used to run every stage for a tenant inside a single try/except:

    e.run_schedules -> reengagement.tick -> briefing.tick -> escalation.tick
    -> e.process_event -> e.tick -> agent_loop.tick

Any exception a coordinator does not catch internally (reengagement.tick, for
example, catches only Forbidden/Conflict/ValueError/LookupError, so a
RuntimeError from a missing integrations entry or a missing credential escapes)
jumped straight to the outer handler.  The remaining stages -- process_event,
the engine tick and the agent loop -- were skipped, and because the failing
coordinator never advanced its own schedule it was due again about a second
later.  One broken pack key therefore froze the tenant's execution forever.

These tests pin the isolation contract itself: one guard per stage, a raising
stage never skips the next, and the log line carries the exception TYPE only --
an exception message may contain a provider URL or a bot token.

Dependency-free on purpose: no FastAPI, no pydantic, no environment.  That is
also why `run_stages` lives in app/worker.py above the lazy imports and is
asserted here to be importable while those packages are blocked.
"""
import ast
import importlib
import sys
import unittest
from pathlib import Path

from app.worker import run_stages
from platform_runtime.engine import Forbidden

# A message shaped like the ones that actually leak: provider host plus token.
SECRET = 'https://api.telegram.org/bot123:SECRET'

WORKER_SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'worker.py'


class Recorder:
    """Collects the stage order and every argument handed to the log callback."""

    def __init__(self):
        self.ran = []
        self.logged = []

    def ok(self, name, result=False):
        def stage():
            self.ran.append(name)
            return result
        return stage

    def boom(self, name, error):
        def stage():
            self.ran.append(name)
            raise error
        return stage

    def log(self, *args, **kwargs):
        self.logged.append(tuple(args) + tuple(sorted(kwargs.items())))

    def logged_text(self):
        return [str(part) for line in self.logged for part in line]


class StageIsolationTests(unittest.TestCase):
    def setUp(self):
        self.rec = Recorder()

    def test_a_raising_stage_does_not_skip_the_stages_after_it(self):
        run_stages('demo-retail', [
            ('reengagement', self.rec.boom('reengagement', RuntimeError(SECRET))),
            ('process_event', self.rec.ok('process_event')),
            ('agent_loop', self.rec.ok('agent_loop')),
        ], log=self.rec.log)
        self.assertEqual(['reengagement', 'process_event', 'agent_loop'], self.rec.ran)

    def test_every_stage_still_runs_when_all_of_them_raise(self):
        run_stages('demo-retail', [
            ('a', self.rec.boom('a', RuntimeError(SECRET))),
            ('b', self.rec.boom('b', KeyError('integrations'))),
            ('c', self.rec.boom('c', OSError('connection refused'))),
        ], log=self.rec.log)
        self.assertEqual(['a', 'b', 'c'], self.rec.ran)
        self.assertEqual(3, len(self.rec.logged))

    def test_stage_order_is_preserved(self):
        order = ['schedules', 'reengagement', 'briefing', 'escalation',
                 'process_event', 'engine', 'agent_loop']
        run_stages('demo-retail', [(n, self.rec.ok(n)) for n in order], log=self.rec.log)
        self.assertEqual(order, self.rec.ran)

    def test_returns_true_when_any_stage_reported_work(self):
        self.assertIs(True, run_stages('t', [
            ('a', self.rec.ok('a', False)),
            ('b', self.rec.ok('b', True)),
            ('c', self.rec.ok('c', False)),
        ], log=self.rec.log))

    def test_returns_false_when_no_stage_reported_work(self):
        self.assertIs(False, run_stages('t', [
            ('a', self.rec.ok('a', False)),
            ('b', self.rec.ok('b', None)),
            ('c', self.rec.ok('c', 0)),
        ], log=self.rec.log))

    def test_a_truthy_non_boolean_result_counts_as_work(self):
        # e.run_schedules returns a submitted count, not a bool.
        self.assertIs(True, run_stages('t', [('schedules', self.rec.ok('schedules', 2))],
                                       log=self.rec.log))

    def test_a_raising_stage_counts_as_no_work(self):
        self.assertIs(False, run_stages('t', [
            ('a', self.rec.boom('a', RuntimeError(SECRET))),
            ('b', self.rec.ok('b', False)),
        ], log=self.rec.log))

    def test_a_raising_stage_does_not_hide_work_done_by_another(self):
        self.assertIs(True, run_stages('t', [
            ('a', self.rec.boom('a', RuntimeError(SECRET))),
            ('b', self.rec.ok('b', True)),
        ], log=self.rec.log))

    def test_an_empty_stage_list_reports_no_work(self):
        self.assertIs(False, run_stages('t', [], log=self.rec.log))
        self.assertEqual([], self.rec.logged)

    def test_a_platform_exception_is_isolated_the_same_way(self):
        run_stages('demo-retail', [
            ('briefing', self.rec.boom('briefing', Forbidden(SECRET))),
            ('engine', self.rec.ok('engine', True)),
        ], log=self.rec.log)
        self.assertEqual(['briefing', 'engine'], self.rec.ran)
        self.assertIn('Forbidden', self.rec.logged_text())

    def test_the_log_names_the_tenant_the_stage_and_the_exception_type(self):
        run_stages('oquv-markaz', [
            ('reengagement', self.rec.boom('reengagement', RuntimeError(SECRET))),
        ], log=self.rec.log)
        self.assertEqual(1, len(self.rec.logged))
        text = self.rec.logged_text()
        self.assertIn('oquv-markaz', text)
        self.assertIn('reengagement', text)
        self.assertIn('RuntimeError', text)

    def test_the_log_never_carries_the_exception_message(self):
        run_stages('demo-retail', [
            ('a', self.rec.boom('a', RuntimeError(SECRET))),
            ('b', self.rec.boom('b', ValueError('token=abc123 leaked'))),
        ], log=self.rec.log)
        for part in self.rec.logged_text():
            self.assertNotIn(SECRET, part)
            self.assertNotIn('api.telegram.org', part)
            self.assertNotIn('bot123', part)
            self.assertNotIn('abc123', part)


class _Blocker:
    """meta_path finder that refuses a set of top-level packages."""

    def __init__(self, names):
        self.names = set(names)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in self.names:
            raise ImportError('blocked by the offline suite: ' + fullname)
        return None


class ImportSurfaceTests(unittest.TestCase):
    """The offline suite must stay dependency-free (same reason as app/planning.py)."""

    def test_importing_app_worker_needs_neither_fastapi_nor_the_environment(self):
        blocked = ('fastapi', 'pydantic', 'yaml', 'jwt')

        def owned(name):
            return name == 'app' or name.startswith('app.') or name.split('.')[0] in blocked

        saved = {n: m for n, m in sys.modules.items() if owned(n)}
        for name in saved:
            del sys.modules[name]
        finder = _Blocker(blocked)
        sys.meta_path.insert(0, finder)
        try:
            module = importlib.import_module('app.worker')
            self.assertTrue(callable(module.run_stages))
        finally:
            sys.meta_path.remove(finder)
            for name in [n for n in sys.modules if owned(n)]:
                del sys.modules[name]
            sys.modules.update(saved)

    def test_the_module_does_not_import_the_fastapi_layer_at_top_level(self):
        tree = ast.parse(WORKER_SOURCE.read_text(encoding='utf-8'))
        names = []
        for node in tree.body:  # top level only; lazy imports live inside main()
            if isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or '')
        for forbidden in ('platform_api', 'packs', 'planning', 'fastapi', 'pydantic', 'yaml'):
            for name in names:
                self.assertNotIn(forbidden, name,
                                 'top-level import must stay dependency-free: ' + name)

    def test_conversation_turns_advance_after_the_agent_loop(self):
        # A run that ended in this pass is settled into a reply in the same pass.
        source = WORKER_SOURCE.read_text(encoding='utf-8')
        self.assertLess(source.index("('agent_loop',"), source.index("('conversation',"))


if __name__ == '__main__':
    unittest.main()
