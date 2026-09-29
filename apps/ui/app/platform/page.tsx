'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import SessionGate from '../../components/SessionGate';
import AgentRuns from '../../components/AgentRuns';
import OAuthConnections from '../../components/OAuthConnections';
import GoogleData from '../../components/GoogleData';
import {BudgetPanel,KnowledgePanel} from '../../components/DevelopmentData';
import {BriefingPanel,CustomerResourcesPanel,EscalationPanel,MetricsPanel,ReconcileControl,ReengagementPanel,SchedulesPanel,SupervisorPanel} from '../../components/OperationsPanels';
import BottomNav from '../../components/BottomNav';
import SettingsPanel from '../../components/SettingsPanel';
import {ApprovalsPanel,CatalogPanel,ChannelsPanel,InboxPanel,OrderCard,OrdersPanel,type Approval} from '../../components/ShopPanels';
import {InboxView,type Conversation,type Handoff,type ThreadRef} from '../../components/ConversationPanels';
import {Alert,ConfirmButton,nowSeconds,When} from '../../components/ui';
import {canReadInbox,draftSummary,shopPath,startAutoRefresh} from '../../lib/shop-client.mjs';
import {conversationsPath,handoffsPath} from '../../lib/conversation-client.mjs';
import {attentionCount,conversationKey,defaultTab,documentTitle,navFor} from '../../lib/nav.mjs';
import {createIdempotency} from '../../lib/idempotency.mjs';
import {formatDate,friendlyError,roleLabel,statusLabel} from '../../lib/format.mjs';
import {argumentFields,buildArguments,toolCallBody,submitPath,toolChoices,toolDescription} from '../../lib/tools-client.mjs';
import {type SessionClient,type Workspace} from '../../lib/session-client.mjs';
type Tool={name:string;risk:string;description?:string;schema:unknown;runner:boolean};
type Agent={id:string;name:string;department:string;tools:string[];ladder:string};
type Task={id:string;agent:string;channel:string;status:string;created:number};
type Step={id:string;tool:string;args:unknown;result:unknown;status:string;error:string;approval_status:string;approver:string};
type Detail=Task & {steps:Step[]};
type Audit={id:number;action:string;actor:string;created:number;task:string};
type Device={id:string;revoked:number;seen:number;generation:number};
type Customer={id:string;external_ref:string|null;display_name:string;status:string;created:number;updated:number;contacts?:unknown[];channel_identities?:unknown[];orders?:unknown[]};
type Live={conversations:Conversation[];handoffs:Handoff[];approvals:Approval[]};
const tone:Record<string,string>={succeeded:'ok',failed:'danger',uncertain:'warn',waiting_approval:'warn',running:'info',queued:'',cancelled:''};
const example=JSON.stringify([{tool:'reports.summary',args:{}}],null,2);
// Chats and approvals are the operator's live work: re-read them this often while the tab is visible.
const LIVE_INTERVAL_MS=7000;

