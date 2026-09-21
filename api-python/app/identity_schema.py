"""Additive v0.3 directory migration, called inside BEGIN IMMEDIATE.

Legacy session rows cannot prove a membership version and are invalidated.
No existing customer, task, user or workspace is removed.
"""
import time


def migrate(c):
    c.execute('CREATE TABLE IF NOT EXISTS p_identity_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)')
    if c.execute('SELECT 1 FROM p_identity_migrations WHERE version=1').fetchone():
        return
    columns = {r[1] for r in c.execute('PRAGMA table_info(p_sessions)')}
    for name, definition in [('family_id', "TEXT NOT NULL DEFAULT ''"), ('membership_version', 'INTEGER NOT NULL DEFAULT 0')]:
        if name not in columns:
            c.execute(f'ALTER TABLE p_sessions ADD COLUMN {name} {definition}')
    c.execute("UPDATE p_sessions SET family_id=id,revoked_at=? WHERE family_id=''", (time.time(),))
    c.execute('CREATE INDEX IF NOT EXISTS ix_session_family ON p_sessions(family_id)')
    c.execute('''CREATE TABLE IF NOT EXISTS p_identity_audit(
      id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, actor TEXT NOT NULL,
      action TEXT NOT NULL, target TEXT NOT NULL, created REAL NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS p_auth_limits(
      bucket TEXT NOT NULL, window INTEGER NOT NULL, count INTEGER NOT NULL,
      PRIMARY KEY(bucket,window))''')
    c.execute('CREATE INDEX IF NOT EXISTS ix_auth_limits_window ON p_auth_limits(window)')
    c.execute('CREATE TABLE IF NOT EXISTS p_freeze(tenant TEXT PRIMARY KEY, stopped INTEGER NOT NULL)')
    c.execute('INSERT INTO p_identity_migrations VALUES(1,?)', (time.time(),))
