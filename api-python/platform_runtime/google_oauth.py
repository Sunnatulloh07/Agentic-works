"""Pinned Google authorization-code transport. No discovery or user URLs.

Google userinfo is used only for provider account binding, not as application
login or an ID-token verifier. Provider traffic is injectable for offline tests.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from .oauth import OAuthError, InvalidGrant, bounded, ident
from .engine import encode
from .tools import NoRedirect

AUTHORIZE = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
REVOKE = 'https://oauth2.googleapis.com/revoke'
USERINFO = 'https://openidconnect.googleapis.com/v1/userinfo'
BASE_SCOPES = {'openid', 'https://www.googleapis.com/auth/userinfo.email'}
API_SCOPES = {
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/drive.metadata.readonly',
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/calendar.events.readonly',
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/spreadsheets',
}

# Every number below is a limit on bytes that a provider, not this platform, decides.
# They were literals at their call sites, which is how a limit stops being reviewable:
# nothing in the suite asserted any of them, so each could be widened silently.
MAX_PROVIDER_RESPONSE_BYTES = 1_000_000
MAX_REQUEST_BODY_BYTES = 64000
MAX_CREDENTIAL_CHARS = 16000
MAX_ERROR_BODY_BYTES = 16000
MAX_CLIENT_ID_CHARS = 512
MAX_REDIRECT_URI_CHARS = 2000
MAX_SUBJECT_CHARS = 256
TRANSPORT_TIMEOUT_SECONDS = 25
CONFIG_SCHEMA_VERSION = 1


def strict_json(raw):
    from .model_response import unique_object, reject_constant
    if len(raw) > MAX_PROVIDER_RESPONSE_BYTES: raise OAuthError('Provider response exceeds limit')
    value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    if not isinstance(value, dict): raise OAuthError('Provider object response required')
    return value


def google_transport(method, url, fields=None, bearer=None):
    if (method, url) not in {('POST', TOKEN), ('POST', REVOKE), ('GET', USERINFO)}:
        raise OAuthError('Unregistered OAuth endpoint')
    headers = {'Accept': 'application/json'}
    if bearer:
        bounded(bearer, MAX_CREDENTIAL_CHARS)
        if not bearer.isascii() or any(c.isspace() for c in bearer): raise OAuthError('Invalid credential')
        headers['Authorization'] = 'Bearer '+bearer
    data = None
    if method == 'POST':
        data = urllib.parse.urlencode(fields or {}).encode('ascii')
        if len(data) > MAX_REQUEST_BODY_BYTES: raise OAuthError('Provider request exceeds limit')
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=TRANSPORT_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
            if response.status != 200: raise OAuthError('Provider rejected request')
            return {} if url == REVOKE and not raw else strict_json(raw)
    except urllib.error.HTTPError as error:
        try:
            body = strict_json(error.read(MAX_ERROR_BODY_BYTES + 1))
        except Exception:
            body = {}
        finally:
            error.close()
        if url == REVOKE and body.get('error') == 'invalid_token': return {}
        if body.get('error') == 'invalid_grant': raise InvalidGrant('Provider grant invalid') from None
        raise OAuthError('Provider request rejected') from None
    except Exception:
        raise OAuthError('Provider transport failed') from None


class GoogleOAuth:
    name = 'google'

    def __init__(self, config, secret_resolver, transport=google_transport):
        allowed = {'enabled', 'client_id', 'client_secret_env', 'redirect_uri', 'expected_subject', 'scopes'}
        if not isinstance(config, dict) or set(config) != allowed or config.get('enabled') is not True:
            raise ValueError('Explicit enabled Google OAuth configuration required')
        self.config = dict(config)
        self.client_id = bounded(config['client_id'], MAX_CLIENT_ID_CHARS)
        if not re.fullmatch(r'[A-Za-z0-9._-]+\.apps\.googleusercontent\.com', self.client_id):
            raise ValueError('Google client ID required')
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{1,127}', config['client_secret_env']):
            raise ValueError('Secret environment reference required')
        self.redirect = bounded(config['redirect_uri'], MAX_REDIRECT_URI_CHARS)
        url = urllib.parse.urlsplit(self.redirect)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment
                or url.port not in {None, 443} or not url.path.startswith('/')):
            raise ValueError('Exact HTTPS callback URI required')
        self.expected_account = bounded(config['expected_subject'], MAX_SUBJECT_CHARS)
        scopes = config['scopes']
        if not isinstance(scopes, list) or not scopes or any(not isinstance(x, str) for x in scopes):
            raise ValueError('Explicit scopes list required')
        self.scopes = frozenset(scopes)
        if not BASE_SCOPES <= self.scopes or not self.scopes <= BASE_SCOPES | API_SCOPES:
            raise ValueError('Unsupported or missing Google scope')
        self.resolve_secret, self.transport = secret_resolver, transport

    def public_config(self):
        return {**self.config, 'scopes': sorted(self.scopes), 'schema_version': CONFIG_SCHEMA_VERSION}

    def authorization_url(self, state, challenge):
        return AUTHORIZE+'?'+urllib.parse.urlencode({
            'client_id': self.client_id, 'redirect_uri': self.redirect,
            'response_type': 'code', 'scope': ' '.join(sorted(self.scopes)),
            'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
            'access_type': 'offline', 'prompt': 'consent select_account',
            'include_granted_scopes': 'false'})

    def _secret(self):
        if self.config['client_secret_env'].startswith('DSEC_'):
            raise OAuthError('Header-only secret cannot be used in OAuth form body')
        value = self.resolve_secret(self.config['client_secret_env'])
        bounded(value, MAX_CREDENTIAL_CHARS)
        # DSEC placeholders are HTTPS-header-only; Google token endpoint needs a
        # client_secret form field. Never transform or send such placeholders.
        if 'DSEC_' in value or value.startswith('«'):
            raise OAuthError('Provider client secret requires deployment secret injection')
        return value

    def exchange(self, code, verifier):
        return self.transport('POST', TOKEN, {
            'client_id': self.client_id, 'client_secret': self._secret(),
            'redirect_uri': self.redirect, 'code': code, 'code_verifier': verifier,
            'grant_type': 'authorization_code'})

    def refresh(self, refresh_token):
        return self.transport('POST', TOKEN, {
            'client_id': self.client_id, 'client_secret': self._secret(),
            'refresh_token': refresh_token, 'grant_type': 'refresh_token'})

    def identity(self, token):
        response = self.transport('GET', USERINFO, bearer=token)
        if not isinstance(response, dict) or response.get('email_verified') is not True:
            raise OAuthError('Verified Google identity required')
        return bounded(response.get('sub'), MAX_SUBJECT_CHARS)

    def revoke(self, token):
        self.transport('POST', REVOKE, {'token': token})


def configured_manager(engine, tenant, connection):
    from .tools import config, secret
    from .secret_vault import SecretVault
    from .oauth import OAuthManager
    ident(connection)
    cfg = config(tenant).get('oauth_connections', {}).get(connection)
    provider = GoogleOAuth(cfg, lambda ref: secret({'value': ref}, 'value'))
    return OAuthManager(engine, SecretVault.from_environment(), provider)
