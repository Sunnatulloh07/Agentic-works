"""Pack loader contract: core o'chmaydigan kod (docs §8, §11.1).

Qoida: yangi mijoz = yangi YAML. Loader xato pack'ni erta va baland ovozda rad
etadi, chunki onboarding YAML ishi, demak YAML xatosi ko'rinishi shart.
"""
import pytest

from app.packs import LADDERS, PACKS_DIR, PackError, load_pack


def write(tmp_path, monkeypatch, name, body):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "pack.yaml").write_text(body, encoding="utf-8")
    monkeypatch.setattr("app.packs.PACKS_DIR", tmp_path)
    return name


def agent_yaml(tools="[reports.summary]", ladder="human_led"):
    return (f"name: probe\nagents:\n  - id: ops.probe\n    name: Probe\n"
            f"    tools: {tools}\n    ladder: {ladder}\nbranches: []\n")


def test_template_pack_loads():
    pack = load_pack("template")
    assert pack.name == "template"
    assert pack.language == "uz"
    assert len(pack.agents) >= 1


def test_demo_retail_catalog_and_branches():
    pack = load_pack("demo-retail")
    assert pack.name == "demo-retail"
    assert len(pack.products) == 20
    assert len(pack.branches) == 2
    assert {a.id for a in pack.agents} >= {"sales.responder", "ops.assistant"}


def test_unknown_pack_raises():
    with pytest.raises(PackError):
        load_pack("no-such-pack")


def test_agent_pack_contract_has_runtime_policy_defaults():
    agent = load_pack("demo-retail").agents[0]

    assert agent.department == "general"
    assert agent.tool_policy == "prefer_api"
    assert agent.approval.required_for == []
    assert agent.memory.scope == "tenant"
    assert agent.language.output == "uz-latn"


def test_pack_preserves_declared_runtime_metadata(tmp_path, monkeypatch):
    persona = {"prompts/ops/test.md": "Namuna persona matni."}
    yaml_text = "\n".join([
        "name: configured", "language: uz", "layout: department_board",
        "agents:",
        "  - id: ops.test",
        "    name: Test agent",
        "    department: operations",
        "    persona: prompts/ops/test.md",
        "    tools: [reports.summary, records.create]",
        "    tool_policy: prefer_cli",
        "    approval:",
        "      required_for: [records.create]",
        "      approver_role: owner",
        "    memory:",
        "      scope: agent",
        "      collections: [products]",
        "    triggers:",
        "      - type: cron",
        "        source: daily",
        "    language:",
        "      input: any",
        "      output: uz-cyrillic",
        "      tone: rasmiy",
        "branches: []",
    ])
    folder = tmp_path / "configured"
    folder.mkdir()
    (folder / "pack.yaml").write_text(yaml_text, encoding="utf-8")
    for relative, body in persona.items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    monkeypatch.setattr("app.packs.PACKS_DIR", tmp_path)

    pack = load_pack("configured")
    agent = pack.agents[0]

    assert agent.prompt == "Namuna persona matni."
    assert pack.layout == "department_board"
    assert agent.department == "operations"
    assert agent.approval.approver_role == "owner"
    assert agent.memory.collections == ["products"]
    assert agent.triggers[0].source == "daily"
    assert agent.language.output == "uz-cyrillic"


def test_pack_rejects_non_mapping_root(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "broken", "- not-a-pack\n")

    with pytest.raises(PackError, match="mapping"):
        load_pack("broken")


def test_pack_rejects_duplicate_agent_ids(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "duplicate",
          "name: duplicate\nagents:\n  - id: same\n    name: A\n  - id: same\n    name: B\nbranches: []\n")

    with pytest.raises(PackError, match="agent id"):
        load_pack("duplicate")


# --- Load-time tool and ladder contract -------------------------------------

def test_pack_rejects_a_tool_with_no_runtime_adapter(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "probe", agent_yaml("[telegram]"))

    with pytest.raises(PackError, match="telegram"):
        load_pack("probe")


def test_rejection_names_the_agent_so_the_author_can_find_it(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "probe", agent_yaml("[kb, web_search]"))

    with pytest.raises(PackError) as caught:
        load_pack("probe")
    assert "ops.probe" in str(caught.value)
    assert "kb" in str(caught.value) and "web_search" in str(caught.value)


