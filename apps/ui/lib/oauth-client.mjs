/** Pure browser-boundary validation, no storage, network or credentials. */
const bounded=(x,n)=>typeof x==='string'&&x.length>0&&x.length<=n&&!/[\x00-\x1f\x7f]/.test(x);
export function authorization(result,origin,now=Date.now()){
  if(!result || !Number.isFinite(result.expires_in) || result.expires_in<=0 ||
     !Number.isFinite(now) || !bounded(result.authorization_url,20000))throw new Error('Invalid OAuth authorization');
  const url=new URL(result.authorization_url);const state=url.searchParams.get('state');
  const redirect=new URL(url.searchParams.get('redirect_uri')||'invalid');
  if(url.origin!=='https://accounts.google.com' || url.pathname!=='/o/oauth2/v2/auth' || url.username || url.password || url.hash ||
     url.searchParams.getAll('state').length!==1 || url.searchParams.getAll('redirect_uri').length!==1 ||
     !bounded(state,256) || redirect.origin!==origin || redirect.pathname!=='/oauth/google/callback' ||
     redirect.username || redirect.password || redirect.search || redirect.hash)throw new Error('Invalid OAuth binding');
  return {url:url.href,state,expires:now+Math.min(result.expires_in,600)*1000};
}
export function callback(search){
  const query=new URLSearchParams(search);const state=query.get('state');const code=query.get('code');const denied=query.has('error');
  if(query.getAll('state').length!==1 || !bounded(state,256) ||
     (!denied && (query.getAll('code').length!==1 || !bounded(code,4096))) ||
     (denied && (query.getAll('error').length!==1 || query.has('code'))))throw new Error('Invalid OAuth callback');
  return {type:'agent-platform-google-oauth',state,code:denied?'':code,denied};
}
export function acceptCallback(pending,event,origin,now=Date.now()){
  const data=event.data;
  return Boolean(pending && Number.isFinite(pending.expires) && pending.expires>now && event.origin===origin &&
    event.source===pending.popup && data && data.type==='agent-platform-google-oauth' && data.state===pending.state &&
    typeof data.denied==='boolean' && (data.denied?data.code==='':bounded(data.code,4096)));
}
