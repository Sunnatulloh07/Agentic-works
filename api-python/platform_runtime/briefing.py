"""Owner-configured briefing: a scheduled per-role digest read from the Business Graph.

A briefing answers one question a manager actually asks in the morning: *what
changed, and what needs a decision today?* It is the same coordinator shape as
``reengagement`` — schedule, bounded read, dedup, deliver — with two deliberate
differences that follow from what a briefing *is*.

1. **The source is the Business Graph, not a single feed.** A briefing is
   cross-system by nature: today's orders from the CRM, low stock from the ERP,
   a margin a clerk typed into a sheet. Reading those through
   ``graph.search`` / ``graph.conflicts`` means every source's authority is
   checked before any provider I/O, every attribute keeps the system that
   produced it, and a disagreement between two systems is *reported* in the
   digest rather than silently averaged away.

2. **Delivery is read + notify, so no approval is required.** The digest is
   assembled deterministically in this module from already-authorized reads, and
   the only write is ``telegram.send`` — the same "tell the operator" channel
   the rest of the platform uses. A briefing never writes to a customer system
   and never contacts a customer, so gating it behind an approval would train
   operators to approve reflexively, which is exactly what we do not want an
   approval queue to teach.

Boundaries that make this safe to schedule unattended:

* The recipient is **operator configuration**, never model output. A digest
  cannot be redirected to an arbitrary chat by anything the model produces.
* A source that could not be read is **named in the digest** and the digest
  carries ``complete: false``. It is never rendered as "nothing to report":
  silence and an outage must not look the same to a manager.
* Conflicts are carried into the digest as facts. The briefing does not pick a
  winner; that is the operator's ``conflict_policy`` decision, made at the graph
  layer, and if no policy picks one the digest says so.
* A destructive or write graph source is impossible by construction, because
  ``business_graph`` only accepts read tools as sources.
* Authority is re-checked every cycle and again inside the send, so revoking the
  configuring owner or freezing the tenant stops delivery.
* The dedup key is ``(tenant, schedule, day)``. A briefing is a *daily* artefact;
  retrying it must not send the same manager two digests because a worker
  restarted. A failed send is retried on the next tick through the ledger's
  ``queued`` state, which is the one repeatable state.
"""
from __future__ import annotations

import re

from .engine import Conflict, Forbidden, NotFound

# Ledger states. Only 'queued' is repeatable: everything else is a delivered
# result the recipient already saw, a send the engine still owns, or a state a
# human must look at.
#
# 'submitted' is the handover: the digest became an ordinary engine task and the
# ledger row carries its id. The engine decides whether a human approves it and
# whether it went out; a later tick reads that verdict back into the ledger.
# 'uncertain' is terminal for the day: the provider may have delivered, and a
# second send would be a duplicate the recipient cannot un-read.
LEDGER_QUEUED = 'queued'
LEDGER_SUBMITTED = 'submitted'
LEDGER_SENT = 'sent'
LEDGER_FAILED = 'failed'
LEDGER_UNCERTAIN = 'uncertain'
# How the engine's task status settles a submitted ledger row.
_TASK_TO_LEDGER = {'succeeded': LEDGER_SENT, 'failed': LEDGER_FAILED,
                   'cancelled': LEDGER_FAILED, 'uncertain': LEDGER_UNCERTAIN}
# Task channel and key prefix. 'cron' means the engine re-checks the owner's
# membership at submit and at dispatch, as it does for every scheduled task.
DELIVERY_CHANNEL = 'cron'

POLICY_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')
DELIVERY_TOOLS = frozenset({'telegram.send'})

LIMITS = {
    'interval_seconds': (300, 604800),
    'hour': (0, 23),
    'minute': (0, 59),
    'max_sections': (1, 12),
    'max_rows': (1, 50),
    'max_seconds': (60, 86400),
}

DAY_SECONDS = 86400

