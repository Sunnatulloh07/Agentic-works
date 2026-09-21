const identifier=value=>typeof value==='string'&&value.length>=1&&value.length<=128&&!/[^A-Za-z0-9_.-]/.test(value);
export function syncRequest(connection,agent,kind,calendar='') {
  if(!identifier(connection)||!identifier(agent))throw new Error('Ulanish va agent ID kerak');
  if(!['gmail','drive','calendar'].includes(kind))throw new Error('Noto‘g‘ri sync turi');
  if(typeof calendar!=='string'||calendar.length>256||/[\x00-\x1f\x7f]/.test(calendar))throw new Error('Calendar ID yaroqsiz');
  if(kind==='calendar'&&!calendar)throw new Error('Calendar ID kerak');
  if(kind!=='calendar'&&calendar)throw new Error('Bu turda Calendar ID mumkin emas');
  return {agent,kind,calendar};
}
export function googlePath(tenant,connection,action) {
  if(!identifier(tenant)||!identifier(connection)||!['page','records','reset'].includes(action))throw new Error('Noto‘g‘ri endpoint');
  return `/platform/${encodeURIComponent(tenant)}/google/${encodeURIComponent(connection)}/sync/${action}`;
}
