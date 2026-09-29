export function stableSignature(value:unknown):string;
export type Idempotency={key(action:string,payload:unknown):string;settle(action:string,payload:unknown):void;readonly size:number};
export function createIdempotency(make?:()=>string,limit?:number):Idempotency;
