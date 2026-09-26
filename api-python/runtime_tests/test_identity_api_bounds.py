"""The identity HTTP surface's declared bounds (§163).

``runtime_tests/test_app_layer_bounds.py`` (§155) declared the app layer's *unnamed*
limits.  This module covers a different defect in the same layer, and the difference is
why it is a separate file: these bounds were not unnamed, they were **named twice**.

``app/identity_api.py`` restated seven of ``identity_store``'s numbers as its own
pydantic constraints -- e-mail 320, password 1/256, token 20/256, name 1/256, workspace
2/64, invitation TTL 300/604800.  Both copies were correct, which is exactly the problem:
a duplicated bound that agrees is indistinguishable from a pin until somebody raises one
side.  Raising ``MAX_EMAIL_CHARS`` in the store would have left the HTTP layer refusing
at 320 with no test failing on either half, and the two files would have disagreed in
production while each looked self-consistent.

So the primary fix was structural, not a test: the models now *read* the owner's
constants.  What remains to pin is (a) that they still do -- a future edit could
re-introduce a literal -- and (b) the handful of bounds that genuinely have one owner
here: the per-client login budget, the admin-token floor, the access-token cap, and the
two regex-derived floors.

Two of these cannot be derived, only *pinned to agree*, and both are labelled as the
weaker instrument rather than quietly counted as equivalent:

* the e-mail and workspace floors, which are the shapes ``identity_store``'s regexes
  accept -- §155 deliberately declined to build those patterns from f-strings;
* ``InviteRequest.role``'s ``Literal``, because pydantic builds the OpenAPI enum from
  literal members and cannot take a set.

Everything else is either a structural read or a behavioural call.

Offline constraints inherited from §155 and observed here: no ``socket``, and no event
loop -- on Windows a loop builds its self-pipe from ``socket.socketpair()``, which the
audit hook in ``scripts/verify_offline.py`` refuses.  Every route helper below
(``rate``, ``tokens``, ``admin``, ``claims``, ``invoke``) is a plain function, so this
suite calls them directly.  The async routes themselves belong to
``integration_tests/test_identity_http.py``, which is outside the offline gate for
exactly that reason.
"""

import os
import re
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from typing import get_args
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

from app import auth, identity_api as api
from app import identity_store as store
from app.storage import reset

SOURCE = Path(api.__file__).resolve().read_text(encoding='utf-8', newline='')
STORE_SOURCE = Path(store.__file__).resolve().read_text(encoding='utf-8', newline='')


def code_only(source):
    """``source`` with whole-line comments removed.

    A source scan that reads prose will find the literal it is looking for inside the
    sentence explaining why the literal is gone -- which is what the first draft of
    ``MintedTokenEntropyTests`` did, failing against ``identity_store``'s own comment.
    ``NoRestatedBoundTests`` already skipped comment lines for the same reason.
    """
    return '\n'.join(line for line in source.splitlines()
                     if not line.lstrip().startswith('#'))


STORE_CODE = code_only(STORE_SOURCE)


class _Client:
    host = '203.0.113.7'


class _Headers(dict):
    """``getlist`` is the one header method ``client_ip.request_client`` reaches for."""

    def getlist(self, name):
        value = self.get(name)
        return [] if value is None else [value]


class _Request:
    def __init__(self, headers=None):
        self.client = _Client()
        self.headers = _Headers(headers or {})


def string_bounds(schema, field):
    """The min/max a client sees for one field, through ``anyOf`` if it is nullable.

    Read from ``model_json_schema`` rather than ``model_fields`` on purpose: the schema
    is what the OpenAPI document publishes, so this pins the contract a client actually
    codes against instead of an attribute that could drift from it.
    """
    node = schema['properties'][field]
    if 'anyOf' in node:
        node = next(arm for arm in node['anyOf'] if arm.get('type') == 'string')
    return node.get('minLength'), node.get('maxLength')


class NoRestatedBoundTests(unittest.TestCase):
    """No numeric literal may declare a length bound in this file again.

    This is the regression guard for the whole section.  It is a source scan rather than
    a behavioural test because the failure it prevents has no behaviour: a literal that
    happens to equal the owner's constant passes every functional test while being a
    second source of truth.
    """

    CONSTRAINT = r'(?:min_length|max_length|ge|le)\s*=\s*-?\d'

    def test_field_constraints_are_never_literals(self):
        offenders = []
        for line in SOURCE.splitlines():
            if line.lstrip().startswith('#'):
                continue
            if re.search(self.CONSTRAINT, line):
                offenders.append(line.strip())
        self.assertEqual([], offenders,
                         'these bounds must be read from their owner, not restated')

    def test_the_models_hold_no_own_ceiling(self):
        """Belt-and-braces: no integer survives in the model block at all."""
        block = SOURCE.split('class AcceptInviteRequest', 1)[0]
        block = block.split('class Credentials', 1)[-1]
        stripped = [line for line in block.splitlines()
                    if not line.lstrip().startswith('#')]
        self.assertEqual([], [line.strip() for line in stripped
                              if re.search(r'(?<![A-Za-z_])\d+(?![A-Za-z_])', line)],
                         'a number in the model block is a second source')


