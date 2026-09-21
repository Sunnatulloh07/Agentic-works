"""Time each runtime test file separately, so optimisation targets are measured.

The full suite takes minutes; a single number cannot say WHICH file owns the
time. This runs discovery once per file, in its own interpreter, and prints a
sorted table. Run from the repository root or from ``api-python``.
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TESTS = os.path.join(ROOT, 'api-python', 'runtime_tests')
CWD = os.path.join(ROOT, 'api-python')

pattern = sys.argv[1] if len(sys.argv) > 1 else 'test_*.py'
files = sorted(f for f in os.listdir(TESTS) if re.fullmatch(pattern.replace('*', '.*'), f))

rows = []
total = 0.0
for name in files:
    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'runtime_tests',
         '-t', 'runtime_tests', '-p', name],
        cwd=CWD, capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    total += elapsed
    tail = proc.stderr.strip().splitlines()
    ran = next((line for line in reversed(tail) if line.startswith('Ran ')), 'Ran ?')
    count = re.search(r'Ran (\d+)', ran)
    rows.append((elapsed, name, int(count.group(1)) if count else 0))

rows.sort(reverse=True)
print(f'{"seconds":>8}  {"tests":>6}  file')
for elapsed, name, count in rows:
    print(f'{elapsed:8.1f}  {count:6d}  {name}')
print()
print(f'{total:8.1f}  {sum(r[2] for r in rows):6d}  TOTAL (sum of per-file runs)')
