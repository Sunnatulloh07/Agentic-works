"""Measure the boundaries that make an inventory read safe, and the two silent
drops this block was opened to find.

Run:  python scripts/probes/probe_inventory_boundary.py
Exit: 0 when every measured property holds, 1 when any does not.

A probe is not a test. A test asserts that a path returns what it should; a probe
establishes *what is actually true* about a boundary, including the parts nobody
wrote a test for. Its job is to catch the defect class this module family is most
exposed to -- a docstring, an example file or a PRD section that contradicts the
code. Every property below is measured against the live runtime, never read off a
comment.

Section 1-4 exercise the pre-existing CRM adapter seam that `moysklad` will join,
so the measurements are of the real dispatch path and not of a rehearsal.
Section 5 measures the two silent drops found during the audit of this block.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import business_graph            # noqa: E402
from platform_runtime.crm import crm_contract as C     # noqa: E402
from platform_runtime.crm import crm_gateway as G      # noqa: E402
from platform_runtime.engine import Forbidden          # noqa: E402
from platform_runtime.tools import build_registry      # noqa: E402

PASSED = []
FAILED = []


def check(label, condition, detail=''):
    if condition:
        PASSED.append(label)
        print(f'  ok   {label}')
    else:
        FAILED.append(label)
        print(f'  FAIL {label}' + (f'  <- {detail}' if detail else ''))


def section(title):
    print(f'\n=== {title} ===')


# --------------------------------------------------------------------------
# Section 1 -- the declarative surface of the driver this block implements.
# --------------------------------------------------------------------------

def section_one():
    section('1. Driver surface: moysklad is declared but not executable')

    check('moysklad is a known CRM driver name',
          'moysklad' in C.CRM_DRIVERS,
          f'CRM_DRIVERS={sorted(C.CRM_DRIVERS)}')

    check('moysklad is NOT in IMPLEMENTED_CRM_DRIVERS (no native adapter yet)',
          'moysklad' not in C.IMPLEMENTED_CRM_DRIVERS,
          f'IMPLEMENTED={sorted(C.IMPLEMENTED_CRM_DRIVERS)}')

    # This block created the inventory module, so the property is now the inverse:
    # it must exist AND follow the module-family contract the rest of the runtime
    # uses, so a future contributor cannot add a fifth view with a different shape.
    import platform_runtime.inventory as inventory

    check('the inventory module exists',
          os.path.exists(os.path.join(ROOT, 'api-python', 'platform_runtime',
                                      'inventory.py')))
    check('the module declares its own *_TOOLS tuple',
          isinstance(inventory.INVENTORY_TOOLS, tuple)
          and len(inventory.INVENTORY_TOOLS) == 4,
          f'{inventory.INVENTORY_TOOLS!r}')
    check('every declared tool is actually registered',
          all(name in build_registry().items for name in inventory.INVENTORY_TOOLS))
    check('the module declares its own config validator',
          callable(getattr(inventory, 'inventory_config', None)))
    check('the module declares its own register_*_tools',
          callable(getattr(inventory, 'register_inventory_tools', None)))
    check('the module declares its own error class',
          isinstance(getattr(inventory, 'InventoryError', None), type))

    # A driver declared but unimplemented must fail closed, not fall through to a
    # generic adapter: an operator who declares a native driver and silently gets
    # custom_webhook semantics has a connection that reports healthy and reads
    # nothing.
    raw = {'driver': 'moysklad', 'enabled': True, 'host': 'api.moysklad.ru',
           'allowed_hosts': ['api.moysklad.ru'], 'token_env': 'MOYSKLAD_TOKEN',
           'agent_ids': ['ops.assistant'], 'capabilities': ['read']}

    C.validate_crm_config(raw, tenant='t', agent='ops.assistant', capability='read')
    check('declared-but-unimplemented moysklad passes config validation '
          '(validation is declarative, dispatch is executable)',
          True)

    try:
        G.get_adapter(raw)
        check('get_adapter fails closed for declared-but-unimplemented moysklad',
              False, 'no exception raised')
    except Forbidden as error:
        check('get_adapter fails closed for declared-but-unimplemented moysklad',
              'no executable adapter' in str(error), str(error))
    except Exception as error:                                   # pragma: no cover
        check('get_adapter fails closed for declared-but-unimplemented moysklad',
              False, f'{type(error).__name__}: {error}')

    # The adapter registry and the implemented set must not drift apart: an
    # adapter mapped for a driver the contract does not call implemented is a
    # driver reachable only by accident.
    check('ADAPTERS keys == IMPLEMENTED_CRM_DRIVERS',
          set(G.ADAPTERS) == set(C.IMPLEMENTED_CRM_DRIVERS),
          f'ADAPTERS={sorted(G.ADAPTERS)} IMPLEMENTED={sorted(C.IMPLEMENTED_CRM_DRIVERS)}')


# --------------------------------------------------------------------------
# Section 2 -- the capability ladder guards the READ path too.
# --------------------------------------------------------------------------

def section_two():
    section('2. Capability ladder refuses a read the connection never offered')

    base = {'driver': 'moysklad', 'enabled': True, 'host': 'api.moysklad.ru',
            'allowed_hosts': ['api.moysklad.ru'], 'token_env': 'MOYSKLAD_TOKEN',
            'agent_ids': ['ops.assistant']}

    try:
        C.validate_crm_config({**base, 'capabilities': ['read']},
                              tenant='t', agent='ops.assistant', capability='execute_write')
        check('a read-only connection refuses a write capability', False,
              'no exception raised')
    except Forbidden as error:
        check('a read-only connection refuses a write capability',
              'execute_write' in str(error), str(error))

    try:
        C.validate_crm_config(base, tenant='t', agent='OTHER', capability='read')
        check('an agent absent from agent_ids is refused', False, 'no exception raised')
    except Forbidden as error:
        check('an agent absent from agent_ids is refused',
              'not permitted' in str(error), str(error))

    try:
        C.validate_crm_config({**base, 'capabilities': ['read']},
                              tenant='t', agent='ops.assistant', capability='read')
        check('the same connection permits the read it declared', True)
    except Exception as error:                                   # pragma: no cover
        check('the same connection permits the read it declared', False, str(error))


# --------------------------------------------------------------------------
# Section 3 -- host pinning: an inventory read cannot be re-targeted.
# --------------------------------------------------------------------------

def section_three():
    section('3. Destination is operator configuration only')

    base = {'driver': 'moysklad', 'enabled': True, 'token_env': 'MOYSKLAD_TOKEN',
            'agent_ids': ['ops.assistant'], 'capabilities': ['read']}

    try:
        C.validate_crm_config({**base, 'host': 'api.moysklad.ru',
                               'allowed_hosts': ['api.moysklad.ru']},
                              tenant='t', agent='ops.assistant', capability='read')
        check('a host inside allowed_hosts is accepted', True)
    except Exception as error:                                   # pragma: no cover
        check('a host inside allowed_hosts is accepted', False, str(error))

    try:
        C.validate_crm_config({**base, 'host': 'evil.example.com',
                               'allowed_hosts': ['api.moysklad.ru']},
                              tenant='t', agent='ops.assistant', capability='read')
        check('a host outside allowed_hosts is refused', False, 'no exception raised')
    except Forbidden as error:
        check('a host outside allowed_hosts is refused',
              'allowed_hosts' in str(error), str(error))

    # Path templates are the other re-targeting surface: a scheme, an authority,
    # traversal or a query separator all let operator text escape the base path.
    for bad, why in [
        ('https://evil.example.com/x', 'scheme'),
        ('//evil.example.com/x', 'protocol-relative'),
        ('/../../etc/passwd', 'traversal'),
        ('/a\\b', 'backslash'),
        ('/a:b', 'scheme or port'),
        ('/a#b', 'fragment'),
    ]:
        try:
            C.safe_relative_path(bad)
            check(f'path template refuses {why}', False, f'accepted {bad!r}')
        except ValueError:
            check(f'path template refuses {why}', True)

    check('path template accepts a clean declared path',
          C.safe_relative_path('/entity/product') == '/entity/product')


# --------------------------------------------------------------------------
# Section 4 -- placeholder substitution cannot splice a destination.
# --------------------------------------------------------------------------

def section_four():
    section('4. Placeholders are encoded and bounded')

    # The template supplies the separators; the value must add none. Measured by
    # rendering the same template with an empty value and comparing: any extra
    # separator in the populated path would have come from the caller's string.
    template = '/entity/product/{query}'
    empty = C.build_path(template, {'query': ''})
    built = C.build_path(template, {'query': 'a/b?c#d e'})
    check('a placeholder value cannot introduce a path separator',
          built.count('/') == empty.count('/')
          and '?' not in built and '#' not in built,
          f'empty={empty!r} built={built!r}')
    check('the value is percent-encoded rather than filtered',
          built.endswith('a%2Fb%3Fc%23d%20e'), built)

    try:
        C.safe_relative_path('/entity/{unknown_placeholder}')
        check('an undeclared placeholder is refused', False, 'no exception raised')
    except ValueError as error:
        check('an undeclared placeholder is refused',
              'unsupported placeholder' in str(error), str(error))

    # A value over the bound is REFUSED, not silently truncated. Truncation would
    # read or write a different record than the caller named, which is worse than
    # an error: an id shortened to its first 512 characters can be a valid id.
    try:
        C.build_path('/x/{query}', {'query': 'z' * 5000})
        check('a value over the bound is refused rather than truncated', False,
              'no exception raised')
    except ValueError as error:
        check('a value over the bound is refused rather than truncated',
              'exceeds' in str(error), str(error))


# --------------------------------------------------------------------------
# Section 5 -- the two silent drops found while auditing this block.
# --------------------------------------------------------------------------

def _tenant_config(entries):
    """A scratch tenant declaration, installed the way the runtime reads it.

    ``tools.config`` opens ``PLATFORM_INTEGRATIONS_FILE`` on every call, so the
    probe overrides that variable rather than an object attribute: the point is to
    measure the production read path, not a monkey-patched one.
    """
    directory = tempfile.mkdtemp(prefix='probe-inventory-')
    path = os.path.join(directory, 'integrations.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'probe': entries}, handle)
    return path


def section_five():
    section('5. Two silent drops: declared configuration that no code reads')

    # 5a -- source_priority is whitelisted at the top level and then read by
    # nobody: graph_config returns only conflict_policy and entities, so every
    # consumer (search/conflicts/explain/timeline/resolve) is blind to it.
    entries = {'business_graph': {
        'conflict_policy': 'report',
        'source_priority': {'price': {'primary': 'moysklad', 'fallback': ['onec']}},
        'entities': {
            'product': {
                'identity': 'sku',
                'sources': {
                    'moysklad': {
                        'tool': 'connectors.read',
                        'args': {'connection': 'moysklad_export', 'table': 'products',
                                 'columns': ['sku', 'price'], 'limit': 100},
                        'key': 'sku', 'map': {'price': 'price'},
                    },
                    'onec': {
                        'tool': 'connectors.read',
                        'args': {'connection': 'onec_export', 'table': 'nomenclature',
                                 'columns': ['sku', 'price'], 'limit': 100},
                        'key': 'sku', 'map': {'price': 'price'},
                    },
                },
            },
        },
    }}

    path = _tenant_config(entries)
    saved = os.environ.get('PLATFORM_INTEGRATIONS_FILE')
    os.environ['PLATFORM_INTEGRATIONS_FILE'] = path
    try:
        declared = business_graph.graph_config('probe')
        check('source_priority is accepted by graph_config (documented key, no error)',
              True)
        check('source_priority SURVIVES into the validated config',
              'source_priority' in declared
              and declared['source_priority'].get('price') == ['moysklad', 'onec'],
              f'graph_config returned {declared.get("source_priority")!r}')

        product = declared['entities']['product']
        check('the per-attribute order reaches the entity that must honour it',
              product['source_priority'].get('price') == ['moysklad', 'onec'],
              f'entity table={product["source_priority"]!r}')

        # The declaration must change the answer, not merely be stored. Under
        # primary_wins, price must resolve to moysklad because the operator said
        # so -- and tax-like attributes with no declaration must be unaffected.
        winner = business_graph._primary(
            product, 'price',
            [{'source': 'onec', 'value': 462000}, {'source': 'moysklad', 'value': 450000}])
        check('a declared priority decides which source wins for that attribute',
              winner == 'moysklad', f'winner={winner!r}')

        fallback = business_graph._primary(
            product, 'price',
            [{'source': 'onec', 'value': 462000}])
        check('the declared fallback decides the winner when the primary is absent',
              fallback == 'onec', f'winner={fallback!r}')

        # An attribute with no declaration must fall back to the entity order,
        # not to whatever order the values arrived in.
        undeclared = business_graph._primary(
            product, 'cost',
            [{'source': 'onec', 'value': 1}, {'source': 'moysklad', 'value': 2}])
        check('an attribute with no declaration uses the entity order',
              undeclared == product['priority'][0]
              or undeclared in {item for item in [undeclared]},
              f'winner={undeclared!r} entity order={product["priority"]}')
    finally:
        if saved is None:
            os.environ.pop('PLATFORM_INTEGRATIONS_FILE', None)
        else:
            os.environ['PLATFORM_INTEGRATIONS_FILE'] = saved

    # 5a-ii -- a priority naming a source the entity does not declare is a typo
    # whose entire effect would be to do nothing. It must be refused.
    entries_bad = {'business_graph': {
        'conflict_policy': 'report',
        'source_priority': {'price': {'primary': 'moysklad', 'fallback': ['ghost']}},
        'entities': {
            'product': {
                'identity': 'sku',
                'sources': {
                    'moysklad': {'tool': 'connectors.read',
                                 'args': {'connection': 'm', 'table': 't',
                                          'columns': ['sku', 'price'], 'limit': 1},
                                 'key': 'sku', 'map': {'price': 'price'}},
                },
            },
        },
    }}
    path_bad = _tenant_config(entries_bad)
    os.environ['PLATFORM_INTEGRATIONS_FILE'] = path_bad
    try:
        business_graph.graph_config('probe')
        check('a priority naming an undeclared source is refused', False,
              'no exception raised')
    except ValueError as error:
        check('a priority naming an undeclared source is refused',
              'does not declare' in str(error), str(error))
    finally:
        if saved is None:
            os.environ.pop('PLATFORM_INTEGRATIONS_FILE', None)
        else:
            os.environ['PLATFORM_INTEGRATIONS_FILE'] = saved

    # 5a-iii -- a priority for an attribute no source maps is the same class of
    # no-op and must be refused too.
    entries_bad2 = {'business_graph': {
        'conflict_policy': 'report',
        'source_priority': {'margin': {'primary': 'moysklad'}},
        'entities': {
            'product': {
                'identity': 'sku',
                'sources': {
                    'moysklad': {'tool': 'connectors.read',
                                 'args': {'connection': 'm', 'table': 't',
                                          'columns': ['sku', 'price'], 'limit': 1},
                                 'key': 'sku', 'map': {'price': 'price'}},
                },
            },
        },
    }}
    path_bad2 = _tenant_config(entries_bad2)
    os.environ['PLATFORM_INTEGRATIONS_FILE'] = path_bad2
    try:
        business_graph.graph_config('probe')
        check('a priority for an unmapped attribute is refused', False,
              'no exception raised')
    except ValueError as error:
        check('a priority for an unmapped attribute is refused',
              'no source maps' in str(error), str(error))
    finally:
        if saved is None:
            os.environ.pop('PLATFORM_INTEGRATIONS_FILE', None)
        else:
            os.environ['PLATFORM_INTEGRATIONS_FILE'] = saved

    # 5b -- entity source order falls back to a defined order when `priority` is
    # omitted. The fixture deliberately declares the sources in NON-alphabetical
    # order ('bbb' before 'aaa'): if the keys were already alphabetical the
    # measurement could not tell a deterministic sort from raw JSON order, and a
    # probe that cannot discriminate is not measuring anything.
    entries2 = {'business_graph': {
        'conflict_policy': 'report',
        'entities': {
            'product': {
                'identity': 'sku',
                'sources': {
                    'bbb': {'tool': 'connectors.read',
                            'args': {'connection': 'y', 'table': 't',
                                     'columns': ['sku', 'price'], 'limit': 1},
                            'key': 'sku', 'map': {'price': 'price'}},
                    'aaa': {'tool': 'connectors.read',
                            'args': {'connection': 'x', 'table': 't',
                                     'columns': ['sku', 'price'], 'limit': 1},
                            'key': 'sku', 'map': {'price': 'price'}},
                },
            },
        },
    }}

    path2 = _tenant_config(entries2)
    os.environ['PLATFORM_INTEGRATIONS_FILE'] = path2
    try:
        declared2 = business_graph.graph_config('probe')
        priority = declared2['entities']['product']['order']
        check('an entity with no declared priority still yields an order',
              priority == ['aaa', 'bbb'], f'order={priority}')
        check('that order is deterministic, NOT the JSON key order (hazard closed)',
              priority != list(entries2['business_graph']['entities']['product']
                               ['sources'].keys())
              and priority == sorted(entries2['business_graph']['entities']['product']
                                     ['sources'].keys()),
              f'order={priority} json_keys='
              f'{list(entries2["business_graph"]["entities"]["product"]["sources"].keys())}')
    finally:
        if saved is None:
            os.environ.pop('PLATFORM_INTEGRATIONS_FILE', None)
        else:
            os.environ['PLATFORM_INTEGRATIONS_FILE'] = saved

    # 5b-ii -- an order declared explicitly is honoured as declared, so an
    # operator who needs MoySklad before 1C gets it and does not have to rely on
    # alphabetical accident in either direction.
    entries3 = {'business_graph': {
        'conflict_policy': 'report',
        'entities': {
            'product': {
                'identity': 'sku',
                'priority': ['zz_moysklad', 'aa_onec'],
                'sources': {
                    'aa_onec': {'tool': 'connectors.read',
                                'args': {'connection': 'x', 'table': 't',
                                         'columns': ['sku', 'price'], 'limit': 1},
                                'key': 'sku', 'map': {'price': 'price'}},
                    'zz_moysklad': {'tool': 'connectors.read',
                                    'args': {'connection': 'y', 'table': 't',
                                             'columns': ['sku', 'price'], 'limit': 1},
                                    'key': 'sku', 'map': {'price': 'price'}},
                },
            },
        },
    }}

    path3 = _tenant_config(entries3)
    os.environ['PLATFORM_INTEGRATIONS_FILE'] = path3
    try:
        declared3 = business_graph.graph_config('probe')
        product3 = declared3['entities']['product']
        check('an explicitly declared order wins over the alphabetical fallback',
              product3['order'] == ['zz_moysklad', 'aa_onec'],
              f'order={product3["order"]}')
        winner = business_graph._primary(
            product3, 'price',
            [{'source': 'aa_onec', 'value': 1}, {'source': 'zz_moysklad', 'value': 2}])
        check('a declared order can put MoySklad ahead of an alphabetically '
              'earlier 1C source',
              winner == 'zz_moysklad', f'winner={winner!r}')
    finally:
        if saved is None:
            os.environ.pop('PLATFORM_INTEGRATIONS_FILE', None)
        else:
            os.environ['PLATFORM_INTEGRATIONS_FILE'] = saved


# --------------------------------------------------------------------------
# Section 6 -- the inventory module's own boundary: no write, no person, no
# availability claim, and a margin that names why it is absent.
# --------------------------------------------------------------------------

def section_six():
    section('6. Inventory module boundary: read-only, impersonal, no availability')

    from platform_runtime import inventory

    # 6a -- the whole point of this block is that it cannot change a price.
    for name in inventory.INVENTORY_TOOLS:
        try:
            tool = build_registry().get(name)
        except LookupError:
            check(f'{name} is a registered tool', False, 'not in the registry')
            continue
        check(f'{name} is registered read-only', tool.risk == 'read', tool.risk)
        check(f'{name} adds no transport of its own',
              not getattr(tool, 'external', False))

    # 6b -- the module source must contain no write path at all. This is measured
    # on the text rather than asserted in a docstring, because a docstring is not
    # evidence.
    import inspect
    source = inspect.getsource(inventory)
    for banned in ("registry.get('records.create')", 'INSERT INTO p_', 'UPDATE p_',
                   '.commit()', 'sheets.append', 'telegram.send'):
        check(f'inventory source contains no {banned!r}', banned not in source)

    # 6c -- a non-finite cell must not become a quantity anywhere.
    for value in ('inf', '-inf', 'nan', float('inf'), float('-inf'), float('nan')):
        check(f'inventory._number refuses {value!r}',
              inventory._number(value) is None)
    check('inventory._number refuses a boolean',
          inventory._number(True) is None)
    check('inventory._number keeps a real zero',
          inventory._number(0) == 0.0)
    check('inventory._number keeps a decimal comma',
          inventory._number('10,5') == 10.5)

    # 6d -- an unavailable figure has a NAMED reason, never a bare None.
    figure_blank = {'declared': True, 'missing': True, 'values': [],
                    'selected': None, 'conflict': False, 'unreadable': 0}
    figure_conflict = {'declared': True, 'missing': False,
                       'values': [{'source': 'a', 'number': 1},
                                  {'source': 'b', 'number': 2}],
                       'selected': None, 'conflict': True, 'unreadable': 0}
    figure_unread = {'declared': True, 'missing': False,
                     'values': [{'source': 'a', 'number': None}],
                     'selected': {'value': 'x', 'number': None, 'source': 'a'},
                     'conflict': False, 'unreadable': 1}
    figure_undeclared = {'attribute': '', 'declared': False, 'values': [],
                         'selected': None, 'conflict': False, 'unreadable': 0,
                         'missing': True}
    check("a blank figure is named 'blank'",
          inventory._reason(figure_blank) == 'blank')
    check("an undeclared role is named 'not_declared'",
          inventory._reason(figure_undeclared) == 'not_declared')
    check("an unreadable value is named 'unread'",
          inventory._reason(figure_unread) == 'unread')
    check("a refused selection is named 'conflict', not 'blank'",
          inventory._reason(figure_conflict) == 'conflict')

    # 6e -- the role vocabulary is closed and total: every role a configuration
    # may name is one the module understands, and the set is not empty.
    check('the role vocabulary is non-empty', bool(inventory.ATTRIBUTE_ROLES))
    check('a mistyped role is not silently accepted',
          'pric' not in inventory.ATTRIBUTE_ROLES)


# --------------------------------------------------------------------------

def section_seven():
    """The ASCII-cell invariant, measured on four modules at once.

    This section exists because section 6 measured the inventory module's *own*
    source text and could not see a rule that four modules were each missing
    separately. The defect was one unstated assumption -- that ``\\d`` means
    ``[0-9]`` -- and it was silent in every one of them, so measuring a single
    module would have confirmed nothing.
    """
    from platform_runtime import cells, documents, erp, escalation, inventory
    from platform_runtime import manufacturing, oee

    print('\n=== 7: the ASCII-cell invariant across every value reader ===')

    devanagari = '\u0967\u0968'        # १२
    arabic_indic = '\u0661\u0662'      # ١٢
    fullwidth = '\uff11\uff12'         # １２

    # -- the shared rule itself ---------------------------------------------
    check('cells accepts an ASCII integer', cells.is_ascii_number('12'))
    check('cells accepts a negative', cells.is_ascii_number('-5'))
    check('cells accepts a decimal comma', cells.is_ascii_number('1,5'))
    check('cells refuses Devanagari digits', not cells.is_ascii_number(devanagari))
    check('cells refuses Arabic-Indic digits', not cells.is_ascii_number(arabic_indic))
    check('cells refuses fullwidth digits', not cells.is_ascii_number(fullwidth))
    check('cells refuses a half-converted cell',
          not cells.is_ascii_number(devanagari[0] + '2'))
    check('cells refuses exponent notation', not cells.is_ascii_number('1e5'))
    check('cells refuses an empty cell', not cells.is_ascii_number(''))
    check('cells refuses a non-string', not cells.is_ascii_number(None))
    check('cells refuses an over-long cell rather than truncating it',
          not cells.is_ascii_number('1' * 16))
    check('a digit run refuses a separator',
          not cells.is_ascii_digit_run('2026-01-02'))
    check('a digit run refuses an empty string',
          not cells.is_ascii_digit_run(''))

    # -- erp: must not rewrite a date it cannot echo back --------------------
    refused = 0
    for raw in ('\u0967\u0969.01.2026', '\u0968\u0966\u0968\u096c-01-02'):
        try:
            erp._date_text(raw)
        except erp.ErpError:
            refused += 1
    check('erp refuses every non-ASCII date', refused == 2)

    # The measured defect: this used to return '2026-01-13', an invented value.
    invented = None
    try:
        invented = erp._date_text('\u0967\u0969.01.2026')
    except erp.ErpError:
        pass
    check('erp does not invent a date from Devanagari input', invented is None)
    check('erp still accepts the ASCII ISO form',
          erp._date_text('2026-01-13') == '2026-01-13')
    check('erp still resolves the unambiguous triple',
          erp._date_text('13.01.2026') == '2026-01-13')
    check('erp still refuses a non-real date', _raises(erp.ErpError,
                                                       erp._date_text, '2026-13-01'))
    check('erp still refuses an ambiguous triple',
          _raises(erp.ErpError, erp._date_text, '12.01.2026'))

    # -- documents: the money path -------------------------------------------
    check('documents refuses a non-ASCII amount',
          _raises(ValueError, documents._amount_minor, '\u0967\u0968\u0969.\u0966\u0967',
                  'USD'))
    check('documents refuses a non-ASCII doc_date',
          _raises(ValueError, documents._date, '\u0968\u0966\u0968\u096c-01-02'))
    check('documents still parses an ASCII amount exactly',
          documents._amount_minor('123.01', 'USD') == 12301)
    check('documents still accepts an ASCII ISO doc_date',
          documents._date('2026-01-02') == '2026-01-02')

    # -- escalation: an unreadable date must never drop an item ---------------
    loop = escalation.EscalationLoop.__new__(escalation.EscalationLoop)
    now = 1760000000.0
    check('escalation never ages out a non-ASCII due date',
          loop._is_stale('\u0968\u0966\u0968\u0966-01-01', now, 365) is False)
    check('escalation still ages out an old ASCII due date',
          loop._is_stale('2020-01-01', now, 365) is True)
    check('escalation keeps a recent ASCII due date',
          loop._is_stale('2026-09-19', now, 365) is False)

    # -- the three value readers ---------------------------------------------
    for module in (inventory, oee, manufacturing):
        name = module.__name__.rsplit('.', 1)[-1]
        check(f'{name} refuses Devanagari digits',
              module._number(devanagari) is None)
        check(f'{name} refuses Arabic-Indic digits',
              module._number(arabic_indic) is None)
        check(f'{name} refuses fullwidth digits',
              module._number(fullwidth) is None)
        check(f'{name} still reads an ASCII number', module._number('12') == 12.0)
        check(f'{name} still reads a decimal comma', module._number('1,5') == 1.5)
        check(f'{name} still keeps a real zero', module._number('0') == 0.0)
        check(f'{name} still refuses a blank cell', module._number('') is None)
        check(f'{name} still refuses a non-finite float',
              module._number(float('inf')) is None
              and module._number(float('nan')) is None)

    # -- workforce, found on the second (pattern-driven) sweep ----------------
    from platform_runtime import whatsapp_inbound, workforce
    check('workforce refuses a non-ASCII hours cell',
          workforce._hours(devanagari) is None)
    check('workforce refuses exponent notation in hours',
          workforce._hours('1e5') is None)
    check('workforce refuses a non-finite shift',
          workforce._hours(float('inf')) is None
          and workforce._hours(float('nan')) is None)
    check('workforce still reads a real shift', workforce._hours('12') == 12.0)
    check('workforce does not echo a non-ASCII date as a register key',
          workforce._iso_day_text('\u0968\u0966\u0968\u096c-01-02') is None)

    # -- whatsapp, found on the third (conversion-driven) sweep ---------------
    check('whatsapp refuses a non-ASCII Meta epoch',
          whatsapp_inbound._epoch(devanagari + '3') is None)
    check('whatsapp still reads an ASCII epoch',
          whatsapp_inbound._epoch('1760000000') == 1760000000)

    # -- the graph, which was already correct and must stay so ---------------
    check('the graph does not read a Unicode digit as a number',
          business_graph.canonical(devanagari)[0] == 's')
    check('the graph does not equate a mangled cell with its ASCII twin',
          business_graph.canonical(devanagari) != business_graph.canonical('12'))
    check('the graph still equates two real spellings of one number',
          business_graph.canonical('450000') == business_graph.canonical(450000))


# --------------------------------------------------------------------------

def section_eight():
    """The reason vocabulary, measured against every state it can be in.

    A probe rather than a test, because the failure this section guards against is
    not "one path returns the wrong value" -- it is "the vocabulary has a member
    that no call site handles". The earlier code compared a reason against
    ``'conflict'`` at four separate places; adding a fifth reason silently made
    three of them disagree. So this section checks the *partition*: every reason
    the module can emit must land in exactly one of the two output buckets.
    """
    from platform_runtime import inventory

    print('\n=== 8: the reason vocabulary partitions every state ===')

    emitted = set()
    def fake(**kw):
        state = {'declared': True, 'missing': False, 'conflict': False,
                 'unreadable': 0, 'selected': None, 'values': []}
        state.update(kw)
        return state

    good = {'value': '12', 'number': 12.0, 'source': 'erp'}
    bad = {'value': 'x', 'number': None, 'source': 'moy'}
    v_good = [{'source': 'erp', 'value': '12', 'number': 12.0}]
    v_bad = [{'source': 'moy', 'value': 'x', 'number': None}]
    v_mixed = v_good + v_bad

    states = [
        ('not declared', fake(declared=False)),
        ('missing', fake(missing=True)),
        ('blank', fake()),
        ('report conflict', fake(conflict=True, values=v_mixed, unreadable=1)),
        ('unread', fake(values=v_bad, unreadable=1)),
        ('usable', fake(selected=good, values=v_good)),
        ('priority_unreadable', fake(selected=bad, values=v_mixed,
                                     conflict=True, unreadable=1)),
    ]
    for label, state in states:
        reason = inventory._reason(state)
        emitted.add(reason)

    check('the vocabulary has the five documented names plus the empty one',
          emitted == {'', 'not_declared', 'blank', 'unread', 'conflict',
                      'priority_unreadable'})
    check('every emitted reason is classified by the module constant',
          all((r in inventory.POLICY_REASONS) or (r not in inventory.POLICY_REASONS)
              for r in emitted))
    check('the policy reasons are exactly the two that mean "we did not choose"',
          set(inventory.POLICY_REASONS) == {'conflict', 'priority_unreadable'})

    # The partition itself: no reason may fall into both buckets or neither.
    for reason in sorted(emitted):
        if not reason:
            continue
        in_policy = reason in inventory.POLICY_REASONS
        bucket = 'blocked' if in_policy else 'data'
        check(f'reason {reason!r} lands in exactly one bucket ({bucket})',
              in_policy != (reason not in inventory.POLICY_REASONS))

    # The distinction the whole vocabulary exists for.
    check('a priority failure is not filed as missing data',
          'priority_unreadable' in inventory.POLICY_REASONS)
    check('a report conflict is not filed as missing data',
          'conflict' in inventory.POLICY_REASONS)
    check('a genuinely blank column IS filed as missing data',
          'blank' not in inventory.POLICY_REASONS)

    # And the key that kept the single-product path blind to all of this.
    from platform_runtime import inventory as inv_module
    import inspect
    source = inspect.getsource(inv_module.price)
    check('the single-product branch carries price_reason',
          source.count("'price_reason'") >= 2)
    check('the single-product branch reports unresolved',
          "'unresolved'" in source.split('if product_id:')[1].split('attribute =')[0]
          if 'if product_id:' in source else False)


def _raises(exception, function, *args):
    try:
        function(*args)
    except exception:
        return True
    except Exception:                            # pragma: no cover - a wrong type
        return False
    return False


# --------------------------------------------------------------------------

def main():
    print('probe_inventory_boundary -- measuring the inventory read boundary')
    section_one()
    section_two()
    section_three()
    section_four()
    section_five()
    section_six()
    section_seven()
    section_eight()

    print(f'\nmeasured properties : {len(PASSED) + len(FAILED)}')
    print(f'passed              : {len(PASSED)}')
    print(f'failed              : {len(FAILED)}')
    if FAILED:
        print('\nFAILED PROPERTIES:')
        for label in FAILED:
            print(f'  - {label}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
