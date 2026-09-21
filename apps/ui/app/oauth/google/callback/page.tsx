'use client';
import {useEffect,useState} from 'react';
import {callback} from '../../../../lib/oauth-client.mjs';

export default function GoogleCallback(){
  const [status,setStatus]=useState('Google javobi tekshirilmoqda...');
  useEffect(()=>{
    const search=window.location.search;
    // Response headers enforce no-referrer/no-store before client JS loads.
    window.history.replaceState(null,'',window.location.pathname);
    try{
      const result=callback(search);
      if(!window.opener)throw new Error();
      window.opener.postMessage(result,window.location.origin);
    }catch{setStatus('Callback yaroqsiz yoki asosiy sahifa yopilgan. Ulanishni qayta boshlang.');return;}
    setStatus('Javob asosiy sahifaga uzatildi. Ushbu oynani yopishingiz mumkin.');
  },[]);
  return <main style={{fontFamily:'system-ui',padding:32}}><h1>Google ulanishi</h1><p role="status">{status}</p></main>;
}
