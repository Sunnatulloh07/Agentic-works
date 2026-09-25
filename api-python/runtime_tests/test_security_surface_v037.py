"""Static route/config regressions; not a substitute for ASGI/browser tests."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SecuritySurfaceTests(unittest.TestCase):
    def constants(self, tree):
        """Module-level literal assignments, by name (one level).

        ``allow_methods`` used to be a list literal and is now the NAME
        ``CORS_METHODS``. Hard-coding either shape makes this guard fail on a
        refactor that changed nothing -- which is how a guard stops being read --
        so the name is resolved here instead.
        """
        found = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                try:
                    found[node.targets[0].id] = ast.literal_eval(node.value)
                except ValueError:
                    continue
        return found

    def resolve(self, node, constants):
        if isinstance(node, ast.Name):
            self.assertIn(node.id, constants,
                          'the CORS surface must be a literal this test can read')
            return constants[node.id]
        return ast.literal_eval(node)

    def test_cors_supports_ui_mutations(self):
        # Source files carry Uzbek text; the default codec is not UTF-8 on Windows.
        tree = ast.parse((ROOT / 'app/main.py').read_text(encoding='utf-8'))
        constants = self.constants(tree)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == 'add_middleware']
        call = next(n for n in calls if n.args and isinstance(n.args[0], ast.Name)
                    and n.args[0].id == 'CORSMiddleware')
        methods = self.resolve(
            next(k.value for k in call.keywords if k.arg == 'allow_methods'), constants)
        self.assertTrue({'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'} <= set(methods))
        self.assertNotIn('*', methods)

    def test_legacy_runner_not_imported_or_registered(self):
        tree = ast.parse((ROOT / 'app/main.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual('runner_ws', node.module)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'include_router':
                self.assertFalse(any(isinstance(a, ast.Name) and a.id == 'runner_router' for a in node.args))
