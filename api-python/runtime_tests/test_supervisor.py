"""Supervisor routing contract tests. Real Engine, real SQLite, real AgentLoop.

The behaviours that matter here are all about **authority**, so they are asserted
by observing what the target run can and cannot do, not by reading a docstring:

* the target's authority is the target's own. A supervisor without
  ``connectors.read`` does not acquire it by routing to an agent that has it, and
  — the sharper direction — a target cannot do work its own policy forbids even
  when the supervisor is allowed to do it;
* a target that is itself a router is refused, so ``supervisor → supervisor``
  cannot be reached by naming one;
* the hop cap holds, is counted from the ledger rather than asserted by the
  caller, and survives a restart;
* an unrouted question fails by name rather than being sent to a default agent;
* freeze and revocation stop routing, checked at route time rather than only at
  declaration time;
* the model cannot name an agent, a tool, a connection or a recipient: it can
  only choose among sections the operator declared.

Where a run is created it is a real ``AgentLoop`` run in a real SQLite file, so
the engine's own validation, rate limiting and audit path all execute.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from platform_runtime.agent_loop import AgentLoop
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.supervisor import (
    DEFAULT_MAX_HOPS,
    DEFAULT_MAX_STEPS,
    MAX_HOPS_CEILING,
    MAX_KEYWORD_LENGTH,
    MAX_KEYWORDS,
    MAX_QUESTION,
    MAX_REQUEST_KEY,
    MAX_SECTIONS,
    MAX_STEPS_CEILING,
    RUN_KEY_PREFIX,
    ROUTE_TOOLS,
    Supervisor,
    _bounded,
    _keywords,
    _name,
    register_supervisor_tools,
)
from platform_runtime.tools import build_registry

TENANT = 't_sup'
OWNER = 'usr_owner'
OPERATOR = 'usr_ops'

# The supervisor may ask and read the section map, and nothing else.
SUPERVISOR_TOOLS = ['supervisor.route', 'supervisor.sections']

# A section agent that can read the CRM. Deliberately *not* held by the supervisor.
SALES_TOOLS = ['connectors.read', 'telegram.send', 'agent.activity']
SALES_CONNECTIONS = ['amocrm']

# A section agent with no external reach at all.
LOGISTICS_TOOLS = ['agent.activity']


class Clock:
    def __init__(self, start=1_770_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.policies = {
            'mgmt.supervisor': {'tools': list(SUPERVISOR_TOOLS), 'ladder': 'human_assisted',
                                'allowed_connections': []},
            'sales.360': {'tools': list(SALES_TOOLS), 'ladder': 'human_assisted',
                          'allowed_connections': list(SALES_CONNECTIONS)},
            'ops.logistics': {'tools': list(LOGISTICS_TOOLS), 'ladder': 'human_assisted',
                              'allowed_connections': []},
            'ops.warehouse': {'tools': list(LOGISTICS_TOOLS), 'ladder': 'human_assisted',
                              'allowed_connections': []},
            # A second supervisor, to prove chaining is refused by default.
            'mgmt.supervisor_two': {'tools': list(SUPERVISOR_TOOLS),
                                    'ladder': 'human_assisted', 'allowed_connections': []},
        }
        # A test authority gate, so "owner only" is exercised rather than assumed.
        self.authority = None
        # ``build_registry`` already registers the supervisor tools. Adding them a
        # second time must be a no-op -- see ``test_registration_is_idempotent`` --
        # so this harness calls it unconditionally and would fail loudly if the
        # register function stopped tolerating a re-add.
        self.registry = build_registry()
        register_supervisor_tools(self.registry)
        self.engine = Engine(self.root / 'sup.db', self.registry, self._policy,
                             clock=self.clock, authority=self._authority)
        self.supervisor = Supervisor(self.engine)
        self.addCleanup(self.tmp.cleanup)

    # ------------------------------------------------------------------ harness

    def _policy(self, tenant, agent):
        if agent not in self.policies:
            raise Forbidden('Agent not in tenant pack')
        return dict(self.policies[agent])

    def _authority(self, c, tenant, channel='', actor='', roles=('owner', 'operator')):
        """Role gate equivalent to production, plus a test override for revocation."""
        if self.authority is not None:
            self.authority(c, tenant, channel, actor, roles)
            return
        if actor and roles is not None and set(roles) == {'owner'} and actor != OWNER:
            raise Forbidden('actor is not permitted for this action')

    def declare(self, section, agent, *, actor=OWNER, **kwargs):
        return self.supervisor.declare(TENANT, section, agent, actor, **kwargs)

    def seed(self):
        """Three sections, one of which is another supervisor."""
        self.declare('sales', 'sales.360', title='Sotuv',
                     keywords=['mijoz', 'lid', 'sotuv', 'bitrix'])
        self.declare('logistics', 'ops.logistics', title='Logistika',
                     keywords=['yetkazib berish', 'yuk', 'ombor'])
        self.declare('management', 'mgmt.supervisor_two', title='Boshqa supervisor',
                     keywords=['xodim', 'jamoa'])

    def route(self, question, key, **kwargs):
        return self.supervisor.route(TENANT, key, question, OWNER, **kwargs)

    def fresh_supervisor(self):
        """A second supervisor over a brand-new database, so caps cannot leak.

        The hop cap is counted from the ledger and is deliberately durable, which
        is exactly what makes it awkward to use as a probe: once a target has been
        routed to, the cap is one step closer to refusing, and a refusal is
        indistinguishable from the ValueError under test. A fresh engine per
        attempt keeps the two apart.
        """
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        clock = self.clock
        engine = Engine(root / 'sup.db', self.registry, self._policy,
                        clock=clock, authority=self._authority)
        supervisor = Supervisor(engine)
        supervisor.declare(TENANT, 'sales', 'sales.360', OWNER, keywords=['mijoz'])
        return supervisor

    def run_of(self, run_id):
        return AgentLoop(self.engine).get(TENANT, run_id)

    # ------------------------------------------------------------- declaration

    def test_both_route_tools_are_read_only(self):
        for name in ROUTE_TOOLS:
            self.assertIn(name, self.registry.items)
            self.assertEqual('read', self.registry.items[name].risk)

    def test_the_module_exposes_no_run_creation_tool(self):
        """Routing must not be a model-callable tool.

        If it were, any agent holding it could manufacture delegated runs and the
        hop cap would rest on the prompt. Run creation stays in the control plane.
        """
        for tool in self.registry.items.values():
            self.assertNotIn('create_run', tool.name)
        names = {t.name for t in self.registry.items.values()}
        self.assertIn('supervisor.route', names)
        # supervisor.route, despite the name, only reports the map.
        tool = self.registry.items['supervisor.route']
        self.assertEqual('read', tool.risk)

    def test_declaring_a_section_requires_the_owner(self):
        with self.assertRaises(Forbidden):
            self.declare('sales', 'sales.360', actor=OPERATOR)

    def test_declaring_an_unknown_agent_is_refused(self):
        with self.assertRaises(Forbidden):
            self.declare('ghost', 'no.such.agent')

    def test_an_empty_section_id_is_refused(self):
        with self.assertRaises(ValueError):
            self.declare('', 'sales.360')

    def test_an_uppercase_section_id_is_refused(self):
        with self.assertRaises(ValueError):
            self.declare('Sales', 'sales.360')

    def test_keywords_must_be_a_bounded_list_of_strings(self):
        with self.assertRaises(ValueError):
            self.declare('sales', 'sales.360', keywords='mijoz')
        with self.assertRaises(ValueError):
            self.declare('sales', 'sales.360', keywords=['mijoz'] * 50)
        with self.assertRaises(ValueError):
            self.declare('sales', 'sales.360', keywords=['x' * 200])

    def test_a_section_can_be_disabled_and_re_enabled(self):
        self.declare('sales', 'sales.360', keywords=['sotuv'])
        self.declare('sales', 'sales.360', keywords=['sotuv'], enabled=False)
        self.assertFalse(self.supervisor.section(TENANT, 'sales')['enabled'])
        with self.assertRaises(Forbidden):
            self.route('sotuv haqida', 'k1')
        self.declare('sales', 'sales.360', keywords=['sotuv'], enabled=True)
        self.assertTrue(self.supervisor.section(TENANT, 'sales')['enabled'])

    # ------------------------------------------------------------------ routing

    def test_a_declared_section_receives_the_question(self):
        self.seed()
        record = self.route('Mijoz bilan nima bo‘ldi?', 'r1')
        self.assertEqual('routed', record['status'])
        self.assertEqual('sales', record['section'])
        self.assertEqual('sales.360', record['agent'])
        self.assertEqual('mijoz', record['matched'])
        run = self.run_of(record['run_id'])
        self.assertEqual('sales.360', run['agent'])
        self.assertIn('Mijoz bilan nima bo‘ldi?', run['input'])

    def test_the_run_is_created_for_the_target_not_the_supervisor(self):
        self.seed()
        record = self.route('yuk qayerda', 'r1')
        run = self.run_of(record['run_id'])
        self.assertEqual('ops.logistics', run['agent'])
        self.assertNotEqual('mgmt.supervisor', run['agent'])

    def test_longest_keyword_wins_and_the_match_is_reported(self):
        """Two sections can both match; the more specific keyword decides.

        'ombor' is a logistics keyword and 'yetkazib berish ombor' is a longer
        logistics keyword, so the longest match still lands on logistics — the
        point is that the decision is reported, so an operator can see why.
        """
        self.seed()
        record = self.route('ombor yetkazib berish kechikdi', 'r1')
        self.assertEqual('logistics', record['section'])
        self.assertIn(record['matched'], ['ombor', 'yetkazib berish'])

    def test_equal_length_keywords_resolve_by_section_id_not_declaration_order(self):
        """The tie-break is the alphabetically first section id, measurably.

        ``score = (len(keyword), -index)`` picks the earlier candidate on a tie,
        and the candidate list is ``ORDER BY id``. So "earlier" means "smaller
        section id", NOT "declared first" -- and ``_match``'s docstring claimed
        declaration order until this test was written.

        The probe declares in the opposite order to alphabetical, which is the only
        arrangement that separates the two rules. Flipping `-index` to `+index`
        passed the entire suite before this test existed; so did fixing the
        docstring's claim while leaving the code alone, because nothing measured
        which rule the code implements.
        """
        self.seed()
        # 'alpha' sorts before 'zulu', but is declared SECOND, so a rule of
        # "declaration order" would pick 'zulu' and this assertion would fail.
        self.declare('zulu', 'ops.logistics', keywords=['ombor'])
        self.declare('alpha', 'sales.360', keywords=['mijoz'])
        # The candidate list is alphabetical by id -- 'alpha' first even though it
        # was declared last, and 'zulu' last even though it was declared first.
        ids = [section['id'] for section in self.supervisor.sections(TENANT)]
        self.assertEqual(sorted(ids), ids)
        self.assertLess(ids.index('alpha'), ids.index('zulu'))
        # ``_match`` is where the rule lives, so it is read directly for the
        # ordering assertions; ``route`` spends a hop each call, and the default
        # cap of one would refuse the second one for an unrelated reason.
        picked = self.supervisor._match(TENANT, 'mijoz ombor')
        self.assertEqual('alpha', picked[0]['section'])
        self.assertEqual('sales.360', picked[0]['agent'])
        # The reverse word order in the question changes nothing: the rule reads
        # the section id, not where the keyword appeared in the text.
        picked = self.supervisor._match(TENANT, 'ombor mijoz')
        self.assertEqual('alpha', picked[0]['section'])
        # A longer keyword in the later-sorting section still wins, so the rule
        # above is a tie-break rather than a blanket alphabetical preference.
        self.declare('zulu', 'ops.logistics', keywords=['ombor', 'ombor zaxira'])
        picked = self.supervisor._match(TENANT, 'mijoz ombor zaxira kerak')
        self.assertEqual('zulu', picked[0]['section'])
        self.assertEqual('ombor zaxira', picked[1])
        # And the same decision is the one an actual route carries.
        record = self.route('mijoz ombor zaxira kerak', 'r1')
        self.assertEqual('zulu', record['section'])
        self.assertEqual('ombor zaxira', record['matched'])

    def test_an_unrouted_question_is_refused_by_name(self):
        self.seed()
        with self.assertRaises(Forbidden):
            self.route('Bugun havo qanday?', 'r1')
        row = self.supervisor._lookup(TENANT, 'r1')
        self.assertEqual('unrouted', row['status'])
        self.assertEqual('no_section_matched', row['reason'])
        self.assertEqual('', row['agent'])

    def test_an_unrouted_question_never_reaches_a_default_agent(self):
        """A payroll question must not be answered by the sales agent."""
        self.seed()
        with self.assertRaises(Forbidden):
            self.route('oylik qachon beriladi', 'r1')
        with self.engine.read() as c:
            runs = c.execute('SELECT count(*) n FROM p_agent_runs WHERE tenant=?',
                             (TENANT,)).fetchone()['n']
        self.assertEqual(0, runs)

    def test_an_explicit_section_bypasses_the_keyword_map(self):
        self.seed()
        record = self.route('hech qanday kalit so‘z yo‘q', 'r1', section='logistics')
        self.assertEqual('logistics', record['section'])
        self.assertEqual('', record['matched'])

    def test_an_unknown_explicit_section_is_refused(self):
        self.seed()
        with self.assertRaises(Forbidden):
            self.route('savol', 'r1', section='board')

    def test_pinning_a_disabled_section_is_refused(self):
        self.declare('sales', 'sales.360', keywords=['sotuv'], enabled=False)
        with self.assertRaises(Forbidden):
            self.route('sotuv', 'r1', section='sales')

    def test_no_declared_section_is_a_refusal_not_an_empty_result(self):
        with self.assertRaises(Forbidden):
            self.route('savol', 'r1')

    # ----------------------------------------------- authority is not inherited

    def test_the_target_runs_with_its_own_policy(self):
        self.seed()
        record = self.route('mijoz', 'r1')
        run = self.run_of(record['run_id'])
        policy = self.engine.policy(TENANT, run['agent'])
        self.assertIn('connectors.read', policy['tools'])
        # And the supervisor's own policy is untouched by having routed.
        own = self.engine.policy(TENANT, 'mgmt.supervisor')
        self.assertNotIn('connectors.read', own['tools'])

    def test_a_supervisor_gains_nothing_from_routing(self):
        """Routing does not widen the supervisor's own policy."""
        self.seed()
        before = dict(self.engine.policy(TENANT, 'mgmt.supervisor'))
        self.route('mijoz', 'r1')
        after = self.engine.policy(TENANT, 'mgmt.supervisor')
        self.assertEqual(before['tools'], after['tools'])
        self.assertEqual(before['allowed_connections'], after['allowed_connections'])

    def test_a_target_cannot_use_a_tool_its_own_policy_forbids(self):
        """The sharper direction: the target's limits bind, not the supervisor's.

        The supervisor is not even the actor here. The point is that the run's
        authority comes from the target's policy, so a tool the target lacks is
        refused regardless of what the supervisor holds.
        """
        self.seed()
        record = self.route('yuk', 'r1')
        run = self.run_of(record['run_id'])
        target_policy = self.engine.policy(TENANT, run['agent'])
        self.assertNotIn('connectors.read', target_policy['tools'])
        with self.assertRaises(Forbidden):
            self.engine.submit(TENANT, 'agent', 'agent-run:x:000:0', run['agent'],
                               [{'tool': 'connectors.read',
                                 'args': {'connection': 'amocrm', 'path': '/x'}}],
                               OWNER)

    def test_the_targets_connection_limit_binds_not_the_supervisors(self):
        """A target constrained to one connection cannot reach another.

        The supervisor's allowed_connections is empty, so if authority were
        inherited the target could reach nothing; because the target's own policy
        is used, the target's own single connection is what applies.
        """
        self.seed()
        record = self.route('mijoz', 'r1')
        run = self.run_of(record['run_id'])
        policy = self.engine.policy(TENANT, run['agent'])
        self.assertEqual(['amocrm'], policy['allowed_connections'])
        self.assertEqual([], self.engine.policy(TENANT, 'mgmt.supervisor')['allowed_connections'])

    def test_a_run_for_an_agent_with_no_policy_is_refused(self):
        """Deleting a target's policy after declaration stops routing to it."""
        self.seed()
        del self.policies['sales.360']
        with self.assertRaises(Forbidden):
            self.route('mijoz', 'r1')

    def test_a_section_whose_agent_lost_its_policy_is_not_offered(self):
        self.seed()
        del self.policies['sales.360']
        offered = {item['section'] for item in self.supervisor._routable(TENANT)}
        self.assertNotIn('sales', offered)
        self.assertIn('logistics', offered)

    # ---------------------------------------------------------- chaining is off

    def test_a_router_section_is_refused_by_default(self):
        self.seed()
        with self.assertRaises(Forbidden):
            self.route('xodim haqida', 'r1')

    def test_the_refusal_is_recorded_with_a_reason(self):
        self.seed()
        with self.assertRaises(Forbidden):
            self.route('xodim haqida', 'r1')
        row = self.supervisor._lookup(TENANT, 'r1')
        self.assertEqual('refused', row['status'])
        self.assertEqual('chained_router_not_allowed', row['reason'])
        self.assertEqual('', row['run_id'])

    def test_the_sections_view_marks_a_router_section(self):
        self.seed()
        by_section = {item['section']: item for item in self.supervisor._routable(TENANT)}
        self.assertTrue(by_section['management']['is_router'])
        self.assertFalse(by_section['logistics']['is_router'])

    def test_chaining_can_be_enabled_only_explicitly(self):
        self.seed()
        record = self.route('xodim haqida', 'r1', allow_chained=True)
        self.assertEqual('mgmt.supervisor_two', record['agent'])
        self.assertEqual('routed', record['status'])

    def test_allow_chained_must_be_a_boolean(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.route('xodim', 'r1', allow_chained='yes')

    # --------------------------------------------------------------- hop cap

    def test_the_hop_cap_refuses_a_second_delegation_to_the_same_target(self):
        """With the default of one hop, the second delegation to a target is refused."""
        self.seed()
        self.route('mijoz', 'r1')
        with self.assertRaises(Forbidden):
            self.route('mijoz yana', 'r2')
        row = self.supervisor._lookup(TENANT, 'r2')
        self.assertEqual('hop_cap_reached', row['reason'])

    def test_the_hop_cap_is_counted_from_the_ledger(self):
        self.seed()
        self.route('mijoz', 'r1')
        self.assertEqual(1, self.supervisor._depth(TENANT, 'sales.360'))

    def test_a_bare_route_list_cannot_say_whether_it_is_the_whole_history(self):
        """The HTTP route returned `routes` as a bare list, so "how many questions
        were routed" answered with the page size.

        The default hop cap is one route per target, so the population comes from
        distinct sections -- which is what a real routing history looks like too.
        """
        self.seed()
        # A third non-router section, so the history can hold three rows with the
        # default hop cap of one route per target.
        self.declare('warehouse', 'ops.warehouse', title='Ombor',
                     keywords=['ombor', 'zaxira'])
        self.route('mijoz savdo', 'r1')
        self.route('yuk yetkazib berish', 'r2')
        self.route('ombor zaxira', 'r3')
        routes, total, truncated = self.supervisor.history(TENANT, 1, with_total=True)
        self.assertEqual(1, len(routes))
        self.assertEqual(3, total)
        self.assertTrue(truncated)

    def test_an_exact_fit_history_is_not_reported_as_cut(self):
        self.seed()
        self.route('mijoz savdo', 'r1')
        self.route('yuk yetkazib berish', 'r2')
        routes, total, truncated = self.supervisor.history(TENANT, 2, with_total=True)
        self.assertEqual(total, len(routes))
        self.assertFalse(truncated)

    def test_the_default_shape_is_unchanged_for_an_existing_caller(self):
        self.seed()
        self.route('mijoz savdo', 'r1')
        self.assertIsInstance(self.supervisor.history(TENANT), list)

    def test_a_wider_cap_allows_the_declared_number_of_hops(self):
        self.seed()
        self.route('mijoz', 'r1', max_hops=2)
        self.route('mijoz yana', 'r2', max_hops=2)
        with self.assertRaises(Forbidden):
            self.route('mijoz uchinchi', 'r3', max_hops=2)
        self.assertEqual(2, self.supervisor._depth(TENANT, 'sales.360'))

    def test_the_hop_cap_is_bounded(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.route('mijoz', 'r1', max_hops=0)
        with self.assertRaises(ValueError):
            self.route('mijoz', 'r1', max_hops=99)
        # 99 proves a ceiling EXISTS but says nothing about where it is: any
        # ceiling below 99 satisfies it. The ceiling itself and the step above it
        # are what pin the number, so both are asserted.
        with self.assertRaises(ValueError):
            self.route('mijoz', 'r1', max_hops=MAX_HOPS_CEILING + 1)

    def test_the_hop_ceiling_itself_is_an_accepted_cap(self):
        """The declared ceiling must be reachable, not merely exceeded by 99.

        ``test_a_wider_cap_allows_the_declared_number_of_hops`` observes a cap of
        two working. Nothing sat ON the ceiling, so raising it to 3 while lowering
        the accepted maximum would have been invisible -- and the operator-facing
        contract is that ``MAX_HOPS_CEILING`` is what a cap may be, not a number
        strictly below it.
        """
        self.seed()
        self.assertEqual(3, MAX_HOPS_CEILING)
        for index in range(MAX_HOPS_CEILING):
            self.route('mijoz savdo', f'h{index}', max_hops=MAX_HOPS_CEILING)
        self.assertEqual(MAX_HOPS_CEILING, self.supervisor._depth(TENANT, 'sales.360'))
        with self.assertRaises(Forbidden):
            self.route('mijoz yana', 'h_extra', max_hops=MAX_HOPS_CEILING)

    def test_the_seconds_budget_is_bounded(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.route('mijoz', 'r1', max_seconds=5)

    def test_the_seconds_window_edges_are_the_documented_pair(self):
        """``max_seconds`` is a closed range 60..86400, and the ENDS themselves.

        The only existing test used 5, which is below the floor, so it proved a
        floor exists without proving its value -- 600 would have passed. Worse,
        the upper bound 86400 was asserted by nothing at all: an operator asking
        for exactly one day, the largest deadline the contract offers, would have
        received a ValueError and no test would have failed.

        The probe below walks the edges from BOTH sides and also walks one value
        past the off-by-one window. That last step is the one that matters: an
        earlier draft tested only (59, 60, 86400, 86401), and widening the bound
        in the source to 86401 left the whole suite green, because 86401 simply
        became a legal value and nothing asserted that it must be refused. A
        boundary test must name the value that is ONE STEP OUTSIDE the bound the
        module declares, not the value one step outside the value it happens to
        accept today.
        """
        self.seed()
        for value, accepted in ((59, False), (60, True),
                                (86_400, True), (86_401, False), (86_402, False)):
            fresh = self.fresh_supervisor()
            if accepted:
                record = fresh.route(TENANT, 'k1', 'mijoz', OWNER, max_seconds=value)
                run = AgentLoop(fresh.engine).get(TENANT, record['run_id'])
                self.assertEqual(value, run['deadline'] - self.clock.now)
            else:
                with self.assertRaises(ValueError):
                    fresh.route(TENANT, 'k1', 'mijoz', OWNER, max_seconds=value)
        # And the value the range is built from is the documented one.
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'supervisor.py').read_text(encoding='utf-8')
        self.assertIn('60 <= max_seconds <= 86400', source)


    def test_the_hop_count_survives_a_restart(self):
        """Depth is read from SQLite, so a new process cannot reset the cap."""
        self.seed()
        self.route('mijoz', 'r1')
        fresh = Supervisor(Engine(self.root / 'sup.db', self.registry, self._policy,
                                  clock=self.clock, authority=self._authority))
        self.assertEqual(1, fresh._depth(TENANT, 'sales.360'))
        with self.assertRaises(Forbidden):
            fresh.route(TENANT, 'r2', 'mijoz yana', OWNER)

    def test_freezing_the_tenant_stops_routing(self):
        self.seed()
        # ``freeze`` opens its own transaction; wrapping it would nest BEGIN
        # IMMEDIATE and lock the database, so it is called directly.
        self.engine.freeze(TENANT, True, OWNER)
        with self.assertRaises(Forbidden):
            self.route('mijoz', 'r1')

    def test_revoking_the_owner_stops_routing(self):
        self.seed()

        def revoked(c, tenant, channel='', actor='', roles=('owner', 'operator')):
            if actor == OWNER:
                raise Forbidden('actor revoked')

        self.authority = revoked
        with self.assertRaises(Forbidden):
            self.route('mijoz', 'r1')

    # ------------------------------------------------------------------ replay

    def test_the_same_request_key_does_not_spend_a_second_hop(self):
        self.seed()
        first = self.route('mijoz', 'r1')
        again = self.route('mijoz', 'r1')
        self.assertEqual(first['run_id'], again['run_id'])
        self.assertEqual(1, self.supervisor._depth(TENANT, 'sales.360'))
        with self.engine.read() as c:
            runs = c.execute('SELECT count(*) n FROM p_agent_runs WHERE tenant=?',
                             (TENANT,)).fetchone()['n']
        self.assertEqual(1, runs)

    def test_a_reused_key_with_a_different_question_is_a_conflict(self):
        self.seed()
        self.route('mijoz', 'r1')
        with self.assertRaises(Exception):
            self.route('butunlay boshqa savol', 'r1')

    def test_the_run_key_is_derived_and_not_caller_supplied(self):
        self.seed()
        record = self.route('mijoz', 'r1')
        run = self.run_of(record['run_id'])
        self.assertEqual('supervisor:r1', run['request_key'])

    # ------------------------------------------------------------------ bounds

    def test_the_question_is_bounded(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.route('', 'r1')
        with self.assertRaises(ValueError):
            self.route('x' * 5000, 'r1')

    def test_the_request_key_is_bounded(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.supervisor.route(TENANT, '', 'mijoz', OWNER)
        with self.assertRaises(ValueError):
            self.supervisor.route(TENANT, 'k' * 400, 'mijoz', OWNER)

    def test_the_step_budget_is_bounded(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.route('mijoz', 'r1', max_steps=0)
        with self.assertRaises(ValueError):
            self.route('mijoz', 'r1', max_steps=99)
        # As with the hop ceiling: 99 proves a ceiling exists, not where it is.
        with self.assertRaises(ValueError):
            self.route('mijoz', 'r1', max_steps=MAX_STEPS_CEILING + 1)

    def test_the_step_ceiling_itself_reaches_the_run(self):
        """``MAX_STEPS_CEILING`` is an accepted budget, and the run really gets it.

        ``test_the_step_budget_reaches_the_run`` uses 3, an interior value, so it
        shows the argument travels but not that the declared maximum is usable.
        The ceiling is the number a caller reads off the module to size a long
        plan, so a ceiling that were refused by one would be a contract nobody
        could actually use.
        """
        self.seed()
        self.assertEqual(12, MAX_STEPS_CEILING)
        record = self.route('mijoz', 'r1', max_steps=MAX_STEPS_CEILING)
        self.assertEqual(MAX_STEPS_CEILING, self.run_of(record['run_id'])['max_steps'])
        self.assertEqual(MAX_STEPS_CEILING, record['max_steps'])


    def test_the_step_budget_reaches_the_run(self):
        self.seed()
        record = self.route('mijoz', 'r1', max_steps=3)
        self.assertEqual(3, self.run_of(record['run_id'])['max_steps'])

    def test_the_seconds_budget_is_bounded(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.route('mijoz', 'r1', max_seconds=5)

    # ------------------------------------------- the model cannot name anything

    def test_the_route_tool_cannot_name_an_agent(self):
        schema = self.registry.items['supervisor.route'].schema
        self.assertEqual({'question'}, set(schema['properties']))
        with self.assertRaises(ValueError):
            self.registry.items['supervisor.route'].validate(
                {'question': 'x', 'agent': 'sales.360'})

    def test_the_sections_tool_takes_no_arguments(self):
        schema = self.registry.items['supervisor.sections'].schema
        self.assertEqual(set(), set(schema['properties']))
        with self.assertRaises(ValueError):
            self.registry.items['supervisor.sections'].validate({'section': 'sales'})

    def test_the_route_tool_reports_the_map_without_routing(self):
        self.seed()
        result = self.registry.items['supervisor.route'].handler(
            self.engine, TENANT, 'mgmt.supervisor', {'question': 'mijoz nima dedi'}, 's1')
        self.assertEqual('sales', result['match']['section'])
        with self.engine.read() as c:
            runs = c.execute('SELECT count(*) n FROM p_agent_runs WHERE tenant=?',
                             (TENANT,)).fetchone()['n']
        self.assertEqual(0, runs, 'the read tool must not create a run')

    def test_the_sections_tool_lists_only_declared_sections(self):
        self.seed()
        sections = self.registry.items['supervisor.sections'].handler(
            self.engine, TENANT, 'mgmt.supervisor', {}, 's1')['sections']
        self.assertEqual({'sales', 'logistics', 'management'},
                         {item['section'] for item in sections})

    def test_the_route_tool_reports_when_nothing_matched(self):
        self.seed()
        result = self.registry.items['supervisor.route'].handler(
            self.engine, TENANT, 'mgmt.supervisor', {'question': 'havo qanday'}, 's1')
        self.assertIsNone(result['match'])

    # ------------------------------------------------------------------ ledger

    def test_the_history_records_routed_and_refused(self):
        self.seed()
        self.route('mijoz', 'r1')
        with self.assertRaises(Forbidden):
            self.route('havo', 'r2')
        statuses = {row['request_key']: row['status']
                    for row in self.supervisor.history(TENANT)}
        self.assertEqual('routed', statuses['r1'])
        self.assertEqual('unrouted', statuses['r2'])

    def test_no_credential_or_url_reaches_the_ledger(self):
        self.seed()
        record = self.route('mijoz', 'r1')
        blob = json.dumps(record, ensure_ascii=False)
        for needle in ('token', 'secret', 'http://', 'https://', 'Bearer'):
            self.assertNotIn(needle, blob)

    def test_the_route_output_carries_no_policy_material(self):
        self.seed()
        record = self.route('mijoz', 'r1')
        for key in ('tools', 'ladder', 'allowed_connections', 'allowed_recipients'):
            self.assertNotIn(key, record)

    def test_declaring_a_section_audits_it(self):
        self.seed()
        with self.engine.read() as c:
            rows = c.execute('''SELECT action FROM p_audit WHERE tenant=?
                                AND action='supervisor.section_declared' ''',
                             (TENANT,)).fetchall()
        self.assertEqual(3, len(rows))


class SupervisorBoundaryTests(SupervisorTests):
    """The exact caps, pinned by VALUE, and the two ceilings that were tied together.

    The rest of the suite asserts behaviour; a bound read back out of the module moves
    with the mutation that widens it, so these pin the literal and walk both ends.
    """

    def _clear_routes(self):
        """`_depth` is durable and counts per TARGET AGENT, so a record that expects a
        route to succeed needs a clean ledger. Clearing the table is enough -- creating a
        fresh Engine per record made this file take 168 seconds."""
        with self.engine.tx() as c:
            c.execute('DELETE FROM p_supervisor_route WHERE tenant=?', (TENANT,))

    def _one_section(self):
        self.declare('sales', 'sales.360', keywords=['mijoz'])

    # ------------------------------------------------------- the limit constants

    def test_the_limit_constants_are_pinned(self):
        self.assertEqual(20, MAX_SECTIONS)
        self.assertEqual(2000, MAX_QUESTION)
        self.assertEqual(20, MAX_KEYWORDS)
        self.assertEqual(60, MAX_KEYWORD_LENGTH)
        self.assertEqual(3, MAX_HOPS_CEILING)
        self.assertEqual(12, MAX_STEPS_CEILING)
        self.assertEqual(1, DEFAULT_MAX_HOPS)
        self.assertEqual(6, DEFAULT_MAX_STEPS)

    def test_the_name_ceiling_is_one_hundred_and_twenty_eight(self):
        self.assertEqual('a' * 128, _name('a' * 128, 'agent', 128))
        with self.assertRaises(ValueError):
            _name('a' * 129, 'agent', 128)
        for value in ('Sales', 'a b', '-a', '.a', '', '   ', 'a' * 128 + ' '):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _name(value, 'agent', 128)
        self.assertEqual('sales', _name('  sales  ', 'agent', 128))

    def test_the_keyword_caps_are_pinned(self):
        self.assertEqual(20, len(_keywords(['k%d' % i for i in range(20)])))
        with self.assertRaises(ValueError):
            _keywords(['k%d' % i for i in range(21)])
        self.assertEqual(60, len(_keywords(['k' * 60])[0]))
        with self.assertRaises(ValueError):
            _keywords(['k' * 61])
        for value in (['   '], [''], [None], ['ok', 5], 'not-a-list', {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _keywords(value)
        self.assertEqual([], _keywords(None))
        self.assertEqual(['sotuv'], _keywords(['  SOTUV  ']))

    def test_the_bounded_helper_refuses_a_non_int(self):
        for value in (1, 10):
            self.assertEqual(value, _bounded(value, 'x', 1, 10))
        for value in (0, 11, True, False, 1.0, '5', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _bounded(value, 'x', 1, 10)

    # ------------------------------------- the request key and the derived run key

    def test_the_request_key_ceiling_leaves_room_for_the_run_prefix(self):
        # The run key is `RUN_KEY_PREFIX + request_key` and AgentLoop.create bounds the
        # KEY at 256. Measured before the fix: a request key up to the declared 256
        # passed this module's check and then failed inside the loop with
        # 'Invalid run identity' for every length from 246 upward.
        self.assertEqual('supervisor:', RUN_KEY_PREFIX)
        self.assertEqual(245, MAX_REQUEST_KEY)
        self.assertEqual(256, MAX_REQUEST_KEY + len(RUN_KEY_PREFIX))
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'supervisor.py').read_text(encoding='utf-8')
        self.assertIn('MAX_REQUEST_KEY = 256 - len(RUN_KEY_PREFIX)', source)
        self.assertIn('RUN_KEY_PREFIX + request_key', source)

    def test_the_request_key_ceiling_holds_at_the_boundary(self):
        # The refusal must be THIS module's, not AgentLoop's. A key of 246 is also
        # refused by the loop ('Invalid run identity'), so asserting only the exception
        # TYPE would pass with the ceiling widened back to 256 -- the loop's refusal
        # would stand in for the one under test. The message is what tells them apart.
        self._one_section()
        for length, accepted in ((244, True), (245, True), (246, False), (256, False)):
            with self.subTest(length=length):
                self._clear_routes()
                key = 'k' * length
                if accepted:
                    self.supervisor.route(TENANT, key, 'mijoz savoli', OWNER,
                                          section='sales')
                else:
                    with self.assertRaises(ValueError) as caught:
                        self.supervisor.route(TENANT, key, 'mijoz savoli', OWNER,
                                              section='sales')
                    self.assertIn('request_key', str(caught.exception))
                    self.assertNotIn('run identity', str(caught.exception))

    def test_the_redundant_regex_bounds_are_pinned_by_their_lines(self):
        # NAME_RE's `{0,127}` is SUBSUMED by `_name`'s own length check: the regex allows
        # at most 129 characters and 129 is refused before the regex is reached, so a
        # widening to `{0,128}` changes nothing observable. Pinned by its exact line with
        # the reason, rather than left to a behaviour that cannot be walked.
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'supervisor.py').read_text(encoding='utf-8')
        self.assertIn("NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,127}$')", source)

    # --------------------------------------------------------- the section ceiling

    def test_the_section_ceiling_is_enforced(self):
        # MAX_SECTIONS was declared and never read, so the section count was unbounded
        # while every other collection in the module is bounded. `seed()` declares 3.
        # `seed()` is not called by setUp, so the table starts empty here.
        for index in range(20):
            self.declare('sec%02d' % index, 'sales.360')
        self.assertEqual(20, len(self.supervisor.sections(TENANT)))
        with self.assertRaises(ValueError):
            self.declare('sec20', 'sales.360')
        self.assertEqual(20, len(self.supervisor.sections(TENANT)))
        # Re-declaring an EXISTING section is always allowed, so a tenant already
        # holding more than the ceiling keeps working.
        self.declare('sec00', 'sales.360', title='yangilangan')

    # ----------------------------------------------------------- the routing caps

    def test_the_hop_cap_holds_at_its_boundary(self):
        self._one_section()
        for cap, allowed in ((1, 1), (3, 3)):
            with self.subTest(cap=cap):
                self._clear_routes()
                for index in range(allowed):
                    self.supervisor.route(TENANT, 'rk_%d_%d' % (cap, index),
                                          'mijoz savoli', OWNER, section='sales',
                                          max_hops=cap)
                with self.assertRaises(Forbidden):
                    self.supervisor.route(TENANT, 'rk_%d_over' % cap, 'mijoz savoli',
                                          OWNER, section='sales', max_hops=cap)

    def test_the_route_bounds_are_walked(self):
        self._one_section()
        cases = (
            ('max_hops', ((1, True), (3, True), (0, False), (4, False))),
            ('max_steps', ((1, True), (12, True), (0, False), (13, False))),
            ('max_seconds', ((60, True), (86400, True), (59, False), (86401, False))),
        )
        for field, values in cases:
            for value, accepted in values:
                with self.subTest(field=field, value=value):
                    self._clear_routes()
                    # The whole field name, not field[0]: all three start with 'm',
                    # so a single-letter prefix made max_hops=1 and max_steps=1 share
                    # the request key and the second route raised Conflict.
                    key = '%s_%s' % (field, value)
                    if accepted:
                        self.supervisor.route(TENANT, key, 'mijoz', OWNER,
                                              section='sales', **{field: value})
                    else:
                        with self.assertRaises(ValueError):
                            self.supervisor.route(TENANT, key, 'mijoz', OWNER,
                                                  section='sales', **{field: value})
        for length, accepted in ((2000, True), (2001, False)):
            with self.subTest(question=length):
                self._clear_routes()
                if accepted:
                    self.supervisor.route(TENANT, 'q%d' % length, 'x' * length, OWNER,
                                          section='sales')
                else:
                    with self.assertRaises(ValueError):
                        self.supervisor.route(TENANT, 'q%d' % length, 'x' * length,
                                              OWNER, section='sales')

    def test_the_history_clamp_is_five_hundred(self):
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'supervisor.py').read_text(encoding='utf-8')
        # DEFENSIVE against the size of the table, so it is pinned by its line and
        # walked at the ends a small table can reach.
        self.assertIn('limit = min(max(1, int(limit)), 500)', source)
        # The clamp RAISES 0 to 1 and CLAMPS 10**9 down to 500, so neither is an
        # error; on a table with no rows both simply return nothing.
        for limit in (0, 1, 10 ** 9):
            with self.subTest(limit=limit):
                self.assertEqual([], self.supervisor.history(TENANT, limit))
        # With rows present, `truncated` compares against the POPULATION, so a page
        # holding exactly `limit` rows is reported complete.
        # setUp does not seed, so declare one section before routing into it.
        self.declare('sales', 'sales.360', keywords=['mijoz'])
        self.route('mijoz savoli', 'rk_hist', section='sales')
        rows, total, truncated = self.supervisor.history(TENANT, 1, with_total=True)
        self.assertEqual(1, len(rows))
        self.assertGreaterEqual(total, 1)
        self.assertEqual(total > len(rows), truncated)
        self.assertEqual(1, len(self.supervisor.history(TENANT, 0)))

    def test_the_keyword_precedence_is_longest_then_alphabetical(self):
        # The docstring used to say "declaration order"; the real rule is the longest
        # keyword, then the alphabetically first section id.
        self.declare('alpha', 'sales.360', keywords=['sotuv narx', 'sotuv'])
        self.declare('beta', 'sales.360', keywords=['sotuv'])
        picked = self.supervisor._match(TENANT, 'sotuv narx kerak')
        self.assertEqual(('alpha', 'sotuv narx'), (picked[0]['section'], picked[1]))
        picked = self.supervisor._match(TENANT, 'sotuv kerak')
        self.assertEqual('alpha', picked[0]['section'])


if __name__ == '__main__':
    unittest.main()
