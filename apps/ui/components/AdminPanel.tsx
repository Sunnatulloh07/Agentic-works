'use client';
import {useState} from 'react';
import {type SessionClient, type Workspace} from '../lib/session-client.mjs';
import {bootstrapBody} from '../lib/shop-client.mjs';
import {formatDate, friendlyError, roleLabel} from '../lib/format.mjs';
import {Alert, ConfirmButton} from './ui';

/**
 * Identity and membership administration ("Jamoa").
 *
 * Seven identity routes were reachable only by hand-written HTTP: `logout-all`,
 * `sessions`, workspace selection, invitations, invitation acceptance, member revocation
 * and first-owner bootstrap. This panel is their control surface.
 *
 * Two things are deliberately awkward here, because they are awkward in the product:
 * session revocation ends with the operator signed out of every device including this
 * one, and bootstrap needs a separate admin token that the session client cannot hold.
 * The UI says so rather than smoothing it over. Switching workspace swaps the session
 * in place (the gate applies it), it does not reload the page -- a reload would drop
 * the in-memory session and sign the operator out.
 */

type Session = {id: string; workspace_id: string; created: number; expires: number; current?: boolean};
type Invitation = {id: string; email: string; role: string; expires: number};
type Membership = {workspace_id: string; user_id: string; role: string; status: string};
type AdminProps = {client: SessionClient; workspace: Workspace; exit: () => void; switchWorkspace: (w: Workspace) => Promise<void>};

export default function AdminPanel({client, workspace, exit, switchWorkspace}: AdminProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('operator');
  const [inviteToken, setInviteToken] = useState('');
  const [acceptToken, setAcceptToken] = useState('');
  const [revokeUser, setRevokeUser] = useState('');
  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('');
    try { await fn(); }
    catch (e) { setError(friendlyError(e)); }
    finally { setBusy(false); }
  }
  return <section className="panel">
    <h2>Jamoa va hisob</h2>
    <p className="muted">Kim nima qila olishini server tekshiradi; bu yerdagi tugmalar hech kimga qo‘shimcha huquq bermaydi.</p>
    <Alert text={error}/>
    {message && <p role="status" className="notice success">{message}</p>}

    <h3>Do‘konlar</h3>
    <p>Hozir ochiq: <strong>{workspace.name}</strong></p>
    <button className="btn" disabled={busy} onClick={() => run(async () => {
      setWorkspaces((await client.request<{workspaces: Workspace[]}>('/identity/workspaces')).workspaces);
    })}>Boshqa do‘konga o‘tish</button>
    {workspaces.map(w => <button key={w.id} className="btn listbtn" disabled={busy || w.id === workspace.id}
      onClick={() => run(async () => { await switchWorkspace(w); })}>
      {w.name} · {roleLabel(w.role)}{w.id === workspace.id ? ' · hozir ochiq' : ''}
    </button>)}

    <h3>Xodim taklif qilish</h3>
    <p className="muted">Taklif kodi <strong>bir marta</strong> ko‘rsatiladi va brauzerda saqlanmaydi. Uni xodimga xavfsiz yo‘l bilan yuboring.</p>
    <label>Email<input type="email" maxLength={320} value={inviteEmail} onChange={e => setInviteEmail(e.target.value)}/></label>
    <label>Rol<select value={inviteRole} onChange={e => setInviteRole(e.target.value)}>
      <option value="operator">{roleLabel('operator')} — mijozlarga javob beradi, tasdiqlaydi</option>
      <option value="integrator">{roleLabel('integrator')} — kanallar va ulanishlar</option>
      <option value="viewer">{roleLabel('viewer')} — faqat ko‘radi</option>
    </select></label>
    <button className="btn primary" disabled={busy || !inviteEmail.trim()} onClick={() => run(async () => {
      const r = await client.request<{invitation: Invitation; token: string}>(
        `/identity/workspaces/${encodeURIComponent(workspace.id)}/invitations`,
        {email: inviteEmail.trim(), role: inviteRole});
      setInviteToken(r.token); setInviteEmail(''); setMessage('Taklif yaratildi.');
    })}>Taklif yaratish</button>
    {inviteToken && <div>
      <p role="status" className="muted">Kod faqat shu ekranda. Sahifani yangilasangiz yo‘qoladi.</p>
      <textarea aria-label="Taklif kodi" readOnly value={inviteToken}/>
      <button className="btn" onClick={() => setInviteToken('')}>Kodni yashirish</button>
    </div>}
    <label>Sizga kelgan taklif kodi<input maxLength={256} value={acceptToken}
      onChange={e => setAcceptToken(e.target.value)}/></label>
    <button className="btn" disabled={busy || acceptToken.trim().length < 20} onClick={() => run(async () => {
      const r = await client.request<{membership: Membership}>('/identity/invitations/accept',
        {token: acceptToken.trim()});
      setAcceptToken(''); setMessage(`Taklif qabul qilindi: ${r.membership.workspace_id} · ${roleLabel(r.membership.role)}.`);
    })}>Taklifni qabul qilish</button>

    <h3>Xodimni chiqarish</h3>
    <p className="muted">Foydalanuvchi ID sini qo‘lda kiriting: server a’zolar ro‘yxatini hali bermaydi. Chiqarilgan
      xodimning barcha qurilmalardagi sessiyalari tugaydi.</p>
    <label>Foydalanuvchi ID<input maxLength={128} value={revokeUser} onChange={e => setRevokeUser(e.target.value)}/></label>
    <ConfirmButton label="A’zolikni bekor qilish" disabled={busy || !revokeUser.trim()}
      title="Xodimni do‘kondan chiqarasizmi?"
      message={<>Foydalanuvchi <code>{revokeUser.trim()}</code> bu do‘konga kira olmaydi va barcha sessiyalari tugaydi.
        Qaytarish uchun yangi taklif kerak bo‘ladi.</>}
      confirmLabel="Ha, chiqarish" onConfirm={() => run(async () => {
        await client.request(`/identity/workspaces/${encodeURIComponent(workspace.id)}/members/${encodeURIComponent(revokeUser.trim())}/revoke`, {});
        setRevokeUser(''); setMessage('A’zolik bekor qilindi.');
      })}/>

    <h3>Sessiyalar</h3>
    <p className="muted">Hisobingiz qaysi qurilmalarda ochiq.</p>
    <button className="btn" disabled={busy} onClick={() => run(async () => {
      setSessions((await client.request<{sessions: Session[]}>('/identity/sessions')).sessions);
    })}>Sessiyalarni ko‘rish</button>
    <ConfirmButton label="Barcha qurilmalardan chiqish" disabled={busy}
      title="Barcha qurilmalardan chiqasizmi?"
      message="Hisobingiz hamma joyda, shu jumladan shu brauzerda ham yopiladi. Keyin qayta kirishingiz kerak bo‘ladi."
      confirmLabel="Ha, hammasidan chiqish" onConfirm={() => run(async () => {
        await client.request('/identity/logout-all', {});
        setSessions([]); exit();
      })}/>
    {sessions.map(s => <p key={s.id} className="row muted">{s.current ? 'Shu qurilma' : s.id.slice(0, 12)} ·
      {' '}{s.workspace_id || 'hisob'} · {formatDate(s.expires)} gacha</p>)}
  </section>;
}

