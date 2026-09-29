import test from 'node:test';
import assert from 'node:assert/strict';
import {createIdempotency,stableSignature} from './idempotency.mjs';

function counter(){let n=0;return ()=>`key-${String(++n).padStart(4,'0')}`;}

test('signature ignores key order but not values',()=>{
  assert.equal(stableSignature({a:1,b:{c:2,d:[1,2]}}),stableSignature({b:{d:[1,2],c:2},a:1}));
  assert.notEqual(stableSignature({a:1}),stableSignature({a:'1'}));
  assert.notEqual(stableSignature({a:[1,2]}),stableSignature({a:[2,1]}));
});

test('a retry of the same payload reuses its key',()=>{
  const keys=createIdempotency(counter());
  const first=keys.key('reply',{chat:'-1',text:'Salom'});
  assert.equal(keys.key('reply',{text:'Salom',chat:'-1'}),first);
});

test('a different payload or action gets a different key',()=>{
  const keys=createIdempotency(counter());
  const a=keys.key('reply',{chat:'-1',text:'Salom'});
  assert.notEqual(keys.key('reply',{chat:'-1',text:'Salom!'}),a);
  assert.notEqual(keys.key('reply',{chat:'-2',text:'Salom'}),a);
  assert.notEqual(keys.key('release',{chat:'-1',text:'Salom'}),a);
});

test('success settles the key, so a deliberate repeat is a new request',()=>{
  const keys=createIdempotency(counter());
  const a=keys.key('task',{tool:'x'});
  keys.settle('task',{tool:'x'});
  assert.notEqual(keys.key('task',{tool:'x'}),a);
});

test('pending keys are bounded, oldest dropped first',()=>{
  const keys=createIdempotency(counter(),2);
  const a=keys.key('a',1);keys.key('b',1);keys.key('c',1);
  assert.equal(keys.size,2);
  assert.notEqual(keys.key('a',1),a);
});

test('default keys are UUIDs the API accepts',()=>{
  const key=createIdempotency().key('x',{});
  assert.match(key,/^[A-Za-z0-9-]{8,128}$/);
});
