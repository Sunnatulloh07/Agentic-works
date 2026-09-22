"""Ladder: human_led → human_assisted → autonomous (docs F5).

Ko'tarilish: oxirgi N vazifada xato ≤ 5% (default N=30).
Tushish: xato > 20% bo'lsa bir pog'ona pastga (review #1).
Kalit: "tenant:agent" — tenantlar aralashmaydi (review #3).
Oyna har pog'onada yangidan boshlanadi (qasddan — izoh).
"""
import json
import threading

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

LEVELS = ["human_led", "human_assisted", "autonomous"]

# Declared bounds (§156).  These three numbers ARE the policy: they decide when an
# agent stops needing a human, and nothing read them.  They were constructor
# defaults, so the promotion rule could be loosened without a single test changing
# colour -- ``LadderStore(min_tasks=1)`` promotes after one successful task, and the
# only signal would have been that agents became autonomous sooner.
DEFAULT_MIN_TASKS = 30
DEFAULT_MAX_ERR = 0.05
DEFAULT_DEMOTE_ERR = 0.20

# The stored outcome history is never shorter than this, whatever ``min_tasks`` is.
# It used to be a second bare ``30`` sitting one line under the default above: two
# literals that had to agree, and nothing that made them.  The floor matters because
# ``record`` trims outcomes to ``[-window:]`` while promotion needs
# ``total >= min_tasks`` -- so a window below ``min_tasks`` makes promotion
# UNREACHABLE, and a dead rule raises nothing.
MIN_WINDOW = 30

_lock = threading.Lock()


def key(tenant: str, agent_id: str) -> str:
    """Tenant izolyatsiyasi: kalit har doim tenant bilan (review #3)."""
    return f"{tenant}:{agent_id}"


class LadderStore:
    def __init__(self, min_tasks: int = DEFAULT_MIN_TASKS,
                 max_err: float = DEFAULT_MAX_ERR, demote_err: float = DEFAULT_DEMOTE_ERR,
                 auto_cap: bool = True) -> None:
        """auto_cap=True: avtomatik faqat human_assisted gacha;
        autonomous — faqat owner endpoint (audit S29: self-approve farming)."""
        self.min_tasks = min_tasks
        self.max_err = max_err
        self.demote_err = demote_err
        self.auto_cap = auto_cap
        self.window = max(MIN_WINDOW, min_tasks)

    def _read_all(self) -> dict:
        from . import storage

        rows = storage.db().execute("SELECT key, level, outcomes FROM ladder").fetchall()
        out = {}
        for r in rows:
            try:
                outcomes = json.loads(r["outcomes"] or "[]")
            except (json.JSONDecodeError, TypeError):
                outcomes = []
            out[r["key"]] = {"level": r["level"], "outcomes": outcomes}
        return out

    def _save(self, agent_id: str, level: str, outcomes: list) -> None:
        from . import storage

        c = storage.db()
        c.execute("INSERT OR REPLACE INTO ladder(key, level, outcomes) VALUES(?,?,?)",
                  (agent_id, level, json.dumps(outcomes)))
        c.commit()

    def _check_id(self, agent_id: str) -> None:
        if not agent_id or ":" in agent_id and agent_id.count(":") != 1:
            raise ValueError("agent_id noto'g'ri (key() dan foydalaning)")

    def level(self, agent_id: str) -> str:
        self._check_id(agent_id)
        return self._read_all().get(agent_id, {}).get("level", "human_led")

    def all_levels(self) -> dict:
        """Bitta o'qish — batch (N+1 yo'q)."""
        return {k: v.get("level", "human_led") for k, v in self._read_all().items()}

    def set_level(self, agent_id: str, level: str) -> str:
        """Owner qo'lda boshqaradi (docs F5)."""
        if level not in LEVELS:
            raise ValueError(f"level: {LEVELS}")
        self._check_id(agent_id)
        with _lock:
            self._save(agent_id, level, [])
        return level

    def record(self, agent_id: str, ok: bool) -> str:
        """30 vazifada xato ≤5% → yuqoriga; >20% → pastga (aniq butun sonlar)."""
        self._check_id(agent_id)
        with _lock:
            data = self._read_all()
            entry = data.get(agent_id, {"level": "human_led", "outcomes": []})
            entry["outcomes"] = (entry.get("outcomes", []) + [bool(ok)])[-self.window:]
            total = len(entry["outcomes"])
            if total >= self.min_tasks:
                errs = sum(1 for o in entry["outcomes"] if not o)
                idx = LEVELS.index(entry.get("level", "human_led"))
                if errs / total <= self.max_err and idx < len(LEVELS) - 1:
                    nxt = LEVELS[idx + 1]
                    if not (self.auto_cap and nxt == "autonomous"):
                        entry["level"] = nxt
                        entry["outcomes"] = []
                elif errs / total > self.demote_err and idx > 0:
                    entry["level"] = LEVELS[idx - 1]
                    entry["outcomes"] = []
            self._save(agent_id, entry.get("level", "human_led"), entry["outcomes"])
            return entry["level"]


# Owner endpointlar (docs F5: darajani owner boshqaradi)
ladder_router = APIRouter()
_ladder_singleton: "LadderStore | None" = None


def _ladder_one() -> "LadderStore":
    global _ladder_singleton
    if _ladder_singleton is None:
        _ladder_singleton = LadderStore()
    return _ladder_singleton


class _LevelSet(BaseModel):
    level: str


@ladder_router.get("/ladder/{tenant}/{agent_id}")
def ladder_get(
    tenant: str,
    agent_id: str,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    from .security import scope_ok

    if not scope_ok(tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return {"level": _ladder_one().level(key(tenant, agent_id))}


@ladder_router.post("/ladder/{tenant}/{agent_id}")
def ladder_set(
    tenant: str,
    agent_id: str,
    req: _LevelSet,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    from .security import scope_ok

    if not scope_ok(tenant, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    try:
        level = _ladder_one().set_level(key(tenant, agent_id), req.level)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "level": level}
