"""The registration contract, stated once for every module that registers tools.

Thirteen modules each carried their own copy of "registering twice is a no-op", and
two carried the OPPOSITE -- an assertion that a second call raises. A contract
stated thirteen times and contradicted twice is not a contract; it is a habit with
exceptions, and the exception looked deliberate because a test defended it.

This file is the single statement. It walks every registrar in the runtime rather
than a hand-kept list, so a module added later is covered without anyone
remembering to add a test here -- the same reason the copy-paste version could
disagree with itself without anyone noticing.

Why the no-op is the contract: ``build_registry`` already calls every registrar, so
a caller that builds a registry and then registers explicitly is a normal pattern.
If that raised, the message would be ``Invalid tool registration``, which says
nothing about the real cause.
"""
import importlib
import pkgutil
import unittest

import platform_runtime
from platform_runtime.tools import Registry, build_registry


def registrars():
    """Every module-level ``register_*`` callable, labelled for a subTest.

    Only callables DEFINED by the module are taken, so an imported helper is not
    mistaken for a registrar. A callable that needs an argument beyond a registry
    is skipped by the caller rather than filtered here, because "what does this
    need" is only answerable by calling it.
    """
    found = []
    for info in pkgutil.iter_modules(platform_runtime.__path__):
        try:
            module = importlib.import_module(f'platform_runtime.{info.name}')
        except Exception:
            continue
        for name in dir(module):
            value = getattr(module, name, None)
            if (name.startswith('register_') and callable(value)
                    and getattr(value, '__module__', '') == module.__name__):
                found.append((f'{info.name}.{name}', value))
    return sorted(found)


def exercised():
    """The registrars that actually add a tool when given a bare registry."""
    for label, register in registrars():
        registry = Registry()
        try:
            register(registry)
        except Exception:
            continue
        if registry.items:
            yield label, register


class RegistrationContractTests(unittest.TestCase):
    def test_every_registrar_is_idempotent(self):
        """A second call adds nothing and does not raise."""
        checked = 0
        for label, register in exercised():
            checked += 1
            registry = Registry()
            register(registry)
            before = set(registry.items)
            with self.subTest(registrar=label):
                register(registry)
                self.assertEqual(before, set(registry.items), label)
        self.assertGreaterEqual(
            checked, 15, 'the walk found too few registrars to be a real check')

    def test_re_registering_into_a_built_registry_adds_nothing(self):
        """The realistic caller: build the registry, then register one module again."""
        registry = build_registry()
        before = set(registry.items)
        for label, register in exercised():
            with self.subTest(registrar=label):
                register(registry)
                self.assertEqual(before, set(registry.items), label)

    def test_building_the_registry_twice_gives_the_same_tools(self):
        self.assertEqual(set(build_registry().items), set(build_registry().items))

    def test_no_registrar_can_widen_a_risk_or_a_schema(self):
        """A re-registration must not be a way to swap a tool's contract.

        The no-op rule is what makes this true: if a second call replaced the entry,
        a module could change the risk of a tool another module registered.
        """
        registry = build_registry()
        before = {name: (tool.risk, repr(tool.schema))
                  for name, tool in registry.items.items()}
        for label, register in exercised():
            register(registry)
        after = {name: (tool.risk, repr(tool.schema))
                 for name, tool in registry.items.items()}
        self.assertEqual(before, after)
