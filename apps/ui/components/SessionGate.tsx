'use client';
import {useState, type ReactNode, type FormEvent} from 'react';
import {SessionClient,type Workspace} from '../lib/session-client.mjs';
import {BootstrapPanel} from './AdminPanel';
const API=process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export default function SessionGate({children}:{children:(client:SessionClient,workspace:Workspace,exit:()=>void)=>ReactNode}){
  const [client]=useState(()=>new SessionClient(API));
  const [workspaces,setWorkspaces]=useState<Workspace[]|null>(null);
  const [selected,setSelected]=useState<Workspace|null>(null);
  const [email,setEmail]=useState('');const [password,setPassword]=useState('');
  const [name,setName]=useState('');const [invite,setInvite]=useState('');
  const [register,setRegister]=useState(false);const [bootstrap,setBootstrap]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  async function authenticate(event:FormEvent){
    event.preventDefault();if(busy)return;setBusy(true);setError('');
    try{
      const result=register?await client.register(email,password,name,invite):await client.login(email,password);
      setWorkspaces(result.workspaces);
    }catch(e){setError(e instanceof Error?e.message:'Kirishda xato');}
    finally{setPassword('');setInvite('');setBusy(false);}
  }
  async function choose(workspace:Workspace){
    if(busy)return;setBusy(true);setError('');
    try{await client.select(workspace.id);setSelected(workspace);}
    catch(e){setError(e instanceof Error?e.message:'Workspace ochilmadi');}
    finally{setBusy(false);}
  }
  async function exit(){
    setBusy(true);setError('');setSelected(null);setWorkspaces(null);
    try{await client.logout();}catch{setError('Lokal session o‘chirildi, server logout tasdiqlanmadi. Sessionlarni qayta kirib bekor qiling.');}
    finally{setBusy(false);}
  }
  if(selected)return children(client,selected,()=>{void exit();});
  return <main style={{maxWidth:520,margin:'50px auto',padding:24,fontFamily:'system-ui'}}>
    <h1>Agent Platform</h1><p>O‘z hisobingizdan biznes workspace’ini tanlang.</p>
    {error&&<p role="alert">{error}</p>}
    {workspaces===null?<form onSubmit={authenticate}>
      <label>Email<input required type="email" autoComplete="username" value={email} onChange={e=>setEmail(e.target.value)} style={field}/></label>
      <label>Parol<input required type="password" minLength={register?12:1} maxLength={256} autoComplete={register?'new-password':'current-password'} value={password} onChange={e=>setPassword(e.target.value)} style={field}/></label>
      {register&&<><label>Ism<input required value={name} maxLength={256} onChange={e=>setName(e.target.value)} style={field}/></label>
      <label>Taklif tokeni<input required type="password" autoComplete="off" value={invite} onChange={e=>setInvite(e.target.value)} style={field}/></label></>}
      <button disabled={busy} type="submit">{busy?'Kutilmoqda...':register?'Taklif bilan ro‘yxatdan o‘tish':'Kirish'}</button>
      <button disabled={busy} type="button" onClick={()=>{setRegister(!register);setPassword('');setInvite('');}}>{register?'Mavjud hisob':'Menda taklif bor'}</button>
    </form>:<section><h2>Bizneslaringiz</h2>
      {workspaces.length===0&&<p>Hozircha faol membership yo‘q. Workspace owneridan taklif so‘rang.</p>}
      {workspaces.map(w=><button disabled={busy} key={w.id} onClick={()=>{void choose(w);}} style={field}>{w.name} ({w.role})</button>)}
      <button disabled={busy} onClick={()=>{void exit();}}>Chiqish</button>
    </section>}
    {workspaces===null&&<p><button type="button" onClick={()=>setBootstrap(!bootstrap)}>{bootstrap?'Bootstrap formasini yopish':'Birinchi owner yaratish (bootstrap)'}</button></p>}
    {workspaces===null&&bootstrap&&<BootstrapPanel client={client}/>}
    <p style={{fontSize:12}}>Tokenlar faqat sahifa xotirasida. Sahifa yopilgach qayta kirish kerak. Production qabul mezonlari hali yopilmagan.</p>
  </main>;
}
const field={display:'block',width:'100%',padding:10,margin:'8px 0 16px'};
