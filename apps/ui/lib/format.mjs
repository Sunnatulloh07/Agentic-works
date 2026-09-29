/** One place for how the dashboard writes dates, statuses, roles and errors.
 * Pure: no storage or network. Dates are always Tashkent wall-clock time, whatever
 * the browser's own zone is, because the shop, its customers and its operators are
 * all there. Month names are fixed here rather than taken from the browser, so every
 * browser shows the same words. */

const MONTHS = ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust',
  'sentabr', 'oktabr', 'noyabr', 'dekabr'];
const TASHKENT_OFFSET = 5 * 3600; // UTC+5, no daylight saving since 1992

let clock = null;
try {
  clock = new Intl.DateTimeFormat('uz-Latn-UZ', {timeZone: 'Asia/Tashkent', year: 'numeric', month: 'numeric',
    day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
} catch { clock = null; }

function valid(seconds) { return typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0; }

function parts(seconds) {
  if (clock) {
    const out = {};
    for (const p of clock.formatToParts(new Date(seconds * 1000))) if (p.type !== 'literal') out[p.type] = Number(p.value);
    if ([out.year, out.month, out.day, out.hour, out.minute].every(Number.isFinite))
      return {year: out.year, month: out.month, day: out.day, hour: out.hour % 24, minute: out.minute};
  }
  const d = new Date((seconds + TASHKENT_OFFSET) * 1000);
  return {year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate(), hour: d.getUTCHours(), minute: d.getUTCMinutes()};
}

const two = (n) => String(n).padStart(2, '0');
const hm = (p) => `${two(p.hour)}:${two(p.minute)}`;
const dayMonth = (p) => `${p.day}-${MONTHS[p.month - 1]}`;
const dayIndex = (p) => Date.UTC(p.year, p.month - 1, p.day) / 86400000;

/** "21-sentabr 2026, 19:13" in Tashkent time; "—" when there is no time. */
export function formatDate(seconds) {
  if (!valid(seconds)) return '—';
  const p = parts(seconds);
  return `${dayMonth(p)} ${p.year}, ${hm(p)}`;
}

export function formatTime(seconds) {
  return valid(seconds) ? hm(parts(seconds)) : '—';
}

/** Short relative time for lists; the full date belongs in a title attribute. */
export function formatRelative(seconds, nowSeconds) {
  if (!valid(seconds) || !valid(nowSeconds)) return '—';
  const diff = nowSeconds - seconds;
  if (diff < -60) return formatDate(seconds);
  if (diff < 60) return 'hozirgina';
  if (diff < 3600) return `${Math.floor(diff / 60)} daqiqa oldin`;
  const p = parts(seconds), now = parts(nowSeconds);
  const days = dayIndex(now) - dayIndex(p);
  if (days === 0) return `bugun, ${hm(p)}`;
  if (days === 1) return `kecha, ${hm(p)}`;
  if (p.year === now.year) return `${dayMonth(p)}, ${hm(p)}`;
  return formatDate(seconds);
}

const STATUS = {
  succeeded: 'Bajarildi', failed: 'Xato', uncertain: 'Natija noma’lum', waiting_approval: 'Tasdiq kutmoqda',
  running: 'Bajarilmoqda', queued: 'Navbatda', cancelled: 'Bekor qilingan', pending: 'Kutilmoqda',
  planning: 'Rejalashtirilmoqda', waiting_task: 'Vazifani kutmoqda', needs_input: 'Aniqlik kerak',
  escalated: 'Odamga uzatildi', approved: 'Tasdiqlangan', rejected: 'Rad etilgan', expired: 'Muddati o‘tgan',
  new: 'Yangi', paid: 'To‘langan', refunded: 'Qaytarilgan', fulfilled: 'Topshirilgan',
  active: 'Faol', inactive: 'Nofaol', blocked: 'Bloklangan', done: 'Bajarildi', accepted: 'Qabul qilindi',
};
export function statusLabel(status) {
  if (status === undefined || status === null) return '';
  return Object.hasOwn(STATUS, status) ? STATUS[status] : String(status);
}

// Usage-budget reservation ledger. `released`: the call failed before it was billed and
// the hold went back. `unreconciled`: the parallel slot was freed after 10 minutes but the
// money stays held until the owner reconciles it, so it needs a person, not a retry.
const RESERVATION = {reserved: 'Band qilingan', dispatching: 'Yuborilmoqda', settled: 'Yopilgan',
  uncertain: 'Natija noma’lum', released: 'Qaytarildi', unreconciled: 'Tekshirish kerak', cancelled: 'Bekor qilingan'};
export function reservationLabel(status) {
  if (status === undefined || status === null) return '';
  return Object.hasOwn(RESERVATION, status) ? RESERVATION[status] : String(status);
}
const RESERVATION_TONE = {settled: 'ok', released: 'ok', uncertain: 'warn', unreconciled: 'warn', dispatching: 'info'};
export function reservationTone(status) { return Object.hasOwn(RESERVATION_TONE, status) ? RESERVATION_TONE[status] : ''; }

const ROLES = {owner: 'Egasi', operator: 'Operator', integrator: 'Integrator', viewer: 'Kuzatuvchi'};
export function roleLabel(role) { return Object.hasOwn(ROLES, role) ? ROLES[role] : String(role ?? ''); }

const CHANNELS = {telegram: 'Telegram', instagram: 'Instagram', whatsapp: 'WhatsApp', web: 'Veb-sayt',
  operator: 'Operator', dashboard: 'Boshqaruv paneli'};
export function channelLabel(channel) { return Object.hasOwn(CHANNELS, channel) ? CHANNELS[channel] : String(channel ?? ''); }

const NETWORK = /failed to fetch|networkerror|load failed|network request failed|fetch failed/i;

/** What went wrong and what to do, in the dashboard's voice. */
export function friendlyError(err) {
  if (!(err instanceof Error)) return 'Amal bajarilmadi. Qayta urinib ko‘ring.';
  if (err instanceof TypeError && NETWORK.test(err.message)) return 'Server bilan aloqa yo‘q. Internetni tekshirib, qayta urinib ko‘ring.';
  const status = err.status;
  if (status === 401) return 'Sessiya tugagan. Qayta kiring.';
  if (status === 403) return 'Bu amal uchun ruxsat yo‘q yoki bot to‘xtatilgan.';
  if (status === 404) return 'Ma’lumot topilmadi. Ro‘yxatni yangilang.';
  if (status === 409) return 'Holat o‘zgargan. Ro‘yxatni yangilab, qayta urinib ko‘ring.';
  if (status === 422) return 'Kiritilgan ma’lumot noto‘g‘ri. Maydonlarni tekshiring.';
  if (status === 429) return 'Juda ko‘p so‘rov. Bir daqiqadan keyin qayta urinib ko‘ring.';
  if (typeof status === 'number' && status >= 500) return 'Serverda xato. Birozdan keyin qayta urinib ko‘ring.';
  return err.message || 'Amal bajarilmadi. Qayta urinib ko‘ring.';
}