SCHEMA = '''
CREATE TABLE IF NOT EXISTS p_briefing(
 tenant TEXT NOT NULL, id TEXT NOT NULL, actor TEXT NOT NULL, agent TEXT NOT NULL,
 recipient TEXT NOT NULL, connection TEXT NOT NULL,
 interval_seconds INTEGER NOT NULL, hour INTEGER NOT NULL, minute INTEGER NOT NULL,
 timezone_offset_minutes INTEGER NOT NULL, max_sections INTEGER NOT NULL,
 max_rows INTEGER NOT NULL, max_seconds INTEGER NOT NULL,
 title TEXT NOT NULL DEFAULT '',
 sections TEXT NOT NULL DEFAULT '[]',
 enabled INTEGER NOT NULL DEFAULT 1, next_due REAL NOT NULL, last_run REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS p_briefing_ledger(
 tenant TEXT NOT NULL, schedule TEXT NOT NULL, day TEXT NOT NULL,
 run_id TEXT NOT NULL DEFAULT '', attempt INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL, sections INTEGER NOT NULL DEFAULT 0, rows INTEGER NOT NULL DEFAULT 0,
 digest TEXT NOT NULL DEFAULT '', first_seen REAL NOT NULL, last_attempt REAL NOT NULL,
 updated REAL NOT NULL, last_error TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(tenant,schedule,day));
CREATE INDEX IF NOT EXISTS p_briefing_due ON p_briefing(tenant,enabled,next_due);
CREATE INDEX IF NOT EXISTS p_briefing_ledger_day ON p_briefing_ledger(tenant,day);
'''


def _bounded(value, name):
    low, high = LIMITS[name]
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def _identifier(value, name, maximum=64):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f'{name} is required')
    return value.strip()


