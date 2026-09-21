"""Aisha synchronous REST adapters based on official API docs, checked 2026-09-13.

HTTP contract is mock-tested only. No streaming, telephony or audio download claim.
Provider credentials stay in X-Api-Key to fixed HTTPS endpoints; no redirects/retries.
"""
import json
import re
import urllib.request
import uuid
from urllib.parse import urlsplit, unquote
from .tools import NoRedirect

ORIGIN = 'https://back.aisha.group'
MAX_AUDIO = 1_000_000  # Local product limit, NOT a published Aisha v1 limit.


class SpeechError(RuntimeError):
    pass


def multipart(fields, audio=None):
    boundary = 'agentplatform' + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    if audio:
        filename, media_type, data = audio
        chunks.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="{filename}"\r\nContent-Type: {media_type}\r\n\r\n'.encode(), data, b'\r\n'])
    chunks.append(f'--{boundary}--\r\n'.encode())
    return b''.join(chunks), 'multipart/form-data; boundary=' + boundary


def http(url, data, headers):
    request = urllib.request.Request(url, data=data, headers=headers, method='POST')
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
        raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise SpeechError('Speech response too large')
        return response.status, json.loads(raw)


class AishaREST:
    def __init__(self, api_key, transport=http):
        if not isinstance(api_key, str) or not api_key:
            raise ValueError('Aisha credential required')
        self._key = api_key
        self._transport = transport

    def _post(self, path, fields, expected, audio=None):
        data, content_type = multipart(fields, audio)
        try:
            status, body = self._transport(ORIGIN + path, data, {'X-Api-Key': self._key, 'Content-Type': content_type})
            if status != expected or not isinstance(body, dict):
                raise SpeechError('Unexpected speech response')
            return body
        except Exception:
            # Do not retry a possibly billed request or leak URLs, audio, text, keys.
            raise SpeechError('Speech request failed; billing outcome may be unknown') from None

    def synthesize(self, text, mood='Neutral', speed=1.0):
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise ValueError('TTS requires 1..1000 characters')
        if mood not in {'Neutral', 'Cheerful', 'Happy', 'Sad'}:
            raise ValueError('Invalid mood')
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.5 <= speed <= 2.0:
            raise ValueError('Invalid speech speed')
        body = self._post('/api/v1/tts/post/', {'transcript': text, 'language': 'uz',
                         'model': 'Gulnoza', 'mood': mood, 'speed': str(speed)}, 201)
        path = body.get('audio_path', '')
        if not isinstance(path, str) or len(path)>1000:
            raise SpeechError('Invalid audio path')
        parts = urlsplit(path)
        decoded = unquote(path)
        if (parts.scheme or parts.netloc or parts.query or parts.fragment
                or not path.startswith('/media/tts_audios/') or '\\' in decoded
                or any(part in {'.', '..'} for part in decoded.split('/'))
                or not re.fullmatch(r'/media/tts_audios/[A-Za-z0-9_.-]+\.wav',path)):
            raise SpeechError('Unexpected media path')
        # A relative path is NOT proof of authenticated media download availability.
        return {'provider': 'aisha', 'audio_path': path, 'characters': len(text), 'download_verified': False}

    def transcribe(self, data, format='wav', diarization=False):
        formats = {'wav':'audio/wav', 'mp3':'audio/mpeg', 'ogg':'audio/ogg', 'm4a':'audio/mp4'}
        if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_AUDIO or format not in formats:
            raise ValueError('Invalid audio or local 1 MB limit exceeded')
        if not isinstance(diarization, bool):
            raise ValueError('Invalid diarization option')
        body = self._post('/api/v1/stt/post/', {'language':'uz', 'has_diarization':str(diarization).lower(),
                          'has_offset':'false', 'is_summary':'false'}, 200,
                          ('audio.' + format, formats[format], data))
        text = body.get('transcript')
        if not isinstance(text, str) or len(text)>80_000:
            raise SpeechError('Invalid transcript')
        return {'provider':'aisha', 'transcript':text}


def tool_tts(engine, tenant, agent, args, step):
    from .tools import config, secret
    cfg = config(tenant).get('speech', {}).get('aisha', {})
    return AishaREST(secret(cfg, 'key_env')).synthesize(args['text'], args.get('mood','Neutral'))
