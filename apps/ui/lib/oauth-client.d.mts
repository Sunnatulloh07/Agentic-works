export function authorization(result:{authorization_url:string;expires_in:number},origin:string,now?:number):{url:string;state:string;expires:number};
export function callback(search:string):{type:string;state:string;code:string|null;denied:boolean};
export function acceptCallback(pending:{popup:Window;state:string;expires:number}|null,event:MessageEvent,origin:string,now?:number):boolean;
