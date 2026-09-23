'use client';
import {useCallback, useEffect, useState} from 'react';
import {type SessionClient} from '../lib/session-client.mjs';
import {approvalPath, draftSummary, formatSum, shopPath, startAutoRefresh} from '../lib/shop-client.mjs';

/**
 * Shop-wide views: approval queue, inbox with customer text, orders, catalogue and
 * channel status. Thin callers of app/shop_api.py; the API decides roles, these
 * panels only avoid offering a button the API would refuse.
 */

const panel = {background: '#111c2e', border: '1px solid #233149', borderRadius: 12, padding: 20, marginBottom: 16};
const button = {background: '#1e3a5f', color: '#e2e8f0', border: '1px solid #3b5273', borderRadius: 7, padding: '10px 14px', margin: 4, cursor: 'pointer'};
const pre = {whiteSpace: 'pre-wrap' as const, overflowWrap: 'anywhere' as const, fontSize: 12, background: '#0b1220', padding: 12, borderRadius: 6};
const row = {borderBottom: '1px solid #334155', padding: 12};
const bubble = {whiteSpace: 'pre-wrap' as const, overflowWrap: 'anywhere' as const, background: '#0b1220', borderLeft: '3px solid #3b82f6', padding: '10px 12px', borderRadius: 6, margin: '8px 0'};
const muted = {color: '#94a3b8', fontSize: 13};
const table = {width: '100%', borderCollapse: 'collapse' as const, fontSize: 14};
const cell = {borderBottom: '1px solid #334155', padding: '8px 6px', textAlign: 'left' as const, verticalAlign: 'top' as const};

type Base = {client: SessionClient; tenant: string};

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

/** Optional 10 s polling, off by default, paused while the tab is hidden. */
function AutoRefresh({refresh}: {refresh: () => Promise<void>}) {
  const [on, setOn] = useState(false);
  useEffect(() => on ? startAutoRefresh(refresh, 10000) : undefined, [on, refresh]);
  return <label style={{...muted, marginLeft: 8}}>
    <input type="checkbox" checked={on} onChange={e => setOn(e.target.checked)}/> Har 10 soniyada yangilash
  </label>;
}

const when = (t: number) => new Date(t * 1000).toLocaleString();

// ------------------------------------------------------------------ approvals

type Approval = {step_id: string; task_id: string; agent: string; tool: string; args: unknown; risk: string;
  channel: string; event_key: string; created: number; expires: number; step_status: string};