class Briefing:
    """Scheduled per-role digest. Reads the graph, notifies the operator, writes nothing else."""

    def __init__(self, engine, graph=None):
        self.engine = engine
        self.graph = graph

    # ------------------------------------------------------------------ setup

    def configure(self, tenant, schedule_id, agent, recipient, connection, actor, *,
                  sections, interval_seconds=DAY_SECONDS, hour=8, minute=0,
                  timezone_offset_minutes=300, max_sections=6, max_rows=5,
                  max_seconds=1800, title='', enabled=True):
        """Owner-only. Every knob and every section is validated before it can run.

        A section is ``{'entity': ..., 'attribute': ..., 'equals': ...}`` for a
        graph search, or ``{'entity': ..., 'conflicts': true}`` to report where
        two systems disagree. The model never sees or produces these: they are
        operator configuration, exactly like a graph source.
        """
        schedule_id = _identifier(schedule_id, 'schedule id', 64)
        if not POLICY_ID_RE.match(schedule_id):
            raise ValueError('schedule id must be lowercase letters, digits, dot, dash '
                             'or underscore')
        agent = _identifier(agent, 'agent', 128)
        recipient = _identifier(recipient, 'recipient', 128)
        connection = _identifier(connection, 'connection', 128)
        actor = _identifier(actor, 'actor', 128)
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        if not isinstance(title, str) or len(title) > 120:
            raise ValueError('title must be a string of at most 120 characters')
        settings = {
            'interval_seconds': _bounded(interval_seconds, 'interval_seconds'),
            'hour': _bounded(hour, 'hour'),
            'minute': _bounded(minute, 'minute'),
            'max_sections': _bounded(max_sections, 'max_sections'),
            'max_rows': _bounded(max_rows, 'max_rows'),
            'max_seconds': _bounded(max_seconds, 'max_seconds'),
        }
        if type(timezone_offset_minutes) is not int or not -1440 <= timezone_offset_minutes <= 1440:
            raise ValueError('timezone_offset_minutes must be an integer -1440..1440')
        clean = self._validate_sections(sections, settings['max_sections'])
        # The agent policy must exist before a schedule points at it, otherwise
        # every cycle would fail later with an opaque "policy unavailable".
        policy = self.engine.policy(tenant, agent)
        if policy.get('ladder') not in {'human_led', 'human_assisted', 'autonomous'}:
            raise Forbidden('Agent policy unavailable for briefing')
        # The digest is delivered as an engine task, so the engine's own rules
        # apply: the agent must hold the notifier and the recipient must be
        # pack-allowlisted. Refusing here turns a first-cycle Forbidden into a
        # configure-time error the owner can read.
        if 'telegram.send' not in (policy.get('tools') or []):
            raise Forbidden('Briefing agent must hold telegram.send to deliver a digest')
        if recipient not in (policy.get('allowed_recipients') or []):
            raise Forbidden('Briefing recipient must be pack-allowlisted for the agent')
        e = self.engine
        with e.tx() as c:
            e.require_active(c, tenant)
            e.require_authority(c, tenant, 'cron', actor, ('owner',))
            now = e.clock()
            c.execute('''INSERT INTO p_briefing
              (tenant,id,actor,agent,recipient,connection,interval_seconds,hour,minute,
               timezone_offset_minutes,max_sections,max_rows,max_seconds,title,sections,
               enabled,next_due,last_run)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
              ON CONFLICT(tenant,id) DO UPDATE SET
               actor=excluded.actor, agent=excluded.agent, recipient=excluded.recipient,
               connection=excluded.connection, interval_seconds=excluded.interval_seconds,
               hour=excluded.hour, minute=excluded.minute,
               timezone_offset_minutes=excluded.timezone_offset_minutes,
               max_sections=excluded.max_sections, max_rows=excluded.max_rows,
               max_seconds=excluded.max_seconds, title=excluded.title,
               sections=excluded.sections, enabled=excluded.enabled,
               next_due=excluded.next_due''',
                      (tenant, schedule_id, actor, agent, recipient, connection,
                       settings['interval_seconds'], settings['hour'], settings['minute'],
                       timezone_offset_minutes, settings['max_sections'],
                       settings['max_rows'], settings['max_seconds'], title,
                       _dump(clean), int(enabled), now + settings['interval_seconds']))
            e.audit(c, tenant, '', 'briefing.configured', actor,
                    {'schedule': schedule_id, 'agent': agent, 'recipient': recipient,
                     'sections': len(clean), 'enabled': enabled})
        return self.schedule(tenant, schedule_id)

    def _validate_sections(self, sections, maximum):
        if not isinstance(sections, list) or not 1 <= len(sections) <= maximum:
            raise ValueError(f'sections must be a list of 1..{maximum} entries')
        clean = []
        for item in sections:
            if not isinstance(item, dict):
                raise ValueError('each section must be an object')
            unknown = set(item) - {'entity', 'attribute', 'equals', 'conflicts', 'label'}
            if unknown:
                raise ValueError(f'section has unsupported keys: {sorted(unknown)}')
            entity = _identifier(item.get('entity'), 'section entity', 64)
            label = item.get('label', '')
            if not isinstance(label, str) or len(label) > 80:
                raise ValueError('section label must be a string of at most 80 characters')
            if item.get('conflicts') is True:
                clean.append({'entity': entity, 'conflicts': True, 'label': label})
                continue
            attribute = _identifier(item.get('attribute'), 'section attribute', 64)
            equals = _identifier(item.get('equals'), 'section equals value', 200)
            clean.append({'entity': entity, 'attribute': attribute, 'equals': equals,
                          'label': label})
        return clean

    def schedule(self, tenant, schedule_id):
        with self.engine.read() as c:
            row = c.execute('SELECT * FROM p_briefing WHERE tenant=? AND id=?',
                            (tenant, schedule_id)).fetchone()
        if not row:
            raise NotFound('Briefing schedule not found')
        out = dict(row)
        out['sections'] = _load(row['sections'])
        return out

    def schedules(self, tenant):
        with self.engine.read() as c:
            rows = [dict(row) for row in c.execute(
                'SELECT * FROM p_briefing WHERE tenant=? ORDER BY id', (tenant,))]
        for row in rows:
            row['sections'] = _load(row['sections'])
        return rows

    def ledger(self, tenant, schedule_id, limit=100, *, with_total=False):
        """Recent ledger rows for one schedule, newest first.

        **A bare list cannot say whether it is the whole ledger.** This was written
        off as "a trap, not a live defect, because nothing in the runtime calls it"
        -- and that reasoning was wrong the moment the HTTP surface was checked:
        ``GET /{tenant}/briefing/{schedule}/ledger`` returns these rows as
        ``entries``, and a schedule with four hundred runs and one with a hundred and
        both answered ``limit`` of them identically. An operator reading ``entries``
        as the run history cannot count the runs.

        ``with_total=True`` counts the table in the same read and returns
        ``(rows, total, truncated)``, as ``erp.ledger_page`` does, so the page and
        the population it came from cannot disagree. ``truncated`` compares against
        the population, not against the limit, so a ledger holding exactly ``limit``
        rows is reported as complete.
        """
        limit = min(max(1, int(limit)), 500)
        with self.engine.read() as c:
            rows = [dict(row) for row in c.execute(
                '''SELECT * FROM p_briefing_ledger WHERE tenant=? AND schedule=?
                   ORDER BY last_attempt DESC LIMIT ?''', (tenant, schedule_id, limit))]
            if not with_total:
                return rows
            total = c.execute(
                'SELECT count(*) n FROM p_briefing_ledger WHERE tenant=? AND schedule=?',
                (tenant, schedule_id)).fetchone()['n']
        return rows, total, total > len(rows)

    def _disable(self, tenant, schedule, reason):
        e = self.engine
        with e.tx() as c:
            c.execute('UPDATE p_briefing SET enabled=0 WHERE tenant=? AND id=?',
                      (tenant, schedule['id']))
            e.audit(c, tenant, '', 'briefing.disabled', 'briefing-loop',
                    {'schedule': schedule['id'], 'reason': reason})

    def _advance(self, tenant, schedule, now, *, enabled=None):
        e = self.engine
        with e.tx() as c:
            if enabled is None:
                c.execute('UPDATE p_briefing SET next_due=?,last_run=? WHERE tenant=? AND id=?',
                          (now + schedule['interval_seconds'], now, tenant, schedule['id']))
            else:
                c.execute('''UPDATE p_briefing SET next_due=?,last_run=?,enabled=?
                  WHERE tenant=? AND id=?''',
                          (now + schedule['interval_seconds'], now, int(enabled), tenant,
                           schedule['id']))

    # ------------------------------------------------------------------- digest

    def _call(self, tool_name, tenant, agent, args, step):
        """Invoke a read tool through its ordinary handler.

        Going through the registry means agent tool permission, connection
        allowlist and the schema gate all apply exactly as for a manual call.
        """
        tool = self.engine.registry.get(tool_name)
        tool.validate(args)
        return tool.handler(self.engine, tenant, agent, args, step)

    def _section_rows(self, tenant, schedule, section, step):
        """Read one section through the graph.

        Returns ``(rows, status, error)`` where ``status`` is one of ``'ok'``,
        ``'partial'`` (a source did not fully read) or ``'failed'`` (no source
        read at all). The three are kept apart because a digest must never print
        ``(0)`` for a section whose data simply could not be fetched: a manager
        reading "0 new orders" would act on it, and "we could not look" is a
        different fact.
        """
        entity = section['entity']
        try:
            if section.get('conflicts'):
                result = self._call('graph.conflicts', tenant, schedule['agent'],
                                    {'entity': entity, 'limit': schedule['max_rows']},
                                    step)
                rows = [{'id': item['id'], 'attribute': item.get('attribute', ''),
                         'values': item.get('values', [])}
                        for item in result.get('conflicts', [])]
            else:
                result = self._call('graph.search', tenant, schedule['agent'],
                                    {'entity': entity, 'attribute': section['attribute'],
                                     'equals': section['equals'],
                                     'limit': schedule['max_rows']}, step)
                rows = [{'id': item['id'], 'attribute': item.get('attribute', ''),
                         'value': item.get('value'),
                         'source': item.get('source', ''), 'conflict': item.get('conflict', False)}
                        for item in result.get('matches', [])]
        except Forbidden:
            # A configuration that can no longer be authorized must stop the whole
            # digest; it is not a section-level hiccup.
            raise
        except (Conflict, ValueError, LookupError) as error:
            return [], 'failed', type(error).__name__
        if result.get('complete', True):
            return rows, 'ok', None
        # The graph read something but not everything. Distinguish "one source of
        # several failed" from "no source could be read at all": the first is a
        # partial answer, the second is not an answer.
        status = 'partial' if rows else 'failed'
        return rows, status, 'source_unreadable'

    def _digest(self, tenant, schedule, now):
        """Assemble the digest from authorized reads. Deterministic and bounded.

        Returns ``(text, stats)``. The model is not involved: a briefing is a
        report of facts, and letting a model paraphrase "stock is low" would add
        a rephrasing step that can only lose accuracy.
        """
        sections = schedule['sections']
        step = 'briefing:' + schedule['id']
        lines = []
        header = schedule.get('title') or f"Kunlik brifing — {schedule['id']}"
        lines.append(header)
        lines.append('')
        total_rows, unread, failed = 0, [], []
        for index, section in enumerate(sections, 1):
            rows, status, error = self._section_rows(tenant, schedule, section, step)
            label = section.get('label') or section['entity']
            if status == 'failed':
                # Never print a row count for a section we could not read: "(0)"
                # reads as "nothing happened" and a manager would act on it.
                failed.append(section['entity'])
                lines.append(f'{index}. {label}')
                lines.append('   ! manba o‘qilmadi — bu bo‘lim to‘liq emas, '
                             '"yozuv yo‘q" degani EMAS')
                lines.append('')
                continue
            lines.append(f'{index}. {label} ({len(rows)})')
            if status == 'partial':
                unread.append(section['entity'])
                lines.append('   ! manba to‘liq o‘qilmadi — natija to‘liq emas')
            if not rows:
                lines.append('   - yozuv yo‘q')
            for row in rows:
                total_rows += 1
                lines.append('   - ' + _render_row(section, row))
            lines.append('')
        lines.append(f"Tayyorlandi: {_stamp(now)}")
        if unread:
            lines.append('Diqqat: quyidagi manbalar to‘liq o‘qilmadi — '
                         + ', '.join(sorted(set(unread))))
        if failed:
            lines.append('Diqqat: quyidagi bo‘limlar o‘qilmadi — '
                         + ', '.join(sorted(set(failed))))
        text = '\n'.join(lines)[:4000]
        return text, {'sections': len(sections), 'rows': total_rows,
                      'complete': not unread and not failed,
                      'unread': sorted(set(unread)), 'failed': sorted(set(failed))}

    # ------------------------------------------------------------------- cycle

    def _claim(self, c, tenant, schedule, day, now):
        """Atomically claim today's delivery. Only a previous 'queued' row repeats."""
        row = c.execute('''SELECT attempt,status FROM p_briefing_ledger
          WHERE tenant=? AND schedule=? AND day=?''',
                        (tenant, schedule['id'], day)).fetchone()
        if row is None:
            c.execute('''INSERT INTO p_briefing_ledger
              (tenant,schedule,day,run_id,attempt,status,sections,rows,digest,
               first_seen,last_attempt,updated,last_error)
              VALUES(?,?,?,'',1,?,0,0,'',?,?,?,'')''',
                      (tenant, schedule['id'], day, LEDGER_QUEUED, now, now, now))
            return 1
        if row['status'] != LEDGER_QUEUED:
            return None
        # Compare-and-set: a second worker reading the same row updates zero rows
        # and therefore must not deliver a second digest.
        updated = c.execute('''UPDATE p_briefing_ledger
          SET attempt=attempt+1,last_attempt=?,updated=?,last_error=''
          WHERE tenant=? AND schedule=? AND day=? AND attempt=?''',
                            (now, now, tenant, schedule['id'], day, row['attempt']))
        return row['attempt'] + 1 if updated.rowcount == 1 else None

    def _deliver(self, tenant, schedule, text, day):
        """Hand the digest to the engine as an ordinary task; return its id.

        This loop never calls a provider. The engine decides whether a human must
        approve the send: an autonomous agent whose recipient is pack-allowlisted
        goes out unattended, anything else waits in the approval queue. Either way
        the send has a step, an audit row, a lease and the uncertain discipline
        every other external write has. The key is one per schedule-day, so a
        crash between submit and mark cannot create a second task.
        """
        args = {'conversation_id': schedule['recipient'], 'text': text}
        key = f"briefing:{schedule['id']}:{day}"
        try:
            return self.engine.submit(tenant, DELIVERY_CHANNEL, key, schedule['agent'],
                                      [{'tool': 'telegram.send', 'args': args}],
                                      schedule['actor'])
        except Conflict:
            # Same key, different text: the digest was already submitted and the
            # clock moved before the ledger was marked. Reuse that task.
            with self.engine.read() as c:
                row = c.execute('SELECT id FROM p_tasks WHERE tenant=? AND channel=? AND event_key=?',
                                (tenant, DELIVERY_CHANNEL, key)).fetchone()
            if not row:
                raise
            return row['id']

    def _settle(self, tenant):
        """Read the engine's verdict on every submitted digest back into the ledger.

        Bookkeeping only: it never sends, never advances a schedule, and does not
        count as work for the caller's return value.
        """
        e = self.engine
        with e.read() as c:
            pending = [dict(r) for r in c.execute('''SELECT l.schedule,l.day,l.run_id,t.status task_status
              FROM p_briefing_ledger l JOIN p_tasks t ON t.id=l.run_id AND t.tenant=l.tenant
              WHERE l.tenant=? AND l.status=?''', (tenant, LEDGER_SUBMITTED))]
        for row in pending:
            verdict = _TASK_TO_LEDGER.get(row['task_status'])
            if not verdict:
                continue
            with e.tx() as c:
                c.execute('''UPDATE p_briefing_ledger SET status=?,updated=?
                  WHERE tenant=? AND schedule=? AND day=? AND status=?''',
                          (verdict, e.clock(), tenant, row['schedule'], row['day'], LEDGER_SUBMITTED))
            e.audit_write(tenant, 'briefing.' + ('delivered' if verdict == LEDGER_SENT else verdict),
                          'briefing-loop', {'schedule': row['schedule'], 'day': row['day'],
                                            'task': row['run_id']})

    def _mark(self, tenant, schedule, day, status, **fields):
        e = self.engine
        now = e.clock()
        with e.tx() as c:
            c.execute('''UPDATE p_briefing_ledger
              SET status=?,run_id=?,sections=?,rows=?,digest=?,last_error=?,updated=?
              WHERE tenant=? AND schedule=? AND day=?''',
                      (status, fields.get('run_id', ''), fields.get('sections', 0),
                       fields.get('rows', 0), fields.get('digest', '')[:500],
                       fields.get('error', '')[:200], now, tenant, schedule['id'], day))

    def tick(self, tenant):
        """Advance at most one due schedule. One submission, no blind retries.

        Returns True when a due schedule was advanced. Settling earlier
        submissions is bookkeeping and never counts.
        """
        e = self.engine
        self._settle(tenant)
        with e.read() as c:
            frozen = c.execute('SELECT stopped FROM p_freeze WHERE tenant=?', (tenant,)).fetchone()
            if frozen and frozen['stopped']:
                return False
            due = c.execute('''SELECT * FROM p_briefing WHERE tenant=? AND enabled=1
              AND next_due<=? ORDER BY next_due,id LIMIT 1''', (tenant, e.clock())).fetchone()
        if not due:
            return False
        schedule = dict(due)
        schedule['sections'] = _load(schedule['sections'])

        # Authority and freeze are re-checked every cycle, not only at configure
        # time: revoking the owner or freezing the tenant must stop delivery.
        try:
            with e.read() as c:
                e.require_active(c, tenant)
                e.require_authority(c, tenant, 'cron', schedule['actor'], ('owner',))
        except Forbidden:
            self._disable(tenant, schedule, 'authority_revoked')
            return True

        now = e.clock()
        day = _day_key(now, schedule)
        try:
            with e.tx() as c:
                attempt = self._claim(c, tenant, schedule, day, now)
        except Exception as error:
            e.audit_write(tenant, 'briefing.cycle_failed', 'briefing-loop',
                          {'schedule': schedule['id'], 'error': type(error).__name__})
            self._advance(tenant, schedule, now)
            return True
        if attempt is None:
            # Already handled today. Do not touch next_due: the schedule is still
            # pointing at the same due time, and moving it forward would push
            # tomorrow's digest later each time a worker happens to restart.
            return False

        try:
            text, stats = self._digest(tenant, schedule, now)
        except Forbidden as error:
            # A configuration that can no longer be authorized would otherwise spin
            # every cycle forever. Disable it with an auditable reason.
            self._mark(tenant, schedule, day, LEDGER_FAILED, error=type(error).__name__)
            self._disable(tenant, schedule, 'digest_denied:' + type(error).__name__)
            return True
        except (Conflict, ValueError, LookupError) as error:
            # A provider outage is NOT "nothing to report". Record and reschedule.
            self._mark(tenant, schedule, day, LEDGER_FAILED, error=type(error).__name__)
            e.audit_write(tenant, 'briefing.cycle_failed', 'briefing-loop',
                          {'schedule': schedule['id'], 'error': type(error).__name__})
            self._advance(tenant, schedule, now)
            return True

        try:
            task = self._deliver(tenant, schedule, text, day)
        except Forbidden as error:
            # The engine refused the send: the agent lost the notifier or the
            # recipient left the pack allowlist since configure. A schedule that
            # can no longer be authorised is disabled with a reason, not retried.
            self._mark(tenant, schedule, day, LEDGER_FAILED, sections=stats['sections'],
                       rows=stats['rows'], digest=text, error=type(error).__name__)
            self._disable(tenant, schedule, 'delivery_denied:' + type(error).__name__)
            return True
        except Exception as error:
            # No task exists. The row stays failed, and the next day is a new key,
            # so a stuck submission never produces a duplicate digest.
            self._mark(tenant, schedule, day, LEDGER_FAILED, sections=stats['sections'],
                       rows=stats['rows'], digest=text, error=type(error).__name__)
            self._advance(tenant, schedule, now)
            return True
        # The engine owns the send from here. Whether it went out, waited for an
        # approval, or came back uncertain is read into the ledger by _settle.
        self._mark(tenant, schedule, day, LEDGER_SUBMITTED, run_id=task,
                   sections=stats['sections'], rows=stats['rows'], digest=text)
        e.audit_write(tenant, 'briefing.submitted', 'briefing-loop',
                      {'schedule': schedule['id'], 'day': day, 'task': task,
                       'recipient': schedule['recipient'], 'sections': stats['sections'],
                       'rows': stats['rows'], 'complete': stats['complete']})
        self._advance(tenant, schedule, now)
        return True


