"""Yagona saqlash qatlami — SQLite/WAL (docs: Postgres kelganda shu interfeys).

Barcha modullar shu yerga murojaat qiladi. Yo'l har chaqiruvda env'dan
olinadi (APP_DB) — test izolyatsiyasi uchun. Postgres/Redis kelganda
faqat shu fayl ichi almashadi, route'lar tegilmaydi (DIP).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from typing import TYPE_CHECKING
from .domain_enums import DeliveryStatus
if TYPE_CHECKING:
    from .domain import InboundMessage

_lock = threading.RLock()
_local = threading.local()

# Declared bounds; pinned literally in runtime_tests/test_storage_bounds.py.
# A connection timeout, a delivery lease and an error ceiling are all numbers a
# caller depends on, and none of them was addressable before they were named.
CONNECT_TIMEOUT_SECONDS = 30.0
DELIVERY_LEASE_SECONDS = 60
MAX_DELIVERY_ERROR_CHARS = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals(
  id TEXT PRIMARY KEY, tenant TEXT, kind TEXT, agent TEXT, summary TEXT,
  payload TEXT, status TEXT DEFAULT 'pending', reason TEXT DEFAULT '',
  decided_at INTEGER DEFAULT 0, decided_by TEXT DEFAULT '', created_at INTEGER);
CREATE TABLE IF NOT EXISTS orders(
  id TEXT PRIMARY KEY, tenant TEXT, update_id INTEGER, customer TEXT, phone TEXT,
  product_id TEXT, qty INTEGER, branch_id TEXT, note TEXT DEFAULT '',
  status TEXT DEFAULT 'new', created_at INTEGER);
CREATE TABLE IF NOT EXISTS ladder(key TEXT PRIMARY KEY, level TEXT, outcomes TEXT);
CREATE TABLE IF NOT EXISTS leads(ts INTEGER, tenant TEXT, channel TEXT, sender TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS idem(tenant TEXT, channel TEXT, key TEXT, PRIMARY KEY(tenant, channel, key));
CREATE TABLE IF NOT EXISTS limits(tenant TEXT PRIMARY KEY, day TEXT, count INTEGER);
CREATE TABLE IF NOT EXISTS warned(tenant TEXT, day TEXT, PRIMARY KEY(tenant, day));
CREATE TABLE IF NOT EXISTS rtasks(id TEXT PRIMARY KEY, tenant TEXT, tool TEXT, params TEXT,
  status TEXT DEFAULT 'queued', enqueued_at INTEGER);
CREATE TABLE IF NOT EXISTS rresults(id TEXT PRIMARY KEY, payload TEXT);
CREATE TABLE IF NOT EXISTS rkilled(tenant TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS rseen(tenant TEXT PRIMARY KEY, ts INTEGER);
CREATE TABLE IF NOT EXISTS rmeta(key TEXT PRIMARY KEY, value INTEGER);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant TEXT NOT NULL,
    channel TEXT NOT NULL,
    external_key TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(tenant, channel, external_key));
CREATE TABLE IF NOT EXISTS deliveries(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    external_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT NOT NULL DEFAULT '',
    lease_until INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(tenant, idempotency_key));
CREATE INDEX IF NOT EXISTS ix_orders_tenant ON orders(tenant);
CREATE INDEX IF NOT EXISTS ix_appr_tenant ON approvals(tenant, status);
CREATE INDEX IF NOT EXISTS ix_appr_agent ON approvals(tenant, agent, status);
CREATE INDEX IF NOT EXISTS ix_messages_tenant ON messages(tenant, created_at);
CREATE INDEX IF NOT EXISTS ix_deliveries_tenant ON deliveries(tenant, status);
CREATE TABLE IF NOT EXISTS p_customers(
  id TEXT PRIMARY KEY, tenant TEXT NOT NULL, external_ref TEXT DEFAULT NULL,
  display_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(tenant, external_ref));
CREATE TABLE IF NOT EXISTS p_customer_contacts(
  id TEXT PRIMARY KEY, tenant TEXT NOT NULL, customer_id TEXT NOT NULL, type TEXT NOT NULL,
  value TEXT NOT NULL, normalized TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
  UNIQUE(tenant, customer_id, type, normalized));
CREATE TABLE IF NOT EXISTS p_channel_identities(
  id TEXT PRIMARY KEY, tenant TEXT NOT NULL, customer_id TEXT NOT NULL, channel TEXT NOT NULL,
  external_id TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
  UNIQUE(tenant, channel, external_id));
CREATE TABLE IF NOT EXISTS p_conversations(
  id TEXT PRIMARY KEY, tenant TEXT NOT NULL, customer_id TEXT NOT NULL, channel TEXT NOT NULL,
  external_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created REAL NOT NULL, updated REAL NOT NULL,
  UNIQUE(tenant, channel, external_id));
CREATE TABLE IF NOT EXISTS p_users(
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, auth_subject TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL, password_hash TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS p_workspaces(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, plan TEXT NOT NULL, status TEXT NOT NULL, region TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS p_memberships(
  workspace_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  created REAL NOT NULL, updated REAL NOT NULL, PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS p_invitations(
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
  expires REAL NOT NULL, accepted_at REAL NOT NULL DEFAULT 0, revoked_at REAL NOT NULL DEFAULT 0, created_by TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS p_sessions(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, workspace_id TEXT NOT NULL, refresh_hash TEXT NOT NULL UNIQUE,
  expires REAL NOT NULL, revoked_at REAL NOT NULL DEFAULT 0, rotated_from TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_memberships_user ON p_memberships(user_id,status);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON p_sessions(user_id,revoked_at);
CREATE TABLE IF NOT EXISTS p_customer_orders(
  id TEXT PRIMARY KEY, tenant TEXT NOT NULL, customer_id TEXT NOT NULL, external_id TEXT NOT NULL,
  status TEXT NOT NULL, currency TEXT NOT NULL, total_minor INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
  UNIQUE(tenant, external_id));
"""


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Eski SQLite fayllariga yangi nullable/default ustunlarni qo'shadi."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(deliveries)").fetchall()}
    if "claimed_by" not in columns:
        conn.execute("ALTER TABLE deliveries ADD COLUMN claimed_by TEXT NOT NULL DEFAULT ''")
    if "lease_until" not in columns:
        conn.execute("ALTER TABLE deliveries ADD COLUMN lease_until INTEGER NOT NULL DEFAULT 0")


