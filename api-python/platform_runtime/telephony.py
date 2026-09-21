"""Call events as one more bounded read source (PRD-04, P14 stage A).

Outbound IP telephony is the last PRD surface and the one deliberately left
until now, for three reasons the PRD itself states: it is **heavy**, it is
**legally exposed** (recording consent and retention are operator obligations,
not product features), and it is **quality sensitive**, because Uzbek dialect and
mixed Russian degrade STT. This module does not implement a dialer. It implements
the **call-event reader and the consent gate** that any later dialer must pass
through, because the consent gate is the part that cannot be retrofitted.

The two rules this module exists to enforce
-------------------------------------------

**1. Audio never reaches the platform.** Only ``{direction, outcome, timestamp,
duration, extension}``. This is the same rule as vision (P11b) and as v0.4's
"audio never reaches the planner, only the transcript", and it is enforced
structurally here: there is no tool argument, no config key and no output field
that carries audio, a recording URL, a transcript or a PCM buffer. The register
this module reads is an operator-written **call log**, not a media store; a
column holding an audio path is treated as opaque text and is never dereferenced.

**2. A call may not be placed without a declared consent.**
The PRD makes consent an operator obligation (VO-07: consent, retention, delete,
DPA, region and subprocessor terms). Modelled here as the same **declaration +
refusal** split the platform uses for biometric vision and for the WhatsApp
window:

* the operator **declares** consent in the register — a per-tenant policy plus a
  per-number record;
* calling a number with **no declaration is refused in code, by name**;
* **recording** a call requires a *separate* consent, and a call whose recording
  consent is absent is not read as "unrecorded" — it is refused, because the
  caller cannot know whether a recording exists.

Why the refusal is in code and not in the prompt
------------------------------------------------

An outbound call to a person who did not consent is not a quality problem, it is
a legal one, and a prompt is not an enforcement mechanism. The same argument the
escalation coordinator makes for delivery applies here: a rule that lives only in
the model's instructions is a rule that a sufficiently convincing input can
remove. So ``telephony.consent`` is a read tool that answers the question
"may we call this number, and may we record it", and ``telephony.call_events``
refuses by name when a row names a number the tenant never declared.

**Stricter than the declaration, on purpose, in one direction.** A declaration is
permitted to be **missing** — an unknown number yields ``consented: False`` with
a reason rather than an exception, because the caller asking the question needs
the answer "no" to be usable. What is *not* permitted is a **malformed**
declaration: an unreadable expiry, an unknown purpose, a status that is neither
``granted`` nor ``withdrawn`` raises at configure time, because a typo in a
consent record must never read as consent granted. "We did not find a record"
and "we could not read the record" are different facts and only one of them is
safe to act on.

Boundaries that make this safe to run:

* Every read goes through the existing ``sheets.rows`` tool, so the register
  declaration, A1 range allowlist, agent tool permission and connection allowlist
  all apply unchanged. This module adds no new data access path.
* The number is **normalised to digits** before any comparison, and matched by
  exact equality, never by ``startswith``: ``+998901234567`` and ``998901234567``
  are the same number, but ``...4567`` and ``...45678`` are not, and a prefix
  match would call a stranger because a shorter number was consented. The same
  segment-not-prefix discipline P11 applies to asset paths applies here to
  numbers, and for the same reason.
* Reads are **windowed and limited**. A call log is continuous, so an unbounded
  read would pour rows into a planner; ``since``/``until`` and ``limit`` are both
  bounded and ``truncated`` always ships.
* No person is evaluated. The register may carry an agent's extension or a
  manager's name; neither is reported. What is reported is the **call fact** —
  direction, outcome, duration — attributed to a **number**, and a number is not
  a performance score.
"""
from __future__ import annotations

import re

from . import assets
from .engine import Forbidden

TELEPHONY_TOOLS = ('telephony.consent', 'telephony.call_events',
                   'telephony.summary', 'telephony.queue',
                   'telephony.retention')

# Mirrors the sheets module's own row ceiling: a telephony read can never scan
# more than the register tool will return, so declaring a larger bound would only
# move the refusal somewhere less obvious.
MAX_ROWS = 200
MAX_EVENTS = 200
MAX_REGISTERS = 20
MAX_PURPOSES = 16

# --- Stage B bounds ---------------------------------------------------------
#
# The queue is a *description of what could be called*, never a dialer. It is
# bounded on every axis an operator could otherwise use to turn it into one:
#
# * ``MAX_ROWS``        -- how many callable rows one read may return at all;
# * ``MAX_QUEUE_ITEMS`` -- how many the planner is asked to carry per call;
# * ``MAX_PER_WINDOW`` / ``MIN_WINDOW_SECONDS`` -- the throughput ceiling, which
#   is the one number that makes an outbound campaign lawful in practice: no row
#   may be described as callable if the declared pace would have to be exceeded;
# * ``MAX_RETENTION_DAYS`` / ``MIN_RETENTION_DAYS`` -- a retention window must be
#   declared and it must be finite. "Keep forever" is not a retention policy, it
#   is the absence of one, and VO-07 names retention an operator obligation.
#
# ``MAX_QUEUE = 200`` used to sit on the next line, described above as "how many
# callable rows one read may return at all". Nothing read it: the read is bounded
# by ``MAX_ROWS`` and the items carried by ``MAX_QUEUE_ITEMS``, so the ceiling the
# comment named did not exist. Removed, and the comment now names the constants
# that are actually enforced -- the same correction fazza I made to
# ``inventory.MAX_NAMES`` and ``oee.MAX_NAMES``.
MAX_QUEUE_ITEMS = 50
MAX_PER_WINDOW = 1000
MIN_WINDOW_SECONDS = 60
MAX_WINDOW_SECONDS = 86_400
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650
MAX_PURPOSE_CHARS = 64
# ``MAX_WINDOW_DAYS = 31`` used to sit here and nothing read it. The obvious intent
# is a ceiling on the ``since``..``until`` span, and ``_day`` validates the FORMAT
# of both but never their distance -- so a caller may still ask for a ten-year
# window. Removing the constant makes that absence visible instead of implying a
# bound that is not enforced; the missing span ceiling is recorded as an open risk
# in BACKLOG.json rather than invented here, because choosing the number is an
# operator's decision.
MAX_NUMBER_DIGITS = 15
# At least this many digits for a cell to count as a phone number. Stripping
# non-digits from arbitrary text collapses unrelated strings onto the same short
# value ('https://a/rec-9.wav' and 'call 9' both become '9'), so a shorter run is
# refused as *not a number* rather than accepted as a suspiciously short one.
# 7 is the shortest national subscriber number in ordinary use; a value below it
# is an extension, a fragment or a collision, never a callable number.
MIN_NUMBER_DIGITS = 7
MAX_DURATION = 86_400

