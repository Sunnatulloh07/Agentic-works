'use client';
import {useCallback, useState} from 'react';
import {type SessionClient} from '../lib/session-client.mjs';
import {approvalPath, draftSummary, formatMinor, formatSum, safePhotoUrl, shopPath, stockView} from '../lib/shop-client.mjs';
import {orderView, type OrderView} from '../lib/conversation-client.mjs';
import {channelLabel, friendlyError, statusLabel} from '../lib/format.mjs';
import {Alert, ConfirmButton, Skeleton, useLoader, When} from './ui';
import {type ThreadRef} from './ConversationPanels';

/**
 * Shop-wide views: approval queue, inbox with customer text, orders, catalogue and
 * channel status. Thin callers of app/shop_api.py; the API decides roles, these
 * panels only avoid offering a button the API would refuse.
 */

type Base = {client: SessionClient; tenant: string};

// ------------------------------------------------------------------ approvals

export type Approval = {step_id: string; task_id: string; agent: string; tool: string; args: unknown; risk: string;
  channel: string; event_key: string; created: number; expires: number; step_status: string};

/** An order as the operator checks it before saying yes: what, how many, to whom. */
export function OrderCard({order}: {order: OrderView}) {
  return <dl className="order-card">
    <dt>Mahsulot</dt><dd>{order.product}</dd>
    <dt>O‘lcham · soni</dt><dd>{order.size || '—'} · {String(order.qty)} dona</dd>
    <dt>Jami</dt><dd className="total">{formatSum(order.total)}</dd>
    <dt>Mijoz</dt><dd>{order.customer || '—'}{order.phone && <> · <a href={`tel:${order.phone}`}>{order.phone}</a></>}</dd>
    {order.delivery && <><dt>Yetkazish</dt><dd>{order.delivery}</dd></>}
  </dl>;
}

export function ApprovalsPanel({client, tenant, canDecide, approvals, loadError, onChanged, onOpenTask, onOpenChat}: Base & {
  canDecide: boolean; approvals: Approval[] | null; loadError: string; onChanged: () => Promise<void>;
  onOpenTask?: (id: string) => void; onOpenChat?: (ref: ThreadRef) => void;
}) {
  const [deciding, setDeciding] = useState('');
  const [error, setError] = useState('');
  const [done, setDone] = useState('');
  async function decide(step: string, decision: 'approved' | 'rejected') {
    setDeciding(step); setError(''); setDone('');
    try {
      await client.request(approvalPath(tenant, step), {decision});
      setDone(decision === 'approved' ? 'Tasdiqlandi. Amal navbatga qo‘yildi.' : 'Rad etildi. Amal bajarilmaydi.');
    } catch (e) {
      // The queue may have moved (someone else decided, or it expired): say so and reload it.
      setError(`Qaror saqlanmadi: ${friendlyError(e)}`);
    } finally {
      setDeciding('');
      await onChanged();
    }
  }
  return <section className="panel">
    <div className="panel-head"><h2>Tasdiqlar{approvals ? ` · ${approvals.length}` : ''}</h2></div>
    <p className="muted">Bot tayyorlagan, lekin hali bajarilmagan amallar. Tasdiqlasangiz aynan shu ko‘rinishda bajariladi.</p>
    <Alert text={error || loadError}/>
    {done && <p role="status" className="notice success">{done}</p>}
    {!approvals && !loadError && <Skeleton rows={3}/>}
    {approvals && approvals.length === 0 && <p>Hozir tasdiq kutayotgan amal yo‘q.</p>}
    {approvals?.map(a => {
      const d = draftSummary(a.tool, a.args);
      const chat = d.kind === 'order' && d.order.conversationId && d.order.channel
        ? {channel: d.order.channel, conversation_id: d.order.conversationId} : null;
      return <article key={a.step_id} className="approval">
        <div className="panel-head">
          <strong>{d.kind === 'order' ? 'Yangi buyurtma' : d.kind === 'message' ? `${channelLabel(d.channel)} xabari` : a.tool}</strong>
          <span className="muted">{channelLabel(a.channel)} · <When at={a.created}/></span>
        </div>
        {d.kind === 'order' && <OrderCard order={d.order}/>}
        {d.kind === 'message' && <>
          <p className="muted">Kimga: <strong>{d.recipient || 'ko‘rsatilmagan'}</strong></p>
          <div className="message-card">{d.text || <em>Matn yo‘q</em>}</div></>}
        {d.kind === 'args' && <pre>{d.text}</pre>}
        <div className="actions">
          <button className="btn primary" disabled={!canDecide || deciding !== ''} onClick={() => void decide(a.step_id, 'approved')}>
            {deciding === a.step_id ? 'Saqlanmoqda…' : d.kind === 'order' ? 'Buyurtmani tasdiqlash' : 'Tasdiqlash'}</button>
          <ConfirmButton label="Rad etish" disabled={!canDecide || deciding !== ''}
            title={d.kind === 'order' ? 'Buyurtmani rad etasizmi?' : 'Amalni rad etasizmi?'}
            message={d.kind === 'order' ? 'Buyurtma yozilmaydi. Mijozga bu haqda o‘zingiz yozishingiz kerak bo‘ladi.'
              : 'Bu amal bajarilmaydi va uni qayta tiklab bo‘lmaydi.'}
            confirmLabel="Ha, rad etish" onConfirm={() => decide(a.step_id, 'rejected')}/>
          {chat && onOpenChat && <button className="btn ghost" onClick={() => onOpenChat(chat)}>Suhbatni ochish</button>}
          {onOpenTask && <button className="btn ghost" onClick={() => onOpenTask(a.task_id)}>Texnik tafsilot</button>}
        </div>
      </article>;
    })}
    {!canDecide && <p className="muted">Qaror qilish egasi va operator uchun, bot to‘xtatilmagan bo‘lishi kerak.</p>}
  </section>;
}

