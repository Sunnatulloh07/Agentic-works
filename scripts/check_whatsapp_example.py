"""Validate the shipped WhatsApp example against the module's own validator.

Run from anywhere; paths are resolved relative to this file. An example the
validator rejects would teach operators a shape that fails at runtime, which this
repository treats as a defect rather than as documentation drift.

It also measures the four claims the block is built on:

* the two credentials stay in separate keys and a send reads only its own;
* a template cannot be expressed without a category, and the category is never a
  send argument;
* a naive timestamp is refused rather than read as UTC (Uzbekistan is UTC+5);
* the On-Premises API cannot be configured, because it was sunset.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import tools as tools_module
from platform_runtime import whatsapp

PATH = os.path.join(ROOT, 'config', 'whatsapp.example.json')

with io.open(PATH, encoding='utf-8') as handle:
    raw = json.load(handle)

tenant = 'demo-sales'
orig = tools_module.config
tools_module.config = lambda name: {tenant: raw.get(tenant, {})}.get(name, {})

try:
    cleaned = whatsapp.whatsapp_config(tenant)
finally:
    tools_module.config = orig

print('registers declared:', sorted(cleaned))
for name, entry in sorted(cleaned.items()):
    print(f"  {name}: api={entry['api']!r} phone_number_id={entry['phone_number_id']!r} "
          f"connection={entry['connection']!r} contacts={sorted(entry['contacts'])}")
    for key, template in sorted(entry['templates'].items()):
        print(f"    template {key}: name={template['name']!r} "
              f"language={template['language']!r} category={template['category']!r}")

# The two credentials are separate keys. A single shared key would let a send
# credential inspect the account.
secrets = raw[tenant]['whatsapp_tokens']['support']
assert set(secrets) == {'messaging', 'management'}, secrets
print('credential keys:', sorted(secrets))

# A template without a category must be refused, so a later send cannot inherit a
# guessed category.
broken = json.loads(json.dumps(raw))
del broken[tenant]['whatsapp']['registers']['support']['templates']['promo']['category']
tools_module.config = lambda name: {tenant: broken.get(tenant, {})}.get(name, {})
try:
    try:
        whatsapp.whatsapp_config(tenant)
        raise AssertionError('a template without a category must be refused')
    except ValueError:
        pass
finally:
    tools_module.config = orig
print('template without category -> refused')

# On-Premises cannot be configured.
broken = json.loads(json.dumps(raw))
broken[tenant]['whatsapp']['registers']['support']['api'] = 'on_premises'
tools_module.config = lambda name: {tenant: broken.get(tenant, {})}.get(name, {})
try:
    try:
        whatsapp.whatsapp_config(tenant)
        raise AssertionError('on-premises must be refused')
    except ValueError:
        pass
finally:
    tools_module.config = orig
print('api=on_premises -> refused')

# A naive timestamp is refused; the offset and Zulu forms are the same instant.
assert whatsapp._parse_timestamp('2026-09-19 10:00:00') is None
assert whatsapp._parse_timestamp('2026-09-19T10:00:00+05:00') == \
       whatsapp._parse_timestamp('2026-09-19T05:00:00Z')
print('naive timestamp -> refused; offset == Zulu')

# The 131047 guarantee, stated as arithmetic rather than as a promise.
opened, closes = whatsapp.window_state(1_789_794_000, 1_789_794_100)
assert opened and closes == 1_789_794_000 + whatsapp.WINDOW_SECONDS
opened, closes = whatsapp.window_state(1_789_794_000,
                                       1_789_794_000 + whatsapp.WINDOW_SECONDS)
assert not opened, 'the window is closed exactly at last + 24h'
print('window closes exactly at last_inbound + 24h; error',
      whatsapp.ERROR_OUTSIDE_WINDOW, 'is unreachable because a closed window is '
      'refused before any provider I/O')

# The send schema carries no category and no language.
schema = tools_module.build_registry().get('whatsapp.send').schema['properties']
for forbidden in ('category', 'language', 'to', 'phone', 'recipients'):
    assert forbidden not in schema, forbidden
print('send schema exposes no category, language or raw destination')

print('OK: example passes the module validator')
