"""WhatsApp inbound: verify a Meta webhook, then hand it to the durable inbox.

This is the other half of P8. The outbound block (``whatsapp.py``) answers "may I
send this, and how". This block answers "did this really come from Meta, and what
did it say" — and it exists because the two questions are not the same question.

The 24-hour service window **opens when the customer writes**. Before this block,
nothing could write that fact: the window was read from a sheet an operator
maintained by hand, which means the platform's licence to reply free-form depended
on a human remembering to type a timestamp. That is a fragile basis for a legal
constraint, so the inbound path closes it.

What this module is NOT is a web server. It takes bytes and headers, and returns a
decision. Deliberately: an HTTP framework is a dependency, a request object is a
place for untrusted input to look structured, and every framework's "already parsed
the body for you" convenience is exactly the thing that breaks signature
verification. A caller with a FastAPI route does three lines of work and gets a
function that is testable without a socket.

The rules, in the order they are applied:

1. **The signature is verified over the RAW bytes, before anything is parsed.**
   Meta signs the exact bytes it sent. Re-serialising the parsed JSON — even
   ``json.dumps(json.loads(body))`` with no change — can reorder keys or change
   whitespace and will invalidate the signature. Verification therefore takes
   ``bytes``, never a dict, and this is why the function signature cannot be
   "convenient".

2. **A missing signature is a refusal, not a warning.** An endpoint that ingests
   unverified webhooks accepts messages from anyone who knows the URL, which means
   anyone can open a 24-hour window and any agent holding ``whatsapp.send`` will
   believe the customer wrote.

3. **The comparison is constant-time.** A byte-by-byte early-exit leaks how many
   leading characters of a forged signature were right, which is enough to forge
   one a character at a time.

4. **Only a message from a declared contact becomes an event.** Meta delivers
   status callbacks (sent/delivered/read) and messages from numbers the operator
   never declared through the same endpoint. Both are dropped with a reason rather
   than ingested: a status callback is not something a customer said, and an
   undeclared number is not a customer of this tenant. Dropping is not an error —
   Meta retries non-2xx responses, so a refusal must be a *decision*, recorded,
   not a thrown exception that produces a retry storm.

5. **Deduplication is the engine's, not this module's.** ``accept_event`` already
   keys on ``(tenant, channel, event_key)`` with a fingerprint conflict check, so
   Meta's at-least-once redelivery is absorbed there. This module supplies the
   event key (the WhatsApp message id) and does not invent a second dedup scheme;
   two dedup layers with different keys is how a duplicate gets through the one
   that was not checked.

The one thing this block adds to the engine's event payload is the **window
attribute**: it records that the customer wrote, at what time, so the outbound
block's window answer stops depending on a hand-maintained sheet. That is the
whole point of the block.
"""
import hashlib
import hmac
import json
import re

from . import cells
from .engine import Conflict, Forbidden, encode

# ------------------------------------------------------------------ constants

INGEST_TOOLS = ('whatsapp.webhook', 'whatsapp.verify')

# The header Meta signs with. A webhook that ignores it is an open door.
SIGNATURE_HEADER = 'x-hub-signature-256'
SIGNATURE_PREFIX = 'sha256='

# Meta's own retry budget is generous and its payloads are small; a body beyond this
# is not a message, it is an attempt to make us allocate.
MAX_BODY_BYTES = 1_000_000
MAX_ENTRIES = 50
MAX_CHANGES = 50
MAX_MESSAGES_PER_CHANGE = 50
MAX_TEXT_CHARS = 4096
MAX_STATUS_CHARS = 32

# The event key is the WhatsApp message id. Bounded and charset-restricted because it
# becomes part of a UNIQUE key and of audit rows.
MESSAGE_ID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')
WA_ID_RE = re.compile(r'^[1-9][0-9]{6,19}$')

# Meta's envelope. The object name is checked because the same endpoint can be
# subscribed to other products, and ingesting a Messenger event as a WhatsApp
# message would attribute words to a customer that never wrote them.
WEBHOOK_OBJECT = 'whatsapp_business_account'
MESSAGE_FIELD = 'messages'

