"""Buyurtma modeli — Sheets/CRM'ga nima yozilishi shu (docs §8.0)."""
import re
import time

from pydantic import BaseModel, Field

PHONE_RE = re.compile(r"^\+998\d{9}$")


class Order(BaseModel):
    id: str = ""
    tenant: str = Field(max_length=64)
    update_id: int
    customer: str = Field(default="mijoz", max_length=200)
    phone: str = Field(max_length=20)
    product_id: str = Field(max_length=64)
    qty: int = Field(ge=1, le=99)
    branch_id: str = Field(max_length=64)
    note: str = Field(default="", max_length=500)
    status: str = "new"
    created_at: int = Field(default_factory=lambda: int(time.time()))


def validate_order_payload(payload: dict, pack) -> None:
    """Decide paytida pack'ga qayta tekshirish (audit S14): fayl o'zgarsa ham."""
    pids = {p.id.upper() for p in pack.products}
    bids = {b.id.lower() for b in pack.branches}
    if str(payload.get("product_id", "")).upper() not in pids:
        raise ValueError("tovar topilmadi")
    if str(payload.get("branch_id", "")).lower() not in bids:
        raise ValueError("filial topilmadi")
    qty = payload.get("qty", 0)
    if not isinstance(qty, int) or not (1 <= qty <= 99):
        raise ValueError("son noto'g'ri")
    if not PHONE_RE.match(str(payload.get("phone", ""))):
        raise ValueError("telefon noto'g'ri")
