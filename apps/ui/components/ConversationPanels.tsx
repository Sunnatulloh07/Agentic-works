'use client';
import {useCallback, useEffect, useState} from 'react';
import {type SessionClient} from '../lib/session-client.mjs';
import {conversationsPath, handoffsPath, reasonLabel, releasePath, releaseRequest, replyErrorText, replyPath,
  replyRequest, takeoverActive, threadLines, threadPath, turnStatusLabel,
  type Takeover} from '../lib/conversation-client.mjs';
import {startAutoRefresh} from '../lib/shop-client.mjs';

/**
 * "Suhbatlar" (conversation list -> thread -> operator reply) and
 * "Operatorga uzatilganlar" (handoffs). Thin callers of app/shop_api.py and the
 * operator-reply endpoint; the API decides roles, these panels only avoid
 * offering a button the API would refuse.
 */

const panel = {background: '#111c2e', border: '1px solid #233149', borderRadius: 12, padding: 20, marginBottom: 16};
const button = {background: '#1e3a5f', color: '#e2e8f0', border: '1px solid #3b5273', borderRadius: 7, padding: '10px 14px', margin: 4, cursor: 'pointer'};
const row = {borderBottom: '1px solid #334155', padding: 12};
const muted = {color: '#94a3b8', fontSize: 13};
const bubbleColor: Record<string, string> = {customer: '#64748b', agent: '#3b82f6', operator: '#22c55e'};
const statusColor: Record<string, string> = {delivered: '#86efac', failed: '#fca5a5', uncertain: '#fdba74',
  delivering: '#fde68a', open: '#93c5fd', queued: '#cbd5e1', operator: '#bbf7d0'};
const when = (t: number) => new Date(t * 1000).toLocaleString();
const nowSeconds = () => Date.now() / 1000;
const badge = {background: '#14532d', color: '#bbf7d0', borderRadius: 6, padding: '2px 8px', fontSize: 12, marginLeft: 6};

/** Visible while a human holds the chat: the bot answers nothing there until `until`. */
function OperatorBadge({takeover}: {takeover: Takeover | null | undefined}) {
  if (!takeoverActive(takeover, nowSeconds())) return null;
  return <span style={badge} title={`Bot ${when(takeover!.until)} gacha javob bermaydi`}>
    Operator rejimi · {takeover!.actor}</span>;
}

type Base = {client: SessionClient; tenant: string};
export type ThreadRef = {channel: string; conversation_id: string};

function useLoader<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const refresh = useCallback(async () => {
    setBusy(true); setError('');
    try { setData(await load()); }
    catch (e) { setError(e instanceof Error ? e.message : 'Xato'); }
    finally { setBusy(false); }
  }, [load]);
  useEffect(() => { void refresh(); }, [refresh]);
  return {data, busy, error, setError, refresh};
}

function AutoRefresh({refresh}: {refresh: () => Promise<void>}) {
  const [on, setOn] = useState(false);
  useEffect(() => on ? startAutoRefresh(refresh, 10000) : undefined, [on, refresh]);
  return <label style={{...muted, marginLeft: 8}}>
    <input type="checkbox" checked={on} onChange={e => setOn(e.target.checked)}/> Har 10 soniyada yangilash
  </label>;
}

// ------------------------------------------------------------------ list

type Conversation = ThreadRef & {last_role: string; last_text: string; last_at: number;
  turn_status: string; turn_error: string; sender: string; takeover: Takeover | null};

