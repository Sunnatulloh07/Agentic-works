"""packs/turkish-baby: the ready tenant for a real children's-clothing shop test.

CLAUDE.md: a pack change is not committed until its tests are green. These pin
what the owner relies on: it loads, Telegram reaches the sales agent, the agent
may only look things up and draft orders, a valid order goes to a human-approved
order taker, the notify recipient is allowlisted, and the sample shop answers.
"""
import shutil
from pathlib import Path

import pytest
import yaml

from app.packs import PACKS_DIR, PackError, load_pack
from app.planning import conversation_agent, route
from app.platform_api import agents, catalog, policy, shop_data
from platform_runtime.conversation import ungrounded_numbers
from platform_runtime.oversight import MAX_EVENTS, MAX_WINDOW_SECONDS, OVERSIGHT_TOOLS
from platform_runtime.shop_tools import order_draft, shop_info
from platform_runtime.tools import build_registry, unknown_tools

T = "turkish-baby"
SALES = "sales.assistant"
WA = "sales.wa_assistant"


@pytest.fixture(scope="module")
def pack():
    return load_pack(T)


def sales_policy():
    return policy(T, SALES)


def test_pack_loads_with_a_small_marked_sample_catalogue(pack):
    assert pack.name == T and pack.language == "uz" and pack.shop_name
    assert 3 <= len(pack.products) <= 20
    assert all("NAMUNA" in p.name for p in pack.products)
    text = (PACKS_DIR / T / "products.yaml").read_text(encoding="utf-8")
    assert text.startswith("#") and "import_catalog.py" in text.splitlines()[1]


def test_every_tool_has_an_adapter_and_every_ladder_is_known(pack):
    for agent in pack.agents:
        assert unknown_tools(agent.tools) == []
        assert agent.ladder in ("human_led", "human_assisted", "autonomous")


def test_telegram_messages_reach_the_sales_agent_only():
    turn = conversation_agent(agents, T, "telegram")
    assert turn["agent"] == SALES and turn["conversation"]["enabled"] is True
    assert conversation_agent(agents, T, "instagram") is None
    # Deterministic commands still route by capability, never by agent id.
    assert route(agents, T, "reports.summary") == "ops.assistant"


def test_sales_agent_is_autonomous_with_lookup_and_draft_tools_only():
    p = sales_policy()
    assert p["ladder"] == "autonomous"
    assert set(p["tools"]) == {"telegram.send", "products.search", "shop.info", "orders.draft"}
    assert "records.create" not in p["tools"]  # it cannot write an order itself


def test_whatsapp_messages_reach_their_own_agent():
    """A channel is a capability boundary: the router picks by trigger source."""
    turn = conversation_agent(agents, T, "whatsapp")
    assert turn["agent"] == WA and turn["conversation"]["enabled"] is True
    assert conversation_agent(agents, T, "instagram") is None


def test_the_whatsapp_agent_owns_the_window_tools_and_still_waits_for_a_human():
    p = policy(T, WA)
    assert p["ladder"] == "human_assisted"
    assert set(p["tools"]) == {"whatsapp.send", "whatsapp.window", "whatsapp.templates",
                               "products.search", "shop.info", "orders.draft"}
    assert "records.create" not in p["tools"]
    registry = build_registry()
    assert registry.get("whatsapp.send").risk == "write"
    assert registry.get("whatsapp.window").risk == "read"
    assert registry.get("whatsapp.templates").risk == "read"
    conversation = p["conversation"]
    assert (conversation["order_agent"], conversation["order_kind"]) == ("sales.order_taker", "order")


def test_the_whatsapp_persona_names_the_window_tools_and_stays_grounded(pack):
    prompt = next(a for a in pack.agents if a.id == WA).prompt
    for word in ("whatsapp.send", "whatsapp.window", "whatsapp.templates",
                 "products.search", "shop.info"):
        assert word in prompt
    # Pack-authored text is grounding for replies: it must not carry prices.
    assert ungrounded_numbers(prompt, "") == []


def test_orders_go_to_a_human_approved_order_taker():
    conversation = sales_policy()["conversation"]
    assert (conversation["order_agent"], conversation["order_kind"]) == ("sales.order_taker", "order")
    taker = policy(T, conversation["order_agent"])
    assert taker["ladder"] == "human_assisted" and "records.create" in taker["tools"]


def test_notify_recipient_is_empty_or_allowlisted():
    p = sales_policy()
    recipient = p["conversation"]["notify_recipient"]
    assert recipient == "" or recipient in p["allowed_recipients"]


