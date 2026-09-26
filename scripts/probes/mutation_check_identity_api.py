"""Mutation harness: does §163's suite actually fail when the fix is reverted?

A passing test is not evidence until it has been seen to fail.  Each mutation below
re-introduces exactly one of the defects §163 closed, in the file that held it.  A
mutation that leaves the suite green is a gap in the suite, not a harmless edit.

Three properties this harness has to have, each learned the hard way:

* **It must survive being killed.**  An interrupted run of a sibling probe left
  ``app/auth.py`` mutated -- ``SESSION_TOKEN_TTL_SECONDS`` sitting at 9000 instead of
  900 -- and it was found by a ``git diff`` at the next session start rather than by
  anything in the tooling.  A ``finally`` block does not run if the process is killed,
  so every mutation is now written through a backup that the *next* run restores before
  it does anything else.  Self-healing, not self-discipline.
* **It must not depend on the working directory.**  A relative ``TARGET`` only resolved
  from ``api-python/``, and silently failed anywhere else.  Paths are derived from
  ``__file__``.
* **It must say how much of an anchor it mutated.**  ``max_length=store.MAX_WORKSPACE_ID_CHARS)``
  occurs three times; ``replace(..., 1)`` touches one.  The count is printed, so partial
  coverage is visible instead of looking like full coverage.

Run:  python scripts/probes/mutation_check_identity_api.py            (full matrix, ~1 min)
      python scripts/probes/mutation_check_identity_api.py --list
      python scripts/probes/mutation_check_identity_api.py --start 0 --count 6

The full matrix outlives a short command budget, so ``--start``/``--count`` split it into
batches.  A batch that is killed still heals, because the backup is written before the
mutation and removed only after the restore -- that ordering is the whole mechanism.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'api-python'
BACKUPS = Path(__file__).resolve().parent / '.mutation_backups'

API = ROOT / 'app' / 'identity_api.py'
STORE = ROOT / 'app' / 'identity_store.py'
SUITE = 'runtime_tests.test_identity_api_bounds'

# (name, target, correct source, reverted source)
MUTATIONS = [
    ('restate the e-mail ceiling as a literal', API,
     'max_length=store.MAX_EMAIL_CHARS', 'max_length=320'),
    ('restate the name ceiling as a literal', API,
     "display_name:str=Field(min_length=store.MIN_NAME_CHARS,max_length=store.MAX_NAME_CHARS)",
     'display_name:str=Field(min_length=1,max_length=256)'),
    ('restate the invitation window as literals', API,
     'default=store.INVITATION_TTL_SECONDS', 'default=86400'),
    ('slice the bearer prefix by a literal 7', API,
     'auth[len(BEARER_PREFIX):]', 'auth[7:]'),
    ('cap the access token at a local 900', API,
     'min(SESSION_TOKEN_TTL_SECONDS,', 'min(900,'),
    ('drop the access-token floor back to 1', API,
     'if ttl<MIN_TOKEN_TTL_SECONDS:', 'if ttl<1:'),
    ('equalise the per-client budget with the per-account one', API,
     'CLIENT_THROTTLE_LIMIT = 60', 'CLIENT_THROTTLE_LIMIT = 20'),
    ('hardcode Retry-After instead of deriving it', API,
     "headers={'Retry-After':str(store.THROTTLE_WINDOW_SECONDS)}",
     "headers={'Retry-After':'900'}"),
    ('remove the admin-token floor', API,
     'len(required)<MIN_ADMIN_TOKEN_CHARS', 'len(required)<0'),
    ('let owner be invited', API,
     "role:Literal['operator','integrator','viewer']",
     "role:Literal['owner','operator','integrator','viewer']"),
    ('widen the workspace ceiling past the store', API,
     'max_length=store.MAX_WORKSPACE_ID_CHARS)', 'max_length=128)'),
    # The §163 audit additions.  Each of these was invisible to the suite before the
    # audit: the first two are wrong-owner rewires that value equality cannot see, and
    # the rest are store-side bounds the HTTP suite did not reach at all.
    ('rewire the password ceiling to the token constant', API,
     'max_length=store.MAX_PASSWORD_CHARS)', 'max_length=store.MAX_TOKEN_CHARS)'),
    ('rewire the refresh-token floor to the password floor', API,
     'refresh_token:SecretStr=Field(min_length=store.MIN_TOKEN_CHARS,',
     'refresh_token:SecretStr=Field(min_length=store.MIN_CANDIDATE_PASSWORD_CHARS,'),
    ('let the store ignore its own workspace floor', STORE,
     'if not MIN_WORKSPACE_ID_CHARS <= len(value) or not SLUG_RE.fullmatch(value):',
     'if not SLUG_RE.fullmatch(value):'),
    ('drop the session entropy back to a literal', STORE,
     'token_urlsafe(SESSION_TOKEN_BYTES)', 'token_urlsafe(48)'),
    ('drop the invitation entropy back to a literal', STORE,
     'token_urlsafe(INVITATION_TOKEN_BYTES)', 'token_urlsafe(32)'),
    ('restore the bare plan and region ceilings', STORE,
     "_name(plan,'plan',MAX_PLAN_CHARS), _name(region,'region',MAX_REGION_CHARS)",
     "_name(plan,'plan',64), _name(region,'region',32)"),
    ('prune the throttle window by a literal', STORE,
     'window-THROTTLE_RETENTION_WINDOWS', 'window-2'),
]


def read(target):
    return target.read_text(encoding='utf-8', newline='')


def write(target, text):
    target.write_text(text, encoding='utf-8', newline='')


def heal():
    """Restore anything a previous, interrupted run left mutated.

    Runs before anything else, so a killed run costs one stale tree instead of a silent
    edit that survives into the next commit.  The backup holds the *pristine* text, so
    restoring it is safe even if the file has since been hand-edited back -- the two are
    then identical.
    """
    healed = []
    if not BACKUPS.exists():
        return healed
    known = {path.name: path for path in (API, STORE)}
    for backup in sorted(BACKUPS.glob('*.bak')):
        target = known.get(backup.name[:-len('.bak')])
        if target is None:
            backup.unlink()
            continue
        write(target, read(backup))
        backup.unlink()
        healed.append(target)
    return healed


def run_suite():
    env = dict(os.environ, ENV='test', ALLOW_INSECURE_DEV='true',
               PYTHONIOENCODING='utf-8')
    done = subprocess.run([sys.executable, '-m', 'unittest', SUITE],
                          capture_output=True, text=True, env=env, cwd=ROOT)
    return done.returncode, done.stderr


def main(argv):
    for target in heal():
        print(f'HEALED {target}: restored from a backup an interrupted run left behind')

    start, count = 0, len(MUTATIONS)
    if '--list' in argv:
        for index, (name, target, _, _) in enumerate(MUTATIONS):
            print(f'{index:>3}  {target.name:<18} {name}')
        return 0
    if '--start' in argv:
        start = int(argv[argv.index('--start') + 1])
    if '--count' in argv:
        count = int(argv[argv.index('--count') + 1])
    batch = MUTATIONS[start:start + count]
    if not batch:
        print('nothing to do in that range')
        return 0
    print(f'running mutations {start}..{start + len(batch) - 1} of {len(MUTATIONS)}')

    BACKUPS.mkdir(exist_ok=True)
    pristine = {target: read(target) for target in (API, STORE)}
    gaps, caught = [], []
    try:
        for name, target, good, bad in batch:
            occurrences = pristine[target].count(good)
            if occurrences == 0:
                print(f'SKIP  {name}: anchor not found (harness is stale)')
                gaps.append(name)
                continue
            backup = BACKUPS / (target.name + '.bak')
            write(backup, pristine[target])
            try:
                write(target, pristine[target].replace(good, bad, 1))
                code, err = run_suite()
                if code == 0:
                    print(f'GAP   {name}: suite stayed GREEN')
                    gaps.append(name)
                else:
                    failed = [line for line in err.splitlines()
                              if line.startswith(('FAIL:', 'ERROR:'))]
                    scope = '' if occurrences == 1 else f'  [{occurrences} sites, 1 mutated]'
                    print(f'CAUGHT {name}{scope}')
                    for line in failed[:3]:
                        print(f'         {line}')
                    caught.append(name)
            finally:
                write(target, pristine[target])
                backup.unlink(missing_ok=True)
    finally:
        for target, text in pristine.items():
            write(target, text)
        for backup in BACKUPS.glob('*.bak'):
            backup.unlink()

    print(f'\ncaught {len(caught)}/{len(batch)}')
    if gaps:
        print('gaps: ' + '; '.join(gaps))
    return 1 if gaps else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
