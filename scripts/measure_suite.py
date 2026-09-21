"""Run the full runtime suite and report wall time and the signature.

**Which interpreter matters, and getting it wrong produces a misleading number.**
The project's baseline is measured with the managed venv, which has ``cryptography``
installed. Under the bare system interpreter, 138 tests error with ``VaultError``
because that package is missing, so the signature reads ``errors=153`` instead of
``errors=11`` -- and those 138 errors are an ENVIRONMENT fact, not a code fact.
This script therefore prefers the managed interpreter and says which one it used,
so a signature can never be compared across two different environments by accident.

Measured with the bare interpreter (2026-09-21), for the record: 2 800 tests,
508 s, ``failures=1, errors=153, skipped=1``.

An earlier version of this script offered a ``--cheap-kdf`` mode that forced
``hashlib.scrypt`` to cheap parameters, to test whether the password KDF dominated
the run. It does not (91 calls, 18.6 s, 6%), and the mode was a trap: it changed 95
tests from passing to erroring, so the signature it printed was meaningless. It was
removed rather than documented, because a measurement mode that cannot produce a
valid signature is worse than no mode.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
API = os.path.join(ROOT, 'api-python')

MANAGED = os.path.join(os.path.expanduser('~'), '.workbuddy-ai', 'binaries',
                       'python', 'envs', 'default', 'Scripts', 'python.exe')


def interpreter():
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        return sys.argv[1], 'argument'
    if os.path.exists(MANAGED):
        return MANAGED, 'managed venv'
    return sys.executable, 'system (no managed venv found)'


def main():
    python, label = interpreter()
    env = dict(os.environ)
    try:
        import site
        paths = site.getsitepackages()
        if paths:
            env['PYTHONPATH'] = '.%s%s' % (os.pathsep, os.pathsep.join(paths))
    except Exception:
        pass

    start = time.perf_counter()
    proc = subprocess.run(
        [python, '-m', 'unittest', 'discover', '-s', 'runtime_tests',
         '-t', 'runtime_tests'],
        cwd=API, capture_output=True, text=True, env=env)
    elapsed = time.perf_counter() - start

    tail = proc.stderr.strip().splitlines()
    ran = next((line for line in reversed(tail) if line.startswith('Ran ')), 'Ran ?')
    signature = next((line for line in reversed(tail)
                      if line.startswith(('OK', 'FAILED'))), '?')

    print(f'interpreter: {python}  ({label})')
    print(f'wall time  : {elapsed:.1f}s  ({elapsed / 60:.1f} min)')
    print(ran)
    print(signature)
    if 'cryptography' not in _installed(python):
        print('NOTE: cryptography is missing, so the signature will read ~153 errors;')
        print('      the documented baseline (errors=11) needs the managed venv.')


def _installed(python):
    try:
        out = subprocess.run([python, '-c', 'import cryptography'],
                             capture_output=True, text=True, timeout=30)
        return 'cryptography' if out.returncode == 0 else ''
    except Exception:
        return ''


if __name__ == '__main__':
    main()