/** First-owner bootstrap. Separate from the session panel on purpose.
 *
 * The route is gated twice: an `X-Admin-Token` header whose configured value must be at
 * least 32 characters, and `IDENTITY_BOOTSTRAP_ENABLED=true` in the deployment
 * environment. A dashboard session cannot supply either, so this form asks for the
 * admin token directly and never stores it. It exists because the alternative was
 * provisioning a first owner with `curl`, which leaves the token in shell history.
 *
 * It is reachable from the sign-in screen behind "Birinchi sozlash", because before
 * bootstrap there is no owner to sign in as. The request is therefore sent without a
 * session (`client.send`). */
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
  return <section className="panel">
    <h2>Birinchi sozlash</h2>
    <p className="muted">Faqat serverni o‘rnatgan administrator uchun: server sozlamasida <code>ADMIN_TOKEN</code> (kamida 32 belgi)
      va <code>IDENTITY_BOOTSTRAP_ENABLED=true</code> bo‘lishi, do‘kon uchun pack oldindan tayyorlangan bo‘lishi kerak.</p>
    <Alert text={error}/>
    {message && <p role="status" className="notice success">{message}</p>}
    <label>Administrator kaliti<input type="password" autoComplete="off" maxLength={256}
      value={adminToken} onChange={e => setAdminToken(e.target.value)}/></label>
    <label>Egasining emaili<input type="email" maxLength={320} value={email} onChange={e => setEmail(e.target.value)}/></label>
    <label>Parol<input type="password" autoComplete="new-password" maxLength={256}
      value={password} onChange={e => setPassword(e.target.value)}/></label>
    <label>Ism<input maxLength={256} value={displayName} onChange={e => setDisplayName(e.target.value)}/></label>
    <label>Do‘kon ID (pack nomi)<input maxLength={64} value={workspaceId} onChange={e => setWorkspaceId(e.target.value)}/></label>
    <label>Do‘kon nomi<input maxLength={256} value={workspaceName} onChange={e => setWorkspaceName(e.target.value)}/></label>
    <button className="btn primary" disabled={busy || !adminToken || !email.trim() || !password || !displayName.trim() || !workspaceId.trim() || !workspaceName.trim()}
      onClick={async () => {
        setBusy(true); setError(''); setMessage('');
        try {
          await client.send('/identity/bootstrap',
            bootstrapBody({email, password, displayName, workspaceId, workspaceName}),
            undefined, 'POST', {'X-Admin-Token': adminToken});
          setMessage('Egasi yaratildi. Endi yuqoridagi formadan kiring.');
          setAdminToken(''); setPassword('');
        } catch (e) {
          setError((e as {status?: number}).status === 403
            ? 'Administrator kaliti noto‘g‘ri yoki birinchi sozlash serverda o‘chirilgan.' : friendlyError(e));
        } finally { setBusy(false); }
      }}>Egasini yaratish</button>
  </section>;
}