class DerivedFloorTests(unittest.TestCase):
    """The two floors that have no owner constant -- pinned to agree, not derived.

    ``identity_store``'s ``EMAIL_RE`` and ``SLUG_RE`` declare their bounds *inside* the
    pattern.  §155 considered building those patterns from f-strings so the numbers could
    be named, and declined: a regex assembled from interpolation is harder to read than
    the bound is worth.  The consequence is honest and stated here -- these floors are a
    *second* declaration checked against the first, not a single one.  If ``EMAIL_RE``
    were widened to ``{0,128}`` this suite goes red, which is the point, but the two
    would have drifted before it did.
    """

    def test_the_email_floor_is_the_shortest_address_the_regex_accepts(self):
        self.assertTrue(store.EMAIL_RE.fullmatch(
            'a' * (api.MIN_EMAIL_CHARS - 2) + '@b'),
            'MIN_EMAIL_CHARS is above what the store accepts')
        self.assertIsNone(store.EMAIL_RE.fullmatch(
            'a' * (api.MIN_EMAIL_CHARS - 3) + '@b'),
            'MIN_EMAIL_CHARS is below what the store accepts')

    def test_the_email_ceiling_is_the_stores(self):
        self.assertEqual(store.MAX_EMAIL_CHARS, api.Credentials.model_json_schema()
                         ['properties']['email']['maxLength'])

    def test_the_workspace_floor_is_the_shortest_slug_the_regex_accepts(self):
        self.assertTrue(store.SLUG_RE.fullmatch('a' * store.MIN_WORKSPACE_ID_CHARS))
        self.assertIsNone(store.SLUG_RE.fullmatch('a' * (store.MIN_WORKSPACE_ID_CHARS - 1)))

    def test_the_workspace_ceiling_is_the_stores(self):
        # Through the store's own validator, not the regex's 64 -- that validator is
        # what a client of this API will actually be refused by.
        longest = 'a' * store.MAX_WORKSPACE_ID_CHARS
        self.assertEqual(longest, store._workspace_id(longest))
        with self.assertRaises(store.IdentityError):
            store._workspace_id(longest + 'b')

    def test_the_models_publish_the_stores_bounds(self):
        """Every restated bound now resolves to the owner's number, via OpenAPI."""
        cases = [
            (api.RegisterRequest, 'email', api.MIN_EMAIL_CHARS, store.MAX_EMAIL_CHARS),
            (api.RegisterRequest, 'password', store.MIN_CANDIDATE_PASSWORD_CHARS,
             store.MAX_PASSWORD_CHARS),
            (api.RegisterRequest, 'display_name', store.MIN_NAME_CHARS,
             store.MAX_NAME_CHARS),
            (api.RegisterRequest, 'invitation_token', store.MIN_TOKEN_CHARS,
             store.MAX_TOKEN_CHARS),
            (api.BootstrapRequest, 'workspace_id', store.MIN_WORKSPACE_ID_CHARS,
             store.MAX_WORKSPACE_ID_CHARS),
            (api.BootstrapRequest, 'workspace_name', store.MIN_NAME_CHARS,
             store.MAX_NAME_CHARS),
            (api.LoginRequest, 'workspace_id', store.MIN_WORKSPACE_ID_CHARS,
             store.MAX_WORKSPACE_ID_CHARS),
            (api.RefreshRequest, 'refresh_token', store.MIN_TOKEN_CHARS,
             store.MAX_TOKEN_CHARS),
            (api.WorkspaceRequest, 'user_id', api.MIN_USER_ID_CHARS,
             api.MAX_USER_ID_CHARS),
            (api.WorkspaceRequest, 'workspace_id', store.MIN_WORKSPACE_ID_CHARS,
             store.MAX_WORKSPACE_ID_CHARS),
            (api.WorkspaceRequest, 'name', store.MIN_NAME_CHARS, store.MAX_NAME_CHARS),
            (api.InviteRequest, 'email', api.MIN_EMAIL_CHARS, store.MAX_EMAIL_CHARS),
            (api.AcceptInviteRequest, 'token', store.MIN_TOKEN_CHARS,
             store.MAX_TOKEN_CHARS),
        ]
        for model, field, floor, ceiling in cases:
            with self.subTest(model=model.__name__, field=field):
                self.assertEqual((floor, ceiling),
                                 string_bounds(model.model_json_schema(), field))

    def test_the_invitation_window_is_the_stores(self):
        schema = api.InviteRequest.model_json_schema()['properties']['ttl_seconds']
        self.assertEqual(store.INVITATION_TTL_SECONDS, schema['default'])
        self.assertEqual(store.MIN_INVITATION_TTL_SECONDS, schema['minimum'])
        self.assertEqual(store.MAX_INVITATION_TTL_SECONDS, schema['maximum'])

    def test_a_ttl_past_the_window_is_refused_by_the_model(self):
        for ttl in (store.MIN_INVITATION_TTL_SECONDS - 1,
                    store.MAX_INVITATION_TTL_SECONDS + 1):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValidationError):
                    api.InviteRequest(email='a@b.co', role='operator', ttl_seconds=ttl)

    def test_the_role_enum_is_the_stores_roles_minus_owner(self):
        """Pinned to agree, and labelled as such.

        ``Literal`` needs its members literally -- pydantic builds the published OpenAPI
        ``enum`` from them -- so this set cannot be constructed from ``store.ROLES`` the
        way the numbers above are.  The weaker instrument, stated rather than implied.
        """
        annotation = api.InviteRequest.model_fields['role'].annotation
        self.assertEqual(store.ROLES - {'owner'}, set(get_args(annotation)))
        # Declaration order, not sorted: pydantic publishes the Literal's own order, so
        # this pins the enum a client sees rather than a set that happens to match it.
        self.assertEqual(list(get_args(annotation)),
                         api.InviteRequest.model_json_schema()
                         ['properties']['role']['enum'])

    def test_an_unknown_role_is_refused_by_the_model(self):
        with self.assertRaises(ValidationError):
            api.InviteRequest(email='a@b.co', role='owner')