// ---------------------------------------------------------------------- inbox

type Message = {channel: string; event_key: string; status: string; error: string; result: Record<string, unknown>;
  text: string; truncated: boolean; sender: string; conversation_id: string};

export function InboxPanel({client, tenant, canWrite}: Base & {canWrite: boolean}) {
  const load = useCallback(async () =>
    (await client.request<{events: Message[]}>(shopPath(tenant, 'inbox'))).events, [client, tenant]);
  const {data, loading, error, setError, refresh} = useLoader(load);
  return <section className="panel">
    <div className="panel-head"><h2>Kiruvchi hodisalar</h2>
      <button className="btn small" disabled={loading} onClick={() => void refresh()}>Yangilash</button></div>
    <Alert text={error}/>
    {data && data.length === 0 && <p>Hali xabar yo‘q.</p>}
    {data?.map(e => <article key={e.channel + e.event_key} className="row">
      <strong>{channelLabel(e.channel)}</strong> · {e.sender || 'noma’lum'} · <span className="muted">{statusLabel(e.status)}{e.error && ` (${e.error})`}</span>
      <div className="message-card">{e.text || <em>Matn yo‘q</em>}{e.truncated && '…'}</div>
      <small className="muted">Suhbat: {e.conversation_id || '—'} · kalit: {e.event_key}
        {typeof e.result?.task_id === 'string' && ` · vazifa: ${String(e.result.task_id).slice(0, 10)}`}</small>
      {e.status === 'failed' && <div><button className="btn" disabled={loading || !canWrite} onClick={async () => {
        try { await client.request(`/platform/${encodeURIComponent(tenant)}/inbox/retry`, {channel: e.channel, key: e.event_key}); await refresh(); }
        catch (err) { setError(friendlyError(err)); }
      }}>Sozlama tuzatilgach qayta urinish</button></div>}
    </article>)}
  </section>;
}

