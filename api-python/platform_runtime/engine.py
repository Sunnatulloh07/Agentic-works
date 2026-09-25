"""Durable task engine. External side effects never run inside a DB transaction.

Expired leases become uncertain: generic exactly-once external execution is impossible.
Approval binds immutable arguments, risk and policy to a single step. All access is scoped.
"""
import hashlib
import importlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


# Policy keys that describe an agent rather than authorise it. Prompt material
# shapes what an agent proposes, never what it may do, so editing it must not
# cancel approvals an operator already granted for specific, unchanged arguments.
# The conversation block (enabled, step/time budget, history depth, fallback text)
# is the same kind of material: it decides whether and how a turn is PLANNED, and
# every step that turn produces is still validated against the authorising keys.
# A drafted reply awaiting approval carries its text in the step arguments, so an
# edited fallback_text cannot change what the operator approved -- and must not
# void that approval either.
DESCRIPTIVE_POLICY_KEYS = ('persona', 'conversation')

# Tools that reach a destination outside the tenant, and the argument that names
# that destination. Naming it here rather than inline matters: a channel added to
# the runtime without being added here would silently lose the engine-level
# destination check and rely on its adapter alone, which is exactly the defence
# that must not depend on a module remembering to do it.
DIRECT_DESTINATION_FIELD = {'telegram.send': 'conversation_id',
                            'instagram.send': 'conversation_id',
                            'whatsapp.send': 'contact'}
OUTBOUND_TOOLS = frozenset(DIRECT_DESTINATION_FIELD)
# Channels that own an inbound event stream, so a reply can be bound to the event
# that opened it. WhatsApp joined once it got a verified webhook route
# (app/whatsapp_api.py): its events carry the sender's wa_id as conversation_id, so a
# task on the whatsapp channel -- a conversation turn's reply -- may only send
# whatsapp.send to that wa_id. This moves only tasks ON the whatsapp channel to the
# event-binding branch below; a template or outreach send from cron, web or an agent
# run has no inbound event and stays on the allowlist branch, unchanged. "Is the
# customer inside the 24-hour window" is still answered in whatsapp.py, from the
# verified events, before any provider I/O.
OUTBOUND_CHANNELS = frozenset({'telegram', 'instagram', 'whatsapp'})

# ERP posting is deliberately NOT in the set above, and it is worth naming why,
# because "reaches an external system" sounds like the same category and is not.
#
# ``erp.posting_submit`` does reach outside the tenant, but its risk is not "which
# recipient was named". There is no recipient: the destination is a configured ERP
# host that the operator declared, and the question that matters is "has this
# document already been posted", which no allowlist can answer. Its guard is the
# document identity -- (driver, kind, supplier, number) -- checked against the ERP
# itself and against a UNIQUE local ledger index, in erp.py. Adding it here would
# put it in the allowlist branch, where an operator would have to allowlist... the
# document number, which changes with every invoice, and the check would then be
# vacuous while appearing present. A rule that cannot fail is worse than no rule:
# it is a rule someone will trust.
#
# The precondition that DOES belong at this layer is the operator's declaration
# that posting is enabled at all, and that is enforced twice -- once in
# erp.resolve_posting and once in erp.submit -- both times before any provider I/O.


class Conflict(ValueError): pass
class Forbidden(PermissionError): pass
class NotFound(LookupError): pass
class RateLimited(RuntimeError): pass


# The only shape a rejection reason may take: a status or a provider verdict,
# never a message. A provider message can quote the URL, and the URL carries
# the bot token.
SAFE_REASON = re.compile(r'[a-z0-9_]{1,40}')


class DeliveryRejected(RuntimeError):
    """The provider answered and definitively did NOT perform the effect.

    ``uncertain`` is for the outcome nobody can know: the request may have
    reached the provider and the effect may have happened (a timeout, a reset,
    a 502 from a gateway). A 4xx from the Bot API or an ``ok: false`` body is
    not that case -- the provider said no, and nothing was delivered. An adapter
    raises this only where the outcome is certain; ``Engine.tick`` turns it into
    ``failed`` so the operator is not sent to reconcile a delivery that never
    happened, and a conversation turn is handed off rather than marked "never
    resend". A RuntimeError, so callers that already catch adapter failures
    keep catching this one.

    ``reason`` is kept only when it matches ``SAFE_REASON``; anything else is
    collapsed to ``rejected``, so no future adapter can leak a message through
    it by accident.
    """
    def __init__(self, reason='rejected'):
        self.reason = reason if isinstance(reason, str) and SAFE_REASON.fullmatch(reason) else 'rejected'
        super().__init__(self.reason)


# Operator takeover replies. A human answers a customer from the dashboard; the
# task is keyed under a channel of its own so that the generic submit path can
# never mint one, and the idempotency key is namespaced so it cannot collide
# with a dashboard task key.
OPERATOR_CHANNEL = 'operator'
OPERATOR_REPLY_PREFIX = 'operator-reply:'
OPERATOR_ROLES = frozenset({'owner', 'operator'})


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def event_error(exc):
    """Failure reason stored on a rejected inbound event, for the operator inbox.

    Platform exceptions carry authored messages that name the pack mistake, for
    example a missing agent or a tool the agent may not use; an operator cannot
    fix a pack from a bare type name. Every other exception is reported by type
    only: provider messages can embed URLs, arguments or credentials.
    """
    if isinstance(exc, (Conflict, Forbidden, NotFound, RateLimited)):
        return (type(exc).__name__ + ': ' + str(exc))[:200]
    return type(exc).__name__


# The conversation an inbound event belongs to, as an expression that is TOTAL over
# p_events: SQLite evaluates an index expression on every INSERT and while building
# the index, and bare json_extract raises on a non-JSON payload -- which would fail
# the insert, or fail Engine startup on a database that already holds such a row.
# The lookup must use this exact text, or the planner cannot use the index.
EVENT_CONVERSATION_ID = "(CASE WHEN json_valid(payload) THEN json_extract(payload,'$.conversation_id') END)"

