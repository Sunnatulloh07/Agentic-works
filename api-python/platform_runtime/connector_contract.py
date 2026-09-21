"""Versioned connector contract and fail-closed configuration validation.

Provider adapters are deliberately separate from the runtime. A config entry
never makes an unsupported provider appear healthy. Every adapter must declare
capabilities and implement the contract methods before it can be enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

CONTRACT_VERSION = "1.0"
LIFECYCLES = {"draft", "authorizing", "configured", "verifying", "healthy", "degraded", "revoked"}
CAPABILITIES = {"discover", "validate", "read", "plan_write", "execute_write", "reconcile", "revoke"}
SUPPORTED_LOCAL_DRIVERS = {"sqlite_readonly", "postgres_readonly"}
DECLARATIVE_DRIVERS = {"mcp", "http_oauth", "bitrix24", "amocrm", "one_c", "custom_http"}


@dataclass(frozen=True)
class ConnectorDescriptor:
    connection_id: str
    driver: str
    contract_version: str
    lifecycle: str
    capabilities: tuple[str, ...]
    status: str
    scopes: tuple[str, ...]
    agent_ids: tuple[str, ...]


class ConnectorAdapter(Protocol):
    def discover(self, config: dict) -> dict: ...
    def validate(self, config: dict) -> dict: ...
    def read(self, tenant: str, config: dict, request: dict) -> dict: ...
    def plan_write(self, tenant: str, config: dict, request: dict) -> dict: ...
    def execute_write(self, tenant: str, config: dict, request: dict, receipt: str) -> dict: ...
    def reconcile(self, tenant: str, config: dict, receipt: str) -> dict: ...
    def revoke(self, config: dict) -> dict: ...


def _string(value: Any, name: str, maximum: int = 128) -> str:
    if (not isinstance(value, str) or value != value.strip()
            or not 1 <= len(value) <= maximum or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f"{name} invalid")
    return value


def _strings(value: Any, name: str, maximum: int = 100) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded list")
    checked = [_string(item, name) for item in value]
    if len(set(checked)) != len(checked):
        raise ValueError(f"{name} must not contain duplicates")
    return checked


def descriptor(connection_id: str, raw: dict, *, live_drivers: set[str] | None = None) -> ConnectorDescriptor:
    connection_id = _string(connection_id, "connection_id", 128)
    if not isinstance(raw, dict):
        raise ValueError("connection config must be object")
    if type(raw.get('enabled', True)) is not bool:
        raise ValueError('enabled must be a boolean')
    driver = _string(raw.get("driver", ""), "driver", 64)
    contract = raw.get("contract_version", CONTRACT_VERSION)
    if contract != CONTRACT_VERSION:
        raise ValueError("unsupported connector contract version")
    lifecycle = raw.get("lifecycle", "configured")
    if not isinstance(lifecycle, str) or lifecycle not in LIFECYCLES:
        raise ValueError("invalid connector lifecycle")
    caps = _strings(raw.get("capabilities", ["discover", "validate", "read"]),
                    'connector capabilities', len(CAPABILITIES))
    if not caps or any(c not in CAPABILITIES for c in caps):
        raise ValueError("invalid connector capabilities")
    scopes = _strings(raw.get("scopes", []), 'connector scopes')
    agents = _strings(raw.get("agent_ids", []), 'connector agent scope')
    live = live_drivers or set()
    if driver in live and set(caps) - {'discover', 'validate', 'read'}:
        raise ValueError('Read-only adapter cannot advertise write capability')
    if not raw.get("enabled", True):
        status = "disabled"
    elif lifecycle == 'revoked':
        status = 'revoked'
    elif driver in live:
        # Config declarations are not evidence of live health.
        status = "configured_not_live_verified"
    elif driver in DECLARATIVE_DRIVERS:
        status = "adapter_required"
    else:
        status = "unsupported_driver"
    return ConnectorDescriptor(connection_id, driver, contract, lifecycle, tuple(caps), status, tuple(scopes), tuple(agents))


def public_descriptor(d: ConnectorDescriptor) -> dict:
    return {
        "id": d.connection_id,
        "driver": d.driver,
        "contract_version": d.contract_version,
        "lifecycle": d.lifecycle,
        "capabilities": list(d.capabilities),
        "status": d.status,
        "scopes": list(d.scopes),
        "agent_ids": list(d.agent_ids),
    }
