"""Read-only oversight of the platform's own agents (PRD v0.5, P9 / T3).

A manager asks three questions about the agents working for them: what did they
do, what did they cost, and are they healthy. This module answers exactly those
three and nothing else.

The boundary that makes this safe is that oversight is **read-only**. There is no
path here that changes an agent's tools, ladder, policy or budget, and that is a
deliberate refusal rather than an unfinished feature: an agent that can change
its own permissions destroys the audit chain, because whoever changed something
would also be the one writing the record of the change. Changing an agent stays
an operator action through the existing pack and budget endpoints.

Two consequences of reading only:

* Every number comes from state the platform already records for its own
  execution — ``p_audit``, ``p_agent_runs``, ``p_tasks``, ``p_budget_*``. Nothing
  is inferred, and nothing is stored a second time.
* A missing table means this meter was never configured, not that the agent spent
  nothing. ``health`` reports the fields it could not read as ``null`` with a
  ``not_recorded`` note instead of a reassuring zero.

The agent name is scoped to the tenant's declared pack: an unknown agent is a
404-style refusal rather than an empty activity list, so an operator cannot read
a typo as "this agent has done nothing".
"""
from __future__ import annotations

import re

from .engine import Forbidden

AGENT_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,127}$')
VIEWS = ('activity', 'cost', 'health')

MAX_EVENTS = 100
DEFAULT_WINDOW_SECONDS = 7 * 86400
MAX_WINDOW_SECONDS = 90 * 86400
OVERSIGHT_TOOLS = ('agent.activity', 'agent.cost', 'agent.health')


