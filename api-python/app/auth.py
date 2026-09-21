"""Tenant JWT (24h) — docs tamoyil 8: miya cloud'da, runner JWT bilan."""
import time

import jwt

from .config import validate_runtime_config

SECRET = validate_runtime_config().jwt_secret
ALGORITHM = "HS256"
TTL_SECONDS = 24 * 3600


ISSUER = "agent-platform"
AUDIENCE = "agent-platform-api"


def issue_token(tenant_id: str, subject: str = "operator", role: str = "operator",
                token_type: str = "user", device_id: str = "", generation: int = 0,
                session_id: str = "", ttl_seconds: int | None = None) -> str:
    now = int(time.time())
    ttl = ttl_seconds if ttl_seconds is not None else (900 if session_id else TTL_SECONDS)
    if type(ttl) is not int or not 1 <= ttl <= TTL_SECONDS: raise ValueError("Invalid token lifetime")
    return jwt.encode({"tenant_id": tenant_id, "sub": subject, "role": role,
                       "token_type": token_type, "device_id": device_id,
                       "generation": generation, "iss": ISSUER, "aud": AUDIENCE,
                       "sid": session_id, "iat": now, "exp": now + ttl}, SECRET, algorithm=ALGORITHM)


def verify_claims(token: str) -> dict:
    p = jwt.decode(token, SECRET, algorithms=[ALGORITHM], issuer=ISSUER,
                   audience=AUDIENCE, options={"require": ["exp", "iat", "sub", "tenant_id", "role", "token_type"]})
    if not p.get("tenant_id") or not p.get("sub"):
        raise jwt.InvalidTokenError("missing identity")
    from .authorization import authorize_claims
    from .identity_store import AuthenticationError
    try:
        return authorize_claims(p)
    except (AuthenticationError, ValueError) as exc:
        raise jwt.InvalidTokenError('Session or membership invalid') from exc


def verify_token(token: str) -> str:
    p = verify_claims(token)
    if p.get("token_type") != "user":
        raise jwt.InvalidTokenError("user token required")
    return p["tenant_id"]
