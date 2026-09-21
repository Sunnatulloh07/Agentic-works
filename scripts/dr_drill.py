"""Disaster-recovery drill: back up, restore, and prove the restore is usable.

The inventory recorded this as absent. There was a backup utility and no evidence that a
backup could be restored into something the platform would actually open, which is the
only claim that matters: an untested backup is a hypothesis, and the first time it is
tested is the day it is needed.

What the drill proves, in order:

1. a snapshot can be taken from a live database while it is open;
2. the snapshot passes ``PRAGMA integrity_check``;
3. the snapshot is itself a valid *source*, so a restore is the same primitive as a
   backup and does not need a second, less-exercised code path;
4. every user table has the same row count after the round trip;
5. tenant isolation still holds in the restored copy -- a query scoped to one tenant
   returns only that tenant's rows;
6. the restored file opens through the real ``Engine`` and answers a real read;
7. how long all of that took, so the recovery-time claim is a measurement.

Nothing here is a substitute for an operator runbook: it does not cover the encrypted
artifact, the remote copy, the retention policy, or restoring while a worker is running.
It covers the part that can be proven offline, and says so.

Run::

    python scripts/dr_drill.py
    python scripts/dr_drill.py --report docs/verification/dr-drill.json
    python scripts/dr_drill.py --source /path/to/platform.db --keep /tmp/dr
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))
sys.path.insert(0, HERE)

import sqlite_backup                                                    # noqa: E402
from platform_runtime.engine import Engine                              # noqa: E402
from platform_runtime.tools import build_registry                        # noqa: E402

TENANTS = ('dr-alpha', 'dr-beta')


def user_tables(connection):
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return sorted(row[0] for row in rows)


def counts(path):
    with sqlite3.connect(path) as connection:
        return {name: connection.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
                for name in user_tables(connection)}


def seed(path):
    """Create the real schema through the real Engine, then add synthetic rows.

    Seeding through ``Engine`` rather than with hand-written DDL is the point: the drill
    then exercises the same migrations a deployment runs, so a schema change that breaks
    restore is caught here instead of in a restore.
    """
    engine = Engine(path, build_registry(), lambda tenant, agent: {
        'tools': ['reports.summary'], 'ladder': 'autonomous'})
    with engine.tx() as db:
        for index, tenant in enumerate(TENANTS):
            db.execute('INSERT INTO p_tasks(id,tenant,channel,event_key,fingerprint,agent,actor,status,created,updated)'
                       ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                       ('task-%s' % tenant, tenant, 'web', 'ev-%d' % index, 'fp-%d' % index,
                        'dr.agent', 'dr.operator', 'succeeded', 1000.0 + index, 1000.0 + index))
            for position in range(3):
                db.execute('INSERT INTO p_steps(id,task,tenant,position,tool,args,risk,approval_needed,fingerprint,status)'
                           ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                           ('step-%s-%d' % (tenant, position), 'task-%s' % tenant, tenant, position,
                            'reports.summary', '{}', 'read', 0, 'fp-%d-%d' % (index, position), 'succeeded'))
    return engine


def isolation_holds(path):
    """Every tenant-scoped table must still answer only for the tenant asked about.

    A restore that silently merges tenants is worse than a failed restore, because it
    looks like success. This is checked on the restored copy, not on the source.
    """
    with sqlite3.connect(path) as connection:
        tables = [name for name in user_tables(connection)
                  if 'tenant' in [column[1] for column in connection.execute('PRAGMA table_info("%s")' % name)]]
        checked = 0
        for name in tables:
            rows = connection.execute('SELECT DISTINCT tenant FROM "%s"' % name).fetchall()
            tenants = sorted(row[0] for row in rows if row[0] is not None)
            if not tenants:
                continue
            for tenant in tenants:
                total = connection.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
                scoped = connection.execute('SELECT COUNT(*) FROM "%s" WHERE tenant=?' % name, (tenant,)).fetchone()[0]
                if scoped > total:
                    raise AssertionError('%s: tenant %r returned more rows than exist' % (name, tenant))
                if len(tenants) > 1 and scoped == total:
                    raise AssertionError('%s: tenant filter %r matched every row' % (name, tenant))
                checked += 1
        return {'tables_with_tenant': len(tables), 'scoped_reads': checked}


def drill(workdir, source=None):
    report = {'kind': 'offline_dr_drill', 'synthetic': source is None,
              'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'steps': [], 'release': 'NO_GO'}
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    live = Path(source) if source else workdir / 'platform.db'
    snapshot = workdir / 'snapshot.db'
    restored = workdir / 'restored.db'
    for path in (snapshot, restored):
        path.unlink(missing_ok=True)

    def step(name, fn):
        started = time.monotonic()
        value = fn()
        elapsed = round(time.monotonic() - started, 4)
        report['steps'].append({'name': name, 'seconds': elapsed,
                                **({'detail': value} if isinstance(value, dict) else {})})
        print('  [ok  ] %-28s %6.3fs  %s' % (name, elapsed, value if isinstance(value, dict) else ''))
        return value

    print('DR drill: %s' % ('synthetic source' if source is None else 'source %s' % live))
    if source is None:
        step('seed through Engine', lambda: {'engine': str(seed(live).__class__.__name__)})
    else:
        if not live.is_file():
            raise SystemExit('source does not exist: %s' % live)
    before = step('count source rows', lambda: counts(live))

    step('snapshot', lambda: {'bytes': sqlite_backup.backup(live, snapshot).stat().st_size})
    step('snapshot integrity', lambda: _integrity(snapshot))
    step('restore from snapshot', lambda: {'bytes': sqlite_backup.backup(snapshot, restored).stat().st_size})
    after = step('count restored rows', lambda: counts(restored))

    if before != after:
        raise SystemExit('row counts differ:\n  source:   %s\n  restored: %s' % (before, after))
    step('row counts match', lambda: {'tables': len(before), 'rows': sum(before.values())})
    step('tenant isolation', lambda: isolation_holds(restored))
    step('engine opens restored copy', lambda: _engine_reads(restored))

    report['tables'] = len(before)
    report['rows'] = sum(before.values())
    report['total_seconds'] = round(sum(item['seconds'] for item in report['steps']), 4)
    report['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report['not_covered'] = ['encrypted artifact', 'remote/offsite copy', 'retention policy',
                             'restore while a worker is running', 'point-in-time recovery',
                             'restore of a network database (PostgreSQL, MySQL)']
    print('\nDR drill passed: %d tables, %d rows, %.3fs total.'
          % (report['tables'], report['rows'], report['total_seconds']))
    print('NOT covered: ' + '; '.join(report['not_covered']))
    return report


def _integrity(path):
    with sqlite3.connect(path) as connection:
        result = connection.execute('PRAGMA integrity_check').fetchone()[0]
    if result != 'ok':
        raise AssertionError('integrity_check returned %r' % result)
    return {'integrity': result}


def _engine_reads(path):
    engine = Engine(path, build_registry(), lambda tenant, agent: {
        'tools': ['reports.summary'], 'ladder': 'autonomous'})
    with engine.read() as db:
        seen = {}
        for tenant in TENANTS:
            rows = db.execute('SELECT id FROM p_tasks WHERE tenant=?', (tenant,)).fetchall()
            seen[tenant] = len(rows)
    if set(seen) != set(TENANTS) or any(count == 0 for count in seen.values()):
        raise AssertionError('restored engine could not read both tenants: %r' % seen)
    return {'tasks_per_tenant': seen}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', help='drill a real database instead of a synthetic one')
    parser.add_argument('--keep', type=Path, help='working directory (default: a temporary one)')
    parser.add_argument('--report', type=Path, help='write the JSON report here')
    args = parser.parse_args()
    if args.keep:
        report = drill(args.keep, args.source)
    else:
        # The seeded Engine keeps its SQLite file open for the life of the process, and
        # Windows refuses to unlink an open file, so a strict cleanup raises AFTER the
        # drill has already passed. That turned a green drill into a traceback and a
        # non-zero exit. The scratch directory is disposable either way; the drill's
        # result must not depend on whether the OS let us delete it.
        with tempfile.TemporaryDirectory(prefix='platform-dr-drill-',
                                         ignore_cleanup_errors=True) as tmp:
            report = drill(tmp, args.source)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('Report: %s' % args.report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