class BearerPrefixTests(unittest.TestCase):
    """One fact, one place: the scheme string and the slice that removes it.

    ``claims`` tested ``'Bearer '`` and then sliced ``auth[7:]``.  Seven is the length of
    that literal, so the two agreed -- until the day a second scheme is accepted and the
    slice silently eats seven characters of a different prefix.  Deriving the slice from
    the constant makes that impossible; this pins that it still is.
    """

    def test_the_slice_is_derived_from_the_constant(self):
        self.assertIn('auth[len(BEARER_PREFIX):]', SOURCE)
        self.assertNotIn('auth[7:]', SOURCE)
        self.assertEqual(len('Bearer '), len(api.BEARER_PREFIX))

    def slice_of(self, header):
        """What ``claims`` hands to ``verify_claims``, captured at the boundary.

        ``verify_claims`` is replaced rather than satisfied: it delegates to
        ``authorize_claims``, which checks the session against the database, so a real
        call would need a persisted user and session.  That is integration territory and
        ``integration_tests/test_identity_http.py`` already does it.  What this class
        pins is narrower and needs no database -- that the prefix removed is exactly the
        prefix tested, which is what a literal ``7`` could only do by coincidence.
        """
        seen = {}

        def capture(token):
            seen['token'] = token
            return {'sub': 'usr_1', 'sid': 'ses_1', 'token_type': 'user'}

        with patch.object(api, 'verify_claims', capture):
            api.claims(_Request({'Authorization': header}))
        return seen['token']

    def test_the_prefix_is_removed_and_nothing_else(self):
        token = 'header.payload.signature'
        self.assertEqual(token, self.slice_of(api.BEARER_PREFIX + token))

    def test_a_scheme_of_a_different_length_would_not_be_miscounted(self):
        """The failure a literal 7 permits, made concrete.

        ``auth[7:]`` on ``'BearerX abc'`` would hand over ``'abc'`` -- the right answer
        for the wrong reason -- while on ``'Bear ab'`` it would hand over ``'b'``.  With
        the slice derived from the constant, neither header matches the prefix at all.
        """
        for header in ('BearerX abc', 'Bear ab'):
            with self.subTest(header=header):
                with self.assertRaises(HTTPException) as caught:
                    api.claims(_Request({'Authorization': header}))
                self.assertEqual(401, caught.exception.status_code)

    def test_a_bare_token_is_refused(self):
        with self.assertRaises(HTTPException) as caught:
            api.claims(_Request({'Authorization': 'header.payload.signature'}))
        self.assertEqual(401, caught.exception.status_code)

    def test_a_session_token_without_a_sid_is_refused(self):
        with patch.object(api, 'verify_claims',
                          lambda token: {'sub': 'usr_1', 'token_type': 'user'}):
            with self.assertRaises(HTTPException) as caught:
                api.claims(_Request({'Authorization': api.BEARER_PREFIX + 'a.b.c'}))
            self.assertEqual(401, caught.exception.status_code)


