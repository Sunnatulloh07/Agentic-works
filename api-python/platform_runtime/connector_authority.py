"""Fail-closed read authority shared by metadata-independent DB entry points.

An empty connection agent_ids list adds no restriction to the required pack
allowlist. A nonempty list requires an explicit matching runtime agent. Provider
OAuth scopes remain metadata until a provider adapter enforces its own contract.
"""
from .connector_contract import descriptor, SUPPORTED_LOCAL_DRIVERS
from .engine import Forbidden


READ_LIFECYCLES = frozenset({'configured', 'healthy', 'degraded'})


def require_read_access(raw, *, agent=None):
    if not isinstance(raw, dict) or raw.get('enabled', True) is False:
        raise Forbidden('Connection unavailable')
    info = descriptor('connection', raw, live_drivers=SUPPORTED_LOCAL_DRIVERS)
    if info.lifecycle not in READ_LIFECYCLES:
        raise Forbidden('Connection lifecycle does not permit execution')
    if info.driver not in SUPPORTED_LOCAL_DRIVERS:
        raise Forbidden('Read adapter unavailable')
    if 'read' not in info.capabilities:
        raise Forbidden('Connection does not permit read')
    if info.agent_ids and (not isinstance(agent, str) or agent not in info.agent_ids):
        raise Forbidden('Connection not allowed for this agent')
    return info
