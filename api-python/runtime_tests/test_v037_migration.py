"""Upgrade a frozen v0.3.6 schema, not an already-new Engine database."""
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
import unittest
from platform_runtime.engine import Engine
from platform_runtime.tools import build_registry


class V037MigrationTests(unittest.TestCase):
    def test_original_schema_upgrades_without_erasing_old_records(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'upgrade.db'
            # closing() is required: the sqlite3 context manager commits but does not
            # close, and Windows keeps the file locked while a handle is open, so the
            # Engine below would fail with WinError 32.
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript((Path(__file__).parent/'fixtures/v036_schema.sql').read_text(encoding='utf-8'))
                db.execute("INSERT INTO p_records VALUES('tenant','order','old','{\"item\":\"retained\"}',1000)")
                self.assertEqual(0,db.execute("SELECT count(*) FROM sqlite_master WHERE name='p_oauth_connections'").fetchone()[0])
            for _ in range(2):
                engine=Engine(path,build_registry(),lambda t,a:{'tools':[],'ladder':'autonomous'})
                with engine.read() as db:
                    self.assertEqual('{"item":"retained"}',db.execute('SELECT body FROM p_records').fetchone()[0])
                    self.assertEqual([1,2,3,4,5,6,7,8,9],[r[0] for r in db.execute('SELECT version FROM p_migrations ORDER BY version')])
                    for table in ['p_oauth_connections','p_oauth_states','p_oauth_revocations','p_google_dispatch','p_sync_streams','p_sync_records']:
                        self.assertEqual(1,db.execute('SELECT count(*) FROM sqlite_master WHERE name=?',(table,)).fetchone()[0])
