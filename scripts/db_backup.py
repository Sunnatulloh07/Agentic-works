"""Consistent SQLite online backup + integrity validation. Destination contains tenant data."""
import argparse
import sqlite3
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('destination');args=p.parse_args()
source=Path(args.source).resolve();dest=Path(args.destination).resolve()
if not source.is_file() or dest.exists():raise SystemExit('Source must exist; destination must not exist')
with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(dest) as b:
    a.backup(b)
    if b.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('Backup integrity failed')
dest.chmod(0o600)
print('Backup integrity: ok. Store encrypted; stop API/worker before restoring.')
