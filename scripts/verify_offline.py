"""Reproducible offline verification using Python, cryptography, Node and optionally Bun.

No package installation, real credentials, provider calls or deployment. This
runs project code and is a verification harness, not a hostile-code sandbox.
HTTP/UI builds remain separate dependency-backed release gates.
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
                       'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'OS')
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
           'TEMP': str(scratch), 'TMP': str(scratch), 'TMPDIR': str(scratch),
           'USERPROFILE': str(home),
           'NODE_OPTIONS': '--require=' + str(temp / 'node-guard.cjs')}
    for name in WINDOWS_PASSTHROUGH:
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


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
    output.mkdir(parents=True, exist_ok=False)
    results = []
    with tempfile.TemporaryDirectory(prefix='platform-offline-') as tmp:
        temp = Path(tmp)
        home = temp / 'home'
        home.mkdir()
        (temp / 'sitecustomize.py').write_text(PYTHON_GUARD)
        (temp / 'node-guard.cjs').write_text(NODE_GUARD)
        (temp / 'syntax.cjs').write_text(BUN_SYNTAX)
        env = child_env(temp, home)
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
                result = subprocess.run(command, cwd=cwd, env=env, text=True,
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
    failed = any(row['status'] == 'FAIL' or (row['name'] in required and row['status'] != 'PASS')
                 for row in summary['results'])
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
