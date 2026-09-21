"""Workforce oversight contract tests. Real Engine, real SQLite, scripted provider.

The behaviours that matter here are safety properties about *people*:

* the platform never evaluates or ranks an employee — there is no score, ratio or
  productivity figure anywhere in the output, which is asserted by looking for the
  keys rather than trusting the docstring;
* an unread roster is never rendered as "nobody is absent", because those two
  facts are dangerously similar to a manager;
* an ambiguous date is not guessed into "overdue", because guessing marks the
  wrong work late;
* a blank hours cell is not reported as zero hours, because that understates a
  shift;
* the register, range and column names are operator configuration and cannot be
  supplied by the caller.

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

from platform_runtime.engine import Engine, Forbidden
from platform_runtime.tools import build_registry
from platform_runtime.workforce import (
    WORKFORCE_TOOLS,
    _hours,
    attendance,
    shifts,
    workload,
    workforce_config,
)

TENANT = 't_work'
AGENT = 'mgmt.hr'
CONNECTION = 'hr'
SPREADSHEET = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'

POLICY = {
    'tools': ['sheets.rows', 'workforce.attendance', 'workforce.shifts',
              'workforce.workload'],
    'allowed_connections': [CONNECTION, 'google'],
    'ladder': 'human_assisted',
}

WORKFORCE = {
    'registers': {
        'attendance': {
            'register': 'hr', 'range': 'attendance',
            'id_column': 'id', 'name_column': 'Ism', 'status_column': 'Holat',
            'date_column': 'Sana',
            'absent_statuses': ['yoq', 'ta’til'],
            'done_statuses': ['keldi'],
        },
        'shifts': {
            'register': 'hr', 'range': 'shifts',
            'id_column': 'id', 'name_column': 'Ism', 'date_column': 'Sana',
            'status_column': 'Smena', 'hours_column': 'Soat',
        },
        'workload': {
            'register': 'hr', 'range': 'tasks',
            'id_column': 'id', 'name_column': 'Ism', 'task_column': 'Vazifa',
            'status_column': 'Holat', 'due_column': 'Muddat',
            'done_statuses': ['bajarildi'],
            'open_statuses': ['ochiq', 'jarayonda'],
        },
    }
}


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


class WorkforceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.transport = RecordingTransport()
        self.registers = {
            'hr': {'connection': 'google', 'spreadsheet_id': SPREADSHEET,
                   'ranges': {'attendance': 'Davomat!A1:D', 'shifts': 'Smena!A1:E',
                              'tasks': 'Vazifa!A1:E'},
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
        self.engine = Engine(self.root / 'work.db', build_registry(),
                             lambda t, a: self.policy, clock=self.clock)

    def write_config(self, workforce=None):
        self.cfg = self.root / 'integrations.json'
        self.cfg.write_text(json.dumps({TENANT: {
            'connections': {'google': {}},
            'sheets_registers': self.registers,
            'workforce': WORKFORCE if workforce is None else workforce,
        }}, ensure_ascii=False), encoding='utf-8')

    def call(self, fn, payload, **kwargs):
        """Invoke a workforce function with only the Sheets hop replaced."""
        self.transport.payload = payload
        with patch('platform_runtime.sheets.configured_manager') as manager:
            manager.return_value.access.return_value.access_token = 'fake-token'
            with patch('platform_runtime.sheets._http_get',
                       side_effect=lambda url, token: self.transport(url, token)):
                return fn(self.engine, TENANT, AGENT, **kwargs)

    # ---------------------------------------------------------- registration

    def test_every_workforce_tool_is_read_only(self):
        registry = build_registry()
        for name in WORKFORCE_TOOLS:
            self.assertEqual('read', registry.get(name).risk, name)

    def test_the_module_exposes_no_write_path(self):
        """No workforce function may mutate anything: names are read verbs only."""
        import platform_runtime.workforce as module
        public = {name for name in dir(module) if not name.startswith('_')}
        for banned in ('create', 'update', 'set', 'delete', 'approve', 'write',
                       'promote', 'score'):
            self.assertFalse([name for name in public if banned in name.lower()],
                             f'unexpected mutating surface matching {banned}')

    # -------------------------------------------------------------- honesty

    def test_the_output_contains_no_score_or_ranking(self):
        """The platform reports facts; it must not evaluate a person.

        This is asserted against the returned keys rather than the docstring, so a
        future change that adds ``productivity`` or ``score`` fails here.
        """
        result = self.call(workload, matrix(
            ['id', 'Ism', 'Vazifa', 'Holat', 'Muddat'],
            [['u1', 'Ali', 'Hisobot', 'ochiq', '2026-01-20']]))
        forbidden = ('score', 'rating', 'rank', 'productivity', 'efficiency',
                     'kpi', 'index', 'performance')
        flat = json.dumps(result, ensure_ascii=False, default=str).lower()
        for word in forbidden:
            self.assertNotIn(word, flat, f'{word!r} must not appear in workforce output')
        for key in ('people', 'overdue', 'overdue_count', 'open_count'):
            self.assertIn(key, result)

    def test_the_count_never_ships_without_the_rows_behind_it(self):
        """A manager must be able to check the reason, so names always accompany counts."""
        result = self.call(attendance, matrix(
            ['id', 'Ism', 'Holat', 'Sana'],
            [['u1', 'Ali', 'yoq', '2026-03-26'],
             ['u2', 'Vali', 'keldi', '2026-03-26']]))
        self.assertEqual(result['absent_count'], 1)
        self.assertEqual([r['name'] for r in result['absent']], ['Ali'])

    # ------------------------------------------------------------ attendance

    def test_attendance_reports_absences_from_the_declared_statuses(self):
        result = self.call(attendance, matrix(
            ['id', 'Ism', 'Holat', 'Sana'],
            [['u1', 'Ali', 'yoq', '2026-03-26'],
             ['u2', 'Vali', 'keldi', '2026-03-26'],
             ['u3', 'Nodira', 'ta’til', '2026-03-26']]))
        self.assertEqual(result['absent_count'], 2)
        self.assertEqual(result['present_count'], 1)
        self.assertEqual(sorted(r['id'] for r in result['absent']), ['u1', 'u3'])

    def test_attendance_surfaces_an_undeclared_status_instead_of_guessing(self):
        """A status the operator never declared is named, not silently counted as present."""
        result = self.call(attendance, matrix(
            ['id', 'Ism', 'Holat', 'Sana'],
            [['u1', 'Ali', 'kasallik', '2026-03-26']]))
        self.assertEqual(result['absent_count'], 0)
        self.assertEqual(result['unknown_status'], ['kasallik'])

    def test_attendance_skips_a_row_without_an_identity_and_counts_it(self):
        result = self.call(attendance, matrix(
            ['id', 'Ism', 'Holat', 'Sana'],
            [['', 'Kimdir', 'yoq', '2026-03-26'],
             ['u2', 'Vali', 'yoq', '2026-03-26']]))
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['absent_count'], 1)

    def test_attendance_requires_a_declared_register(self):
        self.write_config({'registers': {}})
        with self.assertRaises(Forbidden):
            self.call(attendance, matrix(['id'], []))

    def test_attendance_refuses_when_no_status_column_is_declared(self):
        """An empty status_column is legal in the schema and refused by the view.

        A blank optional column is how an operator marks "not used", so the config
        layer must accept it; the view that requires it is the layer that refuses.
        """
        broken = {'registers': {'attendance': dict(
            WORKFORCE['registers']['attendance'], status_column='')}}
        self.write_config(broken)
        self.assertEqual(workforce_config(TENANT)['registers']['attendance']['status_column'],
                         '')
        with self.assertRaises(Forbidden):
            self.call(attendance, matrix(['id'], []))

    def test_an_unread_register_is_not_an_empty_roster(self):
        """A provider failure must surface as an error, never as 'nobody is absent'."""
        from platform_runtime.sheets import SheetsError
        with self.assertRaises(SheetsError):
            with patch('platform_runtime.sheets.configured_manager') as manager:
                manager.return_value.access.return_value.access_token = 'fake-token'
                with patch('platform_runtime.sheets._http_get',
                           side_effect=SheetsError('Sheets transport failure')):
                    attendance(self.engine, TENANT, AGENT)

    # ---------------------------------------------------------------- shifts

    def test_shifts_reports_declared_hours_per_row(self):
        result = self.call(shifts, matrix(
            ['id', 'Ism', 'Sana', 'Smena', 'Soat'],
            [['u1', 'Ali', '2026-03-26', 'ertalab', '8'],
             ['u2', 'Vali', '2026-03-26', 'kech', '7,5']]))
        hours = [r['hours'] for r in result['shifts']]
        self.assertEqual(hours, [8.0, 7.5])

    def test_a_blank_hours_cell_is_not_zero_hours(self):
        """Zero and unreadable are different facts; reporting the first is wrong."""
        result = self.call(shifts, matrix(
            ['id', 'Ism', 'Sana', 'Smena', 'Soat'],
            [['u1', 'Ali', '2026-03-26', 'ertalab', ''],
             ['u2', 'Vali', '2026-03-26', 'kech', '8']]))
        by_id = {r['id']: r for r in result['shifts']}
        self.assertIsNone(by_id['u1']['hours'])
        self.assertEqual(by_id['u2']['hours'], 8.0)
        self.assertEqual(result['unreadable_hours'], 1)

    def test_shifts_returns_no_totals_or_efficiency_figure(self):
        result = self.call(shifts, matrix(
            ['id', 'Ism', 'Sana', 'Smena', 'Soat'],
            [['u1', 'Ali', '2026-03-26', 'ertalab', '8']]))
        for banned in ('total_hours', 'average', 'efficiency', 'utilization'):
            self.assertNotIn(banned, result)

    # -------------------------------------------------------------- workload

    def test_workload_counts_open_and_overdue_per_person(self):
        result = self.call(workload, matrix(
            ['id', 'Ism', 'Vazifa', 'Holat', 'Muddat'],
            [['u1', 'Ali', 'A', 'ochiq', '2026-01-20'],
             ['u1', 'Ali', 'B', 'ochiq', '2027-01-01'],
             ['u1', 'Ali', 'C', 'bajarildi', '2026-01-01'],
             ['u2', 'Vali', 'D', 'jarayonda', '2026-01-05']]))
        by_id = {p['id']: p for p in result['people']}
        self.assertEqual(by_id['u1']['open'], 2)
        self.assertEqual(by_id['u1']['overdue'], 1)
        self.assertEqual(by_id['u2']['open'], 1)
        self.assertEqual(by_id['u2']['overdue'], 1)
        self.assertEqual(result['overdue_count'], 2)

    def test_an_ambiguous_date_is_not_guessed_into_overdue(self):
        """``10.01.2026`` is day-first here but month-first elsewhere, so it is not judged."""
        result = self.call(workload, matrix(
            ['id', 'Ism', 'Vazifa', 'Holat', 'Muddat'],
            [['u1', 'Ali', 'A', 'ochiq', '10.01.2026'],
             ['u1', 'Ali', 'B', 'ochiq', '2026-01-20']]))
        self.assertEqual(result['unparsable_due'], 1)
        self.assertEqual(result['overdue_count'], 1)
        self.assertEqual([o['task'] for o in result['overdue']], ['B'])

    def test_a_done_task_is_never_overdue(self):
        result = self.call(workload, matrix(
            ['id', 'Ism', 'Vazifa', 'Holat', 'Muddat'],
            [['u1', 'Ali', 'A', 'bajarildi', '2020-01-01']]))
        self.assertEqual(result['overdue_count'], 0)
        self.assertEqual(result['open_count'], 0)

    def test_an_open_task_is_never_overdue(self):
        result = self.call(workload, matrix(
            ['id', 'Ism', 'Vazifa', 'Holat', 'Muddat'],
            [['u1', 'Ali', 'A', 'ochiq', '2999-01-01']]))
        self.assertEqual(result['overdue_count'], 0)
        self.assertEqual(result['open_count'], 1)

    def test_overdue_rows_carry_what_is_late(self):
        """The rows ship with the count so a manager can see the work, not just a figure."""
        result = self.call(workload, matrix(
            ['id', 'Ism', 'Vazifa', 'Holat', 'Muddat'],
            [['u1', 'Ali', 'Hisobotni topshirish', 'ochiq', '2026-01-20']]))
        self.assertEqual(result['overdue'][0]['task'], 'Hisobotni topshirish')
        self.assertEqual(result['overdue'][0]['due'], '2026-01-20')

    def test_workload_requires_a_task_column(self):
        broken = WORKFORCE['registers']['workload'].copy()
        broken['task_column'] = ''
        self.write_config({'registers': {'workload': broken}})
        with self.assertRaises(Forbidden):
            self.call(workload, matrix(['id'], []))

    def test_the_workload_view_is_not_truncated_at_exactly_the_limit(self):
        """The workload view slices a ranked list, so the cut is the slice itself."""
        rows = [[f'u{i}', f'P{i}', f'T{i}', 'ochiq', '2027-01-01'] for i in range(4)]
        result = self.call(workload, matrix(
            ['id', 'Ism', 'Vazifa', 'Holat', 'Muddat'], rows), limit=4)
        self.assertEqual(4, len(result['people']))
        self.assertFalse(result['truncated'])
        result = self.call(workload, matrix(
            ['id', 'Ism', 'Vazifa', 'Holat', 'Muddat'], rows + [
                ['u9', 'P9', 'T9', 'ochiq', '2027-01-01']]), limit=4)
        self.assertEqual(4, len(result['people']))
        self.assertTrue(result['truncated'])

    # ------------------------------------------------------------ validation

    def test_an_unknown_register_key_is_refused(self):
        broken = dict(WORKFORCE['registers']['attendance'], spreadsheet='abc')
        self.write_config({'registers': {'attendance': broken}})
        with self.assertRaises(ValueError):
            workforce_config(TENANT)

    def test_an_invalid_register_name_is_refused(self):
        self.write_config({'registers': {'Bad Name': WORKFORCE['registers']['attendance']}})
        with self.assertRaises(ValueError):
            workforce_config(TENANT)

    def test_an_unbounded_status_list_is_refused(self):
        broken = dict(WORKFORCE['registers']['attendance'],
                      absent_statuses=['x'] * 21)
        self.write_config({'registers': {'attendance': broken}})
        with self.assertRaises(ValueError):
            workforce_config(TENANT)

    def test_a_missing_workforce_block_is_an_empty_config_not_an_error(self):
        data = {TENANT: {'connections': {'google': {}},
                         'sheets_registers': self.registers}}
        self.cfg.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(workforce_config(TENANT), {'registers': {}})

    # ---------------------------------------------------------------- access

    def test_the_agent_must_hold_the_sheets_tool(self):
        self.policy = dict(POLICY, tools=['workforce.attendance'])
        with self.assertRaises(Forbidden):
            self.call(attendance, matrix(['id', 'Ism', 'Holat', 'Sana'],
                                         [['u1', 'A', 'yoq', '2026-03-26']]))

    def test_the_connection_must_be_permitted_for_the_agent(self):
        self.policy = dict(POLICY, allowed_connections=['other'])
        with self.assertRaises(Forbidden):
            self.call(attendance, matrix(['id', 'Ism', 'Holat', 'Sana'],
                                         [['u1', 'A', 'yoq', '2026-03-26']]))

    def test_an_undeclared_range_is_refused(self):
        self.registers['hr']['ranges'] = {'attendance': 'Davomat!A1:D'}
        self.write_config()
        with self.assertRaises(Forbidden):
            self.call(shifts, matrix(['id'], []))

    def test_the_caller_cannot_name_a_range_or_a_spreadsheet(self):
        """Register, range and columns are operator configuration, never arguments."""
        schema = build_registry().get('workforce.attendance').schema
        for banned in ('register', 'range', 'spreadsheet_id', 'columns', 'url'):
            self.assertNotIn(banned, schema['properties'])

    # --------------------------------------------------------------- bounds

    def test_limit_is_bounded(self):
        for bad in (0, -1, 51):
            with self.assertRaises(ValueError):
                self.call(attendance, matrix(['id'], []), limit=bad)

    def test_absent_only_must_be_a_boolean(self):
        with self.assertRaises(ValueError):
            self.call(attendance, matrix(['id'], []), absent_only=1)

    def test_truncation_is_reported(self):
        rows = [[f'u{i}', f'P{i}', 'yoq', '2026-03-26'] for i in range(80)]
        result = self.call(attendance, matrix(['id', 'Ism', 'Holat', 'Sana'], rows),
                           limit=10)
        self.assertEqual(result['absent_count'], 80)
        self.assertEqual(len(result['absent']), 10)
        self.assertTrue(result['truncated'])

    def test_an_exactly_full_result_is_not_reported_as_truncated(self):
        """A register holding exactly `limit` rows was answered completely.

        `truncated` used to be derived from `len(out) >= limit`, which is true both
        for a result that was cut and for one that landed exactly on the limit.
        The two mean opposite things to the operator: one says "ask again with a
        bigger limit", the other says "this is everything". Reporting the first when
        the second is true sends them to re-run a query that returns the same list.
        """
        rows = [[f'u{i}', f'P{i}', 'yoq', '2026-03-26'] for i in range(10)]
        result = self.call(attendance, matrix(['id', 'Ism', 'Holat', 'Sana'], rows),
                           limit=10)
        self.assertEqual(10, len(result['absent']))
        self.assertFalse(result['truncated'])

    def test_one_row_past_the_limit_is_reported_as_truncated(self):
        """The neighbour of the test above: the boundary must still detect a cut."""
        rows = [[f'u{i}', f'P{i}', 'yoq', '2026-03-26'] for i in range(11)]
        result = self.call(attendance, matrix(['id', 'Ism', 'Holat', 'Sana'], rows),
                           limit=10)
        self.assertEqual(10, len(result['absent']))
        self.assertTrue(result['truncated'])

    def test_the_shifts_view_agrees_with_the_attendance_view(self):
        """Both views carry the same bound, so both must answer it the same way."""
        rows = [[f'u{i}', f'P{i}', '2026-03-26', 'kunduzgi', '8'] for i in range(6)]
        result = self.call(shifts, matrix(
            ['id', 'Ism', 'Sana', 'Smena', 'Soat'], rows), limit=6)
        self.assertEqual(6, len(result['shifts']))
        self.assertFalse(result['truncated'])

    def test_no_credential_or_provider_url_reaches_the_output(self):
        result = self.call(attendance, matrix(
            ['id', 'Ism', 'Holat', 'Sana'],
            [['u1', 'Ali', 'yoq', '2026-03-26']]))
        flat = json.dumps(result, ensure_ascii=False)
        for secret in (SPREADSHEET, 'Davomat!A1:D', 'sheets.googleapis.com', 'fake-token'):
            self.assertNotIn(secret, flat)

    def test_scanning_scans_the_register_once(self):
        result = self.call(attendance, matrix(
            ['id', 'Ism', 'Holat', 'Sana'],
            [['u1', 'Ali', 'yoq', '2026-03-26']]))
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(result['scanned'], 1)


    def test_an_integer_past_the_float_range_is_unreadable_not_fatal(self):
        """``float(10 ** 400)`` raises; a shift cell must not crash the read.

        The string form is already protected twice over (the ASCII-number gate
        rejects anything non-numeric, and the finite guard rejects ``inf``), but the
        numeric form went straight to ``float()`` and raised.
        """
        self.assertIsNone(_hours(10 ** 400))
        self.assertIsNone(_hours(float('inf')))
        self.assertEqual(1e308, _hours(10 ** 308))
