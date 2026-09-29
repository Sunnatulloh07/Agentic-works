---
name: platform-verify
description: Run the Agent Platform's full verification matrix (runtime unittest, integration pytest, scripts tests, e2e smoke, UI typecheck/build/tests, runner tests, optional manifest) and report one compact pass/fail table. Use this whenever the user says "verify", "run all tests", "run the tests", "before commit", "is it green", "are we done", "check nothing regressed", or before you claim any work is done, fixed, or passing in this repo, even if they do not name the skill. Knows the Windows-only baseline failures so they are not mistaken for regressions.
---

# platform-verify

One command runs every check this repo relies on, so "done" is backed by evidence
instead of a guess. It exists because the checks live in five different directories
with different runners and env vars, and the Windows baseline (13 known POSIX-only
failures) is easy to misread as either "all broken" or "all fine".

## When to use

- Before saying a task is done, fixed, or passing; before `git commit` (CLAUDE.md:
  no commit unless the pack tests are green).
- After touching `api-python/`, `apps/ui`, `apps/runner`, `scripts/`, or `packs/`.
- When the user asks "is it green?" or "did I break anything?".

Use `--only` for a fast loop while iterating, but run the full matrix once before
the final claim.

## How to run

From the repo root (Python 3.14, Node 24 on PATH):

```
python .claude/skills/platform-verify/scripts/verify.py                 # full matrix
python .claude/skills/platform-verify/scripts/verify.py --fast          # skip e2e + UI build
python .claude/skills/platform-verify/scripts/verify.py --only runtime,scripts
python .claude/skills/platform-verify/scripts/verify.py --manifest      # + regenerate/verify MANIFEST.sha256
```

Flags: `--only a,b` (steps: runtime, integration, scripts, e2e, ui, runner, manifest),
`--fast`, `--manifest`, `--pylib <dir>`, `--timeout-scale <float>`.

`cryptography` may be missing from the global Python. Point at a directory that has it
with `--pylib <dir>` or env `PLATFORM_VERIFY_PYLIB` (it is prepended to PYTHONPATH).
Do not hardcode a path in the script; find the current one (a session scratchpad
`pylib` folder, or `pip install --target <dir> cryptography`).

Full runs take a while (runtime is ~3000 tests). Run in the background if your
tool has a timeout, then read the table when it finishes.

## What each step runs

| step | command (cwd) |
|---|---|
| runtime | `python -m unittest discover -s runtime_tests -t runtime_tests` (api-python) |
| integration | `ENV=test ALLOW_INSECURE_DEV=true PIPELINE_MODE=platform IDENTITY_DIRECTORY=false python -m pytest integration_tests -q` (api-python) |
| scripts | `python -m unittest discover -s scripts -p "test_*.py"` (root) |
| e2e | `python scripts/e2e_smoke.py` (root) |
| ui | `npm run typecheck && npm run build && node --test lib/*.test.mjs` (apps/ui) |
| runner | `node --test apps/runner/test.js` (root) |
| manifest | `generate_manifest.py` then `verify_manifest.py` (only with `--manifest`; it rewrites `MANIFEST.sha256`) |

Full output of every step is saved to a temp dir; the table's last column is the log path.

## Reading the result

The script prints `| step | result | counts | time | log |` and a `RESULT:` line, and exits
non-zero on any regression.

- `PASS`: green.
- `PASS*`: runtime on Windows, and every failure is inside `test_portable_fs`,
  `test_macos_bundle`, or `test_foundation_v02` (baseline: failures=1, errors=12,
  skipped=1, recorded in `runtime_tests/test_platform_baseline.py`). Acceptable, but
  these tests are **unverified** on Windows, not passing; say so. If the counts
  differ from the baseline the note says so; investigate (fewer may mean the
  baseline file needs updating, more is a regression).
- `REGRESSION`: a failure outside the allowance. The allowance applies only when
  `os.name == 'nt'` and only to those three modules; on Linux/macOS any failure is `FAIL`.
- `REVIEW` (runner): POSIX-only failures are expected on Windows 11, but the script does
  not excuse them by name. Open the log, confirm the failing tests are POSIX-specific
  (symlink, chmod, signals, etc.), and report the counts.
- `FAIL` / `ERROR` / `TIMEOUT`: real problem, or the step could not run (missing
  dependency, import error). Read the log; do not retry blindly.

## Rules

1. Never claim "done", "green" or "all tests pass" from the table alone. Read it, open the
   log of anything that is not plain `PASS`, and quote the actual counts in your report.
2. A skipped step (`--only`, `--fast`) is unverified; list it as not run.
3. Do not fix a red result by loosening the allowance or editing tests to match; find
   the cause. If the Windows baseline legitimately changes, update
   `test_platform_baseline.py` as its own reviewed change.
4. `--manifest` rewrites `MANIFEST.sha256`; use it only when the change should update it.
5. End the reply with changed files, test results and next step (CLAUDE.md rule).
