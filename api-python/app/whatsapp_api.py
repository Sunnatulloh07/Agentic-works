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

2. **The tenant is resolved from UNVERIFIED bytes for one purpose only: choosing
   whose app secret verifies the signature** (the same rule
   ``whatsapp_inbound.phone_number_ids`` states one layer down). Nothing else is
   read from the body until the signature has passed.

3. **An unset app secret is a 500, never a bypass.** Unlike the legacy Instagram
   route this endpoint has no dev-open fallback: an unverified delivery can open a
   24-hour window and make every agent holding ``whatsapp.send`` believe the
   customer wrote. There is no safe way to be convenient about that.

4. **A delivery for a number no tenant declares is acknowledged and ignored
   (200).** Meta retries a non-2xx, so a retry storm against a deployment that does
   not serve this number is noise, not a fix. Two tenants declaring the SAME
   number is a configuration error and is refused with 409 rather than guessed at
   -- a guess hands one shop's customers to another.

The handshake token is deployment-level (META_VERIFY_TOKEN): a webhook URL belongs
to one Meta app. ``whatsapp_webhook.verify_token_env`` refines the per-tenant tool
path (``whatsapp_inbound.verify_webhook``), not this route.
"""
import json
import os

from fastapi import APIRouter, HTTPException, Request, Response

from platform_runtime import whatsapp_inbound as wa
from . import platform_api as api

router = APIRouter()


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


def _owners(raw: bytes) -> list[str]:
    """Tenants declaring the business number(s) this delivery addresses.

    Reads unverified bytes, and only for rule 2 in the module docstring: choosing
    the secret that verifies the signature. A body that is not JSON yields no
    owners, so the signature is checked against the default secret and the body is
    refused as malformed afterwards.
    """
    try:
        body = json.loads(raw)
    except ValueError:
        return []
    return sorted({tenant for number in wa.phone_number_ids(body)
                   for tenant in wa.tenants_for_phone_number(number)})


def _secret(owners: list[str]) -> str:
    """The secret that must have signed this delivery; '' when none is configured."""
    if len(owners) == 1:
        return wa.app_secret_for(owners[0])
    return wa.app_secret_for(None)


@router.post('/webhooks/whatsapp')
async def whatsapp_webhook(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > wa.MAX_BODY_BYTES:
        raise HTTPException(413, 'juda katta')
    owners = _owners(raw)
    if len(owners) > 1:
        raise HTTPException(409, 'business number is declared by more than one tenant')
    secret = _secret(owners)
    if not secret:
        raise HTTPException(500, 'META_APP_SECRET sozlanmagan')
    if not wa.verify_signature(raw, request.headers.get(wa.SIGNATURE_HEADER), secret):
        raise HTTPException(401, 'bad signature')
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(422, 'JSON noto\u2018g\u2018ri') from None
    try:
        report = wa.customer_messages(body)
    except wa.WebhookError:
        raise HTTPException(422, 'whatsapp webhook formati xato') from None
    if not owners:
        return {'ok': True, 'ignored': True, 'accepted': [], 'duplicates': [],
                'refused': [], 'dropped': [*report['dropped'],
                                           {'reason': 'unknown_business_number'}]}
    tenant = owners[0]
    # Forbidden (frozen tenant) and RateLimited (full inbox) propagate through the
    # platform's mapper as non-2xx on purpose: Meta must redeliver later.
    result = api.call(wa.accept_customer_messages, api.engine(), tenant, report['events'])
    return {'ok': True, **result, 'dropped': report['dropped']}