export function ApprovalsPanel({client, tenant, canDecide, onOpenTask}: Base & {canDecide: boolean; onOpenTask?: (id: string) => void}) {
  const load = useCallback(async () =>
    (await client.request<{approvals: Approval[]}>(shopPath(tenant, 'approvals'))).approvals, [client, tenant]);
  const {data, busy, error, setError, refresh} = useLoader(load);
  const [deciding, setDeciding] = useState('');
  async function decide(step: string, decision: 'approved' | 'rejected') {
    setDeciding(step); setError('');
    try { await client.request(approvalPath(tenant, step), {decision}); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Xato'); }
    finally { setDeciding(''); }
  }
  return <section style={panel}>
    <h2>Tasdiqlar · {data?.length ?? 0}</h2>
    <p style={muted}>Agent tayyorlagan, lekin hali yuborilmagan amallar. Tasdiqlangan qadam
      aynan shu matn va argumentlar bilan bajariladi.</p>
    <button style={button} disabled={busy} onClick={() => void refresh()}>Yangilash</button>
    <AutoRefresh refresh={refresh}/>
    {error && <p role="alert" style={{color: '#fca5a5'}}>{error}</p>}
    {data && data.length === 0 && <p>Kutilayotgan tasdiq yo‘q.</p>}
    {data?.map(a => {
      const d = draftSummary(a.tool, a.args);
      return <article key={a.step_id} style={row}>
        <strong>{a.agent}</strong> · <code>{a.tool}</code> · <span style={muted}>{a.channel} · {when(a.created)}</span>
        {d.kind === 'message'
          ? <><p style={{margin: '8px 0 0'}}>Qabul qiluvchi: <strong>{d.recipient || 'ko‘rsatilmagan'}</strong> ({d.channel})</p>
              <div style={bubble}>{d.text || <em>Matn yo‘q</em>}</div></>
          : <pre style={pre}>{d.text}</pre>}
        <button style={button} disabled={!canDecide || deciding !== ''} onClick={() => void decide(a.step_id, 'approved')}>
          {deciding === a.step_id ? 'Bajarilmoqda...' : 'Tasdiqlash'}</button>
        <button style={button} disabled={!canDecide || deciding !== ''} onClick={() => void decide(a.step_id, 'rejected')}>Rad etish</button>
        {onOpenTask && <button style={button} onClick={() => onOpenTask(a.task_id)}>Vazifani ochish</button>}
      </article>;
    })}
    {!canDecide && <p style={muted}>Tasdiqlash owner/operator uchun va ijro to‘xtatilmagan bo‘lishi kerak.</p>}
  </section>;
}

// ---------------------------------------------------------------------- inbox

type Message = {channel: string; event_key: string; status: string; error: string; result: Record<string, unknown>;
  text: string; truncated: boolean; sender: string; conversation_id: string};

export function InboxPanel({client, tenant, canWrite}: Base & {canWrite: boolean}) {
  const load = useCallback(async () =>
    (await client.request<{events: Message[]}>(shopPath(tenant, 'inbox'))).events, [client, tenant]);
  const {data, busy, error, setError, refresh} = useLoader(load);
  return <section style={panel}>
    <h2>Kiruvchi xabarlar</h2>
    <button style={button} disabled={busy} onClick={() => void refresh()}>Yangilash</button>
    <AutoRefresh refresh={refresh}/>
    {error && <p role="alert" style={{color: '#fca5a5'}}>{error}</p>}
    {data && data.length === 0 && <p>Hali xabar yo‘q.</p>}
    {data?.map(e => <article key={e.channel + e.event_key} style={row}>
      <strong>{e.channel}</strong> · {e.sender || 'noma’lum'} · <span style={muted}>{e.status}{e.error && ` (${e.error})`}</span>
      <div style={bubble}>{e.text || <em>Matn yo‘q</em>}{e.truncated && '…'}</div>
      <small style={muted}>Suhbat: {e.conversation_id || '—'} · kalit: {e.event_key}
        {typeof e.result?.task_id === 'string' && ` · vazifa: ${String(e.result.task_id).slice(0, 10)}`}</small>
      {e.status === 'failed' && <div><button style={button} disabled={busy || !canWrite} onClick={async () => {
        try { await client.request(`/platform/${encodeURIComponent(tenant)}/inbox/retry`, {channel: e.channel, key: e.event_key}); await refresh(); }
        catch (err) { setError(err instanceof Error ? err.message : 'Xato'); }
      }}>Konfiguratsiya tuzatilgach qayta urinish</button></div>}
    </article>)}
  </section>;
}

// --------------------------------------------------------------------- orders

type RecordOrder = {id: string; title: string; body: string; created: number};
type CustomerOrder = {id: string; external_id: string; status: string; currency: string; total_minor: number;
  created: number; updated: number; customer_id: string; customer_name: string | null};

export function OrdersPanel({client, tenant}: Base) {
  const load = useCallback(async () =>
    client.request<{orders: RecordOrder[]; customer_orders: CustomerOrder[]}>(shopPath(tenant, 'orders')), [client, tenant]);
  const {data, busy, error, refresh} = useLoader(load);
  return <section style={panel}>
    <h2>Buyurtmalar</h2>
    <p style={muted}>Agent yozgan buyurtma yozuvlari va mijozlar 360 dagi buyurtmalar (oxirgi 100 tadan).</p>
    <button style={button} disabled={busy} onClick={() => void refresh()}>Yangilash</button>
    {error && <p role="alert" style={{color: '#fca5a5'}}>{error}</p>}
    {data && <>
      <h3>Agent yozuvlari · {data.orders.length}</h3>
      {data.orders.length === 0 && <p>Yozuv yo‘q.</p>}
      {data.orders.map(o => <article key={o.id} style={row}><strong>{o.title || o.id.slice(0, 10)}</strong>
        <span style={muted}> · {when(o.created)}</span>{o.body && <p style={{whiteSpace: 'pre-wrap'}}>{o.body}</p>}</article>)}
      <h3>Mijoz buyurtmalari · {data.customer_orders.length}</h3>
      {data.customer_orders.length === 0 ? <p>Buyurtma yo‘q.</p> : <table style={table}><thead><tr>
        <th style={cell}>Raqam</th><th style={cell}>Mijoz</th><th style={cell}>Holat</th><th style={cell}>Jami (minor)</th><th style={cell}>Yangilangan</th>
      </tr></thead><tbody>{data.customer_orders.map(o => <tr key={o.id}>
        <td style={cell}>{o.external_id}</td><td style={cell}>{o.customer_name || o.customer_id.slice(0, 10)}</td>
        <td style={cell}>{o.status}</td><td style={cell}>{formatSum(o.total_minor, o.currency)}</td><td style={cell}>{when(o.updated)}</td>
      </tr>)}</tbody></table>}
    </>}
  </section>;
}

// -------------------------------------------------------------------- catalog

type Product = {id: string; name: string; price_uzs: number; sizes: (string | number)[]};

export function CatalogPanel({client, tenant}: Base) {
  const load = useCallback(async () =>
    (await client.request<{products: Product[]}>(shopPath(tenant, 'products'))).products, [client, tenant]);
  const {data, busy, error, refresh} = useLoader(load);
  const [query, setQuery] = useState('');
  const shown = (data || []).filter(p => !query.trim() || (p.id + ' ' + p.name).toLowerCase().includes(query.trim().toLowerCase()));
  return <section style={panel}>
    <h2>Katalog · {data?.length ?? 0}</h2>
    <p style={muted}>Pack’dagi mahsulotlar (products.yaml). O‘zgartirish pack orqali qilinadi.</p>
    <input aria-label="Qidirish" placeholder="Qidirish" value={query} onChange={e => setQuery(e.target.value)}
      style={{background: '#0b1220', color: '#e2e8f0', border: '1px solid #475569', borderRadius: 6, padding: 10, margin: 4}}/>
    <button style={button} disabled={busy} onClick={() => void refresh()}>Yangilash</button>
    {error && <p role="alert" style={{color: '#fca5a5'}}>{error}</p>}
    {data && <table style={table}><thead><tr><th style={cell}>ID</th><th style={cell}>Nomi</th><th style={cell}>Narx</th><th style={cell}>O‘lchamlar</th></tr></thead>
      <tbody>{shown.map(p => <tr key={p.id}><td style={cell}><code>{p.id}</code></td><td style={cell}>{p.name}</td>
        <td style={cell}>{formatSum(p.price_uzs)}</td><td style={cell}>{p.sizes.join(', ') || '—'}</td></tr>)}</tbody></table>}
  </section>;
}

// ------------------------------------------------------------------- channels

type Channel = {channel: string; configured: boolean; ready: boolean; credentials: {env: string; set: boolean}[]};

export function ChannelsPanel({client, tenant}: Base) {
  const load = useCallback(async () =>
    (await client.request<{channels: Channel[]}>(shopPath(tenant, 'channels'))).channels, [client, tenant]);
  const {data, busy, error, refresh} = useLoader(load);
  return <section style={panel}>
    <h2>Kanallar holati</h2>
    <p style={muted}>Integratsiya konfiguratsiyasi bormi va u ko‘rsatgan env o‘zgaruvchi serverda
      o‘rnatilganmi. Qiymatlar hech qachon brauzerga yuborilmaydi; bu provayderga ulanish tekshiruvi emas.</p>
    <button style={button} disabled={busy} onClick={() => void refresh()}>Yangilash</button>
    {error && <p role="alert" style={{color: '#fca5a5'}}>{error}</p>}
    {data?.map(c => <article key={c.channel} style={row}>
      <strong>{c.channel}</strong> · <span style={{color: c.ready ? '#86efac' : c.configured ? '#fde68a' : '#a1a1aa'}}>
        {c.ready ? 'tayyor' : c.configured ? 'kalit yetishmaydi' : 'sozlanmagan'}</span>
      {c.credentials.map(k => <p key={k.env} style={muted}><code>{k.env}</code> · {k.set ? 'o‘rnatilgan' : 'o‘rnatilmagan'}</p>)}
    </article>)}
  </section>;
}
