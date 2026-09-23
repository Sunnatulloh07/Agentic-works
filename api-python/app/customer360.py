"""Tenant-scope Customer 360 ma'lumot qatlami.

Bu modul kanallarni avtomatik birlashtirmaydi. Har bir channel identity explicit
operator tasdig'i bilan bog'lanadi. Barcha querylar tenant chegarasini SQL
WHERE sharti bilan tekshiradi, API caller esa alohida RBAC qatlamidan o'tadi.

Declared bounds (§157).  Bu modul `app/` qatlamining eng ko'p chegarali fayli edi
va ularning deyarli hammasi yalang'och literal edi: `_text` ning default'lari,
`_tenant`/`_id`/`_normalize_contact` ning shiftlari, sahifalash oynasi, va
`get_customer` da **to'rt marta** yozilgan `LIMIT 100`.

Eng muhim topilma — `_ID_RE` ning kvantifikatori va `_id` ning `maximum` i
**bir xil sonni ikki joyda** yozadi, va ular mos kelishi shart: qaysi biri tor
bo'lsa, **jimgina o'sha yutadi**.  `{1,128}` ni `{1,12}` qilish hech qanday
xatolik ko'tarmaydi — identifikatorlar shunchaki qisqaradi.  Endi regex
konstantadan quriladi, ya'ni kelishib oladigan ikki literal yo'q.

Ikkinchi topilma — **dominat qilingan shiftlar**.  `kind`, `channel`, `status`
va `currency` uzunlikka tekshiriladi, keyin darhol kichik lug'at yoki regex
bo'yicha tekshiriladi.  Ya'ni bu shiftlar qabul qilinadigan **to'plamni**
belgilamaydi; ular faqat lug'atga qadar ish hajmini chegaralaydi.  Shuning uchun
ularni **xato sababi** bilan qadash kerak: 33 belgili `kind` uzunlik sababidan,
10 belgilisi lug'at sababidan rad etiladi — ikki xil sabab, ikki xil yo'l.
"""
from __future__ import annotations

import re
import sqlite3
import time
import uuid
from typing import Any

from .storage import db, tx

# Matn maydonlarining umumiy chegaralari.
MIN_TEXT_CHARS = 1
MAX_TEXT_CHARS = 256

# Identifikatorlar.  ``MAX_ID_CHARS`` — matn shifti HAM, regex kvantifikatori HAM;
# ilgari bu ikki literal edi (``maximum=128`` va ``{1,128}``) va ularni mos
# qiladigan hech narsa yo'q edi.
MAX_ID_CHARS = 128
MAX_TENANT_CHARS = 64

# Kontakt qiymati: e-mail manzil yoki telefon raqami matni.
MAX_CONTACT_VALUE_CHARS = 512
# Telefon raqamidagi eng kam raqam soni.  ``+998`` dan keyin qisqartirilgan yoki
# chala terilgan raqam shu yerdan o'tmaydi.
MIN_PHONE_DIGITS = 7

MAX_EXTERNAL_REF_CHARS = 256

# Sahifalash oynasi.
MIN_PAGE_LIMIT = 1
MAX_PAGE_LIMIT = 100
MAX_PAGE_OFFSET = 100_000
MAX_QUERY_CHARS = 256

# ``get_customer`` ichki to'plamlarni shu sonda qaytaradi.  Ilgari bu son to'rt
# xil SQL satrda **to'rt marta** yozilgan edi: bittasini o'zgartirish qolgan
# uchtasini jimgina ortda qoldirardi.
MAX_EMBEDDED_ROWS = 100

# Lug'atga qadar ishlaydigan uzunlik shiftlari.  Uchtasi bugun bir xil qiymatga
# ega, lekin ma'nolari boshqa — shuning uchun alohida nomlanadi.
MAX_KIND_CHARS = 32
MAX_CHANNEL_CHARS = 32
MAX_STATUS_CHARS = 32
MAX_CURRENCY_CHARS = 8

# Buyurtma jamlanmasi (minor birlikda).
MAX_ORDER_TOTAL_MINOR = 10 ** 15

