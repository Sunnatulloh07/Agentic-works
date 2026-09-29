'use client';
import {useCallback, useEffect, useRef, useState} from 'react';
import {type SessionClient} from '../lib/session-client.mjs';
import {customerName, latestOpenHandoff, openHandoffs, reasonLabel, releasePath, releaseRequest, replyErrorText, replyPath,
  replyRequest, resolveHandoffPath, resolveHandoffRequest, takeoverActive, threadLines, threadPath, turnStatusLabel,
  type Takeover} from '../lib/conversation-client.mjs';
import {channelLabel, formatTime, friendlyError, statusLabel} from '../lib/format.mjs';
import {conversationFlags, conversationKey} from '../lib/nav.mjs';
import {type Idempotency} from '../lib/idempotency.mjs';
import {Alert, Skeleton, nowSeconds, useLoader, When} from './ui';

/**
 * "Suhbatlar": the conversation list, the operator-needed filter, the handoff history
 * and one open thread with the operator reply. The list and handoffs come from the
 * dashboard's shared poller; the open thread reloads on every poll. Thin callers of
 * app/shop_api.py and the operator-reply endpoint -- the API decides roles.
 */

export type ThreadRef = {channel: string; conversation_id: string};
export type Conversation = ThreadRef & {last_role: string; last_text: string; last_at: number;
  turn_status: string; turn_error: string; sender: string; customer_name?: string; sender_name?: string; takeover: Takeover | null};
export type Handoff = ThreadRef & {id: string; event_key: string; reason: string; text: string; created: number; agent: string;
  resolved?: boolean; resolved_by?: string; resolved_at?: number | null};

type Base = {client: SessionClient; tenant: string};

/** Who the operator is talking to, as readably as the data allows. */
function person(c: ThreadRef & {customer_name?: string; sender_name?: string; sender?: string}) {
  const name = customerName(c);  // plain text: React escapes it, it is never markup
  const id = c.sender || c.conversation_id;
  return {title: name || id, detail: name ? `${channelLabel(c.channel)} · ${id}` : channelLabel(c.channel),
    initials: name ? name.split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase() : id.replace(/\D/g, '').slice(-2) || '?'};
}

function reasonText(reason: string) {
  if (reason === 'operator') return 'siz javob beryapsiz';
  if (reason === 'failed' || reason === 'uncertain' || reason === 'throttled') return turnStatusLabel(reason);
  return reasonLabel(reason);
}

const ROLE = {customer: 'Mijoz', agent: 'Bot', operator: 'Operator'} as Record<string, string>;
const BUSY_TURNS = new Set(['queued', 'open', 'delivering']);

type Filter = 'all' | 'attention' | 'history';

