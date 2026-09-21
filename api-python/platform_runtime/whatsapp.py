"""WhatsApp Cloud API channel: the 24-hour window is read, never guessed.

WhatsApp is not Telegram with a different URL. Meta's rules turn our existing
approval pattern into a legal constraint, and the whole module is shaped by one
sentence from the PRD:

    A free-form message outside the 24-hour service window is **never delivered**.
    The provider answers with error 131047.

An agent that believes it replied, while the provider silently refused, is worse
than an agent that could not reply at all: the customer is left waiting and the
operator's CRM looks answered. So the channel is built so that 131047 is
**impossible to reach by design** rather than being caught after the fact:

* ``whatsapp.window`` (read) reports, per recipient, whether the service window is
  open and exactly when it closes. The closing time is computed from *our* clock
  and the *last inbound* message time, so the answer is inspectable instead of
  being a provider opinion we re-ask for.
* ``whatsapp.send`` (write, approval) refuses free-form text when the window is
  closed **before any provider I/O**. It does not fall back to a template, because
  choosing a template category on the model's behalf is precisely how an account
  loses its quality rating.
* Free-form text inside the window is the *only* path to free-form text, and a
  template is the only path outside it. Sending a template inside the window is
  allowed because Meta allows it; sending text outside is refused outright.

Other rules that shape the code:

* **Two separate credentials.** ``whatsapp_business_messaging`` authorises sending
  and ``whatsapp_business_management`` authorises account inspection. They are
  distinct Meta scopes and this module keeps them in distinct config keys, so a
  send credential can never be used to inspect the account and vice versa.
* **Templates are operator configuration.** A template is declared with a name, a
  language and a category. The model selects a *declared* template by name and may
  not invent one, may not name a language and may not reclassify a category.
  Reclassifying a marketing template as utility to slip past a restriction is the
  exact abuse Meta punishes.
* **One number is one destination.** Every recipient is an operator-declared
  contact under a connection; a raw phone number is never accepted from a tool
  argument, because a model-supplied number is an unbounded send surface.
* **Free-form inside the window is free.** Meta prices per message from
  2025-07-01 but a free-form reply inside the service window is free, so the
  window read also reports that the cheap path is available.

Reads are bounded and the last-inbound timestamp comes from the operator's own
register or the provider, never invented.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .engine import Conflict, Forbidden, NotFound

# ------------------------------------------------------------------ constants

WHATSAPP_TOOLS = ('whatsapp.window', 'whatsapp.send', 'whatsapp.templates')

# Meta's own numbers. Kept as named constants so the rules are visible rather than
# scattered as literals, and so a change is a one-line, auditable edit.
WINDOW_SECONDS = 24 * 60 * 60
MAX_RESPONSE_BYTES = 200_000
MAX_RECIPIENTS = 200
MAX_TEMPLATES = 100
MAX_TEXT_CHARS = 4096
MAX_NAME_CHARS = 64
MAX_LANGUAGE_CHARS = 16
MAX_BODY_PARAMS = 20
MAX_PARAM_CHARS = 400

# The durable inbox channel the inbound block writes under, and how far back the
# window answer will scan for a verified message. The scan is bounded because an OT
# tenant's inbox grows without limit while the window only ever cares about the most
# recent message per contact: 500 rows is far more than the contacts a register may
# declare, and the query stops as soon as a contact is seen (newest-first).
INBOUND_CHANNEL = 'whatsapp'
MAX_EVENTS_SCANNED = 500

# Accepting these is what makes the account tier (250 -> 1000 -> ...) and the
# quality rating meaningful; anything else is a typo that Meta would reject.
TEMPLATE_CATEGORIES = ('marketing', 'utility', 'authentication')

# Only the Cloud API exists: On-Premises was sunset on 2025-10-23. A config that
# names on-premises is refused rather than silently treated as Cloud.
APIS = ('cloud',)

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')
LANGUAGE_RE = re.compile(r'^[a-z]{2}(?:_[A-Z]{2})?$')
# E.164, without a leading '+'. This is the shape Meta's own API uses. It is
# validated so a declared contact cannot smuggle a second destination.
PHONE_RE = re.compile(r'^[1-9][0-9]{6,14}$')

# The provider's error code for "outside the 24-hour window". It is named here
# only so the module can prove it never *sends* anything that could produce it;
# the module never parses it as a control-flow signal, because by then the
# customer has already waited.
ERROR_OUTSIDE_WINDOW = 131047


class WhatsAppError(RuntimeError):
    """A provider or configuration failure that is not the caller's fault.

    Distinguishing it from ``ValueError``/``Forbidden`` matters: this one is a
    transport or provider problem and may be retried or reported, whereas the
    others are deterministic refusals that must stay refusals.
    """


# -------------------------------------------------------------------- config


def _bounded(value, name, maximum):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f'{name} is required and must be at most {maximum} characters')
    return value


def _template(name, entry):
    """Validate one operator-declared template.

    A template cannot be expressed without a category: Meta requires one on
    submission and the category decides what Meta will let the account send.
    Letting the category be optional would let a declaration omit it and a later
    send inherit whatever the model guessed.
    """
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f'template name {name!r} must be lowercase letters, digits, '
                         f'dot, dash or underscore')
    if not isinstance(entry, dict):
        raise ValueError(f'template {name!r} must be an object')
    template_name = entry.get('name')
    if not isinstance(template_name, str) or not NAME_RE.match(template_name):
        raise ValueError(f'template {name!r} needs a Meta template name')
    language = entry.get('language')
    if not isinstance(language, str) or not LANGUAGE_RE.match(language):
        raise ValueError(f'template {name!r} language must look like "uz" or "uz_UZ"')
    category = entry.get('category')
    if category not in TEMPLATE_CATEGORIES:
        raise ValueError(f'template {name!r} category must be one of '
                         f'{", ".join(TEMPLATE_CATEGORIES)}')
    unknown = set(entry) - {'name', 'language', 'category', 'parameters'}
    if unknown:
        raise ValueError(f'template {name!r} has unknown keys: '
                         f'{", ".join(sorted(unknown))}')
    if 'parameters' in entry:
        params = entry['parameters']
        if not isinstance(params, list) or len(params) > MAX_BODY_PARAMS:
            raise ValueError(f'template {name!r} parameters must be a list of at most '
                             f'{MAX_BODY_PARAMS}')
        for value in params:
            if not isinstance(value, str) or len(value) > MAX_PARAM_CHARS:
                raise ValueError(f'template {name!r} parameters must be strings')
    return {'name': template_name, 'language': language, 'category': category,
            'parameters': list(entry.get('parameters', []))}


def _contacts(entry):
    """Operator-declared recipients: a stable id mapped to an E.164 number."""
    contacts = entry.get('contacts')
    if contacts is None:
        return {}
    if not isinstance(contacts, dict):
        raise ValueError('contacts must be an object of id -> phone')
    resolved = {}
    for contact_id, phone in contacts.items():
        if not isinstance(contact_id, str) or not NAME_RE.match(contact_id):
            raise ValueError(f'contact id {contact_id!r} must be lowercase letters, '
                             f'digits, dot, dash or underscore')
        if not isinstance(phone, str) or not PHONE_RE.match(phone):
            raise ValueError(f'contact {contact_id!r} phone must be E.164 digits '
                             f'without "+"')
        resolved[contact_id] = phone
    return resolved


def _register(name, entry):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f'whatsapp register {name!r} must be lowercase letters, '
                         f'digits, dot, dash or underscore')
    if not isinstance(entry, dict):
        raise ValueError(f'whatsapp register {name!r} must be an object')
    unknown = set(entry) - {'api', 'connection', 'phone_number_id', 'contacts',
                            'templates', 'window_register', 'window_range',
                            'last_inbound_column'}
    if unknown:
        raise ValueError(f'whatsapp register {name!r} has unknown keys: '
                         f'{", ".join(sorted(unknown))}')
    api = entry.get('api', 'cloud')
    if api not in APIS:
        raise ValueError(f'whatsapp register {name!r} api must be '
                         f'{", ".join(APIS)}: the On-Premises API was sunset on '
                         f'2025-10-23 and cannot be configured')
    connection = entry.get('connection')
    if not isinstance(connection, str) or not connection:
        raise ValueError(f'whatsapp register {name!r} needs a connection')
    phone_number_id = entry.get('phone_number_id')
    if not isinstance(phone_number_id, str) or not phone_number_id.isdigit():
        raise ValueError(f'whatsapp register {name!r} phone_number_id must be digits')
    templates = entry.get('templates', {})
    if not isinstance(templates, dict) or len(templates) > MAX_TEMPLATES:
        raise ValueError(f'whatsapp register {name!r} templates must be an object of '
                         f'at most {MAX_TEMPLATES}')
    resolved_templates = {key: _template(key, value)
                          for key, value in templates.items()}
    # The window's source. Reading the last inbound message time from the
    # operator's own sheet keeps the window answer inspectable: we compute it from
    # a timestamp we can point at, rather than re-asking the provider and trusting
    # whatever it says now.
    window_register = entry.get('window_register', '')
    window_range = entry.get('window_range', '')
    last_inbound_column = entry.get('last_inbound_column', '')
    if window_register:
        if not isinstance(window_register, str) or not NAME_RE.match(window_register):
            raise ValueError(f'whatsapp register {name!r} window_register must be a '
                             f'register name')
        if not isinstance(window_range, str) or not window_range:
            raise ValueError(f'whatsapp register {name!r} window_register needs a '
                             f'window_range')
        if not isinstance(last_inbound_column, str) or not last_inbound_column:
            raise ValueError(f'whatsapp register {name!r} window_register needs a '
                             f'last_inbound_column')
    return {
        'register': name, 'api': api, 'connection': connection,
        'phone_number_id': phone_number_id,
        'contacts': _contacts(entry),
        'templates': resolved_templates,
        'window_register': window_register, 'window_range': window_range,
        'last_inbound_column': last_inbound_column,
    }


def whatsapp_config(tenant):
    """Every ``whatsapp`` register this tenant declared, validated."""
    from .tools import config

    block = config(tenant).get('whatsapp') or {}
    if not isinstance(block, dict):
        raise ValueError('whatsapp configuration must be an object')
    registers = block.get('registers') or {}
    if not isinstance(registers, dict):
        raise ValueError('whatsapp registers must be an object')
    return {name: _register(name, entry) for name, entry in registers.items()}


def _registers(tenant):
    """Registers, or an empty map when the block is not declared at all.

    An absent block is a declared absence, not an error: a tenant that has not
    connected WhatsApp should see ``whatsapp.window`` report nothing rather than a
    crash. What must *not* happen is an absent block looking like an open window.
    """
    try:
        return whatsapp_config(tenant)
    except (RuntimeError, ValueError, KeyError, TypeError):
        return {}


def _resolve(tenant, register):
    """The declared register, or a refusal. An undeclared name is never an empty map."""
    entry = _registers(tenant).get(register)
    if entry is None:
        raise Forbidden(f'WhatsApp register {register!r} is not declared for this tenant')
    return entry


# ---------------------------------------------------------------- window math


def _now():
    return time.time()


def _engine_now(engine):
    """The engine's clock, falling back to the wall clock for a bare stub.

    The engine takes an injectable clock and every other time-dependent answer in
    the runtime reads it: deadlines, leases, approval expiry, escalation windows.
    The WhatsApp service window must read the SAME now, or the platform answers
    "is this customer inside the 24-hour window" against a different clock than
    the one it used to decide the message was due -- and a runtime driven by an
    injected clock (a test, a replay, a clock skew between hosts) would have its
    send gate decided by something it does not control.

    ``window_state`` keeps its default of the wall clock, because a caller with no
    engine and no clock is asking a pure question. Production call sites pass the
    engine clock explicitly.
    """
    clock = getattr(engine, 'clock', None)
    return clock() if callable(clock) else _now()


def window_state(last_inbound, now=None):
    """The service window as a pure function of the last inbound time.

    Returns ``(open, closes_at)``. A window that has never been opened by a
    customer message is closed, and a missing timestamp is therefore *closed*, not
    "unknown": treating unknown as open is how 131047 gets sent.
    """
    now = _now() if now is None else now
    try:
        last = float(last_inbound)
    except (TypeError, ValueError):
        return False, None
    if last <= 0:
        return False, None
    closes_at = last + WINDOW_SECONDS
    return (closes_at > now), closes_at


def _parse_timestamp(value):
    """Accept an epoch number or a timezone-aware ISO-8601 string.

    An ambiguous local format such as ``10.01.2026`` is *not* guessed, because
    guessing shifts the window by hours and produces exactly the 131047 failure
    this module exists to prevent. The caller reports it as unparsable instead.

    A timestamp without a timezone is refused too, for the same reason but a
    sharper one: Uzbekistan is UTC+5, so reading a naive ``2026-09-19 09:00:00``
    as UTC would open the window five hours early and place a send outside it.
    The register must therefore carry an offset or a ``Z``, and the example
    configuration says so.

    Fractional seconds are accepted, and that is not a nicety. An earlier revision
    matched only ``HH:MM`` and ``HH:MM:SS``, so ``2026-09-20T10:24:50.859388+05:00``
    fell through to "unparsable" and the window read as **closed** -- refusing a
    free-form reply to a customer who had just written. That form is what
    ``datetime.isoformat()``, JavaScript's ``Date.toISOString()``, PostgreSQL and Go
    emit by default, so the inputs most likely to appear in an operator's sheet were
    exactly the ones the parser rejected. The guard is now no stricter than
    ``datetime.fromisoformat``, which always could have read them; the bug was the
    regex, not the parser behind it.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(
            r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?'
            r'(Z|[+-]\d{2}:?\d{2})', text):
        try:
            from datetime import datetime

            cleaned = text.replace(' ', 'T')
            if cleaned.endswith('Z'):
                cleaned = cleaned[:-1] + '+00:00'
            return datetime.fromisoformat(cleaned).timestamp()
        except ValueError:
            return None
    return None


