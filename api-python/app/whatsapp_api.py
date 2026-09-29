"""WhatsApp Cloud API webhook: Meta's handshake, then a verified inbound delivery.

GET  /webhooks/whatsapp  -- Meta's one-time subscribe handshake (META_VERIFY_TOKEN)
POST /webhooks/whatsapp  -- X-Hub-Signature-256 over the RAW body (META_APP_SECRET);
                            the business phone_number_id names the tenant

This module is authentication and routing only. Parsing, the 24-hour window and the
reply rules are ``platform_runtime/whatsapp_inbound.py``; the durable inbox is the
engine's, keyed by Meta's message id, so no second dedup scheme exists here.

Four ordering rules, each deliberate:

1. **The body is read raw and the signature is checked over those exact bytes,
   before anything is decided.** ``verify_signature`` takes ``bytes`` for that
   reason: Meta signs the bytes it sent, and a parsed dict is a different byte
   string.

2. **Tenants are resolved from UNVERIFIED bytes for one purpose only: choosing
   whose app secret verifies the signature** (the same rule
   ``whatsapp_inbound.phone_number_ids`` states one layer down). Nothing else is
   read from the body until the signature has passed. After it has, EACH message
   goes to the tenant owning ITS business number: one Meta app may batch several
   shops' numbers in one POST, and one tenant must not receive another's customers.
   A tenant whose configuration cannot be read makes routing a 503 (logged by
   tenant and exception type), never a guess: read as empty, its numbers looked
   unknown and their messages were acknowledged and lost.

3. **An unset app secret is a 500, never a bypass**, for any delivery that names
   a served number. Unlike the legacy Instagram route this endpoint has no dev-open
   fallback: an unverified delivery can open a 24-hour window and make every agent
   holding ``whatsapp.send`` believe the customer wrote.

4. **A message for a number no tenant declares is dropped with a reason, and a
   delivery with nothing else is acknowledged (200).** Meta retries a non-2xx, so a
   retry storm against a deployment that does not serve this number is noise, not
   a fix -- which is also why such a delivery is acknowledged when no deployment
   secret is set at all: there is nothing to verify it against and nothing to do.
   One number declared by two tenants is a configuration error and is refused with
   409 rather than guessed at -- a guess hands one shop's customers to another.

What an UNSIGNED caller can make the route spend is bounded before rule 2 runs:
the body is refused at ``MAX_BODY_BYTES`` from its declared length, or while it is
streamed in (never buffered whole first); a body naming more than
``MAX_ROUTED_NUMBERS`` business numbers is refused before any tenant is read; and
the tenants are read once per delivery, off the event loop, since configuration
files, the HMAC and SQLite are all blocking work.

The handshake token is deployment-level (META_VERIFY_TOKEN): a webhook URL belongs
to one Meta app. ``whatsapp_webhook.verify_token_env`` refines the per-tenant tool
path (``whatsapp_inbound.verify_webhook``), not this route.
"""
import json
import logging
import os

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from platform_runtime import whatsapp_inbound as wa
from . import platform_api as api

router = APIRouter()
log = logging.getLogger(__name__)
UNKNOWN_NUMBER = 'unknown_business_number'
# Signed, but not by the app secret of the tenant owning this message's number.
UNVERIFIED_NUMBER = 'unverified_business_number'


def _verify_token() -> str:
    """The deployment handshake token; unset is a refusal, not a default."""
    token = os.getenv('META_VERIFY_TOKEN', '')
    if not token:
        raise HTTPException(500, 'META_VERIFY_TOKEN sozlanmagan')
    return token


@router.get('/webhooks/whatsapp')
def whatsapp_verify(request: Request) -> Response:
    """Echo Meta's challenge when mode and token match; 403 otherwise.

    The comparison lives in ``whatsapp_inbound.verify_challenge`` (constant-time,
    and a mismatch reveals nothing about which of the two inputs was wrong), so a
    route cannot accidentally write its own comparison.
    """
    challenge = wa.verify_challenge(dict(request.query_params), _verify_token())
    if challenge is None:
        raise HTTPException(403, 'verify failed')
    return Response(content=challenge, media_type='text/plain')


def _too_large() -> HTTPException:
    return HTTPException(413, 'juda katta')


async def _read_body(request) -> bytes:
    """The raw body, refused past ``MAX_BODY_BYTES`` without buffering it whole first.

    A declared Content-Length over the cap is refused before a byte is read; a
    streamed (chunked) body is refused as soon as the running total passes the
    cap, so at most one transport chunk beyond it is ever held.
    """
    declared = request.headers.get('content-length')
    if declared is not None:
        try:
            size = int(declared)
        except ValueError:
            raise HTTPException(400, 'Content-Length noto‘g‘ri') from None
        if size > wa.MAX_BODY_BYTES:
            raise _too_large()
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > wa.MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b''.join(chunks)


