"""Stage 8: kalitsiz ovoz 501 qaytaradi (yolg'on javob yo'q)."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_transcribe_needs_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    r = client.post("/voice/transcribe")
    assert r.status_code == 501
    assert "GROQ_API_KEY" in r.json()["detail"]


def test_speak_needs_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    r = client.post("/voice/speak", json={"text": "salom"})
    assert r.status_code == 501
    assert "ELEVENLABS_API_KEY" in r.json()["detail"]
