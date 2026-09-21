"""Tool metadata, priority va xavf siyosatini markazlashtiruvchi modul."""
from dataclasses import dataclass
from enum import StrEnum


class ToolKind(StrEnum):
    API = "api"
    CLI = "cli"
    BROWSER = "browser"
    SCREEN = "screen"


_PRIORITY = {
    ToolKind.API: 0,
    ToolKind.CLI: 1,
    ToolKind.BROWSER: 2,
    ToolKind.SCREEN: 3,
}
_APPROVAL_RISKS = {"destructive", "physical"}
_VALID_RISKS = {"read", "write", "destructive", "physical"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: ToolKind
    risk: str = "read"
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool nomi bo'sh bo'lmasin")
        if self.risk not in _VALID_RISKS:
            raise ValueError(f"noma'lum tool risk: {self.risk}")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool allaqachon ro'yxatdan o'tgan: {spec.name}")
        self._tools[spec.name] = spec

    def requires_approval(self, name: str) -> bool:
        return self._get(name).risk in _APPROVAL_RISKS

    def select(self, family: str, available: set[str]) -> ToolSpec:
        candidates = [
            spec for name, spec in self._tools.items()
            if spec.enabled and name in available and name.split(".", 1)[0] == family
        ]
        if not candidates:
            raise LookupError(f"tool topilmadi: {family}")
        return min(candidates, key=lambda spec: _PRIORITY[spec.kind])

    def _get(self, name: str) -> ToolSpec:
        try:
            spec = self._tools[name]
        except KeyError:
            raise KeyError(f"tool topilmadi: {name}") from None
        if not spec.enabled:
            raise LookupError(f"tool o'chirilgan: {name}")
        return spec