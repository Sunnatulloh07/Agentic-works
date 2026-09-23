import test from 'node:test';
import assert from 'node:assert/strict';
import {shopPath,approvalPath,canReadInbox,draftSummary,wholeNumber,formatSum,bootstrapBody,startAutoRefresh} from './shop-client.mjs';

test('pinned shop routes',()=>{
  assert.equal(shopPath('demo-retail','approvals'),'/platform/demo-retail/approvals?status=pending');
  assert.equal(shopPath('demo-retail','inbox'),'/platform/demo-retail/inbox/messages');
  assert.equal(shopPath('t','products'),'/platform/t/products');
  assert.equal(shopPath('t','orders'),'/platform/t/orders');
  assert.equal(shopPath('t','channels'),'/platform/t/channels');
});
test('unknown resource or bad tenant rejected',()=>{
  for(const [t,r] of [['t','../send'],['t','toString'],['../t','orders'],[undefined,'orders'],['a/b','orders']])
    assert.throws(()=>shopPath(t,r));
});
test('approval path pins step id',()=>{
  assert.equal(approvalPath('t','abc123'),'/platform/t/steps/abc123/approval');
  assert.throws(()=>approvalPath('t','../x'));
});
test('only owner and operator read the inbox',()=>{
  assert.equal(canReadInbox('owner'),true);assert.equal(canReadInbox('operator'),true);
  assert.equal(canReadInbox('viewer'),false);assert.equal(canReadInbox('integrator'),false);
});
test('send draft shows recipient and text',()=>{
  assert.deepEqual(draftSummary('telegram.send',{conversation_id:'-123',text:'Ha, bor — o‘lcham 5'}),
    {kind:'message',channel:'telegram',recipient:'-123',text:'Ha, bor — o‘lcham 5'});
  assert.equal(draftSummary('whatsapp.send',{contact:'+998901112233',text:'x'}).recipient,'+998901112233');
});
test('non-send draft stays visible as JSON',()=>{
  const d=draftSummary('records.create',{kind:'order',title:'KB001'});
  assert.equal(d.kind,'args');assert.match(d.text,/"title": "KB001"/);
  assert.equal(draftSummary('x.send',null).text,'');
});
test('whole numbers only, no decimals',()=>{
  assert.equal(wholeNumber('10000000',1),10000000);
  assert.equal(wholeNumber(' 4 ',1,100),4);
  for(const v of ['1.5','1e3','-1','','abc','0x10',undefined])assert.throws(()=>wholeNumber(v));
  assert.throws(()=>wholeNumber('0',1));assert.throws(()=>wholeNumber('101',1,100));
});
test('sum formatting',()=>{
  assert.equal(formatSum(350000),'350 000 so‘m');
  assert.equal(formatSum(1234567,'UZS'),'1 234 567 UZS');
  assert.equal(formatSum(NaN),'—');
});
test('bootstrap body requires display name',()=>{
  const ok={email:' a@b.uz ',password:'p',displayName:' Ali ',workspaceId:'demo-retail',workspaceName:'Do‘kon'};
  assert.deepEqual(bootstrapBody(ok),{email:'a@b.uz',password:'p',display_name:'Ali',workspace_id:'demo-retail',workspace_name:'Do‘kon'});
  assert.throws(()=>bootstrapBody({...ok,displayName:'  '}));
  assert.throws(()=>bootstrapBody({...ok,workspaceId:'x'}));
  assert.throws(()=>bootstrapBody({...ok,workspaceId:'../x'}));
});
function fakeEnv(hidden=false){
  const env={handlers:[],cleared:[],document:{hidden},
    setInterval(fn){env.handlers.push(fn);return env.handlers.length;},clearInterval(id){env.cleared.push(id);}};
  return env;
}
test('auto refresh calls while visible and stops',async()=>{
  const env=fakeEnv();let calls=0;
  const stop=startAutoRefresh(()=>{calls++;},10000,env);
  await env.handlers[0]();await env.handlers[0]();
  assert.equal(calls,2);stop();assert.deepEqual(env.cleared,[1]);
});
test('auto refresh skips hidden tab',async()=>{
  const env=fakeEnv(true);let calls=0;
  startAutoRefresh(()=>{calls++;},10000,env);await env.handlers[0]();
  assert.equal(calls,0);
});
test('auto refresh does not overlap and survives errors',async()=>{
  const env=fakeEnv();let calls=0;let release;
  startAutoRefresh(()=>{calls++;return calls===1?new Promise((_,reject)=>{release=reject;}):undefined;},10000,env);
  const first=env.handlers[0]();await env.handlers[0]();assert.equal(calls,1);
  release(new Error('x'));await first;await env.handlers[0]();assert.equal(calls,2);
});
test('auto refresh rejects tiny interval',()=>assert.throws(()=>startAutoRefresh(()=>{},10,fakeEnv())));
