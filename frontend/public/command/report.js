/* Minimal dependency-free PDF renderer. PDF 1.4 with standard PDF fonts.
 * Exports a frozen copy of the displayed data; never recalculates trading EV.
 * Source, mode, as-of time, accounting scope and snapshot hash travel together. */
(function(root){
'use strict';
const C=root.BTCore;
const ascii=s=>String(s??'').replace(/[−–—]/g,'-').replace(/·/g,' / ').replace(/¢/g,'c').replace(/[’‘]/g,"'").replace(/[“”]/g,'"').replace(/[^\x20-\x7E]/g,'');
const quote=s=>ascii(s).replace(/\\/g,'\\\\').replace(/\(/g,'\\(').replace(/\)/g,'\\)');
const rgb=hex=>hex.match(/[0-9a-f]{2}/gi).map(x=>(parseInt(x,16)/255).toFixed(4)).join(' ');
const bytes=s=>new TextEncoder().encode(s);
const concat=arrays=>{const n=arrays.reduce((a,b)=>a+b.length,0),out=new Uint8Array(n);let off=0;for(const a of arrays){out.set(a,off);off+=a.length;}return out;};
class PDF {
 constructor(){this.pages=[];this.commands=[];this.image=null;}
 page(){this.commands=[];this.pages.push(this.commands);return this;}
 rect(x,y,w,h,color){this.commands.push(`${rgb(color)} rg ${x} ${842-y-h} ${w} ${h} re f`);}
 line(x1,y1,x2,y2,color='#dce3ee',width=.6){this.commands.push(`${rgb(color)} RG ${width} w ${x1} ${842-y1} m ${x2} ${842-y2} l S`);}
 text(t,x,y,size=10,color='#182b43',font='F1'){this.commands.push(`BT /${font} ${size} Tf ${rgb(color)} rg 1 0 0 1 ${x} ${842-y} Tm (${quote(t)}) Tj ET`);}
 wrap(t,x,y,width,size=10,color='#62758d',lineHeight=15){const max=Math.floor(width/(size*.51));let line='';for(const w of ascii(t).split(/\s+/)){if((line+' '+w).length>max&&line){this.text(line,x,y,size,color);y+=lineHeight;line=w;}else line+=(line?' ':'')+w;}if(line)this.text(line,x,y,size,color);return y+lineHeight;}
 logo(x,y,w=150){if(this.image)this.commands.push(`q ${w} 0 0 ${w*this.image.h/this.image.w} ${x} ${842-y-w*this.image.h/this.image.w} cm /Im1 Do Q`);else this.text('BETTORTOKEN',x,y+18,18,'#0066ff','F2');}
 polyline(points,color='#0066ff',width=1.8){if(!points.length)return;this.commands.push(`${rgb(color)} RG ${width} w `+points.map(([x,y],i)=>`${x.toFixed(2)} ${(842-y).toFixed(2)} ${i?'l':'m'}`).join(' ')+' S');}
 output(){
  const objects=[null,'<< /Type /Catalog /Pages 2 0 R >>',null,'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>','<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>'];
  let imageId=null;
  if(this.image){imageId=objects.length;objects.push(concat([bytes(`<< /Type /XObject /Subtype /Image /Width ${this.image.w} /Height ${this.image.h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${this.image.bytes.length} >>\nstream\n`),this.image.bytes,bytes('\nendstream')]));}
  const kids=[];
  for(const command of this.pages){const stream=bytes(command.join('\n'));const contentId=objects.length;objects.push(concat([bytes(`<< /Length ${stream.length} >>\nstream\n`),stream,bytes('\nendstream')]));const pageId=objects.length;kids.push(`${pageId} 0 R`);objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> ${imageId?`/XObject << /Im1 ${imageId} 0 R >>`:''} >> /Contents ${contentId} 0 R >>`);}
  objects[2]=`<< /Type /Pages /Kids [${kids.join(' ')}] /Count ${kids.length} >>`;
  const chunks=[bytes('%PDF-1.4\n% BT COMMAND\n')],offsets=[0];let pos=chunks[0].length;
  for(let i=1;i<objects.length;i++){offsets.push(pos);const body=typeof objects[i]==='string'?bytes(objects[i]):objects[i];const part=concat([bytes(`${i} 0 obj\n`),body,bytes('\nendobj\n')]);chunks.push(part);pos+=part.length;}
  let xref=`xref\n0 ${objects.length}\n0000000000 65535 f \n`;for(let i=1;i<objects.length;i++)xref+=String(offsets[i]).padStart(10,'0')+' 00000 n \n';xref+=`trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${pos}\n%%EOF`;
  chunks.push(bytes(xref));return new Blob([concat(chunks)],{type:'application/pdf'});
 }
}
async function logoImage(){return new Promise(resolve=>{const im=new Image();const timer=setTimeout(()=>resolve(null),2500);im.onload=()=>{clearTimeout(timer);try{const cv=document.createElement('canvas');cv.width=im.naturalWidth;cv.height=im.naturalHeight;const ctx=cv.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,cv.width,cv.height);ctx.drawImage(im,0,0);const b=atob(cv.toDataURL('image/jpeg',.96).split(',')[1]);resolve({w:cv.width,h:cv.height,bytes:Uint8Array.from(b,c=>c.charCodeAt(0))});}catch{resolve(null);}};im.onerror=()=>{clearTimeout(timer);resolve(null);};im.src=root.BETTOR_COMMAND_CONFIG?.logo||'assets/bt-logo-full.png';});}
function save(blob,filename){const u=URL.createObjectURL(blob);const a=document.createElement('a');a.href=u;a.download=filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),60000);}
async function create(snapshot,kind='Management overview'){
 if(!snapshot)throw new Error('No snapshot is available to report.');
 const s=C.clone(snapshot);const check=C.validate(s);if(!check.ok)throw new Error(check.errors.join(' '));
 const hash=await C.digest(s);const pdf=new PDF();pdf.image=await logoImage();
 const demo=s.mode==='DEMO';const created=new Date().toISOString();
 const scope=demo?'ILLUSTRATIVE / NOT ACTUAL PERFORMANCE':s.mode==='SHADOW'?'SHADOW RESEARCH / NOT EXECUTED PERFORMANCE':'ACCOUNT SNAPSHOT / UNAUDITED';
 function header(title,section){pdf.page();pdf.rect(0,0,595,5,'#0066ff');pdf.logo(40,26,168);pdf.text('COMMAND',464,42,11,'#112f57','F2');pdf.text('INSTITUTIONAL WORKSPACE',431,56,7,'#788aa3','F1');pdf.line(40,78,555,78);pdf.rect(40,94,515,25,demo?'#fff3dc':'#eaf1fc');pdf.text(scope,51,110,8,demo?'#926522':'#235ba5','F2');pdf.text(section.toUpperCase(),40,147,8,'#7589a3','F3');pdf.text(title,40,177,25,'#122a48','F2');pdf.text(`Snapshot: ${s.asOf}  |  ${s.mode}  |  ${s.audience}`,40,198,8,'#697f9c','F3');}
 function metric(x,y,label,value,sub){pdf.rect(x,y,245,92,'#f0f4fa');pdf.text(label.toUpperCase(),x+14,y+21,8,'#6d7f97','F2');pdf.text(value,x+14,y+50,23,'#123259','F3');pdf.text(sub,x+14,y+72,8,'#6d7f97');}
 const a=s.account;
 header(kind,'01 / Portfolio & operating context');
 metric(40,221,'Account value',C.usd(a.value),'Account value is not Company or token NAV.');
 metric(310,221,'Net P&L / source window',C.signed(a.netMtd),'Realized plus current unrealized P&L.');
 metric(40,328,'Available capital',C.usd(a.available),'Cash less reserved capital; source supplied.');
 metric(310,328,'Unrealized P&L',C.signed(a.unrealized),'Marked positions; not a realized return.');
 pdf.text('PORTFOLIO VALUE / SOURCE WINDOW',40,452,9,'#405c80','F2');
 const hs=s.history;
 if(hs.length>1){const vals=hs.map(r=>r.value),min=Math.min(...vals),max=Math.max(...vals),span=Math.max(max-min,1);for(let i=0;i<4;i++){let y=476+i*43;pdf.line(40,y,555,y,'#dfe7f2');pdf.text(C.usd(max-span*i/3),42,y-5,7,'#7f91a9','F3');}pdf.polyline(hs.map((r,i)=>[42+i/(hs.length-1)*511,605-(r.value-min)/span*129]));pdf.text(C.date(hs[0].at),40,625,8,'#7188a7');pdf.text(C.date(hs[hs.length-1].at),472,625,8,'#7188a7');}
 else pdf.text('No qualified portfolio history provided.',40,492,10,'#7188a7');
 pdf.wrap(s.provenance.accountingBasis,40,659,515,9,'#667c98',13);
 pdf.wrap(demo?'All amounts, positions, orders, model outputs and charts in this document are synthetic design fixtures. They are not BettorToken operating results, a forecast, an offer, or investment advice.':'This read-only report reflects the source snapshot, not an audit or attestation. Estimated, unrealized, and shadow values must not be treated as realized performance. Past results do not predict future results.',40,703,515,8,'#798ba4',12);
 if(s.audience==='INTERNAL'){
  header('Positions & execution','02 / Account detail');
  pdf.text('OPEN POSITIONS / MARKED IN USD',40,230,9,'#425e83','F2');
  let y=249;
  function tableHead(){pdf.rect(40,y,515,25,'#122e51');pdf.text('POSITION',50,y+16,8,'#ffffff','F2');pdf.text('QTY',278,y+16,8,'#ffffff','F2');pdf.text('MARK',349,y+16,8,'#ffffff','F2');pdf.text('VALUE',409,y+16,8,'#ffffff','F2');pdf.text('UNRL P&L',491,y+16,8,'#ffffff','F2');y+=25;}
  tableHead();
  if(!s.positions.length){pdf.text('No position records supplied. This is not a zero balance.',50,y+27,10,'#7c8ca3');y+=55;}
  for(const [i,p] of s.positions.entries()){
   if(y>697){header('Positions / continued','Account detail');y=229;tableHead();}
   if(i%2===0)pdf.rect(40,y,515,43,'#f1f5fb');
   pdf.text(p.title.slice(0,32),50,y+16,9,'#18395c','F2');pdf.text(`${p.outcome} / ${p.venue}`.slice(0,53),50,y+31,7,'#7487a0');pdf.text(C.count(p.quantity),273,y+22,8,'#304f73','F3');pdf.text(C.price(p.mark),348,y+22,8,'#304f73','F3');pdf.text(C.usd(p.value),405,y+22,8,'#304f73','F3');pdf.text(C.signed(p.unrealized),486,y+22,8,p.unrealized<0?'#b73953':'#18765c','F3');y+=43;
  }
  y+=24;pdf.text('SYSTEM INTENT IS NOT AN EXECUTION INSTRUCTION',40,y,9,'#425e83','F2');
  y=pdf.wrap('This hub has no order submission, cancellation, cash-out, or capital-allocation path. Watching, hold, pair review and exit-watch states describe telemetry. Standing orders must be reconciled by the authoritative venue/order service.',40,y+22,515,9,'#697f9b',14);
 }
 header('Governance & provenance','03 / Evidence, controls & disclosures');
 pdf.text('READINESS CONTROLS',40,230,9,'#405c80','F2');let gy=248;
 for(const g of s.gates){pdf.rect(40,gy,515,47,'#f1f5fb');pdf.text(g.name,53,gy+17,10,'#233e60','F2');pdf.text(g.detail.slice(0,82),53,gy+33,8,'#6e829d');pdf.text(g.status,478,gy+20,8,g.status==='PASS'?'#227b5d':'#926425','F3');gy+=52;}
 gy+=15;pdf.text('SOURCE IDENTITY',40,gy,9,'#405c80','F2');gy+=23;
 for(const [k,v] of [['Mode',s.mode],['Audience',s.audience],['Snapshot ID',s.snapshotId],['Source ID',s.provenance.sourceId],['Snapshot as of',s.asOf],['Export created',created]]){pdf.text(k,40,gy,9,'#7588a3');pdf.text(String(v).slice(0,69),155,gy,8,'#254367','F3');gy+=20;}
 pdf.text('SHA-256 of the frozen source snapshot',40,gy+8,8,'#647d9f','F2');pdf.text(hash?hash.slice(0,42):'Unavailable in this browser context',40,gy+25,8,'#496587','F3');if(hash)pdf.text(hash.slice(42),40,gy+38,8,'#496587','F3');
 pdf.wrap('A matching checksum establishes snapshot integrity, not independently audited accounting or validated trading edge. Investor distribution requires a server-authorized projection and approved disclosures; the demo presentation mode is not an access-control boundary.',40,gy+64,515,8,'#7a8da7',12);
 pdf.pages.forEach((commands,i)=>{pdf.commands=commands;pdf.line(40,790,555,790);pdf.text(`BETTORTOKEN / ${demo?'DEMONSTRATION ONLY':'CONFIDENTIAL'} / ${s.snapshotId}`,40,810,7,'#7489a5','F3');pdf.text(`${String(i+1).padStart(2,'0')} / ${String(pdf.pages.length).padStart(2,'0')}`,520,810,8,'#7489a5','F3');});
 const blob=pdf.output();return {blob,hash,snapshot:s,created,filename:`BettorToken_Command_${demo?'DEMO_':''}${kind.replace(/\W+/g,'_')}_${s.asOf.slice(0,10)}.pdf`,pages:pdf.pages.length};
}
root.BTReport={create,save};
})(window);
