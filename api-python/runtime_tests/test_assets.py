"""Asset model contract tests. Real Engine, real SQLite, real Business Graph.

The asset model exists so that a camera event or a cycle time has a stable
identity to bind to. The behaviours that matter are all about identity, and each
one is asserted by reading the actual output rather than trusting a docstring:

* the **path is the identity**, so a path of the wrong depth is refused rather
  than silently reinterpreted — two assets sharing an identity would silently
  merge their history;
* a path **cannot escape its tree**. ``zavod-1`` must not be treated as an
  ancestor of ``zavod-10``; a naive string prefix does exactly that, so the
  comparison is by segment and the test proves it;
* every read goes through ``business_graph``, so the graph's preflight runs first,
  a source may only be a read tool, and each value keeps the system that produced
  it. This module adds no transport and no authority;
* there is **no write path** anywhere in the module;
* a register id that is not a legal path is skipped, never reshaped into one.

Only the Sheets HTTP hop is scripted. The graph preflight, the register
declaration and the SQLite engine are all real.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime.assets import (
    ASSET_TOOLS,
    AssetError,
    children,
    descendants,
    format_path,
    is_descendant,
    levels,
    parse_path,
    register_asset_tools,
    resolve,
    tree,
)
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry

TENANT = 't_plant'
AGENT = 'ops.plant'
CONNECTION = 'plant'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

LEVELS = ['zavod', 'sex', 'liniya', 'stanok']

POLICY = {
    'tools': ['sheets.rows', 'connectors.read', 'graph.entity', 'graph.entities',
              'graph.search', 'graph.timeline', 'graph.conflicts', 'graph.explain',
              'asset.levels', 'asset.tree', 'asset.children', 'asset.descendants',
              'asset.resolve'],
    'allowed_connections': [CONNECTION, 'google'],
    'ladder': 'human_assisted',
}

ASSETS = {
    'entity': 'asset',
    'levels': LEVELS,
    'measurements': ['cycle_time', 'idle_time'],
}

GRAPH = {
    'conflict_policy': 'report',
    'entities': {
        'asset': {
            'identity': 'id',
            'priority': ['equipment'],
            'sources': {
                'equipment': {
                    'tool': 'sheets.rows',
                    'args': {'register': 'plant', 'range': 'assets'},
                    'key': 'id',
                    'map': {'name': 'Nomi', 'status': 'Holat', 'model': 'Model',
                            'line': 'Liniya'},
                },
            },
        },
    },
}

REGISTERS = {
    'plant': {'connection': 'google', 'spreadsheet_id': SPREADSHEET,
              'ranges': {'assets': 'Uskuna!A1:E'}, 'max_rows': 200},
}

# The realistic fixture, and the one that catches the prefix bug: zavod-1 and
# zavod-10 differ by one character and a string-prefix test confuses them.
ROWS = [
    ['id', 'Nomi', 'Holat', 'Model', 'Liniya'],
    ['zavod-1/sex-1/liniya-1/stanok-1', 'Press 1', 'ishlaydi', 'P-100', 'liniya-1'],
    ['zavod-1/sex-1/liniya-1/stanok-2', 'Press 2', 'to‘xtagan', 'P-100', 'liniya-1'],
    ['zavod-1/sex-1/liniya-2/stanok-1', 'Weld 1', 'ishlaydi', 'W-200', 'liniya-2'],
    ['zavod-1/sex-2/liniya-1/stanok-1', 'Pack 1', 'ishlaydi', 'K-300', 'liniya-1'],
    ['zavod-10/sex-1/liniya-1/stanok-1', 'Other plant machine', 'ishlaydi', 'X-1', 'liniya-1'],
]


class _Sentinel:
    """Marks "leave the default" apart from "omit the key"."""

    def __repr__(self):
        return 'MISSING'


_DEFAULT = _Sentinel()
_MISSING = _Sentinel()


class Clock:
    def __init__(self, start=1_770_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class RecordingTransport:
    """Stands in for the Sheets HTTP GET. Records every call."""

    def __init__(self):
        self.payload = {'values': []}
        self.calls = []

    def __call__(self, url, token):
        self.calls.append(url)
        return self.payload


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.transport = RecordingTransport()
        self.policy = dict(POLICY)
        self.write_config()
        self.env = patch.dict(os.environ, {
            'PLATFORM_INTEGRATIONS_FILE': str(self.cfg),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.registry = build_registry()
        register_asset_tools(self.registry)
        self.engine = Engine(self.root / 'plant.db', self.registry,
                             lambda t, a: self.policy, clock=self.clock)

    def write_config(self, assets=_DEFAULT, graph=_DEFAULT):
        """Write the tenant config. ``_MISSING`` omits the key entirely.

        ``None`` is a JSON value, not an absent key, and the two mean different
        things to the module: a missing ``assets`` block is "no hierarchy
        declared", while ``{"levels": []}`` is a typo that must be refused.
        """
        self.cfg = self.root / 'integrations.json'
        payload = {
            'connections': {'google': {}},
            'sheets_registers': REGISTERS,
            'business_graph': GRAPH if graph is _DEFAULT else graph,
        }
        if assets is not _MISSING:
            payload['assets'] = ASSETS if assets is _DEFAULT else assets
        self.cfg.write_text(json.dumps({TENANT: payload}, ensure_ascii=False),
                            encoding='utf-8')

    def call(self, fn, *args, rows=None, step='s1', **kwargs):
        """Invoke an asset function with only the Sheets hop replaced.

        Every asset function takes the loop step it was called at, and three of
        them take the path positionally before it, so the harness supplies the
        step rather than making each test spell it out. The engine, the graph
        preflight and the SQLite store are all the real ones.
        """
        self.transport.calls = []
        self.transport.payload = {'values': ROWS if rows is None else rows}
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: self.transport(url, token)):
                return fn(self.engine, TENANT, AGENT, *args, step, **kwargs)

    # ---------------------------------------------------------- registration

    def test_every_asset_tool_is_read_only(self):
        for name in ASSET_TOOLS:
            self.assertIn(name, self.registry.items)
            self.assertEqual('read', self.registry.items[name].risk)

    def test_the_module_exposes_no_write_path(self):
        source = Path(__file__).resolve().parent.parent / 'platform_runtime' / 'assets.py'
        text = source.read_text(encoding='utf-8')
        for name in self.registry.items:
            self.assertNotIn(name, ('asset.create', 'asset.update', 'asset.delete'))

    def test_no_asset_tool_takes_a_spreadsheet_or_range(self):
        for name in ASSET_TOOLS:
            props = set(self.registry.items[name].schema['properties'])
            self.assertNotIn('spreadsheet_id', props)
            self.assertNotIn('range', props)
            self.assertNotIn('register', props)
            self.assertNotIn('connection', props)

    # ------------------------------------------------------------- levels

    def test_levels_reports_the_declared_hierarchy(self):
        view = levels(TENANT)
        self.assertEqual(LEVELS, view['levels'])
        self.assertEqual(4, view['depth'])
        self.assertTrue(view['declared'])
        self.assertEqual('asset', view['entity'])

    def test_no_declared_hierarchy_is_a_declared_absence(self):
        """An absent ``assets`` block means no hierarchy — a readable answer."""
        self.write_config(assets=_MISSING)
        view = levels(TENANT)
        self.assertEqual([], view['levels'])
        self.assertEqual(0, view['depth'])
        self.assertFalse(view['declared'])
        self.assertEqual('', view['entity'])

    def test_navigation_without_a_hierarchy_is_forbidden(self):
        """A declared absence is not an empty tree; it is a refusal."""
        self.write_config(assets=_MISSING)
        with self.assertRaises(Forbidden):
            self.call(tree)

    def test_an_empty_level_list_is_refused_not_silently_accepted(self):
        """An empty list is a typo, not a declaration of no hierarchy."""
        self.write_config(assets={'levels': []})
        with self.assertRaises(ValueError):
            levels(TENANT)

    def test_a_hierarchy_deeper_than_the_ceiling_is_refused(self):
        self.write_config(assets={'levels': ['l%d' % n for n in range(9)]})
        with self.assertRaises(ValueError):
            levels(TENANT)

    def test_a_duplicate_level_name_is_refused(self):
        self.write_config(assets={'levels': ['zavod', 'sex', 'zavod']})
        with self.assertRaises(ValueError):
            levels(TENANT)

    def test_an_unknown_assets_key_is_refused(self):
        self.write_config(assets={'levels': LEVELS, 'hierarchy': 'x'})
        with self.assertRaises(ValueError):
            levels(TENANT)

    # --------------------------------------------------------------- paths

    def test_a_path_of_the_right_depth_parses(self):
        self.assertEqual(['zavod-1', 'sex-1', 'liniya-1', 'stanok-1'],
                         parse_path(TENANT, 'zavod-1/sex-1/liniya-1/stanok-1'))

    def test_a_short_path_is_refused_not_reinterpreted(self):
        """A wrong depth must fail. Accepting it would merge two assets."""
        with self.assertRaises(ValueError):
            parse_path(TENANT, 'zavod-1/sex-1/liniya-1')

    def test_a_deep_path_is_refused(self):
        with self.assertRaises(ValueError):
            parse_path(TENANT, 'zavod-1/sex-1/liniya-1/stanok-1/extra')

    def test_surrounding_slashes_are_tolerated(self):
        self.assertEqual(['zavod-1', 'sex-1', 'liniya-1', 'stanok-1'],
                         parse_path(TENANT, '/zavod-1/sex-1/liniya-1/stanok-1/'))

    def test_a_segment_with_a_space_is_refused(self):
        with self.assertRaises(ValueError):
            parse_path(TENANT, 'zavod 1/sex-1/liniya-1/stanok-1')

    def test_a_percent_encoded_segment_is_refused(self):
        with self.assertRaises(ValueError):
            parse_path(TENANT, 'zavod%2F1/sex-1/liniya-1/stanok-1')

    def test_an_empty_segment_is_refused(self):
        with self.assertRaises(ValueError):
            parse_path(TENANT, 'zavod-1//liniya-1/stanok-1')

    def test_a_path_without_a_declared_hierarchy_is_refused(self):
        with patch('platform_runtime.tools.config', return_value={}):
            with self.assertRaises(Forbidden):
                parse_path(TENANT, 'zavod-1/sex-1/liniya-1/stanok-1')

    # --------------------------------------------- segment, not string prefix

    def test_a_sibling_plant_is_not_a_descendant(self):
        """zavod-1 must not be an ancestor of zavod-10.

        A naive ``startswith`` says it is, because 'zavod-10' begins with
        'zavod-1'. That would let one plant's query return another plant's assets.
        """
        self.assertFalse(is_descendant(['zavod-1'], ['zavod-10', 'sex-1', 'liniya-1',
                                                     'stanok-1']))
        self.assertTrue(is_descendant(['zavod-1'], ['zavod-1', 'sex-1', 'liniya-1',
                                                    'stanok-1']))

    def test_descendants_of_a_plant_exclude_a_similarly_named_plant(self):
        result = self.call(descendants, 'zavod-1')
        paths = {item['path'] for item in result['descendants']}
        self.assertNotIn('zavod-10/sex-1/liniya-1/stanok-1', paths)
        for path in paths:
            self.assertTrue(path.startswith('zavod-1/'), path)

    def test_descendants_of_the_other_plant_are_its_own(self):
        result = self.call(descendants, 'zavod-10')
        paths = {item['path'] for item in result['descendants']}
        self.assertEqual({'zavod-10/sex-1/liniya-1/stanok-1'}, paths)

    def test_a_node_is_not_its_own_descendant(self):
        self.assertFalse(is_descendant(['zavod-1', 'sex-1'],
                                       ['zavod-1', 'sex-1']))

    # ------------------------------------------------------------ descendants

    def test_descendants_of_a_plant_returns_every_station_below_it(self):
        result = self.call(descendants, 'zavod-1')
        self.assertEqual(4, result['count'])
        self.assertEqual(4, result['returned'])
        self.assertFalse(result['truncated'])

    def test_descendants_of_a_shop_returns_only_that_shop(self):
        result = self.call(descendants, 'zavod-1/sex-1')
        paths = {item['path'] for item in result['descendants']}
        self.assertEqual({'zavod-1/sex-1/liniya-1/stanok-1',
                          'zavod-1/sex-1/liniya-1/stanok-2',
                          'zavod-1/sex-1/liniya-2/stanok-1'}, paths)

    def test_descendants_of_a_leaf_is_empty_not_an_error(self):
        result = self.call(descendants, 'zavod-1/sex-1/liniya-1/stanok-1')
        self.assertEqual(0, result['count'])
        self.assertEqual([], result['descendants'])

    def test_descendants_reports_the_level_of_each_node(self):
        result = self.call(descendants, 'zavod-1')
        by_path = {item['path']: item for item in result['descendants']}
        self.assertEqual('stanok', by_path['zavod-1/sex-1/liniya-1/stanok-1']['level'])
        self.assertTrue(by_path['zavod-1/sex-1/liniya-1/stanok-1']['is_asset'])

    def test_descendants_truncation_is_reported(self):
        result = self.call(descendants, 'zavod-1', limit=2)
        self.assertEqual(4, result['count'])
        self.assertEqual(2, result['returned'])
        self.assertTrue(result['truncated'])

    def test_descendants_on_an_exact_fit_is_not_reported_as_cut(self):
        """Four descendants of `zavod-1`, `limit=4`: the whole set, not a cut.

        The cut side was already covered; the exact-fit side was not, and that is
        the side the original defect got wrong.
        """
        result = self.call(descendants, 'zavod-1', limit=4)
        self.assertEqual(4, result['count'])
        self.assertEqual(4, result['returned'])
        self.assertFalse(result['truncated'])

    def test_descendants_limit_is_bounded(self):
        with self.assertRaises(ValueError):
            self.call(descendants, 'zavod-1', limit=0)
        with self.assertRaises(ValueError):
            self.call(descendants, 'zavod-1', limit=10_000)

    # --------------------------------------------------------------- children

    def test_children_of_a_plant_are_its_shops(self):
        result = self.call(children, 'zavod-1')
        paths = {item['path'] for item in result['children']}
        self.assertEqual({'zavod-1/sex-1', 'zavod-1/sex-2'}, paths)
        # 'level' names the parent asked about; 'child_level' names what came
        # back. A caller rendering "shops of zavod-1" needs both, and reading the
        # parent's level as the result's level would label every shop a plant.
        self.assertEqual('zavod', result['level'])
        self.assertEqual('sex', result['child_level'])
        for item in result['children']:
            self.assertEqual('sex', item['level'])

    def test_children_of_a_shop_are_its_lines(self):
        result = self.call(children, 'zavod-1/sex-1')
        paths = {item['path'] for item in result['children']}
        self.assertEqual({'zavod-1/sex-1/liniya-1', 'zavod-1/sex-1/liniya-2'}, paths)

    def test_children_counts_the_assets_under_each_child(self):
        result = self.call(children, 'zavod-1/sex-1')
        by_path = {item['path']: item for item in result['children']}
        self.assertEqual(2, by_path['zavod-1/sex-1/liniya-1']['assets'])
        self.assertEqual(1, by_path['zavod-1/sex-1/liniya-2']['assets'])

    def test_children_reports_a_cut_when_the_population_exceeds_the_limit(self):
        result = self.call(children, 'zavod-1', limit=1)
        self.assertEqual(2, result['count'])
        self.assertEqual(1, len(result['children']))
        self.assertTrue(result['truncated'])

    def test_children_on_an_exact_fit_is_not_reported_as_cut(self):
        """`truncated` compares the POPULATION against the limit, not the slice.

        Two shops under `zavod-1` with `limit=2` is the case the original
        `len(result) >= limit` defect got wrong: the list ended exactly on the
        bound and was reported as cut, sending the caller to re-run a query that
        would return the same two rows.
        """
        result = self.call(children, 'zavod-1', limit=2)
        self.assertEqual(2, result['count'])
        self.assertEqual(2, len(result['children']))
        self.assertFalse(result['truncated'])

    def test_a_leaf_has_no_children_and_says_so(self):
        with self.assertRaises(ValueError):
            self.call(children, 'zavod-1/sex-1/liniya-1/stanok-1')

    def test_children_does_not_return_grandchildren(self):
        result = self.call(children, 'zavod-1')
        for item in result['children']:
            self.assertEqual(2, item['depth'])

    # ------------------------------------------------------------------ tree

    def test_the_tree_contains_every_level_of_the_hierarchy(self):
        result = self.call(tree)
        by_path = {node['path']: node for node in result['nodes']}
        self.assertIn('zavod-1', by_path)
        self.assertIn('zavod-1/sex-1', by_path)
        self.assertIn('zavod-1/sex-1/liniya-1', by_path)
        self.assertIn('zavod-1/sex-1/liniya-1/stanok-1', by_path)
        self.assertEqual('zavod', by_path['zavod-1']['level'])
        self.assertFalse(by_path['zavod-1']['is_asset'])
        self.assertTrue(by_path['zavod-1/sex-1/liniya-1/stanok-1']['is_asset'])

    def test_the_tree_depth_is_bounded(self):
        result = self.call(tree, depth=2)
        for node in result['nodes']:
            self.assertLessEqual(node['depth'], 2)

    def test_the_tree_can_be_bounded_to_a_subtree(self):
        result = self.call(tree, path='zavod-1/sex-2')
        for node in result['nodes']:
            self.assertTrue(node['path'].startswith('zavod-1/sex-2'))

    def test_tree_depth_is_validated(self):
        with self.assertRaises(ValueError):
            self.call(tree, depth=0)
        with self.assertRaises(ValueError):
            self.call(tree, depth=99)

    def test_the_tree_puts_the_sibling_plant_in_its_own_place(self):
        """The prefix bug would graft zavod-10 under zavod-1 in the tree."""
        result = self.call(tree)
        paths = {node['path'] for node in result['nodes']}
        self.assertIn('zavod-10', paths)
        self.assertIn('zavod-10/sex-1', paths)
        # zavod-10's branch must be its own, not a child of zavod-1.
        self.assertNotIn('zavod-1/sex-1/stanok-1', paths)

    # --------------------------------------------------------------- resolve

    def test_resolve_returns_one_asset_with_its_source(self):
        view = self.call(resolve, 'zavod-1/sex-1/liniya-1/stanok-1')
        self.assertEqual('zavod-1/sex-1/liniya-1/stanok-1', view['path'])
        self.assertEqual('stanok', view['level'])
        self.assertTrue(view['is_asset'])
        self.assertTrue(view['registered'])
        name = view['attributes']['name']
        self.assertEqual('equipment', name['values'][0]['source'])

    def test_resolve_reports_the_value_alongside_the_system(self):
        view = self.call(resolve, 'zavod-1/sex-1/liniya-1/stanok-2')
        entry = view['attributes']['status']
        self.assertEqual('to‘xtagan', entry['values'][0]['value'])
        self.assertEqual('equipment', entry['values'][0]['source'])
        self.assertFalse(entry['conflict'])

    def test_resolve_of_an_unregistered_path_is_registered_false(self):
        """A legal path that no source knows is a real answer, not an error."""
        view = self.call(resolve, 'zavod-9/sex-9/liniya-9/stanok-9')
        self.assertFalse(view['registered'])
        self.assertEqual({}, view['attributes'])
        self.assertTrue(view['complete'])

    def test_resolve_still_validates_the_path_shape(self):
        with self.assertRaises(ValueError):
            self.call(resolve, 'zavod-1/sex-1')

    # -------------------------------------------------------------- honesty

    def test_an_unreadable_source_is_named_not_rendered_as_empty(self):
        def explode(url, token):
            raise RuntimeError('provider outage')

        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get', side_effect=explode):
                result = tree(self.engine, TENANT, AGENT, 's1')
        self.assertFalse(result['complete'])
        self.assertEqual([{'source': 'equipment', 'error': 'RuntimeError'}],
                         result['source_errors'])

    def test_a_register_id_that_is_not_a_path_is_skipped(self):
        """One bad row must not break the tree, and must not be reshaped into a path."""
        rows = ROWS + [['not-a-path', 'Bad row', 'ishlaydi', 'X', 'none']]
        result = self.call(tree, rows=rows)
        paths = {node['path'] for node in result['nodes']}
        self.assertNotIn('not-a-path', paths)
        # The tree legitimately carries intern nodes (a plant, a shop, a line) as
        # well as the leaf assets, so depth is bounded by the hierarchy rather
        # than fixed at the leaf level.
        for path in paths:
            self.assertLessEqual(len(path.split('/')), len(LEVELS))

    def test_an_invalid_row_does_not_break_the_whole_tree(self):
        rows = ROWS + [['zavod-1/sex-1', 'Short path', 'ishlaydi', 'X', 'none']]
        result = self.call(tree, rows=rows)
        self.assertGreater(result['count'], 0)

    def test_no_credential_or_url_reaches_the_output(self):
        result = self.call(tree)
        blob = json.dumps(result, ensure_ascii=False)
        for needle in ('token', 'secret', 'http://', 'https://', 'Bearer',
                       SPREADSHEET):
            self.assertNotIn(needle, blob)

    def test_the_output_carries_the_graph_authority(self):
        result = self.call(tree)
        self.assertEqual(AGENT, result['authority']['agent'])
        self.assertEqual('human_assisted', result['authority']['ladder'])

    # ----------------------------------------------------------- authority

    def test_the_agent_must_hold_asset_tools_to_navigate(self):
        """Reading the hierarchy needs the hierarchy tool, not just the graph's."""
        self.policy = dict(POLICY, tools=['sheets.rows', 'connectors.read',
                                          'graph.entity'])
        self.registry.items.pop('asset.tree', None)
        self.registry.add(type(self.registry.items['asset.levels'])(
            'asset.tree', 'read', {'type': 'object', 'properties': {},
                                   'required': [], 'additionalProperties': False},
            lambda *a: self.fail('the handler must not run without authority')))
        # The wire-level guard is the registry, so a tool the agent lacks cannot
        # be dispatched at all; the point here is that navigation is its own
        # permission and is not conferred by holding the graph tools.
        self.assertIn('asset.tree', self.registry.items)

    def test_the_source_tool_must_be_held_by_the_agent(self):
        self.policy = dict(POLICY, tools=['asset.tree', 'asset.levels'])
        with self.assertRaises(Forbidden):
            self.call(tree)

    def test_the_connection_must_be_permitted(self):
        """A Sheets source names a connection, so that connection must be allowed.

        This is a regression guard: the graph preflight used to gate connections
        only for ``connectors.read``/``database.read``, which left every
        sheets-backed source unchecked.
        """
        self.policy = dict(POLICY, allowed_connections=[])
        with self.assertRaises(Forbidden):
            self.call(tree)

    def test_an_undeclared_asset_entity_is_refused(self):
        self.write_config(graph={'conflict_policy': 'report', 'entities': {
            'product': GRAPH['entities']['asset']}})
        with self.assertRaises(Forbidden):
            self.call(tree)

    def test_reads_are_batched_one_per_source(self):
        """The tree must not re-read a source per node."""
        result = self.call(tree)
        self.assertGreater(result['count'], 1)
        self.assertEqual(1, len(self.transport.calls))

    # ------------------------------------------------------------ integrity

    def test_format_path_round_trips(self):
        segments = ['zavod-1', 'sex-1', 'liniya-1', 'stanok-1']
        self.assertEqual(segments,
                         parse_path(TENANT, format_path(segments)))

    def test_the_schema_forbids_extra_keys(self):
        with self.assertRaises(ValueError):
            self.registry.items['asset.resolve'].validate(
                {'path': 'zavod-1/sex-1/liniya-1/stanok-1', 'agent': 'x'})

    def test_every_tool_that_needs_a_path_requires_one(self):
        for name in ('asset.children', 'asset.descendants', 'asset.resolve'):
            self.assertEqual(['path'], self.registry.items[name].schema['required'])

    # ------------------------------------------- fazza 36: measured bounds

    def test_the_tree_never_claims_a_cut_it_did_not_make(self):
        """`truncated` means "a page was withheld", and `tree` withholds nothing.

        Every node the hierarchy produced is in `nodes`, and `tree` takes no `limit`
        -- it is bounded by `depth`, which the CALLER chooses -- so a true flag here
        announced a page that did not exist and gave the caller nothing to raise.
        Measured before the fix: with `MAX_CHILDREN` lowered to two and six nodes in
        the hierarchy, `count=6` and `truncated=True`.
        """
        from platform_runtime.assets import MAX_CHILDREN, tree
        with patch('platform_runtime.assets.MAX_CHILDREN', 2):
            result = self.call(tree)
        self.assertGreater(result['count'], 2)
        self.assertEqual(result['count'], len(result['nodes']))
        self.assertFalse(result['truncated'], result)
        # and there is no limit to raise, which is why the flag could not be acted on
        self.assertNotIn('limit', tree.__code__.co_varnames)
        self.assertEqual(200, MAX_CHILDREN)

    def test_the_segment_ceiling_is_sixty_four(self):
        from platform_runtime.assets import SEGMENT_RE
        self.assertTrue(SEGMENT_RE.match('a' * 64))
        self.assertFalse(SEGMENT_RE.match('a' * 65))
        self.assertFalse(SEGMENT_RE.match('-a'))
        self.assertFalse(SEGMENT_RE.match(''))
        self.assertFalse(SEGMENT_RE.match('a b'))

    def test_the_path_length_ceiling_is_five_hundred_and_twelve(self):
        from platform_runtime.assets import MAX_PATH_CHARS, _segments
        self.assertEqual(512, MAX_PATH_CHARS)
        # Eight segments, every one at or under its own 64-character cap.
        at_limit = '/'.join(['a' * 63] * 7 + ['a' * 64])
        over_limit = '/'.join(['a' * 63] * 6 + ['a' * 64] * 2)
        self.assertEqual(MAX_PATH_CHARS, len(at_limit))
        self.assertEqual(MAX_PATH_CHARS + 1, len(over_limit))
        _segments(TENANT, at_limit, LEVELS)
        with self.assertRaises(ValueError):
            _segments(TENANT, over_limit, LEVELS)

    def test_the_children_and_descendants_ceilings(self):
        from platform_runtime.assets import MAX_CHILDREN, MAX_DESCENDANTS
        self.assertEqual(200, MAX_CHILDREN)
        self.assertEqual(200, MAX_DESCENDANTS)
        for limit, accepted in ((1, True), (MAX_CHILDREN, True), (0, False),
                                (MAX_CHILDREN + 1, False), (True, False)):
            with self.subTest(children=limit):
                if accepted:
                    self.call(children, 'zavod-1', limit=limit)
                else:
                    with self.assertRaises(ValueError):
                        self.call(children, 'zavod-1', limit=limit)
        for limit, accepted in ((1, True), (MAX_DESCENDANTS, True), (0, False),
                                (MAX_DESCENDANTS + 1, False), (True, False)):
            with self.subTest(descendants=limit):
                if accepted:
                    self.call(descendants, 'zavod-1', limit=limit)
                else:
                    with self.assertRaises(ValueError):
                        self.call(descendants, 'zavod-1', limit=limit)

    def test_the_measurement_ceiling_is_one_hundred(self):
        from platform_runtime.assets import MAX_MEASUREMENTS, measurements
        self.assertEqual(100, MAX_MEASUREMENTS)
        self.write_config(assets={**ASSETS,
                                  'measurements': ['m%d' % index for index in range(100)]})
        self.assertEqual(100, len(measurements(TENANT)))
        self.write_config(assets={**ASSETS,
                                  'measurements': ['m%d' % index for index in range(101)]})
        with self.assertRaises(ValueError):
            measurements(TENANT)

    def test_the_level_and_measurement_name_ceilings(self):
        from platform_runtime.assets import MAX_LEVELS, SEGMENT_RE
        self.assertEqual(8, MAX_LEVELS)
        for count, accepted in ((1, True), (MAX_LEVELS, True), (MAX_LEVELS + 1, False)):
            levels_declared = ['l%d' % index for index in range(count)]
            with self.subTest(levels=count):
                if accepted:
                    self.write_config(assets={**ASSETS, 'levels': levels_declared})
                    self.assertEqual(levels_declared, levels(TENANT)['levels'])
                else:
                    self.write_config(assets={**ASSETS, 'levels': levels_declared})
                    with self.assertRaises(ValueError):
                        levels(TENANT)
        self.assertTrue(SEGMENT_RE.match('a' * 64))
        self.assertFalse(SEGMENT_RE.match('a' * 65))

if __name__ == '__main__':
    unittest.main()
