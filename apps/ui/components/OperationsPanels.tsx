'use client';
import {useState} from 'react';
import {type SessionClient} from '../lib/session-client.mjs';
import {canReadInbox} from '../lib/shop-client.mjs';

/**
 * Panels for the tenant routes that existed only in the backend.
 *
 * Twenty platform routes had no control surface: re-engagement, briefing, escalation,
 * supervisor routing, recurring schedules and the step-reconcile path. The last one was
 * the sharpest, because the task timeline *instructs* the operator to finish an
 * uncertain step "through the reconcile API" and offered no way to do it -- the only
 * documented recovery path was documented and not built.
 *
 * Every panel here is a thin caller. Nothing decides policy: owner-only routes stay
 * owner-only, the refusal comes from the API, and each panel says in the UI what it
 * does NOT prove. Labels are Uzbek to match the rest of the dashboard.
 */

type Agent = {id: string; name: string; tools: string[]};
type Props = {client: SessionClient; tenant: string; role: string; frozen: boolean; agents: Agent[]};

const panel = {background: '#111c2e', border: '1px solid #233149', borderRadius: 12, padding: 20, marginBottom: 16};
const control = {display: 'block', background: '#0b1220', color: '#e2e8f0', border: '1px solid #475569', padding: 8, margin: '8px 0', borderRadius: 6, maxWidth: '100%', boxSizing: 'border-box' as const};
const button = {...control, display: 'inline-block', marginRight: 8, cursor: 'pointer'};
const pre = {whiteSpace: 'pre-wrap' as const, overflowWrap: 'anywhere' as const, fontSize: 12, background: '#0b1220', padding: 12, borderRadius: 6};
const row = {borderTop: '1px solid #334155', paddingTop: 12, marginTop: 12};

/** One place where every panel gets its busy/error/message handling, so no panel can
 * forget to clear a stale success message before a failing call. */
function useOps() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('');
    try { await fn(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Xato'); }
    finally { setBusy(false); }
  }
  return {busy, error, message, setMessage, run};
}

function Status({busy, error, message}: {busy: boolean; error: string; message: string}) {
  return <>
    {error && <p role="alert" style={{color: '#fca5a5'}}>{error}</p>}
    {message && <p role="status" style={{color: '#86efac'}}>{message}</p>}
    {busy && <p role="status">Bajarilmoqda...</p>}
  </>;
}

