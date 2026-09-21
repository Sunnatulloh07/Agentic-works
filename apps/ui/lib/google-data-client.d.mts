export type SyncKind='gmail'|'drive'|'calendar';
export function syncRequest(connection:string,agent:string,kind:SyncKind,calendar?:string):{agent:string;kind:SyncKind;calendar:string};
export function googlePath(tenant:string,connection:string,action:'page'|'records'|'reset'):string;
