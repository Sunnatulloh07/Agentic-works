import test from 'node:test';
import assert from 'node:assert/strict';
import {SessionClient} from './session-client.mjs';
const tokens=(n='1')=>({access_token:'access'+n,refresh_token:'refresh'+n,expires_in:900,workspace_id:null});
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
function fixture(){
  const calls=[];let time=0;let fail=false;let refreshCount=0;
  const fetcher=async(url,options)=>{
    calls.push({url,options});
    if(url.endsWith('/login'))return response({tokens:tokens(),workspaces:[{id:'ws',name:'Work'}]});
    if(url.endsWith('/refresh')){refreshCount++;return response(tokens('2'));}
    if(fail)return response({},401);
    return response({ok:true});
  };
  const client=new SessionClient('https://api.example',fetcher,()=>time);
  return {client,calls,tick:()=>{time=880000;},fail:()=>{fail=true;},count:()=>refreshCount};
}
test('login installs in-memory session, never returns password',async()=>{
  const f=fixture();await f.client.login('u@example','test');await f.client.request('/platform/ws/tasks');
  assert.equal(f.calls[1].options.headers.Authorization,'Bearer access1');assert.equal(f.calls[1].options.credentials,'omit');
  assert.equal(f.calls[1].options.cache,'no-store');assert.equal(f.client.password,undefined);
});
test('parallel requests use single-flight refresh',async()=>{
  const f=fixture();await f.client.login('u','p');f.tick();await Promise.all([f.client.request('/a'),f.client.request('/b')]);
  assert.equal(f.count(),1);assert.equal(f.calls.filter(x=>x.url.endsWith('/a'))[0].options.headers.Authorization,'Bearer access2');
});
test('401 write is never replayed',async()=>{
  const f=fixture();await f.client.login('u','p');f.fail();await assert.rejects(f.client.request('/write',{amount:1}));
  assert.equal(f.calls.filter(x=>x.url.endsWith('/write')).length,1);assert.equal(f.client.session,null);
});
test('logout clears credentials and sends revocation',async()=>{
  const f=fixture();await f.client.login('u','p');await f.client.logout();assert.equal(f.client.session,null);
  assert.ok(f.calls.some(x=>x.url.endsWith('/logout')));await assert.rejects(f.client.request('/a'));
});
test('stale login response cannot restore logout session',async()=>{
  let resolve;const c=new SessionClient('https://api.example',()=>new Promise(r=>{resolve=r;}));
  const login=c.login('u','p');c.clear();resolve(response({tokens:tokens()}));await assert.rejects(login);assert.equal(c.session,null);
});
test('stale refresh response cannot restore session',async()=>{
  let resolve;let time=0;const c=new SessionClient('https://api.example',async(url)=>{
    if(url.endsWith('/login'))return response({tokens:tokens()});
    return new Promise(r=>{resolve=r;});
  },()=>time);
  await c.login('u','p');time=880000;const access=c.access();c.clear();resolve(response(tokens('2')));
  await assert.rejects(access);assert.equal(c.session,null);
});
test('unsafe API origins rejected',()=>{
  for(const base of ['http://example.com','https://user:pass@example.com','https://example.com?q=x','file:///tmp/x'])assert.throws(()=>new SessionClient(base));
});
test('unsafe network-relative path rejected',async()=>{
  const f=fixture();await f.client.login('u','p');await assert.rejects(f.client.request('//evil.example'));
});
test('explicit PUT is supported without implicit POST',async()=>{
  const f=fixture();await f.client.login('u','p');await f.client.request('/budget',{limit:10},'PUT');
  assert.equal(f.calls.at(-1).options.method,'PUT');assert.equal(f.calls.at(-1).options.redirect,'error');
});
test('PUT authorization failure is not replayed',async()=>{
  const f=fixture();await f.client.login('u','p');f.fail();await assert.rejects(f.client.request('/budget',{limit:10},'PUT'));
  assert.equal(f.calls.filter(c=>c.url.endsWith('/budget')).length,1);
});
test('unsupported methods and GET bodies are denied before network',async()=>{
  const f=fixture();await f.client.login('u','p');const before=f.calls.length;
  await assert.rejects(f.client.request('/a',{},'TRACE'));
  await assert.rejects(f.client.request('/a',{},'GET'));assert.equal(f.calls.length,before);
});
test('backslash and control paths are rejected',async()=>{
  const f=fixture();await f.client.login('u','p');const before=f.calls.length;
  for(const path of ['/\\evil','/a\nother','/a\x00'])await assert.rejects(f.client.request(path));
  assert.equal(f.calls.length,before);
});
