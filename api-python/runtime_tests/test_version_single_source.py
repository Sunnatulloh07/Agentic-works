"""One version, everywhere it is shown.

/health said 0.4.0, the agent catalog 0.3.8, the UI badge 0.3.8 and the README
v0.5. Operators read the version to report a problem, so three answers meant none.
app/version.py is the single source; every other copy is checked against it here
by reading the files, so this runs without FastAPI or Node.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / 'api-python'


def source_version():
    text = (API / 'app' / 'version.py').read_text(encoding='utf-8')
    return re.search(r"^VERSION = '([^']+)'$", text, re.M).group(1)


class VersionTests(unittest.TestCase):
    def test_single_source_is_semver_with_a_stage(self):
        self.assertRegex(source_version(), r'^\d+\.\d+\.\d+-development-preview$')

    def test_python_surfaces_import_the_single_source(self):
        for name in ('main.py', 'platform_api.py'):
            text = (API / 'app' / name).read_text(encoding='utf-8')
            with self.subTest(file=name):
                self.assertIn('from .version import VERSION', text)
                self.assertNotRegex(text, r"'\d+\.\d+\.\d+-development-preview'|\"\d+\.\d+\.\d+-development-preview\"")

    def test_packages_and_ui_badge_match(self):
        number = source_version().split('-')[0]
        for package in ('apps/ui/package.json', 'apps/runner/package.json'):
            with self.subTest(package=package):
                self.assertEqual(number, json.loads((ROOT / package).read_text(encoding='utf-8'))['version'])
        badge = (ROOT / 'apps/ui/app/platform/page.tsx').read_text(encoding='utf-8')
        self.assertIn('Development ' + number, badge)
        self.assertTrue((ROOT / 'README.md').read_text(encoding='utf-8').startswith(
            '# Agent Platform — v' + '.'.join(number.split('.')[:2])))


if __name__ == '__main__':
    unittest.main()
