#!/usr/bin/env python3
"""Run the Agent Platform verification matrix and print a compact markdown table.

Stdlib only.  Exit code 0 = no regression, 1 = at least one regression/error.
See ../SKILL.md for how to read the output.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Windows-only allowance: these three modules assert POSIX behaviour (recorded in
# api-python/runtime_tests/test_platform_baseline.py).  Nothing else is excused.
WIN_ALLOWED_MODULES = ('test_portable_fs', 'test_macos_bundle', 'test_foundation_v02')
WIN_BASELINE = {'failures': 1, 'errors': 12, 'skipped': 1}

STEPS = ['runtime', 'integration', 'scripts', 'e2e', 'ui', 'runner', 'manifest']
PY = '"%s"' % sys.executable


def repo_root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / 'CLAUDE.md').exists() and (p / 'api-python').is_dir():
            return p
    sys.exit('platform-verify: cannot locate repo root (CLAUDE.md + api-python/)')


ROOT = repo_root()


def run(cmd, cwd, env, log, timeout):
    """Run a shell command, append to log. Returns (rc or None on timeout, output)."""
    with open(log, 'ab') as fh:
        fh.write(('$ %s\n(cwd=%s)\n' % (cmd, cwd)).encode())
        p = subprocess.Popen(cmd, cwd=str(cwd), env=env, shell=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            out, _ = p.communicate(timeout=timeout)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)],
                               capture_output=True)
            else:
                p.kill()
            out, _ = p.communicate()
            rc = None
        fh.write(out)
        fh.write(('\n[exit %s]\n\n' % ('TIMEOUT' if rc is None else rc)).encode())
    return rc, out.decode('utf-8', 'replace')


def parse_unittest(text):
    c = {'ran': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    m = re.search(r'^Ran (\d+) tests?', text, re.M)
    if m:
        c['ran'] = int(m.group(1))
    m = re.search(r'^(?:FAILED|OK)\s*\((.*?)\)', text, re.M)
    if m:
        for k, v in re.findall(r'(\w+)=(\d+)', m.group(1)):
            if k in c:
                c[k] = int(v)
    # "ERROR: test_x (module.Class.test_x)" -> module.Class.test_x
    ids = [b[1] for b in re.findall(r'^(?:ERROR|FAIL): (\S+) \(([^)]+)\)', text, re.M)]
    return c, ids


def fmt_ut(c):
    return 'ran=%(ran)d fail=%(failures)d err=%(errors)d skip=%(skipped)d' % c


def verdict_unittest(text, windows_allow):
    c, ids = parse_unittest(text)
    if c['ran'] == 0:
        return 'ERROR', 'no tests ran (import/collection failure?)', ''
    if c['failures'] == 0 and c['errors'] == 0:
        return 'PASS', fmt_ut(c), ''
    if windows_allow:
        outside = [i for i in ids if i.split('.')[0] not in WIN_ALLOWED_MODULES]
        # every failure must be individually identified AND inside the allowed modules
        if not outside and len(ids) == c['failures'] + c['errors']:
            note = ''
            if (c['failures'], c['errors']) != (WIN_BASELINE['failures'], WIN_BASELINE['errors']):
                note = ('differs from baseline f=%d/e=%d; review test_platform_baseline.py'
                        % (WIN_BASELINE['failures'], WIN_BASELINE['errors']))
            return 'PASS*', fmt_ut(c) + ' (Windows-only allowance)', note
        detail = ', '.join(outside[:5]) if outside else 'could not attribute every failure'
        return 'REGRESSION', fmt_ut(c), 'outside allowance: ' + detail
    return 'FAIL', fmt_ut(c), ''


def verdict_pytest(text, rc):
    parts = []
    for key, label in (('passed', 'pass'), ('failed', 'fail'), ('errors?', 'err'),
                       ('skipped', 'skip')):
        m = re.search(r'(\d+) %s\b' % key, text)
        if m:
            parts.append('%s=%s' % (label, m.group(1)))
    counts = ' '.join(parts) or 'no summary'
    ok = rc == 0 and re.search(r'\d+ passed', text)
    return ('PASS' if ok else 'FAIL'), counts, ''


def parse_node(text):
    def n(k):
        # TAP reporter prints '# tests N'; Node 20+'s default spec reporter
        # prints 'ℹ tests N'. Accept both.
        m = re.search(r'^(?:#|ℹ) %s (\d+)' % k, text, re.M)
        return int(m.group(1)) if m else None
    return n('tests'), n('pass'), n('fail'), n('skipped')


def runner_blocked_names():
    """Test names apps/runner/windows-baseline.test.js records as Windows-blocked."""
    try:
        text = (ROOT / 'apps' / 'runner' / 'windows-baseline.test.js').read_text(encoding='utf-8')
    except OSError:
        return set()
    return set(re.findall(r"'([^']+)':'(?:preview|symlink_privilege|private_mode)'", text))


def fmt_node(t):
    return 'tests=%s pass=%s fail=%s skip=%s' % t


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--only', help='comma list of: ' + ','.join(STEPS))
    ap.add_argument('--pylib', default=os.environ.get('PLATFORM_VERIFY_PYLIB'),
                    help='dir added to PYTHONPATH (e.g. for cryptography); '
                         'env PLATFORM_VERIFY_PYLIB')
    ap.add_argument('--manifest', action='store_true',
                    help='also regenerate + verify MANIFEST.sha256 (rewrites the file)')
    ap.add_argument('--fast', action='store_true', help='skip e2e and the UI build')
    ap.add_argument('--timeout-scale', type=float, default=1.0)
    a = ap.parse_args()

    if a.only:
        wanted = [s.strip() for s in a.only.split(',') if s.strip()]
    else:
        wanted = [s for s in STEPS if s != 'manifest']
    unknown = [s for s in wanted if s not in STEPS]
    if unknown:
        sys.exit('unknown step(s): %s; valid: %s' % (unknown, STEPS))
    if a.manifest and 'manifest' not in wanted:
        wanted.append('manifest')
    if a.fast:
        wanted = [s for s in wanted if s != 'e2e']

    logdir = Path(tempfile.mkdtemp(prefix='platform-verify-'))
    base = dict(os.environ)
    base['PYTHONUTF8'] = '1'
    if a.pylib:
        base['PYTHONPATH'] = a.pylib + os.pathsep + base.get('PYTHONPATH', '')
    is_win = os.name == 'nt'
    api = ROOT / 'api-python'

    def T(s):
        return int(s * a.timeout_scale)

    rows = []
    bad = False

    for step in wanted:
        log = logdir / ('%s.log' % step)
        t0 = time.time()
        v = c = n = ''
        try:
            if step == 'runtime':
                rc, out = run(PY + ' -m unittest discover -s runtime_tests -t runtime_tests',
                              api, base, log, T(1500))
                v, c, n = ('TIMEOUT', '', '') if rc is None else verdict_unittest(out, is_win)
            elif step == 'integration':
                env = dict(base, ENV='test', ALLOW_INSECURE_DEV='true',
                           PIPELINE_MODE='platform', IDENTITY_DIRECTORY='false')
                rc, out = run(PY + ' -m pytest integration_tests -q', api, env, log, T(1500))
                v, c, n = ('TIMEOUT', '', '') if rc is None else verdict_pytest(out, rc)
            elif step == 'scripts':
                rc, out = run(PY + ' -m unittest discover -s scripts -p "test_*.py"',
                              ROOT, base, log, T(600))
                if rc is None:
                    v = 'TIMEOUT'
                else:
                    cc, _ = parse_unittest(out)
                    v = 'PASS' if rc == 0 and cc['ran'] else 'FAIL'
                    c = fmt_ut(cc)
            elif step == 'e2e':
                rc, out = run(PY + ' scripts/e2e_smoke.py', ROOT, base, log, T(600))
                if rc is None:
                    v = 'TIMEOUT'
                else:
                    p = len(re.findall(r'\bPASS\b', out))
                    f = len(re.findall(r'\bFAIL\b', out))
                    v = 'PASS' if rc == 0 and f == 0 else 'FAIL'
                    c = 'PASS=%d FAIL=%d' % (p, f)
            elif step == 'ui':
                ui = ROOT / 'apps' / 'ui'
                cmds = ['npm run typecheck']
                if not a.fast:
                    cmds.append('npm run build')
                cmds.append('node --test lib/*.test.mjs')
                v = 'PASS'
                for cmd in cmds:
                    rc, out = run(cmd, ui, base, log, T(900))
                    if rc != 0:
                        v = 'TIMEOUT' if rc is None else 'FAIL'
                        c = 'failed at: ' + cmd
                        break
                    if cmd.startswith('node --test'):
                        c = fmt_node(parse_node(out))
                if v == 'PASS' and a.fast:
                    n = 'build skipped (--fast)'
            elif step == 'runner':
                rc, out = run('node --test apps/runner/test.js', ROOT, base, log, T(600))
                if rc is None:
                    v = 'TIMEOUT'
                else:
                    t = parse_node(out)
                    c = fmt_node(t)
                    if t[0] is None:
                        v, n = 'ERROR', 'no node:test summary'
                    elif rc == 0:
                        v = 'PASS'
                    elif is_win:
                        # Excused ONLY by name: every failing test must be one the
                        # repo records as Windows-blocked (apps/runner/windows-baseline
                        # .test.js BLOCKED). Anything else is a regression.
                        failing = set(re.findall(r'^✖ (.+?) \(', out, re.M))
                        blocked = runner_blocked_names()
                        extra = sorted(failing - blocked)
                        if blocked and not extra:
                            v, n = 'PASS*', '%d recorded Windows-only failures' % len(failing)
                        else:
                            v, n = 'REGRESSION', 'not in Windows baseline: ' + ', '.join(extra[:5])
                    else:
                        v = 'FAIL'
            elif step == 'manifest':
                rc1, out1 = run(PY + ' scripts/generate_manifest.py', ROOT, base, log, T(300))
                rc2, out2 = (run(PY + ' scripts/verify_manifest.py', ROOT, base, log, T(300))
                             if rc1 == 0 else (None, ''))
                v = 'PASS' if rc1 == 0 and rc2 == 0 else 'FAIL'
                c = (out2.strip().splitlines() or [''])[-1][:80]
                n = 'MANIFEST.sha256 was rewritten'
        except Exception as e:  # keep the matrix going
            v, c = 'ERROR', repr(e)
        if v not in ('PASS', 'PASS*'):
            bad = True
        rows.append((step, v, c + ('; ' + n if n else ''),
                     '%ds' % (time.time() - t0), str(log)))

    print('\n| step | result | counts | time | log |')
    print('|---|---|---|---|---|')
    for r in rows:
        print('| %s | %s | %s | %s | %s |' % r)
    print('\nPASS* = only the recorded Windows-only failures. '
          'REVIEW = read the log before claiming green.')
    print('RESULT: ' + ('REGRESSION/FAILURE' if bad else 'no regressions (still review the table)'))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
