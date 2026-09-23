"""Declared bounds of the Customer 360 data layer (§157).

``app/customer360.py`` was the most bound-dense module left in ``app/`` and almost
every one of its bounds was a bare literal: ``_text``'s defaults, the ceilings on
``_tenant``/``_id``/``_normalize_contact``, the paging window, and a ``LIMIT 100``
written out **four times** inside ``get_customer``.

Three shapes of finding, and the first two are why this phase was worth running.

**One number, two places, and whichever is tighter wins silently.** ``_id`` checks
``_text(..., maximum=128)`` and then ``_ID_RE``, whose quantifier was ``{1,128}``.
Two literals that must agree, with nothing making them: widening either one alone
changes nothing, because the other still binds. The regex is now built from
``MAX_ID_CHARS``, so the question is gone rather than tested — the same move as
``max(MIN_WINDOW, min_tasks)`` in §156.

**Dominating ceilings.** ``kind``, ``channel``, ``status`` and ``currency`` are all
length-checked and then immediately looked up in a small vocabulary or matched
against a regex. So their ceilings do not decide the accepted SET — they only bound
the work done before the lookup. That makes them reachable only by their *cause*,
and this module pins them that way: a 33-character ``kind`` is refused **for
length**, a 10-character one **for the vocabulary**. Two different causes, two
different paths — the §155 lesson ("a precondition being true is not the same as
the precondition being why") applied to a length check.

**One ceiling, four sites.** ``MAX_EMBEDDED_ROWS`` bounds each of the four
collections ``get_customer`` returns. It was ``LIMIT 100`` four times over, so
changing one of them would have left the other three behind, silently.

This module deliberately does NOT reach the API through HTTP. It lives in
``runtime_tests``, which ``scripts/verify_offline.py`` runs behind an audit hook
that raises on ``socket.connect``: ``TestClient`` would turn the offline gate red.
These are plain functions, so calling them directly measures the guards rather than
a framework's round-trip through them.
"""

import inspect
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

from app import customer360
from app import identity_store as identity
from app.customer360 import (
    CustomerError,
    CustomerNotFound,
    add_contact,
    add_order,
    create_customer,
    get_customer,
    link_channel_identity,
    list_customers,
)
from app.storage import db, reset

APP = Path(customer360.__file__).resolve().parent


def source(module):
    with Path(module.__file__).resolve().open(encoding='utf-8', newline='') as handle:
        return handle.read()


def code(module):
    """``source`` with the module docstring removed: a note about a literal is not it.

    §155 recorded this the hard way: an assertion that a string is ABSENT matches the
    comment that explains why it was removed.  The docstring in ``customer360`` quotes
    ``LIMIT 100`` on purpose, so the invariant has to be measured against the code.
    Splitting on a marker instead would depend on the marker surviving a rewrite.
    """
    text = source(module)
    return re.sub(r'^"""(?:.|\n)*?"""\n', '', text, count=1)


def digits(count):
    return ''.join(str(i % 10) for i in range(count))


