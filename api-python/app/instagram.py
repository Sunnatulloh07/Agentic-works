"""Instagram adapter (Stage 4): Meta verify + DM. Mantiq pipeline'da."""
import hashlib
import hmac
import os

from fastapi import APIRouter, HTTPException, Request, Response, status

from .pipeline import handle_text_message

router = APIRouter()


def _verify_token() -> str:
    expected = os.getenv("META_VERIFY_TOKEN")
    if not expected:
        from .security import dev_open

        if not dev_open():
            raise HTTPException(status_code=500, detail="META_VERIFY_TOKEN sozlanmagan")
        expected = "dev-meta-verify"
    return expected


@router.get("/webhooks/instagram")
async def instagram_verify(request: Request) -> Response:
    import hmac as _hmac

    p = request.query_params
    if (p.get("hub.mode") == "subscribe"
            and _hmac.compare_digest(p.get("hub.verify_token", ""), _verify_token())):
        return Response(content=p.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify failed")


def _secret_for(tenant: str) -> str | None:
    for pair in os.getenv("META_SECRETS", "").split(","):
        if ":" in pair:
            t, s = pair.split(":", 1)
            if t.strip() == tenant and s.strip():
                return s.strip()
    return os.getenv("META_APP_SECRET")


def _signature_ok(raw: bytes, header: str | None, tenant: str) -> bool:
    secret = _secret_for(tenant)
    if not secret:
        from .security import dev_open

        return dev_open()  # dev-ochiq, prod fail-closed
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + digest, header)


@router.post("/webhooks/instagram")
async def instagram_webhook(request: Request, tenant: str | None = None) -> dict:
    raw = await request.body()
    if len(raw) > 1_000_000:
        raise HTTPException(status_code=413, detail="juda katta")
    from .security import is_prod
    if is_prod():
        import json
        from platform_runtime.tools import tenant_for_instagram_account
        try:
            parsed = json.loads(raw)
            accounts = {str(e["id"]) for e in parsed["entry"]}
            if len(accounts) != 1: raise ValueError()
            resolved = tenant_for_instagram_account(next(iter(accounts)))
            if tenant is not None and tenant != resolved: raise ValueError()
            tenant = resolved
        except (ValueError, KeyError, TypeError, RuntimeError):
            raise HTTPException(403, "Meta account routing rejected")
    else:
        tenant = tenant or "demo-retail"
    if not _signature_ok(raw, request.headers.get("X-Hub-Signature-256"), tenant):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad signature")
    try:
        import json
        body = json.loads(raw)
        entries = body.get("entry", [])
        if not isinstance(entries, list): raise ValueError()
        results = []
        from platform_runtime.tools import config
        from .security import is_prod
        # Signature authenticates the Meta app, not the tenant/account mapping.
        expected = config(tenant).get("instagram", {}).get("account_id", "") if is_prod() else ""
        for entry in entries:
            if expected and str(entry.get("id", "")) != str(expected):
                raise HTTPException(403, "Meta account mismatch")
            for msg in entry.get("messaging", []):
                message = msg.get("message", {})
                if message.get("is_echo") or "mid" not in message:
                    continue
                sender = str(msg["sender"]["id"])
                results.append(handle_text_message(tenant, "instagram", str(message["mid"]),
                                                  sender, str(message.get("text", "")),
                                                  conversation_id=sender))
        return {"ok": True, "events": results}
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="instagram formati xato")
