"""Every shipped ``config/*.example.*`` passes its module's own validator.

An example the validator rejects teaches operators a shape that fails at runtime.
Each JSON example is served the way production reads configuration -- as
``PLATFORM_INTEGRATIONS_FILE`` -- with an empty ``PACKS_DIR`` so no real pack's
``integrations.yaml`` can shadow it. Replaces the 13 ``scripts/check_*_example.py``
scripts (see git history); the refusal cases they also exercised are covered by each
module's own test file.
"""
import inspect
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from platform_runtime import (assets, business_graph, documents, erp, escalation,
                              inventory, manufacturing, oee, telephony, tools, vision,
                              whatsapp, whatsapp_inbound, workforce)

CONFIG = Path(__file__).resolve().parents[2] / 'config'

VALIDATORS = {
    'assets.example.json': [assets.levels],
    'documents.example.json': [documents.documents_config],
    'erp_posting.example.json': [erp.erp_config],
    'inventory.example.json': [inventory.inventory_config, business_graph.graph_config],
    'manufacturing.example.json': [manufacturing.manufacturing_config],
    'oee.example.json': [oee.oee_config],
    'telephony.example.json': [telephony.telephony_config],
    'vision.example.json': [vision.vision_config],
    'whatsapp.example.json': [whatsapp.whatsapp_config],
    'whatsapp_webhook.example.json': [whatsapp_inbound._app_secret],
    'workforce.example.json': [workforce.workforce_config],
}


def escalation_schedules():
    """The two-level ``escalation: <id>: <key>: <value>`` block, read without PyYAML
    (CI's offline job does not install it). Values are JSON scalars or bare words."""
    schedules, current = {}, None
    text = (CONFIG / 'escalation.example.yaml').read_text(encoding='utf-8')
    for line in text.splitlines():
        body = line.split('#', 1)[0].rstrip()
        if body.startswith('    '):
            key, _, raw = body.strip().partition(':')
            try:
                current[key] = json.loads(raw)
            except ValueError:
                current[key] = raw.strip()
        elif body.startswith('  '):
            current = schedules[body.strip().rstrip(':')] = {}
    return schedules


class ConfigExampleTests(unittest.TestCase):
    def test_json_examples_pass_their_module_validator(self):
        with tempfile.TemporaryDirectory() as empty_packs:
            for name, validators in VALIDATORS.items():
                path = CONFIG / name
                tenants = [key for key in json.loads(path.read_text(encoding='utf-8'))
                           if not key.startswith('_')]
                self.assertTrue(tenants, name)
                env = {'PLATFORM_INTEGRATIONS_FILE': str(path), 'PACKS_DIR': empty_packs,
                       'META_APP_SECRET': 'example-secret'}
                with mock.patch.dict(os.environ, env):
                    for tenant in tenants:
                        for validate in validators:
                            with self.subTest(example=name, validator=validate.__name__):
                                result = validate(tenant)
                                # An absent block validates to an all-empty result, so
                                # "did not raise" alone would pass a misspelt block.
                                self.assertTrue(any(result.values())
                                                if isinstance(result, dict) else result)

    def test_capabilities_example_names_only_registered_tools(self):
        text = (CONFIG / 'agent-capabilities.example.yaml').read_text(encoding='utf-8')
        declared = {name.strip() for group in re.findall(r'tools:\s*\[([^\]]*)\]', text)
                    for name in group.split(',') if name.strip()}
        self.assertTrue(declared)
        self.assertEqual(set(), declared - set(tools.build_registry().items))

    def test_escalation_example_passes_the_module_bounds(self):
        schedules = escalation_schedules()
        self.assertEqual({'workforce', 'telephony'},
                         {s.get('source', 'workforce') for s in schedules.values()})
        configure = inspect.signature(escalation.EscalationLoop.configure)
        for schedule_id, settings in schedules.items():
            with self.subTest(schedule=schedule_id):
                self.assertRegex(schedule_id, escalation.SCHEDULE_ID_RE)
                # Unknown or missing keys fail the bind exactly as a configure call would.
                configure.bind(None, 'tenant', schedule_id, actor='owner', **settings)
                self.assertIn(settings.get('source', 'workforce'), escalation.SOURCE_TOOLS)
                for knob in escalation.LIMITS.keys() & settings.keys():
                    escalation._bounded(settings[knob], knob)


if __name__ == '__main__':
    unittest.main()