def _render_row(section, row):
    """One digest line. Values are bounded and provider text is never re-parsed.

    A conflict observation is a list of ``{source, value}`` dicts, but a malformed
    or provider-shaped entry may be anything at all, so each item is guarded rather
    than assumed to be a dict.
    """
    if section.get('conflicts'):
        parts = []
        for item in (row.get('values') or [])[:3]:
            if isinstance(item, dict):
                parts.append(f"{item.get('source', '')}={item.get('value')!r}")
            else:
                parts.append(repr(item))
        return f"{row.get('id')}: " + (' | '.join(parts) if parts else 'ziddiyat')
    flag = ' (ziddiyat)' if row.get('conflict') else ''
    source = row.get('source', '')
    origin = f' [{source}]' if source else ''
    return f"{row.get('id')} = {row.get('value')!r}{origin}{flag}"


def _day_key(now, schedule):
    """Local calendar day of the schedule, so one briefing is sent per day.

    The key is the *schedule's* local day, not UTC: a manager expects one digest
    per their own working day, and a 08:00 Tashkent send is the previous UTC day.
    """
    offset = schedule.get('timezone_offset_minutes', 0) * 60
    stamp = int(now + offset)
    return str(stamp // DAY_SECONDS)


def _stamp(now):
    seconds = int(now) % DAY_SECONDS
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d} UTC"


def _dump(value):
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value):
    import json
    try:
        parsed = json.loads(value or '[]')
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []
