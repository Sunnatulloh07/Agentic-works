"""Durable read-sync leases, cursor CAS, idempotent pages and versioned records.

Provider-specific walkers must translate events into monotonically versioned
records. This module never invents provider cursor ordering or fetches a URL.
A page, records, dedup receipt and next cursor commit in ONE transaction.
"""
import json
import secrets
from .engine import Conflict, Forbidden, NotFound, encode, digest
from .oauth import bounded, ident

SCHEMA = '''
CREATE TABLE IF NOT EXISTS p_sync_streams(
 tenant TEXT NOT NULL, connection TEXT NOT NULL, stream TEXT NOT NULL,
 connection_generation INTEGER NOT NULL, cursor TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 0, claim TEXT NOT NULL DEFAULT '',
 lease REAL NOT NULL DEFAULT 0, owner TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(tenant,connection,stream));
CREATE TABLE IF NOT EXISTS p_sync_pages(
 tenant TEXT NOT NULL, connection TEXT NOT NULL, stream TEXT NOT NULL,
 page_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL,
 PRIMARY KEY(tenant,connection,stream,page_key));
CREATE TABLE IF NOT EXISTS p_sync_versions(
 tenant TEXT NOT NULL, connection TEXT NOT NULL, stream TEXT NOT NULL,
 record_id TEXT NOT NULL, version INTEGER NOT NULL, fingerprint TEXT NOT NULL,
 PRIMARY KEY(tenant,connection,stream,record_id,version));
CREATE TABLE IF NOT EXISTS p_sync_records(
 tenant TEXT NOT NULL, connection TEXT NOT NULL, stream TEXT NOT NULL,
 record_id TEXT NOT NULL, version INTEGER NOT NULL, deleted INTEGER NOT NULL,
 body TEXT NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY(tenant,connection,stream,record_id));
'''