export function ConversationsPanel({client, tenant, canReply, selected, onSelect}: Base & {
  canReply: boolean; selected: ThreadRef | null; onSelect: (ref: ThreadRef | null) => void}) {
  const load = useCallback(async () =>
    (await client.request<{conversations: Conversation[]}>(conversationsPath(tenant))).conversations, [client, tenant]);
  const {data, busy, error, refresh} = useLoader(load);
  return <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(320px,1fr))', gap: 20}}>
    <section style={panel}>
      <h2>Suhbatlar · {data?.length ?? 0}</h2>
      <p style={muted}>Har bir mijoz suhbatining oxirgi xabari va agent navbatining holati (oxirgi 100 ta).</p>
      <button style={button} disabled={busy} onClick={() => void refresh()}>Yangilash</button>
      <AutoRefresh refresh={refresh}/>
      {error && <p role="alert" style={{color: '#fca5a5'}}>{error}</p>}
      {data && data.length === 0 && <p>Hali suhbat yo‘q.</p>}
      {data?.map(c => {
        const active = selected?.channel === c.channel && selected?.conversation_id === c.conversation_id;
        return <button key={c.channel + ':' + c.conversation_id}
          style={{...button, display: 'block', width: '100%', textAlign: 'left', background: active ? '#2563eb' : '#1e293b'}}
          onClick={() => onSelect({channel: c.channel, conversation_id: c.conversation_id})}>
          <strong>{c.channel}</strong> · {c.conversation_id}
          {c.turn_status && <span style={{color: statusColor[c.turn_status] || '#cbd5e1'}}> · {turnStatusLabel(c.turn_status)}</span>}
          <OperatorBadge takeover={c.takeover}/>
          <br/><small>{c.last_role === 'customer' ? 'Mijoz' : c.last_role === 'agent' ? 'Agent' : c.last_role}: {c.last_text.slice(0, 120)}</small>
          <br/><small style={muted}>{when(c.last_at)}</small>
        </button>;
      })}
    </section>
    {selected
      ? <ThreadPanel key={selected.channel + ':' + selected.conversation_id} client={client} tenant={tenant}
          thread={selected} canReply={canReply} onClose={() => onSelect(null)}/>
      : <section style={panel}><h2>Suhbat</h2><p>Chapdan suhbatni tanlang.</p></section>}
  </div>;
}

// ---------------------------------------------------------------- thread

type Turn = {event_key: string; seq: number; status: string; error: string; task: string; reply: string;
  created: number; updated: number};
type Thread = ThreadRef & {history: unknown[]; turns: Turn[]; operator_replies: unknown[]; takeover: Takeover | null};

