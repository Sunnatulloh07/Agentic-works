'use client';
import {useEffect,useRef,useState} from 'react';
import {type SessionClient} from '../lib/session-client.mjs';
import {authorization,acceptCallback} from '../lib/oauth-client.mjs';
type Connection={id:string;provider:string;status:string;account?:string;generation?:number};
type Pending={popup:Window;state:string;connection:string;expires:number};
export default function OAuthConnections({client,tenant,frozen}:{client:SessionClient;tenant:string;frozen:boolean}){
  const [rows,setRows]=useState<Connection[]>([]);const [busy,setBusy]=useState(false);const [message,setMessage]=useState('');
  const pending=useRef<Pending|null>(null);const mounted=useRef(true);
  const root=`/platform/${encodeURIComponent(tenant)}/oauth`;
  const path=(id:string,action:string)=>`${root}/${encodeURIComponent(id)}/${action}`;
  async function refresh(){const r=await client.request<{connections:Connection[]}>(root+'/connections');setRows(r.connections);}
  useEffect(()=>{
    mounted.current=true;
    void refresh().catch(()=>setMessage('OAuth konfiguratsiyasi mavjud emas. Server operatori sozlashi kerak.'));
    const listener=(event:MessageEvent)=>{
      const p=pending.current;const d=event.data;
      if(!p || !acceptCallback(p,event,window.location.origin))return;
      pending.current=null;p.popup.close();
      if(d.denied===true){setBusy(false);setMessage('Google ruxsati rad etildi.');return;}
      if(typeof d.code!=='string' || !d.code || d.code.length>4096){setBusy(false);setMessage('Callback yaroqsiz.');return;}
      void client.request(path(p.connection,'complete'),{state:d.state,code:d.code})
        .then(()=>{setMessage('Google ulanishi saqlandi.');return refresh();})
        .catch(()=>setMessage('OAuth yakunlanmadi. Holatni yangilang; noaniq token avtomatik qayta yuborilmaydi.'))
        .finally(()=>setBusy(false));
    };
    window.addEventListener('message',listener);
    const timer=window.setInterval(()=>{
      const p=pending.current;if(p && (p.popup.closed || p.expires<Date.now())){
        p.popup.close();pending.current=null;setBusy(false);setMessage('OAuth oynasi yopildi yoki vaqt tugadi.');
      }
    },1000);
    return ()=>{mounted.current=false;window.removeEventListener('message',listener);window.clearInterval(timer);pending.current?.popup.close();pending.current=null;};
  },[client,tenant]);
  async function begin(id:string){
    if(busy || frozen)return;
    const popup=window.open('about:blank','_blank','popup,width=600,height=740');
    if(!popup){setMessage('Brauzer popup oynasini blokladi.');return;}
    setBusy(true);setMessage('');
    try{
      const result=await client.request<{authorization_url:string;expires_in:number}>(path(id,'begin'),{});
      if(!mounted.current)throw new Error();
      const validated=authorization(result,window.location.origin);
      pending.current={popup,state:validated.state,connection:id,expires:validated.expires};
      popup.location.href=validated.url;
    }catch{popup.close();setBusy(false);setMessage('OAuth boshlanmadi yoki callback manzili ushbu UI bilan mos emas.');}
  }
  async function action(id:string,name:string,body:unknown={}){
    setBusy(true);setMessage('');try{
      const result=await client.request(path(id,name),body);setMessage(JSON.stringify(result));await refresh();
    }catch{setMessage('Amal tasdiqlanmadi. Holatni yangilang.');}finally{setBusy(false);}
  }
  return <section><h2>Google OAuth ulanishlari</h2>
    <p>Faqat owner boshqaradi. Provider tokenlari brauzerga berilmaydi. Google oynasi asosiy sahifa bilan bir xil callback originiga qaytishi shart.</p>
    <button disabled={busy} onClick={()=>void refresh().catch(()=>setMessage('Holat olinmadi.'))}>Yangilash</button>
    {message&&<p role="status">{message}</p>}
    {rows.map(r=><article key={r.id} style={{borderTop:'1px solid #334155',padding:12}}>
      <h3>{r.id}</h3><p>{r.status} {r.account&&`· ${r.account}`}</p>
      <button disabled={busy||frozen} onClick={()=>void begin(r.id)}>Google bilan ulash</button>{' '}
      <button disabled={busy} onClick={()=>void action(r.id,'revoke',{remote:true})}>Ulanishni bekor qilish</button>{' '}
      <button disabled={busy} onClick={()=>void action(r.id,'revoke',{remote:false})}>Faqat lokal favqulodda bloklash</button>{' '}
      <button disabled={busy} onClick={()=>void action(r.id,'retry-revocations')}>Remote revoke qayta tekshirish</button>
    </article>)}
  </section>;
}