SCHEMA = '''
CREATE TABLE IF NOT EXISTS p_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS p_tasks(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, channel TEXT NOT NULL, event_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, agent TEXT NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL,
 created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(tenant,channel,event_key));
CREATE TABLE IF NOT EXISTS p_steps(
 id TEXT PRIMARY KEY, task TEXT NOT NULL REFERENCES p_tasks(id), tenant TEXT NOT NULL,
 position INTEGER NOT NULL, tool TEXT NOT NULL, args TEXT NOT NULL, risk TEXT NOT NULL,
 approval_needed INTEGER NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
 claim TEXT NOT NULL DEFAULT '', worker TEXT NOT NULL DEFAULT '', lease REAL NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
 device TEXT NOT NULL DEFAULT '', UNIQUE(task,position));
CREATE TABLE IF NOT EXISTS p_approvals(
 step TEXT PRIMARY KEY REFERENCES p_steps(id), tenant TEXT NOT NULL, fingerprint TEXT NOT NULL,
 status TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '', expires REAL NOT NULL, decided REAL);
CREATE TABLE IF NOT EXISTS p_database_dispatch(
 tenant TEXT NOT NULL, step TEXT NOT NULL REFERENCES p_steps(id), task TEXT NOT NULL,
 fingerprint TEXT NOT NULL, status TEXT NOT NULL,
 receipt TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY(tenant,step));
CREATE TABLE IF NOT EXISTS p_audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, task TEXT NOT NULL,
 action TEXT NOT NULL, actor TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS p_memory(
 tenant TEXT NOT NULL, agent TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
 expires REAL NOT NULL DEFAULT 0, PRIMARY KEY(tenant,agent,key));
CREATE TABLE IF NOT EXISTS p_records(
 tenant TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
 created REAL NOT NULL, PRIMARY KEY(tenant,kind,id));
CREATE TABLE IF NOT EXISTS p_devices(
 tenant TEXT NOT NULL, id TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
 generation INTEGER NOT NULL DEFAULT 1, seen REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS p_freeze(tenant TEXT PRIMARY KEY, stopped INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS p_events(
 tenant TEXT NOT NULL, channel TEXT NOT NULL, event_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', claim TEXT NOT NULL DEFAULT '',
 lease REAL NOT NULL DEFAULT 0, result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(tenant,channel,event_key));
CREATE TABLE IF NOT EXISTS p_quota(tenant TEXT NOT NULL, day INTEGER NOT NULL, count INTEGER NOT NULL,
 PRIMARY KEY(tenant,day));
CREATE TABLE IF NOT EXISTS p_schedules(
 tenant TEXT NOT NULL, id TEXT NOT NULL, agent TEXT NOT NULL, steps TEXT NOT NULL,
 interval_seconds INTEGER NOT NULL, next_due REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS p_schedule_owners(tenant TEXT NOT NULL,id TEXT NOT NULL,actor TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS p_agent_runs(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, request_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, agent TEXT NOT NULL, actor TEXT NOT NULL,
 input TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
 deadline REAL NOT NULL, max_steps INTEGER NOT NULL, max_calls INTEGER NOT NULL,
 calls INTEGER NOT NULL DEFAULT 0, steps INTEGER NOT NULL DEFAULT 0,
 current_task TEXT NOT NULL DEFAULT '', claim TEXT NOT NULL DEFAULT '',
 lease REAL NOT NULL DEFAULT 0, answer TEXT NOT NULL DEFAULT '',
 evidence_ids TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
 channel TEXT NOT NULL DEFAULT 'agent',
 UNIQUE(tenant,request_key));
CREATE TABLE IF NOT EXISTS p_agent_turns(
 tenant TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES p_agent_runs(id),
 position INTEGER NOT NULL, task TEXT NOT NULL REFERENCES p_tasks(id),
 action_fingerprint TEXT NOT NULL, PRIMARY KEY(run_id,position), UNIQUE(tenant,task));
CREATE INDEX IF NOT EXISTS p_agent_runs_pending ON p_agent_runs(tenant,status,created);
CREATE INDEX IF NOT EXISTS p_steps_queue ON p_steps(tenant,status,position);
CREATE INDEX IF NOT EXISTS p_tasks_tenant ON p_tasks(tenant,created);
CREATE INDEX IF NOT EXISTS p_audit_tenant ON p_audit(tenant,id);
DROP INDEX IF EXISTS p_events_conversation;
CREATE INDEX IF NOT EXISTS p_events_conversation_id
 ON p_events(tenant,channel,''' + EVENT_CONVERSATION_ID + ''');
'''


# Every module that owns tables, in the order their schemas are applied. A tuple so
# that the fingerprint below and the application loop cannot disagree about which
# schemas exist: a module added to one and not the other would either be skipped on
# a warm database or never invalidate it.
SCHEMA_MODULES = ('usage_budget', 'knowledge', 'oauth', 'google_adapters', 'sync_store',
                  'reengagement', 'briefing', 'supervisor', 'documents', 'erp',
                  'escalation', 'conversation')

# The migration rows the engine records for itself, applied with the schema.
# 9: p_agent_runs.channel (see _add_columns).
MIGRATION_NUMBERS = range(1, 10)

# Columns added to a table that already existed in an older schema. CREATE TABLE IF
# NOT EXISTS cannot add them there, so each is added once, guarded by table_info,
# the pattern app/identity_schema.py uses. The default keeps every pre-existing
# row's meaning: a run created before the column existed was a dashboard run.
ADDED_COLUMNS = (('p_agent_runs', 'channel', "TEXT NOT NULL DEFAULT 'agent'"),)


def _add_columns(c):
    for table, name, definition in ADDED_COLUMNS:
        if name not in {row[1] for row in c.execute(f'PRAGMA table_info({table})')}:
            c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')


def _schema_script():
    """Every schema in this runtime, as ONE script.

    Merged rather than applied one at a time because each ``executescript`` is its
    own transaction, and twelve of them cost twelve commit round trips. Measured on
    a fresh file: twelve scripts 183 ms, one script 100 ms.
    """
    parts = [SCHEMA]
    for name in SCHEMA_MODULES:
        parts.append(importlib.import_module(f'.{name}', __package__).SCHEMA)
    return '\n'.join(parts)


def _schema_fingerprint(script):
    """A number that changes whenever ANY module's schema text changes.

    Stored on the database as ``PRAGMA user_version``. Deriving it from the schema
    text rather than keeping a hand-bumped counter means it cannot drift: adding a
    table to any module changes the string, so the next ``Engine`` re-applies the
    schema. A counter would need a human to remember, and the failure mode of
    forgetting is a missing table discovered at runtime rather than at startup.
    """
    digest = hashlib.sha256(script.encode('utf-8')).digest()
    return int.from_bytes(digest[:4], 'big') & 0x7FFFFFFF


