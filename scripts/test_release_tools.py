import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from sqlite_backup import backup
from check_release import check


class ReleaseToolsTests(unittest.TestCase):
    def test_wal_backup_restore_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'live.db';snapshot=Path(d)/'snapshot.db';restore=Path(d)/'restore.db'
            c=sqlite3.connect(source)
            try:
                c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE data(id INTEGER)');c.execute('INSERT INTO data VALUES(42)');c.commit()
                backup(source,snapshot);backup(snapshot,restore)
                target=sqlite3.connect(restore)
                try:self.assertEqual(42,target.execute('SELECT id FROM data').fetchone()[0])
                finally:target.close()
                if os.name == 'posix':
                    # Windows has no POSIX mode bits; chmod only toggles the read-only
                    # flag there, so the private-artifact check is a POSIX guarantee.
                    self.assertEqual(0o600,snapshot.stat().st_mode & 0o777)
                with self.assertRaises(FileExistsError):backup(source,snapshot)
                with self.assertRaises(ValueError):backup(source,source)
            finally:c.close()
    def test_release_cannot_pass_on_empty_evidence(self):
        self.assertEqual('NO_GO',check({})['release'])
    def test_status_without_evidence_is_not_pass(self):
        self.assertIn('http_integration',check({'gates':{'http_integration':{'status':'PASS'}}})['blockers'])


if __name__=='__main__':unittest.main()