def _path() -> str:
    return os.getenv("APP_DB", str(Path(__file__).resolve().parents[1] / "data" / "app.db"))


def db() -> sqlite3.Connection:
    """Har oqimga alohida ulanish (shared-connection race yo'q)."""
    p = _path()
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != p:
        if getattr(_local, "conn", None) is not None:
            try:
                _local.conn.close()
            except sqlite3.Error:
                pass
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p, timeout=CONNECT_TIMEOUT_SECONDS, isolation_level=None)
        conn.row_factory = sqlite3.Row
        with _lock:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            try:
                _ensure_columns(conn)
                from .identity_schema import migrate
                migrate(conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                conn.close()
                raise
        _local.conn, _local.path = conn, p
    return conn


@contextmanager
def tx():
    """Serialize read-check-write across processes; nested callers get savepoints."""
    with _lock:
        c = db()
        nested = c.in_transaction
        name = 'nested_' + __import__('uuid').uuid4().hex
        c.execute('SAVEPOINT ' + name if nested else 'BEGIN IMMEDIATE')
        try:
            yield c
            c.execute('RELEASE SAVEPOINT ' + name) if nested else c.commit()
        except BaseException:
            if nested:
                c.execute('ROLLBACK TO SAVEPOINT ' + name)
                c.execute('RELEASE SAVEPOINT ' + name)
            else:
                c.rollback()
            raise


def record_inbound(message: InboundMessage) -> bool:
    """Inbound eventni tenant/channel/external key bo'yicha bir marta yozadi."""
    with tx() as c:
        cursor = c.execute(
            "INSERT OR IGNORE INTO messages(tenant,channel,external_key,sender_id,"
            "conversation_id,text,correlation_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (message.tenant_id, message.channel.value, message.external_key,
             message.sender_id, message.conversation_id, message.text,
             message.correlation_id, int(time.time())),
        )
        return cursor.rowcount == 1


def create_delivery(tenant_id: str, entity_type: str, entity_id: str,
                    provider: str, idempotency_key: str) -> int:
    """Delivery attempt yaratadi; ayni tenant key mavjud bo'lsa o'sha ID qaytadi."""
    now = int(time.time())
    with tx() as c:
        existing = c.execute(
            "SELECT id FROM deliveries WHERE tenant=? AND idempotency_key=?",
            (tenant_id, idempotency_key),
        ).fetchone()
        if existing:
            return int(existing["id"])
        cursor = c.execute(
            "INSERT INTO deliveries(tenant,entity_type,entity_id,provider,"
            "idempotency_key,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (tenant_id, entity_type, entity_id, provider, idempotency_key,
             DeliveryStatus.PENDING.value, now, now),
        )
        return int(cursor.lastrowid)


def update_delivery(tenant_id: str, delivery_id: int, status: DeliveryStatus,
                    external_id: str = "", error: str = "") -> bool:
    """Delivery holatini va attempt counter'ni atomik yangilaydi."""
    with tx() as c:
        cursor = c.execute(
            "UPDATE deliveries SET status=?, external_id=?, error=?, "
            "attempt_count=attempt_count+1, claimed_by='', lease_until=0, "
            "updated_at=? WHERE tenant=? AND id=?",
            (status.value, external_id, error[:MAX_DELIVERY_ERROR_CHARS], int(time.time()), tenant_id, delivery_id),
        )
        return cursor.rowcount == 1


def claim_delivery(tenant_id: str, delivery_id: int, worker_id: str,
                   *, now: int | None = None, lease_seconds: int = DELIVERY_LEASE_SECONDS) -> bool:
    """Bitta worker uchun delivery'ni compare-and-set bilan claim qiladi."""
    if not worker_id.strip():
        raise ValueError("worker_id bo'sh bo'lmasin")
    current = int(time.time()) if now is None else now
    with tx() as c:
        cursor = c.execute(
            "UPDATE deliveries SET status='claimed', claimed_by=?, lease_until=?, "
            "updated_at=? WHERE tenant=? AND id=? AND status IN ('pending','failed','claimed') "
            "AND (lease_until=0 OR lease_until<=?)",
            (worker_id, current + max(1, lease_seconds), current,
             tenant_id, delivery_id, current),
        )
        return cursor.rowcount == 1


def get_delivery(tenant_id: str, delivery_id: int) -> dict | None:
    """Faqat berilgan tenant'ning delivery yozuvini qaytaradi."""
    row = db().execute(
        "SELECT * FROM deliveries WHERE tenant=? AND id=?",
        (tenant_id, delivery_id),
    ).fetchone()
    return dict(row) if row else None


def reset() -> None:
    """Faqat testlar uchun."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None
