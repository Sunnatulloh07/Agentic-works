import pytest

from app.tool_registry import ToolKind, ToolRegistry, ToolSpec


def test_registry_selects_highest_priority_available_tool():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="orders.screen", kind=ToolKind.SCREEN, risk="physical"))
    registry.register(ToolSpec(name="orders.api", kind=ToolKind.API, risk="write"))
    registry.register(ToolSpec(name="orders.cli", kind=ToolKind.CLI, risk="write"))

    selected = registry.select("orders", available={"orders.screen", "orders.api", "orders.cli"})

    assert selected.name == "orders.api"


def test_registry_requires_approval_for_destructive_and_physical_tools():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="printer.print", kind=ToolKind.API, risk="physical"))

    assert registry.requires_approval("printer.print") is True


def test_registry_rejects_unknown_tool_and_missing_candidates():
    registry = ToolRegistry()

    with pytest.raises(KeyError):
        registry.requires_approval("missing")
    with pytest.raises(LookupError):
        registry.select("orders", available=set())


def test_disabled_tool_is_not_authorized_for_approval_or_selection():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="orders.api", kind=ToolKind.API, enabled=False))

    with pytest.raises(LookupError):
        registry.requires_approval("orders.api")
    with pytest.raises(LookupError):
        registry.select("orders", available={"orders.api"})