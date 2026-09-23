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
* the file is edited as BYTES, so CRLF is preserved;
* a control that is not GREEN refuses to measure -- but on a platform where some
  tests are known-red, GREEN is the wrong bar and the recorded SIGNATURE is the
  right one. See ``signature`` and the ``baseline`` argument to ``main``.

Mutations are given as a list of ``(label, old_bytes, new_bytes)``.
"""
from __future__ import annotations

import atexit
import hashlib
import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
API = os.path.join(ROOT, 'api-python')
MANAGED = os.path.join(os.path.expanduser('~'), '.workbuddy-ai', 'binaries',
                       'python', 'envs', 'default', 'Scripts', 'python.exe')

RED, GREEN, MISSING = 'RED', 'GREEN', 'PATTERN-MISSING'

# Passing this as ``baseline_signature`` measures the control and adopts whatever it
# produced. That is what makes an audit portable: the same pattern is green on Linux
# and carries eleven known errors on Windows, and hardcoding either would make the
# script wrong on the other platform.
AUTO_BASELINE = 'auto'


def drop_sidecar(path):
    """Remove the sidecar, or say so and carry on.

    This used to be a bare ``os.remove`` and it cost a whole measurement: a host
    policy that blocks bulk deletion refused the unlink, the exception unwound
    through ``main`` and the phase script stopped after its FIRST module -- three
    mutations measured out of thirty-five, with the run reporting nothing wrong.
    An instrument may not die because it could not tidy up after itself, so the
    failure is reported and the measurement continues.

    Leaving a stale sidecar is safe: the next run compares it against the live file
    and either restores from it or overwrites it, and both paths re-enter here.
    """
    try:
        os.remove(path)
    except OSError as exc:
        print(f'note: could not remove {os.path.basename(path)} ({exc.strerror}); '
              f'the next run will reconcile it.')


def signature(line):
    """The failure counts unittest printed, as a comparable string. Empty means green.

    Eleven tests in this suite are red on Windows and stay red: they exercise POSIX mount
    semantics the platform cannot provide. Requiring the control to be GREEN therefore
    refuses to measure any pattern that happens to include one of them -- which is how a
    known-red suite stops being measured at all, and how red stops meaning anything.

    A signature makes the control's requirement "reproduce the recorded baseline
    exactly", and a mutation's requirement "change it". Both are stricter than GREEN: a
    mutation that *removes* a pre-existing failure is caught as well.
    """
    text = line.strip()
    if text.startswith('OK'):
        return ''
    match = re.search(r'\(([^)]*)\)', text)
    if not match:
        return text or 'unknown'
    return ', '.join(sorted(part.strip() for part in match.group(1).split(',') if part.strip()))


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
    # Two modes, and the difference matters for how long a matrix takes.
    #
    # ``*.py`` means "discover by filename", the original behaviour: one pattern,
    # and unittest's ``-p`` accepts only one. A module spec (``runtime_tests.x``)
    # is passed straight to ``unittest``, so SEVERAL files can be measured in one
    # run -- which is what a phase needs when its bounds are pinned across more
    # than one file. Without this the only alternatives were the whole suite
    # (413 s per mutation) or a single file that would report a bound GREEN
    # merely because the test that pins it lives next door.
    if pattern.endswith('.py'):
        argv = ['-m', 'unittest', 'discover', '-s', 'runtime_tests',
                '-t', 'runtime_tests', '-p', pattern]
    else:
        argv = ['-m', 'unittest'] + pattern.split()
    proc = subprocess.run([python] + argv, cwd=API, capture_output=True,
                          text=True, env=env)
    tail = proc.stderr.strip().splitlines()
    last = tail[-1] if tail else ''
    return proc.returncode, last.strip(), signature(last)


def verify(target, mutations):
    """Report whether every mutation pattern matches its target exactly once.

    ``main`` already reports a pattern that matched zero times as PATTERN-MISSING, but
    only after the control run and a full pass over the list -- minutes for a large
    matrix. Checking the patterns first costs milliseconds and answers the same
    question, so a typo is found before the wait rather than after it. This exists
    because the same typo was paid for twice.
    """
    path = os.path.join(API, target) if not os.path.isabs(target) else target
    source = open(path, 'rb').read()
    print(f'target: {os.path.relpath(path, ROOT)}  ({len(source)} bytes)')
    bad = 0
    for label, old, _ in mutations:
        count = source.count(old)
        if count == 1:
            print(f'  OK         {label}')
            continue
        bad += 1
        verdict = MISSING if count == 0 else 'AMBIGUOUS'
        print(f'  {verdict:<15} {label}  count={count}')
        print(f'              {old!r}')
    print(f'\n{len(mutations)} mutations, {len(mutations) - bad} usable, {bad} unusable')
    return 1 if bad else 0


def main(target, pattern, mutations, baseline_signature=None):
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
            drop_sidecar(sidecar)
            return 1
        drop_sidecar(sidecar)

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

    # A control: the unmutated file must reproduce the recorded baseline, or the
    # matrix measures nothing. GREEN is the bar when no baseline is given; when one
    # is, the bar is the recorded signature, because eleven tests are red on Windows
    # and a pattern containing one of them could never be measured otherwise.
    code, last, measured = run_tests(pattern, python)
    if baseline_signature == AUTO_BASELINE:
        baseline_signature = measured
        print(f'baseline    : signature {measured!r} (measured, not recorded)')
    expected = '' if baseline_signature is None else baseline_signature
    control_ok = (code == 0) if baseline_signature is None else (measured == expected)
    print(f'{"CONTROL":<22} {GREEN if control_ok else RED:<15} {last}')
    if not control_ok:
        print('control does not reproduce the recorded baseline; fix it before mutating')
        print('  recorded: %r' % expected)
        print('  measured: %r' % measured)
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
        code, last, measured = run_tests(pattern, python)
        elapsed = time.perf_counter() - start
        open(path, 'wb').write(baseline)
        if baseline_signature is None:
            verdict = RED if code else GREEN
        else:
            verdict = RED if measured != baseline_signature else GREEN
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
        drop_sidecar(sidecar)

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