// --------------------------------------------------------------------- orders

type RecordOrder = {id: string; title: string; body: string; created: number};
type CustomerOrder = {id: string; external_id: string; status: string; currency: string; total_minor: number;
  created: number; updated: number; customer_id: string; customer_name: string | null};

const orderTone: Record<string, string> = {paid: 'ok', fulfilled: 'ok', cancelled: 'danger', refunded: 'warn', new: 'info', pending: 'warn'};

export function OrdersPanel({client, tenant, onOpenChat}: Base & {onOpenChat?: (ref: ThreadRef) => void}) {
  const load = useCallback(async () =>
    client.request<{orders: RecordOrder[]; customer_orders: CustomerOrder[]}>(shopPath(tenant, 'orders')), [client, tenant]);
  const {data, loading, error, refresh} = useLoader(load);
  return <section className="panel">
    <div className="panel-head"><h2>Buyurtmalar</h2>
      <button className="btn small" disabled={loading} onClick={() => void refresh()}>Yangilash</button></div>
    <p className="muted">Suhbatda olingan buyurtma avval “Tasdiqlar”da kutadi, tasdiqlangach shu yerda chiqadi (oxirgi 100 ta).</p>
    <Alert text={error}/>
    {loading && !data && <Skeleton rows={3}/>}
    {data && <>
      <h3>Suhbatdan olingan · {data.orders.length}</h3>
      {data.orders.length === 0 && <p>Hali buyurtma yo‘q.</p>}
      {data.orders.map(o => {
        // A conversation-captured order carries its draft as JSON; anything else stays raw text.
        const v = orderView(o.body);
        return <article key={o.id} className="row">
          <div className="panel-head"><strong>{o.title || o.id.slice(0, 10)}</strong><span className="muted"><When at={o.created}/></span></div>
          {v ? <>
            <OrderCard order={v}/>
            {v.conversationId && v.channel && onOpenChat &&
              <button className="btn ghost small" onClick={() => onOpenChat({channel: v.channel, conversation_id: v.conversationId})}>
                {channelLabel(v.channel)} suhbatini ochish</button>}
          </> : o.body && <p className="pre">{o.body}</p>}
        </article>;
      })}
      <h3>Mijozlar bazasidagi buyurtmalar · {data.customer_orders.length}</h3>
      {data.customer_orders.length === 0 ? <p>Buyurtma yo‘q.</p> : <div className="table-wrap"><table className="data"><thead><tr>
        <th>Raqam</th><th>Mijoz</th><th>Holat</th><th className="num">Jami</th><th>Yangilangan</th>
      </tr></thead><tbody>{data.customer_orders.map(o => <tr key={o.id}>
        <td><code>{o.external_id}</code></td><td>{o.customer_name || o.customer_id.slice(0, 10)}</td>
        <td><span className={`chip ${orderTone[o.status] || ''}`}>{statusLabel(o.status)}</span></td>
        <td className="num">{formatMinor(o.total_minor, o.currency)}</td><td><When at={o.updated}/></td>
      </tr>)}</tbody></table></div>}
    </>}
  </section>;
}

// -------------------------------------------------------------------- catalog

type Product = {id: string; name: string; price_uzs: number; sizes: (string | number)[]; description?: string;
  category?: string; gender?: string; age?: string; colors?: string[]; stock?: Record<string, number>; photo_url?: string};