@router.post('/webhooks/whatsapp')
async def whatsapp_webhook(request: Request) -> dict:
    raw = await _read_body(request)
    # Configuration files, the HMAC and SQLite block: none of it runs on the loop.
    return await run_in_threadpool(_deliver, raw, request.headers.get(wa.SIGNATURE_HEADER))


def _routes(raw: bytes) -> dict[str, str]:
    """``{business number: its one tenant}`` for the numbers this delivery names.

    Reads unverified bytes, and only for rule 2 in the module docstring: choosing
    the secrets that verify the signature. A body that is not JSON names no
    number. More than MAX_ROUTED_NUMBERS numbers is 422, before any tenant is read;
    one number declared by two tenants is 409; an unreadable tenant configuration
    is 503 (Meta retries), logged by source and exception type only. A number no
    tenant declares is simply absent.
    """
    try:
        body = json.loads(raw)
    except ValueError:
        return {}
    numbers = wa.phone_number_ids(body)
    if len(numbers) > wa.MAX_ROUTED_NUMBERS:
        raise HTTPException(422, 'one delivery names too many business numbers')
    try:
        owners = wa.number_owners(numbers)
    except wa.RoutingUnavailable as error:
        log.error('whatsapp webhook: routing refused, configuration of %s is unreadable (%s)',
                  error.source, error.error)
        raise HTTPException(503, 'whatsapp routing configuration unavailable') from None
    if any(len(tenants) > 1 for tenants in owners.values()):
        raise HTTPException(409, 'business number is declared by more than one tenant')
    return {number: tenants[0] for number, tenants in owners.items() if tenants}


def _verified(raw: bytes, signature: str | None, tenants: list[str]) -> set[str]:
    """The owning tenants whose Meta app secret signed exactly these bytes."""
    secrets = {tenant: wa.app_secret_for(tenant) for tenant in tenants}
    if not all(secrets.values()):
        raise HTTPException(500, 'META_APP_SECRET sozlanmagan')
    return {tenant for tenant, secret in secrets.items()
            if wa.verify_signature(raw, signature, secret)}


def _report(raw: bytes) -> dict:
    """The customer messages of a VERIFIED body, or 422."""
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(422, 'JSON noto\u2018g\u2018ri') from None
    try:
        return wa.customer_messages(body)
    except wa.WebhookError:
        raise HTTPException(422, 'whatsapp webhook formati xato') from None


def _ignored(raw: bytes, signature: str | None) -> dict:
    """Rule 4 for a delivery that names no served number: acknowledged, never retried.

    With the deployment secret set it is still verified, so a forged body is 401
    and a malformed signed one 422. With none set there is nothing to verify it
    against and nothing to do: a 500 here only made Meta retry forever.
    """
    secret = wa.app_secret_for(None)
    dropped = [{'reason': UNKNOWN_NUMBER}]
    if secret:
        if not wa.verify_signature(raw, signature, secret):
            raise HTTPException(401, 'bad signature')
        dropped = [*_report(raw)['dropped'], *dropped]
    return {'ok': True, 'ignored': True, 'accepted': [], 'duplicates': [], 'refused': [],
            'dropped': dropped}


def _deliver(raw: bytes, signature: str | None) -> dict:
    """Route, verify and accept one bounded delivery (the four rules, in order)."""
    routes = _routes(raw)
    tenants = sorted(set(routes.values()))
    if not tenants:
        return _ignored(raw, signature)
    verified = _verified(raw, signature, tenants)
    if not verified:
        raise HTTPException(401, 'bad signature')
    report = _report(raw)
    batches, dropped = {}, list(report['dropped'])
    for event in report['events']:
        tenant = routes.get(event['phone_number_id'])
        if tenant is None or tenant not in verified:
            dropped.append({'reason': UNKNOWN_NUMBER if tenant is None else UNVERIFIED_NUMBER})
        else:
            batches.setdefault(tenant, []).append(event)
    unrouted = sum(1 for item in dropped if item['reason'] in (UNKNOWN_NUMBER, UNVERIFIED_NUMBER))
    if unrouted:
        log.warning('whatsapp webhook: %d message(s) for a business number no verified tenant '
                    'serves were dropped', unrouted)
    result = {'accepted': [], 'duplicates': [], 'refused': []}
    for tenant in sorted(batches):
        # Forbidden (frozen tenant) and RateLimited (full inbox) propagate through the
        # platform's mapper as non-2xx on purpose: Meta must redeliver later, and the
        # tenants already accepted see their messages again as duplicates.
        part = api.call(wa.accept_customer_messages, api.engine(), tenant, batches[tenant])
        for name in result:
            result[name].extend(part[name])
    return {'ok': True, **result, 'dropped': dropped}
