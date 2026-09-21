import unittest
from platform_runtime.connector_contract import descriptor, public_descriptor


class ConnectorContractTests(unittest.TestCase):
    def test_supported_postgres_readonly_metadata(self):
        d = descriptor('db', {
            'driver': 'postgres_readonly',
            'capabilities': ['discover', 'validate', 'read'],
            'tables': {'customers': ['id']},
        }, live_drivers={'postgres_readonly'})
        self.assertEqual('configured_not_live_verified', d.status)
        self.assertEqual('1.0', public_descriptor(d)['contract_version'])

    def test_declarative_provider_never_looks_healthy(self):
        d = descriptor('crm', {'driver': 'bitrix24', 'capabilities': ['discover', 'validate', 'read']}, live_drivers=set())
        self.assertEqual('adapter_required', d.status)

    def test_unknown_driver_fails_closed(self):
        d = descriptor('x', {'driver': 'made_up', 'capabilities': ['read']})
        self.assertEqual('unsupported_driver', d.status)

    def test_invalid_contract_and_capability_rejected(self):
        with self.assertRaises(ValueError): descriptor('x', {'driver': 'mcp', 'contract_version': '99'})
        with self.assertRaises(ValueError): descriptor('x', {'driver': 'mcp', 'capabilities': ['write']})


class DeclaredBoundTests(unittest.TestCase):
    """Fazza 33: `connector_contract`'s own gates, none of which were pinned."""

    def test_the_bounded_string_gate_matches_its_siblings(self):
        from platform_runtime.connector_contract import _string
        from platform_runtime.database import contract
        for value in (' crm', 'crm ', 'c\x7frm', '', '   ', 'a' * 129):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    _string(value, 'x', 128)
                with self.assertRaises(ValueError):
                    contract.text(value)
        # The DEFAULT is what the call sites rely on, so it is exercised without an
        # explicit maximum -- a test that always passes 128 leaves the default free.
        self.assertEqual('a' * 128, _string('a' * 128, 'x'))
        with self.assertRaises(ValueError):
            _string('a' * 129, 'x')

    def test_the_list_ceiling_is_one_hundred(self):
        from platform_runtime.connector_contract import _strings
        _strings(['x%d' % index for index in range(100)], 'x')
        with self.assertRaises(ValueError):
            _strings(['x%d' % index for index in range(101)], 'x')
        with self.assertRaises(ValueError):
            _strings(['x', 'x'], 'x')

    def test_a_read_only_driver_may_not_advertise_a_write_capability(self):
        live = {'sqlite_readonly'}
        for capability in ('plan_write', 'execute_write', 'reconcile', 'revoke'):
            with self.subTest(capability=capability):
                with self.assertRaises(ValueError):
                    descriptor('c', {'driver': 'sqlite_readonly',
                                     'capabilities': ['read', capability]},
                               live_drivers=live)
        for capability in ('discover', 'validate', 'read'):
            with self.subTest(capability=capability):
                descriptor('c', {'driver': 'sqlite_readonly', 'capabilities': [capability]},
                           live_drivers=live)

    def test_the_capability_and_scope_ceilings(self):
        from platform_runtime.connector_contract import CAPABILITIES
        self.assertEqual(7, len(CAPABILITIES))
        # every declared capability is accepted, and an unknown one is not
        descriptor('c', {'driver': 'mcp', 'capabilities': sorted(CAPABILITIES)})
        with self.assertRaises(ValueError):
            descriptor('c', {'driver': 'mcp', 'capabilities': ['read', 'teleport']})

if __name__ == '__main__': unittest.main()