export default function Platform(){
  return <SessionGate>{(client,workspace,exit,switchWorkspace)=><PlatformDashboard key={workspace.id} client={client}
    workspace={workspace} exit={exit} switchWorkspace={switchWorkspace}/>}</SessionGate>;
}
function PlatformDashboard({client,workspace,exit,switchWorkspace}:{client:SessionClient;workspace:Workspace;exit:()=>void;switchWorkspace:(w:Workspace)=>Promise<void>}){
  const tenant=workspace.id;
  const [connections,setConnections]=useState<{id:string;driver:string;mode:string;status:string;lifecycle?:string;agent_ids?:string[];tables?:Record<string,string[]>}[]>([]);
  const [probeAgents,setProbeAgents]=useState<Record<string,string>>({});
  const [probeResults,setProbeResults]=useState<Record<string,{verified_at:number}>>({});
  const [role,setRole]=useState('');const [frozen,setFrozen]=useState(false);
  const [connected,setConnected]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const [agents,setAgents]=useState<Agent[]>([]);const [tools,setTools]=useState<Tool[]>([]);const [tasks,setTasks]=useState<Task[]>([]);
  const [audit,setAudit]=useState<Audit[]>([]);const [devices,setDevices]=useState<Device[]>([]);
  const [selected,setSelected]=useState<Detail|null>(null);const [customers,setCustomers]=useState<Customer[]>([]);const [selectedCustomer,setSelectedCustomer]=useState<Customer|null>(null);const [customerName,setCustomerName]=useState('');const [agent,setAgent]=useState('ops.assistant');
  const [steps,setSteps]=useState(example);const [text,setText]=useState('/report');const [tab,setTab]=useState('');
  const [thread,setThread]=useState<ThreadRef|null>(null);
  const [tool,setTool]=useState('');const [toolValues,setToolValues]=useState<Record<string,string>>({});
  const [deviceId,setDeviceId]=useState('office-1');const [deviceToken,setDeviceToken]=useState('');
  // Live shop data: one poller for the chat list, handoffs and approvals feeds every badge.
  const [live,setLive]=useState<Live|null>(null);const [liveError,setLiveError]=useState('');const [version,setVersion]=useState(0);
  const [seen,setSeen]=useState<Record<string,number>>({});const [since]=useState(()=>nowSeconds());
  const [keys]=useState(()=>createIdempotency());
  const more=useRef<HTMLDetailsElement>(null);
  async function req<T>(path:string,body?:unknown):Promise<T>{
    return client.request<T>(`/platform/${encodeURIComponent(tenant)}${path}`,body);
  }
  async function run(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(friendlyError(e));}finally{setBusy(false);}}
  useEffect(()=>{void run(refresh);},[]);
  async function refresh(){
    const [c,t,me]=await Promise.all([req<{agents:Agent[];tools:Tool[]}>('/catalog'),req<{tasks:Task[]}>('/tasks'),req<{role:string;frozen:boolean}>('/identity')]);
    setRole(me.role);setFrozen(me.frozen);setAgents(c.agents);setTools(c.tools);setTasks(t.tasks);setConnected(true);
    setTab(current=>current || defaultTab(me.role));
    setAgent(current=>c.agents.some(a=>a.id===current)?current:(c.agents[0]?.id || ''));
  }
  const shop=canReadInbox(role);
  const refreshLive=useCallback(async()=>{
    try{
      const [c,h,a,me]=await Promise.all([
        client.request<{conversations:Conversation[]}>(conversationsPath(tenant)),
        client.request<{handoffs:Handoff[]}>(handoffsPath(tenant)),
        client.request<{approvals:Approval[]}>(shopPath(tenant,'approvals')),
        client.request<{role:string;frozen:boolean}>(`/platform/${encodeURIComponent(tenant)}/identity`),
      ]);
      setLive({conversations:c.conversations,handoffs:h.handoffs,approvals:a.approvals});
      setFrozen(me.frozen);setLiveError('');setVersion(v=>v+1);
    }catch(e){setLiveError(friendlyError(e));}
  },[client,tenant]);
  useEffect(()=>{
    if(!shop)return;
    void refreshLive();
    return startAutoRefresh(refreshLive,LIVE_INTERVAL_MS);
  },[shop,refreshLive]);
  const openKey=tab==='conversations'&&thread?conversationKey(thread):null;
  const chats=attentionCount(live?.conversations,live?.handoffs,seen,since,nowSeconds(),openKey);
  const pendingApprovals=live?.approvals.length ?? 0;
  useEffect(()=>{document.title=documentTitle(workspace.name,chats+pendingApprovals);},[workspace.name,chats,pendingApprovals]);
  useEffect(()=>()=>{document.title=documentTitle('',0);},[]);
  // On a phone the tab bar scrolls sideways; keep the open tab in sight.
  useEffect(()=>{document.querySelector('.tabs .tab[aria-current="page"]')?.scrollIntoView({block:'nearest',inline:'nearest'});},[tab]);
  useEffect(()=>{
    const close=(event:PointerEvent)=>{const d=more.current;if(d?.open && !d.contains(event.target as Node))d.open=false;};
    document.addEventListener('pointerdown',close);
    return ()=>document.removeEventListener('pointerdown',close);
  },[]);

  function selectThread(ref:ThreadRef|null){
    const now=nowSeconds();
    setSeen(current=>{
      const next={...current};
      if(thread)next[conversationKey(thread)]=now;
      if(ref)next[conversationKey(ref)]=now;
      return next;
    });
    setThread(ref);
  }
  function openChat(ref:ThreadRef){setTab('conversations');selectThread(ref);}
  async function open(id:string){setSelected(await req<Detail>(`/tasks/${id}`));}
  async function approve(step:Step,decision:string){
    try{await req(`/steps/${step.id}/approval`,{decision});}
    finally{if(selected)await open(selected.id);await refresh();if(shop)await refreshLive();}
  }
  async function freeze(stopped:boolean){await run(async()=>{await req('/freeze',{stopped});await refresh();});}
  function go(id:string){
    if(more.current)more.current.open=false;
    void run(async()=>{
      setTab(id);if(id==='audit')setAudit((await req<{events:Audit[]}>('/audit')).events);
      if(id==='customers')setCustomers((await req<{customers:Customer[]}>('/customers')).customers);
      if(id==='connections')setConnections((await req<{connections:typeof connections}>('/connections')).connections);
      if(id==='devices')setDevices((await req<{devices:Device[]}>('/devices')).devices);
      if(id==='approvals'||id==='conversations')await refreshLive();
    });
  }
  const canWrite=['owner','operator'].includes(role) && !frozen;
  const isOwner=role==='owner';
  // The schema-driven tool surface: choices come from the agent's own policy in the
  // catalogue, so an operator can call workforce.workload and friends without a
  // hand-written plan. The engine re-validates; this is a pre-flight, not a gate.
  const agentTools=toolChoices(tools,agents.find(a=>a.id===agent));
  const chosen=agentTools.find(t=>t.name===tool) || agentTools[0];
  const fields=chosen?argumentFields(chosen.schema):[];
  const nav=navFor(role);
  const advancedActive=nav.advanced.find(i=>i.id===tab);
  const badge=(id:string)=>{
    const n=id==='conversations'?chats:id==='approvals'?pendingApprovals:0;
    if(n<=0)return null;
    const what=id==='conversations'?`${n} ta suhbat javob kutmoqda`:`${n} ta tasdiq kutmoqda`;
    return <><span className={`count${id==='approvals'?' warn':''}`} aria-hidden="true">{n>99?'99+':n}</span><span className="sr-only">, {what}</span></>;
  };
  return <>
    <a className="skip" href="#main">Asosiy qismga o‘tish</a>
    <header className="topbar"><div className="topbar-inner">
      <div className="brandrow">
        <h1>{workspace.name}</h1>
        <div className="who">
          {connected && <span className={`botstate${frozen?' off':''}`}>{frozen?'Bot to‘xtatilgan':'Bot ishlayapti'}</span>}
          {role && <span>{roleLabel(role)}</span>}
          <button className="btn small" onClick={exit}>Chiqish</button>
        </div>
      </div>
      {connected && <div className="navrow">
        <nav className="tabs" aria-label="Bo‘limlar">{nav.primary.map(i=><button key={i.id} className="tab"
          aria-current={tab===i.id?'page':undefined} onClick={()=>go(i.id)}>{i.label}{badge(i.id)}</button>)}</nav>
        {nav.advanced.length>0 && <details className="more" ref={more}>
          <summary className="tab" aria-current={advancedActive?'page':undefined}><span className="more-label">{advancedActive?advancedActive.label:'Kengaytirilgan'}</span></summary>
          <div className="more-menu"><p className="muted">Texnik bo‘limlar: agentlar, vazifalar, integratsiyalar va jurnallar.</p>
            {nav.advanced.map(i=><button key={i.id} aria-current={tab===i.id?'page':undefined} onClick={()=>go(i.id)}>{i.label}</button>)}</div>
        </details>}
      </div>}
    </div></header>
    <main className="app" id="main" tabIndex={-1}>
    {frozen && <div className="banner" role="alert">
      <div><strong>Bot to‘xtatilgan.</strong> Mijozlarga javob ketmaydi va buyurtmalar yozilmaydi.</div>
      {isOwner && <button className="btn" disabled={busy} onClick={()=>void freeze(false)}>Botni qayta yoqish</button>}
    </div>}
    <Alert text={error}/>
    {!connected && !error && <p className="muted" role="status">Yuklanmoqda…</p>}
    {!connected && error && <button className="btn" disabled={busy} onClick={()=>void run(refresh)}>Qayta urinish</button>}
    {connected && shop && (tab==='conversations'||tab==='approvals') && <div className="statusline" role="status">
      {liveError?<span className="error">Yangilab bo‘lmadi: {liveError}</span>:<span>Har 7 soniyada o‘zi yangilanadi.</span>}
      <button className="btn small" onClick={()=>void refreshLive()}>Hozir yangilash</button>
    </div>}
    {connected && <>
      {tab==='conversations' && shop && <InboxView client={client} tenant={tenant} canReply={canWrite}
        conversations={live?.conversations ?? null} handoffs={live?.handoffs ?? null} loadError={live?'':liveError}
        selected={thread} onSelect={selectThread} seen={seen} since={since} version={version} keys={keys} onChanged={refreshLive}/>}
      {tab==='approvals' && shop && <ApprovalsPanel client={client} tenant={tenant} canDecide={canWrite}
        approvals={live?.approvals ?? null} loadError={live?'':liveError} onChanged={refreshLive} onOpenChat={openChat}
        onOpenTask={id=>void run(async()=>{await open(id);setTab('tasks');})}/>}
      {tab==='orders' && <OrdersPanel client={client} tenant={tenant} onOpenChat={shop?openChat:undefined}/>}
      {tab==='catalog' && <CatalogPanel client={client} tenant={tenant}/>}
      {tab==='settings' && <SettingsPanel client={client} workspace={workspace} role={role} frozen={frozen} busy={busy}
        onFreeze={freeze} exit={exit} switchWorkspace={switchWorkspace}/>}

      {tab==='google-data' && isOwner && <section className="panel"><GoogleData client={client} tenant={tenant} agents={agents} frozen={frozen}/></section>}
      {tab==='oauth' && isOwner && <section className="panel"><OAuthConnections client={client} tenant={tenant} frozen={frozen}/></section>}
      {tab==='reengagement' && <ReengagementPanel client={client} tenant={tenant} role={role} frozen={frozen} agents={agents}/>}
      {tab==='briefing' && <BriefingPanel client={client} tenant={tenant} role={role} frozen={frozen} agents={agents}/>}
      {tab==='escalation' && <EscalationPanel client={client} tenant={tenant} role={role} frozen={frozen} agents={agents}/>}
      {tab==='supervisor' && <SupervisorPanel client={client} tenant={tenant} role={role} frozen={frozen} agents={agents}/>}
      {tab==='schedules' && <SchedulesPanel client={client} tenant={tenant} role={role} frozen={frozen} agents={agents}/>}
      {tab==='metrics' && <MetricsPanel client={client} tenant={tenant} role={role}/>}
      {tab==='budget' && <BudgetPanel client={client} tenant={tenant} agents={agents} role={role} frozen={frozen}/>}
      {tab==='knowledge' && <KnowledgePanel client={client} tenant={tenant} agents={agents} role={role} frozen={frozen}/>}
      {tab==='agent-runs' && <AgentRuns client={client} tenant={tenant} agents={agents} role={role} frozen={frozen}
        onOpenTask={async id=>{await open(id);setTab('tasks');}}/>}
      {tab==='customers' && <div className="grid">
        <section className="panel"><h2>Mijozlar bazasi</h2><p className="muted">Mijoz, uning kontaktlari, kanallari va buyurtmalari. Ikki kanalni bitta mijozga birlashtirish faqat aniq tasdiq bilan.</p>
          <label>Yangi mijoz ismi<input value={customerName} onChange={e=>setCustomerName(e.target.value)} placeholder="Masalan, Dilnoza Karimova"/></label>
          <button className="btn primary" disabled={busy || !canWrite || !customerName.trim()} onClick={()=>run(async()=>{await req('/customers',{display_name:customerName.trim()});setCustomerName('');setCustomers((await req<{customers:Customer[]}>('/customers')).customers);})}>Mijoz qo‘shish</button>
          {customers.length===0 && <p>Hali mijoz yo‘q.</p>}
          {customers.map(c=><button key={c.id} className="btn listbtn" onClick={()=>run(async()=>setSelectedCustomer((await req<{customer:Customer}>(`/customers/${c.id}`)).customer))}><strong>{c.display_name}</strong><br/><small className="muted">{statusLabel(c.status)} · {c.id.slice(0,10)}</small></button>)}
        </section>
        <section className="panel"><h2>Mijoz ma’lumotlari</h2>{!selectedCustomer && <p className="muted">Chapdan mijozni tanlang.</p>}{selectedCustomer && <><h3>{selectedCustomer.display_name}</h3><p>{statusLabel(selectedCustomer.status)} · {selectedCustomer.external_ref || 'tashqi raqam yo‘q'}</p>
          <details><summary className="muted">Barcha maydonlar (texnik)</summary><pre>{JSON.stringify(selectedCustomer,null,2)}</pre></details>
          <CustomerResourcesPanel client={client} tenant={tenant} role={role} frozen={frozen} customer={selectedCustomer.id}
            onChanged={async()=>setSelectedCustomer((await req<{customer:Customer}>(`/customers/${selectedCustomer.id}`)).customer)}/></>}</section>
      </div>}
      {tab==='tasks' && <div className="grid">
        <section className="panel"><h2>Yangi vazifa</h2>
          <label>Agent <select value={agent} onChange={e=>{setAgent(e.target.value);setTool('');setToolValues({});}}>{agents.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          <p className="muted">Ruxsatlar serverda qayta tekshiriladi.</p>
          <h3>Vositani chaqirish</h3>
          {!chosen && <p>Bu agentning siyosatida chaqiriladigan vosita yo‘q.</p>}
          {chosen && <>
            <label>Vosita <select value={chosen.name} onChange={e=>{setTool(e.target.value);setToolValues({});}}>{agentTools.map(t=><option key={t.name} value={t.name}>{t.name} · {t.risk}</option>)}</select></label>
            {toolDescription(chosen) && <p className="muted">{toolDescription(chosen)}</p>}
            {chosen.risk!=='read' && <p className="warn-text">Yozuvchi vosita: avval tasdiq navbatiga tushadi.</p>}
            {fields.map(f=><label key={f.name}>{f.name}{f.required?' *':''}
              {f.hint && <small className="muted"> ({f.hint})</small>}
              {f.kind==='choice'
                ? <select aria-label={f.name} value={toolValues[f.name]||''} onChange={e=>setToolValues(v=>({...v,[f.name]:e.target.value}))}><option value="">—</option>{(f.choices||[]).map(c=><option key={c} value={c}>{c}</option>)}</select>
                : <input aria-label={f.name} value={toolValues[f.name]||''} onChange={e=>setToolValues(v=>({...v,[f.name]:e.target.value}))}/>}
            </label>)}
            <button className="btn primary" disabled={busy || !canWrite} onClick={()=>run(async()=>{
              const payload={agent,tool:chosen.name,args:buildArguments(fields,toolValues)};
              const body=toolCallBody({...payload,key:keys.key('task',payload)});
              const r=await client.request<{task_id:string}>(submitPath(tenant),body);
              keys.settle('task',payload);
              await refresh();await open(r.task_id);
            })}>Vositani chaqirish</button>
          </>}
          <details><summary>JSON reja</summary>
            <textarea aria-label="JSON reja" value={steps} onChange={e=>setSteps(e.target.value)} rows={9} style={{fontFamily:'var(--mono)'}}/>
            <button disabled={busy || !canWrite} className="btn" onClick={()=>run(async()=>{
              const payload={agent,steps:JSON.parse(steps)};
              const r=await req<{task_id:string}>('/tasks',{...payload,key:keys.key('plan',payload)});
              keys.settle('plan',payload);await refresh();await open(r.task_id);
            })}>Vazifani yaratish</button>
            <details><summary>Ruxsat etilgan vosita sxemalari</summary><pre>{JSON.stringify(tools.filter(t=>agents.find(a=>a.id===agent)?.tools.includes(t.name)),null,2)}</pre></details>
          </details>
          <h3>Matnli topshiriq</h3><p className="muted">Erkin matn uchun LLM kaliti kerak. <code>/report</code> — namunaviy hisobot.</p>
          <textarea aria-label="Matnli topshiriq" value={text} onChange={e=>setText(e.target.value)}/>
          <button className="btn" disabled={busy || !canWrite} onClick={()=>run(async()=>{
            const payload={text};
            await req('/events',{...payload,key:keys.key('event',payload)});
            keys.settle('event',payload);setTab('inbox');
          })}>Kiruvchi navbatga yuborish</button>
        </section>
        <section className="panel"><h2>Vazifalar · {tasks.length}</h2>{tasks.length===0 && <p>Hali vazifa yo‘q.</p>}
          {tasks.map(t=><button key={t.id} className="btn listbtn" onClick={()=>run(()=>open(t.id))}>
            <strong>{t.agent}</strong> <span className={`chip ${tone[t.status]||''}`}>{statusLabel(t.status)}</span><br/>
            <small className="muted">{t.channel} · {t.id.slice(0,10)} · <When at={t.created}/></small></button>)}
        </section>
        {selected && <section className="panel"><h2>Vazifa bosqichlari</h2><code>{selected.id}</code><p><span className={`chip ${tone[selected.status]||''}`}>{statusLabel(selected.status)}</span></p>
          <button disabled={busy} className="btn" onClick={()=>run(()=>open(selected.id))}>Holatni yangilash</button>
          <ConfirmButton label="Vazifani bekor qilish" disabled={busy || !['owner','operator'].includes(role)}
            title="Vazifani bekor qilasizmi?" message="Hali bajarilmagan qadamlar bajarilmaydi. Bajarilganlari orqaga qaytmaydi."
            confirmLabel="Ha, bekor qilish" onConfirm={()=>run(async()=>{await req(`/tasks/${selected.id}/cancel`,{});await open(selected.id);await refresh();})}/>
          {selected.steps.map((s,i)=><article key={s.id} className="row">
            <h3>{i+1}. {s.tool}</h3><p><span className={`chip ${tone[s.status]||''}`}>{statusLabel(s.status)}</span> {s.error && <span className="muted">({s.error})</span>}</p>
            {(()=>{const d=draftSummary(s.tool,s.args);return d.kind==='message'?<><p>Kimga: <strong>{d.recipient||'—'}</strong></p><div className="message-card">{d.text}</div></>
              :d.kind==='order'?<OrderCard order={d.order}/>:null;})()}
            <details><summary>Argumentlar</summary><pre>{JSON.stringify(s.args,null,2)}</pre></details>
            {s.approval_status==='pending' && ['queued','waiting_approval'].includes(s.status) && <div className="actions">
              <button disabled={busy || !canWrite} className="btn primary" onClick={()=>run(()=>approve(s,'approved'))}>Tasdiqlash</button>
              <ConfirmButton label="Rad etish" disabled={busy || !canWrite} title="Qadamni rad etasizmi?"
                message="Bu qadam bajarilmaydi va uni qayta tiklab bo‘lmaydi." confirmLabel="Ha, rad etish"
                onConfirm={()=>run(()=>approve(s,'rejected'))}/></div>}
            {s.approver && <p className="muted">Tasdiqlagan: {s.approver}</p>}
            <details open={s.status==='succeeded'}><summary>Natija</summary><pre>{JSON.stringify(s.result,null,2)}</pre></details>
            {s.status==='uncertain' && <>
              <p className="warn-text">Natija noma’lum. Avtomatik qayta bajarish bloklangan: egasi tashqi tizimni tekshirib, dalil bilan yakunlaydi.</p>
              <ReconcileControl client={client} tenant={tenant} role={role} step={s.id}
                onDone={async()=>{await open(selected.id);await refresh();}}/>
            </>}
          </article>)}
        </section>}
      </div>}
      {tab==='agents' && <section className="panel"><h2>Agentlar</h2>{agents.map(a=><article key={a.id} className="row"><h3>{a.name}</h3><p className="muted">{a.department} · {a.ladder}</p><p>{a.tools.join(', ')}</p>{a.tools.some(n=>!tools.find(t=>t.name===n)) && <p className="error">Ayrim vositalar uchun serverda adapter yo‘q.</p>}</article>)}</section>}
      {tab==='inbox' && <InboxPanel client={client} tenant={tenant} canWrite={canWrite}/>}
      {tab==='audit' && <section className="panel"><h2>Audit jurnali</h2><div className="table-wrap"><table className="data"><thead><tr><th>Vaqt</th><th>Amal</th><th>Kim</th><th>Vazifa</th></tr></thead>
        <tbody>{audit.map(a=><tr key={a.id}><td className="nowrap">{formatDate(a.created)}</td><td><strong>{a.action}</strong></td><td>{a.actor}</td><td><code>{a.task.slice(0,10)}</code></td></tr>)}</tbody></table></div></section>}
      {tab==='connections' && <ChannelsPanel client={client} tenant={tenant}/>}
      {tab==='connections' && <section className="panel"><h2>Mijoz bazasi ulanishlari</h2>
        <p className="muted">SQLite lokal o‘qish sinovdan o‘tgan. PostgreSQL adapteri shartnoma-test bosqichida, jonli tekshiruv talab qilinadi. Boshqa CRM/ERP drayverlari adapter_required sifatida ko‘rsatiladi.
          Ulanishlar server sozlamasidan olinadi; kalit va baza manzili brauzerga yuborilmaydi.</p>
        {connections.length===0 && <p>Ulanish sozlanmagan.</p>}
        {connections.map(c=><article key={c.id} className="row">
          <h3>{c.id}</h3><p className="muted">{c.driver} · {c.mode} · {c.status} · {c.lifecycle || 'configured'}</p>
          <pre>{JSON.stringify(c.tables,null,2)}</pre>
          {c.mode==='managed_approved_operations' && <p>Tasdiqli DB operatsiyalari. Yozish uchun reja va alohida tasdiq kerak; tarmoq bazasida haqiqiy tekshiruv hali tasdiqlanmagan.</p>}
          {c.mode==='read_only' && <>
            <label>Tekshiruv agenti <select aria-label={`${c.id} tekshiruv agenti`}
              value={probeAgents[c.id] || ''}
              onChange={event=>setProbeAgents(current=>({...current,[c.id]:event.target.value}))}>
              <option value="">{c.agent_ids?.length?'Agentni tanlang':'Admin tekshiruvi'}</option>
              {agents.filter(a=>!c.agent_ids?.length || c.agent_ids.includes(a.id)).map(a=>
                <option key={a.id} value={a.id}>{a.name}</option>)}
            </select></label>
            <button className="btn" disabled={busy || frozen || !['owner','integrator'].includes(role)
              || !['configured','healthy','degraded'].includes(c.lifecycle || 'configured')
              || Boolean(c.agent_ids?.length && !probeAgents[c.id])}
              onClick={()=>run(async()=>{
                setProbeResults(current=>{const next={...current};delete next[c.id];return next;});
                const result=await req<{verified_at:number}>(`/connections/${encodeURIComponent(c.id)}/verify`,
                  probeAgents[c.id]?{agent:probeAgents[c.id]}:{});
                setProbeResults(current=>({...current,[c.id]:result}));
              })}>Bitta jadvalni xavfsiz tekshirish</button>
            {probeResults[c.id] && <p>Bir jadval o‘qish tekshiruvi o‘tdi: {formatDate(probeResults[c.id].verified_at)}.
              Mijoz satrlari brauzerga berilmadi. Bu barcha jadvallar yoki production tayyorligi tasdig‘i emas;
              health holati doimiy saqlanmadi.</p>}
          </>}
        </article>)}
      </section>}
      {tab==='devices' && <section className="panel"><h2>Qurilmalar</h2><p className="muted">Linux’da ruxsat ro‘yxati doirasida fayl o‘qish va ro‘yxatlash lokal tekshirilgan. macOS yordamchisi shartnoma bosqichida. Printer, ekran va ilova ishga tushirish hali o‘chirilgan.</p>
        <label>Qurilma ID<input value={deviceId} onChange={e=>setDeviceId(e.target.value)}/></label>
        <button className="btn" disabled={busy || !isOwner || frozen} onClick={()=>run(async()=>{const r=await req<{device_token:string}>('/devices',{device_id:deviceId,revoked:false});setDeviceToken(r.device_token);setDevices((await req<{devices:Device[]}>('/devices')).devices);})}>Ulash yoki kalitni almashtirish</button>
        {deviceToken && <div><p className="muted">Runner kaliti, 24 soat amal qiladi. Xavfsiz lokal sozlamaga saqlang.</p><textarea aria-label="Qurilma kaliti" readOnly value={deviceToken}/><button className="btn" onClick={()=>setDeviceToken('')}>Kalitni yashirish</button></div>}
        {devices.map(d=><div key={d.id} className="row">{d.id} · <span className={`chip ${d.revoked?'':Date.now()/1000-d.seen<90?'ok':'warn'}`}>{d.revoked?'bekor qilingan':Date.now()/1000-d.seen<90?'onlayn':'oflayn'}</span> · avlod {d.generation}{' '}
          <ConfirmButton label="Bekor qilish" disabled={busy || !isOwner || Boolean(d.revoked)} className="btn small danger-outline"
            title="Qurilmani uzasizmi?" message={`${d.id} qurilmasi endi buyruq ololmaydi. Qayta ulash uchun yangi kalit kerak.`}
            confirmLabel="Ha, uzish" onConfirm={()=>run(async()=>{await req('/devices',{device_id:d.id,revoked:true});setDevices((await req<{devices:Device[]}>('/devices')).devices);})}/></div>)}
        <p className="muted">Botni to‘xtatish “Sozlamalar” bo‘limida.</p>
      </section>}
    </>}
    </main>
    {connected && <BottomNav role={role} tab={tab} onSelect={go} badge={badge}/>}
  </>;
}