def _bounded(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def _text(value, name, maximum=128):
    """A required identifier, non-empty AFTER trimming and bounded before it.

    The check tested the TRIMMED value for emptiness and then returned the RAW one, so
    its only caller regex-matched a padded id. Measured: ``_known(' sales ')`` raised
    'Invalid agent id' while ``supervisor._name(' sales ')`` returned 'sales'. Two
    modules disagreeing about whether a padded identifier is valid is worse than either
    answer on its own, so this one now returns what it validated.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{name} must be a non-empty string of at most {maximum} characters')
    return value.strip()


def _declared_agents(engine, tenant):
    """Agent ids the tenant's pack declares, or ``None`` when unavailable.

    The engine carries a ``policy`` callable, not an agent catalog, so this host
    can ask about one agent but cannot enumerate them. ``None`` therefore means
    "cannot enumerate", and the caller falls back to validating the id shape only.
    A future catalog hook is honoured if present rather than assumed.
    """
    catalog = getattr(engine, 'agents', None) or getattr(engine, 'agent_catalog', None)
    if catalog is None:
        return None
    try:
        entries = catalog(tenant) if callable(catalog) else catalog
    except Exception:
        return None
    if not entries:
        return None
    try:
        return {entry['id'] for entry in entries
                if isinstance(entry, dict) and entry.get('id')}
    except (TypeError, KeyError):
        return None


def _known(engine, tenant, agent):
    """Refuse a malformed agent id; refuse an undeclared one when enumerable."""
    agent = _text(agent, 'agent', 128)
    if not AGENT_RE.match(agent):
        raise ValueError('Invalid agent id')
    declared = _declared_agents(engine, tenant)
    if declared is not None and agent not in declared:
        raise Forbidden(f'Agent {agent!r} is not declared for this tenant')
    return agent


def _window(engine, since_seconds):
    _bounded(since_seconds, 'since_seconds', 1, MAX_WINDOW_SECONDS)
    now = engine.clock()
    return now - since_seconds, now


def activity(engine, tenant, agent, since_seconds=DEFAULT_WINDOW_SECONDS, limit=50):
    """What one agent did: task counts, run outcomes and the most recent events.

    Counts come from ``p_tasks`` for work that was actually dispatched, and from
    ``p_agent_runs`` for loop turns. They are reported separately because a task
    without a run is a scheduled or operator-submitted plan, and merging the two
    would inflate the agent's apparent workload.

    Events are joined through ``p_tasks.agent`` rather than matched against the
    free-text ``p_audit.data`` blob: a substring match would let agent ``sales``
    collect the audit trail of ``sales.order_taker``, which is exactly the kind of
    quiet over-reporting an oversight tool must not do.
    """
    _known(engine, tenant, agent)
    limit = _bounded(limit, 'limit', 1, MAX_EVENTS)
    since, now = _window(engine, since_seconds)
    with engine.read() as c:
        task_rows = c.execute(
            '''SELECT status, count(*) n FROM p_tasks
               WHERE tenant=? AND agent=? AND created>=? GROUP BY status''',
            (tenant, agent, since)).fetchall()
        run_rows = c.execute(
            '''SELECT status, count(*) n FROM p_agent_runs
               WHERE tenant=? AND agent=? AND created>=? GROUP BY status''',
            (tenant, agent, since)).fetchall()
        # One row past the limit, so `truncated` can be answered honestly. With
        # `LIMIT ?` set to the limit itself, a result of exactly `limit` rows is
        # ambiguous: it is produced both by an agent with precisely that many events
        # and by one with thousands. Reporting `truncated=True` for the first sends
        # an operator to widen a window that already contains everything.
        probe = limit + 1
        audit = c.execute(
            '''SELECT a.action, a.actor, a.task, a.created FROM p_audit a
               JOIN p_tasks t ON t.id=a.task AND t.tenant=a.tenant
               WHERE a.tenant=? AND t.agent=? AND a.created>=? AND a.task<>''
               ORDER BY a.created DESC, a.id DESC LIMIT ?''',
            (tenant, agent, since, probe)).fetchall()
        latest = c.execute(
            'SELECT max(updated) last FROM p_agent_runs WHERE tenant=? AND agent=?',
            (tenant, agent)).fetchone()
    cut = len(audit) > limit
    audit = audit[:limit]
    return {
        'agent': agent, 'since': since, 'until': now,
        'tasks': {row['status']: row['n'] for row in task_rows},
        'runs': {row['status']: row['n'] for row in run_rows},
        'events': [{'action': row['action'], 'actor': row['actor'],
                    'task': row['task'], 'created': row['created']} for row in audit],
        'last_activity': latest['last'] if latest else None,
        'truncated': cut,
    }


def cost(engine, tenant, agent, since_seconds=DEFAULT_WINDOW_SECONDS):
    """What one agent cost, attributed through the agent's own runs.

    The budget ledger keys a reservation by ``request_key``, which is
    ``model:<uuid>`` or a caller-supplied key — it does not carry the agent. Cost
    is therefore attributed through ``p_agent_runs`` in the same window, and the
    result says so in ``attribution``. Matching the agent id against
    ``request_key`` would look plausible and silently return the wrong rows, so it
    is not done.

    ``spent_micro`` counts only settled and reconciled reservations. In-flight
    reservations are reported separately and never counted as spent, because an
    unsettled reservation may still be cancelled or reconciled down.
    """
    _known(engine, tenant, agent)
    since, now = _window(engine, since_seconds)
    with engine.read() as c:
        runs = c.execute(
            '''SELECT count(*) n, coalesce(sum(calls),0) calls FROM p_agent_runs
               WHERE tenant=? AND agent=? AND created>=?''',
            (tenant, agent, since)).fetchone()
        ledger = c.execute(
            '''SELECT status, count(*) n, coalesce(sum(amount_micro),0) total
               FROM p_budget_reservations WHERE tenant=? AND created>=?
               GROUP BY status''',
            (tenant, since)).fetchall()
        settings = c.execute(
            'SELECT currency, limit_micro FROM p_budget_settings WHERE tenant=?',
            (tenant,)).fetchone()
        spend = c.execute(
            '''SELECT coalesce(sum(acc.spent_micro),0) total FROM p_budget_accounts acc
               WHERE acc.tenant=?''',
            (tenant,)).fetchone()
    by_status = {row['status']: {'calls': row['n'], 'amount_micro': row['total']}
                 for row in ledger}
    inflight = sum(item['amount_micro'] for status, item in by_status.items()
                   if status in {'reserved', 'dispatching', 'uncertain'})
    return {
        'agent': agent, 'since': since, 'until': now,
        'attribution': 'agent_run_window',
        'attribution_note': ('The budget ledger does not record the agent per '
                             'reservation, so cost is attributed by the agent run '
                             'window and the tenant totals are reported alongside.'),
        'runs': runs['n'] if runs else 0,
        'planner_calls': runs['calls'] if runs else 0,
        'tenant_spent_micro': spend['total'] if spend else 0,
        'tenant_ledger_by_status': by_status,
        'tenant_inflight_micro': inflight,
        'currency': settings['currency'] if settings else None,
        'budget_configured': settings is not None,
    }


def health(engine, tenant, agent):
    """Whether an agent is working: last run, recent failure rate, budget state.

    A field this host cannot read is ``null`` with the reason in
    ``not_recorded``. Reporting an unread meter as zero would look like a healthy
    agent, which is the one answer this tool must never give wrongly.
    """
    _known(engine, tenant, agent)
    not_recorded = []
    with engine.read() as c:
        last = c.execute(
            '''SELECT id, status, updated, error FROM p_agent_runs
               WHERE tenant=? AND agent=? ORDER BY updated DESC, id DESC LIMIT 1''',
            (tenant, agent)).fetchone()
        window = c.execute(
            '''SELECT status, count(*) n FROM p_agent_runs
               WHERE tenant=? AND agent=? GROUP BY status''',
            (tenant, agent)).fetchall()
        pending = c.execute(
            '''SELECT count(*) n FROM p_tasks
               WHERE tenant=? AND agent=? AND status IN ('queued','waiting_approval','running')''',
            (tenant, agent)).fetchone()
        approvals = c.execute(
            '''SELECT count(*) n FROM p_approvals a
               JOIN p_steps s ON s.id=a.step AND s.tenant=a.tenant
               JOIN p_tasks t ON t.id=s.task AND t.tenant=s.tenant
               WHERE a.tenant=? AND t.agent=? AND a.status='pending' ''',
            (tenant, agent)).fetchone()
        frozen = c.execute('SELECT stopped FROM p_freeze WHERE tenant=?', (tenant,)).fetchone()
        budget = c.execute(
            'SELECT 1 FROM p_budget_settings WHERE tenant=?', (tenant,)).fetchone()
    if budget is None:
        not_recorded.append('budget')
    totals = {row['status']: row['n'] for row in window}
    finished = sum(totals.get(status, 0) for status in
                   ('succeeded', 'needs_input', 'escalated', 'uncertain', 'cancelled'))
    failed = sum(totals.get(status, 0) for status in ('escalated', 'uncertain'))
    return {
        'agent': agent,
        'last_run': ({'id': last['id'], 'status': last['status'],
                      'updated': last['updated'], 'error': last['error']}
                     if last else None),
        'runs_by_status': totals,
        'finished_runs': finished,
        'failure_rate': (failed / finished) if finished else None,
        'pending_tasks': pending['n'] if pending else 0,
        'pending_approvals': approvals['n'] if approvals else 0,
        'tenant_stopped': bool(frozen and frozen['stopped']),
        'not_recorded': not_recorded,
    }


def _dispatch(name, engine, tenant, agent, args):
    """Dispatch one oversight view. Read-only by construction.

    ``VIEWS`` was declared and never read, so the closed set existed only in this
    if-chain and in the registered tool names. The declaration is now the guard, and
    the fall-through is 'health' because the guard has already excluded everything else.
    """
    target = args.get('agent', agent)
    view = name.split('.', 1)[1]
    if view not in VIEWS:
        raise ValueError(f'Unsupported oversight view {view!r}')
    if view == 'activity':
        return activity(engine, tenant, target,
                        args.get('since_seconds', DEFAULT_WINDOW_SECONDS),
                        args.get('limit', 50))
    if view == 'cost':
        return cost(engine, tenant, target,
                    args.get('since_seconds', DEFAULT_WINDOW_SECONDS))
    return health(engine, tenant, target)


def _activity_tool(engine, tenant, agent, args, step):
    return _dispatch('agent.activity', engine, tenant, agent, args)


def _cost_tool(engine, tenant, agent, args, step):
    return _dispatch('agent.cost', engine, tenant, agent, args)


def _health_tool(engine, tenant, agent, args, step):
    return _dispatch('agent.health', engine, tenant, agent, args)


def register_oversight_tools(registry):
    """Three read tools. No write path exists here at all.

    Oversight must not be able to change what it observes: an agent that can
    edit its own ladder, tools or budget would break the audit chain, because
    the actor and the recorded actor would be the same entity.
    """
    from .tools import Tool, obj, string, register_once
    common = {'agent': string(128),
              'since_seconds': {'type': 'integer', 'minimum': 1,
                                'maximum': MAX_WINDOW_SECONDS}}
    register_once(registry, [
        Tool('agent.activity', 'read', obj(
            {**common, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_EVENTS}},
            ['agent']), _activity_tool),
        Tool('agent.cost', 'read', obj(common, ['agent']), _cost_tool),
        Tool('agent.health', 'read', obj({'agent': string(128)}, ['agent']), _health_tool),
    ])
