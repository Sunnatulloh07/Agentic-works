'use client';
import {useEffect,useRef,useState} from 'react';
import {type SessionClient} from '../lib/session-client.mjs';
import {syncRequest,googlePath,type SyncKind} from '../lib/google-data-client.mjs';
type Agent={id:string;name:string};
type SyncResult={revision:number;changed_records:number;phase:string;caught_up:boolean};
type Records={records:unknown[];next_after:string|null};
export default function GoogleData({client,tenant,agents,frozen}:{client:SessionClient;tenant:string;agents:Agent[];frozen:boolean}){
  const [connection,setConnection]=useState('google');const [agent,setAgent]=useState(agents[0]?.id||'');
  const [kind,setKind]=useState<SyncKind>('gmail');const [calendar,setCalendar]=useState('primary');
  const [step,setStep]=useState('');const [confirm,setConfirm]=useState(false);const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');const [rows,setRows]=useState<unknown[]>([]);const [after,setAfter]=useState<string|null>(null);
  const mounted=useRef(true);const generation=useRef(0);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;generation.current++;};},[]);
  useEffect(()=>{generation.current++;setRows([]);setAfter(null);setMessage('');setConfirm(false);},[tenant,connection,agent,kind,calendar]);
  async function action(name:'page'|'records'|'reset'|'reconcile',next=false){
    if(busy||frozen)return;
    const current=generation.current;setBusy(true);setMessage('');
    const valid=()=>mounted.current&&current===generation.current;
    try{
      const body=syncRequest(connection,agent,kind,kind==='calendar'?calendar:'');
      if(name==='reconcile'){
        if(!/^[A-Za-z0-9_.-]{1,128}$/.test(step))throw new Error('Step ID yaroqsiz');
        const r=await client.request<{status:string}>(`/platform/${encodeURIComponent(tenant)}/google/steps/${encodeURIComponent(step)}/reconcile`,{});
        if(valid())setMessage(`Tekshiruv: ${r.status}. Providerga qayta yozish yuborilmadi.`);
      }else if(name==='records'){
        const r=await client.request<Records>(googlePath(tenant,connection,name),{...body,limit:50,after:next?(after||''):''});
        if(valid()){setRows(r.records);setAfter(r.next_after);}
      }else if(name==='reset'){
        if(!confirm)throw new Error('Lokal sync resetini tasdiqlang');
        await client.request(googlePath(tenant,connection,name),{...body,confirm_reset:true});
        if(valid()){setRows([]);setAfter(null);setConfirm(false);setMessage('Lokal nusxa tozalandi. Google ma’lumotlari o‘chirilmadi.');}
      }else{
        const r=await client.request<SyncResult>(googlePath(tenant,connection,name),body);
        if(valid())setMessage(`Revision ${r.revision}; ${r.changed_records} yozuv; ${r.phase}; ${r.caught_up?'joriy o‘zgarishlar olindi':'keyingi sahifa mavjud'}.`);
      }
    }catch{if(valid())setMessage('Amal tasdiqlanmadi. Ruxsat, scope, konfiguratsiya yoki cursor holatini tekshiring. Yozish avtomatik takrorlanmaydi.');}
    finally{if(mounted.current)setBusy(false);}
  }
  return <section><h2>Google ma’lumotlari va reconciliation</h2>
    <p>Owner uchun metadata sync. Xat matni, fayl kontenti va attachment olinmaydi. Lokal tekshiruvlar live Google acceptance o‘rnini bosmaydi.</p>
    <fieldset disabled={busy||frozen}><legend>Sync manbasi</legend>
      <label>Ulanish ID <input value={connection} onChange={e=>setConnection(e.target.value)}/></label>{' '}
      <label>Agent <select value={agent} onChange={e=>setAgent(e.target.value)}>{agents.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>{' '}
      <label>Tur <select value={kind} onChange={e=>setKind(e.target.value as SyncKind)}><option value="gmail">Gmail</option><option value="drive">Drive</option><option value="calendar">Calendar</option></select></label>
      {kind==='calendar'&&<label>Calendar ID <input value={calendar} onChange={e=>setCalendar(e.target.value)}/></label>}
      <p><button onClick={()=>void action('page')}>Bitta sync sahifasini bajarish</button>{' '}<button onClick={()=>void action('records')}>Lokal metadata</button>{' '}
      <button disabled={!after} onClick={()=>void action('records',true)}>Keyingi yozuvlar</button></p>
      <label><input type="checkbox" checked={confirm} onChange={e=>setConfirm(e.target.checked)}/> Lokal nusxa va cursor tozalanishini tasdiqlayman</label>{' '}
      <button disabled={!confirm} onClick={()=>void action('reset')}>Lokal sync reset</button>
    </fieldset>
    <fieldset disabled={busy||frozen}><legend>Noaniq Google write</legend>
      <label>Step ID <input value={step} onChange={e=>setStep(e.target.value)}/></label>{' '}
      <button disabled={!step} onClick={()=>void action('reconcile')}>Provider dalilini tekshirish</button>
      <p>Gmail uchun gmail.readonly ham talab etiladi. Natija topilmasa uncertain saqlanadi; xat yoki tadbir qayta yaratilmaydi.</p>
    </fieldset>
    {message&&<p role="status">{message}</p>}
    {rows.length>0&&<pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify(rows,null,2)}</pre>}
  </section>;
}
