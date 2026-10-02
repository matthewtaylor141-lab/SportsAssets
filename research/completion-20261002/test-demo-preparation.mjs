import fs from 'node:fs';import assert from 'node:assert/strict';
const {createChapterPreparer}=await import('data:text/javascript;base64,'+fs.readFileSync('frontend/public/command/team-demo/preparation.js').toString('base64'));
const scene={agent:'derek',q:'What do you do?',sub:'Rehearsal',a:'Illustration'};
const response={answer:'Recorded answer',message_id:'m1',conversation_id:'c1',provider:{mode:'LLM'}};
const json=j=>({ok:true,json:async()=>j});const sound=()=>({ok:true,blob:async()=>new Blob(['audio'],{type:'audio/mpeg'})});
let count=0;
// Voice failure retries speech only; stored model answer is preserved.
{
 let calls=[],fail=true;const p=createChapterPreparer({uuid:()=>String(++count),createUrl:()=> 'blob:test',fetchFn:async(path,opts)=>{calls.push([path,JSON.parse(opts.body)]);if(path.endsWith('/chat'))return json(response);if(fail){fail=false;throw Error('voice down');}return sound();}});
 await assert.rejects(p(0,scene),/voice down/);assert.equal((await p(0,scene)).message_id,'m1');await p(0,scene);
 assert.equal(calls.filter(c=>c[0].endsWith('/chat')).length,1);assert.equal(calls.length,3);
}
// Pending and ambiguous network failure reuse the same model request ID.
for(const mode of ['pending','network']){
 let bodies=[],first=true;const p=createChapterPreparer({uuid:()=>String(++count),createUrl:()=> 'blob:test',fetchFn:async(path,opts)=>{if(path.endsWith('/speak'))return sound();bodies.push(JSON.parse(opts.body));if(first){first=false;if(mode==='network')throw Error('network');return json({status:'PENDING'});}return json(response);}});
 await assert.rejects(p(0,scene));await p(0,scene);assert.equal(bodies[0].request_id,bodies[1].request_id);
}
// A fallback never reaches TTS and requires a deliberate retry/new model request.
{
 let bodies=[],speech=0;const p=createChapterPreparer({uuid:()=>String(++count),fetchFn:async(path,opts)=>{if(path.endsWith('/speak'))speech++;bodies.push(JSON.parse(opts.body));return json({...response,provider:{mode:'RECORDS_ONLY'}});}});
 await assert.rejects(p(0,scene),/fallback/);await assert.rejects(p(0,scene),/fallback/);assert.equal(speech,0);assert.notEqual(bodies[0].request_id,bodies[1].request_id);
}
// Deadline includes the response body, not just the HTTP headers.
{
 let aborted=false;const p=createChapterPreparer({timeoutMs:10,fetchFn:async(path,{signal})=>({ok:true,json:()=>new Promise((resolve,reject)=>{signal.addEventListener('abort',()=>{aborted=true;reject(Object.assign(Error('abort'),{name:'AbortError'}));});})})});
 await assert.rejects(p(0,scene),/timed out/);assert(aborted);
}
// Empty audio cannot mark a chapter prepared.
{
 const p=createChapterPreparer({fetchFn:async path=>path.endsWith('/chat')?json(response):{ok:true,blob:async()=>new Blob([],{type:'audio/mpeg'})}});await assert.rejects(p(0,scene),/no audio/);
}
console.log('PASS: 6 preparation behaviors; stored-answer retry, pending/network idempotency, fallback refusal, body timeout, empty audio.');
