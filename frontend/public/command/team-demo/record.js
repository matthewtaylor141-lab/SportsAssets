/* Local-only export: actual prepared audio + the same rigged character models.
 * No upload, account mutation or trading call. */
export async function recordPresentation({scenes, prepared, avatars, status, show, leadSeconds=3, tailSeconds=2}) {
  if(prepared.size!==scenes.length)throw Error('Prepare all agent voices before exporting a voiced video.');
  if(Object.keys(avatars).length!==3)throw Error('All three character models must load before exporting.');
  if([...prepared.values()].some(v=>v.answer.split(/\s+/).length>110))throw Error('A prepared answer is too long for the video captions. Request a shorter answer before export.');
  if(!window.MediaRecorder)throw Error('This browser cannot export video. Use current Chrome or Edge.');
  const AC=window.AudioContext||window.webkitAudioContext,ac=new AC();await ac.resume();
  const canvas=document.createElement('canvas');canvas.width=1280;canvas.height=720;
  const g=canvas.getContext('2d'),dest=ac.createMediaStreamDestination(),an=ac.createAnalyser();an.fftSize=1024;an.connect(dest);an.connect(ac.destination);
  const visual=canvas.captureStream(30),stream=new MediaStream([...visual.getVideoTracks(),...dest.stream.getAudioTracks()]);
  const type=['video/webm;codecs=vp9,opus','video/webm;codecs=vp8,opus','video/mp4'].find(t=>MediaRecorder.isTypeSupported(t));
  if(!type){stream.getTracks().forEach(t=>t.stop());await ac.close();throw Error('No supported video export codec.');}
  const recorder=new MediaRecorder(stream,{mimeType:type,videoBitsPerSecond:5000000}),chunks=[];let frame=0,currentSource=null,abortError=null;
  const stopped=new Promise((resolve,reject)=>{recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};recorder.onstop=resolve;recorder.onerror=e=>reject(e.error||Error('Video encoder failed'));});
  const parser=document.createElement('div'),colors={derek:'#afe3c6',xavier:'#a8d7f4',audrey:'#d5baf8'};
  const plain=x=>{parser.innerHTML=x;return parser.textContent||'';};
  function text(t,x,y,maxWidth,size,color='#edf2f7',weight=400,line=1.32){g.fillStyle=color;g.font=weight+' '+size+'px system-ui';let row='',yy=y;for(const word of String(t).split(/\s+/)){if(g.measureText(row+word).width>maxWidth&&row){g.fillText(row.trim(),x,yy);row='';yy+=size*line;}row+=word+' ';}if(row)g.fillText(row.trim(),x,yy);return yy+size*line;}
  function draw(s,n,reply,dt){const a=avatars[s.agent],accent=colors[s.agent];g.fillStyle='#080e18';g.fillRect(0,0,1280,720);const grad=g.createRadialGradient(1030,270,20,1000,310,600);grad.addColorStop(0,s.agent==='audrey'?'#352849':s.agent==='xavier'?'#1b3b52':'#23473f');grad.addColorStop(1,'#080e18');g.fillStyle=grad;g.fillRect(0,48,1280,672);
    text('BETTORTOKEN',52,30,400,16,accent,700);text('THE MANAGEMENT EXPERIENCE',900,30,350,10,'#9db2c9',600);
    if(a){const buf=new Float32Array(an.fftSize);an.getFloatTimeDomainData(buf);let sum=0;for(const v of buf)sum+=v*v;a.controller.setSpeech({amplitude:Math.min(1,Math.sqrt(sum/buf.length)*6)});a.controller.update(Math.min(.1,dt));a.render();g.drawImage(a.renderer.domElement,765,58,470,530);}
    text(String(n+1).padStart(2,'0')+' / '+s.agent.toUpperCase()+' · AI AGENT',56,90,680,11,accent,700);
    let y=126;const parts=s.title.split('<br>');parts.forEach((part,i)=>{y=text(plain(part),56,y,695,46,i?accent:'#edf2f7',650,1.07);});y=text(s.sub,56,y+16,665,19,'#bacbdc',400,1.4)+16;
    parser.innerHTML=s.details;const tiles=[...parser.querySelectorAll('.tile')];if(tiles.length){tiles.forEach((t,i)=>{const x=56+i*190;g.fillStyle='#111f2d';g.fillRect(x,y,179,67);text(t.querySelector('small').textContent,x+12,y+20,158,9,'#a4bacc',500);text(t.querySelector('strong').textContent,x+12,y+49,158,23,accent,600);});y+=86;}else{const steps=[...parser.querySelectorAll('.steps span')].map(n=>n.textContent);y=text(steps.join('  /  '),56,y+4,665,15,accent,500,1.6)+8;}
    const caption=parser.querySelector('.caption');if(caption)text(caption.textContent,56,y+4,665,11,'#9db0c4',400,1.4);
    g.fillStyle='#0c1726';g.fillRect(48,526,1184,164);g.strokeStyle='#3b5368';g.strokeRect(48,526,1184,164);
    text('MANAGEMENT ASKS',67,550,310,9,accent,650);text(s.q,67,575,310,15,'#edf2f7',400,1.4);
    text(s.agent.toUpperCase()+' · ACTUAL PREPARED RESPONSE',416,550,785,9,accent,650);text(reply,416,575,785,15,'#edf2f7',400,1.34);
    text('ILLUSTRATIVE REHEARSAL · SIMULATED CAPITAL · NOT PRODUCTION PERFORMANCE',52,710,1120,9,'#91a8be',500);
  }
  const hidden=()=>{if(document.hidden)abortError=Error('Export stopped because this tab was hidden. Keep it visible and try again.');};document.addEventListener('visibilitychange',hidden);
  const runFor=(seconds,s,i,reply)=>new Promise((resolve,reject)=>{const start=performance.now();let last=start;const step=now=>{if(abortError){reject(abortError);return;}draw(s,i,reply,(now-last)/1000);last=now;if(now-start>=seconds*1000){resolve();return;}frame=requestAnimationFrame(step);};frame=requestAnimationFrame(step);});
  try{recorder.start(1000);for(let i=0;i<scenes.length;i++){const s=scenes[i],v=prepared.get(i);status('Recording voiced video · '+(i+1)+' / '+scenes.length+'. Keep this tab visible.');show(i);const b=await fetch(v.url).then(r=>r.arrayBuffer()),buffer=await ac.decodeAudioData(b);await runFor(leadSeconds,s,i,v.answer);currentSource=ac.createBufferSource();currentSource.buffer=buffer;currentSource.connect(an);currentSource.start();await runFor(buffer.duration,s,i,v.answer);currentSource.stop();currentSource=null;await runFor(tailSeconds,s,i,v.answer);}recorder.stop();await stopped;const blob=new Blob(chunks,{type}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='BettorToken-Voiced-Management-Demo.'+(type.includes('mp4')?'mp4':'webm');link.click();setTimeout(()=>URL.revokeObjectURL(url),60000);status('Voiced video exported locally with the actual prepared responses.');return blob;}finally{cancelAnimationFrame(frame);document.removeEventListener('visibilitychange',hidden);if(currentSource)try{currentSource.stop();}catch{}if(recorder.state!=='inactive')recorder.stop();stream.getTracks().forEach(t=>t.stop());await ac.close();Object.values(avatars).forEach(a=>a.controller.setSpeech(null));}
}
