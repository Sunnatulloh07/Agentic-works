"""Boundary audit of the Customer 360 data layer -- the revert matrix (§157).

``app/customer360.py`` is the most bound-dense module left in ``app/`` and the first one
in this audit with **independent** consumers.  That matters, because §156 ended on the
weakest possible note: ``LadderStore`` and ``FileApprovalStore`` are used by no gated
suite at all, so their pins were behavioural but nobody outside the audit would have
noticed a widened bound.  Here three other modules drive the same functions:

* ``runtime_tests/test_customer360.py`` -- tenant scoping, cross-customer channel
  merges, order idempotency, and that a query is not SQL;
* ``runtime_tests/test_identity_hardening.py`` -- an order cannot move between
  customers, and a frozen workspace refuses customer mutations;
* ``runtime_tests/test_control_plane_authority.py`` -- an omitted actor is not a
  privileged service identity, and a revoked member cannot write.

So the matrix runs TWICE over the same file, with two different patterns:

* **pass A** measures ``test_customer360_bounds`` alone -- the naming invariants, the
  promoted constants, the pre-filter ceilings, the four SQL sites;
* **pass B** measures the three consumer modules alone, with the bounds module
  excluded, so a RED there cannot be my own test noticing its own fix.

A mutation is listed in whichever pass is supposed to catch it.  That is the point of
the split: it turns "these bounds are pinned" into "these bounds are pinned by an
independent caller", which is the claim §156 could not make.

Enumerated bounds and where they were before this phase:

| Bound | Value | Before |
|---|---|---|
| ``_text`` floor / ceiling | 1 / 256 | default arguments |
| identifier ceiling | 128 | **two literals that had to agree** |
| tenant ceiling | 64 | default argument |
| contact value ceiling | 512 | default argument |
| phone digit floor | 7 | **unnamed literal** |
| external ref ceiling | 256 | **unnamed literal** |
| page limit floor / ceiling | 1 / 100 | **unnamed literal**, in a message too |
| page offset ceiling | 100_000 | **unnamed literal** |
| query ceiling | 256 | **unnamed literal** |
| embedded collection ceiling | 100 | **four separate literals** |
| pre-filter ceilings | 32/32/32/8 | **four unnamed literals** |
| order total ceiling | 10**15 | **unnamed literal** |
| order status vocabulary | 6 words | **inline in the guard** |
| currency pattern | 3 letters | unnamed regex |
| write-role subset | 2 of 4 roles | **inline in the guard** |
| ``status!='deleted'`` | 4 sites | **unnamed, and unreachable from the API** |

Three findings are worth pausing on.

**One number, two places, and whichever is tighter wins silently.**  ``_id`` calls
``_text(..., maximum=128)`` and then matches ``_ID_RE``, whose quantifier was
``{1,128}``.  Two literals that must agree, with nothing making them: narrowing the
regex to ``{1,12}`` changes the accepted set and raises nothing that mentions 128.  The
regex is now built from ``MAX_ID_CHARS``, so the question is gone rather than tested --
the same move as ``max(MIN_WINDOW, min_tasks)`` in §156.  The mutation below changes the
quantifier back to a literal and the pair test goes red.

**Dominating ceilings.**  ``kind``, ``channel``, ``status`` and ``currency`` are
length-checked and then immediately looked up in a small vocabulary or matched against a
regex, so their ceilings do not decide the accepted SET -- they only bound the work done
before the lookup.  A value one character past the ceiling and a value that is merely
unknown both raise the same exception type.  They are pinned by their *cause*: a
33-character ``kind`` is refused for length, a 10-character one for the vocabulary.  That
is §155's "a precondition being true is not the same as the precondition being why",
applied to a length check.

**A guard that no test could reach.**  ``status!='deleted'`` appears in four predicates,
and ``create_customer`` refuses ``status='deleted'`` -- so no test that goes through the
module can ever produce the row those predicates exist to hide.  Four guards, zero
coverage, and deleting all four left the suite green.  The tombstone tests plant the row
with SQL because that is the only way it can exist.

One bound is pinned structurally rather than behaviourally and the difference is recorded
rather than glossed: only two of the four ``LIMIT {MAX_EMBEDDED_ROWS}`` sites have a
collection big enough to over-fill cheaply, so the other two are covered by counting the
placeholder four times in the source.  A mutation of any one site is still RED, but by a
source assertion rather than by a returned list.

What the matrix found, and it is the reason to run one.  The first pass measured 60
mutations and **four of them were GREEN** -- four guards nobody pinned.  One was listed in
the wrong pass (``verified is not True`` is caught by the consumer suite, not this module).
The other three are one family, and the family is the lesson: **an exception type is not
proof of a refusal.**  Every mutator returns ``get_customer`` at the end, so a call is
"refused" even after its guard is deleted -- one line later, and after the row is written.
The tombstone existence check, the orphan-contact check, and the missing-actor check all
left the suite green for exactly this reason.  They are now pinned by their *effect* (the
row that must not exist) or by their *cause* (the message that says why), and the second
run measures **60/60 RED, 0 GREEN, 0 unmeasured, both restores verified**.

Run from the repository root::

    python scripts/probes/audit_customer360_bounds.py --check
    python scripts/probes/audit_customer360_bounds.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

BOUNDS_PATTERN = 'runtime_tests.test_customer360_bounds'
CONSUMER_PATTERN = ('runtime_tests.test_customer360 '
                    'runtime_tests.test_identity_hardening '
                    'runtime_tests.test_control_plane_authority')

# --- pass A: what the audit's own module pins ---------------------------------------
BOUNDS = [
    # --- the promoted constants, each against its literal ---------------------------
    ('text floor 1', b'MIN_TEXT_CHARS = 1', b'MIN_TEXT_CHARS = 2'),
    ('text ceiling 256', b'MAX_TEXT_CHARS = 256', b'MAX_TEXT_CHARS = 128'),
    ('identifier ceiling 128', b'MAX_ID_CHARS = 128', b'MAX_ID_CHARS = 64'),
    ('tenant ceiling 64', b'MAX_TENANT_CHARS = 64', b'MAX_TENANT_CHARS = 32'),
    ('contact value ceiling 512',
     b'MAX_CONTACT_VALUE_CHARS = 512', b'MAX_CONTACT_VALUE_CHARS = 256'),
    ('phone digit floor 7', b'MIN_PHONE_DIGITS = 7', b'MIN_PHONE_DIGITS = 6'),
    ('external ref ceiling 256',
     b'MAX_EXTERNAL_REF_CHARS = 256', b'MAX_EXTERNAL_REF_CHARS = 128'),
    ('page limit floor 1', b'MIN_PAGE_LIMIT = 1', b'MIN_PAGE_LIMIT = 2'),
    ('page limit ceiling 100', b'MAX_PAGE_LIMIT = 100', b'MAX_PAGE_LIMIT = 50'),
    ('page offset ceiling 100000',
     b'MAX_PAGE_OFFSET = 100_000', b'MAX_PAGE_OFFSET = 1_000'),
    ('query ceiling 256', b'MAX_QUERY_CHARS = 256', b'MAX_QUERY_CHARS = 128'),
    ('embedded collection ceiling 100',
     b'MAX_EMBEDDED_ROWS = 100', b'MAX_EMBEDDED_ROWS = 50'),
    ('kind ceiling 32', b'MAX_KIND_CHARS = 32', b'MAX_KIND_CHARS = 16'),
    ('channel ceiling 32', b'MAX_CHANNEL_CHARS = 32', b'MAX_CHANNEL_CHARS = 16'),
    ('status ceiling 32', b'MAX_STATUS_CHARS = 32', b'MAX_STATUS_CHARS = 16'),
    ('currency ceiling 8', b'MAX_CURRENCY_CHARS = 8', b'MAX_CURRENCY_CHARS = 4'),
    ('order total ceiling 10**15',
     b'MAX_ORDER_TOTAL_MINOR = 10 ** 15', b'MAX_ORDER_TOTAL_MINOR = 10 ** 3'),
    # --- the pair that had to agree, and is now one source --------------------------
    ('the regex ceiling is the constant',
     rb'_ID_RE = re.compile(rf"^[A-Za-z0-9_-]{{1,{MAX_ID_CHARS}}}$")',
     rb'_ID_RE = re.compile(rf"^[A-Za-z0-9_-]{{1,12}}$")'),
    ('the text ceiling in _id is the identifier ceiling',
     b'_text(value, name, maximum=MAX_ID_CHARS)',
     b'_text(value, name, maximum=MAX_TEXT_CHARS)'),
    ('the identifier alphabet', b'[A-Za-z0-9_-]', b'[A-Za-z0-9_]'),
    # --- the vocabularies -----------------------------------------------------------
    ('the currency pattern is three letters',
     rb'_CURRENCY_RE = re.compile(r"[A-Z]{3}")', rb'_CURRENCY_RE = re.compile(r"[A-Z]{2}")'),
    ('the order vocabulary keeps refunded',
     b'"cancelled", "refunded", "fulfilled"}', b'"cancelled", "fulfilled"}'),
    ('the settable status set excludes the tombstone',
     b'_ALLOWED_STATUS - {"deleted"}', b'_ALLOWED_STATUS'),
    # --- the guard expressions ------------------------------------------------------
    ('the limit window is closed at the top',
     b'MIN_PAGE_LIMIT <= limit <= MAX_PAGE_LIMIT',
     b'MIN_PAGE_LIMIT <= limit <= MAX_PAGE_LIMIT * 10'),
    ('the offset window is closed at the top',
     b'0 <= offset <= MAX_PAGE_OFFSET', b'0 <= offset <= MAX_PAGE_OFFSET * 10'),
    ('the query bar is inclusive', b'len(query) > MAX_QUERY_CHARS',
     b'len(query) >= MAX_QUERY_CHARS'),
    ('the external ref bar is inclusive', b'len(external_ref) > MAX_EXTERNAL_REF_CHARS',
     b'len(external_ref) >= MAX_EXTERNAL_REF_CHARS'),
    ('the phone floor is inclusive', b'len(normalized) < MIN_PHONE_DIGITS',
     b'len(normalized) <= MIN_PHONE_DIGITS'),
    ('the order total bar is inclusive', b'total_minor > MAX_ORDER_TOTAL_MINOR',
     b'total_minor >= MAX_ORDER_TOTAL_MINOR'),
    # --- a bool is an int, so each of the four needs saying -------------------------
    ('a bool is not a limit', b'isinstance(limit, bool)', b'False'),
    ('a bool is not an offset', b'isinstance(offset, bool)', b'False'),
    ('a bool is not a total', b'isinstance(total_minor, bool)', b'False'),
    ('a bool is not a verified flag', b'isinstance(verified, bool)', b'False'),
    # --- the lookups ----------------------------------------------------------------
    ('the contact type is looked up',
     b'if kind not in _ALLOWED_CONTACT_TYPES:', b'if False:'),
    ('the channel is looked up',
     b'if channel not in _ALLOWED_CHANNELS:', b'if False:'),
    ('the order status and currency are looked up',
     b'if status not in _ORDER_STATUSES or not _CURRENCY_RE.fullmatch(currency):',
     b'if False:'),
    ('the settable status is looked up',
     b'if status not in _ALLOWED_STATUS - {"deleted"}:', b'if False:'),
    # --- the write-role subset, which related to nothing before ---------------------
    ('the write-role vocabulary',
     b'WRITE_ROLES = frozenset({"owner", "operator"})',
     b'WRITE_ROLES = frozenset({"owner", "operator", "viewer"})'),
    ('the role gate is not just a membership gate',
     b"if not m or m['role'] not in WRITE_ROLES:", b'if not m:'),
    # --- the write path, pinned by the audit's own module (its CAUSE, not its type) ---
    ('the write path needs an actor',
     b"if not isinstance(actor,str) or not actor:raise AuthenticationError('Write actor required')",
     b"if False:raise AuthenticationError('Write actor required')"),
    ('the write path requires the customer to exist',
     b'        _ensure_customer(c, tenant, customer_id)\n'
     b'        try:\n'
     b'            c.execute(\n'
     b'                "INSERT INTO p_customer_contacts',
     b'        try:\n'
     b'            c.execute(\n'
     b'                "INSERT INTO p_customer_contacts'),
    # --- the paging message: the third place the window is written -------------------
    ('the refusal text is built from the window',
     b'f"limit {MIN_PAGE_LIMIT}..{MAX_PAGE_LIMIT}', b'"limit 1..100'),
    # --- the four embedded-collection sites -----------------------------------------
    # Each anchored to its table, because ``LIMIT {MAX_EMBEDDED_ROWS}`` alone matches
    # four times and the matrix reports a pattern that matches more than once as
    # AMBIGUOUS -- a mutation that replaced all four at once would measure nothing.
    ('the contacts collection',
     b'FROM p_customer_contacts WHERE tenant=? AND customer_id=? ORDER BY created '
     b'LIMIT {MAX_EMBEDDED_ROWS}',
     b'FROM p_customer_contacts WHERE tenant=? AND customer_id=? ORDER BY created '
     b'LIMIT 50'),
    ('the channel identity collection',
     b'FROM p_channel_identities WHERE tenant=? AND customer_id=? ORDER BY created '
     b'LIMIT {MAX_EMBEDDED_ROWS}',
     b'FROM p_channel_identities WHERE tenant=? AND customer_id=? ORDER BY created '
     b'LIMIT 50'),
    ('the conversation collection',
     b'FROM p_conversations WHERE tenant=? AND customer_id=? ORDER BY updated DESC '
     b'LIMIT {MAX_EMBEDDED_ROWS}',
     b'FROM p_conversations WHERE tenant=? AND customer_id=? ORDER BY updated DESC '
     b'LIMIT 50'),
    ('the order collection',
     b'FROM p_customer_orders WHERE tenant=? AND customer_id=? ORDER BY updated DESC '
     b'LIMIT {MAX_EMBEDDED_ROWS}',
     b'FROM p_customer_orders WHERE tenant=? AND customer_id=? ORDER BY updated DESC '
     b'LIMIT 50'),
    # --- the query is escaped before it becomes a pattern ---------------------------
    ('the percent in a query is literal', rb'.replace("%", "\\%")', rb'.replace("%", "%")'),
    ('the underscore in a query is literal', rb'.replace("_", "\\_")', rb'.replace("_", "_")'),
    ('the backslash in a query is literal',
     rb'.replace("\\", "\\\\").replace("%", "\\%")', rb'.replace("%", "\\%")'),
    # --- the tombstone, at all four sites -------------------------------------------
    ('a tombstone is not readable',
     b'display_name,status,created,updated FROM p_customers WHERE tenant=? AND id=? '
     b"AND status!='deleted'",
     b'display_name,status,created,updated FROM p_customers WHERE tenant=? AND id=?'),
    ('a tombstone is not listed',
     b"WHERE tenant=? AND status!='deleted' AND (display_name LIKE",
     b"WHERE tenant=? AND (display_name LIKE"),
    ('a tombstone takes no new children',
     b"SELECT 1 FROM p_customers WHERE tenant=? AND id=? AND status!='deleted'",
     b'SELECT 1 FROM p_customers WHERE tenant=? AND id=?'),
]

# --- pass B: what an INDEPENDENT consumer pins ---------------------------------------
CONSUMERS = [
    ('the list is scoped to the tenant',
     rb"""        "WHERE tenant=? AND status!='deleted' AND (display_name LIKE ? ESCAPE '\\' OR external_ref LIKE ? ESCAPE '\\') "
        "ORDER BY updated DESC LIMIT ? OFFSET ?",
        (tenant, pattern, pattern, limit, offset),""",
     rb"""        "WHERE status!='deleted' AND (display_name LIKE ? ESCAPE '\\' OR external_ref LIKE ? ESCAPE '\\') "
        "ORDER BY updated DESC LIMIT ? OFFSET ?",
        (pattern, pattern, limit, offset),"""),
    ('the detail read is scoped to the tenant',
     rb"""        "SELECT id,external_ref,display_name,status,created,updated FROM p_customers WHERE tenant=? AND id=? AND status!='deleted'",
        (tenant, customer_id),""",
     rb"""        "SELECT id,external_ref,display_name,status,created,updated FROM p_customers WHERE id=? AND status!='deleted'",
        (customer_id,),"""),
    ('a channel identity cannot be re-pointed',
     b'if existing and existing["customer_id"] != customer_id:',
     b'if False:'),
    ('an order cannot move between customers',
     b"if existing and existing['customer_id'] != customer_id:",
     b'if False:'),
    ('channel identity needs a real true',
     b'if verified is not True:', b'if False:'),
    ('the write path needs a live role',
     b"if not m or m['role'] not in WRITE_ROLES:", b'if False:'),
    ('a frozen workspace refuses writes',
     b"if row and row['stopped']: raise AuthenticationError('Workspace frozen')",
     b"if False: raise AuthenticationError('Workspace frozen')"),
    ('a repeated order updates rather than ignores',
     b'ON CONFLICT(tenant,external_id) DO UPDATE SET',
     b'ON CONFLICT(tenant,external_id) DO NOTHING -- SET'),
]

MATRICES = [
    (BOUNDS_PATTERN, 'app/customer360.py', BOUNDS),
    (CONSUMER_PATTERN, 'app/customer360.py', CONSUMERS),
]

if __name__ == '__main__':
    if '--check' in sys.argv:
        code = 0
        for target in dict.fromkeys(target for _, target, _ in MATRICES):
            mutations = [m for _, t, ms in MATRICES if t == target for m in ms]
            code |= revert_matrix.verify(target, mutations)
        sys.exit(code)
    code = 0
    for pattern, target, mutations in MATRICES:
        print('=' * 72)
        print(f'pattern: {pattern}')
        code |= revert_matrix.main(target, pattern, mutations)
    sys.exit(code)
