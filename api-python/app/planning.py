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


def conversation_agent(listing, tenant, channel):
    """First agent in pack order that holds a conversation on `channel`, or None.

    Capability-driven like `route`: the agent must opt in (conversation.enabled)
    and declare a message trigger whose source is the inbound channel. The
    result carries no steps; the engine records a conversation turn instead.
    """
    for agent in listing(tenant):
        settings = agent.get('conversation') or {}
        if settings.get('enabled') is not True:
            continue
        for trigger in agent.get('triggers') or []:
            if trigger.get('type') == 'message' and trigger.get('source') == channel:
                return {'agent': agent['id'], 'conversation': settings}
    return None


def planner(engine, listing=None):
    if listing is None:
        from .platform_api import agents as listing
    llm = Planner(engine, listing)

    def step(tenant, tool, args):
        return {'agent': route(listing, tenant, tool), 'steps': [{'tool': tool, 'args': args}]}

    def plan(tenant, channel, payload):
        text = payload.get('text', '').strip()
        # A channel a conversation agent owns carries customers, not operators:
        # every message, slash text included, is theirs to be answered. Operator
        # commands stay on channels no conversation claims (the dashboard).
        turn = conversation_agent(listing, tenant, channel)
        if turn:
            return turn
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
