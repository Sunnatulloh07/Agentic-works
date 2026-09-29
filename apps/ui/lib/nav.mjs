/** Dashboard navigation and the "needs you" counts behind its badges.
 *
 * Pure: the API decides every permission; these lists only avoid offering a tab the
 * API would refuse. Shop work (chats, approvals, orders, catalogue, settings) comes
 * first; everything else stays reachable under "Kengaytirilgan". */

import {takeoverActive} from './conversation-client.mjs';

const SHOP = ['owner', 'operator'];

// [id, label, who may see it]; `null` means any member.
const PRIMARY = [
  ['conversations', 'Suhbatlar', SHOP],
  ['approvals', 'Tasdiqlar', SHOP],
  ['orders', 'Buyurtmalar', null],
  ['catalog', 'Katalog', null],
  ['settings', 'Sozlamalar', null],
];

const ADVANCED = [
  ['tasks', 'Vazifalar', null],
  ['inbox', 'Kiruvchi hodisalar', SHOP],
  ['customers', 'Mijozlar bazasi', null],
  ['agents', 'Agentlar', null],
  ['agent-runs', 'Agent sikllari', null],
  ['audit', 'Audit jurnali', SHOP],
  ['devices', 'Qurilmalar', null],
  ['connections', 'Ma’lumot manbalari', ['owner', 'integrator']],
  ['budget', 'Xarajat budjeti', ['owner', 'operator', 'integrator']],
  ['knowledge', 'Bilim bazasi', ['owner', 'operator', 'integrator']],
  ['oauth', 'Google ulanishi', ['owner']],
  ['google-data', 'Google ma’lumotlari', ['owner']],
  ['reengagement', 'Qayta aloqa', SHOP],
  ['briefing', 'Brifing', SHOP],
  ['escalation', 'Eskalatsiya', SHOP],
  ['supervisor', 'Savollarni yo‘naltirish', SHOP],
  ['schedules', 'Takroriy vazifalar', SHOP],
  ['metrics', 'Tizim holati', null],
];

const allowed = (role) => ([, , who]) => who === null || who.includes(role);
const item = ([id, label]) => ({id, label});

export function navFor(role) {
  return {primary: PRIMARY.filter(allowed(role)).map(item), advanced: ADVANCED.filter(allowed(role)).map(item)};
}

/** Phone layout: at most four sections in the bottom bar plus one "Ko‘proq" slot that
 * opens Sozlamalar and the advanced list. Nothing reachable on desktop is lost. */
export function mobileNav(role) {
  const {primary, advanced} = navFor(role);
  const bottom = primary.filter(i => i.id !== 'settings').slice(0, 4);
  return {bottom, more: {main: primary.filter(i => !bottom.includes(i)), advanced}};
}

export function defaultTab(role) { return navFor(role).primary[0]?.id ?? 'orders'; }

export function conversationKey(ref) { return `${ref.channel}:${ref.conversation_id}`; }

const STUCK = new Set(['failed', 'uncertain', 'throttled']);

/** Whether a chat has a customer line nobody has looked at, and whether the bot has
 * stopped on it so a person must answer. `seen` maps a conversation key to the time
 * the operator last opened it; `since` is when this dashboard session started, so a
 * reload does not turn the whole history into "unread". */
export function conversationFlags(c, handoffs, seen, since, nowSeconds) {
  const key = conversationKey(c);
  const customerLast = c.last_role === 'customer';
  const mark = Math.max(Number(since) || 0, Number(seen?.[key]) || 0);
  let handoff = null;
  // A resolved handoff is a person's answer already: only OPEN ones make a chat "Javob kerak".
  for (const h of Array.isArray(handoffs) ? handoffs : [])
    if (!h.resolved && h.channel === c.channel && h.conversation_id === c.conversation_id && (!handoff || h.created > handoff.created)) handoff = h;
  const handedOff = Boolean(handoff && handoff.created >= c.last_at);
  const held = takeoverActive(c.takeover, nowSeconds);
  const needsHuman = customerLast && (STUCK.has(c.turn_status) || held || handedOff);
  const reason = handedOff ? handoff.reason : STUCK.has(c.turn_status) ? c.turn_status : held ? 'operator' : '';
  return {unread: customerLast && c.last_at > mark, needsHuman, reason};
}

/** Chats that need the operator: unread or waiting on a person. The open chat is excluded. */
export function attentionCount(conversations, handoffs, seen, since, nowSeconds, openKey) {
  let n = 0;
  for (const c of Array.isArray(conversations) ? conversations : []) {
    if (conversationKey(c) === openKey) continue;
    const f = conversationFlags(c, handoffs, seen, since, nowSeconds);
    if (f.unread || f.needsHuman) n++;
  }
  return n;
}

export function documentTitle(workspaceName, count) {
  const prefix = count > 0 ? `(${count > 99 ? '99+' : count}) ` : '';
  return prefix + (workspaceName ? `${workspaceName} — Agent Platform` : 'Agent Platform');
}
