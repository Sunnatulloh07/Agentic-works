import pytest

from app.orchestrator import ExecutionPlan, PlanStep
from app.tool_registry import ToolKind, ToolRegistry, ToolSpec


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec(name="orders.api", kind=ToolKind.API, risk="write"))
    registry.register(ToolSpec(name="printer.screen", kind=ToolKind.SCREEN, risk="physical"))
    return registry


def test_plan_marks_physical_step_as_approval_required():
    plan = ExecutionPlan(
        tenant_id="demo-retail",
        agent_id="ops.print",
        steps=[PlanStep(tool_name="printer.screen", params={})],
    )

    validated = plan.validate(_registry())

    assert validated.steps[0].requires_approval is True


def test_plan_rejects_unknown_tool_before_execution():
    plan = ExecutionPlan(
        tenant_id="demo-retail",
        agent_id="ops.test",
        steps=[PlanStep(tool_name="missing.tool", params={})],
    )

    with pytest.raises(LookupError):
        plan.validate(_registry())