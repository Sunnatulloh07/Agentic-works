'use strict';
// Node >=22.4; no npm dependencies. Real read-only device execution, not simulation.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');

function executeDarwin(tool,args,config) {
  const python=process.env.RUNNER_PYTHON;
  if(!python || !path.isAbsolute(python))throw new Error('Explicit absolute RUNNER_PYTHON path required on macOS');
  const helper=path.join(__dirname,'portable_fs.py');
  const result=execFileSync(python,['-I','-S',helper],{
    input:JSON.stringify({tool,args,config:{folders:config.folders,deny_always:config.deny_always || []}}),
    encoding:'utf8',timeout:3000,maxBuffer:65536,windowsHide:true,
    env:{PATH:'/usr/bin:/bin',LANG:'en_US.UTF-8'}});
  const response=JSON.parse(result);
  if(response.ok!==true || !response.result)throw new Error('Filesystem helper denied operation');
  return response.result;
}

const rootCache=new WeakMap();
function permitted(file, config) {
  if (typeof file !== 'string' || !path.isAbsolute(file)) throw new Error('Absolute path required');
  const real = fs.realpathSync(file);
  const lower = real.toLowerCase();
  const deny = ['.env', '.ssh', '.aws', 'credentials', 'password', 'parol', 'cvv', ...(config.deny_always || [])];
  if (deny.some(word => lower.includes(String(word).toLowerCase()))) throw new Error('Denied sensitive path');
  if(!rootCache.has(config))rootCache.set(config,(config.folders || []).map(root => fs.realpathSync(root)));
  const roots = rootCache.get(config);
  if (!roots.some(root => { const rel = path.relative(root, real); return rel === '' || (!rel.startsWith('..' + path.sep) && rel !== '..' && !path.isAbsolute(rel)); })) throw new Error('Outside allowlist');
  return real;
}

