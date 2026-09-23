'use strict';
const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const os=require('node:os');const path=require('node:path');
const {permitted,execute,Journal}=require('./runner');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'platform-runner-'));
const safe=path.join(root,'safe');fs.mkdirSync(safe);fs.writeFileSync(path.join(safe,'hello.txt'),'salom');
const config={folders:[safe],deny_always:['secret']};
test.after(()=>fs.rmSync(root,{recursive:true,force:true}));
test('real file read',()=>assert.deepEqual(execute('fs.read_text',{file:path.join(safe,'hello.txt')},config),{text:'salom'}));
test('real directory listing',()=>assert.ok(execute('fs.list',{dir:safe},config).entries.some(e=>e.name==='hello.txt')));
test('outside allowlist denied',()=>assert.throws(()=>permitted(root,config)));
test('relative path denied',()=>assert.throws(()=>permitted('hello.txt',config)));
test('prefix lookalike denied',()=>{const other=safe+'2';fs.mkdirSync(other);assert.throws(()=>permitted(other,config));});
test('symlink escape denied',()=>{const out=path.join(root,'outside.txt');fs.writeFileSync(out,'private');const link=path.join(safe,'link.txt');fs.symlinkSync(out,link);assert.throws(()=>permitted(link,config));});
test('secret files denied',()=>{fs.writeFileSync(path.join(safe,'.env'),'x');assert.throws(()=>permitted(path.join(safe,'.env'),config));});
test('disabled physical tool denied',()=>assert.throws(()=>execute('print.spool',{},config)));
test('arbitrary shell denied',()=>assert.throws(()=>execute('shell.exec',{cmd:'echo x'},config)));
test('large text denied',()=>{const file=path.join(safe,'large.txt');fs.writeFileSync(file,'a'.repeat(16001));assert.throws(()=>execute('fs.read_text',{file},config));});
test('binary file denied',()=>{const file=path.join(safe,'binary.bin');fs.writeFileSync(file,Buffer.from([0,1]));assert.throws(()=>execute('fs.read_text',{file},config));});
test('durable journal result survives reload',()=>{const dir=path.join(root,'journal');const id='a'.repeat(32);new Journal(dir).write(id,{status:'done',ok:true});assert.deepEqual(new Journal(dir).read(id),{status:'done',ok:true});});
test('journal traversal denied',()=>assert.throws(()=>new Journal(path.join(root,'journal')).read('../bad')));
test('listing omits denied filenames',()=>{
  fs.writeFileSync(path.join(safe,'password.txt'),'private');
  const names=execute('fs.list',{dir:safe},config).entries.map(e=>e.name);
  assert.ok(!names.includes('.env'));assert.ok(!names.includes('password.txt'));
});
test('listing omits symlink escape',()=>{
  const names=execute('fs.list',{dir:safe},config).entries.map(e=>e.name);
  assert.ok(!names.includes('link.txt'));
});
test('hard-linked files denied',()=>{
  const file=path.join(safe,'linked.txt');fs.linkSync(path.join(safe,'hello.txt'),file);
  try {assert.throws(()=>execute('fs.read_text',{file},config));}
  finally {fs.unlinkSync(file);}
});
test('invalid UTF-8 is rejected rather than silently changed',()=>{
  const file=path.join(safe,'invalid.txt');fs.writeFileSync(file,Buffer.from([0xc3,0x28]));
  assert.throws(()=>execute('fs.read_text',{file},config));
});
test('listing is bounded and indicates truncation',()=>{
  const dir=path.join(safe,'many');fs.mkdirSync(dir);
  for(let i=0;i<205;i++)fs.writeFileSync(path.join(dir,`file-${i}.txt`),'x');
  const out=execute('fs.list',{dir},config);assert.equal(out.entries.length,200);assert.equal(out.truncated,true);
});
test('journal symlink directory denied',()=>{
  const real=path.join(root,'real-journal');fs.mkdirSync(real,{mode:0o700});
  const link=path.join(root,'link-journal');fs.symlinkSync(real,link);assert.throws(()=>new Journal(link));
});
test('journal unsafe directory permissions denied',()=>{
  const dir=path.join(root,'public-journal');fs.mkdirSync(dir,{mode:0o755});fs.chmodSync(dir,0o755);
  assert.throws(()=>new Journal(dir));
});
test('journal symlink record denied',()=>{
  const j=new Journal(path.join(root,'symlink-record'));const id='c'.repeat(32);
  fs.symlinkSync(path.join(safe,'hello.txt'),j.file(id));assert.throws(()=>j.read(id));
});
test('journal oversized write refused',()=>{
  const j=new Journal(path.join(root,'oversized-journal'));assert.throws(()=>j.write('d'.repeat(32),{text:'x'.repeat(65536)}));
});
test('private token file loaded without returning source contents elsewhere',()=>{
  const {loadToken}=require('./runner');const file=path.join(root,'device.token');
  fs.writeFileSync(file,'offline-token-value\n',{mode:0o600});assert.equal(loadToken({RUNNER_TOKEN_FILE:file}),'offline-token-value');
  assert.throws(()=>loadToken({RUNNER_TOKEN_FILE:file,RUNNER_TOKEN:'other'}));
  fs.chmodSync(file,0o644);assert.throws(()=>loadToken({RUNNER_TOKEN_FILE:file}));
});
test('strict task validator accepts only supported envelope',()=>{
  const {validateTask}=require('./runner');const now=1000000;
  const good={id:'a'.repeat(32),claim:'b'.repeat(32),lease:1090,tool:'fs.read_text',params:{file:'/allowed/file'}};
  assert.equal(validateTask(good,now),good);
  for(const change of [{lease:null},{lease:NaN},{lease:'1090'},{lease:999},{lease:2000},{claim:''},{tool:'shell.exec'},{params:[]},{params:{file:'/x',extra:1}},{extra:'override'}])assert.throws(()=>validateTask({...good,...change},now));
});
// 4403 is overloaded on the server (platform_api.runner: every exception closes with
// it -- expired JWT, revoked/rotated device, receive timeout, stale claim, DB error),
// so the runner must not treat one 4403 as "revoked forever".
const {closeAction,tokenExpiry,MAX_AUTH_REJECTIONS}=require('./runner');
const jwt=claims=>'h.'+Buffer.from(JSON.stringify(claims)).toString('base64url')+'.s';
const NOW=1_800_000_000_000;
test('4403 after an authenticated session is transient: reconnect, counter reset',()=>{
  const out=closeAction({code:4403,authenticated:true,expiresAt:NOW+60000,now:NOW,tokenFromFile:false,rejections:2});
  assert.equal(out.action,'retry');assert.equal(out.rejections,0);
});
test('4403 before any server message with a live token is retried, then exits as revoked',()=>{
  let state=0,out;
  for(let i=1;i<MAX_AUTH_REJECTIONS;i++){
    out=closeAction({code:4403,authenticated:false,expiresAt:NOW+60000,now:NOW,tokenFromFile:false,rejections:state});
    assert.equal(out.action,'retry');state=out.rejections;assert.equal(state,i);
  }
  out=closeAction({code:4403,authenticated:false,expiresAt:NOW+60000,now:NOW,tokenFromFile:false,rejections:state});
  assert.equal(out.action,'exit');assert.match(out.message,/revoked or rotated/);assert.match(out.message,/re-enroll/);
});
test('an expired token from a file waits for rotation instead of exiting',()=>{
  const out=closeAction({code:4403,authenticated:false,expiresAt:NOW-1,now:NOW,tokenFromFile:true,rejections:0});
  assert.equal(out.action,'retry');assert.match(out.message,/expired/);assert.match(out.message,/RUNNER_TOKEN_FILE/);
  assert.equal(out.rejections,0);
});
test('an expired token from the environment exits with a clear message',()=>{
  const out=closeAction({code:4403,authenticated:false,expiresAt:NOW-1,now:NOW,tokenFromFile:false,rejections:0});
  assert.equal(out.action,'exit');assert.match(out.message,/expired/);
});
test('other close codes keep the ordinary backoff and the rejection count',()=>{
  const out=closeAction({code:1006,authenticated:false,expiresAt:null,now:NOW,tokenFromFile:false,rejections:1});
  assert.deepEqual({action:out.action,rejections:out.rejections},{action:'retry',rejections:1});
});
test('close messages never carry the token',()=>{
  const token=jwt({exp:NOW/1000-5,sub:'device-secret-value'});
  for(const fromFile of [true,false])for(const expiresAt of [tokenExpiry(token),NOW+1]){
    const out=closeAction({code:4403,authenticated:false,expiresAt,now:NOW,tokenFromFile:fromFile,rejections:MAX_AUTH_REJECTIONS});
    assert.ok(!String(out.message).includes(token));assert.ok(!String(out.message).includes('device-secret-value'));
  }
});
test('token expiry is read for the reconnect decision only, and fails soft',()=>{
  assert.equal(tokenExpiry(jwt({exp:1700000000})),1700000000*1000);
  for(const bad of ['', 'no-dots', 'a.!!!.b', jwt({exp:'soon'}), jwt({}), 'a.'+'x'.repeat(9000)+'.b'])assert.equal(tokenExpiry(bad),null);
});