# Message types that carry something a planner can act on. Everything else arrives
# as a type we report and drop, because pretending an image is text is worse than
# saying we cannot read it.
TEXT_TYPES = ('text',)
KNOWN_TYPES = ('text', 'image', 'audio', 'video', 'document', 'sticker', 'location',
               'contacts', 'button', 'interactive', 'order', 'system', 'reaction',
               'unsupported', 'request_welcome', 'ephemeral', 'edit', 'revoke')

# Delivery states Meta reports back. Recognised so we can name them; never turned
# into an inbound message.
STATUS_STATES = ('sent', 'delivered', 'read', 'failed', 'deleted', 'warning')

# Why an item was dropped. A reason, never an exception: Meta retries a non-2xx, so
# a malformed payload would be redelivered forever if the only response were a raise.
DROP_REASONS = ('not_a_message', 'status_callback', 'undeclared_contact',
                'unsupported_type', 'empty_text', 'field_not_subscribed')

CHANNEL = 'whatsapp'

# The service window the inbound message opens. Defined here rather than imported
# from the outbound block so this module has no import edge to it at module scope:
# the outbound module is imported lazily, in ``declared_contacts``, which is the one
# place the two genuinely need to agree.
#
# Restating it is a deliberate trade, and the price of a restatement is that it can
# drift -- so the equality is now ENFORCED rather than assumed:
# ``test_whatsapp.test_the_two_service_window_constants_are_one_fact`` fails if the
# two ever differ. The drift would be silent AND asymmetric. The stored
# ``window_until`` is written with THIS constant; the outbound reader subtracts its
# own copy and ``window_state`` adds it back, so the pair cancels and the events
# path follows THIS value, while the operator-register path follows the outbound
# one. Measured: with the outbound constant set to one hour, the events path still
# yielded twenty-four hours while the register path yielded one.
WINDOW_SECONDS = 24 * 60 * 60


class WebhookError(RuntimeError):
    """A refusal about a webhook, not a transport failure."""


# ------------------------------------------------------------------ signature


def verify_signature(body, signature, app_secret):
    """Is ``signature`` Meta's HMAC-SHA256 of exactly these bytes?

    ``body`` must be the raw bytes as received. Passing a parsed dict is not
    accepted, and that is the point rather than an inconvenience: Meta signs the
    bytes it sent, so anything that has been through a JSON round trip is a
    different byte string and the comparison would fail for a legitimate request —
    which is how a developer concludes the signature check "does not work" and
    disables it.

    Returns True or False. It does not raise on a bad signature, because the caller
    decides what a failure means (401 versus a logged drop), and a boolean is
    harder to accidentally ignore than an exception someone forgot to catch.
    """
    if not isinstance(body, (bytes, bytearray)):
        raise WebhookError('the webhook body must be the raw bytes: Meta signs the '
                           'exact bytes it sent, and a parsed value is a different '
                           'byte string')
    # The size guard lives here rather than in ``ingest`` because this is the first
    # place the raw bytes are held, and refusing an oversized body before hashing it
    # is the point: MAX_BODY_BYTES was declared and documented as enforced in an
    # earlier revision while nothing consulted it, so an oversized body was hashed,
    # parsed and walked. Bounded here, the allocation it was protecting against
    # cannot happen.
    if len(body) > MAX_BODY_BYTES:
        raise WebhookError(f'the webhook body exceeds {MAX_BODY_BYTES} bytes')
    if not isinstance(signature, str) or not signature:
        return False
    if not isinstance(app_secret, str) or not app_secret:
        return False
    if not signature.startswith(SIGNATURE_PREFIX):
        # Meta always prefixes the algorithm. A bare hex digest means something
        # else computed it, and guessing which algorithm is how a check degrades.
        # Lower-cased first, and compared against the lower-cased digest below, so
        # the prefix check and the digest check agree about case. They did not in an
        # earlier revision: the digest was case-insensitive while this line was not,
        # so a spec-compliant sender that wrote "SHA256=" was refused by the prefix
        # test before the tolerant comparison could run -- a rejection that looked
        # like an authentication failure and was actually a formatting mismatch.
        if not signature.lower().startswith(SIGNATURE_PREFIX):
            return False
    provided = signature[len(SIGNATURE_PREFIX):]
    if len(provided) != 64:
        # A SHA-256 hex digest is exactly 64 characters. Length is public
        # information, so rejecting early leaks nothing and avoids a comparison
        # against a value that cannot match.
        return False
    expected = hmac.new(app_secret.encode('utf-8'), bytes(body),
                        hashlib.sha256).hexdigest()
    # Constant-time: an early exit would leak how many leading characters of a
    # forged signature were correct, which is enough to reconstruct one byte at a
    # time. compare_digest does not short-circuit.
    return hmac.compare_digest(expected, provided.lower())


