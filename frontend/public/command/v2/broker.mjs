import {FEEDS} from './model.mjs';
/** A single allowlisted read broker. No order/credential write API exists here. */
export class ReadBroker {
 constructor({fetcher=globalThis.fetch,clock=()=>Date.now(),onChange=()=>{},onAuth=()=>{},timeout=20000}={}){
  this.fetcher=fetcher;this.clock=clock;this.onChange=onChange;this.onAuth=onAuth;this.timeout=timeout;
  this.state={};this.pending=new Map();this.aborters=new Map();this.locked=false;this.generation=0;
 }
 async read(key){
  const f=FEEDS[key];if(!f)throw Error('Endpoint not allowlisted');
  if(this.locked)return null;if(this.pending.has(key))return this.pending.get(key);
  const last=this.state[key];if(last?.nextAt && this.clock()<last.nextAt)return last;
  const gen=this.generation,ctl=new AbortController(),started=this.clock();this.aborters.set(key,ctl);
  const work=(async()=>{
   const timer=setTimeout(()=>ctl.abort(),this.timeout);
   try{
    const r=await this.fetcher(f.url,{method:'GET',credentials:'same-origin',cache:'no-store',signal:ctl.signal,headers:{Accept:'application/json',...(last?.etag?{'If-None-Match':last.etag}:{})}});
    if(r.status===401||r.status===403){this.signOut();return null;}
    if(r.status===304 && last?.data){if(gen!==this.generation)return null;this.state[key]={...last,state:'OK',error:null,received:this.clock(),nextAt:started+f.every};return this.state[key];}
    if(!r.ok)throw Error(`HTTP ${r.status}`);
    const body=await r.text();const data=JSON.parse(body);
    if(!data || typeof data!=='object' || Array.isArray(data))throw Error('Invalid response object');
    if(gen!==this.generation)return null;
    this.state[key]={state:'OK',data,received:this.clock(),started,nextAt:started+f.every,etag:r.headers.get('ETag'),failures:0,error:null};
   }catch(e){
    if(gen!==this.generation || this.locked)return null;
    const failures=(last?.failures||0)+1;
    // Retain last known values only with explicit STALE/error labels; never advance their as-of.
    this.state[key]={...last,state:'ERROR',error:e.name==='AbortError'?'Read timed out':e.message,started,nextAt:this.clock()+Math.min(300000,f.every*2**Math.min(failures,4)),failures,data:last?.data||null};
   }finally{clearTimeout(timer);this.aborters.delete(key);}
   return this.state[key];
  })();
  this.pending.set(key,work);
  try{return await work;}finally{this.pending.delete(key);this.onChange(this.state);}
 }
 signOut(){this.locked=true;this.generation++;for(const c of this.aborters.values())c.abort();this.state={};this.onChange(this.state);this.onAuth();}
 async refresh(){if(this.locked)return;const keys=Object.keys(FEEDS);for(let i=0;i<keys.length;i+=3)await Promise.all(keys.slice(i,i+3).map(k=>this.read(k)));}
 resumeNetwork(){for(const r of Object.values(this.state))if(r.state==='ERROR' && this.clock()-(r.started||0)>15000)r.nextAt=0;return this.refresh();}
 stop(){this.generation++;for(const c of this.aborters.values())c.abort();}
}
