/** Memory-only credentials. No cookies, localStorage or implicit write retries. */

// A caller may add request headers, but never replace the ones this client owns.
// `supervisor/route` is the reason the hook exists: it refuses a request without an
// `Idempotency-Key`, and without a way to send one the route was unreachable from the
// dashboard. The rule below is the smallest one that admits that key: a name is
// alphanumeric-or-hyphen, a value is a short printable string, and the three headers
// that decide identity, body interpretation and destination stay under this client's
// control -- an allowlist of shapes rather than a denylist of names.
const HEADER_NAME = /^[A-Za-z0-9-]{1,64}$/;
const OWNED_HEADERS = new Set(['authorization', 'content-type', 'host', 'cookie', 'origin', 'referer']);

function checkedHeaders(headers) {
  if (headers === undefined) return {};
  if (!headers || typeof headers !== 'object' || Array.isArray(headers)) throw new Error('API sarlavhalari yaroqsiz');
  const out = {};
  for (const [name, value] of Object.entries(headers)) {
    if (!HEADER_NAME.test(name) || OWNED_HEADERS.has(name.toLowerCase()) ||
        typeof value !== 'string' || !value || value.length > 256 || /[\x00-\x1f\x7f]/.test(value)) {
      throw new Error('API sarlavhasi yaroqsiz');
    }
    out[name] = value;
  }
  return out;
}

export class SessionClient {
  constructor(base, fetcher = globalThis.fetch, now = () => Date.now()) {
    const url = new URL(base);
    if (url.username || url.password || url.search || url.hash ||
        (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost','127.0.0.1','[::1]'].includes(url.hostname)))) {
      throw new Error('API HTTPS manzilidan foydalaning');
    }
    this.base = base.replace(/\/$/, ''); this.fetcher=fetcher; this.now=now;
    this.session=null; this.epoch=0; this.pending=null;
  }
  clear() { this.epoch++; this.session=null; this.pending=null; }
  install(tokens, epoch) {
    if (epoch !== this.epoch) throw new Error('Session o‘zgargan');
    if (!tokens || typeof tokens.access_token!=='string' || typeof tokens.refresh_token!=='string' ||
        !Number.isFinite(tokens.expires_in) || tokens.expires_in<=0) throw new Error('Session javobi yaroqsiz');
    this.session={...tokens, expiresAt:this.now()+tokens.expires_in*1000};
  }
  async send(path, body, token, method, headers) {
    if (typeof path!=='string' || !path.startsWith('/') || path.startsWith('//') || /[\x00-\x1f\\]/.test(path)) throw new Error('API yo‘li yaroqsiz');
    method=method || (body===undefined?'GET':'POST');
    if(!['GET','POST','PUT','PATCH','DELETE'].includes(method) || (method==='GET' && body!==undefined))throw new Error('API metodi yaroqsiz');
    const extra=checkedHeaders(headers);
    const response=await this.fetcher(this.base+path, {
      method, credentials:'omit', cache:'no-store', redirect:'error',
      headers:{'Content-Type':'application/json',...extra,...(token?{Authorization:'Bearer '+token}:{})},
      ...(body===undefined?{}:{body:JSON.stringify(body)})
    });
    if (!response.ok) {
      // Do not echo request/provider response that could contain secrets.
      const err=new Error(response.status===401?'Session yaroqsiz. Qayta kiring.':`API xatosi (${response.status})`);
      err.status=response.status; throw err;
    }
    return response.json();
  }
  async login(email,password) {
    this.clear(); const epoch=this.epoch;
    const result=await this.send('/identity/login',{email,password});
    this.install(result.tokens,epoch); return result;
  }
  async register(email,password,display_name,invitation_token) {
    this.clear(); const epoch=this.epoch;
    const result=await this.send('/identity/register',{email,password,display_name,invitation_token});
    this.install(result.tokens,epoch);
    return this.request('/identity/workspaces');
  }
  async access() {
    if (!this.session) throw new Error('Avval tizimga kiring');
    if (this.session.expiresAt > this.now()+30000) return this.session.access_token;
    if (!this.pending) {
      const epoch=this.epoch; const raw=this.session.refresh_token;
      const job=this.send('/identity/refresh',{refresh_token:raw}).then(tokens=>{
        this.install(tokens,epoch); return tokens.access_token;
      }).catch(err=>{if(epoch===this.epoch)this.clear();throw err;});
      this.pending=job;
      job.finally(()=>{if(this.pending===job)this.pending=null;}).catch(()=>{});
    }
    return this.pending;
  }
  async request(path,body,method,headers) {
    const epoch=this.epoch; const token=await this.access();
    if(epoch!==this.epoch)throw new Error('Session o‘zgargan');
    try {
      const result=await this.send(path,body,token,method,headers);
      if(epoch!==this.epoch)throw new Error('Session o‘zgargan');
      return result;
    } catch(err) {
      if(err.status===401 && epoch===this.epoch)this.clear();
      throw err; // Never retry side-effecting POST requests on auth/network failure.
    }
  }
  async select(workspace) {
    const epoch=this.epoch; const old=this.session?.refresh_token;
    const tokens=await this.request('/identity/workspaces/'+encodeURIComponent(workspace)+'/select',{});
    this.install(tokens,epoch);
    if(old)await this.send('/identity/logout',{refresh_token:old});
  }
  async logout() {
    const raw=this.session?.refresh_token; this.clear();
    if(raw)await this.send('/identity/logout',{refresh_token:raw});
  }
}
