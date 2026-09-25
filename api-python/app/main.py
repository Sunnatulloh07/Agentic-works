"""Stage 0 API: health + tenant JWT auth. YAGNI — boshqa hech narsa yo'q."""
import os

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .approvals import router as approvals_router
from .auth import issue_token, verify_token
from .hotlead import leads_router
from .instagram import router as instagram_router
from .ladder import ladder_router
from .operator import router as operator_router
from .packs import NAME_RE
from .security import admin_ok
from .stats import router as stats_router
from .telegram import router as telegram_router
from .voice import router as voice_router
from .identity_api import router as identity_router

from .version import VERSION
bearer = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Declared bounds and surfaces. Every name here is pinned literally in
# runtime_tests/test_main_bounds.py, and the middleware decisions that read them
# are pinned behaviourally there too. Inline literals were not addressable, so
# widening the CORS surface, the tenant charset or the legacy exemption list used
# to be silent.
# ---------------------------------------------------------------------------
DEFAULT_CORS_ORIGIN = 'http://localhost:3000'
CORS_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
CORS_HEADERS = ['Authorization', 'Content-Type', 'X-Admin-Token', 'Idempotency-Key']
MAX_TENANT_CHARS = 64
ROLES = frozenset({'owner', 'operator', 'integrator', 'viewer'})
MUTATION_ROLES = frozenset({'owner', 'operator'})
OWNER_ONLY_PREFIX = '/ladder/'
LEGACY_EXEMPT_PREFIXES = ('/webhooks/', '/platform/', '/auth/', '/identity/')
NO_STORE_PREFIXES = ('/identity/', '/platform/')
LEGACY_RUNNER_PREFIX = '/runner/'

app = FastAPI(title="Agent Platform API", version=VERSION)
app.include_router(telegram_router)
app.include_router(instagram_router)
app.include_router(approvals_router)
app.include_router(ladder_router)
app.include_router(operator_router)
app.include_router(stats_router)
app.include_router(leads_router)
# Legacy runner transport is retired; platform runner enforces device identity.
app.include_router(voice_router)
app.include_router(identity_router)


from .platform_api import router as platform_router
app.include_router(platform_router)
from .google_data_api import router as google_data_router
from .oauth_api import router as oauth_router
app.include_router(oauth_router)
app.include_router(google_data_router)
from .shop_api import router as shop_router
app.include_router(shop_router)
from .conversation_api import router as conversation_router
app.include_router(conversation_router)
from .erp_api import router as erp_router
app.include_router(erp_router)
from .whatsapp_api import router as whatsapp_router
app.include_router(whatsapp_router)

from fastapi.middleware.cors import CORSMiddleware
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGIN).split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=CORS_METHODS,
                   allow_headers=CORS_HEADERS)

class TokenRequest(BaseModel):
    tenant_id: str
    subject: str = Field(default="operator", min_length=1, max_length=128)
    role: str = "operator"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION}


@app.post("/auth/token")
def create_token(
    req: TokenRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    from .authorization import directory_enabled
    if directory_enabled():
        raise HTTPException(410, "Use session-bound identity login")
    required = os.getenv("ADMIN_TOKEN")
    from .security import dev_open

    if not dev_open() and not required:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auth sozlanmagan")
    if not admin_ok(x_admin_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    tenant_id = req.tenant_id.strip()
    if not tenant_id or not NAME_RE.match(tenant_id) or len(tenant_id) > MAX_TENANT_CHARS:
        raise HTTPException(status_code=400, detail="tenant_id noto'g'ri")
    if req.role not in ROLES:
        raise HTTPException(status_code=422, detail="role noto‘g‘ri")
    return {"access_token": issue_token(tenant_id, subject=req.subject, role=req.role), "token_type": "bearer"}


def current_tenant(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing token")
    try:
        return verify_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


@app.get("/me")
def me(tenant_id: str = Depends(current_tenant)) -> dict:
    return {"tenant_id": tenant_id}


@app.middleware("http")
async def legacy_authorization(request, call_next):
    from fastapi.responses import JSONResponse
    from .auth import verify_claims
    path = request.url.path
    if path.startswith(LEGACY_RUNNER_PREFIX):
        return JSONResponse({"detail": "Legacy runner disabled; use /platform/runner/ws and platform tasks"}, status_code=410)
    if (os.getenv("PIPELINE_MODE", "platform") != "legacy"
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and not path.startswith(LEGACY_EXEMPT_PREFIXES)):
        return JSONResponse({"detail": "Legacy mutation retired; use platform API"}, status_code=410)
    auth = request.headers.get("Authorization", "")
    if auth and not path.startswith(LEGACY_EXEMPT_PREFIXES):
        try:
            claims = verify_claims(auth.removeprefix("Bearer "))
            if claims.get("token_type") != "user":
                raise ValueError()
            role = claims.get("role")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                required = {"owner"} if path.startswith(OWNER_ONLY_PREFIX) else MUTATION_ROLES
                if role not in required:
                    return JSONResponse({"detail": "Role not permitted"}, status_code=403)
        except Exception:
            return JSONResponse({"detail": "Invalid token"}, status_code=401)
    response = await call_next(request)
    if path.startswith(NO_STORE_PREFIXES):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.exception_handler(RequestValidationError)
async def sanitized_validation_error(request, exc):
    # Pydantic errors can include raw input, including passwords. Never echo input.
    from fastapi.responses import JSONResponse
    return JSONResponse({'detail':[{'loc':list(e['loc']),'type':e['type'],'msg':e['msg']} for e in exc.errors()]},
                        status_code=422, headers={'Cache-Control':'no-store'})
