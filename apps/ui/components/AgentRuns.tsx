'use client';
import {useEffect, useState} from 'react';
import {type SessionClient} from '../lib/session-client.mjs';
import {friendlyError, statusLabel} from '../lib/format.mjs';
import {createIdempotency} from '../lib/idempotency.mjs';
import {ConfirmButton} from './ui';

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
  const [keys] = useState(() => createIdempotency());
  const writer = ['owner','operator'].includes(role);
  const path = `/platform/${encodeURIComponent(tenant)}/agent-runs`;
  const request = <T,>(suffix:string,body?:unknown):Promise<T> => client.request<T>(path+suffix,body);

  async function perform(action:()=>Promise<void>) {
    setBusy(true);setError('');
    try {await action();} catch(e) {setError(friendlyError(e));}
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
    // An explicit user retry after a lost response reuses the same key.
    // There is no automatic POST retry and no refresh-token persistence.
    const result = await request<{run_id:string}>('',{...payload,key:keys.key('run',payload)});
    keys.settle('run',payload);
    const detail = await request<RunDetail>('/'+encodeURIComponent(result.run_id));
    setSelected(detail);setText('');
    setRuns((await request<{runs:Run[]}>('')).runs);
  }

  return <section className="panel">
    <h2>Agent sikllari</h2>
    <p>Kod preview. Agent har bir haqiqiy tool natijasidan keyin navbatdagi bitta qadamni tanlaydi.
      Tashqi va ichki write amallari odatdagi task tasdig‘ini kutadi. Bu cheksiz yoki tasdiqsiz ijro emas.</p>
    <p className="warn-text">LLM operator konfiguratsiyasida alohida yoqiladi. Tool natijalari sozlangan
      modelga yuborilishi mumkin. Secret kiritmang. Runtime offline testlari bajarildi; API/UI va live provider tekshiruvlari tugallanmagan.</p>
    {error && <p role="alert" className="notice error">{error}</p>}
    <label>Agent <select value={agent} onChange={event=>setAgent(event.target.value)}>
      {agents.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    <label>Maksimal tool qadamlar <input type="number" min={1} max={12} step={1}
      value={maxSteps} onChange={event=>setMaxSteps(Number(event.target.value))}/></label>
    <p>Wall deadline: 30 daqiqa, tasdiq kutish ham shu vaqtga kiradi. Model chaqiruvlari: ko‘pi bilan qadamlar + 1.
      Bu haqiqiy pul xarajati limiti emas.</p>
    <textarea aria-label="Agent sikli topshirig‘i"
      rows={4} maxLength={4000} value={text} onChange={event=>setText(event.target.value)}/>
    <button className="btn" disabled={busy || !writer || frozen || !agent || !text.trim()
      || !Number.isInteger(maxSteps) || maxSteps<1 || maxSteps>12} onClick={()=>void perform(create)}>Loop yaratish</button>
    <button className="btn" disabled={busy} onClick={()=>void perform(refresh)}>Holatni yangilash</button>
    {busy && <p role="status" className="muted">Yuklanmoqda…</p>}
    <div className="grid">
      <div><h3>Agent runlar</h3>
        {runs.length===0 && <p>Hozircha run yo‘q.</p>}
        {runs.map(item=><button key={item.id} className="btn listbtn"
          disabled={busy} onClick={()=>void perform(async()=>setSelected(await request<RunDetail>('/'+encodeURIComponent(item.id))))}>
          {item.agent} · {statusLabel(item.status)}<br/>{item.steps}/{item.max_steps} qadam · {item.id.slice(0,10)}
        </button>)}
      </div>
      {selected && <article>
        <h3>{statusLabel(selected.status)}</h3><p><code>{selected.id}</code></p>
        <p>{selected.steps}/{selected.max_steps} qadam · {selected.calls}/{selected.max_calls} rejalashtirish rezervi</p>
        {selected.error && <p className="warn-text">{selected.error}</p>}
        {active.has(selected.status) && <ConfirmButton label="Siklni bekor qilish" disabled={busy || !writer}
          title="Agent siklini bekor qilasizmi?" message="Keyingi qadamlar bajarilmaydi. Allaqachon bajarilgan amallar orqaga qaytmaydi."
          confirmLabel="Ha, bekor qilish" onConfirm={()=>perform(async()=>{
            await request('/'+encodeURIComponent(selected.id)+'/cancel',{});
            setSelected(await request<RunDetail>('/'+encodeURIComponent(selected.id)));
            setRuns((await request<{runs:Run[]}>('')).runs);
          })}/>}
        <p style={{whiteSpace:'pre-wrap'}}>{selected.input}</p>
        {selected.answer && <>
          <h4>{selected.status==='needs_input'?'Aniqlashtirish kerak':'Model javobi'}</h4>
          <p style={{whiteSpace:'pre-wrap'}}>{selected.answer}</p>
          <p className="warn-text">Dalil identifikatorlari tekshiriladi, lekin javobdagi har bir jumla
            fakt sifatida avtomatik tasdiqlanmagan. Qaror qilishdan oldin tool natijasini ko‘ring.</p>
          <p>{selected.evidence_ids.join(', ')}</p>
        </>}
        {selected.turns.map(turn=><p key={turn.task}>
          {turn.position+1}. {statusLabel(turn.status)}
          <button className="btn" disabled={busy} onClick={()=>void perform(()=>onOpenTask(turn.task))}>
            Task va tasdiqlarni ochish
          </button>
        </p>)}
        {selected.status==='uncertain' && <p>Allaqachon yuborilgan amal orqaga qaytarilgan deb hisoblanmaydi.
          Owner tegishli taskni dalil bilan reconcile qiladi. Loop avtomatik qayta boshlanmaydi.</p>}
      </article>}
    </div>
  </section>;
}