# ------------------------------------------------------------- provider reads

# Separated so a test can substitute a scripted transport and so the credential is
# never assembled anywhere else.
GRAPH_BASE = 'https://graph.facebook.com/v21.0'


def _bounded_json(url, token, *, method='GET', body=None, timeout=20):
    """One bounded provider call. No redirect, no retry: a failed call stays failed."""
    from .tools import NoRedirect

    headers = {'Authorization': 'Bearer ' + token}
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode('utf-8')
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise WhatsAppError('WhatsApp response exceeded the byte limit')
            return json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as error:
        # The provider body can echo the recipient, the phone number id or a token
        # fragment. Only the status is surfaced.
        raise WhatsAppError(f'WhatsApp HTTP status {error.code}') from None
    except WhatsAppError:
        raise
    except Exception:
        raise WhatsAppError('WhatsApp transport failure') from None


def _token(register, kind, tenant):
    """Read the credential for one of the two scopes.

    ``messaging`` and ``management`` are distinct config keys with distinct
    environment references. A single shared key would let a send credential read
    the whole account, which is exactly the split Meta's scopes exist to prevent.
    """
    from .tools import config, secret

    cfg = config(tenant)
    scopes = cfg.get('whatsapp_tokens')
    if not isinstance(scopes, dict):
        raise WhatsAppError('WhatsApp credentials are not configured for this tenant')
    scoped = scopes.get(register['register'])
    if not isinstance(scoped, dict):
        raise WhatsAppError(f'WhatsApp register {register["register"]!r} has no '
                            f'credentials')
    ref = scoped.get(kind)
    if not isinstance(ref, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', ref):
        raise WhatsAppError(f'WhatsApp {kind} credential reference is invalid')
    value = secret({kind + '_token_env': ref}, kind + '_token_env')
    return value


def _window_from_events(engine, tenant, contacts):
    """The last inbound time per contact, as recorded by *verified* messages.

    Returns ``{contact_id: last_inbound_epoch}`` for contacts this tenant has actually
    heard from, read from the durable inbox the inbound block writes:

        whatsapp_inbound.ingest -> accept_event(tenant, 'whatsapp', message_id,
                                   {... 'conversation_id': contact, 'window_until': ...})

    **The conversion is the subtle part, and getting it wrong is a real 131047 risk.**
    The event stores ``window_until``, which is the *expiry* (the customer's timestamp
    plus 24 hours). ``window_state`` takes the *last inbound time* and adds the 24 hours
    itself. Handing the stored expiry straight to ``window_state`` therefore adds the
    window twice, and reports a customer who wrote 25 hours ago as still inside the
    window -- precisely the send this module exists to refuse. So the expiry is
    converted back here, once, and the caller sees the same representation the register
    path produces.

    Only events for this tenant's ``whatsapp`` channel are considered, and only ones
    whose payload carries a usable ``window_until``. A contact with no such event is
    simply absent from the result, which lets the caller fall back to the operator's
    register rather than reading an absent event as "the window is closed".

    Why this exists at all. The register path has the window derived from a
    timestamp a human typed into a sheet, so the platform's licence to reply free-form
    rested on someone remembering to update a cell. A message Meta has signed is a
    fact the platform verified itself, and it is the fact the inbound block was built
    to record. Reading it here is what makes that block load-bearing rather than a
    write-only ledger.

    Deliberately not a tool. This is read by the window answer and the send gate, not
    exposed to a model: a model able to ask "what does the platform believe about this
    window" is fine, but the source of that belief must not be something it can
    choose.
    """
    if not contacts:
        return {}
    with engine.read() as cursor:
        rows = cursor.execute(
            "SELECT payload FROM p_events WHERE tenant=? AND channel=? "
            "ORDER BY rowid DESC LIMIT ?",
            (tenant, INBOUND_CHANNEL, MAX_EVENTS_SCANNED)).fetchall()
    known = set(contacts)
    latest = {}
    for row in rows:
        try:
            payload = json.loads(row['payload'])
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        contact = payload.get('conversation_id')
        if contact not in known or contact in latest:
            # The scan is newest-first, so the first sighting of a contact is the
            # latest message from them; later rows are older and cannot widen a
            # window that a newer message already set.
            continue
        stamp = payload.get('window_until')
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            # A message whose timestamp Meta did not supply records window_until as
            # null *on purpose* (see whatsapp_inbound.unwrap). Treating that null as
            # "closed" here would let an unparsable delivery erase a window a previous
            # good message opened, so the value is skipped and the contact remains
            # absent -- the caller then falls back to the register.
            continue
        # Stored as the EXPIRY; returned as the LAST INBOUND TIME, because that is what
        # window_state takes and what the register path yields. See the docstring: not
        # converting here adds the 24 hours a second time.
        latest[contact] = float(stamp) - WINDOW_SECONDS
    return latest


def _read_last_inbound(engine, tenant, agent, entry, step):
    """Read the last inbound message time from the operator's own register.

    Goes through the ordinary ``sheets.rows`` handler, so agent tool permission,
    the register declaration, the A1 allowlist and the connection allowlist all
    apply exactly as they do for a manual call. The window answer therefore
    inherits the same authority rules as every other read.

    This is the **fallback** source, not the primary one: ``_window_from_events``
    reads the window from the messages Meta actually delivered, and this register is
    consulted only for contacts the platform has no verified message from. See
    ``window_sources`` for why the two are not simply compared.
    """
    if not entry['window_register']:
        return {}
    tool = engine.registry.get('sheets.rows')
    args = {'register': entry['window_register'], 'range': entry['window_range'],
            'limit': MAX_RECIPIENTS}
    tool.validate(args)
    result = tool.handler(engine, tenant, agent, args, step)
    rows = result.get('rows')
    if not isinstance(rows, list):
        raise Conflict('WhatsApp window register returned an unexpected shape')
    column = entry['last_inbound_column']
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = row.get('contact') or row.get('contact_id') or row.get('id')
        if not isinstance(key, str) or not key:
            continue
        out[key] = row.get(column)
    return out


def window_sources(engine, tenant, agent, entry, step, *, contacts):
    """Resolve the last inbound time per contact from both sources, and say which won.

    Returns ``(resolved, sources, status)`` keyed by contact id, where:

    * ``resolved[contact]`` is ``(kind, epoch)``. ``kind`` is one of ``'absent'``
      (nothing recorded), ``'unparsable'`` (something recorded but not a time) or
      ``'at'`` (an epoch). The distinction is kept because "we have no message" and
      "we have a message we cannot read" are different facts for an operator, and the
      window answer reports them differently.
    * ``sources[contact]`` is ``'event'`` (a message Meta signed, recorded by the
      inbound block) or ``'register'`` (a timestamp the operator typed into a sheet).
    * ``status`` is ``'ok'`` or ``'unreadable'`` for the register fallback.

    The precedence rule, and why it is this and not "the newer of the two":

    * **A verified event wins outright.** When the platform holds a signed message
      from a contact, that is the fact the window is derived from. Taking the *newer*
      of the two sources would let a hand-typed sheet cell re-open a window the events
      say is closed, which is the platform granting itself permission from a value it
      did not verify.
    * **The register is the fallback, not a competitor.** A contact with no verified
      message is read from the register. That keeps a tenant mid-migration working and
      keeps the answer for a customer whose message predates the inbound block.
    * **An unreadable register is not an empty one.** The caller gets
      ``status='unreadable'`` and event-derived rows are still used, so the failure
      narrows the answer rather than blanking it.

    ``entry`` is the register declaration, used only for the sheet fallback, so a
    tenant that declared no ``window_register`` simply has no fallback and reads
    purely from events.
    """
    verified = _window_from_events(engine, tenant, contacts)
    status = 'ok'
    registered = {}
    try:
        raw = _read_last_inbound(engine, tenant, agent, entry, step)
    except Forbidden:
        # Authority refusals are re-raised, not softened into "no data": a window that
        # reports closed because we were not allowed to look would push the agent to a
        # template it did not need.
        raise
    except (Conflict, ValueError, LookupError, WhatsAppError):
        raw, status = {}, 'unreadable'
    for contact_id, value in raw.items():
        if contact_id in contacts:
            registered[contact_id] = value

    resolved, sources = {}, {}
    for contact_id in contacts:
        if contact_id in verified:
            resolved[contact_id] = ('at', _parse_timestamp(verified[contact_id]))
            sources[contact_id] = 'event'
            continue
        value = registered.get(contact_id)
        if value in (None, ''):
            resolved[contact_id] = ('absent', None)
        else:
            parsed = _parse_timestamp(value)
            resolved[contact_id] = (('at', parsed) if parsed is not None
                                    else ('unparsable', None))
        sources[contact_id] = 'register'
    return resolved, sources, status


# --------------------------------------------------------------- read function


def window(engine, tenant, agent, step, *, contact=''):
    """Report the 24-hour service window per declared recipient.

    Free-form text is only deliverable while this says ``open: true``. The answer
    is computed from our clock and the last inbound timestamp, so it can be
    explained to an operator instead of being re-asked of the provider.
    """
    registers = _registers(tenant)
    if not registers:
        return {'registers': [], 'complete': True,
                'note': 'No WhatsApp register is declared for this tenant'}
    reports = []
    for name in sorted(registers):
        entry = registers[name]
        contacts = entry['contacts']
        if contact:
            if contact not in contacts:
                raise Forbidden(f'Contact {contact!r} is not declared for register '
                                f'{name!r}')
            contacts = {contact: contacts[contact]}
        resolved, sources, status = window_sources(
            engine, tenant, agent, entry, step, contacts=contacts)
        now = _engine_now(engine)
        rows = []
        for contact_id, phone in sorted(contacts.items()):
            kind, last = resolved.get(contact_id, ('absent', None))
            if kind == 'at' and isinstance(last, (int, float)):
                opened, closes_at = window_state(last, now)
                reason = 'window open' if opened else 'window closed'
            else:
                opened, closes_at = False, None
                reason = ('unparsable timestamp' if kind == 'unparsable'
                          else 'no inbound message')
            rows.append({
                'contact': contact_id,
                # Which fact the answer came from. An operator asking "why does the
                # platform think this customer is inside the window" must be able to
                # see whether it was a message Meta signed or a cell someone typed.
                'source': sources.get(contact_id, 'register'),
                # The number is masked: the window answer does not need to echo the
                # recipient, and an unmasked number in a report is a needless copy
                # of personal data.
                'phone': _mask(phone),
                'open': opened,
                'closes_at': closes_at,
                'last_inbound': last,
                'free_form': opened,
                'reason': reason,
            })
        reports.append({'register': name, 'status': status,
                        'window_seconds': WINDOW_SECONDS, 'contacts': rows})
    return {'registers': reports, 'complete': all(r['status'] == 'ok' for r in reports)}


def _mask(phone):
    if not isinstance(phone, str) or len(phone) < 4:
        return '***'
    return phone[:2] + '*' * max(1, len(phone) - 4) + phone[-2:]


# --------------------------------------------------------------- send function


def send(engine, tenant, agent, args, step):
    """Send one message — template or, only inside the window, free-form text.

    This is a ``write`` tool, so the engine still requires a human approval before
    anything reaches a customer. But approval alone is not enough: an approved
    message that the provider will refuse is still a broken promise to the
    customer. So the window is checked **here, before any provider I/O**, and a
    free-form send outside the window is refused outright rather than being
    silently downgraded to a template.
    """
    register = args.get('register')
    contact = args.get('contact')
    text = args.get('text')
    template = args.get('template')
    has_text = isinstance(text, str) and text.strip() != ''
    has_template = isinstance(template, str) and template != ''
    if has_text == has_template:
        raise ValueError('Provide exactly one of text or template, not both or neither')
    if has_text and len(text) > MAX_TEXT_CHARS:
        raise ValueError(f'text must be at most {MAX_TEXT_CHARS} characters')

    entry = _resolve(tenant, register)
    contacts = entry['contacts']
    if contact not in contacts:
        raise Forbidden(f'Contact {contact!r} is not declared for register {register!r}')

    if has_template:
        declared = entry['templates'].get(template)
        if declared is None:
            raise Forbidden(f'Template {template!r} is not declared for register '
                            f'{register!r}. A template is operator configuration: '
                            f'the model selects one, it does not compose one')
        payload = _template_payload(declared)
    else:
        # The window is the gate. Checked before any provider I/O so a closed
        # window cannot produce a send attempt, and therefore cannot produce
        # 131047.
        #
        # The window is resolved through window_sources, so a message Meta signed and
        # the inbound block recorded takes precedence over the operator's sheet. Reading
        # the sheet alone here (as this did before the inbound block existed) meant a
        # customer's real message was ignored whenever nobody had updated the cell.
        resolved, sources, _status = window_sources(
            engine, tenant, agent, entry, step, contacts={contact: contacts[contact]})
        _kind, last = resolved.get(contact, ('absent', None))
        opened, _closes_at = window_state(last, _engine_now(engine))
        if not opened:
            raise Forbidden(
                f'Free-form text cannot be sent to {contact!r}: the 24-hour service '
                f'window is closed (the window is read from {sources.get(contact, "register")}'
                f' evidence). WhatsApp would refuse it with error '
                f'{ERROR_OUTSIDE_WINDOW} and the customer would never receive it. '
                f'Send a declared template instead, or wait for the customer to '
                f'message first')
        payload = {'type': 'text', 'text': {'preview_url': False, 'body': text}}

    body = {'messaging_product': 'whatsapp', 'to': contacts[contact],
            'recipient_type': 'individual', **payload}
    token = _token(entry, 'messaging', tenant)
    phone_number_id = entry['phone_number_id']
    target = f'{GRAPH_BASE}/{phone_number_id}/messages'
    result = _bounded_json(target, token, method='POST', body=body)
    if not isinstance(result, dict):
        raise WhatsAppError('WhatsApp response was not an object')
    messages = result.get('messages')
    external_id = ''
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        external_id = str(messages[0].get('id') or '')
    return {'provider': 'whatsapp', 'register': register, 'contact': contact,
            'kind': 'template' if has_template else 'text',
            'external_id': external_id}


def _template_payload(declared):
    body = {'type': 'template',
            'template': {'name': declared['name'],
                         'language': {'code': declared['language']}}}
    if declared['parameters']:
        body['template']['components'] = [{
            'type': 'body',
            'parameters': [{'type': 'text', 'text': value}
                           for value in declared['parameters']],
        }]
    return body


def templates(engine, tenant, agent, step, *, register=''):
    """List the operator-declared templates with name, language and category.

    Read-only, and the category is reported because it is the field that decides
    whether a send is permitted at all. There is no tool to create, edit or submit
    a template: submission is a Meta review process with an operator's legal
    responsibility attached, not an agent action.
    """
    registers = _registers(tenant)
    if register:
        entry = _resolve(tenant, register)
        registers = {register: entry}
    rows = []
    for name in sorted(registers):
        for key, declared in sorted(registers[name]['templates'].items()):
            rows.append({'register': name, 'template': key,
                         'name': declared['name'],
                         'language': declared['language'],
                         'category': declared['category']})
    return {'templates': rows}


# ------------------------------------------------------------------- handlers


def _window_tool(engine, tenant, agent, args, step):
    return window(engine, tenant, agent, step, contact=args.get('contact', ''))


def _templates_tool(engine, tenant, agent, args, step):
    return templates(engine, tenant, agent, step, register=args.get('register', ''))


def register_whatsapp_tools(registry):
    """Two read tools and one write tool.

    The write tool is a plain ``write``: the engine requires an approval for it
    because it reaches a customer, and this module adds the window rule on top.
    Note there is no tool to inspect the account with the management credential —
    that scope exists in configuration so the operator can wire account-level
    health checks, but no agent action uses it, because an agent needs to message
    and has no business reading the account.
    """
    from .tools import Tool, obj, string

    definitions = [
        ('whatsapp.window', 'read',
         obj({'contact': string(64)}, required=[]), _window_tool),
        ('whatsapp.templates', 'read',
         obj({'register': string(64)}, required=[]), _templates_tool),
        ('whatsapp.send', 'write',
         obj({'register': string(64), 'contact': string(64), 'text': string(MAX_TEXT_CHARS),
              'template': string(64)}, required=['register', 'contact']),
         send),
    ]
    for name, risk, schema, handler in definitions:
        if name in registry.items:
            continue
        registry.add(Tool(name, risk, schema, handler, external=(risk != 'read')))
