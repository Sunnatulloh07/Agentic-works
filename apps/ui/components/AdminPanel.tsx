'use client';
import {useState} from 'react';
import {type SessionClient, type Workspace} from '../lib/session-client.mjs';

/**
 * Identity and membership administration.
 *
 * Seven identity routes were reachable only by hand-written HTTP: `logout-all`,
 * `sessions`, workspace selection, invitations, invitation acceptance, member revocation
 * and first-owner bootstrap. The backend was complete and the dashboard showed none of
 * it, which is what `ui_full_product: PARTIAL` meant in the backlog.
 *
 * Two things are deliberately awkward here, because they are awkward in the product:
 * session revocation ends with the operator signed out of every device including this
 * one, and bootstrap needs a separate admin token that the session client cannot hold.
 * The UI says so rather than smoothing it over.
 */

type Panel = {background: string; border: string; borderRadius: number; padding: number; marginBottom: number};
const panel: Panel = {background: '#111c2e', border: '1px solid #233149', borderRadius: 12, padding: 20, marginBottom: 16};
const control = {display: 'block', background: '#0b1220', color: '#e2e8f0', border: '1px solid #475569', padding: 8, margin: '8px 0', borderRadius: 6, maxWidth: '100%', boxSizing: 'border-box' as const};
const button = {...control, display: 'inline-block', marginRight: 8, cursor: 'pointer'};
const pre = {whiteSpace: 'pre-wrap' as const, overflowWrap: 'anywhere' as const, fontSize: 12, background: '#0b1220', padding: 12, borderRadius: 6};

type Session = {id: string; workspace_id: string; created: number; expires: number; current?: boolean};
type Invitation = {id: string; email: string; role: string; expires: number};
type Membership = {workspace_id: string; user_id: string; role: string; status: string};
type AdminProps = {client: SessionClient; workspace: Workspace; exit: () => void};

