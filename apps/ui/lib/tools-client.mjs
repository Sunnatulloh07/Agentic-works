/** Schema-driven tool calls for the dashboard.
 *
 * The catalogue already ships every tool's JSON schema, so an operator can fill a
 * form instead of hand-writing JSON. These helpers are pure: the SessionClient does
 * the transport and the engine validates the call again server-side -- nothing here
 * is a security boundary, it is a pre-flight so a typo comes back before the round
 * trip.
 *
 * The supported vocabulary is pinned on the Python side by
 * runtime_tests/test_schema_vocabulary.py: string / integer / boolean /
 * string-array / object, with enum only on strings and no negative integer
 * minimum. A shape outside it falls back to a JSON field, never dropped.
 */

const TENANT = /^[A-Za-z0-9_-]{1,64}$/;
const NAME = /^[A-Za-z0-9_.-]{1,128}$/;

export function submitPath(tenant) {
  if (typeof tenant !== 'string' || !TENANT.test(tenant)) throw new Error('Noto‘g‘ri workspace');
  return `/platform/${encodeURIComponent(tenant)}/tasks`;
}

/** The tools an agent may call, in catalogue order. An unknown agent yields []. */
export function toolChoices(tools, agent) {
  const allowed = new Set((agent && agent.tools) || []);
  return (Array.isArray(tools) ? tools : []).filter(t => t && typeof t.name === 'string'
    && allowed.has(t.name));
}

export function argumentFields(schema) {
  const properties = (schema && typeof schema === 'object' && schema.properties) || {};
  const required = new Set((schema && schema.required) || []);
  return Object.entries(properties).map(([name, spec]) => {
    const s = spec && typeof spec === 'object' ? spec : {};
    const base = {name, required: required.has(name), hint: schemaHint(s)};
    if (s.type === 'integer') return {...base, kind: 'integer', min: s.minimum, max: s.maximum};
    if (s.type === 'boolean') return {...base, kind: 'boolean'};
    if (s.type === 'array') return {...base, kind: 'list'};
    if (s.type === 'object') return {...base, kind: 'json'};
    if (Array.isArray(s.enum) && s.enum.length) return {...base, kind: 'choice', choices: s.enum.map(String)};
    if (s.type === 'string') return {...base, kind: 'text', maxLength: s.maxLength};
    return {...base, kind: 'json'};
  });
}

function schemaHint(spec) {
  const bits = [];
  if (Array.isArray(spec.enum) && spec.enum.length) bits.push(spec.enum.join(' / '));
  if (typeof spec.maxLength === 'number') bits.push(`≤${spec.maxLength} belgi`);
  if (typeof spec.minimum === 'number' || typeof spec.maximum === 'number')
    bits.push(`${spec.minimum ?? '—'}…${spec.maximum ?? '—'}`);
  return bits.join(' · ');
}

function integer(raw, field) {
  const text = String(raw).trim();
  // Non-negative by contract (pinned on the Python side): no declared integer
  // field has a negative minimum, so a minus sign here is a typo, not a value.
  if (!/^\d{1,16}$/.test(text)) throw new Error(`${field.name}: butun son kiriting`);
  const value = Number(text);
  if (!Number.isSafeInteger(value)) throw new Error(`${field.name}: son juda katta`);
  if (typeof field.min === 'number' && value < field.min) throw new Error(`${field.name}: ${field.min} dan kichik bo‘lmasin`);
  if (typeof field.max === 'number' && value > field.max) throw new Error(`${field.name}: ${field.max} dan katta bo‘lmasin`);
  return value;
}

function boolean(raw, field) {
  if (raw === true || raw === false) return raw;
  const text = String(raw).trim().toLowerCase();
  if (['true', '1', 'ha'].includes(text)) return true;
  if (['false', '0', 'yo‘q', ''].includes(text)) return false;
  throw new Error(`${field.name}: ha yoki yo‘q`);
}

function list(raw, field) {
  const text = String(raw).trim();
  if (!text) return [];
  return text.split('\n').map(line => line.trim()).filter(Boolean);
}

function jsonValue(raw, field) {
  try {
    return JSON.parse(String(raw));
  } catch {
    throw new Error(`${field.name}: JSON formati xato`);
  }
}

/** Form values -> one args object. Empty optional fields are omitted, required ones refuse. */
export function buildArguments(fields, values = {}) {
  const args = {};
  for (const field of fields) {
    const raw = values[field.name];
    const blank = raw === undefined || raw === null || (typeof raw === 'string' && !raw.trim());
    if (blank) {
      if (field.required) throw new Error(`${field.name} majburiy`);
      continue;
    }
    if (field.kind === 'integer') args[field.name] = integer(raw, field);
    else if (field.kind === 'boolean') args[field.name] = boolean(raw, field);
    else if (field.kind === 'list') args[field.name] = list(raw, field);
    else if (field.kind === 'json') args[field.name] = jsonValue(raw, field);
    else {
      const value = String(raw);
      if (typeof field.maxLength === 'number' && value.length > field.maxLength)
        throw new Error(`${field.name}: ${field.maxLength} belgidan oshmasin`);
      args[field.name] = value;
    }
  }
  return args;
}

/** The body POST /platform/{tenant}/tasks expects, one step, built before the network. */
export function toolCallBody({agent, tool, args, key}) {
  if (typeof agent !== 'string' || !NAME.test(agent)) throw new Error('Agent tanlanmagan');
  if (typeof tool !== 'string' || !NAME.test(tool)) throw new Error('Tool tanlanmagan');
  if (typeof key !== 'string' || !key.trim() || key.length > 256) throw new Error('Idempotency kaliti kerak');
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Argumentlar obyekt bo‘lishi shart');
  return {agent, key, steps: [{tool, args}]};
}