class SyncStore:
    def __init__(self, engine): self.e = engine

    def _authority(self, db, tenant, actor):
        self.e.require_active(db, tenant)
        self.e.require_authority(db, tenant, 'web', actor, ('owner','integrator'))

    def _connection(self, db, tenant, connection):
        row=db.execute('SELECT generation,status FROM p_oauth_connections WHERE tenant=? AND id=?',(tenant,connection)).fetchone()
        if not row or row['status'] not in {'active','refreshing'}: raise Forbidden('Active OAuth connection required for sync')
        return row

    def _row(self, db, tenant, connection, stream):
        row=db.execute('SELECT * FROM p_sync_streams WHERE tenant=? AND connection=? AND stream=?',(tenant,connection,stream)).fetchone()
        if not row:raise NotFound('Sync stream not found')
        return row

    def claim(self, tenant, connection, stream, actor, lease_seconds=60):
        for value in (tenant,connection,stream):ident(value)
        bounded(actor)
        if type(lease_seconds) is not int or not 5<=lease_seconds<=300:raise ValueError('Sync lease must be 5..300 seconds')
        with self.e.tx() as db:
            self._authority(db,tenant,actor); conn=self._connection(db,tenant,connection)
            db.execute('INSERT OR IGNORE INTO p_sync_streams(tenant,connection,stream,connection_generation) VALUES(?,?,?,?)',
                       (tenant,connection,stream,conn['generation']))
            row=self._row(db,tenant,connection,stream)
            if row['connection_generation']!=conn['generation']:raise Conflict('Connection generation changed; owner must reset sync')
            if row['claim'] and row['lease']>self.e.clock():raise Conflict('Sync lease already owned')
            claim=secrets.token_hex(24)
            db.execute('UPDATE p_sync_streams SET claim=?,lease=?,owner=? WHERE tenant=? AND connection=? AND stream=?',
                       (claim,self.e.clock()+lease_seconds,actor,tenant,connection,stream))
            return {'claim':claim,'cursor':row['cursor'],'revision':row['revision'],
                    'connection_generation':conn['generation'],'expires':self.e.clock()+lease_seconds}

    def _owned(self, db, tenant, connection, stream, actor, claim):
        self._authority(db,tenant,actor);conn=self._connection(db,tenant,connection)
        row=self._row(db,tenant,connection,stream)
        if row['owner']!=actor or row['claim']!=claim or not claim or row['lease']<=self.e.clock():
            raise Conflict('Sync claim stale')
        if row['connection_generation']!=conn['generation']:raise Conflict('Sync credential fenced')
        return row

    def renew(self, tenant, connection, stream, actor, claim, lease_seconds=60):
        if type(lease_seconds) is not int or not 5<=lease_seconds<=300:raise ValueError('Invalid lease')
        with self.e.tx() as db:
            self._owned(db,tenant,connection,stream,actor,claim)
            db.execute('UPDATE p_sync_streams SET lease=? WHERE tenant=? AND connection=? AND stream=?',
                       (self.e.clock()+lease_seconds,tenant,connection,stream))

    def _events(self, events):
        if not isinstance(events,list) or len(events)>100:raise ValueError('Sync page requires at most 100 events')
        validated=[];keys=set()
        for event in events:
            if not isinstance(event,dict) or set(event)!={'id','version','deleted','data'}:raise ValueError('Invalid sync event fields')
            bounded(event['id'],256)
            if type(event['version']) is not int or not 0<=event['version']<2**63:raise ValueError('Bounded monotonic provider version required')
            if type(event['deleted']) is not bool or not isinstance(event['data'],dict):raise ValueError('Invalid sync record')
            if event['deleted'] and event['data']:raise ValueError('Tombstone must not retain content')
            if len(encode(event['data']).encode())>20000:raise ValueError('Sync record exceeds limit')
            key=(event['id'],event['version'])
            if key in keys:raise ValueError('Duplicate version inside page')
            keys.add(key);validated.append(event)
        if len(encode(validated).encode())>500000:raise ValueError('Sync page exceeds limit')
        return validated

    def commit(self, tenant, connection, stream, actor, claim, page_key, expected_cursor, next_cursor, events, *, guard=None):
        bounded(page_key,256)
        for cursor in (expected_cursor,next_cursor):
            if not isinstance(cursor,str) or len(cursor)>4096 or any(ord(c)<32 for c in cursor):raise ValueError('Invalid opaque cursor')
        events=self._events(events)
        fingerprint=digest({'cursor':expected_cursor,'next':next_cursor,'events':events})
        with self.e.tx() as db:
            row=self._owned(db,tenant,connection,stream,actor,claim)
            if guard is not None: guard(db)
            old=db.execute('SELECT * FROM p_sync_pages WHERE tenant=? AND connection=? AND stream=? AND page_key=?',
                           (tenant,connection,stream,page_key)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise Conflict('Sync page key reused with different payload')
                return json.loads(old['receipt'])
            if row['cursor']!=expected_cursor:raise Conflict('Sync cursor changed')
            updated=0
            for event in events:
                ef=digest(event)
                prior=db.execute('SELECT fingerprint FROM p_sync_versions WHERE tenant=? AND connection=? AND stream=? AND record_id=? AND version=?',
                                 (tenant,connection,stream,event['id'],event['version'])).fetchone()
                if prior and prior['fingerprint']!=ef:raise Conflict('Provider record version reused with different content')
                db.execute('INSERT OR IGNORE INTO p_sync_versions VALUES(?,?,?,?,?,?)',
                           (tenant,connection,stream,event['id'],event['version'],ef))
                change=db.execute('INSERT INTO p_sync_records VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(tenant,connection,stream,record_id) '
                                  'DO UPDATE SET version=excluded.version,deleted=excluded.deleted,body=excluded.body,updated=excluded.updated '
                                  'WHERE excluded.version>p_sync_records.version',
                                  (tenant,connection,stream,event['id'],event['version'],int(event['deleted']),encode(event['data']),self.e.clock()))
                updated+=change.rowcount
            receipt={'page_key':page_key,'revision':row['revision']+1,'changed_records':updated,'next_cursor':next_cursor}
            db.execute('UPDATE p_sync_streams SET cursor=?,revision=revision+1 WHERE tenant=? AND connection=? AND stream=?',
                       (next_cursor,tenant,connection,stream))
            db.execute('INSERT INTO p_sync_pages VALUES(?,?,?,?,?,?)',(tenant,connection,stream,page_key,fingerprint,encode(receipt)))
            self.e.audit(db,tenant,'','sync.page_committed',actor,{'connection':connection,'stream':stream,'revision':receipt['revision'],'changed_records':updated})
            return receipt

    def release(self, tenant, connection, stream, actor, claim):
        with self.e.tx() as db:
            self._owned(db,tenant,connection,stream,actor,claim)
            db.execute("UPDATE p_sync_streams SET claim='',lease=0 WHERE tenant=? AND connection=? AND stream=?",(tenant,connection,stream))

    def reset(self, tenant, connection, stream, actor, *, guard=None):
        # Reset is an explicit destructive owner action, never automatic after
        # OAuth account changes. Old records must not leak into a new account.
        with self.e.tx() as db:
            self.e.require_authority(db,tenant,'web',actor,('owner',));self.e.require_active(db,tenant)
            conn=self._connection(db,tenant,connection)
            if guard is not None: guard(db)
            for table in ('p_sync_pages','p_sync_versions','p_sync_records','p_sync_streams'):
                db.execute('DELETE FROM '+table+' WHERE tenant=? AND connection=? AND stream=?',(tenant,connection,stream))
            db.execute('INSERT INTO p_sync_streams(tenant,connection,stream,connection_generation) VALUES(?,?,?,?)',
                       (tenant,connection,stream,conn['generation']))
            self.e.audit(db,tenant,'','sync.reset',actor,{'connection':connection,'stream':stream})

    def records(self, tenant, connection, stream, actor, limit=100, *, after=''):
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid record limit')
        if after: bounded(after,256)
        elif after != '': raise ValueError('Invalid record cursor')
        with self.e.read() as db:
            self._authority(db,tenant,actor);conn=self._connection(db,tenant,connection)
            stream_row=self._row(db,tenant,connection,stream)
            if stream_row['connection_generation']!=conn['generation']:raise Conflict('Sync credential changed')
            rows=db.execute('SELECT record_id,version,deleted,body FROM p_sync_records WHERE tenant=? AND connection=? AND stream=? AND record_id>? ORDER BY record_id LIMIT ?',
                            (tenant,connection,stream,after,limit)).fetchall()
            return [{'id':r['record_id'],'version':r['version'],'deleted':bool(r['deleted']),'untrusted_content':True,'data':json.loads(r['body'])} for r in rows]