export function CatalogPanel({client, tenant}: Base) {
  const load = useCallback(async () =>
    (await client.request<{products: Product[]}>(shopPath(tenant, 'products'))).products, [client, tenant]);
  const {data, loading, error, refresh} = useLoader(load);
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const shown = (data || []).filter(p => !q ||
    [p.id, p.name, p.category, p.age, ...(p.colors || [])].join(' ').toLowerCase().includes(q));
  return <section>
    <div className="panel-head"><h2>Katalog{data ? ` · ${data.length}` : ''}</h2>
      <button className="btn small" disabled={loading} onClick={() => void refresh()}>Yangilash</button></div>
    <p className="muted">Bot mijozlarga shu ro‘yxatdagi narx va qoldiqni aytadi. O‘zgartirish do‘kon sozlamasi (pack) orqali qilinadi.</p>
    <label style={{maxWidth: 420}}>Mahsulot qidirish
      <input type="search" name="q" autoComplete="off" spellCheck={false} placeholder="Nomi, yoshi yoki rangi…"
        value={query} onChange={e => setQuery(e.target.value)}/></label>
    <Alert text={error}/>
    {data && shown.length === 0 && <p>Hech narsa topilmadi.</p>}
    <div className="catalog">{shown.map(p => {
      const stock = stockView(p);
      const photo = safePhotoUrl(p.photo_url);
      return <article key={p.id} className="product">
        <div className="photo">{photo ? <img src={photo} alt={p.name} loading="lazy" referrerPolicy="no-referrer"/>
          : <span aria-hidden="true" title="Rasm yo‘q">{p.name.trim().charAt(0).toUpperCase() || '?'}</span>}</div>
        <div className="body">
          <strong>{p.name}</strong>
          <span className="price">{formatSum(p.price_uzs)}</span>
          <span className="muted">{[p.age, p.category, p.gender].filter(Boolean).join(' · ') || <code>{p.id}</code>}</span>
          {!!p.colors?.length && <span className="muted">Rang: {p.colors.join(', ')}</span>}
          {stock.sizes.length > 0 && <div className="sizes" aria-label="O‘lchamlar va qoldiq">
            {stock.sizes.map(s => <span key={s.size} className={`chip ${s.qty === null ? '' : s.qty > 0 ? 'ok' : 'out'}`}
              title={s.qty === null ? 'Qoldiq yuritilmaydi' : s.qty > 0 ? `${s.qty} dona bor` : 'Tugagan'}>
              {s.size}{s.qty !== null && ` · ${s.qty}`}</span>)}
          </div>}
          <span className="muted">{stock.tracked ? (stock.total ? `Omborda jami ${stock.total} dona` : 'Hammasi tugagan') : 'Qoldiq yuritilmaydi'}</span>
        </div>
      </article>;
    })}</div>
  </section>;
}

// ------------------------------------------------------------------- channels

type Channel = {channel: string; configured: boolean; ready: boolean; problem?: string; credentials: {env: string; set: boolean}[]};

export function ChannelsPanel({client, tenant}: Base) {
  const load = useCallback(async () =>
    (await client.request<{channels: Channel[]}>(shopPath(tenant, 'channels'))).channels, [client, tenant]);
  const {data, loading, error, refresh} = useLoader(load);
  return <section className="panel">
    <div className="panel-head"><h2>Kanallar</h2>
      <button className="btn small" disabled={loading} onClick={() => void refresh()}>Yangilash</button></div>
    <p className="muted">Bot qaysi ilovalarda mijozlarga javob bera oladi. Kalitlarning o‘zi hech qachon brauzerga yuborilmaydi.</p>
    <Alert text={error}/>
    {data?.map(c => <article key={c.channel} className="row">
      <div className="panel-head"><strong>{channelLabel(c.channel)}</strong>
        <span className={`chip ${c.ready ? 'ok' : c.configured ? 'warn' : ''}`}>
          {c.ready ? 'Ulangan' : c.configured ? 'Kalit yetishmaydi' : 'Ulanmagan'}</span></div>
      {c.problem && <p role="status" className="notice warn">Sozlamada muammo: {c.problem}</p>}
      {c.credentials.map(k => <p key={k.env} className="muted"><code>{k.env}</code> · {k.set ? 'serverda o‘rnatilgan' : 'serverda yo‘q'}</p>)}
    </article>)}
  </section>;
}