def verify_challenge(query, verify_token):
    """The one-time GET handshake: echo the challenge, or refuse.

    Meta sends ``hub.mode=subscribe``, ``hub.verify_token`` and ``hub.challenge``
    as **query** parameters — not headers, not a body — and expects the challenge
    echoed verbatim as the response body. This is the only place an unauthenticated
    caller gets a value reflected back, so the reflection is gated on an exact token
    match and on the mode, and a mismatch returns nothing rather than a hint about
    which of the two was wrong.
    """
    if not isinstance(query, dict):
        raise WebhookError('the webhook query must be an object')
    if not isinstance(verify_token, str) or not verify_token:
        raise WebhookError('a webhook verify token must be configured')
    mode = query.get('hub.mode')
    token = query.get('hub.verify_token')
    challenge = query.get('hub.challenge')
    if mode != 'subscribe':
        return None
    if not isinstance(token, str) or not hmac.compare_digest(token, verify_token):
        return None
    if not isinstance(challenge, str) or len(challenge) > 256:
        return None
    return challenge


# ------------------------------------------------------------- envelope walk

def _text(message):
    """The text of a text message, or None. Never guesses at another type."""
    body = message.get('text')
    if not isinstance(body, dict):
        return None
    value = body.get('body')
    if not isinstance(value, str):
        return None
    return value


def _message(message, contacts, declared):
    """Map one Meta message to a decision: an event, or a drop with a reason.

    ``declared`` maps an E.164 phone (as Meta sends it, without "+") to the
    operator's contact id. An undeclared number is dropped rather than ingested: the
    operator's contact list is the definition of who this tenant's customers are, and
    the outbound side already refuses to send to anyone outside it — so ingesting
    from outside it would create a sender who can never be answered, and a window
    that opens for a stranger.
    """
    if not isinstance(message, dict):
        return None, 'not_a_message', {}
    message_id = message.get('id')
    wa_id = message.get('from')
    kind = message.get('type')
    timestamp = message.get('timestamp')
    if not isinstance(message_id, str) or not MESSAGE_ID_RE.match(message_id):
        return None, 'not_a_message', {}
    if not isinstance(wa_id, str) or not WA_ID_RE.match(wa_id):
        return None, 'not_a_message', {}
    # ``declared`` maps an E.164 phone to the operator's contact id, so this is the
    # CONTACT ID, not a phone number. The name matters: an earlier revision called it
    # ``phone`` and then put it in ``conversation_id``, which read as "the customer's
    # number" and led a reviewer to assert that the payload carried the phone. It does
    # not, and it must not: the outbound side names its destination with the operator's
    # contact id (whatsapp.send's field is 'contact', and allowed_recipients holds
    # 'ali', not '998901234567').
    #
    # Note what is and is not true here, because an earlier revision got this wrong in
    # both directions. The engine does *not* compare this conversation_id against the
    # outbound destination for WhatsApp: engine._submit binds a reply to the verified
    # inbound event only for channels in OUTBOUND_CHANNELS -- {'telegram','instagram'}
    # -- and whatsapp is deliberately absent, so a whatsapp.send is authorized by the
    # pack's static allowed_recipients list. What the engine and the outbound adapter
    # *do* now share is the value stored here: whatsapp.window_sources reads
    # window_until from these events and gives it precedence over the operator's sheet
    # register, so the fact this block records is the one the send gate acts on.
    contact = declared.get(wa_id)
    if contact is None:
        # The number is masked into the drop record: the reason a stranger was
        # dropped is worth recording, the stranger's number is not worth storing.
        return None, 'undeclared_contact', {'wa_id': _mask_number(wa_id)}
    if kind not in TEXT_TYPES:
        return None, 'unsupported_type', {'wa_id': _mask_number(wa_id),
                                          'type': kind if isinstance(kind, str) else None}
    text = _text(message)
    if text is None or not text.strip():
        return None, 'empty_text', {'wa_id': _mask_number(wa_id)}
    name = ''
    profile = contacts.get(wa_id)
    if isinstance(profile, dict) and isinstance(profile.get('name'), str):
        name = profile['name'][:MAX_TEXT_CHARS]
    return {
        # Both are the operator's contact id, so an outbound reply can be matched to
        # the inbound event that opened the window (engine _submit compares the
        # destination against the event's conversation_id).
        'sender': contact,
        'conversation_id': contact,
        'text': text[:MAX_TEXT_CHARS],
        'profile_name': name,
        'wa_id': wa_id,
        'message_id': message_id,
        'timestamp': _epoch(timestamp),
    }, '', {}


