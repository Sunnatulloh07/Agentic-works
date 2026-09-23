"""A pack may only name tools a runtime adapter actually provides.

Before this contract existed, a pack naming `telegram` instead of `telegram.send`
loaded clean, returned an empty tool list from /catalog, and only failed much
later as HTTP 422 "Invalid input" on submit or as a silent `failed` inbox event.
Shipped packs `marketing` and `_template` were dead for exactly that reason.
Dependency-free: no YAML, no pydantic, no environment.
"""
import unittest

from platform_runtime.tools import build_registry, known_tool_names, unknown_tools

# The names the marketing and _template packs shipped with. None is a real tool.
HISTORIC_TYPOS = ['kb', 'telegram', 'instagram', 'web_search']


class KnownNameTests(unittest.TestCase):
    def test_reports_the_core_tools_every_vertical_relies_on(self):
        for name in ('reports.summary', 'records.create', 'memory.search',
                     'knowledge.search', 'telegram.send', 'instagram.send'):
            self.assertIn(name, known_tool_names())

    def test_includes_the_catalog_conditional_tool(self):
        # products.search only registers when a catalog is supplied, but a pack
        # is still entitled to name it, so the contract must accept it.
        self.assertNotIn('products.search', build_registry().items)
        self.assertIn('products.search', known_tool_names())

    def test_covers_every_name_a_catalog_enabled_registry_registers(self):
        # Fully injected: the catalogue reader and the shop reader (shop.info,
        # orders.draft) are both conditional, and a pack may name all of them.
        registry = build_registry(lambda tenant, query: [], lambda tenant: {})
        self.assertEqual(set(registry.items), set(known_tool_names()))

    def test_result_is_a_stable_immutable_set(self):
        first = known_tool_names()
        self.assertIsInstance(first, frozenset)
        self.assertEqual(first, known_tool_names())


class UnknownToolTests(unittest.TestCase):
    def test_accepts_a_fully_valid_agent(self):
        self.assertEqual([], unknown_tools(['reports.summary', 'telegram.send']))

    def test_accepts_an_agent_that_declares_no_tools(self):
        self.assertEqual([], unknown_tools([]))

    def test_names_every_offending_tool_so_the_message_is_actionable(self):
        self.assertEqual(['alpha.missing', 'zeta.missing'],
                         unknown_tools(['reports.summary', 'zeta.missing', 'alpha.missing']))

    def test_rejects_the_historic_shipped_pack_names(self):
        self.assertEqual(sorted(HISTORIC_TYPOS), unknown_tools(HISTORIC_TYPOS))

    def test_rejects_a_near_miss_of_a_real_tool(self):
        # `telegram` is the plausible typo for `telegram.send`; it must not pass.
        self.assertEqual(['telegram'], unknown_tools(['telegram', 'telegram.send']))

    def test_deduplicates_and_sorts_for_a_deterministic_error_message(self):
        self.assertEqual(['bad.one'], unknown_tools(['bad.one', 'bad.one']))

    def test_returns_a_plain_sorted_list(self):
        result = unknown_tools(['b.missing', 'a.missing'])
        self.assertIsInstance(result, list)
        self.assertEqual(result, sorted(result))


if __name__ == '__main__':
    unittest.main()
