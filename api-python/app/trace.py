"""Audit trace — har qadam JSONL (docs §4: tool chaqiruvlari 12 oy).

Stage 2: fayl (lock + rotatsiya). Stage 4+: Langfuse (xuddi shu chaqiruv).
Telefon/karta avtomatik maskalanadi.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

PHONE_RE = re.compile(r"\+998[\d\s\-()]{9,16}")
CARD_RE = re.compile(r"\b\d{16}\b")
MAX_BYTES = 5 * 1024 * 1024
_lock = threading.Lock()


def _path() -> Path:
    return Path(os.getenv("TRACE_PATH", Path(__file__).resolve().parents[1] / "traces" / "trace.jsonl"))


def _scrub(value):
    if isinstance(value, str):
        value = PHONE_RE.sub("+998***", value)
        return CARD_RE.sub("****", value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def log(event: dict) -> None:
    try:
        with _lock:
            p = _path()
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.is_file() and p.stat().st_size > MAX_BYTES:
                p.replace(p.with_suffix(".old.jsonl"))
            rec = {"ts": int(time.time()), **_scrub(event)}
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass  # audit yozilmasa ham biznes so'rov o'lmasligi kerak
