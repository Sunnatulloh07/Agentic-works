"""Pack loader — o'chmaydigan core (docs §8, tamoyil 1).

Yangi mijoz = yangi YAML. Validatsiya chegarada (pydantic),
biznes-mantiq pack mazmuniga tegmaydi (OCP).
"""
import os
import re
from pathlib import Path
from collections.abc import Mapping
from typing import Annotated
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

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


# Conversation-turn bounds. A turn answers a customer, so its step and time budget
# is deliberately far below a dashboard run's; the upper bounds equal the agent
# loop's own (agent_loop.MAX_STEPS / MAX_SECONDS) so a pack cannot ask for more.
CONVERSATION_MAX_STEPS = 12
CONVERSATION_MIN_SECONDS = 60
CONVERSATION_MAX_SECONDS = 86400
CONVERSATION_MAX_HISTORY_TURNS = 20
CONVERSATION_MAX_FALLBACK_CHARS = 1000
CONVERSATION_MAX_ID_CHARS = 128
CONVERSATION_MAX_KIND_CHARS = 64
# Equal to platform_runtime/conversation.TAKEOVER_MINUTES.
CONVERSATION_MIN_TAKEOVER_MINUTES = 1
CONVERSATION_MAX_TAKEOVER_MINUTES = 1440


class ConversationPolicy(Strict):
    """Inbound message -> bounded result-fed turn -> reply to the same conversation.

    Off by default: a pack opts an agent in explicitly, and the agent is selected
    by its ``triggers`` ({type: message, source: <channel>}), never by its id.
    Strict types: ``enabled: "yes"`` or ``max_steps: "3"`` is a YAML mistake.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = False
    max_steps: int = Field(default=3, ge=1, le=CONVERSATION_MAX_STEPS)
    max_seconds: int = Field(default=120, ge=CONVERSATION_MIN_SECONDS, le=CONVERSATION_MAX_SECONDS)
    history_turns: int = Field(default=6, ge=0, le=CONVERSATION_MAX_HISTORY_TURNS)
    fallback_text: str = Field(default="", max_length=CONVERSATION_MAX_FALLBACK_CHARS)
    # A valid orders.draft in a turn becomes ONE records.create task for this
    # agent (its ladder decides approval). Empty: no order is captured.
    order_agent: str = Field(default="", max_length=CONVERSATION_MAX_ID_CHARS)
    order_kind: str = Field(default="order", min_length=1, max_length=CONVERSATION_MAX_KIND_CHARS)
    # Operator chat told about every handoff and captured order. It must also be
    # in this agent's allowed_recipients: the engine's allowlist rule still decides.
    notify_recipient: str = Field(default="", max_length=CONVERSATION_MAX_ID_CHARS)
    # After an operator replies in a chat, the bot answers nothing there for this
    # many minutes (a later reply restarts it; "Botga qaytarish" ends it early).
    takeover_minutes: int = Field(default=30, ge=CONVERSATION_MIN_TAKEOVER_MINUTES,
                                  le=CONVERSATION_MAX_TAKEOVER_MINUTES)


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
    conversation: ConversationPolicy = Field(default_factory=ConversationPolicy)


class Branch(Strict):
    id: str
    name: str
    address: str = ""
    phone: str = ""
    hours: str = ""


# Catalogue bounds. A product row is tenant data a model reads and a customer
# hears, so every free-text field has a ceiling and every number a floor.
MAX_PRODUCT_DESCRIPTION_CHARS = 1000
MAX_PRODUCT_LABEL_CHARS = 64
MAX_PRODUCT_COLORS = 20
MAX_PHOTO_URL_CHARS = 2000


class Product(Strict):
    """One catalogue row. Everything after ``sizes`` is optional.

    ``stock`` maps a size to the quantity on hand. Keys are compared as strings
    (YAML ``{92: 3}`` and ``{"92": 3}`` are the same size) and must be sizes the
    product declares, so a typo cannot invent a size the shop does not sell. When
    ``stock`` is declared a size missing from it counts as zero; when it is empty
    the shop does not track stock for the product.
    """

    id: str
    name: str
    price_uzs: int
    sizes: list = Field(default_factory=list)
    description: str = Field(default="", max_length=MAX_PRODUCT_DESCRIPTION_CHARS)
    category: str = Field(default="", max_length=MAX_PRODUCT_LABEL_CHARS)
    gender: str = Field(default="", max_length=MAX_PRODUCT_LABEL_CHARS)
    age: str = Field(default="", max_length=MAX_PRODUCT_LABEL_CHARS)
    colors: list[Annotated[str, Field(min_length=1, max_length=MAX_PRODUCT_LABEL_CHARS)]] = Field(
        default_factory=list, max_length=MAX_PRODUCT_COLORS)
    stock: dict[str, int] = Field(default_factory=dict)
    photo_url: str = Field(default="", max_length=MAX_PHOTO_URL_CHARS)

    @field_validator("stock", mode="before")
    @classmethod
    def _stock_keys_as_text(cls, value):
        if isinstance(value, Mapping):
            return {str(k) if isinstance(k, (int, str)) and not isinstance(k, bool) else k: v
                    for k, v in value.items()}
        return value

    @model_validator(mode="after")
    def _stock_and_photo(self):
        sizes = {str(size) for size in self.sizes}
        negative = sorted(k for k, qty in self.stock.items() if qty < 0)
        if negative:
            raise ValueError(f"stock manfiy bo'lishi mumkin emas: {', '.join(negative)}")
        unknown = sorted(set(self.stock) - sizes)
        if unknown:
            raise ValueError(f"stock sizes ro'yxatida yo'q o'lcham: {', '.join(unknown)}")
        if self.photo_url:
            parts = urlsplit(self.photo_url)
            if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                    or any(ch.isspace() for ch in self.photo_url)):
                raise ValueError("photo_url faqat https:// manzil bo'lishi kerak")
        return self


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
    for agent in pack.agents:
        check_conversation_routes(agent, pack.agents, name)
    return pack


# The tool an order agent writes with, and the tool a notification is sent with.
# Runtime tool names, not pack content: conversation.py submits exactly these.
ORDER_WRITE_TOOL = "records.create"
NOTIFY_TOOL = "telegram.send"


def check_conversation_routes(agent: AgentRef, agents: list, pack: str) -> None:
    """order_agent and notify_recipient must be usable, or the pack does not load.

    Otherwise an order would be refused at the moment a customer placed it, and a
    notification would wait silently for an approval nobody expects.
    """
    policy = agent.conversation
    if policy.order_agent:
        target = next((a for a in agents if a.id == policy.order_agent), None)
        if target is None:
            raise PackError(f"agent {agent.id!r} order_agent {policy.order_agent!r} pack'da yo'q ({pack})")
        if ORDER_WRITE_TOOL not in target.tools:
            raise PackError(f"agent {agent.id!r} order_agent {policy.order_agent!r} uchun "
                            f"tools'da {ORDER_WRITE_TOOL} bo'lishi shart ({pack})")
    if policy.notify_recipient:
        if NOTIFY_TOOL not in agent.tools:
            raise PackError(f"agent {agent.id!r} notify_recipient uchun tools'da {NOTIFY_TOOL} "
                            f"bo'lishi shart ({pack})")
        if policy.notify_recipient not in agent.allowed_recipients:
            raise PackError(f"agent {agent.id!r} notify_recipient {policy.notify_recipient!r} shu agentning "
                            f"allowed_recipients ro'yxatida ham bo'lishi shart ({pack})")
