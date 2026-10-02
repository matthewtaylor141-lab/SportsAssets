/* Resumable actual-agent narration; no invented speech or automatic retries. */
export function createChapterPreparer({fetchFn=fetch,timeoutMs=90000,uuid=()=>crypto.randomUUID(),createUrl=b=>URL.createObjectURL(b)}={}){
 const checkpoints=new Map();
 async function request(path,body,audio=false){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeoutMs);
  try{
   const r=await fetchFn(path,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
   if(!r.ok){const j=await r.json().catch(()=>({}));throw Error(r.status===401||r.status===403?'Sign in to Command first.':String(j.reason||j.detail||'Agent service returned '+r.status));}
   if(!audio)return await r.json();
   const blob=await r.blob();
   if(!blob.type.startsWith('audio/')||!blob.size)throw Error('Speech service returned no audio.');
   return blob;
  }catch(e){if(e.name==='AbortError')throw Error('Agent request timed out. Click Prepare to resume the same request.');throw e;}
  finally{clearTimeout(timer);}
 }
 return async function prepareChapter(chapter,scene){
  if(!['derek','xavier','audrey'].includes(scene.agent))throw Error('Unknown agent');
  const key=JSON.stringify([chapter,scene.agent,scene.q,scene.sub,scene.a]);
  let cp=checkpoints.get(key);
  if(!cp){cp={request_id:'team-demo-'+uuid()};checkpoints.set(key,cp);}
  if(cp.ready)return cp.ready;
  if(!cp.reply){
   const j=await request('/api/command/agents/'+scene.agent+'/persona/chat',{
    message:'For a clearly labelled management product-demo REHEARSAL, answer this question in your own persona in at most 65 words. Do not claim this illustrative scenario happened in production, do not invent results, and do not change any policy or place any trade. Context: '+scene.sub+' Illustrative explanation: '+scene.a+' Management question: '+scene.q,
    request_id:cp.request_id,allow_records_only:false});
   if(j.status==='PENDING')throw Error('Agent is still preparing this response. Click Prepare to resume the same request.');
   if(!j.answer||!j.message_id)throw Error('Agent returned no complete answer or stored message ID.');
   if(j.provider?.mode!=='LLM'){
    // A completed fallback is a terminal response. Only a deliberate next
    // click may create a fresh question; never synthesize it as an LLM answer.
    checkpoints.delete(key);throw Error('Agent returned fallback mode; no model-voice demo prepared.');
   }
   cp.reply=j;
  }
  const j=cp.reply;
  const blob=await request('/api/command/agents/'+scene.agent+'/speak',{message_id:j.message_id},true);
  cp.ready={answer:j.answer,url:createUrl(blob),message_id:j.message_id,conversation_id:j.conversation_id};
  return cp.ready;
 };
}
