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

VERSION = "0.4.0-development-preview"
bearer = HTTPBearer(auto_error=False)

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

from fastapi.middleware.cors import CORSMiddleware
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type", "X-Admin-Token", "Idempotency-Key"])

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
    if not tenant_id or not NAME_RE.match(tenant_id) or len(tenant_id) > 64:
        raise HTTPException(status_code=400, detail="tenant_id noto'g'ri")
    if req.role not in {"owner", "operator", "integrator", "viewer"}:
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
    if path.startswith("/runner/"):
        return JSONResponse({"detail": "Legacy runner disabled; use /platform/runner/ws and platform tasks"}, status_code=410)
    if (os.getenv("PIPELINE_MODE", "platform") != "legacy"
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and not path.startswith(("/webhooks/", "/platform/", "/auth/", "/identity/"))):
        return JSONResponse({"detail": "Legacy mutation retired; use platform API"}, status_code=410)
    auth = request.headers.get("Authorization", "")
    if auth and not path.startswith(("/webhooks/", "/platform/", "/auth/", "/identity/")):
        try:
            claims = verify_claims(auth.removeprefix("Bearer "))
            if claims.get("token_type") != "user":
                raise ValueError()
            role = claims.get("role")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                required = {"owner"} if path.startswith("/ladder/") else {"owner", "operator"}
                if role not in required:
                    return JSONResponse({"detail": "Role not permitted"}, status_code=403)
        except Exception:
            return JSONResponse({"detail": "Invalid token"}, status_code=401)
    response = await call_next(request)
    if path.startswith(('/identity/', '/platform/')):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.exception_handler(RequestValidationError)
async def sanitized_validation_error(request, exc):
    # Pydantic errors can include raw input, including passwords. Never echo input.
    from fastapi.responses import JSONResponse
    return JSONResponse({'detail':[{'loc':list(e['loc']),'type':e['type'],'msg':e['msg']} for e in exc.errors()]},
                        status_code=422, headers={'Cache-Control':'no-store'})
