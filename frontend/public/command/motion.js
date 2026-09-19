/* BettorToken COMMAND / Motion Edition.
 * Presentation only. No transport, order, permission, or financial mutations.
 * The orbital scene is explicitly schematic, never market telemetry.
 */
(function () {
  'use strict';
  const VERSION = '1.1.0-motion';
  const media = window.matchMedia('(prefers-reduced-motion: reduce)');
  const storageKey = 'bettortoken.command.visual-motion.v1';
  let requested = true;
  try { requested = localStorage.getItem(storageKey) !== 'off'; } catch (_) {}
  let presentation = false;
  let context = { view: '', frozen: false };
  let teardown = () => {};
  let frame = 0;
  let timer = 0;
  let sceneVisible = true;
  let lastView = null;
  let lastDraw = 0;
  let drawCount = 0;
  let angle = 0.38;
  let renderScene = null;
  const active = () => requested && !media.matches && !document.hidden && !context.frozen && sceneVisible;
  const esc = value => window.BTCore.esc(value);
  const arrow = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6"/></svg>';
  const expand = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/></svg>';
  const pause = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M8 5v14M16 5v14"/></svg>';
  const play = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m8 5 11 7-11 7Z"/></svg>';

  function controls() {
    return '<div class="motion-controls"><button class="motion-toggle" type="button" data-motion="toggle" aria-pressed="false">'+pause+'<span>Pause motion</span></button><button class="presentation-toggle" type="button" data-motion="present" aria-pressed="false">'+expand+'<span>Presentation</span></button></div>';
  }

  function hero(options) {
    const connected = Boolean(options.connected);
    const demo = options.mode === 'demo';
    return `<section class="command-hero" aria-label="BettorToken Command introduction">
      <div class="hero-grain" aria-hidden="true"></div><div class="hero-meridian" aria-hidden="true"></div>
      <div class="hero-copy"><div class="hero-eyebrow"><span class="edition-square"></span>THE BETTORTOKEN OPERATING EXPERIENCE</div>
        <h2>Intelligence.<br><span>In motion.</span></h2>
        <p>Your capital. Your decisions. Your entire operation.<br class="desktop-break"> One extraordinary point of view.</p>
        <div class="hero-cta"><button type="button" class="hero-primary" data-view="decisions">Explore the engine ${arrow}</button><button type="button" class="hero-secondary" data-motion="present">${expand} Presentation view</button></div>
        <div class="hero-signature"><span></span> THE DISCIPLINE IS THE EDGE.</div>
      </div>
      <div class="orbital-scene"><canvas id="command-orbit" aria-hidden="true"></canvas>
        <div class="orbit-core" aria-hidden="true"><div class="core-ring"></div><div class="core-hex"><svg viewBox="0 0 100 112" fill="none"><path d="M50 7 89 29v51l-39 23-39-23V36M26 18v52l24 14 24-14V40L50 26 39 32v30l11 7 10-7V47L50 41" stroke="currentColor" stroke-width="5" stroke-linejoin="round"/></svg></div><span>BETTOR</span><small>INTELLIGENCE CORE</small></div>
        <button class="orbit-node node-observe" data-view="models"><i></i><span>01 / OBSERVE<small>Market intelligence</small></span>${arrow}</button>
        <button class="orbit-node node-evaluate" data-view="decisions"><i></i><span>02 / EVALUATE<small>Decision evidence</small></span>${arrow}</button>
        <button class="orbit-node node-protect" data-view="risk"><i></i><span>03 / PROTECT<small>Risk & oversight</small></span>${arrow}</button>
        <div class="orbit-caption"><span class="caption-cross">+</span> ARCHITECTURE SCHEMATIC · NOT LIVE TELEMETRY</div>
      </div>
      <div class="hero-bottom"><span class="hero-mode"><i></i>${demo ? 'DESIGN DEMONSTRATION' : connected ? 'SOURCE SNAPSHOT' : 'AWAITING DATA CONNECTION'}</span><span>READ-ONLY BY DESIGN <span class="hero-bottom-arrow">↗</span></span></div>
    </section>`;
  }

  function pulseStrip(snapshot) {
    if (!snapshot) return '';
    const ds = snapshot.audience === 'INVESTOR' ? [] : snapshot.decisions || [];
    if (!ds.length) return '';
    const C = window.BTCore;
    const cells = ds.slice(0, 5).map(d => `<button class="signal-chip" data-detail="decision" data-id="${esc(d.id)}"><span class="signal-dot ${d.state==='REJECTED'?'rejected':''}"></span><span>${esc(d.title)}</span><span class="signal-state">${esc(d.state)}</span><span class="signal-ev">${C.finite(d.netEv)?(d.netEv>=0?'+':'−')+C.price(Math.abs(d.netEv)):'—'} <small>/sh</small></span></button>`).join('');
    return `<section class="signal-strip" aria-label="Decision snapshot"><div class="signal-strip-heading"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M2 12h4l3-7 5 14 3-7h5"/></svg><span>DECISION PULSE<small>${snapshot.mode==='DEMO'?'SYNTHETIC SNAPSHOT':'RECORDED SNAPSHOT'}</small></span></div><div class="signal-scroll">${cells}</div></section>`;
  }

  function decisionMap(snapshot) {
    const C=window.BTCore,p=snapshot?.pipeline;
    return `<section class="evidence-map" aria-label="Recorded decision architecture">
      <div class="evidence-map-head"><div><h2>Inside the intelligence engine</h2><p>${snapshot?.mode==='DEMO'?'SYNTHETIC SNAPSHOT':'RECORDED SNAPSHOT'} / SCHEMATIC, NOT LIVE FLOW</p></div><button type="button" class="map-view-source" data-action="source">Inspect source ↗</button></div>
      <div class="map-figure"><svg class="map-wires" viewBox="0 0 1000 230" preserveAspectRatio="none" aria-hidden="true"><path d="M110 115H370M370 115H440Q470 115 495 85L520 57H650M370 115H440Q470 115 495 155L520 177H650M650 177H730Q760 177 790 145L820 115H910"/><path class="wire-glint" d="M110 115H370M370 115H440Q470 115 495 85L520 57H650M370 115H440Q470 115 495 155L520 177H650"/><path class="wire-blocked" d="M790 145L820 115H910"/></svg>
        <div class="map-node map-input"><small>01 / OBSERVE</small><strong>${C.count(p?.observed)}</strong><span>States evaluated</span></div>
        <div class="map-node map-eval"><small>02 / EVALUATE</small><strong>${C.count(p?.reviewed)}</strong><span>Further review</span></div>
        <div class="map-node map-reject"><small>DISCIPLINE</small><strong>${C.count(p?.rejected)}</strong><span>Rejected states</span></div>
        <div class="map-node map-shadow"><small>03 / SHADOW</small><strong>${C.count(p?.shadow)}</strong><span>Research candidates</span></div>
        <div class="map-node map-execution"><small>04 / EXECUTION</small><strong>LOCKED</strong><span>No order path in this hub</span></div>
      </div><div class="map-footer"><span>Illustrated architecture. Counts describe the source window, not this animation.</span><span class="map-mark">READ-ONLY OBSERVATION</span></div>
    </section>`;
  }

  function syncControls() {
    document.body.classList.toggle('motion-paused', !requested || media.matches || context.frozen || document.hidden);
    document.body.classList.toggle('command-presentation', presentation);
    document.querySelectorAll('[data-motion="toggle"]').forEach(button => {
      button.setAttribute('aria-pressed', String(!requested || media.matches || context.frozen));
      button.disabled = media.matches || context.frozen;
      const label = media.matches ? 'Reduced motion' : context.frozen ? 'View frozen' : requested ? 'Pause motion' : 'Resume motion';
      button.innerHTML = (requested && !media.matches && !context.frozen ? pause : play)+'<span>'+label+'</span>';
      button.title = 'Visual effects only. Does not pause trading or change data.';
    });
    document.querySelectorAll('[data-motion="present"]').forEach(button => {
      button.setAttribute('aria-pressed', String(presentation));
      button.innerHTML = expand+'<span>'+(presentation?'Exit presentation':button.classList.contains('hero-secondary')?'Presentation view':'Presentation')+'</span>';
      button.title = 'Changes layout only. No data or permissions change.';
    });
    const badge = document.getElementById('presentation-notice');
    if (presentation && !badge) {
      const el = document.createElement('div'); el.id='presentation-notice';
      el.innerHTML='<span>PRESENTATION VIEW · SAME DATA & PERMISSIONS</span><button type="button" data-motion="present">Exit presentation ×</button>';
      document.body.appendChild(el);
    } else if (!presentation && badge) badge.remove();
  }

  function stop() { if (frame) cancelAnimationFrame(frame); frame=0; lastDraw=0; }
  function loop(time) {
    frame=0;
    if (!active() || !renderScene) return;
    const interval = innerWidth < 720 ? 50 : 33;
    if (!lastDraw || time-lastDraw >= interval) {
      const delta = lastDraw ? Math.min(time-lastDraw,100) : 0;
      angle += delta*0.00010;
      renderScene(angle);
      drawCount++;
      lastDraw=time;
    }
    frame=requestAnimationFrame(loop);
  }
  function resume() { syncControls(); if (active() && renderScene && !frame) frame=requestAnimationFrame(loop); else if (!active()) stop(); }

  function createOrbit(canvas) {
    let width=0,height=0;
    let ctx=null;
    try { ctx=canvas.getContext('2d',{alpha:true}); } catch (_) {}
    if (!ctx) { canvas.classList.add('canvas-unavailable'); return () => {}; }
    const TAU=Math.PI*2;
    function resize() {
      const r=canvas.getBoundingClientRect();
      width=r.width; height=r.height;
      const dpr=Math.min(window.devicePixelRatio||1,1.6);
      canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
      ctx.setTransform(dpr,0,0,dpr,0,0);
      draw(angle);
    }
    function point(x,y,z,a,r,cx,cy) {
      const xx=x*Math.cos(a)+z*Math.sin(a);
      const zz=-x*Math.sin(a)+z*Math.cos(a);
      const yy=y*.93+zz*.16;
      const dep=zz*.93-y*.16;
      const p=1/(1-dep*.17);
      return [cx+xx*r*p,cy+yy*r*p,dep,p];
    }
    function draw(a) {
      if(!width||!height)return;
      ctx.clearRect(0,0,width,height);
      const cx=width*.5,cy=height*.49,r=Math.min(width*.30,height*.37);
      const glow=ctx.createRadialGradient(cx,cy,15,cx,cy,r*1.65);
      glow.addColorStop(0,'rgba(0,92,255,0.21)');glow.addColorStop(.55,'rgba(13,50,112,0.11)');glow.addColorStop(1,'rgba(0,22,66,0)');
      ctx.fillStyle=glow;ctx.fillRect(0,0,width,height);
      // Sparse fixed stars: decorative, no random data and no counters.
      for(let i=0;i<65;i++){
        const x=((i*193+43)%997)/997*width,y=((i*79+29)%431)/431*height;
        ctx.fillStyle='rgba(120,161,220,'+(i%7===0?.42:.14)+')';ctx.fillRect(x,y,1,1);
      }
      ctx.lineWidth=.6;
      for(let lat=-5;lat<=5;lat++){
        const phi=lat*Math.PI/13;
        let prev=null;
        for(let j=0;j<=96;j++){
          const t=j/96*TAU,p=point(Math.cos(phi)*Math.cos(t),Math.sin(phi),Math.cos(phi)*Math.sin(t),a,r,cx,cy);
          if(prev){ctx.strokeStyle=`rgba(38,117,249,${.035+(p[2]+1)*.09})`;ctx.beginPath();ctx.moveTo(prev[0],prev[1]);ctx.lineTo(p[0],p[1]);ctx.stroke();}prev=p;
        }
      }
      for(let lon=0;lon<16;lon++){
        const theta=lon/16*TAU;let prev=null;
        for(let j=0;j<=40;j++){
          const phi=-Math.PI/2+j/40*Math.PI,p=point(Math.cos(phi)*Math.cos(theta),Math.sin(phi),Math.cos(phi)*Math.sin(theta),a,r,cx,cy);
          if(prev){ctx.strokeStyle=`rgba(56,126,255,${.025+(p[2]+1)*.085})`;ctx.beginPath();ctx.moveTo(prev[0],prev[1]);ctx.lineTo(p[0],p[1]);ctx.stroke();}prev=p;
        }
      }
      // Deterministic points on the schematic globe.
      for(let i=0;i<200;i++){
        const y=1-2*i/199,t=i*2.399963,rho=Math.sqrt(1-y*y);
        const p=point(Math.cos(t)*rho,y,Math.sin(t)*rho,a,r,cx,cy);
        if(p[2]<-.2)continue;
        const bright=i%19===0;
        ctx.fillStyle=bright?'rgba(175,220,255,.88)':`rgba(63,147,255,${.18+(p[2]+1)*.18})`;
        ctx.beginPath();ctx.arc(p[0],p[1],bright?2.2:1,0,TAU);ctx.fill();
        if(bright){ctx.strokeStyle='rgba(68,145,255,.16)';ctx.beginPath();ctx.arc(p[0],p[1],5.5,0,TAU);ctx.stroke();}
      }
      // Three orbital tracks and moving glints. No implied execution route.
      for(let ring=0;ring<3;ring++){
        ctx.save();ctx.translate(cx,cy);ctx.rotate(-.32+ring*.73);
        const rx=r*(1.25+ring*.09),ry=r*(.31+ring*.04);
        ctx.strokeStyle=ring===1?'rgba(127,113,255,.33)':'rgba(57,133,255,.30)';ctx.lineWidth=.8;
        ctx.beginPath();ctx.ellipse(0,0,rx,ry,0,0,TAU);ctx.stroke();
        const t=a*(ring===1?-1.9:1.4)+ring*2;
        ctx.shadowColor='#2b85ff';ctx.shadowBlur=14;ctx.fillStyle='#bbdcff';
        ctx.beginPath();ctx.arc(Math.cos(t)*rx,Math.sin(t)*ry,2.6,0,TAU);ctx.fill();ctx.restore();
      }
      // Instrument ticks.
      for(let i=0;i<72;i++){
        const t=i/72*TAU,rr=r*1.16;
        ctx.strokeStyle='rgba(119,163,218,.16)';ctx.lineWidth=.7;
        ctx.beginPath();ctx.moveTo(cx+Math.cos(t)*rr,cy+Math.sin(t)*rr);
        ctx.lineTo(cx+Math.cos(t)*(rr+(i%6===0?6:2.7)),cy+Math.sin(t)*(rr+(i%6===0?6:2.7)));ctx.stroke();
      }
    }
    const resizeObserver=typeof ResizeObserver==='function'?new ResizeObserver(resize):null;
    resizeObserver?.observe(canvas);
    if(!resizeObserver)window.addEventListener('resize',resize);
    resize();renderScene=draw;
    return ()=>{resizeObserver?.disconnect();if(!resizeObserver)window.removeEventListener('resize',resize);renderScene=null;};
  }

  function mount(next) {
    teardown();stop();context=next;
    document.body.classList.add('motion-edition');
    document.body.dataset.motionVersion=VERSION;
    const page=document.getElementById('pagebody');
    const navChange=lastView!==next.view;
    if(navChange && requested && !media.matches) {
      page?.classList.add('motion-enter');
      clearTimeout(timer);timer=setTimeout(()=>page?.classList.remove('motion-enter'),900);
    }
    lastView=next.view;
    const toolbar=document.querySelector('.topbar');
    if(toolbar && !toolbar.querySelector('.motion-controls')){
      const c=document.createElement('div');c.innerHTML=controls();
      toolbar.insertBefore(c.firstChild,toolbar.lastElementChild);
    }
    const canvas=document.getElementById('command-orbit');
    sceneVisible=true;
    let clean=()=>{},intersection=null;
    if(canvas){clean=createOrbit(canvas); if(typeof IntersectionObserver==='function'){
      intersection=new IntersectionObserver(entries=>{sceneVisible=entries[0].isIntersecting;resume();},{threshold:0.01});intersection.observe(canvas);
    }}
    // Reveal a new page, never animate financial numbers into invented values.
    if(navChange && requested && !media.matches){
      document.querySelectorAll('.panel,.kpi').forEach((el,i)=>{
        el.style.setProperty('--enter-delay', Math.min(i,7)*35+'ms');
      });
    }
    teardown=()=>{clean();intersection?.disconnect();};
    syncControls();resume();
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-motion]');if(!button)return;
    if(button.dataset.motion==='toggle'){
      if(media.matches||context.frozen)return;
      requested=!requested;try{localStorage.setItem(storageKey,requested?'on':'off');}catch(_){}
      resume();
    } else if(button.dataset.motion==='present') {
      presentation=!presentation;syncControls();
      // Observer catches resulting resize. Preserve all source data and permissions.
    }
  });
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&presentation){presentation=false;syncControls();}});
  document.addEventListener('visibilitychange',resume);
  media.addEventListener?.('change',resume);
  window.addEventListener('pagehide',()=>{stop();teardown();clearTimeout(timer);});
  window.BTMotion=Object.freeze({version:VERSION,hero,pulseStrip,decisionMap,mount,inspect:()=>({version:VERSION,requested,systemReduced:media.matches,active:active()&&Boolean(renderScene),frames:drawCount,presentation,decorativeOnly:true})});
})();
