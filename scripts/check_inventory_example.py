"""Validate the shipped inventory example against the module's own validator.

Run from ``api-python`` (or the repository root with the path below). This is the
check that keeps the example honest: an example whose shape the validator would
reject teaches operators a configuration that fails at runtime, which is the
documentation drift this repository treats as a defect.

The example is checked against the **module's own** rules rather than a copy of
them. Where the shipped example and the code disagree, the code wins and this
script fails -- which is how the ``oee`` block's contradiction was caught.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'api-python'))

import platform_runtime.business_graph as business_graph  # noqa: E402
import platform_runtime.inventory as inventory            # noqa: E402
from platform_runtime import tools                        # noqa: E402

PATH = os.path.join(HERE, '..', 'config', 'inventory.example.json')

with open(PATH, encoding='utf-8') as handle:
    example = json.load(handle)

# Comment keys are documentation, not configuration.
blocks = {tenant: value for tenant, value in example.items()
          if not tenant.startswith('_')}
assert blocks, 'the example declares no tenant'

original = tools.config


def install(source):
    """Point every module that bound ``config`` at import time at ``source``.

    ``business_graph`` and ``inventory`` both do ``from .tools import config``, so
    patching only ``tools.config`` would leave the old binding in place and a
    negative check would pass for the wrong reason -- it would be measuring a
    stale function rather than the validator.
    """
    lookups = source if callable(source) else (lambda t: source.get(t, {}))
    tools.config = lookups
    inventory.config = lookups
    business_graph.config = lookups


install(blocks)
try:
    for tenant, payload in sorted(blocks.items()):
        assert 'inventory' in payload, f'{tenant} has no inventory block'
        declared = inventory.inventory_config(tenant)
        assert declared['entity'], f'{tenant} declares no entity'
        assert declared['identity'], f'{tenant} declares no identity'
        assert declared['attributes'], f'{tenant} declares no attribute role'

        for role, attribute in sorted(declared['attributes'].items()):
            assert role in inventory.ATTRIBUTE_ROLES, \
                f'{tenant} uses an undeclared role {role!r}'
            print(f'{tenant}.inventory: {role} -> {attribute!r}')

        # The graph block the example ships alongside must itself validate, and
        # its declared priority must honour the PRD 6.3 shape.
        graph_block = payload.get('business_graph', {})
        if graph_block:
            declared_graph = business_graph.graph_config(tenant)
            product = declared_graph['entities'].get(declared['entity'])
            assert product is not None, \
                f'{tenant} names entity {declared["entity"]!r} the graph does not declare'
            priority = declared_graph['source_priority']
            assert priority, f'{tenant} declares no source_priority'
            for attribute, order in sorted(priority.items()):
                assert order, f'{tenant}.source_priority.{attribute} is empty'
                # Every source a priority names must exist on the entity, or the
                # declaration is a silent no-op -- which is exactly the defect the
                # source_priority fix closed.
                for source_name in order:
                    assert source_name in product['sources'], \
                        f'{tenant} priority for {attribute!r} names {source_name!r}, ' \
                        f'which the entity does not declare'
            # Every role the inventory block maps must be an attribute the graph
            # actually resolves, or the read returns an empty figure forever.
            for role, attribute in sorted(declared['attributes'].items()):
                if attribute == declared['identity']:
                    continue
                assert attribute in product['attributes'], \
                    f'{tenant}.inventory maps role {role!r} to attribute ' \
                    f'{attribute!r}, which no graph source produces'
            print(f'{tenant}.business_graph: priority='
                  f'{ {k: v for k, v in sorted(priority.items())} }')

    # A knob the validator must refuse, to prove the validator is running here and
    # the example is not merely passing by not being checked.
    tenant = sorted(blocks)[0]

    broken = json.loads(json.dumps(blocks))
    broken[tenant]['inventory']['attributes']['pric'] = 'price'
    install(broken)
    refused = False
    try:
        inventory.inventory_config(tenant)
    except ValueError:
        refused = True
    assert refused, 'a mistyped role must be refused by the validator'

    # A priority naming a source the entity does not have: the silent no-op.
    broken = json.loads(json.dumps(blocks))
    broken[tenant]['business_graph']['source_priority']['price'] = {
        'primary': 'ghost_system'}
    install(broken)
    refused = False
    try:
        business_graph.graph_config(tenant)
    except ValueError:
        refused = True
    assert refused, 'a priority naming an undeclared source must be refused'

    # An inventory block with no entity at all.
    broken = json.loads(json.dumps(blocks))
    broken[tenant]['inventory'].pop('entity')
    install(broken)
    declared = inventory.inventory_config(tenant)
    assert not declared['entity'], 'an absent entity must read as unconfigured'
finally:
    tools.config = original
    inventory.config = original
    business_graph.config = original

print('out-of-shape blocks -> refused')
print('OK: example passes the module validator')
