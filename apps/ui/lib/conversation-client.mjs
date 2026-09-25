/** Pure helpers for the conversation views: paths, the operator-reply request,
 * thread rendering and order records. No storage, network or credentials -- the
 * SessionClient does the transport and the API decides every permission. */

const TENANT = /^[A-Za-z0-9_-]{1,64}$/;
const CHANNEL = /^[a-z]{1,32}$/;
const KEY = /^[A-Za-z0-9-]{8,128}$/;
const MAX_REPLY_CHARS = 4000;

function checkedId(value) {
  // A slash would change the route once the server decodes the path.
  if (typeof value !== 'string' || !value || value.length > 256 || /[/\\?#\x00-\x1f]/.test(value) ||
      value === '.' || value === '..') throw new Error('Noto‘g‘ri suhbat');
  return value;
}

function base(tenant) {
  if (typeof tenant !== 'string' || !TENANT.test(tenant)) throw new Error('Noto‘g‘ri endpoint');
  return `/platform/${encodeURIComponent(tenant)}`;
}

export function conversationsPath(tenant) { return `${base(tenant)}/conversations`; }
export function handoffsPath(tenant) { return `${base(tenant)}/handoffs`; }

export function threadPath(tenant, channel, conversationId) {
  if (typeof channel !== 'string' || !CHANNEL.test(channel)) throw new Error('Noto‘g‘ri kanal');
  return `${base(tenant)}/conversations/${channel}/${encodeURIComponent(checkedId(conversationId))}`;
}

/** POST target of the operator reply (implemented by app/conversation_api.py). */
export function replyPath(tenant, channel, conversationId) {
  return `${threadPath(tenant, channel, conversationId)}/reply`;
}

/** POST target that hands an operator-held chat back to the bot (app/shop_api.py). */
export function releasePath(tenant, channel, conversationId) {
  return `${threadPath(tenant, channel, conversationId)}/release`;
}

export function releaseRequest(key) {
  if (typeof key !== 'string' || !KEY.test(key)) throw new Error('Idempotency kaliti yaroqsiz');
  return {body: {}, method: 'POST', headers: {'Idempotency-Key': key}};
}

/** True while an operator holds the chat ({actor, until} in epoch seconds). */
export function takeoverActive(takeover, nowSeconds) {
  return Boolean(takeover && Number.isFinite(takeover.until) && takeover.until > nowSeconds);
}

/** The operator-reply contract: {text: 1..4000}, one Idempotency-Key per send attempt. */
export function replyRequest(text, key) {
  if (typeof text !== 'string' || !text.trim() || text.length > MAX_REPLY_CHARS)
    throw new Error(`Javob matni 1–${MAX_REPLY_CHARS} belgi bo‘lsin`);
  if (typeof key !== 'string' || !KEY.test(key)) throw new Error('Idempotency kaliti yaroqsiz');
  return {body: {text}, method: 'POST', headers: {'Idempotency-Key': key}};
}

export function replyErrorText(err) {
  if (!(err instanceof Error)) return 'Xato';
  if (err.status === 404) return 'Javob yuborish endpointi yoki suhbat topilmadi (404). ' + err.message;
  if (err.status === 409) return 'Bu kalit boshqa matn bilan ishlatilgan (409). Qayta yuboring.';
  if (err.status === 403) return 'Ruxsat yo‘q yoki ijro to‘xtatilgan (403).';
  if (err.status === 422) return 'Matn yoki suhbat yaroqsiz (422).';
  return err.message || 'Xato';
}

const ROLE_LABELS = {customer: 'Mijoz', agent: 'Agent', operator: 'Operator'};

/** History lines and operator replies as one list in time order. */
export function threadLines(thread) {
  const history = Array.isArray(thread?.history) ? thread.history : [];
  const replies = Array.isArray(thread?.operator_replies) ? thread.operator_replies : [];
  const lines = [
    ...history.map((h, i) => ({role: String(h.role), text: String(h.text ?? ''), at: Number(h.created) || 0, order: i})),
    ...replies.map((r, i) => ({role: 'operator', text: String(r.text ?? ''), at: Number(r.created) || 0,
      status: String(r.status ?? ''), order: history.length + i})),
  ];
  lines.sort((a, b) => a.at - b.at || a.order - b.order);
  return lines.map(({order, ...line}) => ({...line, label: ROLE_LABELS[line.role] ?? line.role}));
}

const TURN_STATUS = {queued: 'navbatda', open: 'agent javob tayyorlamoqda',
  delivering: 'yuborilmoqda yoki tasdiq kutmoqda', delivered: 'yetkazildi', failed: 'yuborilmadi',
  uncertain: 'yetgani noma’lum', operator: 'operator javob bermoqda'};
export function turnStatusLabel(status) { return TURN_STATUS[status] ?? String(status ?? ''); }

// Mirrors platform_runtime/conversation.HANDOFF_REASONS; unknown codes are shown as is.
const REASONS = {ungrounded_number: 'javobdagi son tasdiqlanmadi', llm_unavailable: 'model yoqilmagan',
  conversation_disabled: 'suhbat o‘chirilgan', empty_reply: 'agent javob bermadi',
  run_rejected: 'agent ishga tushmadi', send_uncertain: 'javob mijozga yetgani noma’lum',
  order_rejected: 'buyurtma yozilmadi'};
export function reasonLabel(reason) { return REASONS[reason] ?? String(reason ?? ''); }

/** An order record body written by a conversation turn, or null for any other body. */
export function orderView(body) {
  let d;
  try { d = JSON.parse(body); } catch { return null; }
  if (!d || typeof d !== 'object' || Array.isArray(d) || typeof d.product_id !== 'string' ||
      !Number.isFinite(d.total_uzs)) return null;
  const delivery = d.delivery && typeof d.delivery === 'object' ? d.delivery : {};
  return {
    product: d.product_name ? `${d.product_name} (${d.product_id})` : d.product_id,
    size: String(d.size ?? ''), qty: d.qty, total: d.total_uzs,
    customer: String(d.customer_name ?? ''), phone: String(d.phone ?? ''),
    delivery: delivery.type === 'branch' ? `Filial: ${delivery.branch_name || delivery.branch_id || ''}`
      : delivery.type === 'address' ? `Manzil: ${delivery.address ?? ''}` : '',
    channel: String(d.channel ?? ''), conversationId: String(d.conversation_id ?? ''),
  };
}