# Lug'atlar.  Uchtasi allaqachon nomlangan edi; buyurtma holatlari esa
# `add_order` ichida **inline** yozilgan yagona to'plam edi.
_ALLOWED_STATUS = {"active", "inactive", "blocked", "deleted"}
_ALLOWED_CONTACT_TYPES = {"email", "phone", "address", "other"}
_ALLOWED_CHANNELS = {"telegram", "instagram", "whatsapp", "web", "email", "phone", "other"}
_ORDER_STATUSES = {"new", "pending", "paid", "cancelled", "refunded", "fulfilled"}

# ``_writable`` qabul qiladigan rollar.  ``identity_store.ROLES`` to'rtta rolni
# e'lon qiladi (``owner``, ``operator``, ``integrator``, ``viewer``), lekin yozish
# huquqi faqat ikkitasida.  Bu **subset** munosabati hech qayerda yozilmagan edi:
# rol lug'ati o'zgarsa yoki yangi rol qo'shilsa, bu yerdagi inline literal jimgina
# eskirardi va hech bir test buni ko'rmasdi.  ``frozenset`` — siyosat, mutatsiya
# qilinadigan to'plam emas.
WRITE_ROLES = frozenset({"owner", "operator"})

_CURRENCY_RE = re.compile(r"[A-Z]{3}")
# Kvantifikator konstantadan quriladi — kelishib olishi kerak bo'lgan ikkinchi
# literal yo'q.
_ID_RE = re.compile(rf"^[A-Za-z0-9_-]{{1,{MAX_ID_CHARS}}}$")


class CustomerError(ValueError):
    pass


class CustomerNotFound(LookupError):
    pass


