"""Overdue work escalates to a manager (PRD v0.5, P6).

The workforce block (`workforce.workload`, P9b) already *detects* overdue work and
reports the rows behind the count. What it deliberately does not do is *tell
anyone*: its output is a read result that sits in the caller's hands. A manager
who never asks never learns that five invoices passed their due date, which is
exactly the failure the PRD names —

    "Muddati o'tgan ish eskalatsiyasi | ≥ 95% | P6 talabi"
    "Muddati o'tgan ish menejerga eskalatsiya qilinadi."  — PRD v0.5 §6, §8

This module is that missing half. It is a scheduled coordinator in the same shape
as `reengagement` and `briefing`: an owner configures it, a tick reads a bounded
source, and one delivery goes out per dedup key.

The line it does not cross
--------------------------

**It never evaluates a person, and it never acts on the overdue work.**

Two refusals, both inherited from the product's own scope rather than invented
here:

* No score, rank, rating or comparison of employees is computed. The module
  forwards the *facts* the register already carries — who, what task, what due
  date, what status — and leaves the judgement to the manager. A number that
  ranks people is a number that will be used to sanction them, and this platform
  does not know why a task is late (PRD v0.5 §8).
* The escalation is **read + notify only**. The one write it can perform is
  ``telegram.send`` addressed to an operator-configured recipient — the same
  "tell the manager" channel the briefing uses — so no approval is required, and
  hiding a fact report behind an approval would only train operators to approve
  reflexively. It cannot message a customer, cannot reassign a task, cannot
  close anything. The escalation names a problem; fixing it stays a human act.

Boundaries that make this safe to schedule:

* Every read goes through the ordinary ``workforce.workload`` tool handler, so
  the register declaration, A1 allowlist, agent tool permission and connection
  allowlist all apply unchanged. This module adds no new data path.
* A register that cannot be read is **never** turned into "nothing overdue". The
  cycle is rescheduled and audited, and no delivery is sent.
* A task is escalated **at most once, ever**. Once a delivery succeeds for a work
  item it is never re-sent, however many cycles run — the dedup key is
  ``(tenant, schedule, person, task, due)``, the *work item itself*, not the
  person. A rescheduled task has a new due date and therefore a new key, so it is
  escalated again on its own merits; the thing that must never happen is the same
  late invoice being re-reported daily until the channel is ignored. Only an
  undelivered item (a crashed claim, or a provider failure past its cooldown) is
  ever retried.
* Authority and freeze are re-checked every cycle, not only at configure time.
* The digest is assembled deterministically. Provider text is data, never
  instructions, and it is bounded and quoted as a fact.
"""
from __future__ import annotations

import datetime
import json
import re

from . import cells
from .engine import Conflict, Forbidden, NotFound

# The only write this module may perform: tell the manager. It addresses an
# operator-declared recipient and never a customer, so it needs no approval.
DELIVERY_TOOLS = frozenset({'telegram.send'})

# What a schedule reads. Both sources are *existing read tools*; the coordinator
# introduces no data path of its own, and ``register_escalation_tools`` refuses a
# source whose read tool the agent does not hold. Stage C adds ``telephony`` so an
# overdue outbound-call list reaches the same manager through the same send, the
# same claim and the same digest -- because a second notification route is a
# second thing to forget to disable, and the P6 coordinator already answers the
# one question an escalation asks ("has this already been reported?").
SOURCES = ('workforce', 'telephony')

# Which read tool each source pulls its rows from, and which write each may use.
# ``telephony`` still delivers through ``telegram.send``: the point of stage C is
# that telephony escalates through the *coordinator*, not that it grows a sender.
SOURCE_TOOLS = {
    'workforce': 'workforce.workload',
    'telephony': 'telephony.queue',
}

# Ledger states. Only 'queued' is repeatable; every terminal outcome stays closed
# so a manager is not told the same thing twice inside the cooldown window.
LEDGER_QUEUED = 'queued'
LEDGER_SENT = 'sent'
LEDGER_FAILED = 'failed'

SCHEDULE_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')

MAX_TITLE = 120
MAX_RECIPIENT = 128
MAX_OVERDUE = 50
MAX_DIGEST_LINES = 40

