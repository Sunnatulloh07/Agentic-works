"""Consistent SQLite snapshot using the backup API, not a copy of live WAL files.

Artifacts contain customer PII. Encryption, remote storage and retention are
operator responsibilities; this utility is NOT disaster-recovery certification.
"""
import argparse
import os
import sqlite3
from pathlib import Path


def backup(source,destination):
    source=Path(source).resolve(strict=True);destination=Path(destination).absolute()
    if source==destination.resolve():raise ValueError('Different destination required')
    # Refuse overwrite and symlinks. Umask-independent private artifact.
    fd=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    src=dst=None
    try:
        src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
        dst=sqlite3.connect(destination)
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Integrity check failed')
        dst.commit()
    except BaseException:
        if dst:dst.close();dst=None
        destination.unlink(missing_ok=True)
        raise
    finally:
        if src:src.close()
        if dst:dst.close()
    return destination


def main():
    p=argparse.ArgumentParser(description='Back up SQLite, or restore to a NEW offline path')
    p.add_argument('source');p.add_argument('destination');args=p.parse_args()
    backup(args.source,args.destination)
    print('Snapshot validated. No credentials printed. Destination is not encrypted.')


if __name__=='__main__':main()
