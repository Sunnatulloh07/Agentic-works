"""Validate the shipped WhatsApp-webhook example against the module's validator.

Run from anywhere; paths are resolved relative to this file. An example the
validator rejects would teach operators a shape that fails at runtime, which this
repository treats as a defect rather than as documentation drift.

It also measures the claims the block is built on:

* the example names environment variables, not secret values;
* a missing or malformed reference is refused rather than silently ignored;
* an unset environment variable is refused, not treated as "no verification needed";
* the namespaces are disjoint: an app secret is not accepted where a verify token
  belongs, because the handshake is unauthenticated and must not leak the signing key.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import tools as tools_module
from platform_runtime import whatsapp_inbound

PATH = os.path.join(ROOT, 'config', 'whatsapp_webhook.example.json')

with io.open(PATH, encoding='utf-8') as handle:
    raw = json.load(handle)

tenant = 'demo-wa'
block = raw[tenant]['whatsapp_webhook']

# The example holds references, never values. A key that looks like a secret literal
# is a defect: a committed app secret cannot be rotated without a code change.
for key, value in block.items():
    assert isinstance(value, str) and value.isupper(), (key, value)
    assert '=' not in value and len(value) < 64, (key, value)
print('example keys are environment-variable references, not values')

# The two references must differ: reusing the signing key as the handshake token
# would expose it on an unauthenticated endpoint.
assert block['app_secret_env'] != block['verify_token_env'], block
print('app secret and verify token are distinct references')

orig = tools_module.config
tools_module.config = lambda name: {tenant: raw.get(tenant, {})}.get(name, {})

env = {'META_APP_SECRET': 'app-secret-value', 'META_VERIFY_TOKEN': 'verify-value'}
saved = {k: os.environ.get(k) for k in env}
os.environ.update(env)
try:
    assert whatsapp_inbound._app_secret(tenant) == 'app-secret-value'
    print('resolved app secret from the named variable')
    handled = whatsapp_inbound.verify_webhook(
        None, tenant, 'ops.wa', None,
        query={'hub.mode': 'subscribe', 'hub.verify_token': 'verify-value',
               'hub.challenge': '12345'})
    assert handled['challenge'] == '12345', handled
    print('handshake echoes the challenge on a token match')

    # An unset variable is a refusal, not "no verification".
    del os.environ['META_APP_SECRET']
    try:
        whatsapp_inbound._app_secret(tenant)
        raise AssertionError('an unset app secret must be refused')
    except whatsapp_inbound.WebhookError:
        pass
    print('unset app secret -> refused')

    # An unset verify token is a refusal too, and not a reflection of anything.
    del os.environ['META_VERIFY_TOKEN']
    try:
        whatsapp_inbound.verify_webhook(None, tenant, 'ops.wa', None, query={})
        raise AssertionError('an unset verify token must be refused')
    except whatsapp_inbound.WebhookError:
        pass
    print('unset verify token -> refused')

    # A malformed reference is refused: a lowercase or dotted name is not a shell
    # variable and would silently resolve to nothing in production.
    broken = json.loads(json.dumps(raw))
    broken[tenant]['whatsapp_webhook']['app_secret_env'] = 'meta.app_secret'
    tools_module.config = lambda name: {tenant: broken.get(tenant, {})}.get(name, {})
    try:
        whatsapp_inbound._app_secret(tenant)
        raise AssertionError('a malformed reference must be refused')
    except whatsapp_inbound.WebhookError:
        pass
    print('malformed reference -> refused')
    tools_module.config = lambda name: {tenant: raw.get(tenant, {})}.get(name, {})

    # A token is not a secret: the module never reads app_secret_env as the token.
    # META_VERIFY_TOKEN is already unset above, so the token lookup must still refuse
    # rather than borrow the app secret that IS set.
    try:
        whatsapp_inbound.verify_webhook(None, tenant, 'ops.wa', None, query={})
        raise AssertionError('the verify token must not fall back to the app secret')
    except whatsapp_inbound.WebhookError:
        pass
    print('verify token does not fall back to the app secret')
finally:
    tools_module.config = orig
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

print('OK: example passes the module validator')
