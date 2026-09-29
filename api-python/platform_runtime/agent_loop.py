"""Persisted, bounded result-fed planning. Source-only preview, not acceptance.

One planner reservation per turn, then one ordinary Engine task. External writes
still require per-action approval. Planner calls never run inside SQL transactions.
Expired planner leases are escalated, not automatically retried. SQL state is
sole authority; user/model/tool text cannot modify permissions or budgets.
"""
import json
import re
import uuid

from .engine import (OUTBOUND_TOOLS, Conflict, Forbidden, NotFound, RateLimited,
                     digest, encode)

ACTIVE = ('pending', 'planning', 'waiting_task')
# The set is used in three places: `_reserve` compares a row's status to
# it, and `create` and `tick` both need it inside SQL. Spelling it out in
# the SQL was a second and third statement of one fact, and the fazza-28
# audit measured what that costs: changing the `create` copy so it dropped
# 'waiting_task' left the whole suite green, while the same change in
# `tick` was caught. The queue ceiling would simply have stopped counting
# waiting runs. Both sites now build their placeholders from ACTIVE.
_ACTIVE_PLACEHOLDERS = ','.join('?' * len(ACTIVE))
TERMINAL = ('succeeded', 'needs_input', 'escalated', 'uncertain', 'cancelled')
MAX_STEPS = 12
MAX_SECONDS = 86400
MAX_OBSERVATION_BYTES = 12000
MAX_HISTORY_BYTES = 48000
# A decision that arrives after its lease is discarded, so the lease must outlive
# the worst-case planner call: model_transport.MODEL_CALL_DEADLINE_SECONDS (150 s,
# every attempt and backoff wait included) plus 30 s for connecting and the
# commit. At 60 s a call delayed by a legitimate retry would be discarded after
# it was paid for. A run's own wall deadline still caps the lease (see _reserve).
PLANNER_LEASE_SECONDS = 180
# The channel of a dashboard run. Any other channel is an inbound conversation
# turn: the run's authority is the channel sender's, its tasks are submitted under
# that channel, and it may never send -- the reply is delivered by
# conversation.ConversationTurns after the run ends, bound to the verified event.
DASHBOARD_CHANNEL = 'agent'
MAX_CHANNEL_CHARS = 32
# A planner that opts in (``planner.supports_repair(tenant)``; the `tools`
# protocol) gets ONE more call when its decision is rejected for a shape or
# validation reason -- bad arguments, unknown evidence, a repeated call --
# with the rejection fed back. The extra call is reserved like any other (one
# more `calls`, a fresh lease) and fenced again before dispatch; a second
# rejection escalates exactly as before. Policy refusals are never repaired.
MAX_DECISION_REPAIRS = 1
# What the model is told about a rejection is platform text only: an exception
# message outside this charset (quotes, braces, newlines -- anything that could
# echo arguments or configuration) is replaced by a fixed sentence.
REPAIR_REASON = re.compile(r"[A-Za-z0-9 .,:;()'_-]{1,200}")
GENERIC_REPAIR_REASON = 'Decision failed platform validation'


class LoopDecisionError(ValueError):
    pass


class ObservationUnavailable(LoopDecisionError):
    """The run's own history cannot be read. A state fault, never the model's."""