function execute(tool, args, config) {
  if(!args || typeof args!=='object' || Array.isArray(args))throw new Error('Invalid arguments');
  if(process.platform==='darwin')return executeDarwin(tool,args,config);
  if (tool === 'fs.list') {
    const dir = permitted(args.dir, config);
    if(process.platform!=='linux')throw new Error('Secure directory access requires Linux FD verification in this preview');
    const fd=fs.openSync(dir,fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
    try {
      permitted(fs.realpathSync(`/proc/self/fd/${fd}`),config);
      const entries=[];let visited=0,truncated=false;
      const opened=fs.opendirSync(`/proc/self/fd/${fd}`);
      try {
        let item;
        while((item=opened.readSync())!==null){
          if(++visited>1000 || entries.length>=200){truncated=true;break;}
          if(item.isSymbolicLink() || (!item.isDirectory() && !item.isFile()))continue;
          try {
            permitted(path.join(`/proc/self/fd/${fd}`,item.name),config);
            if(item.isFile() && fs.statSync(path.join(`/proc/self/fd/${fd}`,item.name)).nlink!==1)continue;
            entries.push({name:item.name,type:item.isDirectory()?'directory':'file'});
          } catch {continue;}
        }
      } finally {opened.closeSync();}
      permitted(fs.realpathSync(`/proc/self/fd/${fd}`),config);
      return {entries,truncated};
    } finally {fs.closeSync(fd);}
  }
  if (tool === 'fs.read_text') {
    const file = permitted(args.file, config);
    // O_NOFOLLOW prevents replacing the final resolved file with a symlink between check/open.
    if (process.platform !== 'linux') throw new Error('Secure file read requires Linux FD verification in this preview');
    const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    try {
      const opened = fs.realpathSync(`/proc/self/fd/${fd}`);
      permitted(opened, config);
      const stat = fs.fstatSync(fd);
      if (!stat.isFile() || stat.nlink !== 1 || stat.size > 16000) throw new Error('File not small plain text');
      // Recheck path and file identity after open. Parent-directory races remain an OS sandbox concern.
      const again = fs.statSync(permitted(file, config));
      if (stat.dev !== again.dev || stat.ino !== again.ino) throw new Error('Path changed');
      const storage=Buffer.alloc(16001);let count=0;
      while(count<storage.length){const n=fs.readSync(fd,storage,count,storage.length-count,null);if(!n)break;count+=n;}
      const buf=storage.subarray(0,count);
      if (buf.length > 16000) throw new Error('File grew beyond read limit');
      if (buf.includes(0)) throw new Error('Binary file blocked');
      permitted(fs.realpathSync(`/proc/self/fd/${fd}`),config);
      return { text: new TextDecoder('utf-8',{fatal:true}).decode(buf) };
    } finally { fs.closeSync(fd); }
  }
  throw new Error('Tool disabled: no print, app, screen or shell execution');
}

function privateFile(file,maximum=65536) {
  if(typeof file!=='string' || !path.isAbsolute(file))throw new Error('Absolute private file path required');
  const fd=fs.openSync(file,fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const meta=fs.fstatSync(fd);
    if(!meta.isFile() || meta.nlink!==1 || meta.size>maximum || (meta.mode & 0o077)!==0 ||
       (typeof process.getuid==='function' && meta.uid!==process.getuid()))throw new Error('Private single-owner file required');
    if(fs.realpathSync(file)!==path.resolve(file))throw new Error('Private file symlink path denied');
    const data=Buffer.alloc(maximum+1);let size=0;
    while(size<data.length){const n=fs.readSync(fd,data,size,data.length-size,null);if(!n)break;size+=n;}
    if(size>maximum)throw new Error('Private file grew beyond bound');
    return new TextDecoder('utf-8',{fatal:true}).decode(data.subarray(0,size));
  } finally {fs.closeSync(fd);}
}

function loadToken(env=process.env) {
  if(env.RUNNER_TOKEN && env.RUNNER_TOKEN_FILE)throw new Error('Ambiguous token source');
  const token=env.RUNNER_TOKEN_FILE?privateFile(env.RUNNER_TOKEN_FILE,16000).trim():env.RUNNER_TOKEN;
  if(typeof token!=='string' || !token || token.length>16000 || /\s/.test(token))throw new Error('Device token required');
  return token;
}

class Journal {
  constructor(dir) {
    this.dir=path.resolve(dir);fs.mkdirSync(this.dir,{recursive:true,mode:0o700});
    const meta=fs.lstatSync(this.dir);
    if(!meta.isDirectory() || (meta.mode & 0o077)!==0 || fs.realpathSync(this.dir)!==this.dir ||
       (typeof process.getuid==='function' && meta.uid!==process.getuid()))throw new Error('Private journal directory required');
    this.dev=meta.dev;this.ino=meta.ino;
  }
  check() {
    const meta=fs.lstatSync(this.dir);
    if(!meta.isDirectory() || meta.dev!==this.dev || meta.ino!==this.ino || (meta.mode & 0o077)!==0 ||
       fs.realpathSync(this.dir)!==this.dir)throw new Error('Journal directory changed');
  }
  file(id) {if(!/^[a-f0-9]{32}$/.test(id))throw new Error('Invalid task ID');this.check();return path.join(this.dir,id+'.json');}
  read(id) {try{return JSON.parse(privateFile(this.file(id)));}catch(e){if(e.code==='ENOENT')return null;throw e;}}
  write(id,value) {
    const dest=this.file(id),tmp=dest+'.'+crypto.randomUUID()+'.tmp';
    const text=JSON.stringify(value);
    if(Buffer.byteLength(text,'utf8')>65536)throw new Error('Journal record exceeds byte bound');
    const fd=fs.openSync(tmp,'wx',0o600);let renamed=false;
    try {
      this.check();fs.writeFileSync(fd,text);fs.fsyncSync(fd);this.check();
      fs.renameSync(tmp,dest);renamed=true;
      if(process.platform!=='win32'){
        const dirfd=fs.openSync(this.dir,fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
        try{fs.fsyncSync(dirfd);}finally{fs.closeSync(dirfd);}
      }
    } finally {
      fs.closeSync(fd);
      if(!renamed){try{fs.unlinkSync(tmp);}catch{}}
    }
  }
}

function validateTask(task,now=Date.now()) {
  if(!task || typeof task!=='object' || Array.isArray(task) || Object.keys(task).sort().join(',')!=='claim,id,lease,params,tool')throw new Error('Invalid task envelope');
  if(typeof task.id!=='string' || !/^[a-f0-9]{32}$/.test(task.id) || typeof task.claim!=='string' || !/^[a-f0-9]{32}$/.test(task.claim))throw new Error('Invalid task identity');
  if(typeof task.lease!=='number' || !Number.isFinite(task.lease) || task.lease*1000<=now || task.lease*1000>now+300000)throw new Error('Invalid or expired task lease');
  if(!['fs.list','fs.read_text'].includes(task.tool))throw new Error('Disabled tool');
  if(!task.params || typeof task.params!=='object' || Array.isArray(task.params))throw new Error('Invalid task parameters');
  const field=task.tool==='fs.list'?'dir':'file';
  if(Object.keys(task.params).join(',')!==field || typeof task.params[field]!=='string' || task.params[field].length>2000)throw new Error('Invalid filesystem task');
  return task;
}

function main() {
  const url=process.env.RUNNER_SERVER || 'ws://127.0.0.1:8000/platform/runner/ws';
  loadToken(); // Validate the local token source without logging its contents.
  const target=new URL(url);
  if(target.username || target.password || target.search || target.hash)throw new Error('Plain WebSocket endpoint required');
  if(target.protocol!=='wss:' && !(target.protocol==='ws:' && ['localhost','127.0.0.1','[::1]'].includes(target.hostname)))throw new Error('Remote runner requires WSS');
  const config=JSON.parse(fs.readFileSync(process.env.RUNNER_ALLOW || 'allow.json','utf8'));
  if (!Array.isArray(config.folders) || !config.folders.length) throw new Error('Explicit folders required');
  config.folders=config.folders.map(root=>fs.realpathSync(root));
  Object.freeze(config.folders);Object.freeze(config);
  const journal=new Journal(process.env.RUNNER_JOURNAL || './journal');
  const stopFile=process.env.RUNNER_STOP_FILE || './STOP';
  let stopped=false,attempt=0;
  const halt=()=>{stopped=true;process.exit(3);};
  process.on('SIGINT',halt);process.on('SIGTERM',halt);
  setInterval(()=>{if(fs.existsSync(stopFile))halt();},500).unref();
  function connect() {
    if(stopped || fs.existsSync(stopFile))return halt();
    const token=loadToken();
    const ws=new WebSocket(url);let timer;
    const send=value=>{if(ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify(value));};
    ws.addEventListener('open',()=>{
      attempt=0;send({token});send({type:'heartbeat'});
      timer=setInterval(()=>send({type:'heartbeat'}),30000);
    });
    ws.addEventListener('message',event=>{
      if(stopped || fs.existsSync(stopFile))return halt();
      try {
        if(typeof event.data!=='string' || Buffer.byteLength(event.data,'utf8')>100000)throw new Error('Message byte limit');
        const msg=JSON.parse(event.data);
        if(!msg || typeof msg!=='object' || Array.isArray(msg))throw new Error('Invalid server message');
        if(msg.stopped===true)return halt();
        if(msg.tasks!==undefined && (!Array.isArray(msg.tasks) || msg.tasks.length>20))throw new Error('Task batch limit');
        for(const task of msg.tasks || []) {
          if(stopped || fs.existsSync(stopFile))return halt();
          validateTask(task);
          const old=journal.read(task.id);
          if(old) {
            const fingerprint=crypto.createHash('sha256').update(JSON.stringify({tool:task.tool,args:task.params})).digest('hex');
            if(old.fingerprint!==fingerprint)throw new Error('Task payload changed');
            // Completed result may be re-acknowledged, but never re-execute an observed ID.
            send({type:'result',id:task.id,claim:task.claim,uncertain:old.status!=='done',ok:old.status==='done' && old.ok,result:old.result || {error:'uncertain_local_journal'}});
            continue;
          }
          const fingerprint=crypto.createHash('sha256').update(JSON.stringify({tool:task.tool,args:task.params})).digest('hex');
          journal.write(task.id,{status:'started',claim:task.claim,fingerprint});
          let result,ok=true;
          try {result=execute(task.tool,task.params || {},config);}catch{ok=false;result={error:'local_policy_or_execution_error'};}
          journal.write(task.id,{status:'done',ok,result,fingerprint});
          send({type:'result',id:task.id,claim:task.claim,ok,result});
        }
      } catch { ws.close(1008,'invalid task or journal'); }
    });
    ws.addEventListener('close',event=>{
      clearInterval(timer);
      if(event.code===4403){stopped=true;console.error('Device authorization expired or revoked; enroll/rotate token');process.exitCode=4;return;}
      if(!stopped)setTimeout(connect,Math.min(30000,1000*2**Math.min(++attempt,5)));
    });
    ws.addEventListener('error',()=>ws.close());
  }
  connect();
}
module.exports={permitted,execute,Journal,privateFile,loadToken,validateTask};
if(require.main===module){try{main();}catch{console.error('Runner configuration error; check allow.json and device token');process.exitCode=1;}}
