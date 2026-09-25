import test from 'node:test';
import assert from 'node:assert/strict';
import {conversationsPath,handoffsPath,threadPath,replyPath,releasePath,releaseRequest,takeoverActive,
  replyRequest,replyErrorText,threadLines,turnStatusLabel,reasonLabel,orderView} from './conversation-client.mjs';

test('pinned conversation routes',()=>{
  assert.equal(conversationsPath('turkish-baby'),'/platform/turkish-baby/conversations');
  assert.equal(handoffsPath('t'),'/platform/t/handoffs');
  assert.equal(threadPath('t','telegram','-100123'),'/platform/t/conversations/telegram/-100123');
  assert.equal(replyPath('t','telegram','-100123'),'/platform/t/conversations/telegram/-100123/reply');
  assert.equal(threadPath('t','instagram','a b'),'/platform/t/conversations/instagram/a%20b');
});
test('bad tenant, channel or conversation id rejected before the network',()=>{
  for(const args of [['../t','telegram','1'],['t','Tele gram','1'],['t','telegram',''],['t','telegram','a/b'],
    ['t','telegram','..'],['t','telegram','x'.repeat(257)],['t','telegram',undefined]])
    assert.throws(()=>threadPath(...args));
});
test('reply request matches the operator-reply contract',()=>{
  const r=replyRequest('  Salom, 92 bor  ','0f8fad5b-d9cb-469f-a165-70867728950e');
  assert.deepEqual(r,{body:{text:'  Salom, 92 bor  '},method:'POST',headers:{'Idempotency-Key':'0f8fad5b-d9cb-469f-a165-70867728950e'}});
  assert.equal(replyRequest('x'.repeat(4000),'k-12345678').body.text.length,4000);
  for(const text of ['','   ','x'.repeat(4001),undefined])assert.throws(()=>replyRequest(text,'k-12345678'));
  for(const key of ['','a b','x'.repeat(129),undefined])assert.throws(()=>replyRequest('ok',key));
});
test('reply errors say what happened, including a missing endpoint',()=>{
  assert.match(replyErrorText(Object.assign(new Error('API xatosi (404)'),{status:404})),/404/);
  assert.match(replyErrorText(Object.assign(new Error('API xatosi (404)'),{status:404})),/topilmadi/);
  assert.match(replyErrorText(Object.assign(new Error('x'),{status:409})),/409/);
  assert.equal(replyErrorText(new Error('Tarmoq xatosi')),'Tarmoq xatosi');
  assert.equal(replyErrorText('nima'),'Xato');
});
test('thread lines merge history and operator replies in time order',()=>{
  const lines=threadLines({
    history:[{seq:1,role:'customer',text:'Salom',created:10},{seq:2,role:'agent',text:'Nima kerak?',created:12}],
    operator_replies:[{task_id:'t1',text:'Operator javobi',created:11,status:'succeeded',actor:'ali'}],
  });
  assert.deepEqual(lines.map(l=>[l.role,l.label,l.text]),[
    ['customer','Mijoz','Salom'],['operator','Operator','Operator javobi'],['agent','Agent','Nima kerak?']]);
  assert.equal(lines[1].status,'succeeded');
  assert.deepEqual(threadLines(null),[]);
  assert.equal(threadLines({history:[{role:'boshqa',text:'x',created:1}]})[0].label,'boshqa');
});
test('turn statuses and handoff reasons read in Uzbek',()=>{
  assert.equal(turnStatusLabel('delivered'),'yetkazildi');
  assert.equal(turnStatusLabel('delivering'),'yuborilmoqda yoki tasdiq kutmoqda');
  assert.equal(turnStatusLabel('mystery'),'mystery');
  assert.equal(reasonLabel('ungrounded_number'),'javobdagi son tasdiqlanmadi');
  assert.equal(reasonLabel('send_uncertain'),'javob mijozga yetgani noma’lum');
  assert.equal(reasonLabel('new_reason'),'new_reason');
});
test('order record body becomes a readable order',()=>{
  const body=JSON.stringify({product_id:'TB1',product_name:'Futbolka',size:'92',qty:2,unit_price_uzs:99000,
    total_uzs:198000,customer_name:'Dilnoza',phone:'+998901234567',
    delivery:{type:'branch',branch_id:'markaz',branch_name:'Markaz',address:'Toshkent'},
    channel:'telegram',conversation_id:'-123'});
  assert.deepEqual(orderView(body),{product:'Futbolka (TB1)',size:'92',qty:2,total:198000,customer:'Dilnoza',
    phone:'+998901234567',delivery:'Filial: Markaz',channel:'telegram',conversationId:'-123'});
  assert.equal(orderView(JSON.stringify({product_id:'X',qty:1,total_uzs:5,delivery:{type:'address',address:'Uy 5'}})).delivery,'Manzil: Uy 5');
  for(const bad of ['tel +998901112233','{"title":"x"}','[1]','',undefined])assert.equal(orderView(bad),null);
});
test('release hands a chat back to the bot with its own key',()=>{
  assert.equal(releasePath('t','telegram','-1'),'/platform/t/conversations/telegram/-1/release');
  assert.throws(()=>releasePath('t','telegram','a/b'));
  assert.deepEqual(releaseRequest('k-12345678'),{body:{},method:'POST',headers:{'Idempotency-Key':'k-12345678'}});
  assert.throws(()=>releaseRequest('bad key'));
});
test('operator mode is shown only while the takeover is active',()=>{
  assert.equal(takeoverActive({actor:'olga',until:2000},1000),true);
  assert.equal(takeoverActive({actor:'olga',until:1000},1000),false);
  assert.equal(takeoverActive(null,1000),false);
  assert.equal(takeoverActive({until:'x'},1000),false);
  assert.equal(turnStatusLabel('operator'),'operator javob bermoqda');
});