def test_pack_accepts_every_registry_tool_the_agent_declares(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "probe",
          agent_yaml("[reports.summary, telegram.send, knowledge.search, products.search]"))

    assert load_pack("probe").agents[0].tools[-1] == "products.search"


def test_pack_accepts_an_agent_that_declares_no_tools(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "probe", agent_yaml("[]"))

    assert load_pack("probe").agents[0].tools == []


def test_pack_rejects_an_unknown_autonomy_level(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "probe", agent_yaml(ladder="human-led"))

    with pytest.raises(PackError, match="ladder"):
        load_pack("probe")


@pytest.mark.parametrize("ladder", LADDERS)
def test_pack_accepts_each_supported_autonomy_level(tmp_path, monkeypatch, ladder):
    write(tmp_path, monkeypatch, "probe", agent_yaml(ladder=ladder))

    assert load_pack("probe").agents[0].ladder == ladder


# --- Unknown keys are typos until proven otherwise --------------------------

def test_pack_rejects_a_misspelled_top_level_key(tmp_path, monkeypatch):
    # `agants:` used to load a pack with zero agents and no complaint.
    write(tmp_path, monkeypatch, "probe", "name: probe\nagants: []\nbranches: []\n")

    with pytest.raises(PackError, match="agants"):
        load_pack("probe")


def test_pack_rejects_a_misspelled_agent_key(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "probe",
          "name: probe\nagents:\n  - id: ops.probe\n    name: P\n    toolz: [reports.summary]\nbranches: []\n")

    with pytest.raises(PackError, match="toolz"):
        load_pack("probe")


def test_pack_rejects_a_misspelled_nested_policy_key(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "probe",
          "name: probe\nagents:\n  - id: ops.probe\n    name: P\n    tools: []\n"
          "    approval:\n      requred_for: [records.create]\nbranches: []\n")

    with pytest.raises(PackError, match="requred_for"):
        load_pack("probe")


def test_pack_rejects_an_unsupported_catalog_field(tmp_path, monkeypatch):
    # A training centre writing duration_hours must be told, not silently ignored.
    folder = tmp_path / "probe"
    folder.mkdir()
    (folder / "pack.yaml").write_text(agent_yaml("[]"), encoding="utf-8")
    (folder / "products.yaml").write_text(
        "products:\n  - id: C1\n    name: Python kursi\n    price_uzs: 0\n    duration_hours: 40\n",
        encoding="utf-8")
    monkeypatch.setattr("app.packs.PACKS_DIR", tmp_path)

    with pytest.raises(PackError, match="duration_hours"):
        load_pack("probe")


def test_catalog_declared_inline_is_kept_when_there_is_no_products_file(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "probe",
          "name: probe\nagents: []\nbranches: []\n"
          "products:\n  - id: P1\n    name: Mahsulot\n    price_uzs: 1000\n")

    assert [p.id for p in load_pack("probe").products] == ["P1"]


def test_products_file_overrides_an_inline_catalog(tmp_path, monkeypatch):
    folder = tmp_path / "probe"
    folder.mkdir()
    (folder / "pack.yaml").write_text(
        "name: probe\nagents: []\nbranches: []\n"
        "products:\n  - id: INLINE\n    name: A\n    price_uzs: 1\n", encoding="utf-8")
    (folder / "products.yaml").write_text(
        "products:\n  - id: FILE\n    name: B\n    price_uzs: 2\n", encoding="utf-8")
    monkeypatch.setattr("app.packs.PACKS_DIR", tmp_path)

    assert [p.id for p in load_pack("probe").products] == ["FILE"]


def test_pack_name_still_defaults_to_the_directory(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "probe", "agents: []\nbranches: []\n")

    assert load_pack("probe").name == "probe"


def test_every_shipped_pack_loads(monkeypatch):
    """The guard that would have caught the dead `marketing` pack in review."""
    shipped = sorted(p.name for p in PACKS_DIR.iterdir() if (p / "pack.yaml").is_file())
    assert shipped, "no packs found"
    for folder in shipped:
        load_pack("template" if folder == "_template" else folder)
