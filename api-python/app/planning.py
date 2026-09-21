"""Deterministic platform commands; arbitrary natural language uses configured LLM.

Command routing is capability-driven: the core resolves which agent runs a
command from the tenant's own pack and never names an agent id. Adding a
vertical is therefore YAML work, not a core change (CLAUDE.md, PRD tamoyil 1).
The pack listing is injected so this module stays importable, and testable,
without FastAPI or a runtime environment.
"""
from platform_runtime.llm import Planner


def route(listing, tenant, tool):
    """First agent in pack order allowed to use `tool`.

    Pack order is the tenant's declared preference. Raises rather than falling
    back to a guess: a silently mis-routed command is worse than a clear refusal.
    """
    for agent in listing(tenant):
        if tool in (agent.get('tools') or []):
            return agent['id']
    raise LookupError('No agent in this pack is allowed ' + tool)


def planner(engine, listing=None):
    if listing is None:
        from .platform_api import agents as listing
    llm = Planner(engine, listing)

    def step(tenant, tool, args):
        return {'agent': route(listing, tenant, tool), 'steps': [{'tool': tool, 'args': args}]}

    def plan(tenant, channel, payload):
        text = payload.get('text', '').strip()
        if text == '/report':
            return step(tenant, 'reports.summary', {})
        if text.startswith('/memory '):
            return step(tenant, 'memory.search', {'query': text[8:]})
        if text.startswith('/record '):
            parts = text[8:].split('|', 2)
            if len(parts) != 3:
                raise ValueError('Use /record kind|title|body')
            return step(tenant, 'records.create', dict(zip(('kind', 'title', 'body'), parts)))
        return llm(tenant, channel, payload)
    return plan