class Engine:
    def __init__(self, path, registry, policy, clock=time.time, authority=None):
        self.authority = authority
        self.path, self.registry, self.policy, self.clock = str(path), registry, policy, clock
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            # Applying the schema costs about 45 ms and every Engine construction
            # used to pay it, hundreds of times per test file. The fingerprint makes
            # the work once per DATABASE rather than once per object, which changes
            # nothing about what a fresh database receives.
            script = _schema_script()
            fingerprint = _schema_fingerprint(script)
            applied = c.execute('PRAGMA user_version').fetchone()[0]
            if applied != fingerprint:
                # NORMAL durability for this write, then the default is restored by
                # the connection closing. It is safe precisely because the write is
                # IDEMPOTENT and the fingerprint below makes a lost one re-apply on
                # the next open: the only transaction at risk is this one, and its
                # loss is detected rather than silent. WAL plus NORMAL is also the
                # configuration SQLite documents for this mode. Measured: 100 ms
                # with the default, 45 ms with NORMAL.
                c.execute('PRAGMA synchronous=NORMAL')
                c.executescript(script)
                _add_columns(c)
                for number in MIGRATION_NUMBERS:
                    c.execute('INSERT OR IGNORE INTO p_migrations VALUES(?,?)',
                              (number, self.clock()))
                # An integer literal from a value this module computed, never from a
                # caller: PRAGMA does not accept a bound parameter.
                c.execute(f'PRAGMA user_version={fingerprint}')
        c.close()

    def connect(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=30000')
        return c

    @contextmanager
    def tx(self):
        c = self.connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @contextmanager
    def read(self):
        c = self.connect()
        try: yield c
        finally: c.close()

    def audit(self, c, tenant, task, action, actor, data=None):
        c.execute('INSERT INTO p_audit(tenant,task,action,actor,data,created) VALUES(?,?,?,?,?,?)',
                  (tenant, task, action, actor, encode(data or {}), self.clock()))

    def audit_write(self, tenant, action, actor, data=None, task=''):
        """Audit an action that has no surrounding transaction of its own.

        The engine's ``audit`` needs an open cursor. Scheduling and coordinator
        code runs outside ``tx()``, so without this helper a summary event would
        either be lost or force an ad-hoc transaction at each call site.
        """
        with self.tx() as c:
            self.audit(c, tenant, task, action, actor, data)

    def require_authority(self, c, tenant, channel='', actor='', roles=('owner','operator')):
        if self.authority:self.authority(c,tenant,channel,actor,roles)

    def require_active(self, c, tenant):
        self.require_authority(c,tenant)
        frozen=c.execute("SELECT stopped FROM p_freeze WHERE tenant=?",(tenant,)).fetchone()
        if frozen and frozen["stopped"]:
            raise Forbidden("Tenant is frozen")

    def require_task_parent(self, c, tenant, task):
        # A linked run cannot dispatch after cancellation or its wall deadline.
        row=c.execute('''SELECT r.status,r.deadline,r.current_task FROM p_agent_turns l
          JOIN p_agent_runs r ON r.id=l.run_id AND r.tenant=l.tenant
          WHERE l.tenant=? AND l.task=?''',(tenant,task)).fetchone()
        if row and (row['status']!='waiting_task' or row['current_task']!=task
                    or row['deadline']<=self.clock()):
            raise Forbidden('Agent run no longer authorizes this task')

    def dispatch_allowed(self, tenant, step):
        with self.read() as c:
            self.require_active(c,tenant)
            self.require_task_parent(c,tenant,step['task'])
            task=c.execute('SELECT channel,actor FROM p_tasks WHERE tenant=? AND id=?',(tenant,step['task'])).fetchone()
            if not task:raise Forbidden('Task missing')
            self.require_authority(c,tenant,task['channel'],task['actor'])
            if step['approval_needed']:
                approver=c.execute('SELECT actor FROM p_approvals WHERE tenant=? AND step=?',(tenant,step['id'])).fetchone()
                required=('owner',) if self.policy(tenant,step['agent']).get('approver_role')=='owner' else ('owner','operator')
                self.require_authority(c,tenant,'approval',approver['actor'] if approver else '',required)
            row=c.execute("SELECT 1 FROM p_steps WHERE tenant=? AND id=? AND claim=? AND status='running' AND lease>?",
                          (tenant,step["id"],step["claim"],self.clock())).fetchone()
            return bool(row)

    def _origin(self, c, tenant, channel, event_key):
        """The verified inbound event a channel task answers, or None.

        Only channels that own an inbound stream can bind a reply to its origin;
        a cron or web task has no origin and its outbound steps must be allowlisted.
        """
        if channel not in OUTBOUND_CHANNELS:
            return None
        row = c.execute('SELECT payload FROM p_events WHERE tenant=? AND channel=? AND event_key=?',
                        (tenant, channel, event_key)).fetchone()
        if not row:
            return None
        return {'channel': channel, 'conversation_id': json.loads(row['payload']).get('conversation_id')}

    @staticmethod
    def _preauthorized(policy, name, args, origin):
        """An outbound write whose destination the tenant already authorised.

        Two shapes, one rule: the pack allowlisted the recipient, or the recipient
        is the verified inbound conversation this task answers. Only an autonomous
        agent earns the exemption, and the caller has already excluded destructive
        and physical risk, which always wait for a human (PRD F5).
        """
        if policy.get('ladder') != 'autonomous' or name not in OUTBOUND_TOOLS:
            return False
        recipient = args.get(DIRECT_DESTINATION_FIELD[name])
        if recipient in policy.get('allowed_recipients', []):
            return True
        return bool(origin and name == origin['channel'] + '.send'
                    and recipient == origin['conversation_id'])

    def _validated(self, tenant, agent, steps, origin=None):
        policy = self.policy(tenant, agent)
        if policy.get('ladder') not in {'human_led','human_assisted','autonomous'}:
            raise Forbidden('Invalid autonomy policy')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
            raise ValueError('Plan requires 1..20 steps')
        # An operator origin (see operator_reply) authorises exactly one thing: the
        # plain channel send the human wrote. It never relaxes approval -- it forces
        # it, so the human's decision is what the claim path re-verifies -- and a
        # destructive or physical tool never rides on it (PRD F5).
        operator = bool(origin and origin.get('channel') == OPERATOR_CHANNEL)
        if operator and len(steps) != 1:
            raise ValueError('An operator reply is exactly one send')
        result = []
        for step in steps:
            if not isinstance(step, dict) or set(step)-{'tool','args','device'}:
                raise ValueError('Invalid step fields')
            name = step.get('tool')
            spec = self.registry.get(name)
            if name not in policy.get('tools', []):
                raise Forbidden('Tool not allowed for agent')
            if operator and (name != origin['reply_channel'] + '.send'
                             or spec.risk in ('destructive', 'physical')):
                raise Forbidden('Operator reply may only carry a plain channel send')
            args = step.get('args', {})
            spec.validate(args)
            if name in {'connectors.read', 'database.read', 'database.plan_write', 'database.write'} and args['connection'] not in policy.get('allowed_connections',[]):
                raise Forbidden('Connection not allowed for agent')
            if name == 'connectors.read':
                # Rechecked at submission and claim; handler checks again before IO.
                from .connectors import authorize_read_connection
                authorize_read_connection(tenant, args['connection'], agent=agent)
            google_binding = None
            if name.startswith('google.'):
                from .google_adapters import validate_step as validate_google
                google_binding = validate_google(self, tenant, agent, name, args)
            database_binding = None
            if name in {'database.read', 'database.plan_write', 'database.write'}:
                from .database.gateway import validate_step
                database_binding = validate_step(tenant, agent, name, args)
            # Approval is the default for every write. The one exemption is an
            # autonomous agent sending to a destination the tenant pre-authorised;
            # human_led gates everything, required_for gates by name, and
            # destructive or physical risk is never unattended.
            needed = (policy['ladder'] == 'human_led'
                      or operator
                      or name in policy.get('approval', [])
                      or spec.risk in ('destructive', 'physical')
                      or (spec.risk != 'read'
                          and not self._preauthorized(policy, name, args, origin)))
            device = step.get('device', '')
            if not isinstance(device, str) or len(device)>128:
                raise ValueError('Invalid device')
            if spec.runner and not device: raise ValueError('Runner tool requires device')
            if not spec.runner and device: raise ValueError('Cloud step must not bind device')
            canonical = {'tool': name, 'args': args, 'risk': spec.risk,
                         'approval': needed, 'device': device,
                         'policy': {k: v for k, v in policy.items()
                                    if k not in DESCRIPTIVE_POLICY_KEYS}}
            if google_binding is not None:
                canonical['google_binding'] = google_binding
            if database_binding is not None:
                canonical['database_binding'] = database_binding
            result.append((name,args,spec.risk,int(needed),digest(canonical),device))
        return result

    def submit(self, tenant, channel, event_key, agent, steps, actor='system'):
        with self.tx() as c:
            return self._submit(c,tenant,channel,event_key,agent,steps,actor)

    def _submit(self, c, tenant, channel, event_key, agent, steps, actor):
        """Internal: caller owns the write transaction; never calls providers."""
        for v in (tenant,channel,event_key,agent,actor):
            if not isinstance(v,str) or not v or len(v)>256: raise ValueError('Invalid identity')
        if channel == OPERATOR_CHANNEL:
            # Reserved: only operator_reply mints a task on this channel, because the
            # claim path treats that channel as "a human authorised this send".
            raise Forbidden('Channel reserved for operator replies')
        origin = self._origin(c, tenant, channel, event_key)
        validated = self._validated(tenant,agent,steps,origin)
        # External destinations are authorized in the engine, not just in LLM prompting.
        # The destination field differs per channel ('conversation_id' for the chat
        # channels, 'contact' for WhatsApp) but the rule is one rule: a direct
        # outbound must name a pack-allowlisted recipient, and a channel-bound
        # outbound must match the verified inbound event that opened it.
        for step in steps:
            if step['tool'] in OUTBOUND_TOOLS:
                recipient=step.get('args',{}).get(DIRECT_DESTINATION_FIELD.get(step['tool'],'conversation_id'))
                if channel in OUTBOUND_CHANNELS:
                    # The origin is read once above; it used to be re-read here into the
                    # same name, shadowing the dict with a sqlite Row mid-loop.
                    if not origin or step['tool'] != channel+'.send' or origin['conversation_id']!=recipient:
                        raise Forbidden('Outbound destination differs from verified inbound event')
                elif recipient not in self.policy(tenant,agent).get('allowed_recipients',[]):
                    raise Forbidden('Direct outbound destination must be pack-allowlisted')
        return self._create_task(c,tenant,channel,event_key,agent,steps,actor,validated)

    def _create_task(self, c, tenant, channel, event_key, agent, steps, actor, validated, approver=''):
        """Insert an already validated plan; the idempotent half of every submit.

        ``approver`` is set only by operator_reply: the human who wrote the reply
        has decided it, so its approval row is inserted already ``approved`` by
        that actor and audited as such. The claim path then consumes it exactly as
        it consumes a dashboard approval, including the approver authority check.
        """
        fp = digest({'agent':agent,'steps':steps})
        # Replay is not an authorization bypass. An authorized frozen-tenant
        # replay remains read-only, but a revoked actor cannot recover a task.
        self.require_authority(c,tenant,channel,actor)
        old = c.execute('SELECT id,fingerprint FROM p_tasks WHERE tenant=? AND channel=? AND event_key=?',
                        (tenant,channel,event_key)).fetchone()
        if old:
            if old['fingerprint']!=fp: raise Conflict('Idempotency key reused with different payload')
            return old['id']
        self.require_active(c,tenant)
        pending=c.execute("SELECT count(*) n FROM p_tasks WHERE tenant=? AND status IN ('queued','running','waiting_approval')",(tenant,)).fetchone()['n']
        if pending>=1000:raise RateLimited('Task queue full')
        tid = uuid.uuid4().hex
        now = self.clock()
        c.execute('INSERT INTO p_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (tid,tenant,channel,event_key,fp,agent,actor,'queued',now,now))
        for pos,(name,args,risk,needed,sfp,device) in enumerate(validated):
            sid = uuid.uuid4().hex
            c.execute('INSERT INTO p_steps(id,task,tenant,position,tool,args,risk,approval_needed,fingerprint,status,device) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (sid,tid,tenant,pos,name,encode(args),risk,needed,sfp,'queued',device))
            if needed and approver:
                c.execute('INSERT INTO p_approvals(step,tenant,fingerprint,status,actor,expires,decided) VALUES(?,?,?,?,?,?,?)',
                          (sid,tenant,sfp,'approved',approver,now+86400,now))
                self.audit(c,tenant,tid,'approval.approved',approver,{'step':sid,'fingerprint':sfp,'operator_reply':True})
            elif needed:
                c.execute('INSERT INTO p_approvals(step,tenant,fingerprint,status,expires) VALUES(?,?,?,?,?)',
                          (sid,tenant,sfp,'pending',now+86400))
        self.audit(c,tenant,tid,'task.created',actor,{'agent':agent,'steps':len(steps)})
        self._refresh(c,tenant,tid)
        return tid

    def _operator_origin(self, c, tenant, tool, args):
        """The conversation an operator reply answers, re-derived from the step itself.

        A dashboard reply has no inbound event of its own to bind to, so the binding
        is the step's destination checked against the tenant's verified inbound
        stream: some event on that channel, accepted by a verified webhook, must
        carry this conversation_id. Read at submission AND at claim, like _origin,
        so a conversation that is no longer known is not written to. Raises
        NotFound when it is not known; Forbidden when the tool is not a channel
        send at all.
        """
        if tool not in OUTBOUND_TOOLS or not tool.endswith('.send'):
            raise Forbidden('Operator reply requires a channel send')
        channel = tool[:-len('.send')]
        if channel not in OUTBOUND_CHANNELS:
            raise Forbidden('Operator reply needs a channel with a verified inbound stream')
        target = args.get(DIRECT_DESTINATION_FIELD[tool])
        if not isinstance(target, str) or not target:
            raise ValueError('Invalid conversation identity')
        known = c.execute('SELECT 1 FROM p_events WHERE tenant=? AND channel=? AND '
                          + EVENT_CONVERSATION_ID + '=? LIMIT 1',
                          (tenant, channel, target)).fetchone()
        if not known:
            raise NotFound('Conversation unknown to this tenant')
        return {'channel': OPERATOR_CHANNEL, 'reply_channel': channel, 'conversation_id': target}

    def _task_origin(self, c, tenant, row):
        """The origin a claimed step is re-validated against: event-bound or operator-bound."""
        if row['channel'] == OPERATOR_CHANNEL:
            return self._operator_origin(c, tenant, row['tool'], json.loads(row['args']))
        return self._origin(c, tenant, row['channel'], row['event_key'])

    def operator_reply(self, tenant, channel, conversation_id, text, *, actor, role, key, agent):
        """A human answers a customer: one ``<channel>.send`` the human has decided.

        The third authorising shape for an outbound send, deliberately narrow:

        * ``channel`` must own a verified inbound stream (OUTBOUND_CHANNELS) and
          ``conversation_id`` must have sent this tenant an event on it;
        * ``actor`` must hold an owner/operator role NOW (``role`` is the
          authenticated claim; ``require_authority`` re-checks the directory
          inside this transaction), and must satisfy the agent's approver_role;
        * ``agent`` is whichever pack agent holds the send tool -- resolved by the
          caller through capability routing, never named here -- and its ladder
          does not gate the human's own reply, because the human is the approver;
        * the task is idempotent on ``key``: same key and text -> same task; a
          different text or destination under the same key -> Conflict.

        The approval is recorded as decided by ``actor`` so the audit answers "who
        sent this", and the claim path re-checks the actor's authority, the
        conversation's existence and the tenant's freeze before dispatch.
        """
        if channel not in OUTBOUND_CHANNELS:
            raise ValueError('Channel cannot carry an operator reply')
        if role not in OPERATOR_ROLES:
            raise Forbidden('Operator reply requires an owner or operator')
        for value in (actor, agent, conversation_id):
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError('Invalid identity')
        if not isinstance(key, str) or not key or len(OPERATOR_REPLY_PREFIX + key) > 256:
            raise ValueError('Invalid idempotency key')
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Reply text must be 1..4000 characters')
        tool = channel + '.send'
        steps = [{'tool': tool, 'args': {DIRECT_DESTINATION_FIELD[tool]: conversation_id, 'text': text}}]
        with self.tx() as c:
            policy = self.policy(tenant, agent)
            required = ('owner',) if policy.get('approver_role') == 'owner' else ('owner', 'operator')
            if role not in required:
                raise Forbidden('Owner-only approval')
            self.require_authority(c, tenant, 'approval', actor, required)
            origin = self._operator_origin(c, tenant, tool, steps[0]['args'])
            validated = self._validated(tenant, agent, steps, origin)
            return self._create_task(c, tenant, OPERATOR_CHANNEL, OPERATOR_REPLY_PREFIX + key,
                                     agent, steps, actor, validated, approver=actor)

    def _refresh(self,c,tenant,tid):
        rows = c.execute('SELECT status FROM p_steps WHERE tenant=? AND task=? ORDER BY position', (tenant,tid)).fetchall()
        states = [r['status'] for r in rows]
        if 'uncertain' in states: status='uncertain'
        elif 'failed' in states: status='failed'
        elif 'cancelled' in states: status='cancelled'
        elif all(s=='succeeded' for s in states): status='succeeded'
        elif 'running' in states: status='running'
        elif 'waiting_approval' in states: status='waiting_approval'
        else: status='queued'
        c.execute('UPDATE p_tasks SET status=?,updated=? WHERE tenant=? AND id=?', (status,self.clock(),tenant,tid))

    def get(self, tenant, tid):
        with self.read() as c:
            row=c.execute('SELECT * FROM p_tasks WHERE tenant=? AND id=?',(tenant,tid)).fetchone()
            if not row: raise NotFound('Task not found')
            out=dict(row)
            out['steps']=[dict(r) for r in c.execute('SELECT s.*,a.status approval_status,a.actor approver,a.expires approval_expires FROM p_steps s LEFT JOIN p_approvals a ON s.id=a.step WHERE s.tenant=? AND s.task=? ORDER BY s.position',(tenant,tid))]
            for s in out['steps']:
                s['args']=json.loads(s['args']);s['result']=json.loads(s['result']);s.pop('claim',None)
            return out

    def list_tasks(self, tenant, limit=100):
        """The tenant's tasks, newest first, capped at 200.

        The bound is CLAMPED, not validated: a caller asking for more than 200 gets
        200 rather than a refusal, because a page size is a preference. What must
        not happen is an unmappable crash -- `min(200, max(1, limit))` raises
        `TypeError` on a non-integer, and `TypeError` is not in the API layer's
        exception map (`call()` catches RateLimited, Forbidden, NotFound, Conflict,
        ValueError and LookupError), so it would escape as a 500 for what is a
        caller's mistake. Every other bounded reader in the runtime raises
        `ValueError`; this one now does too, so the day `/tasks` grows a `limit`
        query parameter it answers 422 like the rest.
        """
        if type(limit) is not int or isinstance(limit, bool):
            raise ValueError('limit must be an integer')
        with self.read() as c:
            return [dict(r) for r in c.execute('SELECT * FROM p_tasks WHERE tenant=? ORDER BY created DESC LIMIT ?', (tenant,min(200,max(1,limit))))]

    def approve(self,tenant,sid,actor,decision,role):
        if role not in {'owner','operator','super-admin'}: raise Forbidden('Approval role required')
        if decision not in {'approved','rejected'}: raise ValueError('Invalid decision')
        with self.tx() as c:
            r=c.execute('SELECT a.*,s.task,s.status step_status,t.agent,t.actor creator FROM p_approvals a JOIN p_steps s ON s.id=a.step JOIN p_tasks t ON t.id=s.task WHERE a.tenant=? AND a.step=?',(tenant,sid)).fetchone()
            if not r: raise NotFound('Approval not found')
            self.require_active(c,tenant)
            self.require_task_parent(c,tenant,r['task'])
            current_policy=self.policy(tenant,r['agent'])
            required=current_policy.get('approver_role','operator')
            self.require_authority(c,tenant,'approval',actor,('owner',) if required=='owner' else ('owner','operator'))
            if (decision=='approved' and current_policy.get('independent_approval',False)
                    and actor==r['creator']):
                raise Forbidden('Independent approver required')
            if required not in {'operator','owner'}:raise Forbidden('Unsupported approver policy')
            if required=='owner' and role not in {'owner','super-admin'}:raise Forbidden('Owner-only approval')
            if r['status']!='pending' or r['step_status'] not in {'queued','waiting_approval'} or r['expires']<=self.clock():
                raise Conflict('Approval already decided, expired or step not pending')
            c.execute('UPDATE p_approvals SET status=?,actor=?,decided=? WHERE tenant=? AND step=?', (decision,actor,self.clock(),tenant,sid))
            c.execute('UPDATE p_steps SET status=? WHERE tenant=? AND id=?', ('queued' if decision=='approved' else 'cancelled',tenant,sid))
            self.audit(c,tenant,r['task'],'approval.'+decision,actor,{'step':sid,'fingerprint':r['fingerprint']})
            self._refresh(c,tenant,r['task'])

    def claim(self,tenant,worker,device='',lease_seconds=90):
        if not worker or not 5<=lease_seconds<=600: raise ValueError('Invalid lease')
        with self.tx() as c:
            now=self.clock()
            expired=c.execute("SELECT id,task FROM p_steps WHERE tenant=? AND status='running' AND lease<=?",(tenant,now)).fetchall()
            for r in expired:
                c.execute("UPDATE p_steps SET status='uncertain',error='lease_expired',claim='' WHERE tenant=? AND id=?",(tenant,r['id']))
                self.audit(c,tenant,r['task'],'step.uncertain','recovery',{'step':r['id']})
                self._refresh(c,tenant,r['task'])
            frozen=c.execute('SELECT stopped FROM p_freeze WHERE tenant=?',(tenant,)).fetchone()
            if frozen and frozen['stopped']: return None
            try:self.require_authority(c,tenant)
            except Forbidden:return None
            if device:
                d=c.execute('SELECT revoked FROM p_devices WHERE tenant=? AND id=?',(tenant,device)).fetchone()
                if not d or d['revoked']: raise Forbidden('Device revoked or unknown')
            rows=c.execute("SELECT s.*,t.agent,t.actor creator,t.channel,t.event_key FROM p_steps s JOIN p_tasks t ON t.id=s.task WHERE s.tenant=? AND s.device=? AND s.status IN ('queued','waiting_approval') AND t.status IN ('queued','running','waiting_approval') AND NOT EXISTS(SELECT 1 FROM p_steps prev WHERE prev.task=s.task AND prev.position<s.position AND prev.status!='succeeded') ORDER BY t.created,s.position",(tenant,device)).fetchall()
            for row in rows:
                r=dict(row)
                try:
                    self.require_task_parent(c,tenant,r['task'])
                except Forbidden:
                    c.execute("UPDATE p_steps SET status='cancelled',claim='',lease=0,error='agent_run_inactive' WHERE tenant=? AND id=?",(tenant,r['id']))
                    c.execute("UPDATE p_approvals SET status='rejected',actor='agent-loop',decided=? WHERE tenant=? AND step=? AND status IN ('pending','approved')",(now,tenant,r['id']))
                    self.audit(c,tenant,r['task'],'step.cancelled','agent-loop',{'step':r['id'],'reason':'agent_run_inactive'})
                    self._refresh(c,tenant,r['task'])
                    continue
                error='authority_revoked'
                try:
                    self.require_authority(c,tenant,r['channel'],r['creator'])
                    error='policy_changed'
                    # The origin is re-read so a reply is re-bound to the same verified
                    # conversation it was submitted against: the inbound event for a
                    # channel task, the tenant's inbound stream for an operator reply.
                    origin=self._task_origin(c,tenant,r)
                    validated=self._validated(tenant,r['agent'],[{'tool':r['tool'],'args':json.loads(r['args']),'device':r['device']}],origin)[0]
                except NotFound:
                    validated=None;error='conversation_unknown'
                except (ValueError,LookupError,PermissionError):
                    validated=None
                if validated is None or validated[4]!=r['fingerprint']:
                    c.execute("UPDATE p_steps SET status='failed',error=? WHERE id=?",(error,r['id']));self._refresh(c,tenant,r['task']);continue
                if r['approval_needed']:
                    a=c.execute('SELECT * FROM p_approvals WHERE tenant=? AND step=?',(tenant,r['id'])).fetchone()
                    if not a or a['fingerprint']!=r['fingerprint'] or a['expires']<=now:
                        c.execute("UPDATE p_steps SET status='failed',error='approval_expired_or_invalid' WHERE id=?",(r['id'],));self._refresh(c,tenant,r['task']);continue
                    if a['status']!='approved':
                        c.execute("UPDATE p_steps SET status='waiting_approval' WHERE id=?",(r['id'],));self._refresh(c,tenant,r['task']);continue
                    try:
                        required=('owner',) if self.policy(tenant,r['agent']).get('approver_role')=='owner' else ('owner','operator')
                        self.require_authority(c,tenant,'approval',a['actor'],required)
                    except Forbidden:
                        c.execute("UPDATE p_steps SET status='failed',error='approver_revoked' WHERE id=?",(r['id'],))
                        self._refresh(c,tenant,r['task']);continue
                    c.execute("UPDATE p_approvals SET status='consumed' WHERE step=?",(r['id'],))
                token=uuid.uuid4().hex
                c.execute("UPDATE p_steps SET status='running',claim=?,worker=?,lease=?,attempts=attempts+1 WHERE id=?",(token,worker,now+lease_seconds,r['id']))
                self.audit(c,tenant,r['task'],'step.claimed',worker,{'step':r['id']})
                self._refresh(c,tenant,r['task'])
                r.update(claim=token,worker=worker,lease=now+lease_seconds,args=json.loads(r['args']))
                return r
            return None

    def finish(self,tenant,sid,token,result,status='succeeded',error=''):
        if status not in {'succeeded','failed','uncertain'}: raise ValueError('Invalid result status')
        if len(encode(result))>100_000: raise ValueError('Result too large')
        with self.tx() as c:
            r=c.execute("SELECT * FROM p_steps WHERE tenant=? AND id=? AND claim=? AND status='running' AND lease>?",(tenant,sid,token,self.clock())).fetchone()
            if not r: raise Conflict('Stale claim or expired lease')
            c.execute("UPDATE p_steps SET status=?,result=?,error=?,claim='',lease=0 WHERE id=?",(status,encode(result),error[:200],sid))
            self.audit(c,tenant,r['task'],'step.'+status,r['worker'],{'step':sid,'error':error[:200]})
            self._refresh(c,tenant,r['task'])

    def tick(self,tenant,worker='cloud'):
        step=self.claim(tenant,worker)
        if not step:return False
        spec=self.registry.get(step['tool'])
        # Last local dispatch fence; an already dispatched network operation cannot be recalled.
        try:
            if not self.dispatch_allowed(tenant,step):return False
        except Forbidden:
            # Fence failed before dispatch: no provider call. Existing freeze may already have retired the claim.
            try:self.finish(tenant,step['id'],step['claim'],{},'failed','authority_revoked_before_dispatch')
            except Conflict:pass
            return False
        try:
            result=spec.handler(self,tenant,step['agent'],step['args'],step['id'])
        except DeliveryRejected as e:
            # The provider answered and did nothing: a definite failure, whatever the
            # tool's externality. The reason is SAFE_REASON-bounded by construction.
            self.finish(tenant,step['id'],step['claim'],{},'failed','DeliveryRejected: '+e.reason)
        except Exception as e:
            # Do not disclose provider URLs, tokens, arguments or exception messages.
            status='uncertain' if spec.external and spec.risk!='read' else 'failed'
            self.finish(tenant,step['id'],step['claim'],{},status,type(e).__name__)
        else:
            self.finish(tenant,step['id'],step['claim'],result)
        return True

    def cancel(self,tenant,tid,actor):
        with self.tx() as c:
            # Stopping work remains possible while frozen, never after revocation.
            self.require_authority(c,tenant,'web',actor,('owner','operator'))
            r=c.execute('SELECT status FROM p_tasks WHERE tenant=? AND id=?',(tenant,tid)).fetchone()
            if not r:raise NotFound('Task not found')
            if r['status'] in {'succeeded','failed','cancelled'}:raise Conflict('Terminal task')
            c.execute("UPDATE p_steps SET status=CASE WHEN status='running' THEN 'uncertain' ELSE 'cancelled' END,claim='' WHERE tenant=? AND task=? AND status IN ('queued','waiting_approval','running')",(tenant,tid))
            c.execute("UPDATE p_approvals SET status='rejected' WHERE tenant=? AND step IN (SELECT id FROM p_steps WHERE task=?) AND status IN ('pending','approved')",(tenant,tid))
            self.audit(c,tenant,tid,'task.cancel_requested',actor)
            self._refresh(c,tenant,tid)

    def reconcile(self,tenant,sid,actor,role,outcome,evidence):
        if role not in {'owner','super-admin'}:raise Forbidden('Owner required')
        if (outcome not in {'succeeded','failed'} or not isinstance(evidence,str)
                or not evidence.strip() or len(evidence)>500):raise ValueError('Evidence required, maximum 500 characters')
        with self.tx() as c:
            self.require_authority(c,tenant,'web',actor,('owner',))
            r=c.execute("SELECT task FROM p_steps WHERE tenant=? AND id=? AND status='uncertain'",(tenant,sid)).fetchone()
            if not r:raise Conflict('Step is not uncertain')
            c.execute('UPDATE p_steps SET status=?,error=? WHERE tenant=? AND id=?',(outcome,'operator_reconciled',tenant,sid))
            self.audit(c,tenant,r['task'],'step.reconciled',actor,{'step':sid,'outcome':outcome,'evidence':evidence[:500]})
            self._refresh(c,tenant,r['task'])

    def freeze(self,tenant,stopped,actor):
        if type(stopped) is not bool:raise ValueError('stopped must be a boolean')
        with self.tx() as c:
            self.require_authority(c,tenant,'web',actor,('owner',))
            c.execute('INSERT INTO p_freeze VALUES(?,?) ON CONFLICT(tenant) DO UPDATE SET stopped=excluded.stopped',(tenant,int(stopped)))
            if stopped:
                active=c.execute("SELECT DISTINCT task FROM p_steps WHERE tenant=? AND status='running'",(tenant,)).fetchall()
                c.execute("UPDATE p_steps SET status='uncertain',claim='',error='tenant_frozen_during_execution' WHERE tenant=? AND status='running'",(tenant,))
                for r in active:self._refresh(c,tenant,r['task'])
            self.audit(c,tenant,'','tenant.freeze' if stopped else 'tenant.resume',actor)

    def device(self,tenant,device,revoked=False,*,actor=''):
        if not isinstance(device,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',device):
            raise ValueError('Invalid device identity')
        if type(revoked) is not bool:raise ValueError('revoked must be a boolean')
        with self.tx() as c:
            # Authorization, freeze check, generation change and audit share one
            # write transaction. The HTTP layer must not split these operations.
            self.require_authority(c,tenant,'web',actor,('owner',))
            if not revoked:self.require_active(c,tenant)
            affected=c.execute("SELECT DISTINCT task FROM p_steps WHERE tenant=? AND device=? AND status IN ('queued','waiting_approval','running')",(tenant,device)).fetchall()
            c.execute('''UPDATE p_approvals SET status='rejected',actor=?,decided=?
              WHERE tenant=? AND status IN ('pending','approved') AND step IN
              (SELECT id FROM p_steps WHERE tenant=? AND device=?
               AND status IN ('queued','waiting_approval','running'))''',
              (actor,self.clock(),tenant,tenant,device))
            c.execute("UPDATE p_steps SET status=CASE WHEN status='running' THEN 'uncertain' ELSE 'cancelled' END,claim='',lease=0,error='device_enrollment_changed' WHERE tenant=? AND device=? AND status IN ('queued','waiting_approval','running')",(tenant,device))
            for row in affected:self._refresh(c,tenant,row['task'])
            c.execute('INSERT INTO p_devices(tenant,id,revoked) VALUES(?,?,?) ON CONFLICT(tenant,id) DO UPDATE SET revoked=excluded.revoked,generation=generation+1',(tenant,device,int(revoked)))
            generation=c.execute('SELECT generation FROM p_devices WHERE tenant=? AND id=?',(tenant,device)).fetchone()['generation']
            self.audit(c,tenant,'','device.revoked' if revoked else 'device.enrolled',actor,
                       {'device':device,'generation':generation})
            return generation

    def accept_event(self,tenant,channel,key,payload):
        for value in (tenant,channel,key):
            if not isinstance(value,str) or not value or len(value)>256:raise ValueError('Invalid event identity')
        if not isinstance(payload,dict):raise ValueError('Event payload must be an object')
        if len(encode(payload))>20000:raise ValueError('Event too large')
        fp=digest(payload)
        with self.tx() as c:
            self.require_authority(c,tenant,channel,payload.get('sender',''))
            r=c.execute('SELECT * FROM p_events WHERE tenant=? AND channel=? AND event_key=?',(tenant,channel,key)).fetchone()
            if r:
                if r['fingerprint']!=fp:raise Conflict('Conflicting event replay')
                return {'ok':True,'duplicate':True,'status':r['status'],**json.loads(r['result'])}
            self.require_active(c,tenant)
            pending=c.execute("SELECT count(*) n FROM p_events WHERE tenant=? AND status IN ('pending','processing')",(tenant,)).fetchone()['n']
            day=int(self.clock()//86400)
            quota=c.execute('SELECT count FROM p_quota WHERE tenant=? AND day=?',(tenant,day)).fetchone()
            if pending>=1000 or (quota and quota['count']>=10000):raise RateLimited('Tenant inbox quota exhausted')
            c.execute('INSERT INTO p_quota VALUES(?,?,1) ON CONFLICT(tenant,day) DO UPDATE SET count=count+1',(tenant,day))
            c.execute('INSERT INTO p_events(tenant,channel,event_key,fingerprint,payload) VALUES(?,?,?,?,?)' ,(tenant,channel,key,fp,encode(payload)))
            return {'ok':True,'status':'accepted'}

    def process_event(self,tenant,planner):
        token=uuid.uuid4().hex
        with self.tx() as c:
            frozen=c.execute('SELECT stopped FROM p_freeze WHERE tenant=?',(tenant,)).fetchone()
            if frozen and frozen['stopped']:return False
            r=c.execute("SELECT * FROM p_events WHERE tenant=? AND (status='pending' OR (status='processing' AND lease<=?)) ORDER BY rowid LIMIT 1",(tenant,self.clock())).fetchone()
            if not r:return False
            r=dict(r)
            c.execute("UPDATE p_events SET status='processing',claim=?,lease=? WHERE tenant=? AND channel=? AND event_key=?",(token,self.clock()+120,tenant,r['channel'],r['event_key']))
        try:
            # Stable task ID key recovers crash between submit and event completion.
            with self.read() as c:
                old=c.execute('SELECT id FROM p_tasks WHERE tenant=? AND channel=? AND event_key=?',(tenant,r['channel'],r['event_key'])).fetchone()
                # A recorded conversation turn is this event's outcome too: re-planning
                # it after a lease expiry could route it to a one-shot plan and reply twice.
                turn=c.execute('SELECT 1 FROM p_conversation_turns WHERE tenant=? AND channel=? AND event_key=?',(tenant,r['channel'],r['event_key'])).fetchone()
            if old:result={'task_id':old['id']}
            elif turn:result={'turn':r['event_key']}
            else:
                payload=json.loads(r['payload'])
                with self.read() as c:self.require_authority(c,tenant,r['channel'],payload.get('sender',''))
                plan=planner(tenant,r['channel'],payload)
                if 'steps' in plan:
                    result={'task_id':self.submit(tenant,r['channel'],r['event_key'],plan['agent'],plan['steps'],payload.get('sender','event'))}
                else:
                    # No steps: the planner chose a conversation turn. The turn is
                    # recorded here and advanced by conversation.ConversationTurns.
                    from .conversation import record_turn
                    record_turn(self,tenant,r['channel'],r['event_key'],plan['agent'],payload)
                    result={'turn':r['event_key']}
            status='done';error=''
        except Exception as e:
            result={};status='failed';error=event_error(e)
        with self.tx() as c:
            c.execute("UPDATE p_events SET status=?,result=?,error=?,claim='',lease=0 WHERE tenant=? AND channel=? AND event_key=? AND claim=?",(status,encode(result),error,tenant,r['channel'],r['event_key'],token))
        return True

    def retry_event(self,tenant,channel,key,actor):
        with self.tx() as c:
            self.require_active(c,tenant)
            # The caller is a control-plane user, not the original channel sender.
            self.require_authority(c,tenant,'web',actor,('owner','operator'))
            event=c.execute('SELECT payload FROM p_events WHERE tenant=? AND channel=? AND event_key=?',
                            (tenant,channel,key)).fetchone()
            if event:
                self.require_authority(c,tenant,channel,json.loads(event['payload']).get('sender',''))
            n=c.execute("UPDATE p_events SET status='pending',error='' WHERE tenant=? AND channel=? AND event_key=? AND status='failed'",(tenant,channel,key)).rowcount
            if not n:raise Conflict('Only failed events can be retried')
            self.audit(c,tenant,'','event.retry',actor,{'channel':channel,'key':key})

    def schedule(self,tenant,sid,agent,steps,interval_seconds,actor='scheduler'):
        if type(interval_seconds) is not int or not 60<=interval_seconds<=31_536_000:
            raise ValueError('Interval must be an integer within bounds')
        self._validated(tenant,agent,steps)
        with self.tx() as c:
            self.require_active(c,tenant)
            self.require_authority(c,tenant,'cron',actor,('owner',))
            c.execute('INSERT INTO p_schedule_owners VALUES(?,?,?) ON CONFLICT(tenant,id) DO UPDATE SET actor=excluded.actor',(tenant,sid,actor))
            c.execute('INSERT INTO p_schedules VALUES(?,?,?,?,?,?,1) ON CONFLICT(tenant,id) DO UPDATE SET agent=excluded.agent,steps=excluded.steps,interval_seconds=excluded.interval_seconds,enabled=1',(tenant,sid,agent,encode(steps),interval_seconds,self.clock()+interval_seconds))

    def run_schedules(self,tenant):
        with self.read() as c:
            frozen=c.execute('SELECT stopped FROM p_freeze WHERE tenant=?',(tenant,)).fetchone()
            if frozen and frozen['stopped']:return 0
            rows=[dict(r) for r in c.execute('SELECT * FROM p_schedules WHERE tenant=? AND enabled=1 AND next_due<=?',(tenant,self.clock()))]
        submitted=0
        for r in rows:
            with self.read() as c:
                owner=c.execute('SELECT actor FROM p_schedule_owners WHERE tenant=? AND id=?',(tenant,r['id'])).fetchone()
            actor=owner['actor'] if owner else 'scheduler'
            try:self.submit(tenant,'cron',r['id']+':'+str(r['next_due']),r['agent'],json.loads(r['steps']),actor)
            except Forbidden:
                with self.tx() as c:
                    c.execute('UPDATE p_schedules SET enabled=0 WHERE tenant=? AND id=?',(tenant,r['id']))
                    self.audit(c,tenant,'','schedule.authority_denied',actor,{'schedule':r['id']})
                continue
            submitted+=1
            with self.tx() as c:
                c.execute('UPDATE p_schedules SET next_due=? WHERE tenant=? AND id=? AND next_due=?',(self.clock()+r['interval_seconds'],tenant,r['id'],r['next_due']))
        return submitted