function AgentSelect({value, onChange, agents, label}: {value: string; onChange: (v: string) => void; agents: Agent[]; label: string}) {
  return <label>{label}
    <select style={control} value={value} onChange={e => onChange(e.target.value)}>
      <option value="">Tanlang</option>
      {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
    </select>
  </label>;
}

function Ledger({entries, total, truncated}: {entries: unknown[]; total: number; truncated: boolean}) {
  if (!entries.length) return <p>Jurnal yozuvi yo‘q.</p>;
  return <>
    <p>{entries.length} yozuv ko‘rsatilgan · jami {total}{truncated && ' · sahifa kesilgan, davomi bor'}</p>
    <pre style={pre}>{JSON.stringify(entries, null, 2)}</pre>
  </>;
}

/** Numeric field with the API's own minimum and maximum, so the browser refuses what
 * the server would refuse rather than reporting a 422 after a round trip. */
function NumericField({label, value, onChange, min, max, hint}: {label: string; value: string; onChange: (v: string) => void; min: number; max: number; hint?: string}) {
  return <label>{label}{hint && <small style={{color: '#94a3b8'}}> · {hint}</small>}
    <input style={control} type="number" min={min} max={max} step={1} value={value} onChange={e => onChange(e.target.value)}/>
  </label>;
}

const num = (v: string) => globalThis.Number(v);

// ---------------------------------------------------------------- re-engagement

export function ReengagementPanel({client, tenant, role, frozen, agents}: Props) {
  const base = `/platform/${encodeURIComponent(tenant)}/reengagement`;
  const ops = useOps();
  const [policies, setPolicies] = useState<{policy: string; enabled: boolean}[]>([]);
  const [policy, setPolicy] = useState('default');
  const [ledger, setLedger] = useState<{entries: unknown[]; total: number; truncated: boolean} | null>(null);
  const [agent, setAgent] = useState('');
  const [connection, setConnection] = useState('crm');
  const [inactive, setInactive] = useState('120');
  const [cooldown, setCooldown] = useState('86400');
  const [attempts, setAttempts] = useState('2');
  const [perCycle, setPerCycle] = useState('5');
  const isOwner = role === 'owner';
  async function load() { setPolicies((await client.request<{policies: typeof policies}>(base)).policies); }
  async function loadLedger() {
    setLedger(await client.request<{entries: unknown[]; total: number; truncated: boolean}>(`${base}/${encodeURIComponent(policy)}/ledger`));
  }
  return <section style={panel}>
    <h2>Qayta aloqa (re-engagement)</h2>
    <p>Faolsiz mijozlarga avtomatik qayta aloqa. Bu <strong>haqiqiy mijozga</strong> xabar
      yuboradigan jadval, shuning uchun sozlash faqat owner uchun. Jurnal — yuborilgan
      urinishlarning dalili, yetkazilganligining kafolati emas.</p>
    <Status {...ops}/>
    <button style={button} disabled={ops.busy} onClick={() => ops.run(load)}>Siyosatlar ro‘yxati</button>
    {policies.map(p => <button key={p.policy} style={button} disabled={ops.busy}
      onClick={() => { setPolicy(p.policy); ops.run(loadLedger); }}>
      {p.policy} · {p.enabled ? 'faol' : 'o‘chirilgan'}
    </button>)}
    <label>Siyosat nomi<input style={control} value={policy} maxLength={128} onChange={e => setPolicy(e.target.value)}/></label>
    <button style={button} disabled={ops.busy} onClick={() => ops.run(loadLedger)}>Jurnalni yuklash</button>
    {ledger && <Ledger {...ledger}/>}
    {isOwner && <>
      <h3>Owner: siyosatni sozlash</h3>
      <AgentSelect label="Agent" value={agent} onChange={setAgent} agents={agents}/>
      <label>Connector<input style={control} value={connection} maxLength={128} onChange={e => setConnection(e.target.value)}/></label>
      <NumericField label="Faolsizlik, daqiqa" min={1} max={20160} value={inactive} onChange={setInactive} hint="1–20160 (14 kun)"/>
      <NumericField label="Cooldown, soniya" min={300} max={2592000} value={cooldown} onChange={setCooldown}/>
      <NumericField label="Maksimal urinish" min={1} max={10} value={attempts} onChange={setAttempts}/>
      <NumericField label="Bir siklda maksimal" min={1} max={20} value={perCycle} onChange={setPerCycle}/>
      <button style={button} disabled={ops.busy || frozen || !agent || !connection.trim()}
        onClick={() => ops.run(async () => {
          await client.request(`${base}/${encodeURIComponent(policy)}`, {
            agent, connection, inactive_minutes: num(inactive), cooldown_seconds: num(cooldown),
            max_attempts: num(attempts), max_per_cycle: num(perCycle), enabled: true}, 'PUT');
          ops.setMessage('Siyosat saqlandi.'); await load();
        })}>Saqlash</button>
      <button style={button} disabled={ops.busy || frozen}
        onClick={() => ops.run(async () => {
          const r = await client.request<{changed: number}>(`${base}/${encodeURIComponent(policy)}/sync`, {});
          ops.setMessage(`Jurnal sinxronlandi: ${r.changed} yozuv o‘zgardi.`); await loadLedger();
        })}>Jurnalni sinxronlash</button>
    </>}
  </section>;
}

// ------------------------------------------------------------------- briefing

export function BriefingPanel({client, tenant, role, frozen, agents}: Props) {
  const base = `/platform/${encodeURIComponent(tenant)}/briefing`;
  const ops = useOps();
  const [schedules, setSchedules] = useState<{schedule: string; enabled: boolean}[]>([]);
  const [schedule, setSchedule] = useState('daily');
  const [ledger, setLedger] = useState<{entries: unknown[]; total: number; truncated: boolean} | null>(null);
  const [agent, setAgent] = useState('');
  const [recipient, setRecipient] = useState('');
  const [connection, setConnection] = useState('crm');
  const [hour, setHour] = useState('8');
  const [minute, setMinute] = useState('0');
  const [sections, setSections] = useState('[{"title":"Kunlik xulosa"}]');
  const [title, setTitle] = useState('Kunlik brifing');
  const isOwner = role === 'owner';
  async function load() { setSchedules((await client.request<{schedules: typeof schedules}>(base)).schedules); }
  async function loadLedger() {
    setLedger(await client.request<{entries: unknown[]; total: number; truncated: boolean}>(`${base}/${encodeURIComponent(schedule)}/ledger`));
  }
  return <section style={panel}>
    <h2>Brifing</h2>
    <p>Belgilangan vaqtda boshqaruv bo‘limiga xulosa yuboradi. Bo‘limlar manbadan
      o‘qiladi; manba bo‘sh bo‘lsa brifing ham bo‘sh bo‘ladi — bu nosozlik emas.</p>
    <Status {...ops}/>
    <button style={button} disabled={ops.busy} onClick={() => ops.run(load)}>Jadvallar ro‘yxati</button>
    {schedules.map(s => <button key={s.schedule} style={button} disabled={ops.busy}
      onClick={() => { setSchedule(s.schedule); ops.run(loadLedger); }}>
      {s.schedule} · {s.enabled ? 'faol' : 'o‘chirilgan'}
    </button>)}
    <label>Jadval nomi<input style={control} value={schedule} maxLength={128} onChange={e => setSchedule(e.target.value)}/></label>
    <button style={button} disabled={ops.busy} onClick={() => ops.run(loadLedger)}>Jurnalni yuklash</button>
    {ledger && <Ledger {...ledger}/>}
    {isOwner && <>
      <h3>Owner: jadvalni sozlash</h3>
      <AgentSelect label="Agent" value={agent} onChange={setAgent} agents={agents}/>
      <label>Qabul qiluvchi<input style={control} value={recipient} maxLength={128} onChange={e => setRecipient(e.target.value)} placeholder="chat yoki kanal identifikatori"/></label>
      <label>Connector<input style={control} value={connection} maxLength={128} onChange={e => setConnection(e.target.value)}/></label>
      <label>Sarlavha<input style={control} value={title} maxLength={120} onChange={e => setTitle(e.target.value)}/></label>
      <NumericField label="Soat" min={0} max={23} value={hour} onChange={setHour} hint="0–23, mahalliy"/>
      <NumericField label="Daqiqa" min={0} max={59} value={minute} onChange={setMinute}/>
      <label>Bo‘limlar (JSON massiv)<textarea style={{...control, width: '100%', fontFamily: 'monospace'}} rows={4}
        value={sections} onChange={e => setSections(e.target.value)}/></label>
      <button style={button} disabled={ops.busy || frozen || !agent || !recipient.trim()}
        onClick={() => ops.run(async () => {
          await client.request(`${base}/${encodeURIComponent(schedule)}`, {
            agent, recipient, connection, sections: JSON.parse(sections),
            hour: num(hour), minute: num(minute), title, enabled: true}, 'PUT');
          ops.setMessage('Jadval saqlandi.'); await load();
        })}>Saqlash</button>
    </>}
  </section>;
}

// ------------------------------------------------------------------ escalation

export function EscalationPanel({client, tenant, role, frozen, agents}: Props) {
  const base = `/platform/${encodeURIComponent(tenant)}/escalation`;
  const ops = useOps();
  const [schedules, setSchedules] = useState<{schedule: string; enabled: boolean}[]>([]);
  const [schedule, setSchedule] = useState('default');
  const [ledger, setLedger] = useState<{entries: unknown[]; total: number; truncated: boolean} | null>(null);
  const [agent, setAgent] = useState('');
  const [recipient, setRecipient] = useState('');
  const [cooldown, setCooldown] = useState('86400');
  const [perCycle, setPerCycle] = useState('10');
  const [maxAge, setMaxAge] = useState('30');
  const [title, setTitle] = useState('');
  const isOwner = role === 'owner';
  async function load() { setSchedules((await client.request<{schedules: typeof schedules}>(base)).schedules); }
  async function loadLedger() {
    setLedger(await client.request<{entries: unknown[]; total: number; truncated: boolean}>(`${base}/${encodeURIComponent(schedule)}/ledger`));
  }
  return <section style={panel}>
    <h2>Eskalatsiya</h2>
    <p>Haqiqiy odamlar haqida menejerga avtomatik eskalatsiya. Owner bo‘lmagan
      foydalanuvchi uni boshqa manzilga qarata olmaydi. Cooldown bir hodisaning
      takror yuborilishini to‘xtatadi.</p>
    <Status {...ops}/>
    <button style={button} disabled={ops.busy} onClick={() => ops.run(load)}>Jadvallar ro‘yxati</button>
    {schedules.map(s => <button key={s.schedule} style={button} disabled={ops.busy}
      onClick={() => { setSchedule(s.schedule); ops.run(loadLedger); }}>
      {s.schedule} · {s.enabled ? 'faol' : 'o‘chirilgan'}
    </button>)}
    <label>Jadval nomi<input style={control} value={schedule} maxLength={128} onChange={e => setSchedule(e.target.value)}/></label>
    <button style={button} disabled={ops.busy} onClick={() => ops.run(loadLedger)}>Jurnalni yuklash</button>
    {ledger && <Ledger {...ledger}/>}
    {isOwner && <>
      <h3>Owner: jadvalni sozlash</h3>
      <AgentSelect label="Agent" value={agent} onChange={setAgent} agents={agents}/>
      <label>Qabul qiluvchi<input style={control} value={recipient} maxLength={128} onChange={e => setRecipient(e.target.value)}/></label>
      <label>Sarlavha<input style={control} value={title} maxLength={120} onChange={e => setTitle(e.target.value)}/></label>
      <NumericField label="Cooldown, soniya" min={300} max={2592000} value={cooldown} onChange={setCooldown}/>
      <NumericField label="Bir siklda maksimal" min={1} max={50} value={perCycle} onChange={setPerCycle}/>
      <NumericField label="Maksimal yosh, kun" min={1} max={365} value={maxAge} onChange={setMaxAge}/>
      <button style={button} disabled={ops.busy || frozen || !agent || !recipient.trim()}
        onClick={() => ops.run(async () => {
          await client.request(`${base}/${encodeURIComponent(schedule)}`, {
            agent, recipient, title, cooldown_seconds: num(cooldown),
            max_per_cycle: num(perCycle), max_age_days: num(maxAge), enabled: true}, 'PUT');
          ops.setMessage('Jadval saqlandi.'); await load();
        })}>Saqlash</button>
      <button style={button} disabled={ops.busy || frozen}
        onClick={() => ops.run(async () => {
          await client.request(`${base}/${encodeURIComponent(schedule)}/disable?reason=operator_disabled`, {});
          ops.setMessage('Jadval o‘chirildi.'); await load();
        })}>O‘chirish (sabab: operator)</button>
    </>}
  </section>;
}

// ------------------------------------------------------------------ supervisor

export function SupervisorPanel({client, tenant, role, frozen, agents}: Props) {
  const base = `/platform/${encodeURIComponent(tenant)}/supervisor`;
  const ops = useOps();
  const [sections, setSections] = useState<{section: string; agent: string; enabled: boolean}[]>([]);
  const [history, setHistory] = useState<{routes: unknown[]; total: number; truncated: boolean} | null>(null);
  const [section, setSection] = useState('sales');
  const [agent, setAgent] = useState('');
  const [keywords, setKeywords] = useState('');
  const [title, setTitle] = useState('');
  const [question, setQuestion] = useState('');
  const [targetSection, setTargetSection] = useState('');
  const isOwner = role === 'owner';
  async function load() { setSections((await client.request<{sections: typeof sections}>(base)).sections); }
  return <section style={panel}>
    <h2>Supervisor yo‘naltirish</h2>
    <p>Menejer savolini bo‘limga yo‘naltiradi. Bo‘lim e’lon qilish — yo‘naltirish
      vakolatini o‘zgartirish, shuning uchun owner ishi. Yo‘naltirish esa oddiy
      operatsion amal: u haqiqiy agent run sarflaydi.</p>
    <Status {...ops}/>
    <button style={button} disabled={ops.busy} onClick={() => ops.run(load)}>Bo‘limlar ro‘yxati</button>
    {sections.map(s => <p key={s.section}>{s.section} → {s.agent} · {s.enabled ? 'faol' : 'o‘chirilgan'}</p>)}
    <button style={button} disabled={ops.busy} onClick={() => ops.run(async () => {
      setHistory(await client.request<{routes: unknown[]; total: number; truncated: boolean}>(base + '/history'));
    })}>Yo‘naltirish tarixi</button>
    {history && <Ledger entries={history.routes} total={history.total} truncated={history.truncated}/>}
    <h3>Savolni yo‘naltirish</h3>
    <label>Savol<textarea style={{...control, width: '100%'}} rows={3} maxLength={2000}
      value={question} onChange={e => setQuestion(e.target.value)}/></label>
    <label>Bo‘lim (bo‘sh bo‘lsa kalit so‘zlar bo‘yicha tanlanadi)
      <input style={control} value={targetSection} maxLength={64} onChange={e => setTargetSection(e.target.value)}/></label>
    <button style={button} disabled={ops.busy || frozen || !question.trim()}
      onClick={() => ops.run(async () => {
        // The route refuses a request without this header; a fresh UUID per click is
        // what makes a retry after a network failure safe rather than a second run.
        await client.request(base + '/route',
          {question, section: targetSection}, 'POST', {'Idempotency-Key': crypto.randomUUID()});
        ops.setMessage('Yo‘naltirildi.'); setQuestion('');
        setHistory(await client.request<{routes: unknown[]; total: number; truncated: boolean}>(base + '/history'));
      })}>Yo‘naltirish</button>
    {isOwner && <>
      <h3>Owner: bo‘lim e’lon qilish</h3>
      <label>Bo‘lim nomi<input style={control} value={section} maxLength={64} onChange={e => setSection(e.target.value)}/></label>
      <AgentSelect label="Agent" value={agent} onChange={setAgent} agents={agents}/>
      <label>Sarlavha<input style={control} value={title} maxLength={120} onChange={e => setTitle(e.target.value)}/></label>
      <label>Kalit so‘zlar (vergul bilan)<input style={control} value={keywords} maxLength={400}
        onChange={e => setKeywords(e.target.value)}/></label>
      <button style={button} disabled={ops.busy || frozen || !agent || !section.trim()}
        onClick={() => ops.run(async () => {
          await client.request(`${base}/${encodeURIComponent(section)}`, {
            agent, title, keywords: keywords.split(',').map(k => k.trim()).filter(Boolean).slice(0, 20), enabled: true}, 'PUT');
          ops.setMessage('Bo‘lim saqlandi.'); await load();
        })}>Saqlash</button>
    </>}
  </section>;
}

// ------------------------------------------------------------------- schedules

export function SchedulesPanel({client, tenant, role, frozen, agents}: Props) {
  const ops = useOps();
  const [agent, setAgent] = useState('');
  const [key, setKey] = useState('');
  const [interval, setInterval] = useState('3600');
  const [steps, setSteps] = useState(JSON.stringify([{tool: 'reports.summary', args: {}}], null, 2));
  if (role !== 'owner') return <section style={panel}><h2>Takrorlanuvchi jadval</h2>
    <p>Bu bo‘lim faqat owner uchun.</p></section>;
  return <section style={panel}>
    <h2>Takrorlanuvchi jadval</h2>
    <p>Vazifani belgilangan oraliqda qayta yaratadi. Bu <strong>yangi run</strong>
      yaratadi — oldingi run natijasini takrorlamaydi, va har bir ijro baribir approval
      qoidasiga bo‘ysunadi.</p>
    <Status {...ops}/>
    <AgentSelect label="Agent" value={agent} onChange={setAgent} agents={agents}/>
    <label>Kalit (takrorlanmas)<input style={control} value={key} maxLength={256} onChange={e => setKey(e.target.value)}/></label>
    <NumericField label="Oraliq, soniya" min={60} max={31536000} value={interval} onChange={setInterval} hint="60–31536000"/>
    <label>Qadamlar (JSON)<textarea style={{...control, width: '100%', fontFamily: 'monospace'}} rows={6}
      value={steps} onChange={e => setSteps(e.target.value)}/></label>
    <button style={button} disabled={ops.busy || frozen || !agent || !key.trim()}
      onClick={() => ops.run(async () => {
        await client.request(`/platform/${encodeURIComponent(tenant)}/schedules`, {
          agent, key, steps: JSON.parse(steps), interval_seconds: num(interval)});
        ops.setMessage('Jadval yaratildi.'); setKey('');
      })}>Jadval yaratish</button>
  </section>;
}

// ---------------------------------------------------------------------- health

type Health = {status?: string; [k: string]: unknown};

/** Operational dashboard built only from routes that already exist.
 *
 * A metrics endpoint does not exist, so this panel does not pretend to read one: it
 * reports the API's own health response and the counts the list routes already return.
 * The distinction matters -- a dashboard that implies observability it does not have is
 * worse than no dashboard, because it is trusted. */
export function MetricsPanel({client, tenant, role}: {client: SessionClient; tenant: string; role: string}) {
  const ops = useOps();
  const [health, setHealth] = useState<Health | null>(null);
  const [counts, setCounts] = useState<{tasks: number; failed: number; uncertain: number; waiting: number; inbox: number | null; runs: number} | null>(null);
  const base = `/platform/${encodeURIComponent(tenant)}`;
  return <section style={panel}>
    <h2>Holat va metrikalar</h2>
    <p>Bu <strong>health va navbat</strong> ko‘rinishi. Bu yerda metrika eksporti,
      trace, alerting yoki HA holati <strong>yo‘q</strong> — ular hali qurilmagan, va bu
      panel ular borligini ko‘rsatmaydi.</p>
    <Status {...ops}/>
    <button style={button} disabled={ops.busy} onClick={() => ops.run(async () => {
      const [t, i, r] = await Promise.all([
        client.request<{tasks: {status: string}[]}>(base + '/tasks'),
        // /inbox is owner/operator only; asking for it as another role would 403
        // and take the whole panel down with it.
        canReadInbox(role) ? client.request<{events: {status: string}[]}>(base + '/inbox') : Promise.resolve(null),
        client.request<{runs: unknown[]}>(base + '/agent-runs'),
      ]);
      setCounts({
        tasks: t.tasks.length,
        failed: t.tasks.filter(x => x.status === 'failed').length,
        uncertain: t.tasks.filter(x => x.status === 'uncertain').length,
        waiting: t.tasks.filter(x => x.status === 'waiting_approval').length,
        inbox: i ? i.events.filter(x => x.status === 'failed').length : null,
        runs: r.runs.length,
      });
    })}>Hisoblarni yig‘ish</button>
    <button style={button} disabled={ops.busy} onClick={() => ops.run(async () => {
      // /health is unauthenticated by design, so it is read through the same client
      // only for transport consistency; it carries no tenant data.
      setHealth(await client.request<Health>('/health'));
    })}>API health</button>
    {health && <pre style={pre}>{JSON.stringify(health, null, 2)}</pre>}
    {counts && <ul>
      <li>Vazifalar: {counts.tasks} (kutayotgan tasdiq: {counts.waiting})</li>
      <li>Yiqilgan: {counts.failed} · noaniq: {counts.uncertain} — noaniq holat operator qarorini kutadi</li>
      <li>Kiruvchi hodisalar (yiqilgan): {counts.inbox ?? 'bu rol uchun ko‘rinmaydi'}</li>
      <li>Agent runlari: {counts.runs}</li>
    </ul>}
    {role !== 'owner' && role !== 'operator' && <p>Hisoblar barcha rollarga ochiq; bu yerda faqat o‘qish bor.</p>}
  </section>;
}

// ------------------------------------------------------- customer sub-resources

/** Contacts, channel identities and orders for one customer.
 *
 * The customer list and the customer detail view shipped without any way to add the
 * three things a customer is actually made of. `Customer 360` showed whatever the sync
 * had produced and nothing else, so a customer created in the dashboard had no contact,
 * no channel identity and no orders -- and a customer with no channel identity can
 * never be matched to an inbound message.
 *
 * Every write here is owner/operator, because all three routes are. `verified` is a
 * deliberate checkbox rather than a default: an unverified identity is a claim, and
 * merging on an unverified claim is how two customers become one. */
export function CustomerResourcesPanel({client, tenant, role, frozen, customer, onChanged}: {
  client: SessionClient; tenant: string; role: string; frozen: boolean; customer: string; onChanged: () => Promise<void> | void;
}) {
  const ops = useOps();
  const base = `/platform/${encodeURIComponent(tenant)}/customers/${encodeURIComponent(customer)}`;
  const [type, setType] = useState('phone');
  const [value, setValue] = useState('');
  const [verified, setVerified] = useState(false);
  const [channel, setChannel] = useState('whatsapp');
  const [externalId, setExternalId] = useState('');
  const [orderId, setOrderId] = useState('');
  const [currency, setCurrency] = useState('UZS');
  const [total, setTotal] = useState('0');
  const writable = ['owner', 'operator'].includes(role) && !frozen;
  return <section style={{...row, borderColor: '#233149'}}>
    <h3>Aloqa kanallari va buyurtmalar</h3>
    <Status {...ops}/>
    <h4>Kontakt</h4>
    <label>Turi<select style={control} value={type} onChange={e => setType(e.target.value)}>
      {['phone', 'email', 'telegram', 'whatsapp'].map(t => <option key={t} value={t}>{t}</option>)}
    </select></label>
    <label>Qiymat<input style={control} maxLength={512} value={value} onChange={e => setValue(e.target.value)}/></label>
    <label><input type="checkbox" checked={verified} onChange={e => setVerified(e.target.checked)}/> Tekshirilgan</label>
    <button style={button} disabled={ops.busy || !writable || !value.trim()}
      onClick={() => ops.run(async () => {
        await client.request(base + '/contacts', {type, value: value.trim(), verified});
        setValue(''); setVerified(false); ops.setMessage('Kontakt qo‘shildi.'); await onChanged();
      })}>Kontakt qo‘shish</button>

    <h4>Kanal identity</h4>
    <p style={{fontSize: 13}}>Kanal identity — kiruvchi xabarni mijozga bog‘laydigan
      yagona narsa. Tasdiqlanmagan identity bilan birlashtirish xato birlashtirishga olib
      keladi, shuning uchun <code>verified</code> ataylab qo‘lda belgilanadi.</p>
    <label>Kanal<select style={control} value={channel} onChange={e => setChannel(e.target.value)}>
      {['whatsapp', 'telegram', 'instagram', 'phone', 'email'].map(c => <option key={c} value={c}>{c}</option>)}
    </select></label>
    <label>Tashqi ID<input style={control} maxLength={256} value={externalId} onChange={e => setExternalId(e.target.value)}/></label>
    <button style={button} disabled={ops.busy || !writable || !externalId.trim()}
      onClick={() => ops.run(async () => {
        await client.request(base + '/channel-identities', {channel, external_id: externalId.trim(), verified});
        setExternalId(''); ops.setMessage('Kanal identity qo‘shildi.'); await onChanged();
      })}>Identity qo‘shish</button>

    <h4>Buyurtma</h4>
    <p style={{fontSize: 13}}>Summa <strong>minor birlikda</strong> (tiyin). Bu yozuv
      platformada pul harakatlantirmaydi — u faqat faktni qayd etadi.</p>
    <label>Tashqi ID<input style={control} maxLength={256} value={orderId} onChange={e => setOrderId(e.target.value)}/></label>
    <label>Valyuta<input style={control} maxLength={3} value={currency}
      onChange={e => setCurrency(e.target.value.toUpperCase())}/></label>
    <label>Jami, minor<input style={control} type="number" min={0} step={1} value={total} onChange={e => setTotal(e.target.value)}/></label>
    <button style={button} disabled={ops.busy || !writable || !orderId.trim()}
      onClick={() => ops.run(async () => {
        await client.request(base + '/orders', {external_id: orderId.trim(), currency, total_minor: num(total)});
        setOrderId(''); setTotal('0'); ops.setMessage('Buyurtma qo‘shildi.'); await onChanged();
      })}>Buyurtma qo‘shish</button>
  </section>;
}

// ------------------------------------------------------------------- reconcile

/** The step-reconcile control the task timeline asked for and never had.
 *
 * `page.tsx` told the operator that an `uncertain` step is closed "through the
 * reconcile API with evidence", and shipped no way to do it. An uncertain step is
 * blocked from automatic replay on purpose, so the documented path was the ONLY path --
 * which made this the highest-value missing control in the dashboard.
 *
 * Owner-only, because the route is. Evidence is required and free text, because the
 * point is to record what the operator actually verified. */
export function ReconcileControl({client, tenant, role, step, onDone}: {
  client: SessionClient; tenant: string; role: string; step: string; onDone: () => Promise<void> | void;
}) {
  const ops = useOps();
  const [outcome, setOutcome] = useState('succeeded');
  const [evidence, setEvidence] = useState('');
  if (role !== 'owner') return <p style={{color: '#94a3b8'}}>
    Noaniq qadamni yakunlash faqat owner uchun. Tashqi tizimni tekshirib, dalil bilan
    owner yakunlaydi.</p>;
  return <div style={{...row, borderColor: '#fdba74'}}>
    <h4>Noaniq qadamni dalil bilan yakunlash</h4>
    <p style={{fontSize: 13}}>Avtomatik qayta ijro bloklangan. Tashqi tizimdagi haqiqiy
      natijani <strong>tekshirib</strong>, keyin shu yerda yozing. Bu yozuv audit
      jurnaliga tushadi.</p>
    <Status {...ops}/>
    <label>Natija<select style={control} value={outcome} onChange={e => setOutcome(e.target.value)}>
      <option value="succeeded">Bajarilgan (tashqi tizimda tasdiqlandi)</option>
      <option value="failed">Bajarilmagan</option>
    </select></label>
    <label>Tashqi dalil<input style={control} maxLength={500} value={evidence}
      onChange={e => setEvidence(e.target.value)} placeholder="Buyurtma raqami, hujjat ID, provayder javobi"/></label>
    <button style={button} disabled={ops.busy || !evidence.trim()}
      onClick={() => ops.run(async () => {
        await client.request(`/platform/${encodeURIComponent(tenant)}/steps/${encodeURIComponent(step)}/reconcile`,
          {outcome, evidence: evidence.trim()});
        setEvidence(''); ops.setMessage('Yakunlandi.'); await onDone();
      })}>Dalil bilan yakunlash</button>
  </div>;
}