class AgentLoop:
    def __init__(self, engine):
        self.engine = engine

    def create(self, tenant, key, agent, text, actor, *, max_steps=6, max_seconds=1800,
               channel=DASHBOARD_CHANNEL):
        for value, limit in ((tenant, 64), (key, 256), (agent, 128), (actor, 128),
                             (channel, MAX_CHANNEL_CHARS)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError('Invalid run identity')
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Run input must contain 1..4000 characters')
        if type(max_steps) is not int or not 1 <= max_steps <= MAX_STEPS:
            raise ValueError('Invalid step budget')
        if type(max_seconds) is not int or not 60 <= max_seconds <= MAX_SECONDS:
            raise ValueError('Invalid time budget')
        e = self.engine
        policy = e.policy(tenant, agent)
        if policy.get('ladder') not in {'human_led', 'human_assisted', 'autonomous'}:
            raise Forbidden('Agent policy unavailable')
        request = {'agent': agent, 'input': text, 'actor': actor,
                   'max_steps': max_steps, 'max_seconds': max_seconds}
        if channel != DASHBOARD_CHANNEL:
            # Added only off the dashboard, so every existing run keeps its fingerprint.
            request['channel'] = channel
        fingerprint = digest(request)
        with e.tx() as c:
            e.require_authority(c, tenant, channel, actor)
            old = c.execute('SELECT id,fingerprint FROM p_agent_runs WHERE tenant=? AND request_key=?',
                            (tenant, key)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise Conflict('Run key reused with different input')
                return old['id']
            e.require_active(c, tenant)
            pending = c.execute(
                'SELECT count(*) n FROM p_agent_runs WHERE tenant=? AND status IN ('
                + _ACTIVE_PLACEHOLDERS + ')',
                (tenant, *ACTIVE)).fetchone()['n']
            if pending >= 100:
                raise RateLimited('Agent run queue full')
            now = e.clock()
            run_id = uuid.uuid4().hex
            c.execute('''INSERT INTO p_agent_runs
              (id,tenant,request_key,fingerprint,agent,actor,input,status,created,updated,deadline,max_steps,max_calls,channel)
              VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?,?,?)''',
                      (run_id, tenant, key, fingerprint, agent, actor, text, now, now,
                       now + max_seconds, max_steps, max_steps + 1, channel))
            e.audit(c, tenant, '', 'agent_run.created', actor,
                    {'run': run_id, 'max_steps': max_steps, 'max_seconds': max_seconds})
            return run_id

    def _row(self, c, tenant, run_id):
        row = c.execute('SELECT * FROM p_agent_runs WHERE tenant=? AND id=?', (tenant, run_id)).fetchone()
        if not row:
            raise NotFound('Agent run not found')
        return dict(row)

    def get(self, tenant, run_id):
        with self.engine.read() as c:
            row = self._row(c, tenant, run_id)
            for key in ('claim', 'lease', 'fingerprint'):
                row.pop(key, None)
            row['evidence_ids'] = json.loads(row['evidence_ids'])
            row['turns'] = [dict(item) for item in c.execute('''
              SELECT l.position,l.task,t.status FROM p_agent_turns l
              JOIN p_tasks t ON t.id=l.task AND t.tenant=l.tenant
              WHERE l.tenant=? AND l.run_id=? ORDER BY l.position''', (tenant, run_id))]
            row['answer_kind'] = 'model_generated_with_source_references'
            row['semantic_fact_check'] = 'not_performed'
            return row

    def list(self, tenant):
        with self.engine.read() as c:
            return [dict(row) for row in c.execute('''
              SELECT id,agent,actor,status,created,updated,deadline,steps,max_steps,calls,max_calls,error
              FROM p_agent_runs WHERE tenant=? ORDER BY created DESC,id LIMIT 100''', (tenant,))]

    def _stop(self, c, row, status, reason, actor='agent-loop'):
        """Internal retirement, including when creator authority has been revoked."""
        e = self.engine
        task_id = row['current_task']
        if task_id:
            linked = c.execute('''SELECT t.status FROM p_agent_turns l
              JOIN p_tasks t ON t.id=l.task AND t.tenant=l.tenant
              WHERE l.tenant=? AND l.run_id=? AND l.task=?''',
                               (row['tenant'], row['id'], task_id)).fetchone()
            if not linked:
                status, reason = 'escalated', 'run_task_link_invalid'
            elif linked['status'] in {'queued', 'waiting_approval', 'running'}:
                c.execute("""UPDATE p_steps SET
                  status=CASE WHEN status='running' THEN 'uncertain' ELSE 'cancelled' END,
                  claim='',lease=0,error=? WHERE tenant=? AND task=?
                  AND status IN ('queued','waiting_approval','running')""",
                          (reason, row['tenant'], task_id))
                e._refresh(c, row['tenant'], task_id)
                current = c.execute('SELECT status FROM p_tasks WHERE tenant=? AND id=?',
                                    (row['tenant'], task_id)).fetchone()
                if current['status'] == 'uncertain':
                    status = 'uncertain'
            elif linked['status'] == 'uncertain':
                status = 'uncertain'
            if linked:
                # Also retire unused approvals when a worker has already failed
                # or cancelled the task before the coordinator observes it.
                c.execute('''UPDATE p_approvals SET status='rejected',actor=?,decided=?
                  WHERE tenant=? AND status IN ('pending','approved') AND step IN
                  (SELECT id FROM p_steps WHERE tenant=? AND task=?)''',
                          (actor, e.clock(), row['tenant'], row['tenant'], task_id))
        c.execute("""UPDATE p_agent_runs SET status=?,error=?,claim='',lease=0,updated=?
          WHERE tenant=? AND id=?""", (status, reason, e.clock(), row['tenant'], row['id']))
        e.audit(c, row['tenant'], task_id, 'agent_run.' + status, actor,
                {'run': row['id'], 'reason': reason})

    def cancel(self, tenant, run_id, actor):
        with self.engine.tx() as c:
            self.engine.require_authority(c, tenant, 'web', actor)
            row = self._row(c, tenant, run_id)
            if row['status'] in TERMINAL:
                return row['status']
            self._stop(c, row, 'cancelled', 'operator_cancelled', actor)
            return self._row(c, tenant, run_id)['status']

    def _observations(self, c, row):
        rows = c.execute('''SELECT l.position,l.task,t.status task_status,
          s.id,s.tool,s.args,s.result,s.status FROM p_agent_turns l
          JOIN p_tasks t ON t.id=l.task AND t.tenant=l.tenant
          JOIN p_steps s ON s.task=t.id AND s.tenant=t.tenant AND s.position=0
          WHERE l.tenant=? AND l.run_id=? ORDER BY l.position''',
                         (row['tenant'], row['id'])).fetchall()
        if len(rows) != row['steps']:
            raise ObservationUnavailable('Observation history incomplete')
        observations = []
        for item in rows:
            if item['status'] != 'succeeded' or item['task_status'] != 'succeeded':
                raise ObservationUnavailable('Only successful steps are observations')
            observation = {'evidence_id': 'step:' + item['id'], 'task_id': item['task'],
                           'tool': item['tool'], 'arguments': json.loads(item['args']),
                           'result': json.loads(item['result'])}
            if len(encode(observation).encode('utf-8')) > MAX_OBSERVATION_BYTES:
                raise ObservationUnavailable('Observation exceeds context bound')
            observations.append(observation)
        if len(encode(observations).encode('utf-8')) > MAX_HISTORY_BYTES:
            raise ObservationUnavailable('Observation history exceeds context bound')
        return observations

    @staticmethod
    def _context(row, observations):
        """The planner's context for the call being reserved; `row` is read before its `calls+1`."""
        return {'run_id': row['id'], 'agent': row['agent'], 'input': row['input'],
                'channel': row['channel'],
                'call_index': row['calls'] + 1,
                'remaining_steps': row['max_steps'] - row['steps'],
                'remaining_calls': row['max_calls'] - row['calls'] - 1,
                'observations': observations}

    def _reserve(self, tenant, run_id):
        """Return (changed, reservation). Reserve call budget before the provider."""
        e = self.engine
        with e.tx() as c:
            row = self._row(c, tenant, run_id)
            if row['status'] not in ACTIVE:
                return False, None
            now = e.clock()
            if row['deadline'] <= now:
                self._stop(c, row, 'escalated', 'wall_deadline_exceeded')
                return True, None
            try:
                e.require_authority(c, tenant, row['channel'], row['actor'])
            except Forbidden:
                self._stop(c, row, 'escalated', 'creator_authority_revoked')
                return True, None
            if row['status'] == 'planning':
                if row['lease'] <= now:
                    self._stop(c, row, 'escalated', 'planner_lease_expired_no_retry')
                    return True, None
                return False, None
            frozen = c.execute('SELECT stopped FROM p_freeze WHERE tenant=?', (tenant,)).fetchone()
            if frozen and frozen['stopped']:
                return False, None
            if row['status'] == 'waiting_task':
                task = c.execute('SELECT status FROM p_tasks WHERE tenant=? AND id=?',
                                 (tenant, row['current_task'])).fetchone()
                if not task:
                    self._stop(c, row, 'escalated', 'task_missing')
                    return True, None
                if task['status'] in {'queued', 'running', 'waiting_approval'}:
                    return False, None
                if task['status'] != 'succeeded':
                    status = 'uncertain' if task['status'] == 'uncertain' else (
                        'cancelled' if task['status'] == 'cancelled' else 'escalated')
                    self._stop(c, row, status, 'task_' + task['status'])
                    return True, None
            if row['calls'] >= row['max_calls']:
                self._stop(c, row, 'escalated', 'planner_call_budget_exhausted')
                return True, None
            try:
                observations = self._observations(c, row)
            except (ValueError, LookupError):
                self._stop(c, row, 'escalated', 'observation_unavailable_or_too_large')
                return True, None
            claim = uuid.uuid4().hex
            lease = min(row['deadline'], now + PLANNER_LEASE_SECONDS)
            c.execute("""UPDATE p_agent_runs SET status='planning',claim=?,lease=?,calls=calls+1,updated=?
              WHERE tenant=? AND id=?""", (claim, lease, now, tenant, run_id))
            e.audit(c, tenant, row['current_task'], 'agent_run.planner_reserved', row['actor'],
                    {'run': run_id, 'call': row['calls'] + 1})
            return True, {'claim': claim, 'context': self._context(row, observations)}

    def _decision(self, row, observations, decision):
        if not isinstance(decision, dict) or len(encode(decision).encode('utf-8')) > 20000:
            raise LoopDecisionError('Invalid decision object')
        action = decision.get('action')
        if action == 'tool':
            if set(decision) != {'action', 'tool', 'args'}:
                raise LoopDecisionError('Invalid tool decision fields')
            if not isinstance(decision['tool'], str) or not isinstance(decision['args'], dict):
                raise LoopDecisionError('Invalid tool decision values')
            if row['steps'] >= row['max_steps']:
                raise LoopDecisionError('Step budget exhausted')
            if row['channel'] != DASHBOARD_CHANNEL and decision['tool'] in OUTBOUND_TOOLS:
                # A conversation turn replies once, after it ends, to the verified
                # origin. A mid-loop send would bypass that binding and the grounding gate.
                raise Forbidden('Conversation turn cannot send mid-loop')
            tool = self.engine.registry.get(decision['tool'])
            if tool.runner:
                raise Forbidden('Agent loop cannot plan local device actions')
            self.engine._validated(row['tenant'], row['agent'],
                                   [{'tool': decision['tool'], 'args': decision['args']}])
        elif action == 'final':
            if set(decision) != {'action', 'answer', 'evidence_ids'}:
                raise LoopDecisionError('Invalid final decision fields')
            answer, evidence = decision['answer'], decision['evidence_ids']
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 8000:
                raise LoopDecisionError('Invalid final answer')
            valid = {item['evidence_id'] for item in observations}
            if (not isinstance(evidence, list) or not 1 <= len(evidence) <= MAX_STEPS
                    or any(not isinstance(item, str) or item not in valid for item in evidence)
                    or len(set(evidence)) != len(evidence)):
                raise LoopDecisionError('Final answer must reference actual successful observations')
        elif action == 'ask':
            if set(decision) != {'action', 'question'}:
                raise LoopDecisionError('Invalid question fields')
            if not isinstance(decision['question'], str) or not decision['question'].strip() or len(decision['question']) > 1000:
                raise LoopDecisionError('Invalid clarification question')
        else:
            raise LoopDecisionError('Unsupported decision action')
        return action

    def _commit(self, tenant, run_id, claim, decision=None, failure=False, repair=False):
        """Commit one planner result. Returns True/False, or -- only when `repair`
        is allowed and the decision was rejected for a repairable reason -- the
        context of the one repair call, already reserved under the same claim."""
        e = self.engine
        with e.tx() as c:
            row = self._row(c, tenant, run_id)
            if row['status'] != 'planning' or row['claim'] != claim:
                return False
            now = e.clock()
            if row['deadline'] <= now or row['lease'] <= now:
                self._stop(c, row, 'escalated', 'planner_result_arrived_after_deadline')
                return True
            try:
                e.require_active(c, tenant)
                e.require_authority(c, tenant, row['channel'], row['actor'])
            except Forbidden:
                self._stop(c, row, 'escalated', 'authority_or_freeze_changed_during_planning')
                return True
            if failure:
                self._stop(c, row, 'escalated', 'planner_failed_no_retry')
                return True
            c.execute('SAVEPOINT agent_decision')
            observations = None
            try:
                observations = self._observations(c, row)
                action = self._decision(row, observations, decision)
                if action == 'tool':
                    fingerprint = digest({'tool': decision['tool'], 'args': decision['args']})
                    repeat = c.execute('SELECT 1 FROM p_agent_turns WHERE tenant=? AND run_id=? AND action_fingerprint=?',
                                       (tenant, run_id, fingerprint)).fetchone()
                    if repeat:
                        raise LoopDecisionError('Repeated identical action blocked')
                    position = row['steps']
                    task = e._submit(c, tenant, row['channel'], 'agent-run:' + run_id + ':' + str(position),
                                     row['agent'], [{'tool': decision['tool'], 'args': decision['args']}], row['actor'])
                    c.execute('INSERT INTO p_agent_turns(tenant,run_id,position,task,action_fingerprint) VALUES(?,?,?,?,?)',
                              (tenant, run_id, position, task, fingerprint))
                    c.execute("""UPDATE p_agent_runs SET status='waiting_task',steps=steps+1,
                      current_task=?,claim='',lease=0,updated=? WHERE tenant=? AND id=?""",
                              (task, now, tenant, run_id))
                    e.audit(c, tenant, task, 'agent_run.task_linked', row['actor'], {'run': run_id, 'position': position})
                else:
                    status = 'succeeded' if action == 'final' else 'needs_input'
                    answer = decision['answer'] if action == 'final' else decision['question']
                    evidence = decision['evidence_ids'] if action == 'final' else []
                    c.execute("""UPDATE p_agent_runs SET status=?,answer=?,evidence_ids=?,claim='',lease=0,updated=?
                      WHERE tenant=? AND id=?""", (status, answer, encode(evidence), now, tenant, run_id))
                    e.audit(c, tenant, row['current_task'], 'agent_run.' + status, row['actor'],
                            {'run': run_id, 'evidence_ids': evidence})
            except (ValueError, LookupError, PermissionError, RateLimited) as exc:
                c.execute('ROLLBACK TO SAVEPOINT agent_decision')
                c.execute('RELEASE SAVEPOINT agent_decision')
                if (repair and observations is not None and self._repairable(exc)
                        and row['calls'] < row['max_calls']):
                    return self._repair(c, row, observations, decision, exc)
                self._stop(c, row, 'escalated', 'planner_decision_rejected')
            else:
                c.execute('RELEASE SAVEPOINT agent_decision')
            return True

    @staticmethod
    def _repairable(exc):
        """A rejection of the decision's shape or content, not of policy or state."""
        return (isinstance(exc, (ValueError, LookupError))
                and not isinstance(exc, (Conflict, ObservationUnavailable)))

    @staticmethod
    def _repair_reason(exc):
        text = str(exc)
        return text if REPAIR_REASON.fullmatch(text) else GENERIC_REPAIR_REASON

    def _repair(self, c, row, observations, decision, exc):
        """Reserve the one repair call inside the rejecting transaction: one more
        `calls` and a fresh lease under the same claim. Nothing else changes."""
        e = self.engine
        now = e.clock()
        reason = self._repair_reason(exc)
        c.execute('UPDATE p_agent_runs SET calls=calls+1,lease=?,updated=? WHERE tenant=? AND id=?',
                  (min(row['deadline'], now + PLANNER_LEASE_SECONDS), now, row['tenant'], row['id']))
        e.audit(c, row['tenant'], row['current_task'], 'agent_run.planner_repair', row['actor'],
                {'run': row['id'], 'call': row['calls'] + 1, 'reason': reason})
        return {**self._context(row, observations), 'repair': {'decision': decision, 'error': reason}}

    def _planner_dispatch_allowed(self, tenant, run_id, claim):
        # Last local fence. A request already handed to the provider cannot be recalled.
        with self.engine.read() as c:
            row = self._row(c, tenant, run_id)
            if (row['status'] != 'planning' or row['claim'] != claim
                    or row['lease'] <= self.engine.clock()
                    or row['deadline'] <= self.engine.clock()):
                return False
            self.engine.require_active(c, tenant)
            self.engine.require_authority(c, tenant, row['channel'], row['actor'])
            return True

    def _candidates(self, tenant):
        with self.engine.read() as c:
            return [row['id'] for row in c.execute(
                """SELECT id FROM p_agent_runs
                   WHERE tenant=? AND status IN (""" + _ACTIVE_PLACEHOLDERS + """)
                   ORDER BY created,id LIMIT 100""", (tenant, *ACTIVE))]

    def reserve_next(self, tenant, skip=()):
        """Reserve the oldest run due a planner call: (changed, run_id, reservation).

        The worker's planner pool calls this, then hands the reservation to
        ``plan`` on another thread. Unlike ``tick`` it keeps looking past a run it
        retired (a deadline, a finished task), and it never touches a run in
        ``skip`` -- runs the caller is already planning. The SQL claim written by
        ``_reserve`` is what makes a second reservation of one run impossible.
        """
        changed = False
        for run_id in self._candidates(tenant):
            if run_id in skip:
                continue
            moved, reservation = self._reserve(tenant, run_id)
            if reservation:
                return True, run_id, reservation
            changed = changed or moved
        return changed, None, None

    @staticmethod
    def _repairs(planner, tenant):
        """How many rejected decisions this planner may repair: 0 unless it opts in."""
        check = getattr(planner, 'supports_repair', None)
        try:
            return MAX_DECISION_REPAIRS if check is not None and check(tenant) is True else 0
        except Exception:
            return 0

    def plan(self, tenant, run_id, reservation, planner):
        """Make the one reserved planner call and commit its result. Returns True.

        No SQL transaction is open during the call. A failure is committed as
        ``planner_failed_no_retry``; transient provider retries, if any, happened
        inside the planner under the lease. A planner that opts in may get one
        repair call (see MAX_DECISION_REPAIRS), reserved by ``_commit`` and
        fenced here again before it is dispatched.
        """
        claim = reservation['claim']
        context = reservation['context']
        repairs = self._repairs(planner, tenant)
        while True:
            try:
                if not self._planner_dispatch_allowed(tenant, run_id, claim):
                    self._commit(tenant, run_id, claim, failure=True)
                    return True
                decision = planner(tenant, context)
            except Exception:
                self._commit(tenant, run_id, claim, failure=True)
                return True
            outcome = self._commit(tenant, run_id, claim, decision, repair=repairs > 0)
            if not isinstance(outcome, dict):
                return True
            repairs -= 1
            context = outcome

    def tick(self, tenant, planner):
        """Advance at most one run, at most one planner call. No automatic retries."""
        for run_id in self._candidates(tenant):
            changed, reservation = self._reserve(tenant, run_id)
            if reservation:
                return self.plan(tenant, run_id, reservation, planner)
            if changed:
                return True
        return False