class ClientThrottleTests(unittest.TestCase):
    """The per-client login budget, measured rather than asserted.

    This was a bare positional ``60`` at the call site -- ``store.throttle``'s third
    argument, which is the LIMIT and reads like a window.  No offline test could address
    it, and the number is the whole difference between one attacker locking out a NAT
    pool and one attacker locking out one account.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'APP_DB': str(Path(self.tmp.name) / 'app.db')})
        env.start()
        self.addCleanup(env.stop)
        reset()
        self.addCleanup(reset)

    def test_the_limit_is_inclusive_then_exclusive(self):
        request = _Request()
        for _ in range(api.CLIENT_THROTTLE_LIMIT):
            api.rate(request, 'login')
        with self.assertRaises(HTTPException) as caught:
            api.rate(request, 'login')
        self.assertEqual(429, caught.exception.status_code)

    def test_the_limit_is_not_the_stores_per_account_budget(self):
        """Two different budgets, on purpose -- and they must stay different.

        Per-account is ``THROTTLE_LIMIT`` (20): tight, because guessing one password is
        the attack it stops.  Per-client is wider, because a shared NAT puts many
        accounts behind one address.  Equalising them would let one account lock out its
        neighbours; this test fails if a future edit quietly does.
        """
        self.assertNotEqual(store.THROTTLE_LIMIT, api.CLIENT_THROTTLE_LIMIT)
        self.assertGreater(api.CLIENT_THROTTLE_LIMIT, store.THROTTLE_LIMIT)

    def test_retry_after_is_the_stores_window(self):
        """A Retry-After that disagreed with the real window sends the client into
        another 429.  It used to be a hardcoded ``'900'`` next to a constant 900.

        The window is moved to a value nothing else uses, because asserting against the
        real 900 cannot tell a derived header from a literal that happens to agree --
        and "happens to agree" is precisely the defect this section exists to close.
        """
        def explode():
            raise store.AuthRateLimited('no')

        with patch.object(store, 'THROTTLE_WINDOW_SECONDS', 4321):
            with self.assertRaises(HTTPException) as caught:
                api.invoke(explode)
            self.assertEqual('4321', caught.exception.headers['Retry-After'])
        self.assertNotEqual(4321, store.THROTTLE_WINDOW_SECONDS)


class AdminTokenFloorTests(unittest.TestCase):
    """A configured admin token shorter than the floor is a mistake, not a secret."""

    TOKEN = 'x' * api.MIN_ADMIN_TOKEN_CHARS

    def test_a_token_at_the_floor_is_accepted(self):
        with patch.dict(os.environ, {'ADMIN_TOKEN': self.TOKEN}):
            api.admin(_Request({'X-Admin-Token': self.TOKEN}))

    def test_a_short_configured_token_is_refused(self):
        short = 'x' * (api.MIN_ADMIN_TOKEN_CHARS - 1)
        with patch.dict(os.environ, {'ADMIN_TOKEN': short}):
            with self.assertRaises(HTTPException) as caught:
                api.admin(_Request({'X-Admin-Token': short}))
            self.assertEqual(403, caught.exception.status_code)

    def test_a_missing_supplied_token_is_refused(self):
        with patch.dict(os.environ, {'ADMIN_TOKEN': self.TOKEN}):
            with self.assertRaises(HTTPException) as caught:
                api.admin(_Request())
            self.assertEqual(403, caught.exception.status_code)


class AccessTokenCapTests(unittest.TestCase):
    """The access-token cap and floor are ``auth``'s, because that is what they are.

    An access token issued here IS a session token, so measuring it against a second 900
    meant the cap could silently disagree with the lifetime ``auth`` enforces on
    verification -- issuing tokens that were born expired, or valid past the cap.
    """

    def session(self, expires_in):
        return {'user_id': 'usr_1', 'workspace_id': 'ws_1', 'role': 'owner',
                'session_id': 'ses_1', 'session_expires': time.time() + expires_in}

    def test_a_long_session_is_capped_at_auths_lifetime(self):
        pack = api.tokens('raw', self.session(auth.SESSION_TOKEN_TTL_SECONDS * 100))
        self.assertEqual(auth.SESSION_TOKEN_TTL_SECONDS, pack['expires_in'])

    def test_a_short_session_keeps_its_own_remaining_life(self):
        pack = api.tokens('raw', self.session(auth.MIN_TOKEN_TTL_SECONDS + 5))
        self.assertIn(pack['expires_in'],
                      range(auth.MIN_TOKEN_TTL_SECONDS, auth.MIN_TOKEN_TTL_SECONDS + 6))

    def test_a_session_below_auths_floor_is_refused(self):
        # MIN_TOKEN_TTL_SECONDS is 1, so this exercises the same edge the old `ttl<1`
        # did.  Kept because the 401-instead-of-500 behaviour is worth pinning, not
        # because it distinguishes the constant from the literal -- see below.
        with self.assertRaises(HTTPException) as caught:
            api.tokens('raw', self.session(auth.MIN_TOKEN_TTL_SECONDS - 1))
        self.assertEqual(401, caught.exception.status_code)

    def test_the_cap_is_not_a_local_literal(self):
        self.assertNotIn('min(900,', SOURCE)
        self.assertIn('min(SESSION_TOKEN_TTL_SECONDS,', SOURCE)

    def test_the_floor_is_auths_constant_not_a_literal(self):
        """Pinned at its comparison site -- the weaker instrument, and labelled as such.

        ``MIN_TOKEN_TTL_SECONDS`` is 1, so ``ttl<MIN_TOKEN_TTL_SECONDS`` and the ``ttl<1``
        it replaced are the same behaviour today.  No behavioural test can tell them
        apart, and the mutation harness in
        ``scripts/probes/mutation_check_identity_api.py`` proved it: reverting the floor
        left this suite green while every other revert was caught.  So this asserts the
        source, which catches a re-introduced literal but could not catch a wrong value.
        """
        self.assertIn('if ttl<MIN_TOKEN_TTL_SECONDS:', SOURCE)
        self.assertNotIn('if ttl<1:', SOURCE)


def field_owners(model):
    """``{field: {owner constants its declaration reads}}``, read from source.

    Source rather than ``model_fields`` on purpose.  Two constants can hold the same
    number, and then the schema, the OpenAPI document and every behavioural test are
    identical whichever one the declaration names -- so attribution is only observable
    in the text.  Local constants and ``store.``/``auth.`` ones are both collected, the
    latter with their prefix so ``MAX_PASSWORD_CHARS`` and ``store.MAX_PASSWORD_CHARS``
    never collapse into one another.
    """
    block = SOURCE.split('class ' + model, 1)[1]
    block = re.split(r'\n(?:class|def|@)\b', block, maxsplit=1)[0]
    owners, current = {}, None
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        head = re.match(r'(\w+)\s*:', stripped)
        if head:
            current = head.group(1)
            owners.setdefault(current, set())
        if current is not None:
            owners[current] |= set(re.findall(r'(?:store|auth)\.[A-Z][A-Z0-9_]*', stripped))
            owners[current] |= set(re.findall(r'(?<![\w.])(?:MIN|MAX|CLIENT|BEARER)_\w*', stripped))
    return owners


class OwnerAttributionTests(unittest.TestCase):
    """Each bound must come from the constant that *means* it, not one that equals it.

    The rest of this suite proves no literal survived, which is a different claim -- and
    the §163 audit found the gap by falling into it.  Reading a truncated diff, the
    ``password`` ceiling looked wired to ``MAX_TOKEN_CHARS`` instead of
    ``MAX_PASSWORD_CHARS``.  It was not.  But nothing here would have said so, because
    both are 256: every behavioural test, the published schema and the mutation harness
    stay green while the login ceiling silently follows a *token* bound.  Raise
    ``MAX_TOKEN_CHARS`` and passwords widen with it; raise ``MAX_PASSWORD_CHARS`` and
    registration widens while login does not, and the two endpoints disagree.

    A wrong owner is invisible to value equality, so attribution is pinned by name.
    """

    EXPECTED = {
        'Credentials': {
            'email': {'MIN_EMAIL_CHARS', 'store.MAX_EMAIL_CHARS'},
            'password': {'store.MIN_CANDIDATE_PASSWORD_CHARS', 'store.MAX_PASSWORD_CHARS'},
        },
        'RegisterRequest': {
            'display_name': {'store.MIN_NAME_CHARS', 'store.MAX_NAME_CHARS'},
            'invitation_token': {'store.MIN_TOKEN_CHARS', 'store.MAX_TOKEN_CHARS'},
        },
        'BootstrapRequest': {
            'display_name': {'store.MIN_NAME_CHARS', 'store.MAX_NAME_CHARS'},
            'workspace_id': {'store.MIN_WORKSPACE_ID_CHARS', 'store.MAX_WORKSPACE_ID_CHARS'},
            'workspace_name': {'store.MIN_NAME_CHARS', 'store.MAX_NAME_CHARS'},
        },
        'LoginRequest': {
            'workspace_id': {'store.MIN_WORKSPACE_ID_CHARS', 'store.MAX_WORKSPACE_ID_CHARS'},
        },
        'RefreshRequest': {
            'refresh_token': {'store.MIN_TOKEN_CHARS', 'store.MAX_TOKEN_CHARS'},
        },
        'WorkspaceRequest': {
            'user_id': {'MIN_USER_ID_CHARS', 'MAX_USER_ID_CHARS'},
            'workspace_id': {'store.MIN_WORKSPACE_ID_CHARS', 'store.MAX_WORKSPACE_ID_CHARS'},
            'name': {'store.MIN_NAME_CHARS', 'store.MAX_NAME_CHARS'},
        },
        'InviteRequest': {
            'email': {'MIN_EMAIL_CHARS', 'store.MAX_EMAIL_CHARS'},
            # The role is a Literal, not a numeric bound; its agreement with the store
            # is pinned by DerivedFloorTests and cannot be read from a set.
            'role': set(),
            'ttl_seconds': {'store.INVITATION_TTL_SECONDS',
                            'store.MIN_INVITATION_TTL_SECONDS',
                            'store.MAX_INVITATION_TTL_SECONDS'},
        },
        'AcceptInviteRequest': {
            'token': {'store.MIN_TOKEN_CHARS', 'store.MAX_TOKEN_CHARS'},
        },
    }

    def test_every_field_reads_the_constant_that_means_it(self):
        for model, expected in self.EXPECTED.items():
            self.assertEqual(expected, field_owners(model),
                             f'{model} reads a bound from the wrong owner')

    def test_no_password_field_reads_a_token_bound(self):
        """The specific confusion the audit nearly missed, named so it cannot recur."""
        for model, fields in self.EXPECTED.items():
            for field, owners in field_owners(model).items():
                if 'password' in field:
                    self.assertEqual(set(), {o for o in owners if 'TOKEN' in o},
                                     f'{model}.{field} takes its bound from a token constant')
                if 'token' in field:
                    self.assertEqual(set(), {o for o in owners if 'PASSWORD' in o},
                                     f'{model}.{field} takes its bound from a password constant')

    def test_the_table_covers_every_declared_field(self):
        """A new field wired to nothing must fail here rather than pass unnoticed."""
        for model, expected in self.EXPECTED.items():
            self.assertEqual(sorted(expected), sorted(field_owners(model)),
                             f'{model} declares a field this table does not pin')


class MintedTokenEntropyTests(unittest.TestCase):
    """What the store *issues* must be named, wired, and clear what it *accepts*.

    ``MIN_TOKEN_CHARS`` is the floor on tokens this module accepts.  The entropy at each
    mint site is a different fact, and both were bare literals -- ``token_urlsafe(32)``
    for an invitation, ``token_urlsafe(48)`` for a refresh token.  Nothing bounded what
    was issued, so the two were free to disagree in the one direction that matters:

    ``_token_hash`` refuses anything shorter than ``MIN_TOKEN_CHARS``, and it hashes the
    token the store has just minted.  Drop ``SESSION_TOKEN_BYTES`` below 15 and every
    ``create_session`` raises ``AuthenticationError('Invalid token')`` -- nobody can log
    in, register, refresh or accept an invitation, and the message blames the *token*,
    which points an operator at the client instead of at the constant they just lowered.
    The suite was green through all of it.  Entropy has no other symptom: the only
    remaining signal would have been that guessing started working.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'APP_DB': str(Path(self.tmp.name) / 'app.db')})
        env.start()
        self.addCleanup(env.stop)
        reset()
        self.addCleanup(reset)
        self.owner = store.register_user('owner@example.com', 'correct horse battery', 'Owner')
        store.create_workspace(self.owner['id'], 'acme-main', 'Acme')

    def mint_session(self):
        return store.create_session(self.owner['id'])[0]

    def mint_invitation(self, email):
        return store.create_invitation(self.owner['id'], 'acme-main', email, 'viewer')[1]

    def test_a_minted_session_token_clears_the_accept_floor(self):
        self.assertGreaterEqual(len(self.mint_session()), store.MIN_TOKEN_CHARS)

    def test_a_minted_invitation_token_clears_the_accept_floor(self):
        self.assertGreaterEqual(len(self.mint_invitation('invitee@example.com')),
                                store.MIN_TOKEN_CHARS)

    def test_the_entropy_constants_clear_the_floor_they_must_satisfy(self):
        """The invariant itself, without a database: issued length >= accepted floor.

        This is the check that was missing entirely.  It is what turns "two numbers in
        one module" into one fact with a proof, and it fails the moment either side
        moves past the other.
        """
        for name, nbytes in (('SESSION_TOKEN_BYTES', store.SESSION_TOKEN_BYTES),
                             ('INVITATION_TOKEN_BYTES', store.INVITATION_TOKEN_BYTES)):
            issued = len(secrets.token_urlsafe(nbytes))
            self.assertGreaterEqual(
                issued, store.MIN_TOKEN_CHARS,
                f'{name}={nbytes} mints {issued} chars but _token_hash refuses '
                f'anything under {store.MIN_TOKEN_CHARS} -- every mint would raise')

    def test_the_session_mint_site_reads_the_constant(self):
        """Behavioural, not textual: move the constant and the issued token moves.

        16 bytes is the smallest probe that still clears ``MIN_TOKEN_CHARS`` (22 chars),
        so the mint succeeds and the length is the evidence.  A literal ``48`` at the
        call site would keep issuing 64 characters and this would fail.
        """
        self.assertEqual(len(secrets.token_urlsafe(store.SESSION_TOKEN_BYTES)),
                         len(self.mint_session()))
        with patch.object(store, 'SESSION_TOKEN_BYTES', 16):
            self.assertEqual(len(secrets.token_urlsafe(16)), len(self.mint_session()))

    def test_the_invitation_mint_site_reads_the_constant(self):
        self.assertEqual(len(secrets.token_urlsafe(store.INVITATION_TOKEN_BYTES)),
                         len(self.mint_invitation('first@example.com')))
        with patch.object(store, 'INVITATION_TOKEN_BYTES', 16):
            self.assertEqual(len(secrets.token_urlsafe(16)),
                             len(self.mint_invitation('second@example.com')))

    def test_the_entropy_is_not_a_bare_literal_at_either_mint_site(self):
        self.assertNotIn('token_urlsafe(32)', STORE_CODE)
        self.assertNotIn('token_urlsafe(48)', STORE_CODE)
        self.assertIn('token_urlsafe(INVITATION_TOKEN_BYTES)', STORE_CODE)
        self.assertIn('token_urlsafe(SESSION_TOKEN_BYTES)', STORE_CODE)


