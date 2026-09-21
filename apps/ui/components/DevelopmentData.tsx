'use client';
import {useState} from 'react';
import {type SessionClient} from '../lib/session-client.mjs';
type Props={client:SessionClient;tenant:string;role:string;frozen:boolean;agents:{id:string;name:string;tools:string[]}[]};
type Budget={currency:string;limit_micro:number;spent_micro:number;reserved_micro:number;available_micro:number;inflight:number;period:string;warning_80_percent:boolean;limit_exceeded:boolean};
type Pending={id:string;status:string;amount_micro:number;period:string};
type Document={id:string;title:string;version:number;deleted:number};
type Match={source_id:string;document:string;version:number;text:string;title:string};
const section={background:'#111c2e',padding:20,borderRadius:12,marginBottom:16};
const control={display:'block',background:'#0b1220',color:'#e2e8f0',border:'1px solid #475569',padding:8,margin:'8px 0',borderRadius:6};
const button={...control,display:'inline-block',marginRight:8,cursor:'pointer'};

export function BudgetPanel({client,tenant,role}:Props){
  const [data,setData]=useState<Budget|null>(null),[pending,setPending]=useState<Pending[]>([]);
  const [currency,setCurrency]=useState('USD'),[limit,setLimit]=useState('10000000'),[parallel,setParallel]=useState('4');
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[reservation,setReservation]=useState('');
  const [actual,setActual]=useState('0'),[evidence,setEvidence]=useState('');
  const base=`/platform/${encodeURIComponent(tenant)}/usage-budget`;
  async function refresh(){
    const result=await client.request<Budget>(base);setData(result);setCurrency(result.currency);
    if(role==='owner')setPending((await client.request<{reservations:Pending[]}>(base+'/pending')).reservations);
  }
  async function run(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(e instanceof Error?e.message:'Xato');}finally{setBusy(false);}}
  return <section style={section}><h2>Xarajat budjeti</h2>
    <p>Butun microunit hisob-kitobi. 1,000,000 microunit = 1 valyuta birligi. Bu subscription billing yoki provider invoice tasdig‘i emas.</p>
    <button style={button} disabled={busy} onClick={()=>run(refresh)}>Hisobni yangilash</button>
    {error && <p role="alert">{error} Budjet hali yaratilmagan bo‘lsa, owner quyida sozlaydi.</p>}
    {data && <><p>{data.period} · {data.currency} · sarflangan: {data.spent_micro} · rezerv: {data.reserved_micro} · qolgan: {data.available_micro} · ochiq chaqiruvlar: {data.inflight}</p>
      {data.warning_80_percent && <p role="status">Budjetning kamida 80 foizi band.</p>}{data.limit_exceeded && <p role="alert">Provider sarfi limitdan oshgan. Yangi rezervlar bloklanadi.</p>}</>}
    {role==='owner' && <><h3>Owner sozlamalari</h3>
      <label>Valyuta<input disabled={busy} style={control} value={currency} maxLength={3} onChange={e=>setCurrency(e.target.value.toUpperCase())}/></label>
      <label>Oylik limit, microunit<input disabled={busy} style={control} type="number" min="1" step="1" max="1000000000000000" value={limit} onChange={e=>setLimit(e.target.value)}/></label>
      <label>Parallel chaqiruvlar<input disabled={busy} style={control} type="number" min="1" max="100" step="1" value={parallel} onChange={e=>setParallel(e.target.value)}/></label>
      <button style={button} disabled={busy} onClick={()=>run(async()=>{await client.request(base,{currency,limit_micro:Number(limit),max_inflight:Number(parallel)},'PUT');await refresh();})}>Budjetni saqlash</button>
      <h3>Noaniq / ochiq rezervlarni reconcile qilish</h3><p>Faqat haqiqiy foydalanish dalili bilan. Davom etayotgan provider chaqiruvini tekshirmasdan nolga yopmang.</p>
      <label>Rezerv<select disabled={busy} style={control} value={reservation} onChange={e=>setReservation(e.target.value)}><option value="">Tanlang</option>{pending.map(p=><option key={p.id} value={p.id}>{p.id.slice(0,12)} · {p.status} · {p.amount_micro}</option>)}</select></label>
      <label>Haqiqiy sarf, microunit<input disabled={busy} style={control} type="number" min="0" step="1" value={actual} onChange={e=>setActual(e.target.value)}/></label>
      <label>Tashqi dalil<input disabled={busy} style={control} value={evidence} maxLength={500} onChange={e=>setEvidence(e.target.value)} placeholder="Invoice yoki usage receipt reference"/></label>
      <button style={button} disabled={busy || !reservation || !evidence.trim()} onClick={()=>run(async()=>{await client.request(base+'/'+encodeURIComponent(reservation)+'/reconcile',{actual_micro:Number(actual),evidence});setReservation('');setEvidence('');await refresh();})}>Dalil bilan yakunlash</button>
    </>}
  </section>;
}

