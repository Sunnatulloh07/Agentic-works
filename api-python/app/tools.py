"""Tool qatlami SEAM (docs §3.4/F3): CSV bugun, Sheets ertaga.

OrderStore protokoli o'zgarmaydi — faqat adapter almashadi (DIP).
"""
import csv
import os
import threading
import uuid
from pathlib import Path
from typing import Protocol

from .orders import Order
from .packs import Pack

_lock = threading.Lock()


def outbox_path() -> Path:
    return Path(os.getenv("OUTBOX_PATH", Path(__file__).resolve().parents[1] / "outbox" / "orders.csv"))


class OrderStore(Protocol):
    def append(self, order: Order) -> str: ...


def _cell(v) -> str:
    s = str(v).lstrip(" \t'\"`")  # formula bypass yopiladi (audit S16)
    if s[:1] in ("=", "+", "-", "@"):
        return "'" + s
    return s


class CsvOrderStore:
    """Stage 1 adapter. Stage 2: SheetsOrderStore (xuddi shu interfeys)."""

    FIELDS = ["id", "tenant", "update_id", "customer", "phone", "product_id",
              "qty", "branch_id", "note", "status", "created_at"]

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or outbox_path()

    def _ensure_header(self) -> None:
        """Eski schema bo'lsa — .bak ga ko'chirib yangidan (aralash fayl bo'lmaydi)."""
        import time as _t

        if self.path.is_file() and self.path.stat().st_size > 0:
            with self.path.open(encoding="utf-8") as f:
                first = f.readline().strip()
            if first != ",".join(self.FIELDS):
                bak = self.path.with_suffix(f".{int(_t.time())}.bak")
                self.path.replace(bak)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.is_file():
                with self.path.open("w", newline="", encoding="utf-8") as f:
                    import csv as _csv

                    _csv.DictWriter(f, fieldnames=self.FIELDS).writeheader()

    def append(self, order: Order) -> str:
        """SQLite asosiy (restart-safe) + CSV mirror (Sheets/operator).

        Bir xil id ikki marta yozilmaydi (crash-qayta urinish idempotentligi).
        """
        from . import storage

        with _lock:
            order.id = order.id or f"{order.tenant}-{uuid.uuid4().hex[:12]}"
            c = storage.db()
            exists = c.execute("SELECT 1 FROM orders WHERE id=?", (order.id,)).fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO orders(id,tenant,update_id,customer,phone,product_id,"
                    "qty,branch_id,note,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (order.id, order.tenant, order.update_id, order.customer, order.phone,
                     order.product_id, order.qty, order.branch_id, order.note,
                     order.status, order.created_at),
                )
                c.commit()
            self._ensure_header()
            with self.path.open(encoding="utf-8") as f:
                mirror_has = any(row.get("id") == order.id for row in csv.DictReader(f))
            if mirror_has:
                return order.id
            with self.path.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self.FIELDS)
                w.writerow(
                    {
                        "id": order.id,
                        "tenant": order.tenant,
                        "update_id": order.update_id,
                        "customer": _cell(order.customer),
                        "phone": _cell(order.phone),
                        "product_id": order.product_id,
                        "qty": order.qty,
                        "branch_id": order.branch_id,
                        "note": _cell(order.note),
                        "status": order.status,
                        "created_at": order.created_at,
                    }
                )
                f.flush()
            return order.id


def products_search(pack: Pack, query: str, limit: int = 5) -> list:
    """Top-N (docs §11.5): butun katalog prompt'ga kirmaydi."""
    import re as _re

    from .lang import fold_ids, normalize

    q = normalize(query or "")
    if not q.strip():
        return []
    tokens = _re.findall(r"[a-z0-9']+", q)  # tinish belgisiz (responder bilan bir xil)
    scored = []
    for p in pack.products:
        hay = normalize(f"{p.id} {p.name}")
        fid = fold_ids(p.id)
        words = fold_ids(hay).split()
        score = 0
        for tok in tokens:
            ftok = fold_ids(tok)
            if ftok == fid or ftok == p.id.lower():
                score += 5  # aniq ID — eng ustun
            elif any(w == ftok or (len(ftok) > 2 and w.startswith(ftok)) for w in words):
                score += 2  # butun so'z / turlangan shakl (kurtkasi)
            elif len(tok) > 2 and tok in hay:
                score += 1  # qisman
        if score:
            scored.append((score, p))
    scored.sort(key=lambda t: -t[0])
    return [p for _, p in scored[:limit]]
