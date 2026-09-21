"""Run a revert matrix: mutate one bound at a time and see whether anything notices.

A bound whose mutation leaves the suite green is a bound nobody is checking. This
is the instrument the boundary-audit method is built on, made reusable.

The matrix owns the target file while it runs, so nothing else may read it: a
measurement that runs concurrently reads a mutated source and reports failures that
do not exist.

Safety, each learned by getting it wrong:

* the pristine baseline is read once and its hash re-verified before every
  mutation, so a half-applied state can never become the new baseline;
* every mutation reports how many times its pattern matched, and a pattern that
  matched zero times is reported **PATTERN-MISSING** -- it measured nothing, and
  folding it into "green" is how two unrun modes were once read as passing;
* the restore is VERIFIED at the end by walking every mutation's ``old`` bytes
  against the live file, because ``atexit`` does not run when a matrix is killed;
* the file is edited as BYTES, so CRLF is preserved.

Mutations are given as a list of ``(label, old_bytes, new_bytes)``.
"""
from __future__ import annotations

import atexit
import hashlib
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
API = os.path.join(ROOT, 'api-python')
MANAGED = os.path.join(os.path.expanduser('~'), '.workbuddy-ai', 'binaries',
                       'python', 'envs', 'default', 'Scripts', 'python.exe')

RED, GREEN, MISSING = 'RED', 'GREEN', 'PATTERN-MISSING'


def interpreter():
    if os.path.exists(MANAGED):
        return MANAGED
    return sys.executable


def run_tests(pattern, python):
    env = dict(os.environ)
    try:
        import site
        paths = site.getsitepackages()
        if paths:
            env['PYTHONPATH'] = '.%s%s' % (os.pathsep, os.pathsep.join(paths))
    except Exception:
        pass
    proc = subprocess.run(
        [python, '-m', 'unittest', 'discover', '-s', 'runtime_tests',
         '-t', 'runtime_tests', '-p', pattern],
        cwd=API, capture_output=True, text=True, env=env)
    tail = proc.stderr.strip().splitlines()
    last = tail[-1] if tail else ''
    return proc.returncode, last.strip()


def main(target, pattern, mutations):
    path = os.path.join(API, target) if not os.path.isabs(target) else target
    sidecar = path + '.matrix-baseline'

    # A previous run that was killed mid-mutation leaves the source mutated, and the
    # next run would then read the MUTATION as its baseline and "restore" the wrong
    # bytes. The sidecar makes that detectable: it holds the bytes the last run
    # started from, so an interrupted run is refused rather than silently adopted.
    if os.path.exists(sidecar):
        previous = open(sidecar, 'rb').read()
        live = open(path, 'rb').read()
        if previous != live:
            print(f'INTERRUPTED RUN DETECTED: {os.path.relpath(path, ROOT)} does not match')
            print(f'the baseline a previous matrix left behind ({len(previous)} vs '
                  f'{len(live)} bytes).')
            print('Restoring the recorded baseline; re-run to measure.')
            open(path, 'wb').write(previous)
            os.remove(sidecar)
            return 1
        os.remove(sidecar)

    baseline = open(path, 'rb').read()
    baseline_hash = hashlib.sha256(baseline).hexdigest()
    open(sidecar, 'wb').write(baseline)

    def restore(*_):
        # `atexit` does not run when the process is killed by a signal, so both are
        # registered. The sidecar is deliberately LEFT behind: it is what lets the
        # next run notice, and it is removed only on a clean finish.
        open(path, 'wb').write(baseline)

    atexit.register(restore)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: (restore(), sys.exit(130)))
        except (ValueError, OSError):
            pass

    print(f'target      : {os.path.relpath(path, ROOT)}')
    print(f'baseline    : {len(baseline)} bytes, sha256 {baseline_hash[:16]}')
    python = interpreter()
    print(f'interpreter : {python}')
    print(f'tests       : {pattern}\n')

    # A control: the unmutated file must be green, or the matrix measures nothing.
    code, last = run_tests(pattern, python)
    print(f'{"CONTROL":<22} {RED if code else GREEN:<15} {last}')
    if code:
        print('control failed; fix the baseline before mutating')
        return 1

    results = []
    for label, old, new in mutations:
        current = open(path, 'rb').read()
        if hashlib.sha256(current).hexdigest() != baseline_hash:
            print(f'{label}: baseline drifted, restoring')
            open(path, 'wb').write(baseline)
            current = baseline
        count = current.count(old)
        if count == 0:
            results.append((label, MISSING, 0, 'pattern not found'))
            print(f'{label:<22} {MISSING:<15} count=0')
            continue
        if count > 1:
            results.append((label, MISSING, count, 'ambiguous pattern'))
            print(f'{label:<22} {MISSING:<15} count={count} (ambiguous)')
            continue
        open(path, 'wb').write(current.replace(old, new, 1))
        start = time.perf_counter()
        code, last = run_tests(pattern, python)
        elapsed = time.perf_counter() - start
        open(path, 'wb').write(baseline)
        verdict = RED if code else GREEN
        results.append((label, verdict, 1, last))
        print(f'{label:<22} {verdict:<15} {elapsed:5.1f}s  {last}')

    # The restore is verified, not assumed.
    live = open(path, 'rb').read()
    residue = [label for label, old, _ in mutations if old not in live]
    print()
    print(f'restore verified: {"YES" if live == baseline else "NO"}')
    if residue:
        print(f'RESIDUE: {residue}')

    if os.path.exists(sidecar):
        os.remove(sidecar)

    greens = [label for label, verdict, _, _ in results if verdict == GREEN]
    missing = [label for label, verdict, _, _ in results if verdict == MISSING]
    print()
    print(f'{len(results)} mutations: '
          f'{len(results) - len(greens) - len(missing)} red, '
          f'{len(greens)} GREEN (unpinned), {len(missing)} unmeasured')
    if greens:
        print('unpinned bounds:')
        for label in greens:
            print('  -', label)
    return 0


if __name__ == '__main__':
    print(__doc__)
    print('Import this module and call main(target, pattern, mutations) from a')
    print('phase script; the mutation list is the audit, not a command-line flag.')
