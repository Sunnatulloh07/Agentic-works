-- Frozen schema extracted from original v0.3.6 ZIP, before v0.3.7 development.

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
 UNIQUE(tenant,request_key));
CREATE TABLE IF NOT EXISTS p_agent_turns(
 tenant TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES p_agent_runs(id),
 position INTEGER NOT NULL, task TEXT NOT NULL REFERENCES p_tasks(id),
 action_fingerprint TEXT NOT NULL, PRIMARY KEY(run_id,position), UNIQUE(tenant,task));
CREATE INDEX IF NOT EXISTS p_agent_runs_pending ON p_agent_runs(tenant,status,created);
CREATE INDEX IF NOT EXISTS p_steps_queue ON p_steps(tenant,status,position);
CREATE INDEX IF NOT EXISTS p_tasks_tenant ON p_tasks(tenant,created);
CREATE INDEX IF NOT EXISTS p_audit_tenant ON p_audit(tenant,id);


CREATE TABLE IF NOT EXISTS p_budget_settings(
 tenant TEXT PRIMARY KEY, currency TEXT NOT NULL, limit_micro INTEGER NOT NULL,
 max_inflight INTEGER NOT NULL, generation INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS p_budget_accounts(
 tenant TEXT NOT NULL, period TEXT NOT NULL, spent_micro INTEGER NOT NULL DEFAULT 0,
 reserved_micro INTEGER NOT NULL DEFAULT 0, inflight INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant,period));
CREATE TABLE IF NOT EXISTS p_budget_reservations(
 tenant TEXT NOT NULL, id TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 period TEXT NOT NULL, amount_micro INTEGER NOT NULL, actual_micro INTEGER,
 status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY(tenant,id), UNIQUE(tenant,request_key));
CREATE INDEX IF NOT EXISTS p_budget_pending ON p_budget_reservations(tenant,status,created);


CREATE TABLE IF NOT EXISTS p_kb_collections(
 tenant TEXT NOT NULL,id TEXT NOT NULL,model TEXT NOT NULL DEFAULT '',dimension INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS p_kb_acl(
 tenant TEXT NOT NULL,collection TEXT NOT NULL,agent TEXT NOT NULL,
 PRIMARY KEY(tenant,collection,agent),FOREIGN KEY(tenant,collection) REFERENCES p_kb_collections(tenant,id));
CREATE TABLE IF NOT EXISTS p_kb_documents(
 tenant TEXT NOT NULL,collection TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,
 title TEXT NOT NULL,source_url TEXT NOT NULL,content_hash TEXT NOT NULL,updated REAL NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant,collection,id),FOREIGN KEY(tenant,collection) REFERENCES p_kb_collections(tenant,id));
CREATE TABLE IF NOT EXISTS p_kb_chunks(
 tenant TEXT NOT NULL,collection TEXT NOT NULL,document TEXT NOT NULL,id TEXT NOT NULL,
 position INTEGER NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,text TEXT NOT NULL,vector TEXT NOT NULL DEFAULT '[]',
 PRIMARY KEY(tenant,collection,id),
 FOREIGN KEY(tenant,collection,document) REFERENCES p_kb_documents(tenant,collection,id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS p_kb_chunk_scope ON p_kb_chunks(tenant,collection,document,position);

INSERT OR IGNORE INTO p_migrations VALUES(1,1000);
INSERT OR IGNORE INTO p_migrations VALUES(2,1000);
INSERT OR IGNORE INTO p_migrations VALUES(3,1000);
INSERT OR IGNORE INTO p_migrations VALUES(4,1000);
INSERT OR IGNORE INTO p_migrations VALUES(5,1000);
