"""Stage 8 (v2): ovoz SEAM — STT/TTS provayder interfeysi.

Kalit yo'q = 501 + sozlash yo'riqnomasi. Hech qachon soxta javob yo'q.
Bu legacy HTTP endpointlar implement qilinmagan. Yangi Aisha REST adapteri
platform_runtime/speech.py da; streaming va upload pipeline hali ulanmagan. IoT/Modbus/1C — apparat kelganda shu pattern'da.
"""
import os

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from .security import scope_ok

router = APIRouter()
TENANT = "demo-retail"  # ovoz studiyasi tenant'i (operator)


class VoiceError(Exception):
    pass


def _need(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise VoiceError(f"{key} o'rnatilmagan. .env ga qo'shing (docs §11.5).")
    return val


class SpeakRequest(BaseModel):
    text: str
    voice: str = "uz-default"


@router.post("/voice/transcribe")
async def transcribe(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    if not scope_ok(TENANT, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    try:
        _need("GROQ_API_KEY")
    except VoiceError as e:
        raise HTTPException(status_code=501, detail=str(e))
    raise HTTPException(status_code=501, detail="audio pipeline Bosqich 8 da ulanadi")


@router.post("/voice/speak")
async def speak(
    req: SpeakRequest,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    if not scope_ok(TENANT, x_admin_token, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    try:
        _need("ELEVENLABS_API_KEY")
    except VoiceError as e:
        raise HTTPException(status_code=501, detail=str(e))
    raise HTTPException(status_code=501, detail="audio pipeline Bosqich 8 da ulanadi")