class WorkspaceFloorEnforcementTests(unittest.TestCase):
    """A constant must be read by the module that declares it.

    The §163 audit found ``MIN_WORKSPACE_ID_CHARS`` declared in ``identity_store`` and
    referenced exactly once in the whole repository -- by its own declaration.  The HTTP
    surface read it; the store that owned it did not.  That is the defect the scrypt
    parameters had under a different name: a bound with a name and no effect, which reads
    as enforced to anyone who greps for the name and finds it.

    ``SLUG_RE`` happens to require two characters, so behaviour was correct by
    coincidence.  Raising the constant to 5 would have moved the HTTP floor and left the
    store accepting two-character slugs from any other caller.
    """

    def test_the_store_refuses_a_slug_below_its_own_floor(self):
        for slug in ('', 'a'):
            with self.assertRaises(store.IdentityError, msg=f'{slug!r} was accepted'):
                store._workspace_id(slug)

    def test_a_slug_at_the_floor_is_accepted(self):
        self.assertEqual('ab', store._workspace_id('ab'))

    def test_the_floor_is_enforced_by_the_constant_not_only_by_the_regex(self):
        """Move the constant and the store must follow -- this is the wiring proof.

        ``'abcd'`` is four characters and perfectly legal under ``SLUG_RE``, so the regex
        cannot be what refuses it.  Only ``MIN_WORKSPACE_ID_CHARS`` can.  Before the fix
        this raised nothing and the constant was decorative.
        """
        with patch.object(store, 'MIN_WORKSPACE_ID_CHARS', 5):
            with self.assertRaises(store.IdentityError):
                store._workspace_id('abcd')
        self.assertEqual('abcd', store._workspace_id('abcd'))

    def test_the_ceiling_is_enforced_by_the_constant(self):
        over = 'a' * (store.MAX_WORKSPACE_ID_CHARS + 1)
        with self.assertRaises(store.IdentityError):
            store._workspace_id(over)
        self.assertEqual('a' * store.MAX_WORKSPACE_ID_CHARS,
                         store._workspace_id('a' * store.MAX_WORKSPACE_ID_CHARS))


