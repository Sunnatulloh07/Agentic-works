"""Validate the shipped escalation example against the module's own bounds.

Run from ``api-python``. This is the check that keeps the example honest: an
example whose knobs the validator would reject teaches operators a shape that
fails at runtime, which is the documentation drift this repository treats as a
defect.

The example is YAML (it mirrors ``reengagement.example.yaml``), so this parses it
without a YAML dependency via a small, explicit reader: the file's shape is fixed
and a permissive parser would hide exactly the mistakes worth catching.
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'api-python'))

from platform_runtime.escalation import (
    LIMITS,
    SCHEDULE_ID_RE,
    SOURCE_TOOLS,
    SOURCES,
    _bounded,
)

PATH = os.path.join(HERE, '..', 'config', 'escalation.example.yaml')


def parse(path):
    """Read the two-level ``escalation: <id>: <key>: <value>`` shape explicitly."""
    with io.open(path, encoding='utf-8') as handle:
        lines = handle.read().splitlines()
    schedules, current, in_block = {}, None, False
    for line in lines:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line.startswith(' '):
            in_block = line.rstrip(':').strip() == 'escalation'
            continue
        if not in_block:
            continue
        body = line.strip()
        indent = len(line) - len(line.lstrip())
        if indent == 2 and body.endswith(':'):
            current = body[:-1].strip()
            schedules[current] = {}
            continue
        if indent == 4 and ':' in body and current:
            key, _, value = body.partition(':')
            value = value.split('#')[0].strip()
            if value.lower() in ('true', 'false'):
                value = value.lower() == 'true'
            elif re.fullmatch(r'-?\d+', value):
                value = int(value)
            elif len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            schedules[current][key] = value
            continue
        raise SystemExit(f'unexpected line in escalation example: {line!r}')
    return schedules


schedules = parse(PATH)
assert schedules, 'the example declares no schedule'

KNOWN = {'agent', 'recipient', 'title', 'cooldown_seconds', 'max_per_cycle',
         'interval_seconds', 'max_age_days', 'enabled', 'source'}

for schedule_id, settings in sorted(schedules.items()):
    assert SCHEDULE_ID_RE.match(schedule_id), f'invalid schedule id {schedule_id!r}'
    unknown = set(settings) - KNOWN
    assert not unknown, f'{schedule_id} has unsupported keys: {sorted(unknown)}'
    for required in ('agent', 'recipient'):
        assert settings.get(required), f'{schedule_id} requires {required}'
    assert isinstance(settings.get('enabled', True), bool), f'{schedule_id}.enabled must be bool'
    # The source must be one the module knows AND one with a declared reader, so
    # the example cannot teach a source the coordinator would refuse at configure
    # time. workforce is the default, so an absent key is valid.
    source = settings.get('source', 'workforce')
    assert source in SOURCES, f'{schedule_id} has unknown source {source!r}'
    assert source in SOURCE_TOOLS, f'{schedule_id} source {source!r} has no reader'
    for name in LIMITS:
        if name in settings:
            # The module's own gate, so the example cannot drift from the code.
            _bounded(settings[name], name)
    print(f'{schedule_id}: source={source} agent={settings["agent"]!r} '
          f'recipient={settings["recipient"]!r} '
          f'cooldown={settings.get("cooldown_seconds")} max_per_cycle={settings.get("max_per_cycle")} '
          f'interval={settings.get("interval_seconds")} max_age_days={settings.get("max_age_days")}')

# The example must exercise both sources, or the documentation would only ever
# teach the older one and the telephony path would live in prose alone.
declared_sources = {s.get('source', 'workforce') for s in schedules.values()}
assert 'workforce' in declared_sources, 'the example teaches no workforce schedule'
assert 'telephony' in declared_sources, 'the example teaches no telephony schedule'
print(f'sources covered -> {sorted(declared_sources)}')
print('telephony still delivers via telegram.send ->',
      SOURCE_TOOLS['telephony'] != 'telegram.send')

# A knob outside its bound must be refused by the same gate the example passes.
for bad in (0, LIMITS['max_per_cycle'][1] + 1):
    refused = False
    try:
        _bounded(bad, 'max_per_cycle')
    except ValueError:
        refused = True
    assert refused, f'the bound gate must reject {bad}'
print('out-of-range knobs -> refused')
print('OK: example passes the module validator')
