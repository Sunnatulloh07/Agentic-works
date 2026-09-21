"""Check that every tool named in the capabilities example exists in the registry.

Path-independent: run it from the repository root or from ``api-python``, either
works. The earlier version inserted ``'.'`` into ``sys.path`` and opened
``'../config/...'``, so it only ran from one directory and failed everywhere else
with a bare ``ModuleNotFoundError`` — a helper that cannot be run is not a check.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime.tools import build_registry

names = set(build_registry().items)
path = os.path.join(ROOT, 'config', 'agent-capabilities.example.yaml')
with open(path, encoding='utf-8') as handle:
    text = handle.read()

tools = set()
for match in re.finditer(r'tools:\s*\[([^\]]*)\]', text):
    tools |= {t.strip() for t in match.group(1).split(',') if t.strip()}

unknown = sorted(tools - names)
print('registry tools      :', len(names))
print('declared tool refs  :', len(tools))
print('unknown             :', unknown)
print('agent ids           :', re.findall(r'^- id:\s*(\S+)', text, re.M))

if unknown:
    sys.exit(f'FAIL: {len(unknown)} tool reference(s) are not registered: {unknown}')
print('OK: every declared tool reference is registered')
