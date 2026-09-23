"""Run the API and the worker locally with api-python/.env loaded. Stdlib only.

    python scripts/run_local.py              # API on 127.0.0.1:8000 + worker
    python scripts/run_local.py --port 8010 --no-worker
    python scripts/run_local.py --check      # validate env, config and packs, then exit

Nothing else loads api-python/.env (python-dotenv is not a dependency), so
`uvicorn app.main:app` alone dies with `ConfigError: ENV sozlanmagan`. This
script is that missing step. Rules:

* KEY=VALUE lines, `#` comments, optional `export `, single or double quotes.
  A variable already in the process environment WINS over the file.
* APP_DB, PACKS_DIR and PLATFORM_INTEGRATIONS_FILE default to the repo's own
  locations; a relative value is resolved against the repository root, because
  the children run with cwd api-python.
* No value is ever printed: output names checks and variable NAMES only.

Both children run with cwd api-python. Ctrl+C stops both; if either child exits
on its own, the other is stopped and this script exits non-zero.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / 'api-python'
ENV_FILE = API_DIR / '.env'
KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
# Repo-relative defaults; setup_local.py writes the same three lines.
PATH_DEFAULTS = {
    'APP_DB': 'api-python/data/app.db',
    'PACKS_DIR': 'packs',
    'PLATFORM_INTEGRATIONS_FILE': 'config/integrations.json',
}
STOP_GRACE_SECONDS = 8


class EnvFileError(ValueError):
    """A malformed .env line. The message names the line number, never its text."""


def parse_env(text: str) -> dict[str, str]:
    """Pure .env parser. Raises EnvFileError on a line it cannot read."""
    values: dict[str, str] = {}
    for number, line in enumerate(text.lstrip('\ufeff').splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.startswith('export '):
            stripped = stripped[len('export '):].lstrip()
        key, sep, value = stripped.partition('=')
        key = key.strip()
        if not sep or not KEY.fullmatch(key):
            raise EnvFileError(f'.env line {number}: expected KEY=VALUE')
        value = value.strip()
        if value[:1] in ('"', "'"):
            end = value.find(value[0], 1)
            if end < 0:
                raise EnvFileError(f'.env line {number}: unterminated quote')
            rest = value[end + 1:].strip()
            if rest and not rest.startswith('#'):
                raise EnvFileError(f'.env line {number}: text after closing quote')
            value = value[1:end]
        else:
            value = re.split(r'\s#', value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def apply_env(values: dict[str, str], environ, root: Path = ROOT) -> None:
    """Fill `environ` from `values` without overriding it; then path defaults."""
    for key, value in values.items():
        if key not in environ:
            environ[key] = value
    for key, default in PATH_DEFAULTS.items():
        path = Path(environ.get(key) or default)
        environ[key] = str(path if path.is_absolute() else (root / path).resolve())


def load_environment(env_file: Path = ENV_FILE, environ=None, root: Path = ROOT) -> bool:
    """Apply `env_file` (if present) and the path defaults. True if the file existed."""
    environ = os.environ if environ is None else environ
    found = env_file.is_file()
    apply_env(parse_env(env_file.read_text(encoding='utf-8')) if found else {}, environ, root)
    return found


# --- checks -----------------------------------------------------------------

def _api_importable() -> None:
    if str(API_DIR) not in sys.path:
        sys.path.insert(0, str(API_DIR))


def _brief(exc: BaseException) -> str:
    return f'{type(exc).__name__}: {str(exc)[:200]}'


def tenants(environ) -> list[str]:
    packs = Path(environ['PACKS_DIR'])
    if not packs.is_dir():
        return []
    return sorted(p.name for p in packs.iterdir() if p.is_dir() and not p.name.startswith(('_', '.')))


def integration_checks(environ, names) -> list[tuple[str, bool | None, str]]:
    """(check, passed, detail) per tenant. None = skipped (tenant not configured).

    The core config() keeps its terse message; the hint naming both places to
    configure lives here, in the operator's tool.
    """
    hint = ('configure PLATFORM_INTEGRATIONS_FILE (config/integrations.json) '
            'or packs/<tenant>/integrations.yaml')
    json_path = Path(environ.get('PLATFORM_INTEGRATIONS_FILE', ''))
    try:
        operator = json.loads(json_path.read_text(encoding='utf-8')) if json_path.is_file() else {}
    except (OSError, ValueError) as exc:
        return [('integrations', False, f'{json_path} unreadable ({_brief(exc)})')]
    results = []
    for tenant in names:
        local = Path(environ['PACKS_DIR']) / tenant / 'integrations.yaml'
        if not local.is_file() and tenant not in operator:
            results.append((f'integrations {tenant}', None, 'not configured'))
            continue
        try:
            from platform_runtime.tools import config  # reads os.environ, as the API does
            cfg = config(tenant)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            results.append((f'integrations {tenant}', False, _brief(exc)))
            continue
        missing = sorted({ref for block in cfg.values() if isinstance(block, dict)
                          for field, ref in block.items()
                          if field in ('key_env', 'token_env') and isinstance(ref, str)
                          and not environ.get(ref)})
        if missing:
            results.append((f'integrations {tenant}', False, 'empty or unset: ' + ', '.join(missing)))
        else:
            results.append((f'integrations {tenant}', True, 'credentials referenced by name are set'))
    if not any(ok is not None for _, ok, _ in results):
        results.append(('integrations', False, f'no tenant has integration config: {hint}'))
    return results


PLACEHOLDER_MODELS = ('', 'SET_YOUR_AVAILABLE_MODEL_ID')


def conversation_check(agents, cfg) -> tuple[bool | None, str]:
    """(passed, detail) for a pack whose agents may hold customer conversations.

    None when no agent opts in. A conversation agent without the result-fed loop
    (llm.agent_loop_enabled true, a real model) answers every customer with the
    handoff text only -- the platform stays up, so this is the check that says why.
    """
    names = [a.get('id', '?') for a in agents if (a.get('conversation') or {}).get('enabled') is True]
    if not names:
        return None, 'no conversation agent'
    llm = cfg.get('llm') if isinstance(cfg, dict) else None
    if not isinstance(llm, dict) or llm.get('agent_loop_enabled') is not True:
        return False, (', '.join(names) + ' hold conversations but llm.agent_loop_enabled is not true: '
                       'customers get only the handoff text')
    if not isinstance(llm.get('model'), str) or llm['model'].strip() in PLACEHOLDER_MODELS:
        return False, 'llm.model is not set to a real model id'
    return True, ', '.join(names) + ': model loop enabled'


def _interrupt(*_):
    raise KeyboardInterrupt


def run_checks(env_found: bool) -> int:
    """Print one PASS/FAIL/SKIP line per check. Checks read os.environ, as the API does."""
    environ = os.environ
    _api_importable()
    results: list[tuple[str, bool | None, str]] = [
        ('env file', env_found or None, 'loaded' if env_found else 'not found; using process environment')]
    try:
        from app.config import validate_runtime_config
        validate_runtime_config(environ)
        results.append(('runtime config', True, 'ENV and required secrets present'))
    except Exception as exc:  # noqa: BLE001
        results.append(('runtime config', False, _brief(exc)))
    names = tenants(environ)
    if not names:
        results.append(('packs', False, 'no pack directory found under PACKS_DIR'))
    packs = {}
    for name in names:
        try:
            from app.packs import load_pack  # reads PACKS_DIR at import: env is applied by now
            packs[name] = load_pack(name)
            results.append((f'pack {name}', True, 'loaded'))
        except Exception as exc:  # noqa: BLE001
            results.append((f'pack {name}', False, _brief(exc)))
    results.extend(integration_checks(environ, names))
    for name, pack in packs.items():
        agents = [a.model_dump() for a in pack.agents]
        try:
            from platform_runtime.tools import config
            cfg = config(name)
        except Exception:  # noqa: BLE001 - integration_checks already reported why
            cfg = {}
        ok, detail = conversation_check(agents, cfg)
        if ok is not None:
            results.append((f'conversation {name}', ok, detail))
    for name, ok, detail in results:
        label = 'SKIP' if ok is None else ('PASS' if ok else 'FAIL')
        print(f'{label}  {name}: {detail}')
    failed = [name for name, ok, _ in results if ok is False]
    print('check: ' + ('FAILED (' + ', '.join(failed) + ')' if failed else 'OK'))
    return 1 if failed else 0


# --- supervision --------------------------------------------------------------

def stop(procs, graceful: bool) -> None:
    """Stop every still-running child: wait (Ctrl+C already reached them), then terminate, then kill."""
    alive = [p for _, p in procs if p.poll() is None]
    if graceful and os.name == 'posix':
        for p in alive:
            p.send_signal(signal.SIGINT)
    deadline = time.monotonic() + (STOP_GRACE_SECONDS if graceful else 0)
    for p in alive:
        try:
            p.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for p in alive:
        if p.poll() is None:
            p.terminate()
    for p in alive:
        try:
            p.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def supervise(procs, poll_seconds: float = 0.5) -> int:
    """Block until a child exits (-> stop the rest, non-zero) or Ctrl+C (-> stop all, 130)."""
    try:
        while True:
            for name, p in procs:
                code = p.poll()
                if code is not None:
                    print(f'run_local: {name} exited with code {code}; stopping the rest', file=sys.stderr)
                    stop(procs, graceful=False)
                    return code or 1
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        print('run_local: stopping', file=sys.stderr)
        stop(procs, graceful=True)
        return 130


def start(port: int, worker: bool) -> int:
    _api_importable()
    try:
        from app.config import validate_runtime_config
        validate_runtime_config(os.environ)
    except Exception as exc:  # noqa: BLE001
        print(f'FAIL  runtime config: {_brief(exc)}', file=sys.stderr)
        return 2
    commands = [('api', [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)])]
    if worker:
        commands.append(('worker', [sys.executable, '-m', 'app.worker']))
    if os.name == 'posix':
        # A plain `kill <pid>` of this script must still stop the children.
        signal.signal(signal.SIGTERM, _interrupt)
    procs = []
    for name, command in commands:
        procs.append((name, subprocess.Popen(command, cwd=API_DIR, env=dict(os.environ))))
    print(f'run_local: api http://127.0.0.1:{port} ' + ('+ worker' if worker else '(no worker)')
          + '; Ctrl+C to stop', file=sys.stderr)
    return supervise(procs)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-worker', action='store_true', help='start only the API')
    parser.add_argument('--check', action='store_true', help='validate env, config and packs, then exit')
    parser.add_argument('--env-file', type=Path, default=ENV_FILE, help='default: api-python/.env')
    args = parser.parse_args(argv)
    try:
        found = load_environment(args.env_file)
    except (EnvFileError, OSError, UnicodeError) as exc:
        print(f'FAIL  env file: {_brief(exc)}', file=sys.stderr)
        return 2
    if args.check:
        return run_checks(found)
    if not found:
        print(f'run_local: {args.env_file} not found; using the process environment '
              '(run scripts/setup_local.py first)', file=sys.stderr)
    return start(args.port, not args.no_worker)


if __name__ == '__main__':
    raise SystemExit(main())
