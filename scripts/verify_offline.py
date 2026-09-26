"""Reproducible offline verification using Python, cryptography, Node and optionally Bun.

No package installation, real credentials, provider calls or deployment. This
runs project code and is a verification harness, not a hostile-code sandbox.
HTTP/UI builds remain separate dependency-backed release gates.

The bar is not "green" but the **recorded platform surface**: ``python_runtime`` and
``node_runner`` are red on Windows for platform reasons, so their failing set is
compared against the records the tree already keeps (``test_platform_baseline``'s
BLOCKED map and ``windows-baseline.test.js``) and reported as
``PASS_WITH_RECORDED_BLOCKED`` when it matches. Anything else -- a new failing test,
an unreadable report, a missing record -- is FAIL.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time


PYTHON_GUARD = """import sys

def block(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
        raise RuntimeError('Offline verification: network disabled')

sys.addaudithook(block)
"""
NODE_GUARD = """const deny=()=>{throw new Error('Offline verification: network disabled')};
for(const name of ['node:net','node:tls']) {
  const module=require(name);module.connect=deny;module.createConnection=deny;
}
for(const name of ['node:http','node:https']) {
  const module=require(name);module.request=deny;module.get=deny;
}
globalThis.fetch=deny;
globalThis.WebSocket=class {constructor(){deny()}};
require('node:module').syncBuiltinESMExports();
"""
# Windows-only variables the OS loader, Winsock and CNG need to initialise.
# Carried into children because the harness otherwise builds its environment from
# scratch; none of them carries credentials, proxies or provider configuration.
WINDOWS_PASSTHROUGH = ('SystemRoot', 'SystemDrive', 'windir', 'COMSPEC', 'PATHEXT',
                       'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'OS',
                       # User site-packages live under APPDATA on Windows. Without it the
                       # isolated child cannot import a user-installed dependency while the
                       # parent can, and the gate reports import errors as test failures --
                       # measured: 172 errors from fastapi/pydantic/yaml/jwt/cryptography.
                       'APPDATA')
# Wall-clock bound per job. A hung provider or deadlock must not stall evidence
# collection, but the bound has to fit the slowest supported platform: the full
# Python runtime suite takes ~27s on Linux CI and needs several times that on a
# Windows host, so the old single 90s limit truncated the run instead of failing it.
JOB_TIMEOUT_SECONDS = 90
JOB_TIMEOUT_OVERRIDES = {'python_runtime': 600}
# Dependencies the Python jobs cannot even IMPORT without. Checked before the jobs
# run; see the refusal in ``run()``.
#
# This is deliberately NARROWER than the list the summary reports. ``psycopg`` and
# ``redis`` are database drivers: their absence makes a handful of contract tests
# skip, it does not stop the suite from importing. Blocking on them would refuse a
# perfectly usable environment -- and did, on the first attempt, against the very
# virtualenv this repository's baseline was measured in.
#
# ``cryptography`` is in this list and was NOT in the reported one below, which is a
# gap the refusal exposed: a run without it produced **164 errors** -- the vault and
# OAuth suites -- and the summary would never have named the package once.
REQUIRED_DEPENDENCIES = ('pytest', 'fastapi', 'httpx', 'pydantic', 'jwt', 'yaml',
                         'cryptography')
# Reported in ``summary.json``: the required set plus the optional drivers, whose
# absence is information about coverage rather than a reason to refuse.
HTTP_DEPENDENCIES = REQUIRED_DEPENDENCIES + ('psycopg', 'redis')

# ---------------------------------------------------------------------------
# The recorded platform surface.
#
# Two jobs are red on Windows for platform reasons rather than code reasons, and
# both records already live in the tree: ``runtime_tests/test_platform_baseline.py``
# holds the thirteen blocked Python tests WITH the reason that causes each (its own
# guard re-measures those reasons), and ``apps/runner/windows-baseline.test.js``
# holds the eleven blocked Node tests with theirs. This gate READS those records
# instead of keeping a second copy, so the bar is the recorded surface and not
# "green": a new failing test keeps the job FAIL, while the recorded surface reports
# PASS_WITH_RECORDED_BLOCKED with the ids attached. Before this, the gate was red on
# every Windows run -- and a gate that is always red carries no information.
# ---------------------------------------------------------------------------
ANSI_RE = re.compile(re.escape(chr(27) + '[') + '[0-9;]*m')
CROSS = '\u2716'
BLOCKED_SOURCES = {'python_runtime': 'runtime_tests/test_platform_baseline.py',
                   'node_runner': 'apps/runner/windows-baseline.test.js'}
ACCEPTED_STATUSES = frozenset({'PASS', 'PASS_WITH_RECORDED_BLOCKED'})
NODE_BLOCKED_RE = re.compile("'([^']+)':'(preview|symlink_privilege|private_mode)'")


def python_blocked_ids(root: Path):
    """Blocked Python test ids, from the module whose guard verifies the reasons."""
    if str(root / 'api-python') not in sys.path:
        sys.path.insert(0, str(root / 'api-python'))
    from runtime_tests.test_platform_baseline import BLOCKED
    return set(BLOCKED)


def node_blocked_names(root: Path):
    text = (root / 'apps' / 'runner' / 'windows-baseline.test.js').read_text(encoding='utf-8')
    return {name for name, _reason in NODE_BLOCKED_RE.findall(text)}


def failing_tests(text: str):
    """Dotted ids of the tests unittest reported as errors or failures.

    ``unittest -v`` wraps a long id onto the next line::

        ERROR: test_read_real_file
        (runtime_tests.test_portable_fs.PortableFSTests.test_read_real_file)

    so the id is read from the following line when the first one does not carry it.
    A reporting shape this does not understand yields no ids, and the caller refuses
    rather than blessing a failure it could not read.
    """
    lines = text.splitlines()
    found = set()
    for index, line in enumerate(lines):
        match = re.match(r'^(?:ERROR|FAIL): (.+)$', line)
        if not match:
            continue
        candidate = match.group(1).strip()
        if candidate.endswith(')') and '(' in candidate:
            # The one-line shape unittest prints in the summary:
            # ``ERROR: test_x (runtime_tests.module.Class.test_x)``
            candidate = candidate[candidate.rfind('(') + 1:-1]
        else:
            # A long id is wrapped onto the next line instead.
            following = lines[index + 1].strip() if index + 1 < len(lines) else ''
            candidate = following[1:-1] if (following.startswith('(')
                                            and following.endswith(')')) else ''
        if candidate and '.' in candidate:
            found.add(candidate)
    return found


def node_failing(text: str):
    """The short test names ``node --test`` marked with its cross marker.

    The marker also heads the summary section (``✖ failing tests:``), and that line
    is not a test: a real entry carries its duration in parentheses.
    """
    found = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(CROSS):
            continue
        rest = stripped[len(CROSS):].strip()
        if ' (' not in rest:
            continue
        found.add(rest.split(' (', 1)[0].strip())
    return found


def without_package_prefix(test_id: str) -> str:
    """The id as ``discover -s runtime_tests`` reports it.

    ``-s <dir>`` puts that directory on sys.path, so the reported ids lose the
    ``runtime_tests.`` prefix that the baseline map carries (it was measured with
    ``-t runtime_tests``). Both sides are normalised here rather than asking one
    command to change, because the ids the reader sees in the job log are the ones
    that must be compared.
    """
    return test_id[len('runtime_tests.'):] if test_id.startswith('runtime_tests.') else test_id


def recorded_blocked(name: str, text: str, root: Path):
    """The failing set when it is within the recorded platform surface, else None.

    A blocked test that starts passing is allowed (the baseline module's own guard
    re-measures the reasons); a failing test OUTSIDE the record keeps the job FAIL.
    Python ids are returned in the record's own form (with the package prefix).
    """
    clean = ANSI_RE.sub('', text)
    try:
        if name == 'python_runtime':
            failing = {without_package_prefix(one) for one in failing_tests(clean)}
            allowed = {without_package_prefix(one) for one in python_blocked_ids(root)}
            if failing and failing <= allowed:
                return sorted('runtime_tests.' + one for one in failing)
            return None
        if name == 'node_runner':
            failing, allowed = node_failing(clean), node_blocked_names(root)
        else:
            return None
    except Exception:
        # An unreadable record is not a blessing: the job stays FAIL.
        return None
    return sorted(failing) if failing and failing <= allowed else None
BUN_SYNTAX = """const fs=require('node:fs');const path=require('node:path');
const root=process.argv[2];let checked=0;const errors=[];
function walk(dir){for(const entry of fs.readdirSync(dir,{withFileTypes:true})) {
  if(['node_modules','.next'].includes(entry.name))continue;
  const file=path.join(dir,entry.name);
  if(entry.isDirectory()){walk(file);continue;}
  if(!/\\.(tsx?|mts|mjs|js)$/.test(entry.name))continue;
  const loader=entry.name.endsWith('.tsx')?'tsx':/\\.(ts|mts)$/.test(entry.name)?'ts':'js';
  try{new Bun.Transpiler({loader}).transformSync(fs.readFileSync(file,'utf8'));checked++;}
  catch(error){errors.push({file:path.relative(root,file),message:String(error.message)});}
}}
walk(root);
console.log(JSON.stringify({kind:'syntax_only_not_typecheck_or_build',checked,errors},null,2));
process.exitCode=errors.length?1:0;
"""


def child_env(temp: Path, home: Path) -> dict:
    """Minimal child environment: no secrets, proxies or provider config inherited.

    Two operating-system requirements cannot be dropped. Windows loader, socket and
    TLS initialization read ``SystemRoot``; without it every child that imports
    ``asyncio`` fails with WinError 10106 and Node aborts with a CSPRNG assertion.
    A missing ``TEMP`` makes ``tempfile`` fall back to the working directory, so
    test scratch databases would land inside the source tree. Both are satisfied
    from throwaway directories instead of the caller's profile.
    """
    scratch = temp / 'scratch'
    scratch.mkdir(exist_ok=True)
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(home),
           'LANG': 'C.UTF-8', 'PYTHONPATH': str(temp), 'PYTHONDONTWRITEBYTECODE': '1',
           # The child writes UTF-8 whatever the host locale is, and the parent decodes
           # the same way; without the pair, Node's cross marker arrives as 'вњ–' on a
           # Russian Windows and the platform-surface check cannot see it.
           'PYTHONIOENCODING': 'utf-8',
           'TEMP': str(scratch), 'TMP': str(scratch), 'TMPDIR': str(scratch),
           'USERPROFILE': str(home),
           'NODE_OPTIONS': '--require=' + str(temp / 'node-guard.cjs')}
    for name in WINDOWS_PASSTHROUGH:
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def child_imports(env: dict, root: Path, names=REQUIRED_DEPENDENCIES):
    """Import the required set in the CHILD environment; returns (code, output).

    Same interpreter, same environment, same switch as the jobs, so its answer is
    the jobs' answer. A refusal that measured anything else would be the gate lying
    about its own environment -- which is exactly what it did before this probe.
    """
    probe = subprocess.run([sys.executable, '-c', 'import ' + ', '.join(names)],
                           cwd=root, env=env, encoding='utf-8', errors='replace',
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    return probe.returncode, probe.stdout or ''


def run(root: Path, output: Path) -> dict:
    # Refuse before spending three minutes proving the environment is wrong; see
    # REQUIRED_DEPENDENCIES for why this is a refusal and not a summary field. The
    # check comes before the evidence directory is created, so a refusal does not
    # leave an empty run behind to be mistaken for a completed one.
    missing = [name for name in REQUIRED_DEPENDENCIES
               if importlib.util.find_spec(name) is None]
    if missing:
        raise SystemExit(
            'REFUSED: %s cannot import %s.\n'
            'Every Python job in this gate runs with that same interpreter, so it would\n'
            'report a whole suite of import errors that are really one missing\n'
            'environment -- and the two are indistinguishable: python_runtime: FAIL,\n'
            'exit 1, either way.\n'
            'Run this script with the environment under test, for example:\n'
            '  python -m venv .venv\n'
            '  .venv/bin/pip install -r api-python/requirements.txt\n'
            '  .venv/bin/python scripts/verify_offline.py'
            % (sys.executable, ', '.join(missing)))
    results = []
    with tempfile.TemporaryDirectory(prefix='platform-offline-') as tmp:
        temp = Path(tmp)
        home = temp / 'home'
        home.mkdir()
        (temp / 'sitecustomize.py').write_text(PYTHON_GUARD)
        (temp / 'node-guard.cjs').write_text(NODE_GUARD)
        (temp / 'syntax.cjs').write_text(BUN_SYNTAX)
        env = child_env(temp, home)
        # The parent's find_spec measured the PARENT's environment, and that is not the
        # one the jobs run in -- on this host the packages live in the user site, which
        # the isolated child used to lose. So the refusal is re-measured in the child's
        # own environment before anything is created or spent.
        child_code, child_text = child_imports(env, root)
        if child_code:
            raise SystemExit(
                'REFUSED: the job environment cannot import the required set.\n'
                'The parent interpreter has it, the child that runs every job does not,\n'
                'so the suite would report import errors as if they were test failures.\n'
                'Child output:\n' + child_text.strip()[-800:])
        output.mkdir(parents=True, exist_ok=False)
        jobs = [
            ('python_runtime', [sys.executable, '-m', 'unittest', 'discover', '-s', 'runtime_tests', '-v'], root / 'api-python'),
            ('release_tools', [sys.executable, '-m', 'unittest', 'discover', '-s', 'scripts', '-p', 'test_release_tools.py', '-v'], root),
            ('manifest_tools', [sys.executable, '-m', 'unittest', 'discover', '-s', 'scripts', '-p', 'test_manifest.py', '-v'], root),
            ('sqlite_demo', [sys.executable, 'scripts/demo_runtime.py'], root),
            ('managed_database_demo', [sys.executable, 'scripts/demo_managed_database.py'], root),
        ]
        node = shutil.which('node')
        if node:
            jobs += [
                ('node_runner', [node, '--test', 'apps/runner/test.js', 'apps/runner/windows-baseline.test.js'], root),
                ('browser_session_client', [node, '--test', 'apps/ui/lib/session-client.test.mjs'], root),
                ('browser_oauth_client', [node, '--test', 'apps/ui/lib/oauth-client.test.mjs'], root),
                ('browser_google_data_client', [node, '--test', 'apps/ui/lib/google-data-client.test.mjs'], root),
                ('browser_tools_client', [node, '--test', 'apps/ui/lib/tools-client.test.mjs'], root),
            ]
        else:
            for name in ['node_runner', 'browser_session_client', 'browser_oauth_client',
                         'browser_google_data_client', 'browser_tools_client']:
                results.append({'name': name, 'status': 'BLOCKED', 'reason': 'Node unavailable'})
        bun = shutil.which('bun')
        if bun:
            jobs.append(('javascript_typescript_syntax', [bun, str(temp / 'syntax.cjs'), str(root / 'apps')], root))
        else:
            results.append({'name': 'javascript_typescript_syntax', 'status': 'BLOCKED', 'reason': 'Bun unavailable; optional syntax-only check'})
        for name, command, cwd in jobs:
            start = time.monotonic()
            try:
                # ``errors='replace'`` because the two ends of this pipe do not have
                # to agree on an encoding. The child writes with whatever its locale
                # picked -- on a Russian Windows, ``[WinError 1314]`` carries Cyrillic
                # from FormatMessage -- while the parent decodes with its own
                # preference, and an ambient ``PYTHONIOENCODING`` is enough to make
                # them differ. Without this the reader thread raises
                # UnicodeDecodeError, ``result.stdout`` comes back None, and the gate
                # dies with a TypeError from write_text instead of reporting a result.
                # A mojibake log line is a far better outcome than a gate that cannot
                # finish on the platform it is meant to check.
                result = subprocess.run(command, cwd=cwd, env=env, encoding='utf-8',
                                        errors='replace',
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        timeout=JOB_TIMEOUT_OVERRIDES.get(name, JOB_TIMEOUT_SECONDS))
                code, text = result.returncode, result.stdout or ''
            except subprocess.TimeoutExpired as exc:
                code = 124
                text = exc.stdout or ''
                if isinstance(text, bytes):
                    text = text.decode('utf-8', errors='replace')
                text += '\nVerification command timed out.\n'
            (output / (name + '.log')).write_text(text, encoding='utf-8')
            row = {'name': name, 'status': 'PASS' if code == 0 else 'FAIL', 'exit_code': code,
                   'command': command, 'seconds': round(time.monotonic() - start, 3), 'log': name + '.log'}
            blocked = recorded_blocked(name, text, root) if code != 0 else None
            if blocked:
                row['status'] = 'PASS_WITH_RECORDED_BLOCKED'
                row['blocked'] = blocked
                row['blocked_source'] = BLOCKED_SOURCES[name]
                row['note'] = ('every failing test is on the recorded platform surface; '
                               'that record owns the reasons and re-measures them')
            count = re.search(r'Ran (\d+) tests?', text) or re.search(r'(?:ℹ|#) tests (\d+)', text)
            if count:
                row['tests'] = int(count.group(1))
            results.append(row)
            print(json.dumps(row, ensure_ascii=False))

    failures, checked = [], 0
    for file in root.rglob('*.py'):
        if any(part in {'node_modules', '.venv', '__pycache__'} for part in file.parts):
            continue
        try:
            compile(file.read_bytes(), str(file), 'exec', dont_inherit=True)
            checked += 1
        except (ValueError, SyntaxError) as exc:
            failures.append({'file': str(file.relative_to(root)), 'error': str(exc)})
    syntax = {'name': 'python_syntax', 'status': 'FAIL' if failures else 'PASS', 'checked': checked, 'errors': failures}
    (output / 'python_syntax.json').write_text(json.dumps(syntax, ensure_ascii=False, indent=2) + '\n')
    results.append(syntax)
    summary = {'kind': 'offline_verification_only', 'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'python': sys.version.split()[0], 'results': results,
               'http_dependencies_missing': [name for name in HTTP_DEPENDENCIES
                                             if importlib.util.find_spec(name) is None],
               'optional_database_dependencies_missing': [name for name in ['psycopg','pymysql','pymongo','redis','pyodbc','oracledb','boto3','cassandra','neo4j','elasticsearch']
                                                          if importlib.util.find_spec(name) is None],
               'not_performed': ['dependency-backed HTTP tests', 'Next/React typecheck', 'Next production build',
                                 'live providers', 'live network databases including SQL Server, Oracle, DynamoDB, Cassandra, Neo4j and Elasticsearch',
                                 'native macOS F_GETPATH/LaunchAgent', 'local model inference', 'UI browser acceptance', 'production dependency audit', 'deployment'],
               'release': 'NO_GO'}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New directory; existing evidence is not overwritten')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.output or root / 'docs' / 'verification' / ('local-' + stamp)
    summary = run(root, output.resolve())
    print('Evidence:', output)
    print('Production: NO-GO. Read summary.json for blocked and unperformed checks.')
    required = {'python_runtime', 'release_tools', 'manifest_tools', 'sqlite_demo', 'managed_database_demo', 'node_runner', 'browser_session_client', 'browser_oauth_client', 'browser_google_data_client', 'python_syntax'}
    failed = any(row['status'] == 'FAIL'
                 or (row['name'] in required and row['status'] not in ACCEPTED_STATUSES)
                 for row in summary['results'])
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