function ThreadPanel({client, tenant, thread, canReply, onClose}: Base & {
  thread: ThreadRef; canReply: boolean; onClose: () => void}) {
  const load = useCallback(async () =>
    client.request<Thread>(threadPath(tenant, thread.channel, thread.conversation_id)), [client, tenant, thread]);
  const {data, busy, error, refresh} = useLoader(load);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');
  const [sent, setSent] = useState('');
  async function send() {
    setSending(true); setSendError(''); setSent('');
    try {
      // A new key per send attempt: the API returns the same task for a replayed key.
      const req = replyRequest(text, crypto.randomUUID());
      const out = await client.request<{task_id: string; status: string}>(
        replyPath(tenant, thread.channel, thread.conversation_id), req.body, req.method, req.headers);
      setSent(`Yuborildi: vazifa ${String(out.task_id).slice(0, 10)} · ${out.status}`);
      setText('');
      await refresh();
    } catch (e) { setSendError(replyErrorText(e)); }
    finally { setSending(false); }
  }
  async function giveBack() {
    setSending(true); setSendError(''); setSent('');
    try {
      const req = releaseRequest(crypto.randomUUID());
      await client.request(releasePath(tenant, thread.channel, thread.conversation_id), req.body, req.method, req.headers);
      setSent('Suhbat botga qaytarildi: keyingi xabarga bot javob beradi.');
      await refresh();
    } catch (e) { setSendError(replyErrorText(e)); }
    finally { setSending(false); }
  }
  const lines = threadLines(data);
  const held = takeoverActive(data?.takeover, nowSeconds());
  return <section style={panel}>
    <h2>{thread.channel} · {thread.conversation_id}<OperatorBadge takeover={data?.takeover}/></h2>
    {held && <p style={muted}>Operator javob bergani uchun bot bu suhbatda {when(data!.takeover!.until)} gacha
      javob bermaydi. Mijoz xabarlari shu yerda ko‘rinadi.
      <button style={button} disabled={!canReply || sending} onClick={() => void giveBack()}>Botga qaytarish</button></p>}
    <button style={button} disabled={busy} onClick={() => void refresh()}>Yangilash</button>
    <button style={button} onClick={onClose}>Yopish</button>
    <AutoRefresh refresh={refresh}/>
    {error && <p role="alert" style={{color: '#fca5a5'}}>{error}</p>}
    {data && lines.length === 0 && <p>Xabar tarixi yo‘q.</p>}
    {lines.map((l, i) => <div key={i} style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', background: '#0b1220',
      borderLeft: `3px solid ${bubbleColor[l.role] || '#94a3b8'}`, padding: '8px 12px', borderRadius: 6, margin: '8px 0',
      marginLeft: l.role === 'customer' ? 0 : 24}}>
      <small style={muted}>{l.label} · {when(l.at)}{l.status ? ` · ${l.status}` : ''}</small><br/>{l.text}
    </div>)}
    {data && data.turns.length > 0 && <details><summary>Agent navbatlari · {data.turns.length}</summary>
      {data.turns.map(t => <p key={t.event_key} style={{...row, margin: 0}}>
        <span style={{color: statusColor[t.status] || '#cbd5e1'}}>{turnStatusLabel(t.status)}</span>
        {t.error && <span style={muted}> · {reasonLabel(t.error)}</span>}
        <span style={muted}> · {when(t.updated)} · kalit {t.event_key}{t.task && ` · vazifa ${t.task.slice(0, 10)}`}</span>
      </p>)}
    </details>}
    <h3>Operator javobi</h3>
    <p style={muted}>Javob shu suhbatga, agent nomidan emas, sizning nomingizdan yuboriladi va auditga yoziladi.
      Yuborilgach bot shu suhbatda vaqtincha jim turadi (operator rejimi).</p>
    <textarea aria-label="Operator javobi" value={text} maxLength={4000} rows={4} onChange={e => setText(e.target.value)}
      style={{background: '#0b1220', color: '#e2e8f0', border: '1px solid #475569', borderRadius: 6, padding: 10,
        width: '100%', boxSizing: 'border-box'}}/>
    <button style={button} disabled={!canReply || sending || !text.trim()} onClick={() => void send()}>
      {sending ? 'Yuborilmoqda...' : 'Javob yuborish'}</button>
    {!canReply && <p style={muted}>Javob yuborish owner/operator uchun va ijro to‘xtatilmagan bo‘lishi kerak.</p>}
    {sendError && <p role="alert" style={{color: '#fca5a5'}}>{sendError}</p>}
    {sent && <p role="status" style={{color: '#86efac'}}>{sent}</p>}
  </section>;
}

// -------------------------------------------------------------- handoffs

type Handoff = ThreadRef & {id: string; event_key: string; reason: string; text: string; created: number; agent: string};

export function HandoffsPanel({client, tenant, onOpen}: Base & {onOpen: (ref: ThreadRef) => void}) {
  const load = useCallback(async () =>
    (await client.request<{handoffs: Handoff[]}>(handoffsPath(tenant))).handoffs, [client, tenant]);
  const {data, busy, error, refresh} = useLoader(load);
  return <section style={panel}>
    <h2>Operatorga uzatilganlar · {data?.length ?? 0}</h2>
    <p style={muted}>Agent o‘zi javob bera olmagan yoki javobi yetib borgani noma’lum bo‘lgan xabarlar (oxirgi 100 ta).</p>
    <button style={button} disabled={busy} onClick={() => void refresh()}>Yangilash</button>
    <AutoRefresh refresh={refresh}/>
    {error && <p role="alert" style={{color: '#fca5a5'}}>{error}</p>}
    {data && data.length === 0 && <p>Uzatilgan xabar yo‘q.</p>}
    {data?.map(h => <article key={h.id} style={row}>
      <strong>{reasonLabel(h.reason)}</strong> · <span style={muted}>{h.channel} · {h.conversation_id} · {when(h.created)}</span>
      <div style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', background: '#0b1220', borderLeft: '3px solid #f59e0b',
        padding: '8px 12px', borderRadius: 6, margin: '8px 0'}}>{h.text || <em>Matn yo‘q</em>}</div>
      <button style={button} onClick={() => onOpen({channel: h.channel, conversation_id: h.conversation_id})}>
        Suhbatni ochish</button>
    </article>)}
  </section>;
}
