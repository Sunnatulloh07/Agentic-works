export type Workspace={id:string;name:string;role:string;status:string};
export class SessionClient {
  constructor(base:string,fetcher?:typeof fetch,now?:()=>number);
  login(email:string,password:string):Promise<{workspaces:Workspace[]}>;
  register(email:string,password:string,name:string,invitation:string):Promise<{workspaces:Workspace[]}>;
  send<T=unknown>(path:string,body?:unknown,token?:string,method?:'GET'|'POST'|'PUT'|'PATCH'|'DELETE',headers?:Record<string,string>):Promise<T>;
  request<T=unknown>(path:string,body?:unknown,method?:'GET'|'POST'|'PUT'|'PATCH'|'DELETE',headers?:Record<string,string>):Promise<T>;
  select(workspace:string):Promise<void>;
  logout():Promise<void>;
  clear():void;
}