class CustomerFixtureTests(unittest.TestCase):
    """One database per test, with the directory enforced.

    ``storage.reset()`` closes the connection; it does not delete the file, so a
    shared ``APP_DB`` would let one test read another's rows. ``IDENTITY_DIRECTORY``
    is on so the write guards in ``_writable`` are live rather than dev-open.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {
            'APP_DB': str(Path(self.tmp.name) / 'app.db'),
            'ENV': 'production',
            'IDENTITY_DIRECTORY': 'true',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        reset()
        self.addCleanup(reset)
        self.owner = identity.register_user('owner@example.invalid',
                                            'fixture password 123', 'Owner')
        for tenant in ('tenant-a', 'tenant-b', 'tt'):
            identity.create_workspace(self.owner['id'], tenant, tenant)
        self.actor = self.owner['id']

    def customer(self, tenant='tt', name='Ali'):
        return create_customer(tenant, name, actor=self.actor)


class DeclaredBoundTests(unittest.TestCase):
    """Every promoted number, pinned to its literal value."""

    def test_text_bounds(self):
        self.assertEqual(customer360.MIN_TEXT_CHARS, 1)
        self.assertEqual(customer360.MAX_TEXT_CHARS, 256)

    def test_identifier_bounds(self):
        self.assertEqual(customer360.MAX_ID_CHARS, 128)
        self.assertEqual(customer360.MAX_TENANT_CHARS, 64)
        self.assertEqual(customer360.MAX_EXTERNAL_REF_CHARS, 256)

    def test_contact_bounds(self):
        self.assertEqual(customer360.MAX_CONTACT_VALUE_CHARS, 512)
        self.assertEqual(customer360.MIN_PHONE_DIGITS, 7)

    def test_page_window_bounds(self):
        self.assertEqual(customer360.MIN_PAGE_LIMIT, 1)
        self.assertEqual(customer360.MAX_PAGE_LIMIT, 100)
        self.assertEqual(customer360.MAX_PAGE_OFFSET, 100_000)
        self.assertEqual(customer360.MAX_QUERY_CHARS, 256)

    def test_embedded_collection_ceiling(self):
        self.assertEqual(customer360.MAX_EMBEDDED_ROWS, 100)

    def test_prefilter_ceilings(self):
        self.assertEqual(customer360.MAX_KIND_CHARS, 32)
        self.assertEqual(customer360.MAX_CHANNEL_CHARS, 32)
        self.assertEqual(customer360.MAX_STATUS_CHARS, 32)
        self.assertEqual(customer360.MAX_CURRENCY_CHARS, 8)

    def test_order_total_ceiling(self):
        self.assertEqual(customer360.MAX_ORDER_TOTAL_MINOR, 10 ** 15)

    def test_the_window_is_ordered(self):
        self.assertLess(customer360.MIN_PAGE_LIMIT, customer360.MAX_PAGE_LIMIT)
        self.assertLess(customer360.MIN_TEXT_CHARS, customer360.MAX_TEXT_CHARS)
        self.assertGreater(customer360.MIN_PHONE_DIGITS, 0)
        self.assertGreater(customer360.MAX_ORDER_TOTAL_MINOR, 0)

    def test_the_defaults_are_the_declared_ones(self):
        text = inspect.signature(customer360._text).parameters
        self.assertEqual(text['minimum'].default, customer360.MIN_TEXT_CHARS)
        self.assertEqual(text['maximum'].default, customer360.MAX_TEXT_CHARS)
        self.assertEqual(
            inspect.signature(list_customers).parameters['limit'].default,
            customer360.MAX_PAGE_LIMIT)
        self.assertEqual(
            inspect.signature(list_customers).parameters['offset'].default, 0)


class IdentifierPairTests(unittest.TestCase):
    """``_ID_RE``'s quantifier and ``_id``'s ceiling are the same number."""

    def test_the_regex_ceiling_is_the_declared_one(self):
        self.assertIsNotNone(customer360._ID_RE.fullmatch('a' * customer360.MAX_ID_CHARS))
        self.assertIsNone(customer360._ID_RE.fullmatch('a' * (customer360.MAX_ID_CHARS + 1)))

    def test_the_text_ceiling_is_the_declared_one(self):
        self.assertEqual('a' * customer360.MAX_ID_CHARS,
                         customer360._id('a' * customer360.MAX_ID_CHARS))
        with self.assertRaises(CustomerError):
            customer360._id('a' * (customer360.MAX_ID_CHARS + 1))

    def test_the_two_sites_agree(self):
        """Whichever is tighter wins silently, so they must be the same number.

        Asserted as an agreement rather than as two values: a regex at 12 beside a
        ceiling at 128 accepts 12 characters and refuses nothing with an error that
        mentions 128.
        """
        widest = len('a' * customer360.MAX_ID_CHARS)
        self.assertIsNotNone(customer360._ID_RE.fullmatch('a' * widest))
        self.assertEqual(widest, len(customer360._id('a' * widest)))

    def test_the_allowed_identifier_characters(self):
        for value in ('a', 'A', '0', '_', '-', 'a-b_c9'):
            with self.subTest(value=value):
                self.assertEqual(value, customer360._id(value))

    def test_a_disallowed_identifier_character(self):
        for value in ('a b', 'a.b', 'a/b', "a'b", 'a%b'):
            with self.subTest(value=value):
                with self.assertRaises(CustomerError):
                    customer360._id(value)


class TextBoundTests(unittest.TestCase):
    def test_the_floor_is_one_character(self):
        self.assertEqual('a', customer360._text('a', 'x'))
        with self.assertRaises(CustomerError):
            customer360._text('', 'x')

    def test_whitespace_does_not_clear_the_floor(self):
        """The value is stripped before it is measured, so spaces are not content."""
        for value in ('', ' ', '   ', '\t', '\n'):
            with self.subTest(value=repr(value)):
                with self.assertRaises(CustomerError):
                    customer360._text(value, 'x')

    def test_the_ceiling_is_inclusive(self):
        limit = customer360.MAX_TEXT_CHARS
        self.assertEqual(limit, len(customer360._text('a' * limit, 'x')))
        with self.assertRaises(CustomerError):
            customer360._text('a' * (limit + 1), 'x')

    def test_a_non_string_is_refused(self):
        for value in (None, 1, 1.0, True, ['a'], {'a': 1}):
            with self.subTest(value=repr(value)):
                with self.assertRaises(CustomerError):
                    customer360._text(value, 'x')

    def test_the_tenant_ceiling(self):
        limit = customer360.MAX_TENANT_CHARS
        self.assertEqual('a' * limit, customer360._tenant('a' * limit))
        with self.assertRaises(CustomerError):
            customer360._tenant('a' * (limit + 1))


class ContactBoundTests(CustomerFixtureTests):
    def test_the_phone_floor(self):
        """One digit short is refused; at the floor it is accepted."""
        customer = self.customer()
        with self.assertRaises(CustomerError):
            add_contact('tt', customer['id'], 'phone',
                        digits(customer360.MIN_PHONE_DIGITS - 1), actor=self.actor)
        add_contact('tt', customer['id'], 'phone',
                    digits(customer360.MIN_PHONE_DIGITS), actor=self.actor)

    def test_the_separators_a_phone_is_written_with_are_dropped(self):
        """``+`` survives normalisation; it is part of the number, not decoration."""
        customer = self.customer()
        add_contact('tt', customer['id'], 'phone', '+998 90 123 45 67', actor=self.actor)
        stored = db().execute(
            "SELECT normalized FROM p_customer_contacts WHERE tenant=? AND customer_id=?",
            ('tt', customer['id'])).fetchone()['normalized']
        self.assertEqual('+998901234567', stored)

    def test_an_email_is_normalised_to_lower_case(self):
        customer = self.customer()
        add_contact('tt', customer['id'], 'email', 'Ali@Example.INVALID', actor=self.actor)
        stored = db().execute(
            "SELECT normalized FROM p_customer_contacts WHERE tenant=? AND customer_id=?",
            ('tt', customer['id'])).fetchone()['normalized']
        self.assertEqual('ali@example.invalid', stored)

    def test_the_contact_value_ceiling(self):
        customer = self.customer()
        limit = customer360.MAX_CONTACT_VALUE_CHARS
        add_contact('tt', customer['id'], 'email', 'a' * limit, actor=self.actor)
        with self.assertRaises(CustomerError):
            add_contact('tt', customer['id'], 'email', 'a' * (limit + 1), actor=self.actor)

    def test_an_unknown_contact_type_is_refused(self):
        customer = self.customer()
        with self.assertRaises(CustomerError):
            add_contact('tt', customer['id'], 'fax', 'x', actor=self.actor)

    def test_a_non_boolean_verified_is_refused(self):
        """``verified`` decides whether a contact is trusted, so it must be a real bool.

        ``verified=1`` is the shape a JSON body arrives in, and a truthiness test would
        take it; the guard asks ``isinstance(..., bool)`` and nothing exercised it.
        """
        customer = self.customer()
        for value in ('true', 1, 0, None, 'yes'):
            with self.subTest(value=repr(value)):
                with self.assertRaises(CustomerError):
                    add_contact('tt', customer['id'], 'other', 'note',
                                verified=value, actor=self.actor)

    def test_each_declared_contact_type_is_accepted(self):
        customer = self.customer()
        values = {'email': 'a@example.invalid', 'phone': '+998901234567',
                  'address': '1 Main Street', 'other': 'note'}
        for kind in sorted(customer360._ALLOWED_CONTACT_TYPES):
            with self.subTest(kind=kind):
                add_contact('tt', customer['id'], kind, values[kind], actor=self.actor)


class ExternalRefTests(CustomerFixtureTests):
    def test_the_ceiling_is_inclusive(self):
        limit = customer360.MAX_EXTERNAL_REF_CHARS
        create_customer('tt', 'At ceiling', external_ref='a' * limit, actor=self.actor)
        with self.assertRaises(CustomerError):
            create_customer('tt', 'Past ceiling', external_ref='a' * (limit + 1),
                            actor=self.actor)

    def test_an_external_ref_is_unique_per_tenant(self):
        create_customer('tt', 'One', external_ref='dup', actor=self.actor)
        with self.assertRaises(CustomerError):
            create_customer('tt', 'Two', external_ref='dup', actor=self.actor)
        create_customer('tenant-a', 'Three', external_ref='dup', actor=self.actor)


class TombstoneTests(CustomerFixtureTests):
    """``status!='deleted'`` is a guard against rows the API cannot create.

    ``create_customer`` refuses ``status='deleted'``, so no test that goes through the
    module can ever produce a tombstone -- which means all four ``status!='deleted'``
    predicates are unreachable from the API and were therefore unpinned. They are not
    decoration: the column is written by migrations and by the ERP import, and a row
    carrying it must stay invisible. The row is planted with SQL here because that is
    the only way it can exist.
    """

    def tombstone(self, tenant='tt'):
        row = create_customer(tenant, 'Gone', actor=self.actor)
        db().execute("UPDATE p_customers SET status='deleted' WHERE id=?", (row['id'],))
        db().commit()
        return row['id']

    def test_a_tombstone_is_not_readable(self):
        with self.assertRaises(CustomerNotFound):
            get_customer('tt', self.tombstone())

    def test_a_tombstone_is_not_listed(self):
        gone = self.tombstone()
        self.customer(name='Live')
        self.assertEqual(['Live'],
                         [r['display_name'] for r in list_customers('tt')])
        self.assertNotIn(gone, [r['id'] for r in list_customers('tt')])

    def test_a_tombstone_takes_no_new_children(self):
        """``_ensure_customer`` is the fourth site, and the one on the write path.

        Asserted on the EFFECT, not only the exception type: a refused call must leave
        no row.  Asserting only ``CustomerNotFound`` is not enough, because the function
        returns ``get_customer`` at the end -- and THAT raises on a tombstone too, after
        the child has already been written.  The matrix measured it: deleting the
        predicate left the suite green while an orphan row appeared.
        """
        gone = self.tombstone()
        for call, table in (
                (lambda: add_contact('tt', gone, 'other', 'note', actor=self.actor),
                 'p_customer_contacts'),
                (lambda: add_order('tt', gone, 'o-1', actor=self.actor),
                 'p_customer_orders'),
                (lambda: link_channel_identity('tt', gone, 'telegram', 'chat',
                                               verified=True, actor=self.actor),
                 'p_channel_identities')):
            with self.subTest(table=table):
                with self.assertRaises(CustomerNotFound):
                    call()
                written = db().execute(
                    f"SELECT count(*) FROM {table} WHERE customer_id=?",
                    (gone,)).fetchone()[0]
                self.assertEqual(0, written,
                                 f'the refusal must happen BEFORE the insert into {table}')


class WriteRoleTests(CustomerFixtureTests):
    """The write gate accepts two of the four declared roles, as an inline set.

    ``identity_store.ROLES`` is ``{owner, operator, integrator, viewer}``; ``_writable``
    spelled out ``{'owner','operator'}`` inside the guard.  Nothing related the two, so a
    role added to the vocabulary -- or the subset widened by a typo -- changed who may
    write customer data with no test in the way.  No fixture in any suite ever built a
    member whose role was neither an owner nor an operator.

    The membership row is planted with SQL for the same reason the tombstone is: the
    only route the API offers is an invitation round trip, and the role is what is being
    measured, not the invitation.
    """

    def member(self, role, tenant='tt'):
        user = identity.register_user(f'member-{role}@example.invalid',
                                      'fixture password 123', role.title())
        now = time.time()
        db().execute(
            "INSERT INTO p_memberships(workspace_id,user_id,role,status,version,created,"
            "updated) VALUES(?,?,?,?,?,?,?)",
            (tenant, user['id'], role, 'active', 1, now, now))
        db().commit()
        return user['id']

    def test_a_read_only_role_cannot_write(self):
        for role in ('viewer', 'integrator'):
            with self.subTest(role=role):
                with self.assertRaises(identity.AuthenticationError):
                    create_customer('tt', 'Denied', actor=self.member(role))

    def test_a_writing_role_can_write(self):
        """The subset must not be narrower either: both declared writers still write."""
        for role in ('owner', 'operator'):
            with self.subTest(role=role):
                create_customer('tt', f'Allowed {role}', actor=self.member(role))


class ExistenceGuardTests(CustomerFixtureTests):
    """The two write-path guards the revert matrix measured GREEN, and what pins them.

    Both are masked by the read-back: every mutator returns ``get_customer`` at the end,
    so removing a guard still raises ``CustomerNotFound`` -- one line later, and after the
    row has been written.  Asserting the exception TYPE cannot tell these apart; the pin
    is either the row that must NOT exist, or the message that says WHY.
    """

    def test_a_contact_cannot_be_added_to_a_customer_that_does_not_exist(self):
        """``_ensure_customer`` prevents an ORPHAN row, and nothing tested the row.

        With the guard the call refuses at the check; without it the INSERT succeeds and
        only the read-back of ``get_customer`` raises.  The difference is a contact row
        pointing at a customer that never was.
        """
        ghost = 'g' * 32
        with self.assertRaises(CustomerNotFound):
            add_contact('tt', ghost, 'other', 'note', actor=self.actor)
        written = db().execute(
            "SELECT count(*) FROM p_customer_contacts WHERE customer_id=?",
            (ghost,)).fetchone()[0]
        self.assertEqual(0, written,
                         'the existence check must precede the insert, not the read-back')

    def test_a_missing_actor_is_refused_as_a_missing_actor(self):
        """The actor check is FIRST on purpose, and its message is the cause.

        Deleting it still refuses -- an empty actor has no membership, so the NEXT line
        raises.  But the message changes from ``Write actor required`` to ``Write
        permission revoked``, and those are two different audit trails: a blank actor is
        a code bug, a wrong actor is a permission problem.
        """
        customer = self.customer()
        with self.assertRaises(identity.AuthenticationError) as caught:
            add_contact('tt', customer['id'], 'other', 'note')
        self.assertIn('Write actor required', str(caught.exception))


class PageWindowTests(CustomerFixtureTests):
    def test_the_limit_floor(self):
        for limit in (0, -1):
            with self.subTest(limit=limit):
                with self.assertRaises(CustomerError):
                    list_customers('tt', limit=limit)
        list_customers('tt', limit=customer360.MIN_PAGE_LIMIT)

    def test_the_limit_ceiling(self):
        list_customers('tt', limit=customer360.MAX_PAGE_LIMIT)
        with self.assertRaises(CustomerError):
            list_customers('tt', limit=customer360.MAX_PAGE_LIMIT + 1)

    def test_the_refusal_reports_the_declared_window(self):
        """Measured on the exception, not on the source: this is what a caller sees."""
        with self.assertRaises(CustomerError) as caught:
            list_customers('tt', limit=customer360.MAX_PAGE_LIMIT + 1)
        self.assertIn(
            f'{customer360.MIN_PAGE_LIMIT}..{customer360.MAX_PAGE_LIMIT}',
            str(caught.exception))

    def test_a_boolean_is_not_a_limit(self):
        """``True`` is an ``int`` in Python, so a naive range check would accept it."""
        for limit in (True, False):
            with self.subTest(limit=limit):
                with self.assertRaises(CustomerError):
                    list_customers('tt', limit=limit)

    def test_the_offset_window(self):
        list_customers('tt', offset=0)
        list_customers('tt', offset=customer360.MAX_PAGE_OFFSET)
        for offset in (-1, customer360.MAX_PAGE_OFFSET + 1):
            with self.subTest(offset=offset):
                with self.assertRaises(CustomerError):
                    list_customers('tt', offset=offset)

    def test_a_boolean_is_not_an_offset(self):
        """The same hole as ``limit``, one argument over.

        ``limit`` refuses a bool and ``offset`` refused it too, but only ``limit`` had a
        test -- so deleting the ``offset`` clause left the suite green while the two
        guards stopped agreeing.
        """
        for offset in (True, False):
            with self.subTest(offset=offset):
                with self.assertRaises(CustomerError):
                    list_customers('tt', offset=offset)

    def test_the_query_ceiling(self):
        list_customers('tt', query='a' * customer360.MAX_QUERY_CHARS)
        with self.assertRaises(CustomerError):
            list_customers('tt', query='a' * (customer360.MAX_QUERY_CHARS + 1))

    def test_a_like_wildcard_in_the_query_is_literal(self):
        """The query is escaped before it becomes a pattern, so ``%`` is a character.

        Without the escaping the pattern becomes ``%%%`` and matches every row, which
        turns a search into a full listing.
        """
        create_customer('tt', '100% Cotton', actor=self.actor)
        create_customer('tt', 'Plain', actor=self.actor)
        self.assertEqual(['100% Cotton'],
                         [r['display_name'] for r in list_customers('tt', query='%')])

    def test_an_underscore_in_the_query_is_literal(self):
        create_customer('tt', 'a_b', actor=self.actor)
        create_customer('tt', 'axb', actor=self.actor)
        self.assertEqual(['a_b'],
                         [r['display_name'] for r in list_customers('tt', query='a_b')])

    def test_the_escape_character_itself_is_escaped(self):
        """Three substitutions, and the third is the one nobody writes a test for.

        ``ESCAPE '\\'`` gives the backslash a meaning inside the pattern, so a backslash
        that arrived in the QUERY has to be doubled or it escapes the character after
        it: ``a\\b`` compiles to ``a\\b``, which is the single character ``b``, and a
        search for ``a\\b`` silently returns ``ab``.
        """
        create_customer('tt', 'a\\b', actor=self.actor)
        create_customer('tt', 'ab', actor=self.actor)
        self.assertEqual(['a\\b'],
                         [r['display_name'] for r in list_customers('tt', query='a\\b')])

    def test_the_offset_skips_whole_rows(self):
        for index in range(3):
            create_customer('tt', f'C{index}', actor=self.actor)
        self.assertEqual(3, len(list_customers('tt', limit=10)))
        self.assertEqual(1, len(list_customers('tt', limit=10, offset=2)))


class OrderBoundTests(CustomerFixtureTests):
    def test_the_total_floor_and_ceiling(self):
        customer = self.customer()
        for total in (0, 1, customer360.MAX_ORDER_TOTAL_MINOR):
            with self.subTest(total=total):
                add_order('tt', customer['id'], f'o-{total}', total_minor=total,
                          actor=self.actor)
        with self.assertRaises(CustomerError):
            add_order('tt', customer['id'], 'o-over',
                      total_minor=customer360.MAX_ORDER_TOTAL_MINOR + 1, actor=self.actor)

    def test_a_negative_total_is_refused(self):
        customer = self.customer()
        with self.assertRaises(CustomerError):
            add_order('tt', customer['id'], 'o-neg', total_minor=-1, actor=self.actor)

    def test_a_boolean_is_not_a_total(self):
        customer = self.customer()
        with self.assertRaises(CustomerError):
            add_order('tt', customer['id'], 'o-bool', total_minor=True, actor=self.actor)

    def test_the_currency_regex_is_three_letters(self):
        customer = self.customer()
        add_order('tt', customer['id'], 'cur-ok', currency='UZS', actor=self.actor)
        add_order('tt', customer['id'], 'cur-case', currency='uzs', actor=self.actor)
        for currency in ('UZ', 'UZSX', 'U', ''):
            with self.subTest(currency=currency):
                with self.assertRaises(CustomerError):
                    add_order('tt', customer['id'], f'cur-{currency}', currency=currency,
                              actor=self.actor)

    def test_the_currency_is_upper_cased_before_matching(self):
        customer = self.customer()
        add_order('tt', customer['id'], 'cur-lower', currency='uzs', actor=self.actor)
        order = get_customer('tt', customer['id'])['orders'][0]
        self.assertEqual('UZS', order['currency'])

    def test_each_declared_order_status_is_accepted(self):
        customer = self.customer()
        for index, status in enumerate(sorted(customer360._ORDER_STATUSES)):
            with self.subTest(status=status):
                add_order('tt', customer['id'], f'st-{index}', status=status,
                          actor=self.actor)

    def test_an_unknown_order_status_is_refused(self):
        customer = self.customer()
        with self.assertRaises(CustomerError):
            add_order('tt', customer['id'], 'st-bad', status='shipped', actor=self.actor)

    def test_the_customer_status_vocabulary_excludes_deleted(self):
        """``deleted`` is a tombstone: it must not be settable through the API."""
        for status in sorted(customer360._ALLOWED_STATUS - {'deleted'}):
            with self.subTest(status=status):
                create_customer('tt', f'S-{status}', status=status, actor=self.actor)
        with self.assertRaises(CustomerError):
            create_customer('tt', 'S-deleted', status='deleted', actor=self.actor)


class DominatingCeilingTests(CustomerFixtureTests):
    """A pre-filter ceiling is reachable only by its CAUSE, never by its set.

    ``kind``, ``channel``, ``status`` and ``currency`` are length-checked and then
    looked up in a small vocabulary (or matched by a regex). So a value one
    character past the ceiling and a value that is merely unknown both end in the
    same exception TYPE, and a test that only asserted ``CustomerError`` would pin
    neither. These separate the two causes by their message.
    """

    LENGTH = "uzunligi noto'g'ri"

    def long_and_unknown(self, ceiling, call, unknown):
        """Return (long_error, unknown_error) for one pre-filter ceiling."""
        with self.assertRaises(CustomerError) as long:
            call('x' * (ceiling + 1))
        with self.assertRaises(CustomerError) as short:
            call(unknown)
        return str(long.exception), str(short.exception)

    def test_the_kind_ceiling_is_the_length_cause(self):
        customer = self.customer()
        long_error, unknown_error = self.long_and_unknown(
            customer360.MAX_KIND_CHARS,
            lambda v: add_contact('tt', customer['id'], v, 'x', actor=self.actor),
            'x' * 10)
        self.assertIn(self.LENGTH, long_error)
        self.assertNotIn(self.LENGTH, unknown_error)

    def test_the_channel_ceiling_is_the_length_cause(self):
        customer = self.customer()
        long_error, unknown_error = self.long_and_unknown(
            customer360.MAX_CHANNEL_CHARS,
            lambda v: link_channel_identity('tt', customer['id'], v, 'e',
                                            verified=True, actor=self.actor),
            'x' * 10)
        self.assertIn(self.LENGTH, long_error)
        self.assertNotIn(self.LENGTH, unknown_error)

    def test_the_status_ceiling_is_the_length_cause(self):
        customer = self.customer()
        long_error, unknown_error = self.long_and_unknown(
            customer360.MAX_STATUS_CHARS,
            lambda v: add_order('tt', customer['id'], 'st', status=v, actor=self.actor),
            'x' * 10)
        self.assertIn(self.LENGTH, long_error)
        self.assertNotIn(self.LENGTH, unknown_error)

    def test_the_currency_ceiling_is_the_length_cause(self):
        customer = self.customer()
        long_error, unknown_error = self.long_and_unknown(
            customer360.MAX_CURRENCY_CHARS,
            lambda v: add_order('tt', customer['id'], 'cur', currency=v, actor=self.actor),
            'AAAA')
        self.assertIn(self.LENGTH, long_error)
        self.assertNotIn(self.LENGTH, unknown_error)

    def test_the_kind_ceiling_is_below_the_unknown_value_it_must_not_accept(self):
        """The two probes must be genuinely different lengths, or the test is vacuous."""
        self.assertGreater(10, 0)
        self.assertLess(10, customer360.MAX_KIND_CHARS + 1)
        self.assertLess(10, customer360.MAX_CHANNEL_CHARS + 1)
        self.assertLess(10, customer360.MAX_STATUS_CHARS + 1)


class EmbeddedCollectionTests(CustomerFixtureTests):
    def test_each_collection_is_bounded_by_the_declared_ceiling(self):
        """One ceiling, four collections -- and it was four separate literals.

        Measured by over-filling the cheapest one (orders): a ceiling that had been
        changed in only one of the four SQL strings would still be 100 here for
        orders, but not for the other three.
        """
        customer = create_customer('tenant-a', 'Big', actor=self.actor)
        total = customer360.MAX_EMBEDDED_ROWS + 1
        for index in range(total):
            add_order('tenant-a', customer['id'], f'bulk-{index:04d}',
                      total_minor=index, actor=self.actor)
        detail = get_customer('tenant-a', customer['id'])
        self.assertEqual(customer360.MAX_EMBEDDED_ROWS, len(detail['orders']))

    def test_the_ceiling_is_what_stops_the_list(self):
        """One below the ceiling returns everything: the bound is a cap, not a cut."""
        customer = create_customer('tenant-a', 'Small', actor=self.actor)
        for index in range(customer360.MAX_EMBEDDED_ROWS - 1):
            add_order('tenant-a', customer['id'], f'few-{index:04d}', actor=self.actor)
        detail = get_customer('tenant-a', customer['id'])
        self.assertEqual(customer360.MAX_EMBEDDED_ROWS - 1, len(detail['orders']))

    def test_the_contact_collection_is_bounded_too(self):
        customer = self.customer()
        total = customer360.MAX_EMBEDDED_ROWS + 1
        for index in range(total):
            add_contact('tt', customer['id'], 'other', f'note {index}', actor=self.actor)
        self.assertEqual(customer360.MAX_EMBEDDED_ROWS,
                         len(get_customer('tt', customer['id'])['contacts']))

    def test_every_embedded_collection_is_returned(self):
        """All four keys are present even when empty, so a caller can rely on them."""
        customer = self.customer()
        detail = get_customer('tt', customer['id'])
        for key in ('contacts', 'channel_identities', 'conversations', 'orders'):
            with self.subTest(key=key):
                self.assertEqual([], detail[key])


class NoUnnamedBoundTests(unittest.TestCase):
    """The invariant that keeps the promoted names from drifting back to literals.

    Every assertion here runs against ``code(customer360)``, not ``source``: this
    module's own docstring quotes the literals it removed, and a fix that writes a note
    about the literal will satisfy an absence check if the check reads the note.
    """

    def test_the_identifier_ceiling_is_used_in_both_sites(self):
        """The pair that must agree is now one source: the regex is built from it."""
        text = code(customer360)
        self.assertIn('_ID_RE = re.compile(rf"^[A-Za-z0-9_-]{{1,{MAX_ID_CHARS}}}$")', text)
        self.assertIn('_text(value, name, maximum=MAX_ID_CHARS)', text)

    def test_the_text_ceiling_names_the_constant(self):
        text = code(customer360)
        for literal in ('maximum=256', 'maximum=512', 'maximum=64',
                        'maximum=32', 'maximum=8'):
            with self.subTest(literal=literal):
                self.assertNotIn(literal, text)

    def test_the_embedded_ceiling_names_the_constant_everywhere(self):
        """Four SQL strings; a literal in any of them would drift on its own."""
        text = code(customer360)
        self.assertNotIn('LIMIT 100', text)
        self.assertEqual(4, text.count('LIMIT {MAX_EMBEDDED_ROWS}'))

    def test_the_paging_message_is_built_from_the_constants(self):
        """The window is written in a THIRD place: the text the caller is refused with.

        A message that hard-codes ``1..100`` keeps saying 100 after ``MAX_PAGE_LIMIT``
        moves, so the caller is handed a bound that is no longer the bound.  Nothing
        imports the message, so nothing would have caught it.
        """
        text = code(customer360)
        self.assertNotIn('1..100', text)
        self.assertIn(
            "f\"limit {MIN_PAGE_LIMIT}..{MAX_PAGE_LIMIT} bo'lishi kerak\"", text)

    def test_the_order_status_vocabulary_is_named_and_used(self):
        text = code(customer360)
        self.assertIn('_ORDER_STATUSES = {"new", "pending", "paid", "cancelled", '
                      '"refunded", "fulfilled"}', text)
        self.assertIn('not in _ORDER_STATUSES', text)
        self.assertEqual(1, text.count('"refunded"'),
                         'the order vocabulary must be spelled exactly once')

    def test_the_currency_regex_is_named_and_used(self):
        text = code(customer360)
        self.assertIn('_CURRENCY_RE = re.compile(r"[A-Z]{3}")', text)
        self.assertIn('_CURRENCY_RE.fullmatch(currency)', text)
        self.assertEqual(1, text.count('r"[A-Z]{3}"'))

    def test_the_write_roles_are_named_and_a_subset_of_the_declared_roles(self):
        """A subset is a relationship, and nothing stated it.

        The guard used to hold the set itself, so it could not be related to anything.
        Asserted as a subset rather than as two values: adding ``viewer`` to the gate is
        the failure that matters, and a role added to ``identity_store.ROLES`` should not
        silently appear here.
        """
        text = code(customer360)
        self.assertIn('WRITE_ROLES = frozenset({"owner", "operator"})', text)
        self.assertIn("m['role'] not in WRITE_ROLES", text)
        self.assertLessEqual(set(customer360.WRITE_ROLES), set(identity.ROLES))
        self.assertEqual({'owner', 'operator'}, set(customer360.WRITE_ROLES))

    def test_the_page_guards_name_their_constants(self):
        text = code(customer360)
        for pattern in (r'MIN_PAGE_LIMIT <= limit <= MAX_PAGE_LIMIT',
                        r'0 <= offset <= MAX_PAGE_OFFSET',
                        r'len\(query\) > MAX_QUERY_CHARS',
                        r'len\(external_ref\) > MAX_EXTERNAL_REF_CHARS',
                        r'len\(normalized\) < MIN_PHONE_DIGITS',
                        r'total_minor > MAX_ORDER_TOTAL_MINOR'):
            with self.subTest(pattern=pattern):
                self.assertRegex(text, pattern)

    def test_the_module_carries_no_bare_number_in_a_guard(self):
        """A number compared against in a guard is a bound that lost its name."""
        hits = [line.strip() for line in code(customer360).splitlines()
                if re.search(r'(?:<=|>=|<|>)\s*-?[1-9]', line)
                and not line.strip().startswith('#')]
        self.assertEqual([], hits, f'name these bounds: {hits}')


if __name__ == '__main__':
    unittest.main()
