"""Route a manager's question to the right section agent (PRD v0.5, P10b / T2).

A manager does not know that a question about a late delivery belongs to the
logistics agent and a question about a stalled lead belongs to the sales agent.
They ask one thing, in their own words. This module decides which section agent
should answer, and then hands the question over.

The one rule everything else follows from
-----------------------------------------

**The supervisor's authority is never inherited.**

    "Supervisor boshqa agentni chaqirsa, ikkinchi agent o'z `policy`si bilan
     ishlaydi. Supervisor vakolati meros qilinmaydi."  — PRD v0.5 §4 (UPA)

So routing here is a **routing decision, not a permission grant**. When the
supervisor picks a target, this module does exactly one thing: it creates an
ordinary agent run for that target, through ``AgentLoop.create``. The target's
own policy is then re-resolved from its own pack entry, exactly as if the
manager had addressed it directly. The supervisor's ``tools``,
``allowed_connections``, ``ladder`` and ``allowed_recipients`` are never copied,
merged, widened or handed on.

The practical consequence is worth stating plainly, because it is the whole
security property: **a supervisor that cannot read the CRM cannot cause the CRM
to be read through a subordinate.** If the logistics agent holds
``connectors.read`` and the supervisor does not, the logistics agent still reads
fine when addressed — and the supervisor gains nothing, because it never
performs that read itself. Authority flows to the actor that executes, not to
the actor that asked.

The second rule: a step cap
---------------------------

    "Agent-to-agent cheksiz zanjir — supervisor → agent → agent → … Bu
     `max_steps` bilan to'silishi kerak."  — PRD v0.5 §7 (open risk 5)

A delegation chain that can extend itself is a cost that grows without bound, so
the chain length is bounded three ways:

* ``max_hops`` caps how many delegations may be made *to the same target* within
  the request's ledger. The default is 1 — a single hop — because the supervisor's
  job is to hand a question over, not to run a tree of agents. The count is read
  from the ledger, so it survives a restart and the caller cannot reset it.
* **A delegate may not itself be a supervisor.** A target that carries the
  ``route`` tool is refused by default (``allow_chained=False``). Otherwise
  'supervisor → agent → agent' is reachable simply by naming another supervisor.
* **Routing is not a tool.** Run creation lives in the control plane, not in a
  registered tool, so no agent can manufacture delegated runs by naming one and
  the cap is enforced by code rather than by prompting.

The third rule: routing is bounded work
---------------------------------------

Choosing a target may be done by the model or by an operator-declared keyword
map. Both are supported, and both are bounded: the candidate list is capped,
the question is capped, and a decision that names something outside the declared
section list is refused rather than passed through. The model can only ever pick
from a list the operator wrote; it cannot invent an agent id, and it cannot name
a tool, a connection or a recipient.
"""
from __future__ import annotations

import json
import re

from .engine import Conflict, Forbidden, NotFound, RateLimited

ROUTE_TOOLS = ('supervisor.route', 'supervisor.sections')

# The delegated run's key is derived from the request key, so the request key's ceiling
# is the RUN key's ceiling minus this prefix. Measured before the fix: a request key up
# to the declared 256 passed this module's own check and then failed inside
# ``AgentLoop.create`` with 'Invalid run identity' for every length from 246 upward,
# because the derived key was over the loop's own 256. The two numbers are now one
# expression, so they cannot drift apart again.
RUN_KEY_PREFIX = 'supervisor:'
MAX_REQUEST_KEY = 256 - len(RUN_KEY_PREFIX)

# A section id and an agent id follow the same shape the rest of the platform uses.
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,127}$')

MAX_SECTIONS = 20
MAX_QUESTION = 2000
MAX_KEYWORDS = 20
MAX_KEYWORD_LENGTH = 60
MAX_HOPS_CEILING = 3
MAX_STEPS_CEILING = 12

DEFAULT_MAX_HOPS = 1
DEFAULT_MAX_STEPS = 6

SCHEMA = '''
CREATE TABLE IF NOT EXISTS p_supervisor_section(
 tenant TEXT NOT NULL, id TEXT NOT NULL, actor TEXT NOT NULL,
 agent TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', keywords TEXT NOT NULL DEFAULT '[]',
 enabled INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS p_supervisor_route(
 tenant TEXT NOT NULL, request_key TEXT NOT NULL, question TEXT NOT NULL,
 actor TEXT NOT NULL, section TEXT NOT NULL DEFAULT '', agent TEXT NOT NULL DEFAULT '',
 run_id TEXT NOT NULL DEFAULT '', matched TEXT NOT NULL DEFAULT '',
 hops INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 created REAL NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY(tenant,request_key));
CREATE INDEX IF NOT EXISTS p_supervisor_section_enabled
 ON p_supervisor_section(tenant,enabled);
'''


