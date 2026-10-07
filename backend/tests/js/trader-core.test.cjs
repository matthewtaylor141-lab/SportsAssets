const test=require('node:test');const assert=require('node:assert/strict');
const path=require('node:path');const fs=require('node:fs');
// frontend/package.json is "type":"module": evaluate the browser IIFE in a
// CommonJS sandbox so its module.exports branch runs (the browser path is
// unchanged)
const vm=require('node:vm');
const C=(()=>{const m={exports:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../../frontend/public/command/trader-core.js'),'utf8'),{module:m,exports:m.exports,Intl,Date,Math,Number,String,Set,Array,Object,JSON,console});return m.exports;})();
const p=()=>({position_id:'p1',qty:10,holding_side:'LONG',state:'ACTIVE',title:'Yankees Boston',
 orders:[{position_id:'p1',order_id:'o1',direction:'SELL',limit_price:.7,state:'RESTING'}],
 quote:{bid:.69,ask:.71,at:999,current:true},packet:{complete:true,missing:[],probability_current:true,probability_at:999}});
const s=()=>({schema:'bettor.trader.v1',mode:'PAPER',source:'NATIVE_LEDGER',snapshot_at:1000,total_position_count:1,positions:[p()],execution_authority:false});
test('native read-only PAPER schema accepted',()=>assert.equal(C.validate(s(),{now:1000}).ok,true));
test('illustrative feed cannot silently substitute for production',()=>{let x=s();x.source='ILLUSTRATIVE';assert.equal(C.validate(x,{now:1000}).ok,false);});
test('live capital authority refused in the remote contract',()=>{let x=s();x.execution_authority=true;assert.equal(C.validate(x,{now:1000}).ok,false);});
test('duplicate positions refused',()=>{let x=s();x.positions.push(p());x.total_position_count=2;assert.equal(C.validate(x,{now:1000}).ok,false);});
test('cross-position order refused',()=>{let x=s();x.positions[0].orders[0].position_id='other';assert.equal(C.validate(x,{now:1000}).ok,false);});
test('future snapshot rejected',()=>{let x=s();x.snapshot_at=1006;assert.equal(C.validate(x,{now:1000}).ok,false);});
test('zero probability treated as a number',()=>assert.equal(C.finite(0),true));
test('unknown is not converted to zero',()=>assert.equal(C.money(null),'—'));
test('sell target uses bid not ask',()=>{let x=p();assert.equal(C.gap(x.orders[0],x.quote,1000).met,false);});
test('buy target uses ask not bid',()=>{let x=p();assert.equal(C.gap({...x.orders[0],direction:'BUY'},x.quote,1000).met,false);});
test('target touch never becomes a fill',()=>{let x=p();let g=C.gap(x.orders[0],{...x.quote,bid:.7},1000);assert.equal(g.met,true);assert.equal(g.isFill,false);});
test('expired quote invalidates target condition',()=>{let x=p();assert.equal(C.gap(x.orders[0],{...x.quote,at:1,bid:.8},1000).met,null);});
test('receipt rendering does not renew probability age',()=>assert.equal(C.packet(p(),1030).complete,false));
test('future source stamp refuses currency',()=>assert.equal(C.fresh(1000.001,1000,30),false));
test('source-age boundary is not rounded down',()=>assert.equal(C.fresh(969.9996,1000,30),false));
test('countdown interpolated only while confirmation current',()=>{const g={clock_seconds:60,source_at:999,clock_direction:'DOWN',clock_running:true,status:'CURRENT'};assert.equal(C.clock(g,1000).text,'~0:59');assert.equal(C.clock(g,1015).text,'1:00');assert.equal(C.clock(g,1015).frozen,true);});
test('clock reaching zero never fabricates next period',()=>assert.equal(C.clock({clock_seconds:1,source_at:990,clock_direction:'DOWN',clock_running:true,status:'CURRENT'},1000).text,'~0:00'));
test('missing official clock remains missing',()=>assert.equal(C.clock({clock_seconds:null},1000).value,null));
test('observed history removes duplicate observations',()=>{let x=p();x.quote_history=[{at:1,price:.4,observation_id:'a'},{at:2,price:.5,observation_id:'a'}];assert.equal(C.series(x).length,1);});
test('one observed point does not create a fabricated curve',()=>{let x=p();x.quote_history=[{at:1,price:.4}];assert.match(C.chart(x,null),/One observation/);assert.doesNotMatch(C.chart(x,null),/chart-line/);});
test('untrusted strings escaped',()=>assert.equal(C.esc('<img src=x onerror="bad()">'),'&lt;img src=x onerror=&quot;bad()&quot;&gt;'));
test('near-target filter includes only current observed prices',()=>{let x=p();assert.equal(C.matches(x,'near','',1000),true);x.quote.current=false;assert.equal(C.matches(x,'near','',1000),false);});
test('blocked positions remain visible in all filter',()=>{let x=p();x.packet.complete=false;assert.equal(C.matches(x,'all','',1000),true);assert.equal(C.matches(x,'blocked','',1000),true);});
test('desk materials have bounded textures and no frame-time/network work',async()=>{
 const src=fs.readFileSync(path.join(__dirname,'../../../frontend/public/command/desk-materials.js'),'utf8');
 const module=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
 const sizes=[];const grad={addColorStop(){}};const ctx={createLinearGradient(){return grad},fillRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){}};
 const doc={createElement(){let c={getContext(){return ctx}};sizes.push(c);return c;}};
 class Mat{constructor(v){this.v=v;}}class Tex{constructor(c){this.canvas=c;this.repeat={set(){}};}}
 const three={CanvasTexture:Tex,MeshStandardMaterial:Mat,MeshPhysicalMaterial:Mat,SRGBColorSpace:'sRGB',RepeatWrapping:1};
 const a=module.makeDeskFinish(three,{phone:false,documentRef:doc});const b=module.makeDeskFinish(three,{phone:true,documentRef:doc});
 assert.equal(sizes[0].width,512);assert.equal(sizes[1].width,256);assert.equal(a.v.clearcoat,.22);assert.equal(b.v.clearcoat,.08);
 assert.doesNotMatch(src,/fetch\(|requestAnimationFrame\(|Math\.random\(/);
});