export function InboxView({client, tenant, canReply, conversations, handoffs, loadError, selected, onSelect,
  seen, since, version, keys, onChanged}: Base & {
  canReply: boolean; conversations: Conversation[] | null; handoffs: Handoff[] | null; loadError: string;
  selected: ThreadRef | null; onSelect: (ref: ThreadRef | null) => void; seen: Record<string, number>; since: number;
  version: number; keys: Idempotency; onChanged: () => Promise<void>;
}) {
  const [filter, setFilter] = useState<Filter>('all');
  const [resolving, setResolving] = useState('');
  const [resolveFailure, setResolveFailure] = useState('');
  const now = nowSeconds();
  // Only OPEN handoffs are "Javob kerak"; the API already filters, this keeps the list honest if it ever sends both.
  const openList = openHandoffs(handoffs);
  async function resolve(h: Handoff) {
    const payload = {handoff: h.id};
    setResolving(h.id); setResolveFailure('');
    try {
      const req = resolveHandoffRequest(keys.key('resolve-handoff', payload));
      await client.request(resolveHandoffPath(tenant, h.id), req.body, req.method, req.headers);
      keys.settle('resolve-handoff', payload);
      await onChanged();
    } catch (e) { setResolveFailure(sendError(e)); }
    finally { setResolving(''); }
  }
  const rows = (conversations || []).map(c => ({c, f: conversationFlags(c, openList, seen, since, now)}));
  const attention = rows.filter(r => r.f.needsHuman);
  const shown = filter === 'attention' ? attention : rows;
  const openKey = selected ? conversationKey(selected) : null;
  const open = conversations?.find(c => conversationKey(c) === openKey);
  return <div className="inbox" data-open={selected ? 'true' : 'false'}>
    <section className="panel inbox-list">
      <h2>Suhbatlar</h2>
      <div className="filters" role="group" aria-label="Suhbatlarni saralash">
        <button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>Hammasi · {rows.length}</button>
        <button aria-pressed={filter === 'attention'} onClick={() => setFilter('attention')}>Javob kerak · {attention.length}</button>
        <button aria-pressed={filter === 'history'} onClick={() => setFilter('history')}>Uzatilganlar</button>
      </div>
      <Alert text={loadError}/>
      <Alert text={resolveFailure}/>
      {!conversations && !loadError && <Skeleton rows={5}/>}
      {filter !== 'history' && <div className="conv-list">
        {conversations && shown.length === 0 && <p className="muted">
          {filter === 'attention' ? 'Hamma mijozga javob berilgan.' : 'Hali suhbat yo‘q. Mijoz yozishi bilan shu yerda chiqadi.'}</p>}
        {shown.map(({c, f}) => {
          const p = person(c);
          const key = conversationKey(c);
          const held = takeoverActive(c.takeover, now);
          return <button key={key} className={`conv${f.unread && key !== openKey ? ' unread' : ''}`}
            aria-current={key === openKey ? 'true' : undefined}
            onClick={() => onSelect({channel: c.channel, conversation_id: c.conversation_id})}>
            <span className={`avatar ${c.channel}`} aria-hidden="true">{p.initials}</span>
            <span className="name">{p.title}</span>
            <span className="time"><When at={c.last_at}/></span>
            <span className="preview">{c.last_role !== 'customer' && `${ROLE[c.last_role] || c.last_role}: `}{c.last_text}</span>
            <span className="flags">
              <span className="detail">{p.detail}</span>
              {f.needsHuman && <span className="chip warn">Javob kerak · {reasonText(f.reason)}</span>}
              {!f.needsHuman && held && <span className="chip ok">Operator rejimi</span>}
              {!f.needsHuman && BUSY_TURNS.has(c.turn_status) && <span className="chip info">Bot javob yozmoqda</span>}
            </span>
          </button>;
        })}
      </div>}
      {filter === 'history' && <div className="conv-list">
        <p className="muted">Bot o‘zi javob bera olmagan va hali hal qilinmagan xabarlar (oxirgi 100 ta).</p>
        {handoffs && openList.length === 0 && <p>Hal qilinmagan uzatilgan xabar yo‘q.</p>}
        {openList.map(h => <article key={h.id} className="row">
          <div className="panel-head"><strong>{reasonText(h.reason)}</strong><span className="muted"><When at={h.created}/></span></div>
          <div className="message-card">{h.text || <em>Matn yo‘q</em>}</div>
          <button className="btn small" onClick={() => onSelect({channel: h.channel, conversation_id: h.conversation_id})}>
            {channelLabel(h.channel)} · {h.conversation_id} suhbatini ochish</button>
          <button className="btn small" disabled={!canReply || resolving === h.id} onClick={() => void resolve(h)}>
            {resolving === h.id ? 'Saqlanmoqda…' : 'Hal qilindi'}</button>
        </article>)}
      </div>}
    </section>
    <div className="thread-slot">
      {selected
        ? <ThreadPanel key={openKey!} client={client} tenant={tenant} thread={selected} row={open}
            canReply={canReply} onClose={() => onSelect(null)} version={version} keys={keys} onChanged={onChanged}
            openHandoff={latestOpenHandoff(openList, selected)} onResolve={resolve} resolving={resolving}/>
        : <section className="panel"><h2>Suhbat</h2><p className="muted">Suhbatni tanlang: mijoz yozgan hamma narsa va bot javoblari shu yerda.</p></section>}
    </div>
  </div>;
}

// ---------------------------------------------------------------- thread

type Turn = {event_key: string; seq: number; status: string; error: string; task: string; reply: string;
  created: number; updated: number};
type Thread = ThreadRef & {history: unknown[]; turns: Turn[]; operator_replies: unknown[]; takeover: Takeover | null;
  sender_name?: string};

const sendError = (e: unknown) => {
  const status = (e as {status?: unknown})?.status;
  return typeof status === 'number' ? replyErrorText(e) : friendlyError(e);
};

