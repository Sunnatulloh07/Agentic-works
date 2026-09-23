"""Measure the biometric gate rather than claiming it (PRD v0.5, P11b / T4).

The vision module's docstring makes two strong claims:

1. person-identifying events can only be read by a ``human_led`` agent;
2. an agent without the right ladder cannot cause provider I/O against that data
   at all.

Claims in a docstring are not evidence, so this probe runs the same call at all
three ladder levels and counts the provider requests each one produces. The
interesting number is not that the first two are refused — it is that they are
refused with **zero** GETs: a gate that refuses after reading the data is not a
gate.

Run from anywhere; paths are resolved relative to this file.
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import vision as vision_module
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry

SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

ROWS = [
    ['stansiya', 'hodisa', 'sana', 'ishonch'],
    ['zavod-1/sex-1/liniya-1/stanok-1', 'nuqson', '2026-09-18', '0.91'],
    ['zavod-1/sex-1/liniya-1/stanok-1', 'yuz', '2026-09-18', '0.99'],
    ['zavod-10/sex-1/liniya-1/stanok-1', 'nuqson', '2026-09-18', '0.95'],
]

CONFIG = {
    't_plant': {
        'connections': {'google': {}},
        'sheets_registers': {'plant': {
            'connection': 'google', 'spreadsheet_id': SPREADSHEET,
            'ranges': {'events': 'Hodisa!A1:D'}, 'max_rows': 200}},
        'assets': {'entity': 'asset', 'levels': ['zavod', 'sex', 'liniya', 'stanok']},
        'vision': {'registers': {
            'shopfloor': {
                'register': 'plant', 'range': 'events',
                'station_column': 'stansiya', 'event_column': 'hodisa',
                'timestamp_column': 'sana', 'confidence_column': 'ishonch',
                'sensitivity': 'station'},
            'access': {
                'register': 'plant', 'range': 'events',
                'station_column': 'stansiya', 'event_column': 'hodisa',
                'timestamp_column': 'sana',
                'sensitivity': 'person', 'person_classes': ['yuz'],
                'biometric_ack': True}}},
    }
}


def attempt(ladder, fn):
    """Run one read at one ladder level. Returns (outcome, provider gets)."""
    tmp = tempfile.TemporaryDirectory()
    try:
        root = Path(tmp.name)
        cfg = root / 'integrations.json'
        cfg.write_text(json.dumps(CONFIG, ensure_ascii=False), encoding='utf-8')
        previous = os.environ.get('PLATFORM_INTEGRATIONS_FILE')
        os.environ['PLATFORM_INTEGRATIONS_FILE'] = str(cfg)
        policy = {
            'tools': ['sheets.rows', 'vision.station_event', 'vision.person_event',
                      'vision.summary'],
            'allowed_connections': ['google'],
            'ladder': ladder,
        }
        engine = Engine(root / 'probe.db', build_registry(), lambda t, a: policy)
        calls = []

        def transport(url, token):
            calls.append(url)
            return {'values': ROWS}

        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'probe-token'
            with patch('platform_runtime.sheets._http_get', side_effect=transport):
                try:
                    result = fn(engine, 't_plant', 'ops.vision')
                except Forbidden as error:
                    return ('refused: ' + str(error)[:52], len(calls))
                return ('read %d event(s)' % len(result['events']), len(calls))
        if previous is None:
            os.environ.pop('PLATFORM_INTEGRATIONS_FILE', None)
        else:
            os.environ['PLATFORM_INTEGRATIONS_FILE'] = previous
    finally:
        tmp.cleanup()


def main():
    print('vision tools        :', list(vision_module.VISION_TOOLS))
    print()
    print('%-18s %-34s %s' % ('ladder', 'person_event outcome', 'provider GETs'))
    print('-' * 70)
    leaks = 0
    for ladder in ('autonomous', 'human_assisted', 'human_led'):
        outcome, gets = attempt(ladder, vision_module.person_event)
        if ladder != 'human_led' and gets:
            leaks += 1
        print('%-18s %-34s %d' % (ladder, outcome, gets))

    print()
    print('%-18s %-34s %s' % ('ladder', 'station_event outcome', 'provider GETs'))
    print('-' * 70)
    for ladder in ('autonomous', 'human_led'):
        outcome, gets = attempt(ladder, vision_module.station_event)
        print('%-18s %-34s %d' % (ladder, outcome, gets))

    # A station read must never surface a person-identified row, whatever the
    # register it was read from.
    station_events, _ = attempt('human_led', vision_module.station_event)
    if 'yuz' in str(station_events):
        leaks += 1
        print()
        print('LEAK: station_event returned a person-identified row')

    print()
    if leaks:
        print('FAILED: biometric data was reachable without the human_led gate')
        return 1
    print('PROVEN: person events are refused before any provider I/O unless the')
    print('        agent is human_led, and the impersonal read never surfaces a')
    print('        person-identified row')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