def test_filling_notify_recipient_without_allowlisting_it_is_refused(tmp_path, monkeypatch):
    shutil.copytree(PACKS_DIR / T, tmp_path / T)
    path = tmp_path / T / "pack.yaml"
    text = path.read_text(encoding="utf-8")
    assert text.count('notify_recipient: ""') == 1
    path.write_text(text.replace('notify_recipient: ""', 'notify_recipient: "-1001234567890"'), encoding="utf-8")
    monkeypatch.setattr("app.packs.PACKS_DIR", tmp_path)
    with pytest.raises(PackError, match="allowed_recipients"):
        load_pack(T)
    path.write_text(text.replace('notify_recipient: ""', 'notify_recipient: "-1001234567890"')
                    .replace("allowed_recipients: []", 'allowed_recipients: ["-1001234567890"]'),
                    encoding="utf-8")
    assert load_pack(T).agents[0].conversation.notify_recipient == "-1001234567890"


def test_the_ops_assistant_holds_the_read_only_oversight_surface():
    """agent.* needs no connection, so a shipped pack can actually use it."""
    p = policy(T, "ops.assistant")
    assert set(OVERSIGHT_TOOLS) <= set(p["tools"])
    registry = build_registry()
    assert {registry.get(name).risk for name in OVERSIGHT_TOOLS} == {"read"}
    # The bounds a caller fills in the dashboard form are the module's own.
    activity = registry.get("agent.activity").schema["properties"]
    assert activity["limit"]["maximum"] == MAX_EVENTS
    assert activity["since_seconds"]["maximum"] == MAX_WINDOW_SECONDS


def test_the_pack_carries_no_write_path_into_oversight(pack):
    """An agent able to edit its own ladder would break the audit chain."""
    ops = next(a for a in pack.agents if a.id == "ops.assistant")
    assert set(OVERSIGHT_TOOLS) & set(ops.tools) == set(OVERSIGHT_TOOLS)
    assert ops.ladder == "human_assisted"


def test_persona_is_present_uzbek_and_grounded(pack):
    prompt = next(a for a in pack.agents if a.id == SALES).prompt
    for word in ("products.search", "shop.info", "orders.draft", "Chegirma", "BITTA", "o‘lcham"):
        assert word in prompt
    # Pack-authored text is grounding for replies: it must not carry prices.
    assert ungrounded_numbers(prompt, "") == []


def test_products_are_valid_and_stock_matches_sizes(pack):
    ids = [p.id for p in pack.products]
    assert len(ids) == len({i.lower() for i in ids})
    for product in pack.products:
        assert product.price_uzs > 0 and product.sizes and product.category
        assert set(product.stock) <= {str(s) for s in product.sizes}
    raw = yaml.safe_load((PACKS_DIR / T / "products.yaml").read_text(encoding="utf-8"))
    assert len(raw["products"]) == len(pack.products)


def test_shop_info_answers_delivery_payment_returns_and_branches():
    info = shop_info(shop_data(T))
    assert info["shop_name"]
    assert {"delivery", "payment", "returns"} <= set(info["faq"])
    assert info["branches"] and info["branches"][0]["id"]
    assert info["truncated"] is False


def test_orders_draft_validates_a_sample_order(pack):
    product = next(p for p in pack.products if any(q > 0 for q in p.stock.values()))
    size = next(s for s, q in product.stock.items() if q > 0)
    args = {"product_id": product.id, "size": size, "qty": 1, "customer_name": "Sinov Mijoz",
            "phone": "+998 90 123 45 67", "delivery": pack.branches[0].id}
    result = order_draft(shop_data(T), args)
    assert result["valid"] is True
    assert result["draft"]["total_uzs"] == product.price_uzs
    assert result["draft"]["delivery"]["type"] == "branch"
    bad = order_draft(shop_data(T), {**args, "phone": "123", "qty": 0})
    assert bad["valid"] is False and {p["code"] for p in bad["problems"]} == {"invalid_phone", "qty_out_of_range"}


def test_catalogue_search_answers_a_size_question():
    rows = catalog(T, "futbolka 92 razmer bormi?")
    assert rows and rows[0]["stock_tracked"] is True
    assert "92" in rows[0]["available_sizes"]


def test_fallback_text_needs_no_lookup():
    fallback = sales_policy()["conversation"]["fallback_text"]
    assert fallback and ungrounded_numbers(fallback, "") == []
    assert Path(PACKS_DIR / T / "prompts" / "sales" / "responder.md").is_file()
