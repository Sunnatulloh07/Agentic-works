import test from 'node:test';
import assert from 'node:assert/strict';
import {submitPath,toolChoices,argumentFields,buildArguments,toolCallBody} from './tools-client.mjs';

const SCHEMA={type:'object',additionalProperties:false,required:['query'],
  properties:{
    query:{type:'string',minLength:1,maxLength:200},
    limit:{type:'integer',minimum:1,maximum:50},
    kinds:{type:'array',items:{type:'string'},maxItems:5},
    exact:{type:'boolean'},
    window:{type:'object',properties:{from:{type:'string'}},required:['from'],additionalProperties:false},
  }};

test('submit path is pinned and tenant-checked',()=>{
  assert.equal(submitPath('demo-retail'),'/platform/demo-retail/tasks');
  for(const bad of ['../t','a/b',undefined,'','x'.repeat(65)])assert.throws(()=>submitPath(bad));
});

test('tool choices are the agent policy, not the whole catalogue',()=>{
  const tools=[{name:'a.one',risk:'read',schema:{}},{name:'b.two',risk:'write',schema:{}}];
  assert.deepEqual(toolChoices(tools,{id:'x',tools:['b.two']}).map(t=>t.name),['b.two']);
  assert.deepEqual(toolChoices(tools,{id:'x',tools:['ghost']}),[]);
  assert.deepEqual(toolChoices(tools,undefined),[]);
  assert.deepEqual(toolChoices(null,{id:'x',tools:['a.one']}),[]);
});

test('schema becomes typed fields with their bounds as hints',()=>{
  const fields=argumentFields(SCHEMA);
  assert.deepEqual(fields.map(f=>[f.name,f.kind,f.required]),
    [['query','text',true],['limit','integer',false],['kinds','list',false],
     ['exact','boolean',false],['window','json',false]]);
  assert.equal(fields[0].maxLength,200);
  assert.equal(fields[1].min,1);assert.equal(fields[1].max,50);
  assert.equal(fields[1].hint,'1…50');
  assert.equal(fields[0].hint,'≤200 belgi');
});

test('an enum becomes a choice and an unknown shape falls back to JSON',()=>{
  const fields=argumentFields({type:'object',properties:{
    mode:{type:'string',enum:['fast','safe']},odd:{type:'number'}}});
  assert.deepEqual(fields[0],{name:'mode',required:false,hint:'fast / safe',kind:'choice',choices:['fast','safe']});
  assert.equal(fields[1].kind,'json');
  assert.deepEqual(argumentFields(undefined),[]);
});

test('required fields refuse and empty optionals are omitted',()=>{
  assert.throws(()=>buildArguments(argumentFields(SCHEMA),{}));
  assert.throws(()=>buildArguments(argumentFields(SCHEMA),{query:'   '}));
  assert.deepEqual(buildArguments(argumentFields(SCHEMA),{query:'salom'}),{query:'salom'});
});

test('integers respect the declared bounds and refuse typos',()=>{
  const fields=argumentFields(SCHEMA);
  assert.deepEqual(buildArguments(fields,{query:'q',limit:'7'}),{query:'q',limit:7});
  for(const bad of ['0','51','-1','1.5','1e3','abc']){
    assert.throws(()=>buildArguments(fields,{query:'q',limit:bad}),undefined,bad);
  }
});

test('strings, booleans, lists and objects convert',()=>{
  const fields=argumentFields(SCHEMA);
  const args=buildArguments(fields,{query:'q',exact:'ha',kinds:'a\n b \n\nc',
    window:'{"from":"2026-01-01"}'});
  assert.equal(args.exact,true);
  assert.deepEqual(args.kinds,['a','b','c']);
  assert.deepEqual(args.window,{from:'2026-01-01'});
  assert.throws(()=>buildArguments(fields,{query:'q',exact:'maybe'}));
  assert.throws(()=>buildArguments(fields,{query:'q',window:'{oops'}));
  assert.throws(()=>buildArguments(fields,{query:'x'.repeat(201)}));
});

test('the task body is exactly one step',()=>{
  assert.deepEqual(toolCallBody({agent:'ops.assistant',tool:'records.list',args:{},key:'k1'}),
    {agent:'ops.assistant',key:'k1',steps:[{tool:'records.list',args:{}}]});
  assert.throws(()=>toolCallBody({agent:'../x',tool:'a.b',args:{},key:'k'}));
  assert.throws(()=>toolCallBody({agent:'a',tool:'../x',args:{},key:'k'}));
  assert.throws(()=>toolCallBody({agent:'a',tool:'b',args:[],key:'k'}));
  assert.throws(()=>toolCallBody({agent:'a',tool:'b',args:{},key:'  '}));
  assert.throws(()=>toolCallBody({agent:'a',tool:'b',args:{},key:'x'.repeat(257)}));
});