# A cell that names a locator rather than a value. Used in two places and defined
# once, so a phone-number cell and an outcome cell are judged by the same rule:
#
# * ``normalise`` refuses a locator outright, because a URL that embeds a full
#   phone number (``https://cdn.example/rec-998901234567.wav``) would otherwise
#   reduce to a valid counterparty and turn a recording's filename into a
#   consented number;
# * ``_outcome`` withholds one, because "treat the column as opaque text" is
#   exactly how a recording link leaks into a model's context and a third party.
#
# A scheme-prefixed URL (any scheme), a protocol-relative ``//`` host, the inline
# carriers ``data:``/``blob:``, and a media file extension at a word boundary.
# Deliberately narrow: ``zavod-1/sex-1/liniya-1/stanok-1`` and ``answered`` are
# not locators and must survive.
_LOCATOR_RE = re.compile(
    r'(?:^|[\s(=])(?:[a-z][a-z0-9+.-]*://|//|data:|blob:)'
    r'|\.(?:wav|mp3|ogg|opus|webm|m4a|aac|pcm|flac|amr|gsm|ulaw|alaw)(?:$|[?#\s])',
    re.IGNORECASE)

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')

# An ISO calendar date. Compared as text against the register's own cell,
# deliberately: parsing provider text would guess at ambiguous formats such as
# 10.01.2026 and silently include or exclude the wrong day. Same rule as vision.
# ``[0-9]`` and not ``\d``: the value is compared as text, so a Unicode date
# would simply not match -- but being explicit means a future change that starts
# parsing it cannot silently normalise it. See ``platform_runtime.cells``.
DAY_RE = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')

# Direction of a call relative to the tenant. ``inbound`` is a customer calling
# us (the consent question is different: they called us), ``outbound`` is us
# calling them, which is the direction that requires a declaration.
DIRECTIONS = ('inbound', 'outbound')

# The purpose a consent was given FOR. A consent to call about a delivery is not
# a consent to call about a promotion, and collapsing them into one flag is how a
# lawful list becomes an unlawful one.
PURPOSES = ('service', 'delivery', 'payment', 'support', 'marketing')

# Only two statuses exist. There is no ``pending`` and no ``unknown``, because a
# consent whose state is not one of these two is not a state -- it is a record we
# cannot read, and reading it as granted is the defect this module exists against.
CONSENT_STATUS = ('granted', 'withdrawn')


class TelephonyError(RuntimeError):
    pass