function ThreadPanel({client, tenant, thread, row, canReply, onClose, version, keys, onChanged, openHandoff, onResolve, resolving}: Base & {
  thread: ThreadRef; row?: Conversation; canReply: boolean; onClose: () => void; version: number;
  keys: Idempotency; onChanged: () => Promise<void>;
  openHandoff: Handoff | null; onResolve: (h: Handoff) => Promise<void>; resolving: string;
}) {
  const load = useCallback(async () =>
    client.request<Thread>(threadPath(tenant, thread.channel, thread.conversation_id)), [client, tenant, thread]);
  const {data, loading, error, refresh} = useLoader(load);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState('');
  const [sent, setSent] = useState('');
  const box = useRef<HTMLElement>(null);
  const lineBox = useRef<HTMLDivElement>(null);
  const first = useRef(true);

  useEffect(() => {
    // On a phone the thread replaces the list; bring its top into view.
    if (window.matchMedia?.('(max-width: 820px)').matches) box.current?.scrollIntoView({block: 'start'});
  }, []);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    void refresh();
  }, [version, refresh]);
  const lines = threadLines(data);
  useEffect(() => {
    if (lineBox.current) lineBox.current.scrollTop = lineBox.current.scrollHeight;
  }, [lines.length]);

  async function send() {
    const payload = {channel: thread.channel, conversation_id: thread.conversation_id, text};
    setSending(true); setFailure(''); setSent('');
    try {
      // Same text to the same chat keeps its key until it succeeds, so clicking again
      // after a lost response cannot send the customer a second copy.
      const req = replyRequest(text, keys.key('reply', payload));
      await client.request<{task_id: string; status: string}>(
        replyPath(tenant, thread.channel, thread.conversation_id), req.body, req.method, req.headers);
      keys.settle('reply', payload);
      setSent('Javob yuborildi.');
      setText('');
      await Promise.all([refresh(), onChanged()]);
    } catch (e) {
      setFailure(`${sendError(e)} Qayta bossangiz, xabar ikki marta yuborilmaydi.`);
    } finally { setSending(false); }
  }
  async function giveBack() {
    const payload = {channel: thread.channel, conversation_id: thread.conversation_id};
    setSending(true); setFailure(''); setSent('');
    try {
      const req = releaseRequest(keys.key('release', payload));
      await client.request(releasePath(tenant, thread.channel, thread.conversation_id), req.body, req.method, req.headers);
      keys.settle('release', payload);
      setSent('Suhbat botga qaytarildi: mijozning keyingi xabariga bot javob beradi.');
      await Promise.all([refresh(), onChanged()]);
    } catch (e) { setFailure(sendError(e)); }
    finally { setSending(false); }
  }

  const p = person({...thread, customer_name: row?.customer_name, sender_name: row?.sender_name || data?.sender_name,
    sender: row?.sender});
  const held = takeoverActive(data?.takeover, nowSeconds());
  return <section className="panel thread" ref={box} aria-label={`${p.title} bilan suhbat`}>
    <div className="thread-head">
      <button className="btn small back" onClick={onClose}>← Suhbatlar</button>
      <span className={`avatar ${thread.channel}`} aria-hidden="true">{p.initials}</span>
      <div className="grow"><h2>{p.title}</h2><span className="muted">{p.detail}</span></div>
      {openHandoff && <button className="btn small" disabled={!canReply || resolving === openHandoff.id}
        onClick={() => void onResolve(openHandoff)}>{resolving === openHandoff.id ? 'Saqlanmoqda…' : 'Hal qilindi'}</button>}
      <button className="btn small close-wide" onClick={onClose} aria-label="Suhbatni yopish">Yopish</button>
    </div>
    {held && <div className="notice">
      Siz javob berganingiz uchun bot bu suhbatda {formatTime(data!.takeover!.until)} gacha jim turadi.
      <div><button className="btn small" disabled={!canReply || sending} onClick={() => void giveBack()}>Botga qaytarish</button></div>
    </div>}
    <Alert text={error}/>
    <div className="lines" ref={lineBox}>
      {loading && !data && <Skeleton rows={3}/>}
      {data && lines.length === 0 && <p className="muted">Xabarlar tarixi yo‘q.</p>}
      {lines.map((l, i) => <div key={i} className={`bubble ${l.role in ROLE ? l.role : 'customer'}`}>
        <span className="meta">{ROLE[l.role] || l.label} · <When at={l.at}/>{l.status && l.status !== 'succeeded' ? ` · ${statusLabel(l.status)}` : ''}</span>
        {l.text}
      </div>)}
    </div>
    {data && data.turns.length > 0 && <details><summary className="muted">Bot navbatlari (texnik) · {data.turns.length}</summary>
      {data.turns.map(t => <p key={t.event_key} className="row muted">
        {turnStatusLabel(t.status)}{t.error && ` · ${reasonLabel(t.error)}`} · <When at={t.updated}/>
        {t.task && ` · vazifa ${t.task.slice(0, 10)}`}
      </p>)}
    </details>}
    <div className="composer">
      <label htmlFor={`reply-${thread.channel}-${thread.conversation_id}`} className="muted">
        Javobingiz {channelLabel(thread.channel)} orqali sizning nomingizdan ketadi; keyin bot bu suhbatda vaqtincha jim turadi.</label>
      <textarea id={`reply-${thread.channel}-${thread.conversation_id}`} value={text} maxLength={4000} rows={3}
        placeholder="Mijozga javob yozing" onChange={e => setText(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && canReply && !sending && text.trim()) void send(); }}/>
      <button className="btn primary" disabled={!canReply || sending || !text.trim()} onClick={() => void send()}>
        {sending ? 'Yuborilmoqda…' : 'Yuborish'}</button>
      {!canReply && <p className="muted">Javob yozish egasi va operator uchun, bot to‘xtatilmagan bo‘lishi kerak.</p>}
      <Alert text={failure}/>
      {sent && <p role="status" className="success">{sent}</p>}
    </div>
  </section>;
}
