"""Generate local-only config without printing secrets. Run from repository root.

Writes api-python/.env (random secrets, the three path lines scripts/run_local.py
resolves against the repo root, and EMPTY provider-key lines for you to fill in)
and, only if absent, config/integrations.json: a minimal telegram + llm mapping
for the demo tenant that holds env variable NAMES, never values. An existing
.env is never touched.
"""
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TENANT = 'demo-retail'
# The names config/integrations.example.json uses for the same two blocks.
TELEGRAM_TOKEN_ENV = 'DEMO_TELEGRAM_TOKEN'
LLM_KEY_ENV = 'PLATFORM_LLM_KEY'


def env_body():
    return '\n'.join([
        'ENV=dev', 'PIPELINE_MODE=platform', 'IDENTITY_DIRECTORY=true', 'IDENTITY_BOOTSTRAP_ENABLED=false',
        'JWT_SECRET=' + secrets.token_urlsafe(48), 'ADMIN_TOKEN=' + secrets.token_urlsafe(32),
        'TELEGRAM_WEBHOOK_SECRET=' + secrets.token_urlsafe(24),
        'CORS_ORIGINS=http://localhost:3000', 'META_VERIFY_TOKEN=' + secrets.token_urlsafe(24),
        '# Paths: relative ones are resolved against the repository root by scripts/run_local.py.',
        'APP_DB=api-python/data/app.db', 'PACKS_DIR=packs', 'PLATFORM_INTEGRATIONS_FILE=config/integrations.json',
        '# Provider keys: fill in here; never commit this file.',
        TELEGRAM_TOKEN_ENV + '=', LLM_KEY_ENV + '=', ''])


def integrations_template():
    return {TENANT: {
        # Anthropic Messages API (platform_runtime/model_transport.py). The demo
        # pack's sales agent holds conversations, which need the result-fed loop;
        # low effort suits a chat route. Change model/effort per account.
        'llm': {'provider': 'anthropic', 'model': 'claude-opus-5', 'effort': 'low',
                'key_env': LLM_KEY_ENV, 'agent_loop_enabled': True},
        'telegram': {'token_env': TELEGRAM_TOKEN_ENV},
    }}


def main(root=ROOT):
    root = Path(root)
    config = root / 'config' / 'integrations.json'
    if not config.exists():
        config.write_text(json.dumps(integrations_template(), indent=2) + '\n', encoding='utf-8')
        print(f'Created {config.relative_to(root)} ({TENANT}: telegram + llm, env names only).')
    env = root / 'api-python' / '.env'
    if env.exists():
        print('Existing .env preserved. Edit it manually.')
        return 1
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(env_body())
    print(f'Created local .env. Fill in {TELEGRAM_TOKEN_ENV} and {LLM_KEY_ENV} there. No secrets printed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