def _epoch(value):
    """Meta sends the timestamp as a Unix epoch in a **string**.

    Returned as an int, because the window arithmetic compares it against our clock
    and a string comparison of ``'989...'`` against a number is the kind of bug that
    silently reports every window open.

    ``str.isdigit()`` is Unicode-aware -- it is true for Devanagari, Arabic-Indic and
    fullwidth digits -- and ``int()`` normalises them, so ``'१२३'`` used to become
    epoch 123 and a customer's window would be computed from it. Only ASCII digits
    are accepted; see ``platform_runtime.cells``.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and cells.is_ascii_digit_run(value):
        return int(value)
    return None


def _mask_number(value):
    if not isinstance(value, str) or len(value) < 4:
        return '***'
    return value[:2] + '*' * max(1, len(value) - 4) + value[-2:]


def _change(change, declared, now):
    """Walk one ``changes`` entry. Returns (messages, drops)."""
    if not isinstance(change, dict):
        return [], [{'reason': 'not_a_message', 'field': None}]
    field = change.get('field')
    value = change.get('value')
    if field != MESSAGE_FIELD:
        # The endpoint can be subscribed to other fields (message_template_status_update,
        # account_update and others). They are not customer words.
        return [], [{'reason': 'field_not_subscribed', 'field': field}]
    if not isinstance(value, dict):
        return [], [{'reason': 'not_a_message', 'field': field}]
    contacts = {}
    for profile in value.get('contacts') or []:
        if isinstance(profile, dict) and isinstance(profile.get('wa_id'), str):
            contacts[profile['wa_id']] = {'name': profile.get('profile', {}).get('name')
                                          if isinstance(profile.get('profile'), dict)
                                          else ''}
    messages, drops = [], []
    for raw in (value.get('messages') or [])[:MAX_MESSAGES_PER_CHANGE]:
        event, reason, detail = _message(raw, contacts, declared)
        if event is not None:
            messages.append(event)
        else:
            drops.append({'reason': reason, **detail, 'field': field})
    for raw in (value.get('statuses') or [])[:MAX_MESSAGES_PER_CHANGE]:
        state = raw.get('status') if isinstance(raw, dict) else None
        drops.append({'reason': 'status_callback',
                      'status': state if state in STATUS_STATES else 'unrecognised'})
    return messages, drops


def unwrap(payload, declared, now=None):
    """Turn a verified Meta webhook body into engine-ready events.

    ``declared`` maps an E.164 phone to the operator's contact id and is the
    allowlist: nothing outside it becomes an event.

    ``window_until`` is derived **only** from Meta's own timestamp, never from our
    wall clock. When the timestamp is absent or unparsable the window is reported as
    ``None``, meaning "not opened by this message", rather than being opened at the
    time of processing. An earlier revision fell back to the host clock, which has
    two defects: the value is non-deterministic (so no test can assert it), and worse,
    it opens a 24-hour window based on when *our server* happened to process a
    delivery. That is a licence to message a customer derived from a fact about
    ourselves rather than about the customer, and it would also make the window
    depend on how long Meta's retry queue was.

    ``now`` is accepted for the caller's convenience but is no longer consulted for
    the window: it must not be, for the reason above.
    """
    if not isinstance(payload, dict):
        raise WebhookError('the webhook body must be a JSON object')
    if payload.get('object') != WEBHOOK_OBJECT:
        raise WebhookError(f'the webhook object must be {WEBHOOK_OBJECT!r}: another '
                           f'product\'s event delivered to this endpoint would be '
                           f'read as a customer message')
    entries = payload.get('entry')
    if not isinstance(entries, list):
        raise WebhookError('the webhook entry must be a list')
    events, drops = [], []
    for entry in entries[:MAX_ENTRIES]:
        if not isinstance(entry, dict):
            drops.append({'reason': 'not_a_message'})
            continue
        for change in (entry.get('changes') or [])[:MAX_CHANGES]:
            found, refused = _change(change, declared, now)
            events.extend(found)
            drops.extend(refused)
    for event in events:
        stamp = event.get('timestamp')
        event['window_until'] = stamp + WINDOW_SECONDS if isinstance(stamp, int) \
            else None
        if event['window_until'] is None:
            event['window_reason'] = ('Meta supplied no usable timestamp, so this '
                                      'message does not open the window; the '
                                      'operator-maintained register still does')
    return {'events': events, 'dropped': drops, 'complete': True}


# ------------------------------------------------------------------ ingest


def declared_contacts(tenant):
    """Every declared contact across this tenant's WhatsApp registers, phone -> id.

    Built from the outbound block's own configuration so the inbound allowlist and
    the outbound allowlist are the *same list read once*, rather than two lists that
    can disagree. A number that can be written to but not read from (or the reverse)
    is a support ticket, not a policy.

    An ambiguous declaration is a **refusal**, not a last-one-wins. Two registers
    declaring the same number under different contact ids used to resolve to whichever
    register iterated last, which is dict insertion order — so reordering a config
    file silently changed which customer a customer was. The same person would appear
    as two different contacts to the window logic depending on the order the operator
    happened to type their registers, and nothing said so. Naming the conflict is the
    only reading that cannot quietly attribute a customer's words to someone else.
    """
    from .whatsapp import _registers

    resolved = {}
    owners = {}
    for name, entry in _registers(tenant).items():
        for contact_id, phone in entry['contacts'].items():
            seen = owners.get(phone)
            if seen is not None and seen[1] != contact_id:
                raise WebhookError(
                    f'WhatsApp number {_mask_number(phone)} is declared by two '
                    f'registers under different contact ids: {seen[0]!r} ({seen[1]!r}) '
                    f'and {name!r} ({contact_id!r}). An inbound message from this '
                    f'number cannot be attributed to one customer, so it is refused '
                    f'rather than guessed')
            owners[phone] = (name, contact_id)
            resolved[phone] = contact_id
    return resolved


def _app_secret(tenant):
    """The Meta app secret, read as a reference the operator set.

    Read through the same ``secret`` indirection as every other credential in this
    runtime: configuration holds the *name* of the environment variable, never the
    value, so a config file can be committed and reviewed.

    ``secret()`` **raises** rather than returning an empty string, so there is no
    falsy return to check here. An earlier revision wrote ``if not value: raise
    WebhookError(...)`` after the call, which is unreachable code: the raise above it
    always won. The RuntimeError it raises is caught and re-raised as a WebhookError
    so a caller sees a refusal that names the webhook configuration rather than a
    generic missing-credential message from deep in the tool layer.
    """
    from .tools import config, secret

    block = config(tenant).get('whatsapp_webhook') or {}
    if not isinstance(block, dict):
        raise ValueError('whatsapp_webhook configuration must be an object')
    reference = block.get('app_secret_env')
    if not isinstance(reference, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', reference):
        raise WebhookError('whatsapp_webhook.app_secret_env must name an environment '
                           'variable, for example META_APP_SECRET')
    try:
        return secret({'app_secret_env': reference}, 'app_secret_env')
    except RuntimeError as error:
        raise WebhookError(f'the Meta app secret is not available in {reference} '
                           f'({error})') from None


def ingest(engine, tenant, body, headers, *, actor='whatsapp', now=None):
    """Verify, unwrap and durably accept one webhook delivery.

    Returns a report rather than raising on a *decision*: Meta redelivers any
    non-2xx response, so a refusal must be a recorded answer. Only a body that
    cannot be trusted at all — a bad signature, a wrong object name — is refused
    outright, because those are not partial deliveries to be retried, they are
    requests from something that is not Meta.
    """
    if not isinstance(headers, dict):
        raise WebhookError('the webhook headers must be an object')
    lowered = {(k.lower() if isinstance(k, str) else k): v for k, v in headers.items()}
    signature = lowered.get(SIGNATURE_HEADER)
    secret = _app_secret(tenant)
    if not verify_signature(body, signature, secret):
        # Named as a security event, and deliberately without echoing the
        # signature: it is attacker-supplied and belongs in no log line.
        engine.audit_write(tenant, 'webhook.signature_rejected', actor,
                           {'channel': CHANNEL, 'reason': 'signature_mismatch'})
        raise Forbidden('WhatsApp webhook signature is missing or invalid')
    try:
        payload = json.loads(bytes(body).decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as error:
        raise WebhookError(f'the webhook body is not valid JSON: {error}') from None
    declared = declared_contacts(tenant)
    report = unwrap(payload, declared, now)
    accepted, duplicates, refused = [], [], []
    for event in report['events']:
        key = event['message_id']
        try:
            result = engine.accept_event(tenant, CHANNEL, key, {
                'sender': event['sender'], 'conversation_id': event['conversation_id'],
                'text': event['text'], 'wa_id': event['wa_id'],
                'profile_name': event['profile_name'],
                'received_at': event['timestamp'],
                'window_until': event['window_until'],
            })
        except Conflict:
            # Same message id, different content: Meta redelivered a key we already
            # hold with a body that does not match what we stored. That is either a
            # bug in Meta's delivery or a replayed request, and guessing which is how
            # a forged message replaces a real one.
            engine.audit_write(tenant, 'webhook.conflict', actor,
                               {'channel': CHANNEL, 'message_id': key})
            refused.append({'message_id': key, 'reason': 'fingerprint_conflict'})
            continue
        if result.get('duplicate'):
            duplicates.append(key)
        else:
            accepted.append(key)
    return {
        'accepted': accepted,
        'duplicates': duplicates,
        'refused': refused,
        'dropped': report['dropped'],
        'declared_contacts': len(declared),
    }


# ------------------------------------------------------------------- tools


def webhook_status(engine, tenant, agent, step, *, limit=20):
    """What arrived through the webhook: accepted, dropped and why.

    Read-only. The inbound path is the input to the window, so an operator asking
    "why does the platform think this customer is inside the window" needs to see the
    messages we did and did not take, not just the ones we did.
    """
    # A None limit is coerced rather than handed to int(), which raises TypeError.
    # The registry validator rejects limit=None, so this is not reachable through the
    # tool path; it is here because this is also a plain function a route may call
    # directly, and int(None) failing with a bare TypeError is a worse answer than
    # the documented default.
    if limit is None:
        limit = 20
    limit = min(max(1, int(limit)), 200)
    with engine.read() as c:
        # p_events columns are (tenant, channel, event_key, fingerprint, payload,
        # status, claim, lease, result, error). There is no 'created': ordering is by
        # rowid, which is the insertion order, and 'lease' is the claim expiry rather
        # than a timestamp. Selecting a column that does not exist is how this
        # function failed the first time it ran.
        rows = [dict(row) for row in c.execute(
            '''SELECT event_key, status, error, lease FROM p_events
               WHERE tenant=? AND channel=? ORDER BY rowid DESC LIMIT ?''',
            (tenant, CHANNEL, limit))]
    return {'channel': CHANNEL, 'events': rows, 'count': len(rows),
            'note': 'Only messages from operator-declared contacts become events; '
                    'status callbacks and undeclared numbers are dropped with a '
                    'reason rather than raising, because Meta retries a non-2xx.'}


def verify_webhook(engine, tenant, agent, step, *, query=None):
    """The GET handshake answer, without touching the network.

    Exposed as a function so the challenge is handled by the same code that is
    tested, and a caller with a route cannot accidentally write its own comparison.
    """
    from .tools import config, secret

    block = config(tenant).get('whatsapp_webhook') or {}
    if not isinstance(block, dict):
        raise ValueError('whatsapp_webhook configuration must be an object')
    reference = block.get('verify_token_env')
    if not isinstance(reference, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', reference):
        raise WebhookError('whatsapp_webhook.verify_token_env must name an '
                           'environment variable')
    try:
        token = secret({'verify_token_env': reference}, 'verify_token_env')
    except RuntimeError as error:
        raise WebhookError(f'the webhook verify token is not available in '
                           f'{reference} ({error})') from None
    return {'challenge': verify_challenge(query or {}, token)}


def _query_payload(args):
    """Decode the challenge query, which travels as JSON text.

    Same reason as the document block: a bare ``{'type': 'object'}`` schema property
    is refused by the registry validator, because with no ``properties`` declared
    every nested dict fails with "Schema fields mismatch". A previous block shipped
    that shape and all five of its tools were unsubmittable; this one does not repeat
    it. ``hub.*`` keys are flat strings, so nothing is lost by the JSON form.
    """
    value = args.get('query')
    if value in (None, ''):
        return {}
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ValueError('query must be a JSON object')
    try:
        parsed = json.loads(value)
    except ValueError:
        raise ValueError('query must be valid JSON') from None
    if not isinstance(parsed, dict):
        raise ValueError('query must be a JSON object')
    return parsed


def _webhook_tool(engine, tenant, agent, args, step):
    return webhook_status(engine, tenant, agent, step, limit=args.get('limit', 20))


def _verify_tool(engine, tenant, agent, args, step):
    return verify_webhook(engine, tenant, agent, step, query=_query_payload(args))


def register_whatsapp_inbound_tools(registry):
    """Two read tools. There is deliberately no write tool here.

    Ingest is not a tool. A model able to call "accept this webhook" could
    manufacture an inbound message, which would open a 24-hour window for a customer
    who never wrote and turn the platform's own legal constraint into something an
    agent can lift. The ingest function is called by the transport that has already
    verified Meta's signature; no agent reaches it.
    """
    from .tools import Tool, obj, string

    definitions = [
        # required=[] because limit is optional and the handler defaults it. The
        # obj() helper makes every declared property required when required is
        # omitted, so leaving it out made a read tool that could not be called with
        # no arguments at all -- the handler's own ``args.get('limit', 20)`` is the
        # evidence that optional was the intent. Every sibling bare read tool
        # (graph.entities, supervisor.sections, asset.levels) declares required=[].
        ('whatsapp.webhook', obj({
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200}},
            required=[]), _webhook_tool),
        # The query is JSON text, not a nested object: see _query_payload.
        ('whatsapp.verify', obj({'query': string(2000)}, required=[]), _verify_tool),
    ]
    for name, schema, handler in definitions:
        if name in registry.items:
            continue
        registry.add(Tool(name, 'read', schema, handler, external=False))
