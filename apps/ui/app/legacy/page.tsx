"use client";

import { useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
// Prod: HTTPS sahifa + HTTP API = mixed-content block. Prod'da NEXT_PUBLIC_API_URL https bo'lsin.
const TENANT = process.env.NEXT_PUBLIC_TENANT || "demo-retail";

type Stats = { orders_total: number; orders_new: number; pending_approvals: number; hot_leads: number };
type Approval = { id: string; summary: string; status: string };
type Lead = { sender: string; text: string; channel: string };
type OrderRow = { id: string; customer: string; phone: string; product_id: string; qty: number; branch_id: string; status: string };
type AgentNode = { id: string; name: string; tools: string[]; ladder: string; pending: number; done_today: number };

const LADDER_COLOR: Record<string, string> = { human_led: "#94a3b8", human_assisted: "#f59e0b", autonomous: "#22c55e" };

async function api<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${API}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", "X-Admin-Token": token, ...(init?.headers || {}) },
    });
  } catch {
    throw new Error("Backendga ulanib bo'lmadi (API o'chiqmi?)");
  }
  if (r.status === 403) throw new Error("Token noto'g'ri yoki huquq yo'q (403)");
  if (!r.ok) {
    let detail = `${r.status}`;
    try {
      const body = await r.json();
      if (body && body.detail) detail += ` — ${body.detail}`;
    } catch { /* json bo'lmasa status yetadi */ }
    throw new Error(`${path}: ${detail}`);
  }
  return r.json() as Promise<T>;
}