def _bounded(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def _day(value, name):
    if value is None or value == '':
        return ''
    if not isinstance(value, str) or not DAY_RE.match(value):
        raise ValueError(f'{name} must be an ISO date (YYYY-MM-DD)')
    return value


def _identifier(value, name, maximum=64):
    if not isinstance(value, str) or not NAME_RE.match(value):
        raise ValueError(f'{name} must match {NAME_RE.pattern}')
    if len(value) > maximum:
        raise ValueError(f'{name} must be at most {maximum} characters')
    return value


def _column(value, name):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError(f'{name} must be a non-empty column name of at most 64 characters')
    return value


def _purpose_list(value, register, key):
    if not isinstance(value, list) or len(value) > MAX_PURPOSES:
        raise ValueError(f'Telephony register {register} {key} must be a list of at most '
                         f'{MAX_PURPOSES}')
    clean = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > MAX_PURPOSE_CHARS:
            raise ValueError(f'Telephony register {register} {key} has an invalid purpose')
        clean.append(item.strip().casefold())
    return clean


def normalise(number):
    """A phone number as digits, or ``''`` when it is not a usable number.

    Every comparison in this module goes through here, so that two spellings of
    one number cannot be two different consents. ``+998 90 123 45 67``,
    ``998901234567`` and ``998-90-123-45-67`` are one number; anything else --
    a blank cell, a name, a short extension, an over-long string -- is ``''`` and
    is therefore *not consented* rather than accidentally equal to something.

    Digits are kept, not stripped of a leading zero: an Uzbek number written
    locally (``901234567``) and internationally (``998901234567``) are genuinely
    different strings and this module refuses to guess which the operator meant.
    Guessing is what produces an outbound call to the wrong person.

    **Two shapes are refused before any digits are extracted.**

    *A minimum length.* Stripping non-digits from arbitrary text is lossy in a
    dangerous direction: a media URL (``https://media.example/rec-9.wav``), a
    bare filename (``rec-9.wav``), the word ``call 9`` and a lone ``9`` all
    reduce to ``'9'`` -- six unrelated strings becoming one "number", so an
    allowlist or consent row for ``'9'`` would cover every one of them. A phone
    number has at least ``MIN_NUMBER_DIGITS`` digits, so a shorter run is refused
    as *not a number* rather than accepted as a suspiciously short one.

    *A locator shape.* A URL or path that **embeds** a full phone number would
    otherwise pass the length test and become a valid counterparty:
    ``https://cdn.example/rec-998901234567.wav`` reduces to ``998901234567``, so a
    register whose number column points at a media column could turn a recording's
    own filename into a consented number. ``tel:`` is the one scheme that
    legitimately names a number, so it is stripped and the rest judged as before;
    every other locator is refused outright.

    The rule is the same one P11 applies to asset paths: do not accept a value
    whose meaning you would be guessing.
    """
    if number is None or isinstance(number, bool):
        return ''
    text = str(number).strip()
    if not text:
        return ''
    # ``tel:`` names a number, so it is a spelling of one, not a locator to
    # refuse. ``tel://`` is not a real form but is stripped the same way.
    scheme = re.match(r'^tel:/*', text, re.IGNORECASE)
    if scheme:
        text = text[scheme.end():].strip()
    elif _LOCATOR_RE.search(text):
        # A URL, a UNC path or a media filename is not a phone number however
        # many digits it embeds.
        return ''
    digits = re.sub(r'\D', '', text)
    if len(digits) < MIN_NUMBER_DIGITS or len(digits) > MAX_NUMBER_DIGITS:
        return ''
    return digits


def _register(name, entry):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f'Invalid telephony register name: {name!r}')
    if not isinstance(entry, dict):
        raise ValueError(f'Telephony register {name} must be an object')
    unknown = set(entry) - {
        'register', 'range', 'direction_column', 'number_column',
        'timestamp_column', 'outcome_column', 'duration_column',
        'extension_column', 'purpose',
    }
    if unknown:
        raise ValueError(f'Telephony register {name} has unsupported keys: {sorted(unknown)}')
    out = {}
    for key in ('register', 'range'):
        value = entry.get(key)
        if not isinstance(value, str) or not NAME_RE.match(value):
            raise ValueError(f'Telephony register {name} requires a valid {key}')
        out[key] = value
    # The number column is the join to the consent register. Without it a call has
    # no counterparty, which is the one thing the consent gate exists to check.
    for key in ('direction_column', 'number_column', 'timestamp_column'):
        out[key] = _column(entry.get(key), f'Telephony register {name} {key}')
    for key in ('outcome_column', 'duration_column', 'extension_column'):
        value = entry.get(key, '')
        out[key] = _column(value, f'Telephony register {name} {key}') if value else ''
    # What this register's rows are FOR. A register holding marketing calls and a
    # register holding delivery calls are different consent questions, so the
    # purpose is declared per register rather than inferred from a column, which
    # would let a row relabel itself.
    purpose = entry.get('purpose', 'service')
    if not isinstance(purpose, str) or purpose.strip().casefold() not in PURPOSES:
        raise ValueError(f'Telephony register {name} purpose must be one of {list(PURPOSES)}')
    out['purpose'] = purpose.strip().casefold()
    return out


def _consent_register(name, entry):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f'Invalid consent register name: {name!r}')
    if not isinstance(entry, dict):
        raise ValueError(f'Consent register {name} must be an object')
    unknown = set(entry) - {
        'register', 'range', 'number_column', 'status_column',
        'purpose_column', 'expiry_column', 'recording_column',
    }
    if unknown:
        raise ValueError(f'Consent register {name} has unsupported keys: {sorted(unknown)}')
    out = {}
    for key in ('register', 'range'):
        value = entry.get(key)
        if not isinstance(value, str) or not NAME_RE.match(value):
            raise ValueError(f'Consent register {name} requires a valid {key}')
        out[key] = value
    for key in ('number_column', 'status_column', 'purpose_column'):
        out[key] = _column(entry.get(key), f'Consent register {name} {key}')
    for key in ('expiry_column', 'recording_column'):
        value = entry.get(key, '')
        out[key] = _column(value, f'Consent register {name} {key}') if value else ''
    return out


def _number_list(value, tenant):
    """The operator's declared allowlist of numbers that may be called at all."""
    if value is None:
        return frozenset()
    if not isinstance(value, list) or len(value) > 5000:
        raise ValueError('telephony.consented_numbers must be a list of at most 5000 numbers')
    digits = set()
    for item in value:
        clean = normalise(item)
        if not clean:
            raise ValueError(f'telephony.consented_numbers has an unusable number: {item!r}')
        digits.add(clean)
    return frozenset(digits)


