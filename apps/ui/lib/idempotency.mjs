/** Idempotency keys that survive an explicit retry.
 *
 * A key minted per click turns "the response was lost, click again" into a second
 * customer message or a second task. Here a key belongs to the exact payload: the same
 * action with the same body gets the same key until it succeeds (`settle`), and a
 * changed body gets a new one. There is still no automatic retry -- the user decides
 * to click again; this only makes that click safe. Memory only, bounded. */

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value).sort()) out[k] = canonical(value[k]);
    return out;
  }
  return value;
}

export function stableSignature(value) { return JSON.stringify(canonical(value)); }

function uuid() { return globalThis.crypto.randomUUID(); }

export function createIdempotency(make = uuid, limit = 32) {
  const pending = new Map();
  const sig = (action, payload) => stableSignature([String(action), payload ?? null]);
  return {
    key(action, payload) {
      const s = sig(action, payload);
      let key = pending.get(s);
      if (!key) {
        key = make();
        pending.set(s, key);
        while (pending.size > limit) pending.delete(pending.keys().next().value);
      }
      return key;
    },
    settle(action, payload) { pending.delete(sig(action, payload)); },
    get size() { return pending.size; },
  };
}
