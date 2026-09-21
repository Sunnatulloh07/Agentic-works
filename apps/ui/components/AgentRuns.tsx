'use client';
import {useEffect, useRef, useState} from 'react';
import {type SessionClient} from '../lib/session-client.mjs';

type Agent = {id:string; name:string};
type Run = {
  id:string; agent:string; status:string; steps:number; max_steps:number;
  calls:number; max_calls:number; deadline:number; error:string;
};
type RunDetail = Run & {
  input:string; answer:string; evidence_ids:string[];
  turns:{position:number; task:string; status:string}[];
  semantic_fact_check:string;
};
type Props = {
  client:SessionClient; tenant:string; agents:Agent[]; role:string; frozen:boolean;
  onOpenTask:(id:string)=>Promise<void>;
};
const active = new Set(['pending','planning','waiting_task']);

export default function AgentRuns({client,tenant,agents,role,frozen,onOpenTask}:Props) {
  const [agent,setAgent] = useState(agents[0]?.id || '');
  const [text,setText] = useState('');
  const [maxSteps,setMaxSteps] = useState(6);
  const [runs,setRuns] = useState<Run[]>([]);
  const [selected,setSelected] = useState<RunDetail|null>(null);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const pending = useRef<{signature:string;key:string}|null>(null);
  const writer = ['owner','operator'].includes(role);
  const path = `/platform/${encodeURIComponent(tenant)}/agent-runs`;
  const request = <T,>(suffix:string,body?:unknown):Promise<T> => client.request<T>(path+suffix,body);

  async function perform(action:()=>Promise<void>) {
    setBusy(true);setError('');
    try {await action();} catch(e) {setError(e instanceof Error?e.message:'Amal bajarilmadi');}
    finally {setBusy(false);}
  }
  async function refresh() {
    const result = await request<{runs:Run[]}>('');
    setRuns(result.runs);
    if(selected) setSelected(await request<RunDetail>('/'+encodeURIComponent(selected.id)));
  }
  useEffect(()=>{void perform(refresh);},[]);
  useEffect(()=>{
    if(!agents.some(item=>item.id===agent)) setAgent(agents[0]?.id || '');
  },[agents,agent]);

  async function create() {
    const payload = {agent,text:text.trim(),max_steps:maxSteps,max_seconds:1800};
    const signature = JSON.stringify(payload);
    // An explicit user retry after a lost response reuses the same key.
    // There is no automatic POST retry and no refresh-token persistence.
    if(!pending.current || pending.current.signature!==signature) {
      pending.current = {signature,key:crypto.randomUUID()};
    }
    const result = await request<{run_id:string}>('',{...payload,key:pending.current.key});
    const detail = await request<RunDetail>('/'+encodeURIComponent(result.run_id));
    setSelected(detail);setText('');pending.current=null;
    setRuns((await request<{runs:Run[]}>('')).runs);
  }

  return <section style={panel}>
    <h2>Natijaga tayanuvchi agent loop</h2>
    <p>Kod preview. Agent har bir haqiqiy tool natijasidan keyin navbatdagi bitta qadamni tanlaydi.
      Tashqi va ichki write amallari odatdagi task tasdig‘ini kutadi. Bu cheksiz yoki tasdiqsiz ijro emas.</p>
    <p style={{color:'#fde68a'}}>LLM operator konfiguratsiyasida alohida yoqiladi. Tool natijalari sozlangan
      modelga yuborilishi mumkin. Secret kiritmang. Runtime offline testlari bajarildi; API/UI va live provider tekshiruvlari tugallanmagan.</p>
    {error && <p role="alert" style={{color:'#fca5a5'}}>{error}</p>}
    <label>Agent <select style={input} value={agent} onChange={event=>setAgent(event.target.value)}>
      {agents.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    <label>Maksimal tool qadamlar <input style={input} type="number" min={1} max={12} step={1}
      value={maxSteps} onChange={event=>setMaxSteps(Number(event.target.value))}/></label>
    <p>Wall deadline: 30 daqiqa, tasdiq kutish ham shu vaqtga kiradi. Model chaqiruvlari: ko‘pi bilan qadamlar + 1.
      Bu haqiqiy pul xarajati limiti emas.</p>
    <textarea aria-label="Agent loop topshirig‘i" style={{...input,width:'100%',boxSizing:'border-box'}}
      rows={4} maxLength={4000} value={text} onChange={event=>setText(event.target.value)}/>
    <button style={button} disabled={busy || !writer || frozen || !agent || !text.trim()
      || !Number.isInteger(maxSteps) || maxSteps<1 || maxSteps>12} onClick={()=>void perform(create)}>Loop yaratish</button>
    <button style={button} disabled={busy} onClick={()=>void perform(refresh)}>Holatni yangilash</button>
    {busy && <p role="status">Yuklanmoqda...</p>}
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(280px,1fr))',gap:16}}>
      <div><h3>Agent runlar</h3>
        {runs.length===0 && <p>Hozircha run yo‘q.</p>}
        {runs.map(item=><button key={item.id} style={{...button,display:'block',textAlign:'left'}}
          disabled={busy} onClick={()=>void perform(async()=>setSelected(await request<RunDetail>('/'+encodeURIComponent(item.id))))}>
          {item.agent} · {item.status}<br/>{item.steps}/{item.max_steps} qadam · {item.id.slice(0,10)}
        </button>)}
      </div>
      {selected && <article>
        <h3>{selected.status}</h3><p><code>{selected.id}</code></p>
        <p>{selected.steps}/{selected.max_steps} qadam · {selected.calls}/{selected.max_calls} rejalashtirish rezervi</p>
        {selected.error && <p style={{color:'#fdba74'}}>{selected.error}</p>}
        {active.has(selected.status) && <button style={button} disabled={busy || !writer}
          onClick={()=>void perform(async()=>{
            await request('/'+encodeURIComponent(selected.id)+'/cancel',{});
            setSelected(await request<RunDetail>('/'+encodeURIComponent(selected.id)));
            setRuns((await request<{runs:Run[]}>('')).runs);
          })}>Loopni bekor qilish</button>}
        <p style={{whiteSpace:'pre-wrap'}}>{selected.input}</p>
        {selected.answer && <>
          <h4>{selected.status==='needs_input'?'Aniqlashtirish kerak':'Model javobi'}</h4>
          <p style={{whiteSpace:'pre-wrap'}}>{selected.answer}</p>
          <p style={{color:'#fde68a'}}>Dalil identifikatorlari tekshiriladi, lekin javobdagi har bir jumla
            fakt sifatida avtomatik tasdiqlanmagan. Qaror qilishdan oldin tool natijasini ko‘ring.</p>
          <p>{selected.evidence_ids.join(', ')}</p>
        </>}
        {selected.turns.map(turn=><p key={turn.task}>
          {turn.position+1}. {turn.status}
          <button style={button} disabled={busy} onClick={()=>void perform(()=>onOpenTask(turn.task))}>
            Task va tasdiqlarni ochish
          </button>
        </p>)}
        {selected.status==='uncertain' && <p>Allaqachon yuborilgan amal orqaga qaytarilgan deb hisoblanmaydi.
          Owner tegishli taskni dalil bilan reconcile qiladi. Loop avtomatik qayta boshlanmaydi.</p>}
      </article>}
    </div>
  </section>;
}
const panel={background:'#111c2e',border:'1px solid #233149',borderRadius:12,padding:20,marginBottom:16};
const input={background:'#0b1220',color:'#e2e8f0',border:'1px solid #475569',borderRadius:6,padding:10,margin:4};
const button={background:'#1e3a5f',color:'#e2e8f0',border:'1px solid #3b5273',borderRadius:7,padding:'10px 14px',margin:4,cursor:'pointer'};
