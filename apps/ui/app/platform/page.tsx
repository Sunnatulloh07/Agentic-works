'use client';
import {useEffect,useState} from 'react';
import SessionGate from '../../components/SessionGate';
import AgentRuns from '../../components/AgentRuns';
import OAuthConnections from '../../components/OAuthConnections';
import GoogleData from '../../components/GoogleData';
import {BudgetPanel,KnowledgePanel} from '../../components/DevelopmentData';
import {BriefingPanel,CustomerResourcesPanel,EscalationPanel,MetricsPanel,ReconcileControl,ReengagementPanel,SchedulesPanel,SupervisorPanel} from '../../components/OperationsPanels';
import AdminPanel from '../../components/AdminPanel';
import {ApprovalsPanel,CatalogPanel,ChannelsPanel,InboxPanel,OrdersPanel} from '../../components/ShopPanels';
import {draftSummary} from '../../lib/shop-client.mjs';
import {type SessionClient,type Workspace} from '../../lib/session-client.mjs';
type Tool={name:string;risk:string;schema:unknown;runner:boolean};
type Agent={id:string;name:string;department:string;tools:string[];ladder:string};
type Task={id:string;agent:string;channel:string;status:string;created:number};
type Step={id:string;tool:string;args:unknown;result:unknown;status:string;error:string;approval_status:string;approver:string};
type Detail=Task & {steps:Step[]};
type Audit={id:number;action:string;actor:string;created:number;task:string};
type Device={id:string;revoked:number;seen:number;generation:number};
type Customer={id:string;external_ref:string|null;display_name:string;status:string;created:number;updated:number;contacts?:unknown[];channel_identities?:unknown[];orders?:unknown[]};
const colors:Record<string,string>={succeeded:'#86efac',failed:'#fca5a5',uncertain:'#fdba74',waiting_approval:'#fde68a',running:'#93c5fd',queued:'#cbd5e1',cancelled:'#a1a1aa'};
const example=JSON.stringify([{tool:'reports.summary',args:{}}],null,2);