def _text(value: Any, name: str, *, minimum: int = MIN_TEXT_CHARS,
          maximum: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        raise CustomerError(f"{name} string bo'lishi kerak")
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise CustomerError(f"{name} uzunligi noto'g'ri")
    return value


def _tenant(tenant: str) -> str:
    return _text(tenant, "tenant", maximum=MAX_TENANT_CHARS)


def _id(value: str, name: str = "id") -> str:
    value = _text(value, name, maximum=MAX_ID_CHARS)
    if not _ID_RE.fullmatch(value):
        raise CustomerError(f"{name} noto'g'ri")
    return value


def _normalize_contact(kind: str, value: str) -> str:
    value = _text(value, "value", maximum=MAX_CONTACT_VALUE_CHARS)
    if kind == "email":
        return value.casefold()
    if kind == "phone":
        normalized = re.sub(r"[^0-9+]", "", value)
        if len(normalized) < MIN_PHONE_DIGITS:
            raise CustomerError("phone noto'g'ri")
        return normalized
    return " ".join(value.split()).casefold()


def _ensure_customer(c: sqlite3.Connection, tenant: str, customer_id: str) -> None:
    row = c.execute(
        "SELECT 1 FROM p_customers WHERE tenant=? AND id=? AND status!='deleted'",
        (tenant, customer_id),
    ).fetchone()
    if not row:
        raise CustomerNotFound("Customer not found")


def create_customer(tenant: str, display_name: str, *, external_ref: str = "", status: str = "active", actor: str = "") -> dict:
    tenant = _tenant(tenant)
    display_name = _text(display_name, "display_name", maximum=MAX_TEXT_CHARS)
    if not isinstance(external_ref, str): raise CustomerError("external_ref string required")
    external_ref = external_ref.strip()
    if len(external_ref) > MAX_EXTERNAL_REF_CHARS:
        raise CustomerError("external_ref uzun")
    if status not in _ALLOWED_STATUS - {"deleted"}:
        raise CustomerError("status noto'g'ri")
    customer_id = uuid.uuid4().hex
    now = time.time()
    with tx() as c:
        _writable(c, tenant, actor)
        try:
            c.execute(
                "INSERT INTO p_customers(id,tenant,external_ref,display_name,status,created,updated) VALUES(?,?,?,?,?,?,?)",
                (customer_id, tenant, external_ref or None, display_name, status, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise CustomerError("external_ref allaqachon mavjud") from exc
        _audit(c, tenant, actor, customer_id)
    return get_customer(tenant, customer_id)


def list_customers(tenant: str, *, query: str = "", limit: int = MAX_PAGE_LIMIT, offset: int = 0) -> list[dict]:
    tenant = _tenant(tenant)
    if not isinstance(limit, int) or isinstance(limit, bool) or not MIN_PAGE_LIMIT <= limit <= MAX_PAGE_LIMIT:
        raise CustomerError(f"limit {MIN_PAGE_LIMIT}..{MAX_PAGE_LIMIT} bo'lishi kerak")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= MAX_PAGE_OFFSET:
        raise CustomerError("offset noto'g'ri")
    query = query.strip() if isinstance(query, str) else ""
    if len(query) > MAX_QUERY_CHARS:
        raise CustomerError("query uzun")
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    rows = db().execute(
        "SELECT id,external_ref,display_name,status,created,updated FROM p_customers "
        "WHERE tenant=? AND status!='deleted' AND (display_name LIKE ? ESCAPE '\\' OR external_ref LIKE ? ESCAPE '\\') "
        "ORDER BY updated DESC LIMIT ? OFFSET ?",
        (tenant, pattern, pattern, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def get_customer(tenant: str, customer_id: str) -> dict:
    tenant, customer_id = _tenant(tenant), _id(customer_id, "customer_id")
    c = db()
    row = c.execute(
        "SELECT id,external_ref,display_name,status,created,updated FROM p_customers WHERE tenant=? AND id=? AND status!='deleted'",
        (tenant, customer_id),
    ).fetchone()
    if not row:
        raise CustomerNotFound("Customer not found")
    out = dict(row)
    out["contacts"] = [dict(r) for r in c.execute(
        f"SELECT id,type,value,verified,created FROM p_customer_contacts WHERE tenant=? AND customer_id=? ORDER BY created LIMIT {MAX_EMBEDDED_ROWS}",
        (tenant, customer_id),
    )]
    out["channel_identities"] = [dict(r) for r in c.execute(
        f"SELECT id,channel,external_id,verified,created FROM p_channel_identities WHERE tenant=? AND customer_id=? ORDER BY created LIMIT {MAX_EMBEDDED_ROWS}",
        (tenant, customer_id),
    )]
    out["conversations"] = [dict(r) for r in c.execute(
        f"SELECT id,channel,external_id,status,created,updated FROM p_conversations WHERE tenant=? AND customer_id=? ORDER BY updated DESC LIMIT {MAX_EMBEDDED_ROWS}",
        (tenant, customer_id),
    )]
    out["orders"] = [dict(r) for r in c.execute(
        f"SELECT id,external_id,status,currency,total_minor,created,updated FROM p_customer_orders WHERE tenant=? AND customer_id=? ORDER BY updated DESC LIMIT {MAX_EMBEDDED_ROWS}",
        (tenant, customer_id),
    )]
    return out


def add_contact(tenant: str, customer_id: str, kind: str, value: str, *, verified: bool = False, actor: str = "") -> dict:
    tenant, customer_id = _tenant(tenant), _id(customer_id, "customer_id")
    kind = _text(kind, "type", maximum=MAX_KIND_CHARS).casefold()
    if kind not in _ALLOWED_CONTACT_TYPES:
        raise CustomerError("contact type noto'g'ri")
    if not isinstance(verified, bool):
        raise CustomerError("verified boolean bo'lishi kerak")
    normalized = _normalize_contact(kind, value)
    contact_id = uuid.uuid4().hex
    with tx() as c:
        _writable(c, tenant, actor)
        _ensure_customer(c, tenant, customer_id)
        try:
            c.execute(
                "INSERT INTO p_customer_contacts(id,tenant,customer_id,type,value,normalized,verified,created) VALUES(?,?,?,?,?,?,?,?)",
                (contact_id, tenant, customer_id, kind, _text(value, "value", maximum=MAX_CONTACT_VALUE_CHARS), normalized, int(verified), time.time()),
            )
        except sqlite3.IntegrityError as exc:
            raise CustomerError("contact allaqachon mavjud") from exc
        _audit(c, tenant, actor, customer_id)
    return get_customer(tenant, customer_id)


def link_channel_identity(tenant: str, customer_id: str, channel: str, external_id: str, *, verified: bool, actor: str = "") -> dict:
    tenant, customer_id = _tenant(tenant), _id(customer_id, "customer_id")
    channel = _text(channel, "channel", maximum=MAX_CHANNEL_CHARS).casefold()
    external_id = _text(external_id, "external_id", maximum=MAX_EXTERNAL_REF_CHARS)
    if channel not in _ALLOWED_CHANNELS:
        raise CustomerError("channel noto'g'ri")
    if verified is not True:
        raise CustomerError("channel identity faqat explicit verified=true bilan ulanadi")
    identity_id = uuid.uuid4().hex
    with tx() as c:
        _writable(c, tenant, actor)
        _ensure_customer(c, tenant, customer_id)
        existing = c.execute(
            "SELECT customer_id FROM p_channel_identities WHERE tenant=? AND channel=? AND external_id=?",
            (tenant, channel, external_id),
        ).fetchone()
        if existing and existing["customer_id"] != customer_id:
            raise CustomerError("channel identity boshqa customerga biriktirilgan")
        try:
            c.execute(
                "INSERT INTO p_channel_identities(id,tenant,customer_id,channel,external_id,verified,created) VALUES(?,?,?,?,?,?,?)",
                (identity_id, tenant, customer_id, channel, external_id, 1, time.time()),
            )
        except sqlite3.IntegrityError:
            pass
        _audit(c, tenant, actor, customer_id)
    return get_customer(tenant, customer_id)


def add_order(tenant: str, customer_id: str, external_id: str, *, status: str = "new", currency: str = "UZS", total_minor: int = 0, actor: str = "") -> dict:
    tenant, customer_id = _tenant(tenant), _id(customer_id, "customer_id")
    external_id = _text(external_id, "external_id", maximum=MAX_EXTERNAL_REF_CHARS)
    status = _text(status, "status", maximum=MAX_STATUS_CHARS).casefold()
    currency = _text(currency, "currency", maximum=MAX_CURRENCY_CHARS).upper()
    if not isinstance(total_minor, int) or isinstance(total_minor, bool) or total_minor < 0 or total_minor > MAX_ORDER_TOTAL_MINOR:
        raise CustomerError("total_minor noto'g'ri")
    if status not in _ORDER_STATUSES or not _CURRENCY_RE.fullmatch(currency):
        raise CustomerError("Invalid order status or currency")
    now = time.time()
    order_id = uuid.uuid4().hex
    with tx() as c:
        _writable(c, tenant, actor)
        _ensure_customer(c, tenant, customer_id)
        existing = c.execute('SELECT customer_id FROM p_customer_orders WHERE tenant=? AND external_id=?', (tenant,external_id)).fetchone()
        if existing and existing['customer_id'] != customer_id:
            raise CustomerError('Order belongs to another customer')
        c.execute(
            "INSERT INTO p_customer_orders(id,tenant,customer_id,external_id,status,currency,total_minor,created,updated) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant,external_id) DO UPDATE SET customer_id=excluded.customer_id,status=excluded.status,currency=excluded.currency,total_minor=excluded.total_minor,updated=excluded.updated",
            (order_id, tenant, customer_id, external_id, status, currency, total_minor, now, now),
        )
        _audit(c, tenant, actor, customer_id)
    return get_customer(tenant, customer_id)


def _writable(c, tenant, actor):
    from .identity_store import AuthenticationError, _membership
    from .authorization import directory_enabled
    row=c.execute('SELECT stopped FROM p_freeze WHERE tenant=?',(tenant,)).fetchone()
    if row and row['stopped']: raise AuthenticationError('Workspace frozen')
    if directory_enabled():
        # An omitted actor is not a privileged internal service identity.
        if not isinstance(actor,str) or not actor:raise AuthenticationError('Write actor required')
        m=_membership(c,actor,tenant)
        if not m or m['role'] not in WRITE_ROLES: raise AuthenticationError('Write permission revoked')


def _audit(c, tenant, actor, customer_id):
    from .identity_store import audit
    audit(c,tenant,actor or 'insecure-dev','customer.mutated',customer_id)
