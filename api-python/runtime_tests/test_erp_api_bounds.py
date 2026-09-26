"""Declared bounds of the ERP posting HTTP surface (app/erp_api.py).

The newest HTTP layer arrived with three inline numbers (the page limit, the
evidence ceiling, the external-id ceiling) and two inline role tuples. The HTTP
behaviour is `integration_tests/test_erp_api.py`'s; this file is the offline owner
of the numbers and of the two-authority shape -- the reconcile route is owner-only
and the read route is owner/operator, and a mutation that widens either must fail
here rather than in a review.

The request body is a pydantic model, so its bounds are exercised directly: no
server, no socket, no database.
"""
import ast
import os
import unittest
from pathlib import Path

from pydantic import ValidationError

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

from app import erp_api
from app.erp_api import ErpReconcile, POSTING_STATUSES


def source():
    return Path(erp_api.__file__).read_text(encoding='utf-8')


class DeclaredBoundTests(unittest.TestCase):
    def test_the_posting_limit(self):
        self.assertEqual(erp_api.POSTING_LIMIT, 100)

    def test_the_evidence_ceiling(self):
        self.assertEqual(erp_api.MAX_EVIDENCE_CHARS, 1000)

    def test_the_external_id_ceiling(self):
        self.assertEqual(erp_api.MAX_EXTERNAL_ID_CHARS, 128)

    def test_the_statuses_are_the_five_the_ledger_uses(self):
        self.assertEqual(set(POSTING_STATUSES.__args__),
                         {'uncertain', 'unconfirmed', 'posting', 'failed', 'posted'})

    def test_no_inline_literal_survives_in_the_code(self):
        """The module docstring may restate the numbers; the code may not."""
        text = source()
        code = text.replace(ast.get_docstring(ast.parse(text), clean=False) or '', '', 1)
        for literal in ('POSTING_LIMIT = 100', 'MAX_EVIDENCE_CHARS = 1000',
                        'MAX_EXTERNAL_ID_CHARS = 128'):
            self.assertIn(literal, code)
        for inline in ('LIMIT 100', 'max_length=1000', 'max_length=128'):
            self.assertNotIn(inline, code)

    def test_the_two_authority_shapes_are_pinned(self):
        """The read is owner/operator; resolving an unknown posting is owner-only."""
        text = source()
        self.assertIn("api.identity(request, tenant, ('owner', 'operator'))", text)
        self.assertIn("api.identity(request, tenant, ('owner',))", text)


class ReconcileBodyTests(unittest.TestCase):
    def body(self, **overrides):
        fields = dict(outcome='posted', evidence='bank statement, line 7')
        fields.update(overrides)
        return ErpReconcile(**fields)

    def test_a_minimal_body_defaults_the_external_id(self):
        self.assertEqual(self.body().external_id, '')

    def test_the_evidence_ceiling_is_the_declared_one(self):
        self.body(evidence='x' * erp_api.MAX_EVIDENCE_CHARS)
        with self.assertRaises(ValidationError):
            self.body(evidence='x' * (erp_api.MAX_EVIDENCE_CHARS + 1))
        with self.assertRaises(ValidationError):
            self.body(evidence='')

    def test_the_external_id_ceiling(self):
        self.body(external_id='x' * erp_api.MAX_EXTERNAL_ID_CHARS)
        with self.assertRaises(ValidationError):
            self.body(external_id='x' * (erp_api.MAX_EXTERNAL_ID_CHARS + 1))

    def test_the_outcome_is_a_closed_set(self):
        for outcome in ('posted', 'failed'):
            with self.subTest(outcome=outcome):
                self.assertEqual(self.body(outcome=outcome).outcome, outcome)
        with self.assertRaises(ValidationError):
            self.body(outcome='maybe')

    def test_extra_fields_are_forbidden(self):
        with self.assertRaises(ValidationError):
            ErpReconcile(outcome='posted', evidence='x', agent='ops.assistant')


if __name__ == '__main__':
    unittest.main()
