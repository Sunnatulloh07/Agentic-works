"""Prove that routing does not transfer authority, adversarially.

The claim in the module docstring is: "a supervisor that cannot read the CRM
cannot cause the CRM to be read through a subordinate." A docstring is not
evidence, so this probe constructs the sharpest case it can and measures the
outcome.

Setup: a supervisor holding no data tool at all routes a question to a section
agent holding ``connectors.read``. Two things are measured:

1. the run that is created carries the *target's* policy, not the supervisor's;
2. the supervisor's own policy is byte-identical before and after routing.

Then the reverse case, which is the one that actually matters at runtime: the
supervisor is given a tool the target lacks, and the target attempts to use it.
It must be refused, because the target's limits bind — not the supervisor's.

Run from anywhere: the path is derived from ``__file__``, not the working
directory.
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'api-python'))

from platform_runtime.agent_loop import AgentLoop
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.supervisor import Supervisor, register_supervisor_tools
from platform_runtime.tools import build_registry

TENANT = 't_probe'
OWNER = 'usr_owner'

POLICIES = {
    'mgmt.supervisor': {'tools': ['supervisor.route', 'supervisor.sections'],
                        'ladder': 'human_assisted', 'allowed_connections': []},
    'sales.360': {'tools': ['connectors.read', 'agent.activity'],
                  'ladder': 'human_assisted', 'allowed_connections': ['amocrm']},
}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        registry = build_registry()
        # The supervisor tools are part of the production registry, so only add
        # them when they are genuinely missing.
        if 'supervisor.route' not in registry.items:
            register_supervisor_tools(registry)
        engine = Engine(root / 'probe.db', registry,
                        lambda t, a: dict(POLICIES[a]), clock=lambda: 1_770_000_000.0)
        sup = Supervisor(engine)
        sup.declare(TENANT, 'sales', 'sales.360', OWNER, keywords=['mijoz'])

        before = dict(engine.policy(TENANT, 'mgmt.supervisor'))
        record = sup.route(TENANT, 'p1', 'mijoz bilan nima bo\'ldi', OWNER)
        run = AgentLoop(engine).get(TENANT, record['run_id'])
        after = dict(engine.policy(TENANT, 'mgmt.supervisor'))
        target = dict(engine.policy(TENANT, run['agent']))

        print('supervisor tools      :', before['tools'])
        print('routed to agent       :', run['agent'])
        print('target tools          :', target['tools'])
        print('supervisor tools after:', after['tools'])
        print('supervisor connections:', before['allowed_connections'], '->',
              after['allowed_connections'])

        assert run['agent'] == 'sales.360', 'the run must be for the target'
        assert before['tools'] == after['tools'], 'routing widened the supervisor'
        assert before['allowed_connections'] == after['allowed_connections'], \
            'routing widened the supervisor connections'
        assert 'connectors.read' in target['tools'], 'target kept its own tool'
        assert 'connectors.read' not in after['tools'], \
            'the supervisor must NOT have acquired the target tool'

        # Reverse case: a tool the target lacks must be refused for the target,
        # no matter what the supervisor holds.
        POLICIES['mgmt.supervisor']['tools'].append('telegram.send')
        refused = False
        try:
            engine.submit(TENANT, 'agent', 'probe:1', 'sales.360',
                          [{'tool': 'telegram.send',
                            'args': {'conversation_id': '1', 'text': 'x'}}], OWNER)
        except Forbidden:
            refused = True
        print('target using a tool it lacks -> refused:', refused)
        assert refused, 'the target must not inherit the supervisor tool'

        print()
        print('PROVEN: authority flows to the executor, not to the asker')


if __name__ == '__main__':
    main()
