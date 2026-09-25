'use strict';
// The Windows-blocked surface of apps/runner/test.js, recorded as a number.
//
// The inventory said "11/24"; measured now (node v24.18.1, this host) test.js holds
// 31 tests, 20 pass, 11 fail, 0 skipped. These eleven are UNVERIFIED on Windows, not
// verified-green, and the split is by cause:
//
//   5  execute() refuses every filesystem call off Linux ("...requires Linux FD
//      verification in this preview"): the descriptor identity check needs /proc.
//   2  creating a symlink needs Developer Mode or an elevated token (EPERM).
//   4  Windows does not model POSIX permission bits, so `(mode & 0o077) === 0` and
//      the journal/token privacy checks refuse every file the tests can create.
//
// The lesson this file is built on is the one runtime_tests/test_platform_baseline.py
// learned the hard way: checking that a precondition is TRUE does not check that the
// precondition is WHY the test failed. "Windows does not model permission bits" is
// true whether or not it killed a given test. So the reason tests below RUN the same
// operation and match the error that actually comes out -- a plausible-but-wrong row
// fails here instead of being discovered in a CI log.
//
// On a POSIX host the same three operations must SUCCEED (the preview path is Linux,
// permission bits are modelled), so the baseline is a Windows measurement rather
// than a general claim about the runner.
const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const os=require('node:os');const path=require('node:path');
const {execute,loadToken}=require('./runner');

// Recorded so the number can be corrected rather than re-derived (see module docstring).
const RECORDED_SIGNATURE={tests:31,pass:20,fail:11,skipped:0};

const REASONS={
  preview:error=>error instanceof Error &&
    /requires Linux FD verification in this preview/.test(error.message),
  symlink_privilege:error=>Boolean(error)&&error.code==='EPERM',
  private_mode:error=>error instanceof Error &&
    /Private (single-owner file|journal directory) required/.test(error.message),
};

const BLOCKED={
  'real file read':'preview',
  'real directory listing':'preview',
  'listing omits denied filenames':'preview',
  'listing omits symlink escape':'preview',
  'listing is bounded and indicates truncation':'preview',
  'symlink escape denied':'symlink_privilege',
  'journal symlink directory denied':'symlink_privilege',
  'durable journal result survives reload':'private_mode',
  'journal symlink record denied':'private_mode',
  'journal oversized write refused':'private_mode',
  'private token file loaded without returning source contents elsewhere':'private_mode',
};

const root=fs.mkdtempSync(path.join(os.tmpdir(),'runner-baseline-'));
const safe=path.join(root,'safe');fs.mkdirSync(safe);
fs.writeFileSync(path.join(safe,'hello.txt'),'salom');
const CONFIG={folders:[safe],deny_always:[]};
test.after(()=>fs.rmSync(root,{recursive:true,force:true}));

function thrown(fn){try{fn();return null;}catch(error){return error;}}

test('the recorded signature is internally consistent',()=>{
  assert.equal(RECORDED_SIGNATURE.tests,
    RECORDED_SIGNATURE.pass+RECORDED_SIGNATURE.fail+RECORDED_SIGNATURE.skipped);
  assert.equal(RECORDED_SIGNATURE.fail,Object.keys(BLOCKED).length);
  for(const reason of Object.values(BLOCKED))assert.ok(reason in REASONS,reason);
});

test('every recorded blocked test still exists in test.js',()=>{
  // A renamed test must force this baseline to be re-measured, not silently drop a row.
  const source=fs.readFileSync(path.join(__dirname,'test.js'),'utf8');
  for(const name of Object.keys(BLOCKED))assert.ok(source.includes(`test('${name}'`),name);
});

test('reason preview is the actual cause: execute() refuses off Linux',()=>{
  const error=thrown(()=>execute('fs.read_text',{file:path.join(safe,'hello.txt')},CONFIG));
  if(process.platform!=='win32'){assert.equal(error,null,'the preview path is Linux');return;}
  assert.ok(REASONS.preview(error),`unexpected error: ${error}`);
});

test('reason symlink_privilege is the actual cause: symlink creation is EPERM',()=>{
  const link=path.join(root,'link.txt');
  const error=thrown(()=>fs.symlinkSync(path.join(safe,'hello.txt'),link));
  if(process.platform!=='win32'){assert.equal(error,null,'POSIX hosts allow symlinks');return;}
  assert.ok(REASONS.symlink_privilege(error),`unexpected error: ${error}`);
});

test('reason private_mode is the actual cause: default-mode files are refused',()=>{
  const file=path.join(root,'device.token');
  fs.writeFileSync(file,'offline-token-value');
  if(process.platform!=='win32'){
    fs.chmodSync(file,0o600);
    assert.equal(loadToken({RUNNER_TOKEN_FILE:file}),'offline-token-value');
    return;
  }
  const error=thrown(()=>loadToken({RUNNER_TOKEN_FILE:file}));
  assert.ok(REASONS.private_mode(error),`unexpected error: ${error}`);
});