class SupervisorError(RuntimeError):
    """A routing request could not be carried out."""


def _bounded(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def _name(value, name, maximum=128):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{name} must be a non-empty string of at most {maximum} characters')
    text = value.strip()
    if not NAME_RE.match(text):
        raise ValueError(f'{name} must be lowercase letters, digits, dot, dash or underscore')
    return text


def _keywords(value):
    """Operator-declared trigger words for a section.

    These are matched literally and case-folded. They are deliberately *not*
    regular expressions: an operator writing a keyword is writing a word, and a
    pattern language would let a typo widen a match invisibly.
    """
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_KEYWORDS:
        raise ValueError(f'keywords must be a list of at most {MAX_KEYWORDS}')
    clean = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > MAX_KEYWORD_LENGTH:
            raise ValueError(f'each keyword must be a non-empty string of at most '
                             f'{MAX_KEYWORD_LENGTH} characters')
        clean.append(item.strip().casefold())
    return clean


class Supervisor:
    """Declared sections plus a bounded router. Read-only apart from the delegated run."""

    def __init__(self, engine, loop=None):
        self.engine = engine
        self._loop = loop

    @property
    def loop(self):
        # AgentLoop is imported lazily so this module can be imported by tests that
        # only exercise section configuration and matching.
        if self._loop is None:
            from .agent_loop import AgentLoop
            self._loop = AgentLoop(self.engine)
        return self._loop

    # ------------------------------------------------------------------ setup

    def declare(self, tenant, section_id, agent, actor, *, title='', keywords=None,
                enabled=True):
        """Owner-only. Declare that one section agent answers one kind of question.

        The declaration is operator configuration, exactly like a graph source or
        a sheets register. The model never creates one and never sees the keyword
        map as a tool argument: it can only choose among sections already here.
        """
        section_id = _name(section_id, 'section id', 64)
        agent = _name(agent, 'agent', 128)
        actor = _name(actor, 'actor', 128)
        if not isinstance(title, str) or len(title) > 120:
            raise ValueError('title must be a string of at most 120 characters')
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        clean = _keywords(keywords)
        # The target's policy must exist and be usable now, otherwise every route
        # into this section would fail later with an opaque error.
        policy = self.engine.policy(tenant, agent)
        if policy.get('ladder') not in {'human_led', 'human_assisted', 'autonomous'}:
            raise Forbidden('Target agent policy unavailable for routing')
        e = self.engine
        with e.tx() as c:
            e.require_active(c, tenant)
            e.require_authority(c, tenant, 'cron', actor, ('owner',))
            # MAX_SECTIONS was declared and never read, so the section count was
            # unbounded while every other collection in this module is bounded. Enforced
            # for a NEW section only: re-declaring an existing one is always allowed, so
            # a tenant that already holds more than the ceiling keeps working.
            existing = c.execute(
                'SELECT count(*) n FROM p_supervisor_section WHERE tenant=?',
                (tenant,)).fetchone()['n']
            known = c.execute(
                'SELECT 1 FROM p_supervisor_section WHERE tenant=? AND id=?',
                (tenant, section_id)).fetchone()
            if known is None and existing >= MAX_SECTIONS:
                raise ValueError(
                    f'at most {MAX_SECTIONS} supervisor sections may be declared')
            now = e.clock()
            c.execute('''INSERT INTO p_supervisor_section
              (tenant,id,actor,agent,title,keywords,enabled,created,updated)
              VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(tenant,id) DO UPDATE SET
               actor=excluded.actor, agent=excluded.agent, title=excluded.title,
               keywords=excluded.keywords, enabled=excluded.enabled, updated=excluded.updated''',
                      (tenant, section_id, actor, agent, title, _dump(clean),
                       int(enabled), now, now))
            e.audit(c, tenant, '', 'supervisor.section_declared', actor,
                    {'section': section_id, 'agent': agent, 'enabled': enabled})
        return self.section(tenant, section_id)

    def section(self, tenant, section_id):
        with self.engine.read() as c:
            row = c.execute('SELECT * FROM p_supervisor_section WHERE tenant=? AND id=?',
                            (tenant, section_id)).fetchone()
        if not row:
            raise NotFound('Supervisor section not found')
        return self._view(row)

    def sections(self, tenant):
        with self.engine.read() as c:
            rows = c.execute('''SELECT * FROM p_supervisor_section WHERE tenant=?
                                ORDER BY id''', (tenant,)).fetchall()
        return [self._view(row) for row in rows]

    def _view(self, row):
        out = dict(row)
        out['keywords'] = _load(out.get('keywords'))
        out['enabled'] = bool(out.get('enabled'))
        return out

    # ------------------------------------------------------------------ routing

    def _routable(self, tenant):
        """Enabled sections, in section-id order, resolved to their own policies.

        The order is the one ``sections`` returns, which is ``ORDER BY id`` --
        **alphabetical by section id**, not the order the operator happened to
        declare them in. That matters because it is also the tie-break below, so
        the id is the precedence key and the docstring here used to say otherwise.

        A section whose agent has since lost its policy is skipped rather than
        offered: routing to it would create a run that the target itself would
        then be unable to authorise.
        """
        out = []
        for section in self.sections(tenant):
            if not section['enabled']:
                continue
            try:
                policy = self.engine.policy(tenant, section['agent'])
            except Exception:
                continue
            if policy.get('ladder') not in {'human_led', 'human_assisted', 'autonomous'}:
                continue
            out.append({'section': section['id'], 'agent': section['agent'],
                        'title': section['title'], 'keywords': section['keywords'],
                        'tools': sorted(policy.get('tools') or []),
                        'is_router': ROUTE_TOOLS[0] in (policy.get('tools') or [])})
        return out

    def _match(self, tenant, question):
        """Pick a section by operator-declared keyword, or ``None``.

        Longest keyword wins; a tie between two equally long keywords goes to the
        **alphabetically first section id**, because the candidate list comes from
        ``_routable`` and that is ``ORDER BY id``. This docstring used to say
        "declaration order", which was wrong and misleading in a way that mattered:
        an operator who read it would declare precedence-ordered sections and get
        the opposite routing, with a well-formed ledger row and no error to notice.
        The tests below pin the real rule so the sentence cannot drift again.

        Deterministic on purpose: a routing decision that is hard to explain is
        hard to audit, and the keyword map is small enough that a deterministic
        rule is enough.
        """
        text = question.casefold()
        best = None
        for index, candidate in enumerate(self._routable(tenant)):
            for keyword in candidate['keywords']:
                if keyword and keyword in text:
                    score = (len(keyword), -index)
                    if best is None or score > best[0]:
                        best = (score, candidate, keyword)
        if best is None:
            return None
        return best[1], best[2]

    def route(self, tenant, request_key, question, actor, *, section='', max_hops=None,
              max_steps=DEFAULT_MAX_STEPS, max_seconds=1800, allow_chained=False):
        """Send one manager question to one section agent.

        Exactly one delegated run is created per call, through the ordinary
        ``AgentLoop.create`` path, so the target is validated, rate-limited and
        audited like any other run. Its policies are the target's own.

        ``section`` pins the destination. When it is absent the destination is
        chosen from the operator's keyword map. When neither yields a section the
        call fails: an unrouted question is not silently sent to a default agent,
        because the wrong agent answering a payroll question is worse than no
        answer.
        """
        e = self.engine
        if (not isinstance(request_key, str) or not request_key.strip()
                or len(request_key) > MAX_REQUEST_KEY):
            raise ValueError('request_key must be a non-empty string of at most '
                             f'{MAX_REQUEST_KEY} characters')
        if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION:
            raise ValueError(f'question must contain 1..{MAX_QUESTION} characters')
        actor = _name(actor, 'actor', 128)
        max_hops = DEFAULT_MAX_HOPS if max_hops is None else _bounded(
            max_hops, 'max_hops', 1, MAX_HOPS_CEILING)
        max_steps = _bounded(max_steps, 'max_steps', 1, MAX_STEPS_CEILING)
        if type(allow_chained) is not bool:
            raise ValueError('allow_chained must be a boolean')
        if not isinstance(max_seconds, int) or not 60 <= max_seconds <= 86400:
            raise ValueError('max_seconds must be an integer 60..86400')
        if section != '':
            section = _name(section, 'section', 64)

        # Replay is not a second delegation. The run already exists under this
        # request key, so the stored answer is returned rather than a new hop
        # being spent.
        previous = self._lookup(tenant, request_key)
        if previous is not None:
            if previous['question'] != question.strip():
                raise Conflict('Route key reused with a different question')
            return previous

        with e.read() as c:
            e.require_active(c, tenant)

        candidates = self._routable(tenant)
        if not candidates:
            raise Forbidden('No supervisor section is declared for this tenant')

        matched = ''
        if section:
            chosen = next((item for item in candidates if item['section'] == section), None)
            if chosen is None:
                raise Forbidden(f'Supervisor section {section!r} is not declared or is disabled')
        else:
            picked = self._match(tenant, question)
            if picked is None:
                # Named refusal, not a default: the operator can see which question
                # could not be routed and add a keyword.
                self._record(tenant, request_key, question, actor, '', '',
                             status='unrouted', reason='no_section_matched')
                raise Forbidden('No section matched this question')
            chosen, matched = picked

        target = chosen['agent']
        # A delegate may not itself be a router unless the operator explicitly
        # allows chaining, because supervisor -> supervisor -> ... is exactly the
        # unbounded chain the PRD calls out.
        if chosen['is_router'] and not allow_chained:
            self._record(tenant, request_key, question, actor, chosen['section'], target,
                         matched=matched, status='refused', reason='chained_router_not_allowed')
            raise Forbidden('Target section is itself a router; chaining is not allowed')
        # Chain depth is counted from the request key, not asserted. A request whose
        # key already routed is the same request and returned above; a *new* key
        # that names the same target as an already-routed key is a second hop, and
        # that is what max_hops bounds. With the default of one hop the second
        # delegation is refused instead of quietly becoming a chain.
        depth = self._depth(tenant, target)
        if depth + 1 > max_hops:
            self._record(tenant, request_key, question, actor, chosen['section'], target,
                         matched=matched, status='refused', reason='hop_cap_reached')
            raise Forbidden(f'Hop cap reached: {target!r} has already been routed to '
                            f'{depth} time(s) under max_hops={max_hops}')

        # The one place authority is decided: the target's own policy. Note what
        # is NOT here -- the supervisor's tools, connections or ladder are never
        # consulted, copied or merged. If the target cannot do the work, the work
        # does not happen.
        policy = e.policy(tenant, target)
        if policy.get('ladder') not in {'human_led', 'human_assisted', 'autonomous'}:
            raise Forbidden('Target agent policy unavailable')

        try:
            run_id = self.loop.create(tenant, RUN_KEY_PREFIX + request_key, target,
                                      question.strip(), actor, max_steps=max_steps,
                                      max_seconds=max_seconds)
        except (Forbidden, RateLimited, Conflict) as error:
            self._record(tenant, request_key, question, actor, chosen['section'], target,
                         matched=matched, status='refused', reason=type(error).__name__)
            raise
        except ValueError as error:
            self._record(tenant, request_key, question, actor, chosen['section'], target,
                         matched=matched, status='refused', reason=type(error).__name__)
            raise

        record = self._record(tenant, request_key, question, actor, chosen['section'], target,
                              run_id=run_id, matched=matched, hops=1, status='routed')
        e.audit_write(tenant, 'supervisor.routed', actor,
                      {'section': chosen['section'], 'agent': target, 'run': run_id,
                       'matched': matched, 'max_hops': max_hops, 'max_steps': max_steps})
        # ``max_hops`` documents the cap that was enforced for this request. With
        # the default of one, the single run above is the whole chain.
        record['max_hops'] = max_hops
        record['max_steps'] = max_steps
        return record

    # ------------------------------------------------------------------ ledger

    def _lookup(self, tenant, request_key):
        with self.engine.read() as c:
            row = c.execute('SELECT * FROM p_supervisor_route WHERE tenant=? AND request_key=?',
                            (tenant, request_key)).fetchone()
        return dict(row) if row else None

    def _depth(self, tenant, target):
        """How many routed runs already exist for this target agent.

        Depth is measured from the ledger, so it survives a process restart and
        cannot be reset by a caller. Counting distinct request keys rather than
        rows matters: one request key is one delegation, whatever the row does.
        """
        with self.engine.read() as c:
            row = c.execute('''SELECT count(DISTINCT request_key) n FROM p_supervisor_route
              WHERE tenant=? AND agent=? AND status='routed' AND run_id!='' ''',
                            (tenant, target)).fetchone()
        return int(row['n']) if row else 0

    def history(self, tenant, limit=100, *, with_total=False):
        """Recent routing rows, newest first.

        **A bare list cannot tell a full page from the whole routing history.** The
        note here said "only tests call this today, so it is a trap rather than a
        live defect" -- wrong, because ``GET /{tenant}/supervisor/history`` returns
        these rows as ``routes``. "How many questions were routed" answered with the
        page size, the same defect ``erp.posting_status`` carried.

        ``with_total=True`` counts the table in the same read and returns
        ``(rows, total, truncated)``. ``truncated`` compares against the population
        rather than the limit, so a history holding exactly ``limit`` rows is
        reported as complete. The private helper above takes the other, correct
        approach for its own count -- asking the database with ``count(...)``
        instead of measuring a fetched list -- and that is what happens here too.
        """
        limit = min(max(1, int(limit)), 500)
        with self.engine.read() as c:
            rows = c.execute('''SELECT * FROM p_supervisor_route WHERE tenant=?
                                ORDER BY created DESC, request_key LIMIT ?''',
                             (tenant, limit)).fetchall()
            if not with_total:
                return [dict(row) for row in rows]
            total = c.execute('SELECT count(*) n FROM p_supervisor_route WHERE tenant=?',
                              (tenant,)).fetchone()['n']
        return [dict(row) for row in rows], total, total > len(rows)

    def _record(self, tenant, request_key, question, actor, section, agent, *, run_id='',
                matched='', hops=0, status, reason=''):
        """Write the routing ledger row. One transition, no retry loop.

        A refused route is recorded too: "the supervisor declined this question"
        is a fact an operator needs, and losing it would make a routing failure
        look like a question nobody asked.
        """
        e = self.engine
        now = e.clock()
        with e.tx() as c:
            c.execute('''INSERT INTO p_supervisor_route
              (tenant,request_key,question,actor,section,agent,run_id,matched,hops,status,
               reason,created,updated)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(tenant,request_key) DO UPDATE SET
               section=excluded.section, agent=excluded.agent, run_id=excluded.run_id,
               matched=excluded.matched, hops=excluded.hops, status=excluded.status,
               reason=excluded.reason, updated=excluded.updated''',
                      (tenant, request_key, question.strip(), actor, section, agent,
                       run_id, matched, hops, status, reason, now, now))
        return self._lookup(tenant, request_key) or {}


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _load(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


# --------------------------------------------------------------- tool handlers


def _route_tool(engine, tenant, agent, args, step):
    """Read-only tool: answer which agents exist and where a question would go.

    This tool deliberately **does not route**. It reports the declared sections and
    the agent each one resolves to, so an operator can see the map before trusting
    it. Creating a run is a separate, audited action (``Supervisor.route``) taken
    by the control plane, not something a model can trigger by naming a tool.
    """
    supervisor = Supervisor(engine)
    question = args.get('question', '')
    if question:
        if len(question) > MAX_QUESTION:
            raise ValueError(f'question must be at most {MAX_QUESTION} characters')
        picked = supervisor._match(tenant, question)
        if picked is None:
            return {'tenant': tenant, 'question': question, 'match': None,
                    'note': 'Savol hech qaysi bo‘limga tushmadi. Operator keyword '
                            'qo‘shishi kerak.'}
        chosen, keyword = picked
        return {'tenant': tenant, 'question': question,
                'match': {'section': chosen['section'], 'agent': chosen['agent'],
                          'title': chosen['title'], 'keyword': keyword}}
    return {'tenant': tenant, 'sections': supervisor._routable(tenant)}


def _sections_tool(engine, tenant, agent, args, step):
    """Read-only tool: the declared section map, without any routing."""
    return {'tenant': tenant, 'sections': Supervisor(engine)._routable(tenant)}


def register_supervisor_tools(registry):
    """Two read tools. Routing itself is a control-plane action, not a model tool.

    That split is the point. If routing were a tool, any agent holding it could
    manufacture delegated runs, and the step cap would be enforced only by the
    prompt. Keeping run creation in the control plane means the cap is enforced by
    code at the only place that can create the run.

    Registration is idempotent. ``build_registry`` already calls this, so a caller
    that builds a registry and then adds these tools would otherwise hit the
    duplicate-name guard and fail with a message that says nothing about why. Re-adding
    an existing tool is a no-op rather than an error, which is the same tolerance the
    engine applies when it re-runs its schema scripts.
    """
    from .tools import Tool, obj, string
    for tool in (
        Tool('supervisor.route', 'read', obj({'question': string(MAX_QUESTION)}), _route_tool),
        Tool('supervisor.sections', 'read', obj({}), _sections_tool),
    ):
        if tool.name in registry.items:
            continue
        registry.add(tool)

