"""The tool-schema vocabulary the dashboard's form builder can render.

``apps/ui/lib/tools-client.mjs`` turns a tool's JSON schema into a form. That is
only safe while the registry's vocabulary stays inside a known set: string,
integer, boolean, an array of strings, and an object (rendered as a JSON field).
A new shape -- a float, an array of objects, a nested array -- would silently
become a JSON textarea, which is exactly the "call a tool" gap this surface
exists to close.

So the contract is pinned here instead of assumed there. Two rules are stronger
than the vocabulary itself:

* **an enum appears only on strings**, so a choice is always a select box;
* **no integer field declares a negative minimum**, which is why the builder may
  refuse a minus sign as a typo (``tools-client.mjs`` integer()).

A failure here is not "the registry is wrong": it is "teach the builder the new
shape, or declare one it already renders".
"""
import unittest

from platform_runtime.tools import build_registry

SUPPORTED_TYPES = {'string', 'integer', 'boolean', 'object', 'array'}
SUPPORTED_ITEM_TYPES = {'string'}


def properties():
    for tool in build_registry().items.values():
        schema = tool.schema or {}
        for name, spec in (schema.get('properties') or {}).items():
            if isinstance(spec, dict):
                yield tool.name, name, spec


class SchemaVocabularyTests(unittest.TestCase):
    def test_the_registry_and_its_schemas_are_not_empty(self):
        self.assertGreaterEqual(len(list(properties())), 100)

    def test_every_property_type_is_one_the_builder_renders(self):
        for tool, name, spec in properties():
            with self.subTest(tool=tool, field=name):
                self.assertIn(spec.get('type'), SUPPORTED_TYPES)

    def test_arrays_hold_only_strings(self):
        for tool, name, spec in properties():
            if spec.get('type') == 'array':
                with self.subTest(tool=tool, field=name):
                    self.assertIn((spec.get('items') or {}).get('type'), SUPPORTED_ITEM_TYPES)

    def test_an_enum_is_always_a_non_empty_string_choice(self):
        for tool, name, spec in properties():
            if 'enum' in spec:
                with self.subTest(tool=tool, field=name):
                    self.assertEqual('string', spec.get('type'))
                    self.assertTrue(spec['enum'])

    def test_no_integer_field_is_negative(self):
        for tool, name, spec in properties():
            if spec.get('type') == 'integer':
                with self.subTest(tool=tool, field=name):
                    self.assertGreaterEqual(spec.get('minimum', 0), 0)

    def test_every_declared_bound_is_an_int(self):
        for tool, name, spec in properties():
            for key in ('minimum', 'maximum', 'minLength', 'maxLength', 'minItems', 'maxItems'):
                if key in spec:
                    with self.subTest(tool=tool, field=name, key=key):
                        self.assertIsInstance(spec[key], int)

    def test_an_object_field_names_its_properties(self):
        for tool, name, spec in properties():
            if spec.get('type') == 'object':
                with self.subTest(tool=tool, field=name):
                    self.assertIsInstance(spec.get('properties'), dict)
                    self.assertTrue(spec['properties'])

    def test_the_builder_reads_the_same_catalogue_the_route_serves(self):
        """``describe()`` is what ``/catalog`` returns: name, risk, schema, runner."""
        described = build_registry().describe()
        self.assertTrue(described)
        for entry in described:
            self.assertGreaterEqual(set(entry), {'name', 'risk', 'schema', 'runner'})


if __name__ == '__main__':
    unittest.main()
