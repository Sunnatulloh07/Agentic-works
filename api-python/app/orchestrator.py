"""Agent rejasini execution'dan oldin tekshiradigan orchestration seam'i."""
from pydantic import BaseModel, Field

from .tool_registry import ToolRegistry


class PlanStep(BaseModel):
    tool_name: str = Field(min_length=1, max_length=128)
    params: dict = Field(default_factory=dict)
    requires_approval: bool = False


class ExecutionPlan(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    agent_id: str = Field(min_length=1, max_length=128)
    steps: list[PlanStep] = Field(default_factory=list)

    def validate(self, registry: ToolRegistry) -> "ExecutionPlan":
        """Tool mavjudligi va xavf policy'sini plan ichiga muhrlaydi."""
        validated_steps = [
            step.model_copy(update={
                "requires_approval": registry.requires_approval(step.tool_name),
            })
            for step in self.steps
        ]
        return self.model_copy(update={"steps": validated_steps})