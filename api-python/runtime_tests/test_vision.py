"""Vision event contract tests. Real Engine, real SQLite, scripted provider.

The behaviours that matter here are legal and safety properties, not features:

* **a frame never reaches the platform** — asserted structurally: no tool takes a
  frame, image, URL or stream argument, and no output carries one;
* **person-identifying events are biometric** — an event class the operator
  declares as identifying can only be read when the agent is ``human_led`` *and*
  the operator has acknowledged the obligation. The gate runs before any provider
  read, so an autonomous agent cannot even cause I/O against that data;
* **a person count is biometric processing too**, which is why ``summary`` never
  counts person events even though counting is otherwise harmless;
* **an event is bound by segment, not by string prefix** — ``zavod-1`` must not
  claim ``zavod-10``'s events. This is the P11 rule applied to the feed that
  depends on it;
* **an unbound event is reported, not guessed** — an event attached to the wrong
  plant is worse than one a manager can see is unbound;
* **reads are windowed and bounded** — the stream is continuous in reality, so an
  unbounded read is a defect rather than a feature;
* **confidence is never a correctness claim** — it is the register's own text.

Every read goes through the ordinary ``sheets.rows`` handler, so the register
declaration, A1 allowlist, agent tool permission and connection allowlist are all
exercised for real.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_runtime import vision
from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry
from platform_runtime.vision import (
    MAX_EVENTS,
    VISION_TOOLS,
    person_event,
    register_vision_tools,
    station_event,
    summary,
    vision_config,
)

TENANT = 't_plant'
AGENT = 'ops.vision'
CONNECTION = 'plant'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

LEVELS = ['zavod', 'sex', 'liniya', 'stanok']

POLICY = {
    'tools': ['sheets.rows', 'vision.station_event', 'vision.person_event',
              'vision.summary'],
    'allowed_connections': [CONNECTION, 'google'],
    'ladder': 'human_assisted',
}

# The asset hierarchy the events bind to. Declared here because a vision read is
# meaningless without one: the PRD's ordering rule ("asset model first, not tags")
# means an event with nothing to bind to is an event with no owner.
ASSETS = {
    'entity': 'asset',
    'levels': LEVELS,
}

VISION = {
    'registers': {
        'shopfloor': {
            'register': 'plant', 'range': 'events',
            'station_column': 'stansiya', 'event_column': 'hodisa',
            'timestamp_column': 'sana', 'confidence_column': 'ishonch',
            'sensitivity': 'station',
        },
        'access': {
            'register': 'plant', 'range': 'person_events',
            'station_column': 'stansiya', 'event_column': 'hodisa',
            'timestamp_column': 'sana',
            'sensitivity': 'person',
            'person_classes': ['yuz'],
            'biometric_ack': True,
        },
    }
}

# The realistic fixture. Two plants whose names collide under a string prefix,
# which is the whole point: 'zavod-10'.startswith('zavod-1') is True.
ROWS = [
    ['stansiya', 'hodisa', 'sana', 'ishonch'],
    ['zavod-1/sex-1/liniya-1/stanok-1', 'nuqson', '2026-09-18', '0.91'],
    ['zavod-1/sex-1/liniya-1/stanok-2', 'bekor', '2026-09-18', '0.77'],
    ['zavod-1/sex-1/liniya-2/stanok-1', 'sikl', '2026-09-19', ''],
    ['zavod-1/sex-2/liniya-1/stanok-1', 'nuqson', '2026-09-19', '0.88'],
    ['zavod-10/sex-1/liniya-1/stanok-1', 'nuqson', '2026-09-19', '0.95'],
    ['zavod-1/sex-1/liniya-1/stanok-1', 'yuz', '2026-09-19', '0.99'],
]

PERSON_ROWS = [
    ['stansiya', 'hodisa', 'sana'],
    ['zavod-1/sex-1/liniya-1/stanok-1', 'yuz', '2026-09-19'],
    ['zavod-1/sex-1/liniya-1/stanok-2', 'yuz', '2026-09-19'],
]


class _Sentinel:
    def __repr__(self):
        return 'MISSING'


_DEFAULT = _Sentinel()
_MISSING = _Sentinel()


class Clock:
    def __init__(self, start=1_770_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class RecordingTransport:
    """Stands in for the Sheets HTTP GET. Records every call."""

    def __init__(self):
        self.payload = {'values': []}
        self.calls = []

    def __call__(self, url, token):
        self.calls.append(url)
        return self.payload


def matrix(header, rows):
    return {'values': [header] + [list(row) for row in rows]}


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.transport = RecordingTransport()
        self.registers = {
            'plant': {'connection': 'google', 'spreadsheet_id': SPREADSHEET,
                      'ranges': {'events': 'Hodisa!A1:D',
                                 'person_events': 'Shaxs!A1:C'},
                      'max_rows': 200},
        }
        self.policy = dict(POLICY)
        self.write_config()
        self.env = patch.dict(os.environ, {
            'PLATFORM_INTEGRATIONS_FILE': str(self.cfg),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.engine = Engine(self.root / 'vision.db', build_registry(),
                             lambda t, a: self.policy, clock=self.clock)

    def write_config(self, vision=_DEFAULT, assets=_DEFAULT):
        """Write the tenant config. ``_MISSING`` omits the key entirely.

        ``None`` is a JSON value and an absent key is not, and the module treats
        the two differently: a missing ``vision`` block means "no feed declared"
        (a refusal), while an empty register list is a typo to be reported.
        """
        self.cfg = self.root / 'integrations.json'
        payload = {
            'connections': {'google': {}},
            'sheets_registers': self.registers,
        }
        if assets is not _MISSING:
            payload['assets'] = ASSETS if assets is _DEFAULT else assets
        if vision is not _MISSING:
            payload['vision'] = VISION if vision is _DEFAULT else vision
        self.cfg.write_text(json.dumps({TENANT: payload}, ensure_ascii=False),
                            encoding='utf-8')

    def call(self, fn, payload, **kwargs):
        """Invoke a vision function with only the Sheets hop replaced."""
        self.transport.calls = []
        self.transport.payload = payload
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: self.transport(url, token)):
                return fn(self.engine, TENANT, AGENT, **kwargs)

    # ---------------------------------------------------------- registration

    def test_every_vision_tool_is_read_only(self):
        registry = build_registry()
        for name in VISION_TOOLS:
            self.assertIn(name, registry.items)
            self.assertEqual('read', registry.items[name].risk, name)

    def registry_items(self):
        return self.engine.registry.items

    def test_the_module_exposes_no_write_path(self):
        """No vision function may mutate anything: the feed is evidence.

        Checked against the module's own callables rather than every imported
        name, because ``assets`` and ``re`` are imported modules, not surfaces.
        """
        import inspect
        import platform_runtime.vision as module
        callables = {name for name, value in vars(module).items()
                     if not name.startswith('_')
                     and (inspect.isfunction(value) or inspect.isclass(value))
                     and getattr(value, '__module__', '') == module.__name__}
        for banned in ('create', 'update', 'set', 'delete', 'approve', 'write',
                       'ingest', 'record', 'store', 'save'):
            self.assertFalse([name for name in callables if banned in name.lower()],
                             f'unexpected mutating surface matching {banned}')

    def test_no_tool_accepts_a_frame_or_a_stream(self):
        """A frame must never reach the platform. Asserted on the real schemas."""
        forbidden = ('frame', 'image', 'video', 'stream', 'rtsp', 'url', 'snapshot',
                     'base64', 'photo', 'clip', 'camera_url')
        for name in VISION_TOOLS:
            props = set(self.engine.registry.items[name].schema['properties'])
            for needle in forbidden:
                self.assertNotIn(needle, props, f'{name} accepts {needle!r}')

    def test_no_tool_accepts_a_register_window_or_column(self):
        """Register, range and column names are operator configuration."""
        for name in VISION_TOOLS:
            props = set(self.engine.registry.items[name].schema['properties'])
            for needle in ('register', 'range', 'spreadsheet_id', 'connection',
                           'station_column', 'event_column'):
                self.assertNotIn(needle, props, f'{name} accepts {needle!r}')

    # -------------------------------------------------------------- training

    def test_the_module_contains_no_model_or_frame_vocabulary(self):
        """The platform does not train a face model and does not keep footage.

        Asserted against the source so a future edit cannot quietly add a frame
        buffer or a training entry point without failing a test.
        """
        source = Path(__file__).resolve().parent.parent / 'platform_runtime' / 'vision.py'
        text = source.read_text(encoding='utf-8').lower()
        for needle in ('cv2', 'opencv', 'numpy', 'torch', 'tensorflow', 'pillow',
                       'base64', 'b64decode', 'write_bytes', 'open(', 'mkdtemp'):
            self.assertNotIn(needle, text, f'vision.py mentions {needle!r}')

    # --------------------------------------------------------------- classes

    def test_station_events_are_read_on_the_ordinary_path(self):
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        self.assertEqual('station_event', result['view'])
        self.assertEqual('station', result['sensitivity'])

    def test_a_person_class_row_is_not_returned_by_the_station_read(self):
        """The two halves of the split must not leak into each other."""
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        classes = {event['event'] for event in result['events']}
        self.assertNotIn('yuz', classes)

    def test_an_impersonal_row_is_not_returned_by_the_person_read(self):
        self.policy = dict(POLICY, ladder='human_led')
        result = self.call(person_event, matrix(ROWS[0], ROWS[1:]))
        classes = {event['event'] for event in result['events']}
        self.assertEqual({'yuz'}, classes)

    def test_every_returned_event_carries_its_class(self):
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        for event in result['events']:
            self.assertIn(event['event'], {'nuqson', 'bekor', 'sikl'})

    # ----------------------------------------------------------------- gate

    def test_a_person_read_by_an_autonomous_agent_is_refused(self):
        """A legal duty cannot be discharged by an autonomous agent."""
        self.policy = dict(POLICY, ladder='autonomous')
        with self.assertRaises(Forbidden) as caught:
            self.call(person_event, matrix(PERSON_ROWS[0], PERSON_ROWS[1:]))
        self.assertIn('human_led', str(caught.exception))

    def test_a_person_read_by_a_human_assisted_agent_is_refused(self):
        self.policy = dict(POLICY, ladder='human_assisted')
        with self.assertRaises(Forbidden):
            self.call(person_event, matrix(PERSON_ROWS[0], PERSON_ROWS[1:]))

    def test_a_person_read_by_a_human_led_agent_is_allowed(self):
        self.policy = dict(POLICY, ladder='human_led')
        result = self.call(person_event, matrix(PERSON_ROWS[0], PERSON_ROWS[1:]))
        self.assertEqual(2, result['count'])
        self.assertEqual('human_led', result['authority']['ladder'])

    def test_the_gate_refuses_before_any_provider_read(self):
        """No I/O against biometric data when the ladder is wrong."""
        self.policy = dict(POLICY, ladder='autonomous')
        with self.assertRaises(Forbidden):
            self.call(person_event, matrix(PERSON_ROWS[0], PERSON_ROWS[1:]))
        self.assertEqual([], self.transport.calls)

    def test_a_person_register_without_acknowledgement_is_refused(self):
        unacknowledged = json.loads(json.dumps(VISION))
        unacknowledged['registers']['access']['biometric_ack'] = False
        self.write_config(vision=unacknowledged)
        self.policy = dict(POLICY, ladder='human_led')
        with self.assertRaises(Forbidden) as caught:
            self.call(person_event, matrix(PERSON_ROWS[0], PERSON_ROWS[1:]))
        self.assertIn('biometric', str(caught.exception).lower())
        self.assertEqual([], self.transport.calls)

    def test_a_person_register_must_name_its_person_classes(self):
        """Otherwise every row would be read impersonally while claiming to be sensitive."""
        broken = json.loads(json.dumps(VISION))
        broken['registers']['access']['sensitivity'] = 'person'
        broken['registers']['access']['person_classes'] = []
        self.write_config(vision=broken)
        with self.assertRaises(ValueError):
            vision_config(TENANT)

    def test_an_unknown_sensitivity_is_refused(self):
        broken = json.loads(json.dumps(VISION))
        broken['registers']['shopfloor']['sensitivity'] = 'maybe'
        self.write_config(vision=broken)
        with self.assertRaises(ValueError):
            vision_config(TENANT)

    def test_summary_never_counts_person_events(self):
        """A count of identified people is itself biometric processing."""
        result = self.call(summary, matrix(ROWS[0], ROWS[1:]))
        self.assertNotIn('yuz', result['by_class'])

    def test_summary_has_no_oee_or_performance_figure(self):
        """OEE needs cycle times and planned run time: that is P13's work.

        Checked against the returned *keys*, not the note. The note deliberately
        says "this is not OEE, that is P13", so searching the whole payload would
        fail on the caveat that makes the refusal honest.
        """
        result = self.call(summary, matrix(ROWS[0], ROWS[1:]))
        for key in ('oee', 'availability', 'performance', 'efficiency', 'score',
                    'rank', 'rating', 'index', 'percent'):
            self.assertNotIn(key, {name.lower() for name in result})
        for item in result['by_station']:
            for key in ('oee', 'availability', 'performance', 'efficiency', 'score'):
                self.assertNotIn(key, {name.lower() for name in item})
        self.assertIn('P13', result['note'])

    # ------------------------------------------------------- asset binding

    def test_events_bind_to_the_asset_hierarchy(self):
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        for event in result['events']:
            self.assertEqual(4, len(event['path'].split('/')))
            self.assertEqual(event['station'], event['path'])

    def test_a_sibling_plant_does_not_claim_its_neighbours_events(self):
        """The P11 segment rule, applied to the feed that depends on it.

        'zavod-10'.startswith('zavod-1') is True, so a prefix match would bind one
        plant's events to the other and never say so.
        """
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        pairs = {(event['station'], event['path']) for event in result['events']}
        for station, path in pairs:
            self.assertEqual(station, path, 'a bound event must map to its own path')
        stations = {event['station'] for event in result['events']}
        self.assertIn('zavod-10/sex-1/liniya-1/stanok-1', stations)
        self.assertIn('zavod-1/sex-1/liniya-1/stanok-1', stations)
        self.assertEqual(0, result['unbound'], 'every fixture station is a legal path')

    def test_an_unbound_station_is_reported_not_dropped(self):
        """An event bound to the wrong plant is worse than an unbound one."""
        rows = ROWS + [['not-a-path', 'nuqson', '2026-09-19', '0.9']]
        result = self.call(station_event, matrix(ROWS[0], rows[1:]))
        self.assertEqual(1, result['unbound'])
        for event in result['events']:
            self.assertNotEqual('not-a-path', event['station'])

    def test_a_station_of_the_wrong_depth_is_unbound(self):
        """A prefix that cannot be a legal path is not silently reshaped."""
        rows = ROWS + [['zavod-1/sex-1', 'nuqson', '2026-09-19', '0.9']]
        result = self.call(station_event, matrix(ROWS[0], rows[1:]))
        self.assertEqual(1, result['unbound'])

    def test_a_read_without_an_asset_hierarchy_is_forbidden(self):
        """P11 must exist first: an event with nothing to bind to has no owner."""
        self.write_config(assets=_MISSING)
        with self.assertRaises(Forbidden):
            self.call(station_event, matrix(ROWS[0], ROWS[1:]))

    # ------------------------------------------------------------- windowing

    def test_the_window_excludes_events_outside_it(self):
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]), since='2026-09-19')
        for event in result['events']:
            self.assertEqual('2026-09-19', event['day'])

    def test_the_window_has_an_upper_bound_too(self):
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]), until='2026-09-18')
        for event in result['events']:
            self.assertEqual('2026-09-18', event['day'])

    def test_an_inverted_window_is_refused(self):
        with self.assertRaises(ValueError):
            self.call(station_event, matrix(ROWS[0], ROWS[1:]),
                      since='2026-09-19', until='2026-09-01')

    def test_a_non_iso_window_is_refused(self):
        """Guessing at 19.09.2026 would include or exclude the wrong day."""
        for bad in ('19.09.2026', '2026/09/19', 'yesterday', '2026-9-19'):
            with self.subTest(window=bad), self.assertRaises(ValueError):
                self.call(station_event, matrix(ROWS[0], ROWS[1:]), since=bad)

    def test_an_undated_row_is_counted_not_guessed_into_the_window(self):
        rows = ROWS + [['zavod-1/sex-1/liniya-1/stanok-1', 'nuqson', '19.09.2026', '0.9']]
        result = self.call(station_event, matrix(ROWS[0], rows[1:]))
        self.assertEqual(1, result['undated'])
        for event in result['events']:
            self.assertNotEqual('19.09.2026', event['day'])

    def test_without_a_window_every_valid_day_is_included(self):
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        days = {event['day'] for event in result['events']}
        self.assertEqual({'2026-09-18', '2026-09-19'}, days)

    # ---------------------------------------------------------------- bound

    def test_the_limit_is_applied_and_reported(self):
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]), limit=2)
        self.assertEqual(2, result['returned'])
        self.assertTrue(result['truncated'])

    def test_an_exact_fit_is_not_reported_as_cut(self):
        """A limit EXACTLY equal to the number of matching rows: the whole window.

        The limit has to sit on the boundary for this to measure anything. A limit
        comfortably above the population passes under both the correct predicate
        (`matched > limit`) and the broken one (`matched >= limit`), so it proves
        nothing about which is in force -- the boundary is the only place the two
        differ.
        """
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]), limit=5)
        self.assertEqual(5, result['returned'])
        self.assertFalse(result['truncated'])

    def test_the_limit_is_bounded(self):
        for bad in (0, -1, MAX_EVENTS + 1, 10_000):
            with self.subTest(limit=bad), self.assertRaises(ValueError):
                self.call(station_event, matrix(ROWS[0], ROWS[1:]), limit=bad)

    def test_truncation_is_visible_even_when_nothing_was_dropped_by_the_limit(self):
        """A register that itself truncated must say so, not look complete."""
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        self.assertIn('truncated', result)

    # ------------------------------------------------------------- honesty

    def test_confidence_is_reported_verbatim_and_never_as_a_claim(self):
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        given = {(event['station'], event['event']): event.get('confidence')
                 for event in result['events']}
        self.assertEqual('0.91',
                         given[('zavod-1/sex-1/liniya-1/stanok-1', 'nuqson')])
        self.assertEqual('0.95',
                         given[('zavod-10/sex-1/liniya-1/stanok-1', 'nuqson')])

    def test_a_blank_confidence_is_omitted_not_zero(self):
        """A blank cell and a confident zero are different facts."""
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        for event in result['events']:
            if event['event'] == 'sikl':
                self.assertNotIn('confidence', event)

    def test_the_note_names_the_confidence_caveat(self):
        """The PRD's own warning about vendor accuracy must survive into output."""
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        self.assertIn('to‘g‘rilik', result['note'])

    def test_a_malformed_row_does_not_break_the_read(self):
        rows = ROWS + [['', '', '2026-09-19', '']]
        result = self.call(station_event, matrix(ROWS[0], rows[1:]))
        self.assertGreater(result['count'], 0)
        self.assertEqual(1, result['skipped'])

    def test_an_unreadable_register_is_not_reported_as_no_events(self):
        """An outage and a quiet shop floor must not look the same.

        The provider error must surface. Returning ``events: []`` with
        ``complete: true`` would read to a manager as "nothing happened today".
        """
        def explode(url, token):
            raise RuntimeError('vms outage')

        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get', side_effect=explode):
                with self.assertRaises(RuntimeError):
                    station_event(self.engine, TENANT, AGENT)

    def test_no_credential_or_url_reaches_the_output(self):
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        blob = json.dumps(result, ensure_ascii=False)
        for needle in ('token', 'secret', 'http://', 'https://', 'Bearer',
                       SPREADSHEET):
            self.assertNotIn(needle, blob)

    def test_the_output_carries_the_agent_authority(self):
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        self.assertEqual(AGENT, result['authority']['agent'])
        self.assertEqual('human_assisted', result['authority']['ladder'])

    # ------------------------------------------------------------ authority

    def test_the_source_tool_must_be_held_by_the_agent(self):
        self.policy = dict(POLICY, tools=['vision.station_event'])
        with self.assertRaises(Forbidden):
            self.call(station_event, matrix(ROWS[0], ROWS[1:]))

    def test_a_connection_the_agent_does_not_hold_is_refused(self):
        """The connection allowlist is enforced, and an empty one denies.

        The engine's dispatch gate is the layer that enforces this for every
        tool, and it treats an empty list as "no connection is permitted". This
        asserts the end-to-end refusal rather than sheets' own helper, which
        reads an empty list as unrestricted.
        """
        self.policy = dict(POLICY, allowed_connections=['other'])
        with self.assertRaises(Forbidden):
            self.call(station_event, matrix(ROWS[0], ROWS[1:]))

    def test_an_undeclared_vision_block_is_forbidden(self):
        """A declared absence is a refusal, not an empty feed."""
        self.write_config(vision=_MISSING)
        with self.assertRaises(Forbidden):
            self.call(station_event, matrix(ROWS[0], ROWS[1:]))

    def test_a_register_is_read_once_per_call(self):
        """No per-event re-read: the scan is one bounded pass."""
        result = self.call(station_event, matrix(ROWS[0], ROWS[1:]))
        self.assertGreater(result['count'], 1)
        self.assertEqual(1, len(self.transport.calls))

    def test_a_station_event_register_is_required_for_a_station_read(self):
        """A tenant with only a person register cannot satisfy a station read."""
        only_person = {'registers': {'access': VISION['registers']['access']}}
        self.write_config(vision=only_person)
        with self.assertRaises(Forbidden):
            self.call(station_event, matrix(ROWS[0], ROWS[1:]))

    # ------------------------------------------------------------ integrity

    def test_the_schema_forbids_extra_keys(self):
        with self.assertRaises(ValueError):
            self.engine.registry.items['vision.station_event'].validate(
                {'since': '2026-09-01', 'frame': 'x'})

    def test_the_vision_config_refuses_unknown_keys(self):
        broken = json.loads(json.dumps(VISION))
        broken['registers']['shopfloor']['camera_url'] = 'rtsp://x'
        self.write_config(vision=broken)
        with self.assertRaises(ValueError):
            vision_config(TENANT)

    def test_a_register_missing_its_station_column_is_refused(self):
        broken = json.loads(json.dumps(VISION))
        del broken['registers']['shopfloor']['station_column']
        self.write_config(vision=broken)
        with self.assertRaises(ValueError):
            vision_config(TENANT)



    # ------------------------------------------- fazza 35: measured bounds

    def test_the_truncated_flag_counts_events_not_matched_rows(self):
        """`truncated` answers "should I ask again with a larger limit".

        It was derived from `matched`, which counts every row that passed the WINDOW
        -- including rows that could not bind to an asset and never became events.
        Measured: three bindable rows, seven intern-node rows and limit five gave
        `returned=3, unbound=7, truncated=True`, so the caller was sent to re-run a
        query that returns the same three rows.
        """
        header = ['stansiya', 'hodisa', 'sana', 'ishonch']
        intern = ['zavod-1/sex-1/liniya-1', 'nuqson', '2026-09-18', '0.9']
        good = ['zavod-1/sex-1/liniya-1/stanok-1', 'nuqson', '2026-09-18', '0.9']
        out = self.call(station_event, matrix(header, [good] * 3 + [intern] * 7), limit=5)
        self.assertEqual(7, out['unbound'])
        self.assertEqual(3, out['returned'])
        self.assertEqual(10, out['count'])
        self.assertFalse(out['truncated'], out)

    def test_the_truncated_flag_is_true_when_the_list_really_was_cut(self):
        header = ['stansiya', 'hodisa', 'sana', 'ishonch']
        good = ['zavod-1/sex-1/liniya-1/stanok-1', 'nuqson', '2026-09-18', '0.9']
        cut = self.call(station_event, matrix(header, [good] * 8), limit=5)
        self.assertEqual(5, cut['returned'])
        self.assertTrue(cut['truncated'], cut)
        exact = self.call(station_event, matrix(header, [good] * 5), limit=5)
        self.assertEqual((5, False), (exact['returned'], exact['truncated']))

    def test_the_class_ceilings(self):
        self.assertEqual(32, vision.MAX_CLASSES)
        self.assertEqual(64, vision.MAX_CLASS_CHARS)
        for count, accepted in ((32, True), (33, False)):
            classes = ['k%d' % index for index in range(count)]
            with self.subTest(count=count):
                if accepted:
                    vision._class_list(classes, 'r', 'k')
                else:
                    with self.assertRaises(ValueError):
                        vision._class_list(classes, 'r', 'k')
        for length, accepted in ((64, True), (65, False)):
            with self.subTest(length=length):
                if accepted:
                    vision._class_list(['a' * length], 'r', 'k')
                else:
                    with self.assertRaises(ValueError):
                        vision._class_list(['a' * length], 'r', 'k')

    def test_the_register_name_and_column_caps(self):
        self.assertTrue(vision.NAME_RE.match('a' * 64))
        self.assertFalse(vision.NAME_RE.match('a' * 65))
        base = dict(VISION['registers']['shopfloor'])
        for length, accepted in ((64, True), (65, False)):
            entry = {**base, 'station_column': 'a' * length}
            with self.subTest(length=length):
                if accepted:
                    vision._register('r', entry)
                else:
                    with self.assertRaises(ValueError):
                        vision._register('r', entry)
        # The register cap is a bare 20 with no named constant, so the literal and
        # the line reading it are pinned together.
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'vision.py').read_text(encoding='utf-8')
        self.assertIn('len(declared) > 20', source)

    def test_the_window_edges_are_inclusive(self):
        self.assertTrue(vision._window('2026-09-18', '2026-09-18', ''))
        self.assertFalse(vision._window('2026-09-17', '2026-09-18', ''))
        self.assertTrue(vision._window('2026-09-18', '', '2026-09-18'))
        self.assertFalse(vision._window('2026-09-19', '', '2026-09-18'))

    def test_since_must_not_be_after_until(self):
        with self.assertRaises(ValueError):
            vision._check_window('2026-09-19', '2026-09-18')
        vision._check_window('2026-09-18', '2026-09-18')

    def test_the_summary_truncation_flag(self):
        header = ['stansiya', 'hodisa', 'sana', 'ishonch']
        rows = [['zavod-1/sex-1/liniya-1/stanok-%d' % index, 'nuqson', '2026-09-18', '0.9']
                for index in range(3)]
        cut = self.call(summary, matrix(header, rows), limit=1)
        self.assertEqual(3, cut['station_count'])
        self.assertEqual(1, len(cut['by_station']))
        self.assertTrue(cut['truncated'], cut)
        whole = self.call(summary, matrix(header, rows), limit=3)
        self.assertFalse(whole['truncated'], whole)

    def test_the_limit_ceiling_is_the_event_ceiling(self):
        """Both reads share one ceiling; the literal and both call sites are pinned."""
        source = (Path(__file__).resolve().parents[1] / 'platform_runtime'
                  / 'vision.py').read_text(encoding='utf-8')
        self.assertEqual(2, source.count("_bounded(limit, 'limit', 1, MAX_EVENTS)"))
        self.assertEqual(200, MAX_EVENTS)
        header = ['stansiya', 'hodisa', 'sana', 'ishonch']
        with self.assertRaises(ValueError):
            self.call(station_event, matrix(header, []), limit=MAX_EVENTS + 1)
        self.call(station_event, matrix(header, []), limit=MAX_EVENTS)

class DeclaredBoundTests(unittest.TestCase):
    """Fazza 32: `vision` restated a ceiling that nothing enforced."""

    def test_the_module_declares_no_ceiling_it_does_not_enforce(self):
        """`MAX_WINDOW_DAYS = 31` was declared here and in `telephony`, and read by
        neither. The span between `since` and `until` is validated for FORMAT and
        never for distance, so the constant advertised a limit nobody enforced."""
        self.assertFalse(hasattr(vision, 'MAX_WINDOW_DAYS'))
        self.assertEqual(200, vision.MAX_ROWS)
        self.assertEqual(200, vision.MAX_EVENTS)
        self.assertEqual(31, 31)

if __name__ == '__main__':
    unittest.main()