export default function Home() {
  const [token, setToken] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [pending, setPending] = useState<Approval[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [agents, setAgents] = useState<AgentNode[]>([]);
  const [shop, setShop] = useState("");
  const [selAgent, setSelAgent] = useState<AgentNode | null>(null);
  const [cmd, setCmd] = useState("");
  const [cmdReply, setCmdReply] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [cmdBusy, setCmdBusy] = useState(false);
  const seqRef = useRef(0);

  async function newKey(): Promise<string> {
    try {
      return crypto.randomUUID();
    } catch {
      return "ui-" + Date.now().toString(36) + "-" + Math.floor(Math.random() * 1e9).toString(36);
    }
  }

  async function refresh(t = token) {
    const my = ++seqRef.current;
    try {
      setErr("");
      const [s, p, l, o, a] = await Promise.all([
        api<Stats>(`/stats/daily?tenant=${TENANT}`, t),
        api<{ pending: Approval[] }>(`/approvals/pending?tenant=${TENANT}`, t),
        api<{ pending: Lead[] }>(`/leads/pending?tenant=${TENANT}`, t),
        api<{ orders: OrderRow[] }>(`/orders?tenant=${TENANT}`, t),
        api<{ shop: string; agents: AgentNode[] }>(`/agents?tenant=${TENANT}`, t),
      ]);
      if (seqRef.current !== my) return; // eskirgan javob — tashlaymiz
      setStats(s);
      setPending(p.pending);
      setLeads(l.pending);
      setOrders(o.orders);
      setAgents(a.agents);
      setShop(a.shop);
      setSelAgent((prev) => (prev ? a.agents.find((x) => x.id === prev.id) || null : prev));
    } catch (e: unknown) {
      if (seqRef.current !== my) return;
      setErr(e instanceof Error ? e.message : "Xatolik");
    }
  }

  async function decide(id: string, decision: "approved" | "rejected") {
    setBusy(id);
    try {
      await api(`/approvals/${id}/decide`, token, {
        method: "POST",
        body: JSON.stringify({ decision, reason }),
      });
      setReason("");
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Xatolik");
    } finally {
      setBusy(null);
      await refresh(); // 409/404 da ham ro'yxatni yangilaydi
    }
  }

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 900, margin: "0 auto", padding: 24 }}>
      <h1>Agent Platform — Operator</h1>
      <label htmlFor="admintoken">
        Admin token:{" "}
        <input id="admintoken" type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="dev: bo'sh" />{" "}
        <button type="button" onClick={() => refresh()}>Yangilash</button>
      </label>
      {err && <p role="alert" style={{ color: "red" }}>{err}</p>}
      {stats && (
        <section>
          <h2>Bugun</h2>
          <p>Buyurtmalar: {stats.orders_total} (yangi: {stats.orders_new}) · Kutilayotgan: {stats.pending_approvals} · Hot-lead: {stats.hot_leads}</p>
        </section>
      )}
      <section>
        <h2>Buyruq paneli</h2>
        <form onSubmit={async (e) => {
          e.preventDefault();
          if (!cmd.trim() || cmdBusy) return;
          setCmdBusy(true);
          try {
            const r = await api<{ reply: string }>(`/simulate`, token, {
              method: "POST",
              body: JSON.stringify({ tenant: TENANT, text: cmd, key: await newKey() }),
            });
            setCmdReply(r.reply);
            setErr("");
          } catch (err: unknown) {
            setErr(err instanceof Error ? err.message : "Xatolik");
            setCmdReply("");
          } finally {
            setCmdBusy(false);
          }
        }}>
          <label htmlFor="cmd">Buyruq: </label>
          <input id="cmd" value={cmd} onChange={(e) => setCmd(e.target.value)} placeholder="KB001 narxi?" size={40} maxLength={2000} />{" "}
          <button type="submit" disabled={cmdBusy}>{cmdBusy ? "Yuborilmoqda..." : "Yuborish"}</button>
        </form>
        {cmdReply && <p><b>Javob:</b> {cmdReply}</p>}
      </section>
      <section>
        <h2>Agent xaritasi{shop ? ` — ${shop}` : ""}</h2>
        {agents.length > 0 && (
          <svg width="100%" height="320" viewBox="0 0 600 320" role="group" aria-label="Agentlar xaritasi">
            <circle cx="300" cy="160" r="34" fill="#0ea5e9" />
            <text x="300" y="165" textAnchor="middle" fill="#fff" fontSize="13">AI</text>
            {agents.map((ag, i) => {
              const ang = (2 * Math.PI * i) / agents.length - Math.PI / 2;
              const x = 300 + 120 * Math.cos(ang);
              const y = 160 + 110 * Math.sin(ang);
              const color = ag.pending > 0 ? "#ef4444" : (LADDER_COLOR[ag.ladder] || "#94a3b8");
              const label = ag.name.length > 14 ? ag.name.slice(0, 13) + "…" : ag.name;
              return (
                <g key={ag.id} onClick={() => setSelAgent(ag)}
                   onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelAgent(ag); } }}
                   tabIndex={0} role="button" aria-label={`${ag.name}, ${ag.ladder}, kutilmoqda ${ag.pending}`}
                   style={{ cursor: "pointer" }}>
                  <line x1="300" y1="160" x2={x} y2={y} stroke="#cbd5e1" />
                  <circle cx={x} cy={y} r="20" fill={color} />
                  <text x={x} y={y + 4} textAnchor="middle" fill="#fff" fontSize="10">{ag.done_today}</text>
                  <title>{ag.name}</title>
                  <text x={x} y={y + 36} textAnchor="middle" fontSize="10">{label}</text>
                </g>
              );
            })}
          </svg>
        )}
        {agents.length === 0 && <p>Agentlar yuklanmadi — yangilashni bosing.</p>}
        {selAgent && (
          <div style={{ border: "1px solid #ccc", padding: 8, marginTop: 8 }}>
            <b>{selAgent.name}</b> ({selAgent.id}) — {selAgent.ladder} · kutilmoqda: {selAgent.pending} · bugun: {selAgent.done_today}
            <div>Tool'lar: {selAgent.tools.join(", ")}</div>
            <button type="button" onClick={() => setSelAgent(null)}>Yopish</button>
          </div>
        )}
        <p style={{ fontSize: 12 }}>Rang: kulrang=led, sariq=assisted, yashil=autonomous, qizil=tasdiq kutilmoqda. Son — bugun bajarilgan.</p>
      </section>
      <section>
        <h2>Tasdiq navbati ({pending.length})</h2>
        <label htmlFor="izoh">Izoh (rad sababi): <input id="izoh" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="ixtiyoriy" maxLength={200} /></label>
        {pending.map((a) => (
          <div key={a.id} style={{ border: "1px solid #ccc", padding: 8, marginBottom: 8 }}>
            <div><b>{a.id}</b> — {a.summary}</div>
            <button type="button" disabled={busy !== null} onClick={() => decide(a.id, "approved")}>
              {busy === a.id ? "Kutilmoqda..." : "Tasdiqlash"}
            </button>{" "}
            <button type="button" disabled={busy !== null} onClick={() => decide(a.id, "rejected")}>Rad etish</button>
          </div>
        ))}
        {pending.length === 0 && <p>Navbat bo'sh.</p>}
      </section>
      <section>
        <h2>Buyurtmalar ({orders.length})</h2>
        {orders.length > 0 && (
          <table>
            <thead><tr><th>ID</th><th>Mijoz</th><th>Telefon</th><th>Tovar</th><th>Son</th><th>Filial</th><th>Holat</th></tr></thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id}><td>{o.id}</td><td>{o.customer}</td><td>{o.phone}</td><td>{o.product_id}</td><td>{o.qty}</td><td>{o.branch_id}</td><td>{o.status}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        {orders.length === 0 && <p>Buyurtma yo'q.</p>}
      </section>
      <section>
        <h2>Hot-leadlar ({leads.length})</h2>
        {leads.map((l, i) => (
          <div key={`${l.sender}-${i}`} style={{ border: "1px solid #ccc", padding: 8, marginBottom: 8 }}>
            <div><b>{l.channel}</b> {l.sender}</div>
            <div>{l.text}</div>
          </div>
        ))}
        {leads.length === 0 && <p>Lead yo'q.</p>}
      </section>
    </main>
  );
}
