import test from 'node:test';
import assert from 'node:assert/strict';
import {navFor,mobileNav,defaultTab,conversationKey,conversationFlags,attentionCount,documentTitle} from './nav.mjs';

const ids=(items)=>items.map(i=>i.id);

test('shop roles land on conversations; developer tools are under advanced',()=>{
  for(const role of ['owner','operator']){
    const nav=navFor(role);
    assert.deepEqual(ids(nav.primary),['conversations','approvals','orders','catalog','settings']);
    assert.equal(defaultTab(role),'conversations');
    assert.ok(ids(nav.advanced).includes('tasks'));
    assert.ok(ids(nav.advanced).includes('audit'));
    assert.ok(!ids(nav.advanced).includes('conversations'));
  }
  assert.ok(ids(navFor('owner').advanced).includes('oauth'));
  assert.ok(!ids(navFor('operator').advanced).includes('oauth'));
});

test('other roles never see customer text tabs',()=>{
  for(const role of ['viewer','integrator','']){
    const all=[...ids(navFor(role).primary),...ids(navFor(role).advanced)];
    for(const hidden of ['conversations','approvals','inbox','audit','handoffs'])assert.ok(!all.includes(hidden),role+':'+hidden);
    assert.equal(defaultTab(role),'orders');
  }
  assert.ok(ids(navFor('integrator').advanced).includes('connections'));
  assert.ok(!ids(navFor('viewer').advanced).includes('connections'));
});

test('conversation key is channel plus id',()=>{
  assert.equal(conversationKey({channel:'telegram',conversation_id:'-1'}),'telegram:-1');
});

const conv=(over={})=>({channel:'telegram',conversation_id:'-1',last_role:'customer',last_at:100,turn_status:'delivered',takeover:null,...over});

test('a new customer line since the operator looked is unread',()=>{
  assert.equal(conversationFlags(conv(),[],{},50,200).unread,true);
  assert.equal(conversationFlags(conv(),[],{'telegram:-1':150},50,200).unread,false);
  assert.equal(conversationFlags(conv(),[],{},150,200).unread,false);       // older than the session
  assert.equal(conversationFlags(conv({last_role:'agent'}),[],{},50,200).unread,false);
});

test('a chat needs a human when the bot cannot answer the last customer line',()=>{
  assert.equal(conversationFlags(conv({turn_status:'failed'}),[],{},500,200).needsHuman,true);
  assert.equal(conversationFlags(conv({turn_status:'uncertain'}),[],{},500,200).needsHuman,true);
  const t=conversationFlags(conv({turn_status:'throttled'}),[],{},500,200);
  assert.equal(t.needsHuman,true);assert.equal(t.reason,'throttled');
  assert.equal(conversationFlags(conv({turn_status:'throttled',last_role:'agent'}),[],{},500,200).needsHuman,false);
  for(const reason of ['operator_takeover','order_pending']){
    const g=conversationFlags(conv(),[{channel:'telegram',conversation_id:'-1',created:101,reason}],{},500,200);
    assert.equal(g.needsHuman,true);assert.equal(g.reason,reason);
  }
  assert.equal(conversationFlags(conv({takeover:{actor:'ali',until:300}}),[],{},500,200).needsHuman,true);
  assert.equal(conversationFlags(conv({takeover:{actor:'ali',until:150}}),[],{},500,200).needsHuman,false);
  const h=[{channel:'telegram',conversation_id:'-1',created:101,reason:'ungrounded_number'}];
  const f=conversationFlags(conv(),h,{},500,200);
  assert.equal(f.needsHuman,true);assert.equal(f.reason,'ungrounded_number');
  // Answered after the handoff: no longer waiting on a person.
  assert.equal(conversationFlags(conv({last_role:'operator',last_at:120}),h,{},500,200).needsHuman,false);
  // A handoff from before the latest customer line belongs to an older question.
  assert.equal(conversationFlags(conv({last_at:130}),h,{},500,200).needsHuman,false);
});

test('a resolved handoff no longer needs a human; a newer open one does again',()=>{
  const old={channel:'telegram',conversation_id:'-1',created:101,reason:'empty_reply',resolved:true};
  assert.equal(conversationFlags(conv(),[old],{},500,200).needsHuman,false);
  const fresh={channel:'telegram',conversation_id:'-1',created:150,reason:'send_uncertain',resolved:false};
  const f=conversationFlags(conv({last_at:140}),[old,fresh],{},500,200);
  assert.equal(f.needsHuman,true);assert.equal(f.reason,'send_uncertain');
  assert.equal(attentionCount([conv({last_at:100,last_role:'customer'})],[{...old,created:101}],{},500,200,null),0);
});

test('attention counts each chat once and skips the open one',()=>{
  const list=[conv(),conv({conversation_id:'-2',turn_status:'failed'}),conv({conversation_id:'-3',last_role:'agent'})];
  assert.equal(attentionCount(list,[],{},50,200,null),2);
  assert.equal(attentionCount(list,[],{},50,200,'telegram:-1'),1);
  assert.equal(attentionCount(null,null,{},50,200,null),0);
});

test('document title carries the count',()=>{
  assert.equal(documentTitle('Bolajon',0),'Bolajon — Agent Platform');
  assert.equal(documentTitle('Bolajon',3),'(3) Bolajon — Agent Platform');
  assert.equal(documentTitle('',120),'(99+) Agent Platform');
});

test('mobile bottom bar has at most five slots and keeps settings under more',()=>{
  for(const role of ['owner','operator']){
    const m=mobileNav(role);
    assert.deepEqual(ids(m.bottom),['conversations','approvals','orders','catalog']);
    assert.ok(m.bottom.length+1<=5);
    assert.deepEqual(ids(m.more.main),['settings']);
    assert.deepEqual(ids(m.more.advanced),ids(navFor(role).advanced));
  }
  const viewer=mobileNav('viewer');
  assert.deepEqual(ids(viewer.bottom),['orders','catalog']);
  assert.deepEqual(ids(viewer.more.main),['settings']);
  // Every reachable section is in exactly one place.
  for(const role of ['owner','operator','viewer','integrator','']){
    const m=mobileNav(role);const n=navFor(role);
    const all=[...ids(m.bottom),...ids(m.more.main),...ids(m.more.advanced)].sort();
    assert.deepEqual(all,[...ids(n.primary),...ids(n.advanced)].sort(),role);
  }
});
