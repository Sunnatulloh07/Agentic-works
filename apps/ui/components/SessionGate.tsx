'use client';
import {useEffect, useState, type ReactNode, type FormEvent} from 'react';
import {SessionClient,type Workspace} from '../lib/session-client.mjs';
import {friendlyError, roleLabel} from '../lib/format.mjs';
import {BootstrapPanel} from './AdminPanel';
import {Alert} from './ui';
const API=process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

type Render=(client:SessionClient,workspace:Workspace,exit:()=>void,switchWorkspace:(w:Workspace)=>Promise<void>)=>ReactNode;

/** Sign-in and workspace choice. Tokens live only in this page's memory (SessionClient);
 * when the server ends the session the client reports it and the gate comes back here
 * with a message, instead of leaving a dashboard that can no longer load anything. */
export default function SessionGate({children}:{children:Render}){
  const [client]=useState(()=>new SessionClient(API));
  const [workspaces,setWorkspaces]=useState<Workspace[]|null>(null);
  const [selected,setSelected]=useState<Workspace|null>(null);
  const [email,setEmail]=useState('');const [password,setPassword]=useState('');
  const [name,setName]=useState('');const [invite,setInvite]=useState('');
  const [register,setRegister]=useState(false);const [bootstrap,setBootstrap]=useState(false);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
  useEffect(()=>{
    client.onExpired=()=>{
      setSelected(null);setWorkspaces(null);setBusy(false);
      setNotice('Sessiya tugadi. Davom etish uchun qayta kiring.');
    };
    return ()=>{client.onExpired=null;};
  },[client]);
  async function open(workspace:Workspace){
    await client.select(workspace.id);setSelected(workspace);
  }
  async function authenticate(event:FormEvent){
    event.preventDefault();if(busy)return;setBusy(true);setError('');setNotice('');
    try{
      const result=register?await client.register(email,password,name,invite):await client.login(email,password);
      // One shop: open it. Several: let the person choose.
      if(result.workspaces.length===1)await open(result.workspaces[0]);
      else setWorkspaces(result.workspaces);
    }catch(e){
      const status=(e as {status?:number}).status;
      setError(status===401||status===403
        ?(register?'Taklif kodi yaroqsiz yoki muddati o‘tgan.':'Email yoki parol noto‘g‘ri.')
        :friendlyError(e));
    }
    finally{setPassword('');setInvite('');setBusy(false);}
  }
  async function choose(workspace:Workspace){
    if(busy)return;setBusy(true);setError('');
    try{await open(workspace);}
    catch(e){setError(`Do‘kon ochilmadi: ${friendlyError(e)}`);}
    finally{setBusy(false);}
  }
  async function exit(){
    setBusy(true);setError('');setNotice('');setSelected(null);setWorkspaces(null);
    try{await client.logout();}catch{setError('Bu brauzerda chiqildi, lekin server buni tasdiqlamadi. Xavfsizlik uchun qayta kirib, “Barcha qurilmalardan chiqish”ni bosing.');}
    finally{setBusy(false);}
  }
  if(selected)return children(client,selected,()=>{void exit();},open);
  return <main className="login">
    <h1>Agent Platform</h1>
    <p className="muted">Do‘koningiz suhbatlari, buyurtmalari va tasdiqlari bir joyda.</p>
    {notice&&<p role="status" className="notice">{notice}</p>}
    <Alert text={error}/>
    {workspaces===null?<form className="panel" onSubmit={authenticate}>
      <label>Email<input required type="email" name="email" autoComplete="username" spellCheck={false} value={email} onChange={e=>setEmail(e.target.value)}/></label>
      <label>Parol<input required type="password" name="password" minLength={register?12:1} maxLength={256} autoComplete={register?'new-password':'current-password'} value={password} onChange={e=>setPassword(e.target.value)}/></label>
      {register&&<><label>Ismingiz<input required value={name} maxLength={256} onChange={e=>setName(e.target.value)}/></label>
      <label>Taklif kodi<input required type="password" autoComplete="off" value={invite} onChange={e=>setInvite(e.target.value)}/></label></>}
      <button className="btn primary" disabled={busy} type="submit">{busy?'Kutilmoqda…':register?'Ro‘yxatdan o‘tish':'Kirish'}</button>
      <button className="link" disabled={busy} type="button" onClick={()=>{setRegister(!register);setPassword('');setInvite('');setError('');}}>
        {register?'Hisobim bor — kirish':'Menda xodim taklifi bor'}</button>
    </form>:<section className="panel"><h2>Qaysi do‘konni ochamiz?</h2>
      {workspaces.length===0&&<p>Sizda hali do‘kon yo‘q. Do‘kon egasidan taklif so‘rang.</p>}
      {workspaces.map(w=><button className="btn workspace-btn" disabled={busy} key={w.id} onClick={()=>{void choose(w);}}>
        <strong>{w.name}</strong> · {roleLabel(w.role)}</button>)}
      <button className="link" disabled={busy} onClick={()=>{void exit();}}>Boshqa hisob bilan kirish</button>
    </section>}
    <p className="muted">Xavfsizlik uchun kirish ma’lumoti faqat shu sahifada saqlanadi: sahifani yopsangiz, qayta kirasiz.</p>
    {workspaces===null&&<p><button type="button" className="link" onClick={()=>setBootstrap(!bootstrap)}>
      {bootstrap?'Birinchi sozlashni yopish':'Birinchi sozlash (administrator)'}</button></p>}
    {workspaces===null&&bootstrap&&<BootstrapPanel client={client}/>}
  </main>;
}
