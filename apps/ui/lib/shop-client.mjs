/** Pure helpers for the shop views: paths, draft rendering, number parsing, polling.
 * No storage, network or credentials -- the SessionClient does the transport. */

const TENANT = /^[A-Za-z0-9_-]{1,64}$/;
const RESOURCES = {
  approvals: '/approvals?status=pending',
  inbox: '/inbox/messages',
  products: '/products',
  orders: '/orders',
  channels: '/channels',
};

export function shopPath(tenant, resource) {
  if (typeof tenant !== 'string' || !TENANT.test(tenant) || !Object.hasOwn(RESOURCES, resource)) {
    throw new Error('Noto‘g‘ri endpoint');
  }
  return `/platform/${encodeURIComponent(tenant)}${RESOURCES[resource]}`;
}

export function approvalPath(tenant, step) {
  if (typeof tenant !== 'string' || !TENANT.test(tenant) ||
      typeof step !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(step)) throw new Error('Noto‘g‘ri endpoint');
  return `/platform/${encodeURIComponent(tenant)}/steps/${encodeURIComponent(step)}/approval`;
}

// Roles the API lets read the inbox; anything else gets a 403 from /inbox.
export function canReadInbox(role) { return role === 'owner' || role === 'operator'; }

const RECIPIENT_FIELDS = ['conversation_id', 'contact', 'recipient', 'to', 'chat_id'];

/** A pending step as the operator should read it. Outbound `*.send` tools become
 * "to whom, what text"; anything else stays as pretty JSON, never hidden. */
export function draftSummary(tool, args) {
  const a = args && typeof args === 'object' && !Array.isArray(args) ? args : {};
  if (typeof tool === 'string' && tool.endsWith('.send')) {
    const field = RECIPIENT_FIELDS.find(k => typeof a[k] === 'string' || typeof a[k] === 'number');
    const text = typeof a.text === 'string' ? a.text : (typeof a.message === 'string' ? a.message : '');
    return {kind: 'message', channel: tool.slice(0, -'.send'.length),
            recipient: field ? String(a[field]) : '', text};
  }
  return {kind: 'args', text: JSON.stringify(a, null, 2)};
}

/** A whole, non-negative integer from a form field, or an error before the network.
 * `Number('1.5')` would reach a strict int field and come back as a 422. */
export function wholeNumber(value, min = 0, max = Number.MAX_SAFE_INTEGER) {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!/^\d{1,16}$/.test(text)) throw new Error('Butun son kiriting');
  const n = Number(text);
  if (!Number.isSafeInteger(n) || n < min || n > max) throw new Error(`Qiymat ${min}–${max} oralig‘ida bo‘lsin`);
  return n;
}

export function formatSum(value, currency = 'so‘m') {
  if (!Number.isFinite(value)) return '—';
  const whole = String(Math.trunc(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return `${whole} ${currency}`;
}

/** Bootstrap body with the API's own minimums, checked before the round trip. */
export function bootstrapBody({email, password, displayName, workspaceId, workspaceName}) {
  const out = {email: String(email ?? '').trim(), password: String(password ?? ''),
    display_name: String(displayName ?? '').trim(), workspace_id: String(workspaceId ?? '').trim(),
    workspace_name: String(workspaceName ?? '').trim()};
  if (out.email.length < 3 || !out.password || !out.display_name || !out.workspace_name ||
      !/^[A-Za-z0-9_-]{2,64}$/.test(out.workspace_id)) throw new Error('Barcha maydonlarni to‘ldiring');
  return out;
}

/** Calls `fn` every `interval` ms while the page is visible; returns a stop function.
 * A call is skipped while the tab is hidden or the previous call is still running. */
export function startAutoRefresh(fn, interval = 10000, env = globalThis) {
  if (typeof fn !== 'function' || !Number.isFinite(interval) || interval < 1000) throw new Error('Yaroqsiz interval');
  let running = false;
  const id = env.setInterval(async () => {
    if (running || env.document?.hidden) return;
    running = true;
    try { await fn(); } catch { /* the panel shows its own error */ } finally { running = false; }
  }, interval);
  return () => env.clearInterval(id);
}
