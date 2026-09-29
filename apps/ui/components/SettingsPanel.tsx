'use client';
import {type SessionClient, type Workspace} from '../lib/session-client.mjs';
import AdminPanel from './AdminPanel';
import {ChannelsPanel} from './ShopPanels';
import {ConfirmButton} from './ui';

/** "Sozlamalar": pausing the bot, channel status and the team. The pause is the only
 * control here that changes what customers experience, so it is first and asks first. */
export default function SettingsPanel({client, workspace, role, frozen, busy, onFreeze, exit, switchWorkspace}: {
  client: SessionClient; workspace: Workspace; role: string; frozen: boolean; busy: boolean;
  onFreeze: (stopped: boolean) => Promise<void>; exit: () => void; switchWorkspace: (w: Workspace) => Promise<void>;
}) {
  const isOwner = role === 'owner';
  return <div className="grid">
    <div>
      <section className="panel">
        <h2>Botni to‘xtatish</h2>
        <p>Holat: <strong className={frozen ? 'error' : 'success'}>{frozen ? 'Bot to‘xtatilgan' : 'Bot ishlayapti'}</strong></p>
        <p className="muted">To‘xtatilganda bot va agentlar hech qanday amal bajarmaydi: mijozga javob ketmaydi, buyurtma
          yozilmaydi. Ayni damda bajarilayotgan amal “natija noma’lum” bo‘lib qoladi va uni qo‘lda tekshirish kerak bo‘ladi.</p>
        {isOwner && !frozen && <ConfirmButton label="Botni to‘xtatish" disabled={busy} className="btn danger"
          title="Botni to‘xtatasizmi?"
          message="Mijozlar javob olmaydi va buyurtmalar yozilmaydi, toki siz botni qayta yoqmaguningizcha."
          confirmLabel="Ha, to‘xtatish" onConfirm={() => onFreeze(true)}/>}
        {isOwner && frozen && <button className="btn primary" disabled={busy} onClick={() => void onFreeze(false)}>Botni qayta yoqish</button>}
        {!isOwner && <p className="muted">Botni faqat do‘kon egasi to‘xtata oladi yoki yoqa oladi.</p>}
      </section>
      {(isOwner || role === 'integrator') && <ChannelsPanel client={client} tenant={workspace.id}/>}
    </div>
    <AdminPanel client={client} workspace={workspace} exit={exit} switchWorkspace={switchWorkspace}/>
  </div>;
}