export default function Platform(){
  return <SessionGate>{(client,workspace,exit)=><PlatformDashboard key={workspace.id} client={client} workspace={workspace} exit={exit}/>}</SessionGate>;
}
function PlatformDashboard({client,workspace,exit}:{client:SessionClient;workspace:Workspace;exit:()=>void}){
  const tenant=workspace.id;
  const [connections,setConnections]=useState<{id:string;driver:string;mode:string;status:string;lifecycle?:string;agent_ids?:string[];tables?:Record<string,string[]>}[]>([]);
  const [probeAgents,setProbeAgents]=useState<Record<string,string>>({});
  const [probeResults,setProbeResults]=useState<Record<string,{verified_at:number}>>({});
  const [role,setRole]=useState('');const [frozen,setFrozen]=useState(false);
  const [connected,setConnected]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const [agents,setAgents]=useState<Agent[]>([]);const [tools,setTools]=useState<Tool[]>([]);const [tasks,setTasks]=useState<Task[]>([]);
  const [audit,setAudit]=useState<Audit[]>([]);const [devices,setDevices]=useState<Device[]>([]);
  const [selected,setSelected]=useState<Detail|null>(null);const [customers,setCustomers]=useState<Customer[]>([]);const [selectedCustomer,setSelectedCustomer]=useState<Customer|null>(null);const [customerName,setCustomerName]=useState('');const [agent,setAgent]=useState('ops.assistant');
  const [steps,setSteps]=useState(example);const [text,setText]=useState('/report');const [tab,setTab]=useState('tasks');
  const [deviceId,setDeviceId]=useState('office-1');const [deviceToken,setDeviceToken]=useState('');
  async function req<T>(path:string,body?:unknown):Promise<T>{
    return client.request<T>(`/platform/${encodeURIComponent(tenant)}${path}`,body);
  }
  async function run(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(e instanceof Error?e.message:'Xato');}finally{setBusy(false);}}
  useEffect(()=>{void run(refresh);},[]);
  async function refresh(){
    const [c,t,me]=await Promise.all([req<{agents:Agent[];tools:Tool[]}>('/catalog'),req<{tasks:Task[]}>('/tasks'),req<{role:string;frozen:boolean}>('/identity')]);
    setRole(me.role);setFrozen(me.frozen);setAgents(c.agents);setTools(c.tools);setTasks(t.tasks);setConnected(true);
    setAgent(current=>c.agents.some(a=>a.id===current)?current:(c.agents[0]?.id || ''));
  }
  async function open(id:string){setSelected(await req<Detail>(`/tasks/${id}`));}
  async function approve(step:Step,decision:string){await req(`/steps/${step.id}/approval`,{decision});if(selected)await open(selected.id);await refresh();}
  const key=()=>crypto.randomUUID();
  const canWrite=['owner','operator'].includes(role) && !frozen;
  const isOwner=role==='owner';
  return <main style={{fontFamily:'system-ui,sans-serif',background:'#0b1220',color:'#e2e8f0',minHeight:'100vh',padding:'28px',maxWidth:1500,margin:'auto'}}>
    <header style={{display:'flex',justifyContent:'space-between',gap:20,flexWrap:'wrap'}}>
      <div><h1 style={{margin:0}}>Agent Platform</h1><p style={{color:'#94a3b8'}}>AI xodimlar boshqaruvi · Kanalga bog‘lanmagan runtime</p></div>
      <span style={{color:'#fde68a'}}>Development 0.3.8 · Lokal dalillar mavjud · Yakuniy mahsulot emas</span>
    </header>
    <section style={panel}>
      <strong>{workspace.name}</strong><p>Workspace: {tenant}</p>
      <button style={button} disabled={busy} onClick={()=>run(refresh)}>Yangilash</button>
      <button style={button} disabled={busy} onClick={exit}>Chiqish / workspace almashtirish</button>
      <p style={{fontSize:12,color:'#94a3b8'}}>Session avtomatik yangilanadi. Vazifalarni alohida worker bajaradi.</p>
    </section>
    {error && <div role="alert" style={{...panel,color:'#fca5a5'}}>{error}</div>}
    {busy && <p role="status">Yuklanmoqda...</p>}
    {connected && <>
      <p>Rol: <strong>{role}</strong> · {frozen?'Ijro to‘xtatilgan':'Faol'}</p>
      <nav style={{display:'flex',gap:8,flexWrap:'wrap',marginBottom:20}}>{[['tasks','Vazifalar'],['approvals','Tasdiqlar'],['orders','Buyurtmalar'],['catalog','Katalog'],['agent-runs','Agent loop'],['customers','Mijozlar 360'],['agents','Agentlar'],['inbox','Kiruvchi hodisalar'],['audit','Audit'],['devices','Qurilmalar'],['connections','Connectorlar'],['budget','Xarajat budjeti'],['knowledge','Bilim bazasi'],['oauth','Google OAuth'],['google-data','Google sync'],['reengagement','Qayta aloqa'],['briefing','Brifing'],['escalation','Eskalatsiya'],['supervisor','Supervisor'],['schedules','Jadval'],['metrics','Holat'],['admin','Hisob']].filter(([id])=>(!['oauth','google-data'].includes(id) || role==='owner') && (!['budget','knowledge'].includes(id) || ['owner','operator','integrator'].includes(role)) && (!['audit','inbox','approvals'].includes(id) || ['owner','operator'].includes(role)) && (id!=='connections' || ['owner','integrator'].includes(role)) && (!['reengagement','briefing','escalation','supervisor','schedules'].includes(id) || ['owner','operator'].includes(role))).map(([id,label])=><button key={id} style={{...button,background:tab===id?'#2563eb':'#1e293b'}} onClick={()=>run(async()=>{
        setTab(id);if(id==='audit')setAudit((await req<{events:Audit[]}>('/audit')).events);
        if(id==='customers')setCustomers((await req<{customers:Customer[]}>('/customers')).customers);
        if(id==='connections')setConnections((await req<{connections:typeof connections}>('/connections')).connections);
        if(id==='devices')setDevices((await req<{devices:Device[]}>('/devices')).devices);
      })}>{label}</button>)}</nav>
      {tab==='google-data' && isOwner && <GoogleData client={client} tenant={tenant} agents={agents} frozen={frozen}/>}
      {tab==='oauth' && isOwner && <OAuthConnections client={client} tenant={tenant} frozen={frozen}/>}
      {tab==='reengagement' && <ReengagementPanel client={client} tenant={tenant} role={role} frozen={frozen} agents={agents}/>}
      {tab==='briefing' && <BriefingPanel client={client} tenant={tenant} role={role} frozen={frozen} agents={agents}/>}
      {tab==='escalation' && <EscalationPanel client={client} tenant={tenant} role={role} frozen={frozen} agents={agents}/>}
      {tab==='supervisor' && <SupervisorPanel client={client} tenant={tenant} role={role} frozen={frozen} agents={agents}/>}
      {tab==='schedules' && <SchedulesPanel client={client} tenant={tenant} role={role} frozen={frozen} agents={agents}/>}
      {tab==='metrics' && <MetricsPanel client={client} tenant={tenant} role={role}/>}
      {tab==='admin' && <><AdminPanel client={client} workspace={workspace} exit={exit}/></>}
      {tab==='budget' && <BudgetPanel client={client} tenant={tenant} agents={agents} role={role} frozen={frozen}/>}
      {tab==='knowledge' && <KnowledgePanel client={client} tenant={tenant} agents={agents} role={role} frozen={frozen}/>}
      {tab==='agent-runs' && <AgentRuns client={client} tenant={tenant} agents={agents} role={role} frozen={frozen}
        onOpenTask={async id=>{await open(id);setTab('tasks');}}/>}
      {tab==='customers' && <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(280px,1fr))',gap:20}}>
        <section style={panel}><h2>Mijozlar 360</h2><p>Tenant doirasidagi mijoz, kontakt, kanal identity va buyurtmalar. Kanal birlashtirish faqat explicit tasdiq bilan ishlaydi.</p>
          <input aria-label="Mijoz nomi" style={input} value={customerName} onChange={e=>setCustomerName(e.target.value)} placeholder="Mijoz nomi"/>
          <button style={button} disabled={busy || !canWrite || !customerName.trim()} onClick={()=>run(async()=>{await req('/customers',{display_name:customerName.trim()});setCustomerName('');setCustomers((await req<{customers:Customer[]}>('/customers')).customers);})}>Mijoz qo‘shish</button>
          {customers.length===0 && <p>Hali mijoz yo‘q. Yangilash tugmasini bosing.</p>}
          {customers.map(c=><button key={c.id} style={{...button,display:'block',width:'100%',textAlign:'left'}} onClick={()=>run(async()=>setSelectedCustomer((await req<{customer:Customer}>(`/customers/${c.id}`)).customer))}><strong>{c.display_name}</strong><br/><small>{c.status} · {c.id.slice(0,10)}</small></button>)}
        </section>
        <section style={panel}><h2>Customer detail</h2>{!selectedCustomer && <p>Mijozni tanlang.</p>}{selectedCustomer && <><h3>{selectedCustomer.display_name}</h3><p>{selectedCustomer.status} · {selectedCustomer.external_ref || 'external reference yo‘q'}</p><pre style={pre}>{JSON.stringify(selectedCustomer,null,2)}</pre>
          <CustomerResourcesPanel client={client} tenant={tenant} role={role} frozen={frozen} customer={selectedCustomer.id}
            onChanged={async()=>setSelectedCustomer((await req<{customer:Customer}>(`/customers/${selectedCustomer.id}`)).customer)}/></>}</section>
      </div>}
      {tab==='tasks' && <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(320px,1fr))',gap:20}}>
        <section style={panel}><h2>Yangi vazifa</h2>
          <label>Agent <select style={input} value={agent} onChange={e=>setAgent(e.target.value)}>{agents.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          <p>Typed plan, tool ruxsatlari serverda qayta tekshiriladi.</p>
          <textarea aria-label="JSON reja" value={steps} onChange={e=>setSteps(e.target.value)} rows={9} style={{...input,width:'100%',boxSizing:'border-box',fontFamily:'monospace'}}/>
          <button disabled={busy || !canWrite} style={button} onClick={()=>run(async()=>{const r=await req<{task_id:string}>('/tasks',{agent,key:key(),steps:JSON.parse(steps)});await refresh();await open(r.task_id);})}>Vazifani yaratish</button>
          <details><summary>Ruxsat etilgan tool sxemalari</summary><pre style={pre}>{JSON.stringify(tools.filter(t=>agents.find(a=>a.id===agent)?.tools.includes(t.name)),null,2)}</pre></details>
          <h3>Matnli topshiriq</h3><p style={{fontSize:13}}>Erkin matn uchun LLM kaliti kerak. <code>/report</code> deterministik demo.</p>
          <textarea aria-label="Matnli topshiriq" value={text} onChange={e=>setText(e.target.value)} style={{...input,width:'100%',boxSizing:'border-box'}}/>
          <button style={button} disabled={busy || !canWrite} onClick={()=>run(async()=>{await req('/events',{key:key(),text});setTab('inbox');})}>Inboxga yuborish</button>
        </section>
        <section style={panel}><h2>Vazifalar · {tasks.length}</h2>{tasks.length===0 && <p>Hali vazifa yo‘q.</p>}
          {tasks.map(t=><button key={t.id} style={{...button,display:'block',width:'100%',textAlign:'left',marginBottom:8}} onClick={()=>run(()=>open(t.id))}>
            <strong>{t.agent}</strong> <span style={{color:colors[t.status]||'#cbd5e1'}}>{t.status}</span><br/>
            <small>{t.channel} · {t.id.slice(0,10)} · {new Date(t.created*1000).toLocaleString()}</small></button>)}
        </section>
        {selected && <section style={panel}><h2>Task timeline</h2><code>{selected.id}</code><p>{selected.status}</p>
          <button disabled={busy} style={button} onClick={()=>run(()=>open(selected.id))}>Holatni yangilash</button>
          <button disabled={busy || !['owner','operator'].includes(role)} style={button} onClick={()=>run(async()=>{await req(`/tasks/${selected.id}/cancel`,{});await open(selected.id);await refresh();})}>Bekor qilish</button>
          {selected.steps.map((s,i)=><article key={s.id} style={{borderTop:'1px solid #334155',marginTop:14,paddingTop:10}}>
            <h3>{i+1}. {s.tool}</h3><p style={{color:colors[s.status]}}>{s.status} {s.error && `(${s.error})`}</p>
            {(()=>{const d=draftSummary(s.tool,s.args);return d.kind==='message'?<p>Qabul qiluvchi: <strong>{d.recipient||'—'}</strong><br/><span style={{whiteSpace:'pre-wrap'}}>{d.text}</span></p>:null;})()}
            <details open><summary>Argumentlar</summary><pre style={pre}>{JSON.stringify(s.args,null,2)}</pre></details>
            {s.approval_status==='pending' && ['queued','waiting_approval'].includes(s.status) && <><p>Ushbu aniq argumentlarni tasdiqlaysizmi?</p><button disabled={busy || !canWrite} style={button} onClick={()=>run(()=>approve(s,'approved'))}>Tasdiqlash</button><button disabled={busy || !canWrite} style={button} onClick={()=>run(()=>approve(s,'rejected'))}>Rad etish</button></>}
            {s.approver && <p>Tasdiqlovchi: {s.approver}</p>}
            <details open={s.status==='succeeded'}><summary>Natija</summary><pre style={pre}>{JSON.stringify(s.result,null,2)}</pre></details>
            {s.status==='uncertain' && <>
              <p style={{color:'#fdba74'}}>Natija noaniq. Avtomatik qayta ijro bloklangan. Owner tashqi tizimni tekshirib, quyidagi dalil bilan yakunlaydi.</p>
              <ReconcileControl client={client} tenant={tenant} role={role} step={s.id}
                onDone={async()=>{await open(selected.id);await refresh();}}/>
            </>}
          </article>)}
        </section>}
      </div>}
      {tab==='agents' && <section style={panel}><h2>Pack agentlari</h2>{agents.map(a=><article key={a.id} style={{borderBottom:'1px solid #334155',padding:12}}><h3>{a.name}</h3><p>{a.department} · {a.ladder}</p><p>{a.tools.join(', ')}</p>{a.tools.some(n=>!tools.find(t=>t.name===n)) && <p style={{color:'#fca5a5'}}>Pack ichidagi ayrim tool uchun runtime adapter mavjud emas.</p>}</article>)}</section>}
      {tab==='inbox' && <InboxPanel client={client} tenant={tenant} canWrite={canWrite}/>}
      {tab==='approvals' && <ApprovalsPanel client={client} tenant={tenant} canDecide={canWrite} onOpenTask={id=>void run(async()=>{await open(id);setTab('tasks');})}/>}
      {tab==='orders' && <OrdersPanel client={client} tenant={tenant}/>}
      {tab==='catalog' && <CatalogPanel client={client} tenant={tenant}/>}
      {tab==='audit' && <section style={panel}><h2>Audit</h2>{audit.map(a=><p key={a.id}><time>{new Date(a.created*1000).toLocaleString()}</time> · <strong>{a.action}</strong> · {a.actor} · <code>{a.task.slice(0,10)}</code></p>)}</section>}
      {tab==='connections' && <ChannelsPanel client={client} tenant={tenant}/>}
      {tab==='connections' && <section style={panel}><h2>Mijoz bazasi connectorlari</h2>
        <p>SQLite lokal o‘qish testlangan. PostgreSQL adapteri contract-test bosqichida, live tekshiruv talab qilinadi. Boshqa CRM/ERP driverlari adapter_required sifatida ko‘rsatiladi.</p>
        <p>Ulanishlar operatorning server konfiguratsiyasidan olinadi. Secret va baza manzili brauzerga yuborilmaydi.</p>
        {connections.length===0 && <p>Ulanish sozlanmagan.</p>}
        {connections.map(c=><article key={c.id} style={{borderBottom:'1px solid #334155',padding:12}}>
          <h3>{c.id}</h3><p>{c.driver} · {c.mode} · {c.status} · {c.lifecycle || 'configured'}</p>
          <pre style={pre}>{JSON.stringify(c.tables,null,2)}</pre>
          {c.mode==='managed_approved_operations' && <p>Tasdiqli DB operatsiyalari. Yozish uchun plan va alohida tasdiq kerak; tarmoq bazasida haqiqiy tekshiruv hali tasdiqlanmagan.</p>}
          {c.mode==='read_only' && <>
            <label>Tekshiruv agenti <select aria-label={`${c.id} tekshiruv agenti`} style={input}
              value={probeAgents[c.id] || ''}
              onChange={event=>setProbeAgents(current=>({...current,[c.id]:event.target.value}))}>
              <option value="">{c.agent_ids?.length?'Agentni tanlang':'Admin tekshiruvi'}</option>
              {agents.filter(a=>!c.agent_ids?.length || c.agent_ids.includes(a.id)).map(a=>
                <option key={a.id} value={a.id}>{a.name}</option>)}
            </select></label>
            <button style={button} disabled={busy || frozen || !['owner','integrator'].includes(role)
              || !['configured','healthy','degraded'].includes(c.lifecycle || 'configured')
              || Boolean(c.agent_ids?.length && !probeAgents[c.id])}
              onClick={()=>run(async()=>{
                setProbeResults(current=>{const next={...current};delete next[c.id];return next;});
                const result=await req<{verified_at:number}>(`/connections/${encodeURIComponent(c.id)}/verify`,
                  probeAgents[c.id]?{agent:probeAgents[c.id]}:{});
                setProbeResults(current=>({...current,[c.id]:result}));
              })}>Bitta jadvalni xavfsiz tekshirish</button>
            {probeResults[c.id] && <p>Bir jadval o‘qish tekshiruvi o‘tdi: {new Date(probeResults[c.id].verified_at*1000).toLocaleString()}.
              Mijoz satrlari brauzerga berilmadi. Bu barcha jadvallar yoki production tayyorligi tasdig‘i emas;
              health holati doimiy saqlanmadi.</p>}
          </>}
        </article>)}
      </section>}
      {tab==='devices' && <section style={panel}><h2>Qurilma nazorati</h2><p>Linux’da allowlist doirasida fayl o‘qish va ro‘yxatlash lokal tekshirilgan. macOS descriptor yordamchisi source/kontrakt bosqichida. Printer, ekran va app launch hali o‘chirilgan.</p>
        <input aria-label="Device ID" style={input} value={deviceId} onChange={e=>setDeviceId(e.target.value)}/>
        <button style={button} disabled={busy || !isOwner || frozen} onClick={()=>run(async()=>{const r=await req<{device_token:string}>('/devices',{device_id:deviceId,revoked:false});setDeviceToken(r.device_token);setDevices((await req<{devices:Device[]}>('/devices')).devices);})}>Owner: ulash / tokenni almashtirish</button>
        {deviceToken && <div><p>Runner token, 24 soat. Xavfsiz lokal konfiguratsiyaga saqlang.</p><textarea aria-label="Device token" readOnly style={{...input,width:'100%',boxSizing:'border-box'}} value={deviceToken}/><button style={button} onClick={()=>setDeviceToken('')}>Tokenni yashirish</button></div>}
        {devices.map(d=><p key={d.id}>{d.id} · {d.revoked?'revoked':Date.now()/1000-d.seen<90?'online':'offline'} · gen {d.generation} <button style={button} disabled={busy || !isOwner} onClick={()=>run(async()=>{await req('/devices',{device_id:d.id,revoked:true});setDevices((await req<{devices:Device[]}>('/devices')).devices);})}>Bekor qilish</button></p>)}
        <button style={button} disabled={busy || !isOwner} onClick={()=>run(async()=>{await req('/freeze',{stopped:true});await refresh();})}>Owner: barcha yangi ijrolarni to‘xtatish</button>
        <button style={button} disabled={busy || !isOwner} onClick={()=>run(async()=>{await req('/freeze',{stopped:false});await refresh();})}>Owner: davom ettirish</button>
      </section>}
    </>}
    <footer style={{marginTop:30,color:'#94a3b8',fontSize:12}}>Tashqi write natijasi noaniq bo‘lsa avtomatik retry yo‘q. Secretlar pack yoki UI logida saqlanmaydi. To‘liq release mezonlari: docs/IMPLEMENTATION-STATUS.md.</footer>
  </main>;
}
const panel={background:'#111c2e',border:'1px solid #233149',borderRadius:12,padding:20,marginBottom:16};
const input={background:'#0b1220',color:'#e2e8f0',border:'1px solid #475569',borderRadius:6,padding:10,margin:4};
const button={background:'#1e3a5f',color:'#e2e8f0',border:'1px solid #3b5273',borderRadius:7,padding:'10px 14px',margin:4,cursor:'pointer'};
const pre={whiteSpace:'pre-wrap' as const,overflowWrap:'anywhere' as const,fontSize:12,background:'#0b1220',padding:12,borderRadius:6};
