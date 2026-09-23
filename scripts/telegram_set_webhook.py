"""Register the Telegram webhook WITH the secret the API verifies.

The API rejects every update whose `X-Telegram-Bot-Api-Secret-Token` header does
not match TELEGRAM_WEBHOOK_SECRET (or the tenant's TENANT_SECRETS entry), and
Telegram only sends that header when the webhook was registered with
`secret_token`. Nothing in the repo did that registration, so the first real
update from a real bot answered 401. This script is that missing step.

No secret is ever printed: the bot token and the secret are read from the
environment variables named on the command line and appear only inside the
request. `--dry-run` shows the request with the token masked.

    python scripts/telegram_set_webhook.py --url https://example.uz/webhooks/telegram?tenant=demo-retail \
        --token-env DEMO_TELEGRAM_TOKEN --secret-env TELEGRAM_WEBHOOK_SECRET
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

# The same token shape platform_runtime.tools.telegram accepts, so a token that
# would be refused at send time is refused here first.
TOKEN_RE = re.compile(r'[0-9]+:[A-Za-z0-9_-]+')
# Telegram's own constraint on secret_token: 1-256 characters, A-Z a-z 0-9 _ -.
SECRET_RE = re.compile(r'[A-Za-z0-9_-]{1,256}')
ENV_NAME_RE = re.compile(r'[A-Z][A-Z0-9_]*')
API = 'https://api.telegram.org'
TIMEOUT_SECONDS = 15


class WebhookError(ValueError):
    pass


def build_request(url: str, secret: str, token: str) -> tuple[str, dict]:
    """(setWebhook endpoint, JSON body). Pure, so it is testable without a socket."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise WebhookError('Webhook URL must be a plain https URL')
    if not TOKEN_RE.fullmatch(token):
        raise WebhookError('Bot token has an unexpected shape')
    if not SECRET_RE.fullmatch(secret):
        raise WebhookError('Secret must be 1..256 characters of A-Z a-z 0-9 _ -')
    body = {'url': url, 'secret_token': secret, 'allowed_updates': ['message'],
            'drop_pending_updates': False}
    return f'{API}/bot{token}/setWebhook', body


def masked(endpoint: str) -> str:
    """The endpoint with the token replaced, for logs and dry runs."""
    return re.sub(r'/bot[^/]+/', '/bot***/', endpoint)


def read_env(name: str) -> str:
    if not ENV_NAME_RE.fullmatch(name or ''):
        raise WebhookError('Environment variable names must look like DEMO_TELEGRAM_TOKEN')
    value = os.environ.get(name, '')
    if not value:
        raise WebhookError(f'{name} is not set')
    return value


def call(endpoint: str, body: dict) -> dict:
    request = urllib.request.Request(endpoint, data=json.dumps(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read(65536))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--url', required=True, help='Public https URL of POST /webhooks/telegram')
    parser.add_argument('--token-env', required=True, help='Env var holding the bot token')
    parser.add_argument('--secret-env', default='TELEGRAM_WEBHOOK_SECRET',
                        help='Env var holding the secret the API verifies (default: TELEGRAM_WEBHOOK_SECRET)')
    parser.add_argument('--dry-run', action='store_true', help='Print the masked request, do not call Telegram')
    args = parser.parse_args(argv)
    try:
        endpoint, body = build_request(args.url, read_env(args.secret_env), read_env(args.token_env))
    except WebhookError as exc:
        print('Refused:', exc, file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps({'endpoint': masked(endpoint), 'body': {**body, 'secret_token': '***'}},
                         ensure_ascii=False, indent=2))
        return 0
    result = call(endpoint, body)
    # Telegram echoes only ok/result/description here; none of them carry the token.
    print(json.dumps({'ok': result.get('ok'), 'description': result.get('description', '')},
                     ensure_ascii=False))
    return 0 if result.get('ok') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