export function KnowledgePanel({client,tenant,role,frozen,agents}:Props){
  const [collection,setCollection]=useState('manuals'),[agent,setAgent]=useState('');
  const [document,setDocument]=useState('manual-1'),[title,setTitle]=useState('Qo‘llanma'),[content,setContent]=useState('');
  const [version,setVersion]=useState('0'),[query,setQuery]=useState(''),[docs,setDocs]=useState<Document[]>([]),[matches,setMatches]=useState<Match[]>([]);
  const [error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  const base=`/platform/${encodeURIComponent(tenant)}/knowledge/${encodeURIComponent(collection)}`;
  async function run(fn:()=>Promise<void>){setBusy(true);setError('');setMessage('');try{await fn();}catch(e){setError(e instanceof Error?e.message:'Xato');}finally{setBusy(false);}}
  async function load(){setDocs((await client.request<{documents:Document[]}>(base+'/documents')).documents);}
  const allowed=agents.filter(a=>a.tools.includes('knowledge.search'));
  return <section style={section}><h2>Bilim bazasi</h2><p>Matn ingestion, versiyalash, agent ACL va BM25 qidiruvi. Binary fayl parseri, avtomatik embeddings yoki semantik fakt tekshiruvi deb talqin qilinmasin.</p>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <label>Kolleksiya<input disabled={busy} style={control} value={collection} maxLength={128} onChange={e=>{setCollection(e.target.value);setDocs([]);setMatches([]);}}/></label>
    <button style={button} disabled={busy} onClick={()=>run(load)}>Hujjatlar ro‘yxati</button>
    {role==='owner' && <button style={button} disabled={busy || frozen} onClick={()=>run(async()=>{await client.request(`/platform/${encodeURIComponent(tenant)}/knowledge/collections`,{id:collection});setMessage('Kolleksiya yaratildi yoki oldin mavjud.');})}>Matn kolleksiyasini yaratish</button>}
    <label>Agent<select disabled={busy} style={control} value={agent} onChange={e=>{setAgent(e.target.value);setMatches([]);}}><option value="">Tanlang</option>{allowed.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
    {!allowed.length && <p>Pack konfiguratsiyasida agentga knowledge.search vositasi berilishi kerak. UI bu ruxsatni o‘zi qo‘sha olmaydi.</p>}
    {role==='owner' && <><button style={button} disabled={busy || frozen || !agent} onClick={()=>run(async()=>{await client.request(base+'/grant',{agent,allowed:true},'PUT');setMessage('Agentga kolleksiya ruxsati berildi.');})}>Agentga ruxsat berish</button>
      <button style={button} disabled={busy || frozen || !agent} onClick={()=>run(async()=>{await client.request(base+'/grant',{agent,allowed:false},'PUT');setMatches([]);setMessage('Ruxsat bekor qilindi.');})}>Ruxsatni bekor qilish</button></>}
    <h3>Hujjat matni</h3><label>Hujjat ID<input disabled={busy} style={control} value={document} maxLength={128} onChange={e=>setDocument(e.target.value)}/></label>
    <label>Sarlavha<input disabled={busy} style={control} value={title} maxLength={200} onChange={e=>setTitle(e.target.value)}/></label>
    <label>Kutilgan mavjud versiya (yangi hujjat uchun 0)<input disabled={busy} style={control} type="number" min="0" step="1" value={version} onChange={e=>setVersion(e.target.value)}/></label>
    <label>Matn<textarea disabled={busy} style={{...control,width:'100%',boxSizing:'border-box'}} rows={8} value={content} maxLength={100000} onChange={e=>setContent(e.target.value)}/></label>
    <button style={button} disabled={busy || frozen || !content.trim()} onClick={()=>run(async()=>{const result=await client.request<{version:number;chunks:number}>(base+'/documents',{id:document,title,content,expected_version:Number(version)},'PUT');setVersion(String(result.version));setMessage(`${result.chunks} bo‘lak saqlandi, versiya ${result.version}.`);await load();})}>Versiya bilan saqlash</button>
    {docs.map(d=><p key={d.id}>{d.title} · {d.id} · v{d.version} · {d.deleted?'o‘chirilgan':'faol'} <button style={button} disabled={busy} onClick={()=>{setDocument(d.id);setTitle(d.title);setVersion(String(d.version));setContent('');}}>Tahrir ma’lumotlari</button></p>)}
    <h3>Agent sifatida qidirish</h3><label>So‘rov<input disabled={busy} style={control} value={query} maxLength={500} onChange={e=>setQuery(e.target.value)}/></label>
    <button style={button} disabled={busy || frozen || !agent || !query.trim()} onClick={()=>run(async()=>{setMatches([]);const result=await client.request<{matches:Match[]}>(base+'/search',{agent,query,limit:4});setMatches(result.matches);if(!result.matches.length)setMessage('Mos manba topilmadi.');})}>Manbalarni qidirish</button>
    {matches.map(m=><article key={m.source_id} style={{borderTop:'1px solid #475569',paddingTop:12}}><strong>{m.title} · v{m.version}</strong><p style={{whiteSpace:'pre-wrap'}}>{m.text}</p><small>{m.source_id}</small></article>)}
  </section>;
}