export default function AdminPanel({client, workspace, exit}: AdminProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [members, setMembers] = useState<Membership[]>([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('operator');
  const [inviteToken, setInviteToken] = useState('');
  const [acceptToken, setAcceptToken] = useState('');
  const [revokeUser, setRevokeUser] = useState('');
  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('');
    try { await fn(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Xato'); }
    finally { setBusy(false); }
  }
  return <section style={panel}>
    <h2>Hisob va a’zolik boshqaruvi</h2>
    <p>Sessiyalar, workspace almashtirish, taklifnomalar va a’zolikni bekor qilish.
      Rollar serverda tekshiriladi; bu panel hech qanday huquq bermaydi.</p>
    {error && <p role="alert" style={{color: '#fca5a5'}}>{error}</p>}
    {message && <p role="status" style={{color: '#86efac'}}>{message}</p>}
    {busy && <p role="status">Bajarilmoqda...</p>}

    <h3>Sessiyalar</h3>
    <p>Joriy hisobning barcha qurilmalardagi sessiyalari. <code>logout-all</code> —
      qaytarib bo‘lmaydigan amal: u <strong>shu sessiyani ham</strong> tugatadi.</p>
    <button style={button} disabled={busy} onClick={() => run(async () => {
      setSessions((await client.request<{sessions: Session[]}>('/identity/sessions')).sessions);
    })}>Sessiyalar ro‘yxati</button>
    <button style={button} disabled={busy} onClick={() => run(async () => {
      await client.request('/identity/logout-all', {});
      setSessions([]); setMessage('Barcha sessiyalar bekor qilindi. Qayta kiring.');
      exit();
    })}>Barcha qurilmalardan chiqish</button>
    {sessions.map(s => <p key={s.id}>{s.id.slice(0, 12)} · workspace {s.workspace_id || 'account'} ·
      tugaydi {new Date(s.expires * 1000).toLocaleString()}</p>)}

    <h3>Workspace</h3>
    <p>Joriy: <strong>{workspace.name}</strong> ({workspace.id}). Almashtirish yangi
      sessiya oladi va eski refresh tokenni bekor qiladi.</p>
    <button style={button} disabled={busy} onClick={() => run(async () => {
      setWorkspaces((await client.request<{workspaces: Workspace[]}>('/identity/workspaces')).workspaces);
    })}>Workspace ro‘yxati</button>
    {workspaces.map(w => <button key={w.id} style={button} disabled={busy || w.id === workspace.id}
      onClick={() => run(async () => { await client.select(w.id); window.location.reload(); })}>
      {w.name} · {w.role}{w.id === workspace.id ? ' · joriy' : ''}
    </button>)}

    <h3>Taklifnoma</h3>
    <p>Yaratilgan token <strong>bir marta</strong> ko‘rsatiladi va brauzerda saqlanmaydi.
      Uni xavfsiz kanal orqali yetkazing.</p>
    <label>Email<input style={control} type="email" maxLength={320} value={inviteEmail}
      onChange={e => setInviteEmail(e.target.value)}/></label>
    <label>Rol<select style={control} value={inviteRole} onChange={e => setInviteRole(e.target.value)}>
      <option value="operator">operator</option>
      <option value="integrator">integrator</option>
      <option value="viewer">viewer</option>
    </select></label>
    <button style={button} disabled={busy || !inviteEmail.trim()} onClick={() => run(async () => {
      const r = await client.request<{invitation: Invitation; token: string}>(
        `/identity/workspaces/${encodeURIComponent(workspace.id)}/invitations`,
        {email: inviteEmail.trim(), role: inviteRole});
      setInviteToken(r.token); setInviteEmail(''); setMessage('Taklifnoma yaratildi.');
    })}>Taklifnoma yaratish</button>
    {inviteToken && <div>
      <p role="status">Token faqat shu ekranda. Sahifani yangilasangiz yo‘qoladi.</p>
      <textarea aria-label="Taklifnoma tokeni" readOnly style={{...control, width: '100%'}} value={inviteToken}/>
      <button style={button} onClick={() => setInviteToken('')}>Tokenni yashirish</button>
    </div>}
    <label>Taklifnoma tokenini qabul qilish<input style={control} maxLength={256} value={acceptToken}
      onChange={e => setAcceptToken(e.target.value)}/></label>
    <button style={button} disabled={busy || acceptToken.trim().length < 20} onClick={() => run(async () => {
      const r = await client.request<{membership: Membership}>('/identity/invitations/accept',
        {token: acceptToken.trim()});
      setAcceptToken(''); setMessage(`A’zolik qabul qilindi: ${r.membership.workspace_id} · ${r.membership.role}.`);
    })}>Qabul qilish</button>

    <h3>A’zoni bekor qilish</h3>
    <p>Foydalanuvchi ID sini qo‘lda kiritasiz: <code>/identity/workspaces</code> a’zolar
      ro‘yxatini qaytarmaydi, shuning uchun bu maydon erkin matn. Bekor qilish
      foydalanuvchining <em>barcha</em> sessiyalarini tugatadi.</p>
    <label>Foydalanuvchi ID<input style={control} maxLength={128} value={revokeUser}
      onChange={e => setRevokeUser(e.target.value)}/></label>
    <button style={button} disabled={busy || !revokeUser.trim()} onClick={() => run(async () => {
      await client.request(`/identity/workspaces/${encodeURIComponent(workspace.id)}/members/${encodeURIComponent(revokeUser.trim())}/revoke`, {});
      setRevokeUser(''); setMessage('A’zolik bekor qilindi.');
    })}>A’zolikni bekor qilish</button>
    {members.length > 0 && <pre style={pre}>{JSON.stringify(members, null, 2)}</pre>}
  </section>;
}

/** First-owner bootstrap. Separate from the session panel on purpose.
 *
 * The route is gated twice: an `X-Admin-Token` header whose configured value must be at
 * least 32 characters, and `IDENTITY_BOOTSTRAP_ENABLED=true` in the deployment
 * environment. A dashboard session cannot supply either, so this form asks for the
 * admin token directly and never stores it. It exists because the alternative was
 * provisioning a first owner with `curl`, which leaves the token in shell history. */
export function BootstrapPanel({client}: {client: SessionClient}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [adminToken, setAdminToken] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [workspaceId, setWorkspaceId] = useState('');
  const [workspaceName, setWorkspaceName] = useState('');
  return <section style={panel}>
    <h2>Birinchi owner (bootstrap)</h2>
    <p>Deployment tomonida <code>ADMIN_TOKEN</code> (kamida 32 belgi) va
      <code> IDENTITY_BOOTSTRAP_ENABLED=true</code> bo‘lishi shart. Workspace uchun
      oldindan pack tayyorlangan bo‘lishi kerak — <code>template</code> qabul qilinmaydi.</p>
    {error && <p role="alert" style={{color: '#fca5a5'}}>{error}</p>}
    {message && <p role="status" style={{color: '#86efac'}}>{message}</p>}
    <label>Admin token<input style={control} type="password" autoComplete="off" maxLength={256}
      value={adminToken} onChange={e => setAdminToken(e.target.value)}/></label>
    <label>Email<input style={control} type="email" maxLength={320} value={email} onChange={e => setEmail(e.target.value)}/></label>
    <label>Parol<input style={control} type="password" autoComplete="new-password" maxLength={256}
      value={password} onChange={e => setPassword(e.target.value)}/></label>
    <label>Ko‘rsatiladigan ism<input style={control} maxLength={256} value={displayName} onChange={e => setDisplayName(e.target.value)}/></label>
    <label>Workspace ID<input style={control} maxLength={64} value={workspaceId} onChange={e => setWorkspaceId(e.target.value)}/></label>
    <label>Workspace nomi<input style={control} maxLength={256} value={workspaceName} onChange={e => setWorkspaceName(e.target.value)}/></label>
    <button style={button} disabled={busy || !adminToken || !email.trim() || !password || !workspaceId.trim() || !workspaceName.trim()}
      onClick={async () => {
        setBusy(true); setError(''); setMessage('');
        try {
          await client.request('/identity/bootstrap',
            {email: email.trim(), password, display_name: displayName.trim(),
             workspace_id: workspaceId.trim(), workspace_name: workspaceName.trim()},
            'POST', {'X-Admin-Token': adminToken});
          setMessage('Owner yaratildi. Endi oddiy kirish ekranidan kiring.');
          setAdminToken(''); setPassword('');
        } catch (e) { setError(e instanceof Error ? e.message : 'Xato'); }
        finally { setBusy(false); }
      }}>Bootstrap</button>
  </section>;
}
