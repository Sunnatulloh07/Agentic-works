"""Pack loader — o'chmaydigan core (docs §8, tamoyil 1).

Yangi mijoz = yangi YAML. Validatsiya chegarada (pydantic),
biznes-mantiq pack mazmuniga tegmaydi (OCP).
"""
import os
import re
from pathlib import Path
from collections.abc import Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

PACKS_DIR = Path(os.getenv("PACKS_DIR", Path(__file__).resolve().parents[2] / "packs"))
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Autonomy levels the engine understands. A typo here used to brick an agent
# only at submit time, as an opaque Forbidden.
LADDERS = ("human_led", "human_assisted", "autonomous")
# A persona is a system prompt; it is bounded so one pack cannot dominate the
# model context or the request body.
MAX_PERSONA_CHARS = 8000
# Filled by the loader from the persona file. An author supplying it directly
# would be injecting prompt text while bypassing the path and size checks.
LOADER_OWNED_AGENT_KEYS = ("prompt",)


class PackError(Exception):
    """Pack topilmadi yoki yaroqsiz — erta va baland ovozda."""


def read_persona(pack_dir: Path, relative: str, agent_id: str, pack: str) -> str:
    """Resolve a pack-relative persona file.

    The path comes from tenant-authored YAML, so it is confined to the pack
    directory: a persona must never be able to read a neighbouring tenant's
    prompts, an .env file or anything else on the host.
    """
    if not relative:
        return ""
    target = (pack_dir / relative).resolve()
    if pack_dir.resolve() not in target.parents:
        raise PackError(
            f"agent {agent_id!r} persona pack chegarasidan tashqarida ({pack}): {relative}")
    if not target.is_file():
        raise PackError(f"agent {agent_id!r} persona fayli topilmadi ({pack}): {relative}")
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PackError(f"agent {agent_id!r} persona o'qilmadi ({pack}): {relative} ({exc})")
    if len(text) > MAX_PERSONA_CHARS:
        raise PackError(
            f"agent {agent_id!r} persona juda katta ({pack}): {len(text)} > {MAX_PERSONA_CHARS}")
    return text


class Strict(BaseModel):
    """Noma'lum kalit — xato yozuv, kelajak imkoniyati emas.

    `agants:` yoki `toolz:` jimgina tashlansa, tenant sababsiz o'lik qoladi.
    Yangi maydon kerak bo'lsa u shu yerga qo'shiladi, YAML'da o'ylab topilmaydi.
    """

    model_config = ConfigDict(extra="forbid")


class ApprovalPolicy(Strict):
    required_for: list[str] = Field(default_factory=list)
    approver_role: str = "operator"
    independent: bool = False


class MemoryPolicy(Strict):
    scope: str = "tenant"
    collections: list[str] = Field(default_factory=list)


class Trigger(Strict):
    type: str
    source: str
    filter: str = ""


class LanguagePolicy(Strict):
    input: str = "any"
    output: str = "uz-latn"
    tone: str = "standard"


class AgentRef(Strict):
    id: str
    name: str
    department: str = "general"
    persona: str = ""           # pack-relative path to the prompt file
    prompt: str = ""            # resolved persona text; written by the loader only
    tools: list[str] = Field(default_factory=list)
    tool_policy: str = "prefer_api"
    allowed_recipients: list[str] = Field(default_factory=list)
    allowed_connections: list[str] = Field(default_factory=list)
    ladder: str = "human_led"
    approval: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    memory: MemoryPolicy = Field(default_factory=MemoryPolicy)
    triggers: list[Trigger] = Field(default_factory=list)
    language: LanguagePolicy = Field(default_factory=LanguagePolicy)


class Branch(Strict):
    id: str
    name: str
    address: str = ""
    phone: str = ""
    hours: str = ""


class Product(Strict):
    id: str
    name: str
    price_uzs: int
    sizes: list = Field(default_factory=list)


class Pack(Strict):
    name: str
    language: str = "uz"
    shop_name: str = ""
    layout: str = "radial_map"
    theme: dict = Field(default_factory=dict)
    faq: dict = Field(default_factory=dict)
    agents: list[AgentRef] = Field(default_factory=list)
    branches: list[Branch] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)


def load_pack(name: str) -> Pack:
    if not NAME_RE.match(name or ""):
        raise PackError(f"pack nomi noto'g'ri: {name!r}")
    dirname = "_template" if name == "template" else name
    pack_file = (PACKS_DIR / dirname / "pack.yaml").resolve()
    if PACKS_DIR.resolve() not in pack_file.parents:
        raise PackError(f"pack chegaradan tashqarida: {name!r}")
    if not pack_file.is_file():
        raise PackError(f"pack topilmadi: {name}")
    try:
        data = yaml.safe_load(pack_file.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as e:
        raise PackError(f"pack.yaml xato ({name}): {e}")
    if not isinstance(data, Mapping):
        raise PackError(f"pack.yaml mapping bo'lishi kerak ({name})")

    products: list = []
    products_file = pack_file.parent / "products.yaml"
    if products_file.is_file():
        try:
            pdata = yaml.safe_load(products_file.read_text(encoding="utf-8")) or {}
            if not isinstance(pdata, Mapping) or not isinstance(pdata.get("products", []), list):
                raise PackError(f"products.yaml products list bo'lishi kerak ({name})")
            products = pdata.get("products", [])
        except (OSError, UnicodeError, yaml.YAMLError) as e:
            raise PackError(f"products.yaml xato ({name}): {e}")

    # The whole mapping is validated, not a hand-picked subset: cherry-picking
    # known keys is what let a misspelled one disappear without a word.
    values = dict(data)
    values.setdefault("name", name)
    if products_file.is_file():
        values["products"] = products
    for entry in values.get("agents") or []:
        reserved = isinstance(entry, Mapping) and set(entry) & set(LOADER_OWNED_AGENT_KEYS)
        if reserved:
            raise PackError(
                f"pack {', '.join(sorted(reserved))} kalitini o'zi bera olmaydi ({name}); "
                "persona faylini `persona:` orqali ko'rsating")
    try:
        pack = Pack.model_validate(values)
    except ValidationError as e:
        raise PackError(f"pack validatsiyadan o'tmadi ({name}): {e}")
    for label, values in (
        ("agent id", pack.agents),
        ("branch id", pack.branches),
        ("product id", pack.products),
    ):
        ids = [item.id for item in values]
        if len(ids) != len(set(ids)):
            raise PackError(f"duplicate {label} ({name})")
    # Fail loudly at load, not silently at /catalog and then as an opaque 422 on
    # submit. Onboarding a vertical is YAML work, so YAML mistakes must be visible.
    from platform_runtime.tools import unknown_tools

    for agent in pack.agents:
        if agent.ladder not in LADDERS:
            raise PackError(
                f"agent {agent.id!r} ladder {agent.ladder!r} noto'g'ri ({name}); "
                f"ruxsat etilgan: {', '.join(LADDERS)}"
            )
        missing = unknown_tools(agent.tools)
        if missing:
            raise PackError(
                f"agent {agent.id!r} uchun runtime adapter yo'q ({name}): "
                + ", ".join(missing)
            )
        agent.prompt = read_persona(pack_file.parent, agent.persona, agent.id, name)
    return pack