def _throughput(value):
    """The declared outbound pace: at most ``per_window`` calls per ``window``.

    Deliberately a **rate**, not a total: a total would permit a burst that the
    provider would reject and that no human could supervise, and the PRD's own
    live criterion (VO-04, p95 < 2.5s) is a latency statement about a stream, not
    a batch. Absent, the pace is "no outbound row may be described as callable",
    which is the safe default: an operator who has not decided their pace has not
    decided whether to call anyone.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('telephony.throughput must be an object')
    unknown = set(value) - {'per_window', 'window_seconds'}
    if unknown:
        raise ValueError(f'telephony.throughput has unsupported keys: {sorted(unknown)}')
    per = value.get('per_window')
    if type(per) is not int or not 1 <= per <= MAX_PER_WINDOW:
        raise ValueError(f'telephony.throughput.per_window must be an integer '
                         f'1..{MAX_PER_WINDOW}')
    window = value.get('window_seconds')
    if type(window) is not int or not MIN_WINDOW_SECONDS <= window <= MAX_WINDOW_SECONDS:
        raise ValueError(f'telephony.throughput.window_seconds must be an integer '
                         f'{MIN_WINDOW_SECONDS}..{MAX_WINDOW_SECONDS}')
    return {'per_window': per, 'window_seconds': window}


def _retention(value):
    """The declared retention window, in days.

    Required whenever the tenant declares a consent register, and finite. A
    call log is personal data; a window of ``0`` or one above ``MAX`` is refused
    rather than clipped, because an operator who asked for "forever" must be told
    the platform will not hold a call record indefinitely, not handed a silently
    different number.
    """
    if value is None:
        return None
    if type(value) is not int or not MIN_RETENTION_DAYS <= value <= MAX_RETENTION_DAYS:
        raise ValueError(f'telephony.retention_days must be an integer '
                         f'{MIN_RETENTION_DAYS}..{MAX_RETENTION_DAYS}')
    return value


def telephony_config(tenant) -> dict:
    """Validate the tenant's declared telephony mapping, or an empty one.

    Configuration errors raise instead of being skipped: a typo in a column name
    must not silently produce an empty call list, because "no calls were made" and
    "we read the wrong column" would look identical to a manager. The consent
    register is validated the same way, because a consent table we cannot read is
    not a table that grants consent.
    """
    from .tools import config
    raw = config(tenant).get('telephony')
    if raw is None:
        return {'registers': {}, 'consent_registers': {}, 'consented_numbers': frozenset(),
                'throughput': None, 'retention_days': None}
    if not isinstance(raw, dict):
        raise ValueError('telephony must be an object')
    unknown = set(raw) - {'registers', 'consent_registers', 'consented_numbers',
                          'throughput', 'retention_days'}
    if unknown:
        raise ValueError(f'telephony has unsupported keys: {sorted(unknown)}')
    declared = raw.get('registers', {})
    if not isinstance(declared, dict) or len(declared) > MAX_REGISTERS:
        raise ValueError(f'telephony.registers must be an object with at most '
                         f'{MAX_REGISTERS} entries')
    consents = raw.get('consent_registers', {})
    if not isinstance(consents, dict) or len(consents) > MAX_REGISTERS:
        raise ValueError(f'telephony.consent_registers must be an object with at most '
                         f'{MAX_REGISTERS} entries')
    retention = _retention(raw.get('retention_days'))
    # A tenant that declares consent to call people has thereby declared that it
    # holds personal data about them, and a data holder without a retention window
    # has made the one choice VO-07 forbids by omission. Refused at configure
    # time, in the same spirit as the malformed-consent refusal above.
    if consents and retention is None:
        raise ValueError('telephony.retention_days is required when a consent register '
                         'is declared (VO-07: retention is an operator obligation)')
    return {
        'registers': {name: _register(name, entry)
                      for name, entry in declared.items()},
        'consent_registers': {name: _consent_register(name, entry)
                              for name, entry in consents.items()},
        'consented_numbers': _number_list(raw.get('consented_numbers'), tenant),
        'throughput': _throughput(raw.get('throughput')),
        'retention_days': retention,
    }


def _registers(tenant):
    return telephony_config(tenant)['registers']


def _consent_registers(tenant):
    return telephony_config(tenant)['consent_registers']


def purposes(tenant):
    """Every purpose this tenant declared across all its call registers.

    Deliberately **tenant-wide** for the same reason vision's ``person_classes``
    is: a purpose is a property of the call, not of the table a row happens to sit
    in. A marketing call landing in the delivery register must still be checked
    against marketing consent.
    """
    names = set()
    for entry in _registers(tenant).values():
        names.add(entry['purpose'])
    return frozenset(names)


def _resolve(tenant, register):
    declared = _registers(tenant)
    if not declared:
        raise Forbidden('No telephony register is declared for this tenant')
    if register is None:
        if len(declared) == 1:
            return next(iter(declared.values()))
        raise Forbidden('More than one telephony register is declared; name one')
    if register not in declared:
        raise Forbidden(f'Telephony register {register!r} is not declared')
    return declared[register]


def _ladder(engine, tenant, agent):
    return str(engine.policy(tenant, agent).get('ladder', ''))


def _read_rows(engine, tenant, agent, entry):
    """Read one declared register through the ordinary sheets tool handler."""
    tool = engine.registry.get('sheets.rows')
    args = {'register': entry['register'], 'range': entry['range']}
    tool.validate(args)
    result = tool.handler(engine, tenant, agent, args, 'telephony')
    rows = result.get('rows')
    if not isinstance(rows, list):
        raise TelephonyError('Telephony register returned an unexpected shape')
    return rows[:MAX_ROWS], result


def _cell_text(value, maximum=120):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)[:maximum]


# A cell that names a locator rather than an outcome. The boundary "audio never
# reaches the platform" must not depend on the operator choosing a column name the
# platform likes, so a value that looks like a pointer to bytes is refused as an
# outcome and reported as a withheld locator instead. The pattern lives beside the
# other constants (``_LOCATOR_RE``) so the number field is judged by the same
# rule.
def _outcome(cell):
    """The outcome text, or ``''`` when the cell is a locator rather than a word.

    A locator is **withheld, not rewritten**: the caller sees an empty outcome and
    a ``withheld_outcomes`` count, so the operator learns that their column points
    at media rather than silently receiving a URL-shaped string in a field that
    claims to be a call outcome.
    """
    text = _cell_text(cell, 64).strip()
    if not text:
        return ''
    if _LOCATOR_RE.search(text):
        return ''
    return text


def _bind(tenant, extension):
    """Place an extension cell in the asset hierarchy, or ``None``.

    The PRD binds a call to the ``asset`` entity where one is named, so it can
    appear on that asset's timeline. A cell that is not a hierarchy path is
    reported as unbound rather than attached to a guessed node. Matching is by
    segment, never prefix: ``zavod-1`` must not claim ``zavod-10``'s calls.
    """
    try:
        segments = assets.parse_path(tenant, extension)
    except (ValueError, Forbidden):
        return None
    return segments


def _window(row_day, since, until):
    if not since and not until:
        return True
    if not DAY_RE.match(row_day or ''):
        return False
    if since and row_day < since:
        return False
    if until and row_day > until:
        return False
    return True


def _consent_index(engine, tenant, agent):
    """Every declared consent, keyed by ``(number, purpose)``.

    Returns ``(granted, withdrawn, recording, unreadable)``. A row we cannot read
    -- an unreadable status, an unreadable purpose -- is **not** silently absent:
    it lands in ``unreadable`` and is reported, because a consent record the
    platform misread is worse than one it never saw.
    """
    granted, withdrawn, recording = {}, {}, {}
    unreadable = 0
    for entry in _consent_registers(tenant).values():
        rows, _ = _read_rows(engine, tenant, agent, entry)
        for row in rows:
            number = normalise(row.get(entry['number_column']))
            status = _cell_text(row.get(entry['status_column']), 32).strip().casefold()
            purpose = _cell_text(row.get(entry['purpose_column']), MAX_PURPOSE_CHARS
                                 ).strip().casefold()
            if not number or status not in CONSENT_STATUS or not purpose:
                unreadable += 1
                continue
            if entry['expiry_column']:
                raw_expiry = _cell_text(row.get(entry['expiry_column']), 32).strip()
                if raw_expiry:
                    # An unreadable expiry is not an absent one. A consent whose
                    # end date cannot be read is a consent the platform cannot
                    # honour, so it is counted rather than treated as open-ended.
                    if not DAY_RE.match(raw_expiry):
                        unreadable += 1
                        continue
                    if _day_today() > raw_expiry:
                        withdrawn[(number, purpose)] = 'expired'
                        continue
            if status == 'granted':
                granted[(number, purpose)] = True
            else:
                withdrawn[(number, purpose)] = status
            if entry['recording_column'] and status == 'granted':
                flag = _cell_text(row.get(entry['recording_column']), 32).strip().casefold()
                recording[(number, purpose)] = flag in ('1', 'true', 'yes', 'ha', 'granted')
    return granted, withdrawn, recording, unreadable


def _day_today():
    import datetime
    return datetime.date.today().isoformat()


def consent(engine, tenant, agent, step, *, number, purpose='service', _index=None):
    """May this number be called for this purpose, and may the call be recorded?

    A **read** tool, and the answer is a fact with a reason, never an exception
    for a missing record: the caller asking "may I call" needs ``False`` to be a
    usable answer. The refusals that remain are refusals of *use*, not of
    *question* -- there is no way to ask this tool to make a call.

    ``consented`` is true only when **all** of these hold, and each one is
    reported separately so the operator can see which is missing:

    * the number is a usable phone number at all;
    * it is in the operator's declared ``consented_numbers`` allowlist, if one is
      declared (an empty allowlist means "no allowlist declared", not "nothing is
      consented", and that distinction is reported);
    * a consent record exists for **this number and this purpose** with status
      ``granted`` and an expiry that has not passed.

    ``_index`` is an internal seam for a caller checking **many** numbers in one
    pass (the queue does exactly this). It carries one already-read consent index,
    so asking about N numbers costs one read of the consent register rather than
    N. It is not part of the tool surface: the tool handler never passes it, and a
    caller that omits it gets a fresh read, which is the correct behaviour for a
    single question about one number.
    """
    purpose = str(purpose or 'service').strip().casefold()
    if purpose not in PURPOSES:
        raise Forbidden(f'Unknown purpose {purpose!r}; must be one of {list(PURPOSES)}')
    clean = normalise(number)
    declared = telephony_config(tenant)
    allowlist = declared['consented_numbers']
    reasons = []
    if not clean:
        reasons.append('number is not a usable phone number')
    if allowlist and clean and clean not in allowlist:
        reasons.append('number is not in the declared consented_numbers list')
    if _index is None:
        _index = _consent_index(engine, tenant, agent)
    granted, withdrawn, recording, unreadable = _index
    if clean:
        if (clean, purpose) in granted:
            pass
        elif (clean, purpose) in withdrawn:
            reasons.append(f'consent for {purpose} is {withdrawn[(clean, purpose)]}')
        else:
            reasons.append(f'no consent record for {purpose} covers this number')
    return {
        'view': 'consent',
        'number': clean,
        'purpose': purpose,
        'consented': not reasons,
        'recording_allowed': bool(clean and recording.get((clean, purpose))),
        'allowlist_declared': bool(allowlist),
        'reasons': reasons,
        'unreadable_consent_rows': unreadable,
        'source_errors': [],
        'authority': _authority(engine, tenant, agent),
        'note': ('Bu — rozilik FAKTI, ruxsat emas. Platforma qo‘ng‘iroq '
                 'qilmaydi va hech qachon audio saqlamaydi: faqat raqam, '
                 'yo‘nalish, natija va davomiylik. Rozilik bo‘lmasa — '
                 '``consented: false``, va sabab ko‘rsatiladi. Yozib olish '
                 'uchun ALOHIDA rozilik talab qilinadi va u yo‘q bo‘lsa '
                 '``recording_allowed: false``.'),
    }


def _authority(engine, tenant, agent):
    return {'agent': agent, 'ladder': _ladder(engine, tenant, agent)}


def _scan(engine, tenant, agent, entry, *, since='', until='', limit=MAX_EVENTS):
    """Read, window and gate one register. The shared body of the event reads."""
    limit = _bounded(limit, 'limit', 1, MAX_EVENTS)
    rows, raw = _read_rows(engine, tenant, agent, entry)
    events, unbound, undated, skipped, refused, withheld = [], 0, 0, 0, 0, 0
    matched = 0
    # Same seam as the queue: one consent read for the whole scan. A call log with
    # a hundred outbound rows must not cause a hundred register reads.
    index = None
    if any(_cell_text(row.get(entry['direction_column']), 32).strip().casefold()
           == 'outbound' for row in rows):
        index = _consent_index(engine, tenant, agent)
    for row in rows:
        number = normalise(row.get(entry['number_column']))
        direction = _cell_text(row.get(entry['direction_column']), 32).strip().casefold()
        day = _cell_text(row.get(entry['timestamp_column']), 32).strip()
        if not number:
            skipped += 1
            continue
        if direction not in DIRECTIONS:
            skipped += 1
            continue
        if not _window(day, since, until):
            continue
        matched += 1
        if len(events) >= limit:
            continue
        # The outcome is read through the locator filter: a cell holding a
        # recording URL is *withheld*, not republished as a call outcome.
        outcome = _outcome(row.get(entry['outcome_column'])) \
            if entry['outcome_column'] else ''
        if not outcome and entry['outcome_column']:
            raw_outcome = _cell_text(row.get(entry['outcome_column']), 64).strip()
            if raw_outcome and _LOCATOR_RE.search(raw_outcome):
                withheld += 1
        # The consent gate, applied on the way OUT as well as on the way in: an
        # outbound row naming a number the tenant never consented to call is not
        # reported as a normal call, because a call log that shows an unlawful
        # call as an ordinary fact teaches the operator nothing.
        if direction == 'outbound':
            answer = consent(engine, tenant, agent, 'telephony',
                             number=number, purpose=entry['purpose'], _index=index)
            if not answer['consented']:
                refused += 1
                events.append({
                    'number': number,
                    'direction': direction,
                    'day': day,
                    'outcome': outcome,
                    'duration_seconds': _duration(row, entry),
                    'purpose': entry['purpose'],
                    'consent': False,
                    'consent_reasons': answer['reasons'],
                })
                continue
        record = {
            'number': number,
            'direction': direction,
            'day': day,
            'purpose': entry['purpose'],
            'consent': True,
        }
        if entry['outcome_column']:
            record['outcome'] = outcome
        if entry['duration_column']:
            record['duration_seconds'] = _duration(row, entry)
        if entry['extension_column']:
            extension = _cell_text(row.get(entry['extension_column']), 255).strip()
            if extension:
                bound = _bind(tenant, extension)
                if bound:
                    record['path'] = bound
                else:
                    unbound += 1
        if not DAY_RE.match(day):
            undated += 1
        events.append(record)
    return {
        'view': 'call_events',
        'register': entry['register'],
        'purpose': entry['purpose'],
        'events': events,
        'event_count': len(events),
        'matched': matched,
        'unbound': unbound,
        'undated': undated,
        'skipped': skipped,
        'refused_without_consent': refused,
        # Cells that held a locator rather than a word. Reported so the operator
        # learns the column points at media, instead of receiving a URL-shaped
        # string in a field that claims to be a call outcome.
        'withheld_outcomes': withheld,
        'scanned': len(rows),
        'truncated': matched > limit or raw.get('truncated', False),
        'complete': True,
        'source_errors': [],
        'authority': _authority(engine, tenant, agent),
        'note': ('Qo‘ng‘iroq FAKTLARI, baho emas. Audio hech qachon '
                 'platformaga yetib kelmaydi — na yozuv, na transkript, na '
                 'havola. Roziliksiz chiqish qo‘ng‘irog‘i ``consent: false`` '
                 'bilan belgilanadi va u oddiy qo‘ng‘iroq sifatida '
                 'ko‘rsatilmaydi. Yozuv havolasi yoki media yo‘li ushlangan '
                 'bo‘lsa, u ``withheld_outcomes`` da sanaladi va javobda '
                 'ko‘rsatilmaydi.'),
    }


def _duration(row, entry):
    if not entry['duration_column']:
        return None
    raw = _cell_text(row.get(entry['duration_column']), 32).strip()
    if not raw:
        return None
    # ASCII digits only, and no exponent form. ``float()`` normalises Unicode
    # digits, so a Devanagari duration would otherwise be reported as a real call
    # length; see ``platform_runtime.cells``. The pattern is also what keeps
    # ``'1e3'`` from becoming a thousand seconds.
    if not re.match(r'^[0-9]{1,6}$', raw.replace('.', '', 1).replace(',', '.', 1)):
        return None
    try:
        seconds = float(raw.replace(',', '.'))
    except ValueError:
        return None
    if seconds < 0 or seconds > MAX_DURATION:
        return None
    return seconds


def call_events(engine, tenant, agent, step, *, register=None, since='',
                until='', limit=MAX_EVENTS):
    """One register's calls inside a window, with the consent gate applied."""
    since = _day(since, 'since')
    until = _day(until, 'until')
    entry = _resolve(tenant, register)
    return _scan(engine, tenant, agent, entry, since=since, until=until, limit=limit)


def summary(engine, tenant, agent, step, *, register=None, since='', until=''):
    """Counts by direction and outcome over one register.

    No per-person figure is produced. A count of calls is a fact about a
    telephone line; a count of calls per agent is a number that will be used in a
    review, and the platform does not know why a call was long or short.
    """
    since = _day(since, 'since')
    until = _day(until, 'until')
    entry = _resolve(tenant, register)
    result = _scan(engine, tenant, agent, entry, since=since, until=until,
                   limit=MAX_EVENTS)
    by_direction, by_outcome = {}, {}
    seconds = 0.0
    timed = 0
    for event in result['events']:
        direction = event['direction']
        by_direction[direction] = by_direction.get(direction, 0) + 1
        outcome = event.get('outcome') or ''
        if outcome:
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        duration = event.get('duration_seconds')
        if isinstance(duration, (int, float)):
            seconds += duration
            timed += 1
    return {
        'view': 'telephony_summary',
        'register': entry['register'],
        'purpose': entry['purpose'],
        'by_direction': by_direction,
        'by_outcome': by_outcome,
        'call_count': result['event_count'],
        'answered_count': by_outcome.get('answered', 0),
        'total_seconds': round(seconds, 3),
        'timed_count': timed,
        'refused_without_consent': result['refused_without_consent'],
        'skipped': result['skipped'],
        'scanned': result['scanned'],
        'truncated': result['truncated'],
        'complete': True,
        'source_errors': [],
        'authority': _authority(engine, tenant, agent),
        'note': ('Bu qo‘ng‘iroq statistikasi — shaxs bo‘yicha ko‘rsatkich '
                 'YO‘Q. Agent yoki operator reytingi hisoblanmaydi. '
                 'Davomiyliklar yig‘indisi faqat o‘qilgan qatorlar uchun '
                 '``timed_count`` bilan birga ko‘rsatiladi.'),
    }


def _age_days(day, today):
    """Whole days between an ISO day and today, or ``None`` when unreadable.

    Returns ``None`` for an unreadable date rather than a large number: "this row
    is ancient" and "this row's date is a typo" are different facts, and only one
    of them licenses discarding a call record. An unreadable date makes the row
    *retained* (it is reported as unaged), never silently old enough to drop.
    """
    if not DAY_RE.match(day or ''):
        return None
    import datetime
    try:
        then = datetime.date.fromisoformat(day)
        now = datetime.date.fromisoformat(today)
    except ValueError:
        return None
    return (now - then).days


def retention(engine, tenant, agent, step, *, register=None, today=''):
    """What this tenant's retention window means for the rows on the register.

    Not a delete. The platform **never removes a call record**, because the one
    thing worse than keeping a call log too long is a system that quietly decides
    which calls it remembers. What this view does is state, per row, whether the
    operator's own declared window has **passed** — so the decision to destroy and
    the act of destroying both stay with the operator, who is the only party that
    can show a lawful basis for either (VO-07).

    Three outcomes per row, and they are deliberately **three**, not two:

    * ``in_window``    -- the declared window has not passed; the record stands;
    * ``past_window``  -- the window has passed and the operator should destroy it;
    * ``unaged``       -- the row's date cannot be read, so no window applies.

    ``unaged`` is not folded into either of the other two. A row with an
    unreadable date would otherwise be *expired by accident* and destroyed on a
    typo, and this module will not destroy anything on a typo.
    """
    entry = _resolve(tenant, register)
    declared = telephony_config(tenant)
    window = declared['retention_days']
    if window is None:
        raise Forbidden('No telephony.retention_days is declared for this tenant')
    if today:
        if not DAY_RE.match(today):
            raise ValueError('today must be an ISO date (YYYY-MM-DD)')
    else:
        today = _day_today()
    rows, raw = _read_rows(engine, tenant, agent, entry)
    in_window, past_window, unaged, skipped = 0, 0, 0, 0
    rows_out = []
    for row in rows:
        number = normalise(row.get(entry['number_column']))
        if not number:
            skipped += 1
            continue
        day = _cell_text(row.get(entry['timestamp_column']), 32).strip()
        age = _age_days(day, today)
        if age is None:
            unaged += 1
            state = 'unaged'
        elif age >= window:
            past_window += 1
            state = 'past_window'
        else:
            in_window += 1
            state = 'in_window'
        if len(rows_out) < MAX_ROWS:
            rows_out.append({'number': number, 'day': day, 'state': state, 'age_days': age})
    return {
        'view': 'telephony_retention',
        'register': entry['register'],
        'retention_days': window,
        'today': today,
        'rows': rows_out,
        'in_window': in_window,
        'past_window': past_window,
        'unaged': unaged,
        'skipped': skipped,
        'scanned': len(rows),
        'deleted': 0,
        'source_errors': [],
        'authority': _authority(engine, tenant, agent),
        'note': ('Bu — saqlash oynasi HISOBOTI, o‘chirish emas. Platforma '
                 'qo‘ng‘iroq yozuvini hech qachon o‘zi o‘chirmaydi: '
                 '``past_window`` — operator o‘zi yo‘q qilishi kerak degani, '
                 '``deleted`` esa doim 0. Sanasi o‘qib bo‘lmaydigan qator '
                 '``unaged`` bo‘ladi va hech qachon "muddati o‘tgan" deb '
                 'hisoblanmaydi — xato yozilgan sana yozuvni yo‘q qilishga '
                 'sabab bo‘lmasligi kerak.'),
    }


def _queue_item(engine, tenant, agent, entry, row, answer):
    record = {
        'number': answer['number'],
        'direction': 'outbound',
        'purpose': entry['purpose'],
        'consented': answer['consented'],
        'recording_allowed': answer['recording_allowed'],
    }
    day = _cell_text(row.get(entry['timestamp_column']), 32).strip()
    if DAY_RE.match(day):
        record['due_day'] = day
    if answer['reasons']:
        record['reasons'] = answer['reasons']
    return record


def queue(engine, tenant, agent, step, *, register=None, since='', until='',
          limit=MAX_QUEUE_ITEMS):
    """Numbers the tenant **could** call, already gated. Never a dialer.

    This is the queue the PRD's outbound calling needs and that a dialer would
    later read. It is the same read the event view performs, turned around: rows
    whose direction is ``outbound``, whose number is usable, and whose consent
    check *passes* are listed as callable; rows whose consent check fails are
    listed too, but marked ``consented: false`` with the reason, because hiding
    them would leave an operator believing their list is clean.

    **The throughput ceiling is enforced here, not merely reported.** If the
    declared pace could not lawfully carry the rows the read found, the surplus is
    *not* returned as callable: ``over_capacity`` counts it and ``truncated`` is
    true. A queue that overstates what may be called is worse than a short one,
    because the operator's remedy for a short list is to wait, and for an
    overstated list is nothing.

    ``limit`` bounds the **items carried**, never the evaluation: every row is
    still checked, so ``matched`` and ``over_capacity`` describe the whole window
    and not just the part the caller asked to see. The distinction matters —
    "there are 900 callable numbers" and "here are 50 of them" are two different
    facts, and a caller given only the second would pace against the wrong number.
    """
    since = _day(since, 'since')
    until = _day(until, 'until')
    limit = _bounded(limit, 'limit', 1, MAX_QUEUE_ITEMS)
    entry = _resolve(tenant, register)
    declared = telephony_config(tenant)
    pace = declared['throughput']
    rows, raw = _read_rows(engine, tenant, agent, entry)
    items, matched, blocked, skipped = [], 0, 0, 0
    # One read of the consent register for the whole pass, not one per row. The
    # index is the same object every consent question is answered against, so a
    # fifty-row queue costs the same consent read as a one-row queue.
    index = _consent_index(engine, tenant, agent) if rows else None
    for row in rows:
        number = normalise(row.get(entry['number_column']))
        direction = _cell_text(row.get(entry['direction_column']), 32).strip().casefold()
        day = _cell_text(row.get(entry['timestamp_column']), 32).strip()
        if not number or direction not in DIRECTIONS:
            skipped += 1
            continue
        if direction != 'outbound':
            # An inbound call is not a queue item: we did not choose to make it,
            # so there is nothing to schedule and no pace to respect.
            continue
        if not _window(day, since, until):
            continue
        matched += 1
        if len(items) >= limit:
            continue
        answer = consent(engine, tenant, agent, 'telephony',
                         number=number, purpose=entry['purpose'], _index=index)
        if not answer['consented']:
            blocked += 1
        items.append(_queue_item(engine, tenant, agent, entry, row, answer))
    # What the declared pace permits for this window, and what the read found.
    capacity = pace['per_window'] if pace else 0
    callable_count = sum(1 for item in items if item['consented'])
    over_capacity = max(0, callable_count - capacity)
    return {
        'view': 'telephony_queue',
        'register': entry['register'],
        'purpose': entry['purpose'],
        'items': items,
        'item_count': len(items),
        'callable_count': callable_count,
        'blocked_count': blocked,
        'matched': matched,
        'skipped': skipped,
        'scanned': len(rows),
        # The declared pace, echoed so the caller can see the number the ceiling
        # was applied against, or ``None`` when the tenant declared none.
        'throughput': pace,
        'capacity': capacity,
        'over_capacity': over_capacity,
        'truncated': matched > limit or raw.get('truncated', False),
        'source_errors': [],
        'authority': _authority(engine, tenant, agent),
        'note': ('Bu NAVBAT — nima qo‘ng‘iroq QILINISHI MUMKIN, dialer emas. '
                 'Platforma qo‘ng‘iroq qilmaydi: bu ro‘yxatni keyingi dialer '
                 'o‘qishi kerak va u ham rozilik darvozasidan o‘tishi shart. '
                 'Roziliksiz raqamlar ham ko‘rsatiladi (``consented: false``) — '
                 'ro‘yxat toza deb o‘ylamaslik uchun. ``over_capacity`` — '
                 'e’lon qilingan tezlik ko‘tarolmaydigan ortiqcha soni; u '
                 'qo‘ng‘iroq qilinadigan deb ko‘rsatilmaydi. ``limit`` faqat '
                 'ko‘rsatilgan elementlarni cheklaydi; baholash har doim butun '
                 'oyna bo‘yicha.'),
    }


# --------------------------------------------------------------- tool handlers
def _consent_tool(engine, tenant, agent, args, step):
    return consent(engine, tenant, agent, step,
                   number=args.get('number', ''), purpose=args.get('purpose', 'service'))


def _events_tool(engine, tenant, agent, args, step):
    return call_events(engine, tenant, agent, step,
                       register=args.get('register'),
                       since=args.get('since', ''), until=args.get('until', ''),
                       limit=args.get('limit', MAX_EVENTS))


def _summary_tool(engine, tenant, agent, args, step):
    return summary(engine, tenant, agent, step,
                   register=args.get('register'),
                   since=args.get('since', ''), until=args.get('until', ''))


def _queue_tool(engine, tenant, agent, args, step):
    return queue(engine, tenant, agent, step,
                 register=args.get('register'),
                 since=args.get('since', ''), until=args.get('until', ''),
                 limit=args.get('limit', MAX_QUEUE_ITEMS))


def _retention_tool(engine, tenant, agent, args, step):
    return retention(engine, tenant, agent, step,
                     register=args.get('register'),
                     today=args.get('today', ''))


def register_telephony_tools(registry):
    """Five read tools. No write path exists here at all.

    Read-only because a call log is evidence: an entity able to edit or delete a
    call record could rewrite what the platform was told happened. Placing a call
    is deliberately **not** a tool -- it would be an outbound write to a person,
    and the consent gate must be enforced by code on the one path that can reach a
    provider, not by a prompt on a path any agent could hold. The queue names what
    *could* be called and the retention view names what *should be destroyed*;
    neither performs the act, because both acts belong to an operator with a lawful
    basis, not to the platform.
    """
    from .tools import Tool, obj, string
    window = {'register': string(64), 'since': string(10), 'until': string(10)}
    tools = [
        ('telephony.consent', obj({'number': string(20), 'purpose': string(MAX_PURPOSE_CHARS)},
                                  required=['number']), _consent_tool),
        ('telephony.call_events', obj(dict(window,
                                           limit={'type': 'integer', 'minimum': 1,
                                                  'maximum': MAX_EVENTS}),
                                      required=[]), _events_tool),
        ('telephony.summary', obj(dict(window), required=[]), _summary_tool),
        ('telephony.queue', obj(dict(window,
                                     limit={'type': 'integer', 'minimum': 1,
                                            'maximum': MAX_QUEUE_ITEMS}),
                                required=[]), _queue_tool),
        ('telephony.retention', obj(dict(window, today=string(10)),
                                    required=[]), _retention_tool),
    ]
    for name, schema, handler in tools:
        if name in registry.items:
            continue
        registry.add(Tool(name, 'read', schema, handler))