class ProvisioningCeilingTests(unittest.TestCase):
    """``create_workspace``'s plan and region ceilings were bare ``64`` and ``32``.

    Both are passed to ``_name`` as its third argument, whose *default* is the named
    ``MAX_NAME_CHARS`` -- so the two callers that overrode it did so with literals while
    every other caller read a constant.  ``create_workspace`` is a trusted admin
    primitive, which lowers the severity but not the defect: an unnamed ceiling cannot be
    addressed by value, cannot be compared against the HTTP surface, and cannot be found
    by anyone who does not already know to look inside a call argument.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'APP_DB': str(Path(self.tmp.name) / 'app.db')})
        env.start()
        self.addCleanup(env.stop)
        reset()
        self.addCleanup(reset)
        self.owner = store.register_user('owner@example.com', 'correct horse battery', 'Owner')

    def test_the_ceilings_are_named(self):
        self.assertEqual(64, store.MAX_PLAN_CHARS)
        self.assertEqual(32, store.MAX_REGION_CHARS)

    def test_a_plan_past_its_ceiling_is_refused(self):
        with self.assertRaises(store.IdentityError):
            store.create_workspace(self.owner['id'], 'plan-probe', 'Probe',
                                   plan='p' * (store.MAX_PLAN_CHARS + 1))

    def test_a_region_past_its_ceiling_is_refused(self):
        with self.assertRaises(store.IdentityError):
            store.create_workspace(self.owner['id'], 'region-probe', 'Probe',
                                   region='r' * (store.MAX_REGION_CHARS + 1))

    def test_both_call_sites_read_their_constants(self):
        """Behavioural wiring proof: shrink the constant and the default value must trip.

        ``plan='starter'`` is seven characters and ``region='uz'`` is two, so each probe
        shrinks one ceiling below its own default and expects a refusal.  A literal at
        the call site would keep accepting both and these would fail.
        """
        with patch.object(store, 'MAX_PLAN_CHARS', 3):
            with self.assertRaises(store.IdentityError):
                store.create_workspace(self.owner['id'], 'probe-a', 'Probe', plan='starter')
        with patch.object(store, 'MAX_REGION_CHARS', 1):
            with self.assertRaises(store.IdentityError):
                store.create_workspace(self.owner['id'], 'probe-b', 'Probe', region='uz')

    def test_the_provisioning_defaults_still_fit_their_own_ceilings(self):
        workspace = store.create_workspace(self.owner['id'], 'fits-fine', 'Fits')
        self.assertEqual('starter', workspace['plan'])
        self.assertLessEqual(len(workspace['plan']), store.MAX_PLAN_CHARS)
        self.assertLessEqual(len(workspace['region']), store.MAX_REGION_CHARS)


class ThrottleRetentionTests(unittest.TestCase):
    """The throttle's pruning horizon -- pinned at its call site, the weaker instrument.

    ``THROTTLE_RETENTION_WINDOWS`` is 2, so ``window-THROTTLE_RETENTION_WINDOWS`` and the
    ``window-2`` it replaced are the same behaviour today and no behavioural test can
    tell them apart.  That is the same situation as ``MIN_TOKEN_TTL_SECONDS``, and it is
    handled the same way rather than being quietly counted as equivalent: assert the
    source, which catches a re-introduced literal but could not catch a wrong value.

    What the constant buys is addressability.  ``window-2`` was an unnamed horizon inside
    a SQL argument -- it could not be compared against ``THROTTLE_WINDOW_SECONDS``, could
    not be found by anyone grepping for throttle bounds, and read as an arbitrary offset
    rather than as "keep this many previous windows".
    """

    def test_the_prune_horizon_is_the_constant_not_a_literal(self):
        self.assertIn('window-THROTTLE_RETENTION_WINDOWS', STORE_CODE)
        self.assertNotIn('window-2', STORE_CODE)

    def test_the_horizon_keeps_at_least_the_current_window(self):
        """Pruning must never drop the window being counted into."""
        self.assertGreaterEqual(store.THROTTLE_RETENTION_WINDOWS, 1)


if __name__ == '__main__':
    unittest.main()