# Bounds on every knob, so a typo becomes a refusal rather than a stampede.
LIMITS = {
    'cooldown_seconds': (300, 2592000),
    'max_per_cycle': (1, 50),
    'interval_seconds': (300, 604800),
    'max_age_days': (1, 365),
}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS p_escalation(
 tenant TEXT NOT NULL, id TEXT NOT NULL, actor TEXT NOT NULL, agent TEXT NOT NULL,
 recipient TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL DEFAULT 'workforce',
 cooldown_seconds INTEGER NOT NULL, max_per_cycle INTEGER NOT NULL,
 interval_seconds INTEGER NOT NULL, max_age_days INTEGER NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, next_due REAL NOT NULL, last_run REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS p_escalation_ledger(
 tenant TEXT NOT NULL, schedule TEXT NOT NULL, person TEXT NOT NULL,
 task TEXT NOT NULL, due TEXT NOT NULL, run_id TEXT NOT NULL DEFAULT '',
 attempt INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
 first_seen REAL NOT NULL, last_attempt REAL NOT NULL, updated REAL NOT NULL,
 last_error TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(tenant,schedule,person,task,due));
CREATE INDEX IF NOT EXISTS p_escalation_due ON p_escalation(tenant,enabled,next_due);
CREATE INDEX IF NOT EXISTS p_escalation_ledger_schedule
 ON p_escalation_ledger(tenant,schedule,status);
'''


class EscalationError(RuntimeError):
    """An escalation request could not be carried out."""


def _bounded(value, name):
    low, high = LIMITS[name]
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer {low}..{high}')
    return value


def _identifier(value, name, maximum=64):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{name} is required and must be at most {maximum} characters')
    return value.strip()


def _day(now):
    return datetime.datetime.fromtimestamp(int(now), datetime.timezone.utc).strftime('%Y-%m-%d')


class EscalationLoop:
    """Scheduled coordinator. Reads overdue work and notifies a manager once per key."""

    def __init__(self, engine, workforce=None):
        self.engine = engine
        self._workforce = workforce
        # Facts a telephony read carries that the item list itself does not: how
        # many rows were refused, how many exceeded the declared pace, and whether
        # the read was truncated. Reset per read so one cycle's counts can never be
        # reported against another cycle's items.
        self._telephony_meta = {}

    @property
    def workforce(self):
        if self._workforce is None:
            from . import workforce as module
            self._workforce = module
        return self._workforce

    # ---------------------------------------------------------------- policy

    def configure(self, tenant, schedule_id, agent, recipient, actor, *, title='',
                  cooldown_seconds=86400, max_per_cycle=10, interval_seconds=3600,
                  max_age_days=30, enabled=True, source='workforce'):
        """Owner-only. Every knob is validated before it can affect a cycle.

        ``recipient`` is operator configuration, exactly like the briefing's — the
        model never sees it and can never redirect an escalation to another chat.

        ``source`` names which read tool the schedule escalates from. It defaults
        to ``workforce`` so every existing schedule keeps its meaning, and the
        agent must hold the corresponding read tool: an escalation whose source
        the agent cannot read would fail on every cycle with an opaque Forbidden,
        which is exactly the class of configuration error this method validates
        out of existence.
        """
        schedule_id = _identifier(schedule_id, 'schedule id', 64)
        if not SCHEDULE_ID_RE.match(schedule_id):
            raise ValueError('schedule id must be lowercase letters, digits, dot, dash or underscore')
        agent = _identifier(agent, 'agent', 128)
        recipient = _identifier(recipient, 'recipient', MAX_RECIPIENT)
        actor = _identifier(actor, 'actor', 128)
        if not isinstance(source, str) or source.strip().casefold() not in SOURCES:
            raise ValueError(f'source must be one of {list(SOURCES)}')
        source = source.strip().casefold()
        if not isinstance(title, str) or len(title) > MAX_TITLE:
            raise ValueError(f'title must be a string of at most {MAX_TITLE} characters')
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        settings = {
            'cooldown_seconds': _bounded(cooldown_seconds, 'cooldown_seconds'),
            'max_per_cycle': _bounded(max_per_cycle, 'max_per_cycle'),
            'interval_seconds': _bounded(interval_seconds, 'interval_seconds'),
            'max_age_days': _bounded(max_age_days, 'max_age_days'),
        }
        # The agent policy must exist before a schedule can point at it, otherwise
        # every cycle would fail later with an opaque "policy unavailable".
        policy = self.engine.policy(tenant, agent)
        if policy.get('ladder') not in {'human_led', 'human_assisted', 'autonomous'}:
            raise Forbidden('Agent policy unavailable for escalation')
        # The agent must actually hold the read tool it will be asked to run, or
        # the first cycle would fail with a Forbidden the operator did not expect.
        read_tool = SOURCE_TOOLS[source]
        if read_tool not in (policy.get('tools') or []):
            raise Forbidden(f'Escalation agent must hold {read_tool}')
        if 'telegram.send' not in (policy.get('tools') or []):
            raise Forbidden('Escalation agent must hold telegram.send to notify a manager')
        if recipient not in (policy.get('allowed_recipients') or []):
            raise Forbidden('Escalation recipient must be pack-allowlisted for the agent')
        e = self.engine
        with e.tx() as c:
            e.require_active(c, tenant)
            e.require_authority(c, tenant, 'cron', actor, ('owner',))
            now = e.clock()
            c.execute('''INSERT INTO p_escalation
              (tenant,id,actor,agent,recipient,title,source,cooldown_seconds,max_per_cycle,
               interval_seconds,max_age_days,enabled,next_due,last_run)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)
              ON CONFLICT(tenant,id) DO UPDATE SET
               actor=excluded.actor, agent=excluded.agent, recipient=excluded.recipient,
               title=excluded.title, source=excluded.source,
               cooldown_seconds=excluded.cooldown_seconds,
               max_per_cycle=excluded.max_per_cycle, interval_seconds=excluded.interval_seconds,
               max_age_days=excluded.max_age_days, enabled=excluded.enabled,
               next_due=excluded.next_due''',
                      (tenant, schedule_id, actor, agent, recipient, title, source,
                       settings['cooldown_seconds'], settings['max_per_cycle'],
                       settings['interval_seconds'], settings['max_age_days'],
                       int(enabled), now + settings['interval_seconds']))
            e.audit(c, tenant, '', 'escalation.configured', actor,
                    {'schedule': schedule_id, 'agent': agent, 'recipient': recipient,
                     'source': source, 'enabled': enabled})
        return self.schedule(tenant, schedule_id)

    def schedule(self, tenant, schedule_id):
        with self.engine.read() as c:
            row = c.execute('SELECT * FROM p_escalation WHERE tenant=? AND id=?',
                            (tenant, schedule_id)).fetchone()
        if not row:
            raise NotFound('Escalation schedule not found')
        return dict(row)

    def schedules(self, tenant):
        with self.engine.read() as c:
            return [dict(row) for row in c.execute(
                'SELECT * FROM p_escalation WHERE tenant=? ORDER BY id', (tenant,))]

    def ledger(self, tenant, schedule_id, limit=100, *, with_total=False):
        """Recent ledger rows for one schedule, newest first.

        **A bare list cannot tell a full page from the whole ledger.** The note here
        used to say "no runtime path calls this, so it is a trap rather than a live
        defect" -- which was true of the runtime and false of the product:
        ``GET /{tenant}/escalation/{schedule}/ledger`` returns these rows as
        ``entries``. "How many escalations were raised" therefore answered with the
        page size, exactly as ``erp.posting_status`` once did.

        ``with_total=True`` counts the table in the same read and returns
        ``(rows, total, truncated)``, as ``erp.ledger_page`` does. ``truncated``
        compares against the population rather than the limit, so a ledger holding
        exactly ``limit`` rows is reported as complete.
        """
        limit = min(max(1, int(limit)), 500)
        with self.engine.read() as c:
            rows = [dict(row) for row in c.execute(
                '''SELECT * FROM p_escalation_ledger WHERE tenant=? AND schedule=?
                   ORDER BY last_attempt DESC LIMIT ?''', (tenant, schedule_id, limit))]
            if not with_total:
                return rows
            total = c.execute(
                'SELECT count(*) n FROM p_escalation_ledger WHERE tenant=? AND schedule=?',
                (tenant, schedule_id)).fetchone()['n']
        return rows, total, total > len(rows)

    def disable(self, tenant, schedule_id, actor, reason='operator_disabled'):
        """Owner-only. Stop a schedule with an auditable reason.

        An escalation that can no longer be authorized must be stoppable, or it
        would spin every cycle forever; the reason is recorded so a manager can
        tell a deliberate stop from a broken one.
        """
        actor = _identifier(actor, 'actor', 128)
        e = self.engine
        with e.tx() as c:
            e.require_authority(c, tenant, 'cron', actor, ('owner',))
            row = c.execute('SELECT id FROM p_escalation WHERE tenant=? AND id=?',
                            (tenant, schedule_id)).fetchone()
            if not row:
                raise NotFound('Escalation schedule not found')
            c.execute('UPDATE p_escalation SET enabled=0 WHERE tenant=? AND id=?',
                      (tenant, schedule_id))
            e.audit(c, tenant, '', 'escalation.disabled', actor,
                    {'schedule': schedule_id, 'reason': reason})
        return self.schedule(tenant, schedule_id)

    # ------------------------------------------------------------- dedup claim

    def _claim(self, c, tenant, schedule, person, task, due, now):
        """Atomically claim one escalation for one overdue work item.

        Returns the attempt number, or ``None`` when the item must not be
        escalated again.

        ``sent`` is **terminal for this key, permanently** — not merely for the
        cooldown window. The dedup key already includes the due date, so a task
        that is rescheduled is a *different* key and gets its own escalation; the
        only way this key can legitimately recur is if the operator moves the due
        date back to the same day, which is the same fact and should not be
        re-reported. Re-sending on a timer would mean a manager is told
        "invoice #12 is late" every single day for as long as it stays late, which
        is how an alert channel gets muted.

        ``failed`` is retryable after the cooldown, because a provider outage is
        transient and the manager genuinely has not been told. A ``queued`` row
        (a worker crashed between claim and mark) is retryable immediately, since
        nothing was delivered.

        The key is the work item, not the person, so a manager is told "this
        invoice is late" rather than "this person is late".
        """
        row = c.execute('''SELECT attempt,status,last_attempt FROM p_escalation_ledger
          WHERE tenant=? AND schedule=? AND person=? AND task=? AND due=?''',
                        (tenant, schedule['id'], person, task, due)).fetchone()
        if row is None:
            c.execute('''INSERT INTO p_escalation_ledger
              (tenant,schedule,person,task,due,run_id,attempt,status,first_seen,
               last_attempt,updated,last_error)
              VALUES(?,?,?,?,?,'',1,?,?,?,?,'')''',
                      (tenant, schedule['id'], person, task, due, LEDGER_QUEUED,
                       now, now, now))
            return 1
        if row['status'] == LEDGER_SENT:
            # Delivered once. The work item is the key, and this item has already
            # been reported; re-reporting it on a timer is the failure mode this
            # module exists to avoid.
            return None
        if row['status'] == LEDGER_FAILED:
            # Retryable, but only after the cooldown, so a provider outage does not
            # become a message storm when it recovers.
            if row['last_attempt'] + schedule['cooldown_seconds'] > now:
                return None
        # Compare-and-set on last_attempt: a second worker reading the same row
        # updates zero rows and therefore must not notify.
        updated = c.execute('''UPDATE p_escalation_ledger
          SET attempt=attempt+1,status=?,last_attempt=?,updated=?,run_id='',last_error=''
          WHERE tenant=? AND schedule=? AND person=? AND task=? AND due=? AND last_attempt=?''',
                            (LEDGER_QUEUED, now, now, tenant, schedule['id'], person,
                             task, due, row['last_attempt']))
        return row['attempt'] + 1 if updated.rowcount == 1 else None

    def _mark(self, tenant, schedule, person, task, due, status, run_id='', error=''):
        e = self.engine
        now = e.clock()
        with e.tx() as c:
            c.execute('''UPDATE p_escalation_ledger SET status=?,run_id=?,last_error=?,updated=?
              WHERE tenant=? AND schedule=? AND person=? AND task=? AND due=?''',
                      (status, run_id, error[:200], now, tenant, schedule['id'],
                       person, task, due))

    def _advance(self, tenant, schedule, now, *, enabled=None):
        e = self.engine
        with e.tx() as c:
            if enabled is None:
                c.execute('UPDATE p_escalation SET next_due=?,last_run=? WHERE tenant=? AND id=?',
                          (now + schedule['interval_seconds'], now, tenant, schedule['id']))
            else:
                c.execute('''UPDATE p_escalation SET next_due=?,last_run=?,enabled=?
                  WHERE tenant=? AND id=?''',
                          (now + schedule['interval_seconds'], now, int(enabled), tenant,
                           schedule['id']))

    def _disable(self, tenant, schedule, reason):
        e = self.engine
        with e.tx() as c:
            c.execute('UPDATE p_escalation SET enabled=0 WHERE tenant=? AND id=?',
                      (tenant, schedule['id']))
            e.audit(c, tenant, '', 'escalation.disabled', 'escalation-loop',
                    {'schedule': schedule['id'], 'reason': reason})

    # ------------------------------------------------------------------ read

    def _read_overdue(self, tenant, schedule):
        """Read overdue work through the ordinary read handler for this schedule's source.

        Going through the registered tool means agent tool permission, connection
        allowlist and the register declaration all apply exactly as for a manual
        call, and a provider failure surfaces as the handler's own exception.

        ``source`` selects WHICH ordinary handler, and there are exactly two. Both
        are read tools the agent already holds and both were validated at configure
        time, so this method adds no authority -- it only chooses a reader. A
        telephony-sourced schedule still notifies through ``telegram.send``; stage C
        gives telephony a *source*, not a sender.
        """
        source = str(schedule.get('source') or 'workforce')
        read_tool = SOURCE_TOOLS.get(source)
        if read_tool is None:
            raise Conflict(f'Escalation source {source!r} has no reader')
        self._telephony_meta = {}
        tool = self.engine.registry.get(read_tool)
        if source == 'telephony':
            args = {'limit': schedule['max_per_cycle']}
        else:
            args = {'limit': MAX_OVERDUE, 'overdue_limit': schedule['max_per_cycle']}
        tool.validate(args)
        result = tool.handler(self.engine, tenant, schedule['agent'], args,
                              'escalation:' + schedule['id'])
        if source == 'telephony':
            return self._telephony_items(result)
        rows = result.get('overdue')
        if not isinstance(rows, list):
            raise Conflict('Workload feed returned an unexpected shape')
        return rows

    def _telephony_items(self, result):
        """Normalise a telephony queue into the coordinator's item shape.

        Only rows whose consent check **passed** become escalation items, and the
        ones that did not are counted rather than dropped: an outbound queue that
        is quietly full of un-consented numbers is a *compliance* fact the manager
        needs, not a rounding error. The number is the item identity, so the same
        number is never reported twice, and ``over_capacity`` is carried into the
        digest for the same reason the queue itself reports it — a manager who is
        told "12 calls are ready" when only 2 may lawfully be placed has been
        misinformed.

        No person is named: the queue carries numbers and purposes, never an
        operator, so a telephony escalation can never become a performance report.
        """
        items = result.get('items')
        if not isinstance(items, list):
            raise Conflict('Telephony queue returned an unexpected shape')
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            number = str(item.get('number') or '')[:20]
            if not number or not item.get('consented'):
                continue
            rows.append({
                'person': number,
                'name': number,
                'task': str(item.get('purpose') or '')[:40],
                'due': str(item.get('due_day') or '')[:40],
                'status': 'callable',
            })
        # The counts the manager needs and the items do not carry. Attached to the
        # last row so they travel with the same read that produced the items, and
        # so a queue with zero callable rows still reports its blocked/capacity
        # facts instead of looking like an empty register.
        self._telephony_meta = {
            'blocked_count': int(result.get('blocked_count') or 0),
            'over_capacity': int(result.get('over_capacity') or 0),
            'capacity': int(result.get('capacity') or 0),
            'callable_count': int(result.get('callable_count') or 0),
            'truncated': bool(result.get('truncated')),
        }
        return rows

    def _is_stale(self, due, now, max_age_days):
        """Whether an overdue item is too old to keep escalating at all.

        A task that has been late for a year is not an escalation any more; it is
        a data-quality problem, and re-notifying it forever would train the
        manager to ignore the channel. Only an ISO ``YYYY-MM-DD`` due date can be
        aged; an unparsable one is never dropped, because dropping it would hide
        the row the operator most needs to see.
        """
        if not due:
            return False
        text = str(due).strip()
        # An ASCII date only. ``\d`` would also match Devanagari, Arabic-Indic and
        # fullwidth digits, and ``int()`` normalises them, so a due date written
        # that way would age out and the item would be dropped -- exactly what this
        # method's contract forbids. A non-ASCII date is "unparsable", and an
        # unparsable date is never treated as stale. See ``platform_runtime.cells``.
        if not cells.is_ascii_digit_run(text[:10].replace('-', '')):
            return False
        match = re.match(r'^([0-9]{4})-([0-9]{2})-([0-9]{2})', text)
        if not match:
            return False
        try:
            parsed = datetime.datetime(int(match.group(1)), int(match.group(2)),
                                       int(match.group(3)), tzinfo=datetime.timezone.utc)
        except ValueError:
            return False
        return (now - parsed.timestamp()) > max_age_days * 86400

    def _overdue_items(self, tenant, schedule, now):
        """Read overdue work and normalise it to the escalation item shape.

        One definition, used by both the coordinator and the preview tool, so the
        "what would be escalated" answer and the "what was escalated" answer can
        never drift apart. Items without a work identity are skipped: there is
        nothing to dedup on and a blank escalation is only noise.

        Returns ``(items, stale_count)``. A stale item is counted and carried into
        the digest of the next real escalation rather than dropped in silence, so a
        permanently-late task leaves a trace instead of vanishing. It is not sent on
        its own: a channel that says "1 item is very old" every hour is a channel the
        manager stops reading. Only an ISO due date can be aged; an unparsable one is
        never treated as stale, because that would hide the row most needing a look.

        The staleness rule applies to ``workforce`` only. A telephony queue item is
        a number that may be called, not work that went late: ageing it out because
        its date is old would silently retire a consented number, and "old" is not a
        reason a lawful call stops being lawful.
        """
        rows = self._read_overdue(tenant, schedule)
        source = str(schedule.get('source') or 'workforce')
        items, stale = [], 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            person = str(row.get('person') or '')[:64]
            task = str(row.get('task') or '')[:120]
            due = str(row.get('due') or '')[:40]
            if not person or not task:
                continue
            if source == 'workforce' and self._is_stale(due, now, schedule['max_age_days']):
                stale += 1
                continue
            items.append({'person': person, 'name': str(row.get('name') or '')[:120],
                          'task': task, 'due': due,
                          'status': str(row.get('status') or '')[:40]})
        return items, stale

    # ---------------------------------------------------------------- digest

    def _digest(self, tenant, schedule, items, now, *, stale=0):
        """Assemble the escalation message. Deterministic and bounded.

        The model is not involved: an escalation is a report of facts, and letting
        a model rephrase "3 invoices are past due" would add a step that can only
        lose accuracy. Every line names the person, the task and the due date, so
        the manager can look it up instead of trusting a count.
        """
        title = schedule.get('title') or f"Kechikkan ish eskalatsiyasi — {schedule['id']}"
        lines = [title, '']
        shown = items[:MAX_DIGEST_LINES]
        for index, item in enumerate(shown, 1):
            name = item.get('name') or item.get('person') or '-'
            task = item.get('task') or '-'
            due = item.get('due') or '-'
            status = item.get('status') or '-'
            lines.append(f'{index}. {name} — {task} (muddat: {due}, holat: {status})')
        if len(items) > len(shown):
            lines.append(f'… va yana {len(items) - len(shown)} ta yozuv')
        lines.append('')
        lines.append(f"Jami kechikkan: {len(items)}")
        # A telephony-sourced escalation reports the two counts its item list
        # cannot carry: the numbers the consent gate refused, and the callable rows
        # the declared pace cannot lawfully absorb. Without them a manager reads
        # "12 calls are ready" and never learns that 10 of the 12 are refused or
        # that only 2 may be placed -- which is the same overstatement the queue
        # itself refuses to make.
        meta = self._telephony_meta
        if meta:
            if meta.get('blocked_count'):
                lines.append(f"Rozilik darvozasi rad etgan raqamlar: "
                             f"{meta['blocked_count']} — qo‘ng‘iroq qilinmaydi")
            if meta.get('over_capacity'):
                lines.append(f"E’lon qilingan sur’atdan ortiq (bajarilmaydi): "
                             f"{meta['over_capacity']} — sig‘im {meta.get('capacity', 0)}")
            if meta.get('truncated'):
                lines.append('Ro‘yxat to‘liq emas (truncated) — bu qism, hammasi emas')
        if stale:
            # Carried on the next real escalation instead of its own message: a
            # very old task leaves a trace in a report the manager is already
            # reading, and does not become an hourly "1 item is old" that trains
            # the channel to be ignored.
            lines.append(f"Eslatish muddati o‘tgan (juda eski) yozuvlar: {stale} "
                         f"— bu yozuv yana alohida yuborilmaydi")
        lines.append(f"Tayyorlandi: {_day(now)}")
        lines.append('')
        lines.append('Bu faktlar ro‘yxati — platforma xodimni baholamaydi va '
                     'jazolash qarori qabul qilmaydi. Qaror menejerniki.')
        return '\n'.join(lines)[:4000]

    def _deliver(self, tenant, schedule, text):
        """Send the escalation through the ordinary write tool.

        The recipient is operator configuration and the agent must hold it in its
        ``allowed_recipients`` (enforced at configure time and again by the engine
        at submit), so the escalation can only ever reach the manager.
        """
        tool = self.engine.registry.get('telegram.send')
        args = {'conversation_id': schedule['recipient'], 'text': text}
        tool.validate(args)
        return tool.handler(self.engine, tenant, schedule['agent'], args,
                            'escalation:' + schedule['id'])

    # ------------------------------------------------------------------ cycle

    def tick(self, tenant):
        """Advance at most one due schedule. One read, one delivery, no blind retries."""
        e = self.engine
        with e.read() as c:
            frozen = c.execute('SELECT stopped FROM p_freeze WHERE tenant=?', (tenant,)).fetchone()
            if frozen and frozen['stopped']:
                return False
            due = c.execute('''SELECT * FROM p_escalation WHERE tenant=? AND enabled=1
              AND next_due<=? ORDER BY next_due,id LIMIT 1''', (tenant, e.clock())).fetchone()
        if not due:
            return False
        schedule = dict(due)

        # Authority and freeze are re-checked every cycle, not only at configure
        # time: revoking the owner or freezing the tenant must stop escalation.
        try:
            with e.read() as c:
                e.require_active(c, tenant)
                e.require_authority(c, tenant, 'cron', schedule['actor'], ('owner',))
        except Forbidden:
            self._disable(tenant, schedule, 'authority_revoked')
            return True

        try:
            items, stale = self._overdue_items(tenant, schedule, e.clock())
        except Forbidden as error:
            # A configuration that can no longer be authorized would otherwise spin
            # every cycle forever. Disable it with an auditable reason.
            self._disable(tenant, schedule, 'read_denied:' + type(error).__name__)
            return True
        except Exception as error:
            # A provider outage is NOT "nothing overdue", and it is not a reason to
            # retire the schedule either: a Sheets 503 or a dropped connection is
            # transient, and disabling on the first bad minute would turn a blip
            # into a permanent silence. Every other exception here is a read that
            # did not happen, so the honest response is the same for all of them —
            # send nothing, audit the error class, and retry on the next interval.
            #
            # Only the exception *class name* is recorded. A provider message can
            # echo a range, a spreadsheet id or a token, and the register idiom in
            # this project is that provider text is data, never provenance.
            e.audit_write(tenant, 'escalation.cycle_failed', 'escalation-loop',
                          {'schedule': schedule['id'], 'error': type(error).__name__})
            self._advance(tenant, schedule, e.clock())
            return True

        now = e.clock()
        claimed = []
        for item in items:
            try:
                with e.tx() as c:
                    attempt = self._claim(c, tenant, schedule, item['person'],
                                          item['task'], item['due'], now)
            except Exception:
                attempt = None
            if attempt is None:
                continue
            claimed.append(item)
            if len(claimed) >= schedule['max_per_cycle']:
                break

        if not claimed:
            # Nothing new to report. Still reschedule: the schedule is alive and
            # quiet, which is different from broken.
            e.audit_write(tenant, 'escalation.cycle', 'escalation-loop',
                          {'schedule': schedule['id'], 'seen': len(items), 'sent': 0,
                           'stale': stale})
            self._advance(tenant, schedule, now)
            return True

        text = self._digest(tenant, schedule, claimed, now, stale=stale)
        try:
            result = self._deliver(tenant, schedule, text)
        except Exception as error:
            # No delivery happened. The rows stay failed and the cooldown gates the
            # next attempt, so a stuck send never produces a duplicate escalation.
            for item in claimed:
                self._mark(tenant, schedule, item['person'], item['task'], item['due'],
                           LEDGER_FAILED, error=type(error).__name__)
            e.audit_write(tenant, 'escalation.delivery_failed', 'escalation-loop',
                          {'schedule': schedule['id'], 'error': type(error).__name__})
            self._advance(tenant, schedule, now)
            return True

        run_id = ''
        if isinstance(result, dict):
            run_id = str(result.get('external_id') or result.get('id') or '')[:64]
        for item in claimed:
            self._mark(tenant, schedule, item['person'], item['task'], item['due'],
                       LEDGER_SENT, run_id=run_id)
        e.audit_write(tenant, 'escalation.delivered', 'escalation-loop',
                      {'schedule': schedule['id'], 'recipient': schedule['recipient'],
                       'items': len(claimed), 'seen': len(items), 'stale': stale})
        self._advance(tenant, schedule, now)
        return True


# --------------------------------------------------------------- tool handlers


def _preview_tool(engine, tenant, agent, args, step):
    """Read-only tool: what an escalation *would* report, without sending.

    This tool deliberately **does not notify**. It runs the same read and the same
    staleness filter the coordinator uses — through ``_overdue_items``, so the
    preview and the real delivery can never disagree about what is overdue — and
    returns the items an operator would receive. Sending is a separate, audited
    action taken by the coordinator, not something a model can trigger by naming
    a tool.
    """
    loop = EscalationLoop(engine)
    source = str(args.get('source') or 'workforce').strip().casefold()
    if source not in SOURCES:
        raise Forbidden(f'Unknown escalation source {source!r}; must be one of {list(SOURCES)}')
    schedule = {'id': 'preview', 'agent': agent, 'recipient': args.get('recipient', ''),
                'max_per_cycle': args.get('limit', 10),
                'max_age_days': args.get('max_age_days', 30),
                'source': source}
    items, stale = loop._overdue_items(tenant, schedule, engine.clock())
    return {'tenant': tenant, 'source': source, 'overdue_count': len(items),
            'stale_count': stale,
            'items': items[:MAX_OVERDUE],
            'note': 'Bu faqat ko‘rish — hech qanday xabar yuborilmadi.'}


def _schedules_tool(engine, tenant, agent, args, step):
    """Read-only tool: the declared escalation schedules, without any routing."""
    return {'tenant': tenant, 'schedules': EscalationLoop(engine).schedules(tenant)}


def register_escalation_tools(registry):
    """Two read tools. Delivering an escalation is a coordinator action.

    That split is the point: if notifying were a tool, any agent holding it could
    manufacture messages, and the dedup/cooldown cap would be enforced only by the
    prompt. Keeping delivery in the coordinator means it is enforced by code.
    """
    from .tools import Tool, obj, string
    for tool in (
        Tool('escalation.preview', 'read', obj({
            'recipient': string(MAX_RECIPIENT),
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_OVERDUE},
            'max_age_days': {'type': 'integer', 'minimum': 1, 'maximum': 365},
            'source': string(32),
        }, []), _preview_tool),
        Tool('escalation.schedules', 'read', obj({}), _schedules_tool),
    ):
        if tool.name in registry.items:
            continue
        registry.add(tool)
